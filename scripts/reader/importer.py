from pathlib import Path
import re
import shutil
import unicodedata
from markdown_it import MarkdownIt
import pdfplumber
import pypdfium2 as pdfium
from filelock import FileLock
from .store import folder, atomic, digest, read

SUPPORTED_EXTENSIONS = (
    '.pdf', '.md', '.markdown',
    '.txt', '.html', '.htm', '.docx', '.tex',
)


def norm(text):
    return unicodedata.normalize('NFKC', text)


def _line_span(text, start, end):
    """1-based line_start/line_end covering [start, end) in text."""
    line_start = text.count('\n', 0, start) + 1
    line_end = text.count('\n', 0, max(start, end - 1)) + 1 if end > start else line_start
    return line_start, line_end


def _add_factory(atoms, blocks):
    def add(text, location, kind='paragraph', asset=None):
        aid = f'a{len(atoms)+1:05d}'
        atoms.append({'id': aid, 'text': text, 'location': location})
        bid = f'b{len(blocks)+1:05d}'
        blocks.append({'id': bid, 'kind': kind, 'text': text, 'source_ids': [aid],
                       'asset': asset, 'translation': None, 'history': [], 'review': None,
                       'structure_note': '', 'user_edited': False})
        return blocks[-1]
    return add



def _copy_local_image(src_path, rel, directory, block, issues):
    """Copy a same-directory relative image if safe; else record an issue. Never fetch remotes."""
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', rel) or rel.startswith('//'):
        issues.append({'id': f'image-{block["id"]}', 'message': 'Image not embedded (remote, missing or unsafe path)', 'resolution': None})
        return
    image = (src_path.parent / rel).resolve()
    try:
        safe = image.is_relative_to(src_path.parent.resolve())
    except (OSError, ValueError):
        safe = False
    if safe and image.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') and image.is_file():
        name = f"image-{block['id']}{image.suffix.lower()}"
        shutil.copyfile(image, directory / name)
        block['asset'] = name
        block['kind'] = 'figure'
    else:
        issues.append({'id': f'image-{block["id"]}', 'message': 'Image not embedded (remote, missing or unsafe path)', 'resolution': None})


def _finish_markdown_images(path, directory, blocks, issues):
    for b in blocks:
        match = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', b['text'].strip())
        if match:
            _copy_local_image(path, match[2].strip(), directory, b, issues)


def _import_txt(raw, add):
    text = raw.decode('utf-8-sig')
    # Blank-line separated paragraphs; preserve offsets into original text.
    parts = re.split(r'(\n\s*\n)', text)
    pos = 0
    for part in parts:
        if re.fullmatch(r'\n\s*\n', part or ''):
            pos += len(part)
            continue
        if not part or not part.strip():
            pos += len(part)
            continue
        # Trim trailing newlines inside the paragraph chunk for block text, keep location on full span.
        value = part.strip('\r\n')
        # Adjust start to first non-leading-whitespace content line start for cleaner offsets.
        leading = len(part) - len(part.lstrip('\r\n'))
        trailing = len(part) - len(part.rstrip('\r\n'))
        start = pos + leading
        end = pos + len(part) - trailing
        if value.strip():
            ls, le = _line_span(text, start, end)
            add(value, {'start': start, 'end': end, 'line_start': ls, 'line_end': le}, 'paragraph')
        pos += len(part)


def _xml_local(tag):
    return tag.rsplit('}', 1)[-1] if tag else ''


def _xml_attr(el, name):
    if el is None:
        return None
    suffix = '}' + name
    for key, value in el.attrib.items():
        if key == name or key.endswith(suffix):
            return value
    return None


_SUP_MAP = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', '−': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', 'n': 'ⁿ', 'i': 'ⁱ',
}
_SUB_MAP = {
    '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
    '+': '₊', '-': '₋', '−': '₋', '=': '₌', '(': '₍', ')': '₎',
    'a': 'ₐ', 'e': 'ₑ', 'h': 'ₕ', 'i': 'ᵢ', 'j': 'ⱼ', 'k': 'ₖ', 'l': 'ₗ', 'm': 'ₘ',
    'n': 'ₙ', 'o': 'ₒ', 'p': 'ₚ', 'r': 'ᵣ', 's': 'ₛ', 't': 'ₜ', 'u': 'ᵤ', 'v': 'ᵥ', 'x': 'ₓ',
}


def _script_unicode(text, kind):
    table = _SUP_MAP if kind == 'superscript' else _SUB_MAP
    out = []
    for ch in text:
        if ch.isspace():
            out.append(ch)
            continue
        mapped = table.get(ch)
        if mapped is None:
            return None
        out.append(mapped)
    return ''.join(out)


def _run_script_text(text, kind):
    if not text:
        return ''
    mapped = _script_unicode(text, kind)
    if mapped is not None:
        return mapped
    inner = text.replace('$', '')
    if kind == 'superscript':
        return '$^{' + inner + '}$'
    return '$_{' + inner + '}$'


def _wrap_math(latex, display=False):
    latex = re.sub(r'\s+', ' ', (latex or '').replace('$', '')).strip()
    if not latex:
        return ''
    if display:
        return '$$' + latex + '$$'
    return '$' + latex + '$'


def _math_only_text(value):
    text = (value or '').strip()
    if not text:
        return False
    return re.fullmatch(r'(?:\$\$[\s\S]*?\$\$|\$[^$]+\$|\s)+', text) is not None


_OMML_CHARS = {
    '⋅': r'\cdot ', '·': r'\cdot ', '⋯': r'\cdots ', '…': r'\ldots ',
    '→': r'\rightarrow ', '←': r'\leftarrow ', '⇒': r'\Rightarrow ', '↔': r'\leftrightarrow ',
    '≤': r'\leq ', '≥': r'\geq ', '≠': r'\neq ', '≈': r'\approx ', '∈': r'\in ',
    '∑': r'\sum ', '∏': r'\prod ', '∫': r'\int ', '∞': r'\infty ',
    '±': r'\pm ', '×': r'\times ', '÷': r'\div ', '−': '-', '–': '-', '—': '-',
    '′': "'", '″': "''", '∣': r'\mid ', '≤': r'\leq ',
    'Φ': r'\Phi ', 'φ': r'\phi ', 'ϕ': r'\varphi ', 'α': r'\alpha ', 'β': r'\beta ',
    'γ': r'\gamma ', 'δ': r'\delta ', 'ε': r'\varepsilon ', 'θ': r'\theta ',
    'λ': r'\lambda ', 'μ': r'\mu ', 'π': r'\pi ', 'ρ': r'\rho ', 'σ': r'\sigma ',
    'τ': r'\tau ', 'ω': r'\omega ', 'Γ': r'\Gamma ', 'Δ': r'\Delta ', 'Ω': r'\Omega ',
    'Σ': r'\Sigma ', 'Λ': r'\Lambda ', 'Θ': r'\Theta ', 'ℓ': r'\ell ',
    'ℤ': r'\mathbb{Z}', 'ℕ': r'\mathbb{N}', 'ℝ': r'\mathbb{R}',
}
_OMML_ACCENT = {
    '\u0305': r'\bar', '̅': r'\bar', '̂': r'\hat', '˜': r'\tilde', '̃': r'\tilde',
    '̇': r'\dot', '̈': r'\ddot', '⃗': r'\vec', '̆': r'\breve', '̌': r'\check',
    '́': r'\acute', '̀': r'\grave',
}
_OMML_NARY = {
    '∑': r'\sum', '∏': r'\prod', '∫': r'\int', '∮': r'\oint',
    '⋃': r'\bigcup', '⋂': r'\bigcap', '⋀': r'\bigwedge', '⋁': r'\bigvee',
}
_OMML_FUNCS = {
    'sin', 'cos', 'tan', 'log', 'ln', 'exp', 'det', 'ker', 'dim', 'min', 'max',
    'sup', 'inf', 'lim', 'Pr', 'gcd', 'hom', 'arg',
}
_OMML_SCR = {'double-struck': 'mathbb', 'fraktur': 'mathfrak', 'script': 'mathcal'}
_OMML_PROP = {
    'ctrlPr', 'rPr', 'dPr', 'fPr', 'naryPr', 'accPr', 'funcPr', 'radPr', 'boxPr',
    'phantPr', 'sSubPr', 'sSupPr', 'sSubSupPr', 'sPrePr', 'limLowPr', 'limUppPr',
    'eqArrPr', 'mPr', 'mcPr', 'argPr', 'argSz', 'aln', 'chr', 'begChr', 'endChr',
    'sepChr', 'count', 'show', 'zeroAsc', 'zeroDesc', 'zeroWid', 'degHide',
    'supHide', 'limLoc', 'cGp', 'cGpRule', 'mcJc', 'mcs', 'mc', 'lit', 'sty',
    'scr', 'nor',
}
_OMML_KNOWN = {
    'oMath', 'oMathPara', 'r', 't', 'e', 'sSub', 'sSup', 'sSubSup', 'sPre',
    'f', 'num', 'den', 'd', 'nary', 'sub', 'sup', 'acc', 'func', 'fName',
    'rad', 'deg', 'box', 'phant', 'limLow', 'limUpp', 'lim', 'm', 'mr',
    'groupChr', 'bar', 'eqArr',
}


def _omml_first(el, name):
    for child in el:
        if _xml_local(child.tag) == name:
            return child
    return None


def _omml_all(el, name):
    return [child for child in el if _xml_local(child.tag) == name]


def _omml_text_to_latex(text, flags):
    out = []
    for ch in text or '':
        if ch in _OMML_CHARS:
            out.append(_OMML_CHARS[ch])
            continue
        if ch in '\\{}#$%&_':
            out.append('\\' + ch)
            continue
        if ch == '~':
            out.append(r'\sim ')
            continue
        if ch == '^':
            out.append(r'\hat{}')
            flags['faithful'] = False
            continue
        out.append(ch)
    return ''.join(out)


def _omml_delim_token(ch, default):
    if ch is None:
        return default
    if ch == '':
        return ''
    return {'{': r'\{', '}': r'\}', '⟨': r'\langle ', '⟩': r'\rangle ',
            '⌊': r'\lfloor ', '⌋': r'\rfloor ', '‖': r'\Vert '}.get(ch, ch)


def _omml_convert(el, flags):
    loc = _xml_local(el.tag)
    if loc in _OMML_PROP:
        return ''
    if loc == 't':
        return _omml_text_to_latex(el.text or '', flags)
    if loc == 'phant':
        return ''
    if loc == 'r':
        text = ''.join((c.text or '') for c in el if _xml_local(c.tag) == 't')
        rPr = _omml_first(el, 'rPr')
        nor = False
        sty = None
        scr = None
        aln = False
        if rPr is not None:
            nor = _omml_first(rPr, 'nor') is not None
            sty = _xml_attr(_omml_first(rPr, 'sty'), 'val')
            scr = _xml_attr(_omml_first(rPr, 'scr'), 'val')
            aln = _omml_first(rPr, 'aln') is not None
        if sty not in (None, '', 'p', 'b', 'i', 'bi'):
            flags['faithful'] = False
        if scr in _OMML_SCR:
            latex = '\\' + _OMML_SCR[scr] + '{' + text + '}'
        elif sty == 'b':
            latex = r'\mathbf{' + _omml_text_to_latex(text, flags) + '}'
        elif sty == 'bi':
            latex = r'\boldsymbol{' + _omml_text_to_latex(text, flags) + '}'
        else:
            latex = _omml_text_to_latex(text, flags)
            if (nor or sty == 'p') and re.search(r'[A-Za-z]', text):
                latex = r'\mathrm{' + latex + '}'
        if aln:
            latex = '&' + latex
        return latex
    if loc in ('oMath', 'oMathPara', 'e', 'box', 'num', 'den', 'sub', 'sup', 'deg', 'lim', 'fName'):
        return ''.join(_omml_convert(c, flags) for c in el)
    if loc == 'sSub':
        return '{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e')) + '}_{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sub')) + '}'
    if loc == 'sSup':
        return '{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e')) + '}^{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sup')) + '}'
    if loc == 'sSubSup':
        return ('{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e')) + '}_{'
                + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sub')) + '}^{'
                + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sup')) + '}')
    if loc == 'sPre':
        return ('{}_{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sub')) + '}^{'
                + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sup')) + '}{'
                + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e')) + '}')
    if loc == 'f':
        return (r'\frac{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'num')) + '}{'
                + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'den')) + '}')
    if loc == 'd':
        dPr = _omml_first(el, 'dPr')
        beg, end, sep = '(', ')', ','
        if dPr is not None:
            if _omml_first(dPr, 'begChr') is not None:
                beg = _xml_attr(_omml_first(dPr, 'begChr'), 'val') or ''
            if _omml_first(dPr, 'endChr') is not None:
                end = _xml_attr(_omml_first(dPr, 'endChr'), 'val') or ''
            if _omml_first(dPr, 'sepChr') is not None:
                sep = _xml_attr(_omml_first(dPr, 'sepChr'), 'val')
                if sep is None:
                    sep = ','
        parts = [_omml_convert(c, flags) for c in _omml_all(el, 'e')]
        sep_l = _omml_text_to_latex(sep, flags) if sep else ','
        return _omml_delim_token(beg, '(') + sep_l.join(parts) + _omml_delim_token(end, ')')
    if loc == 'nary':
        naryPr = _omml_first(el, 'naryPr')
        chr_v = '∑'
        hide_sup = False
        if naryPr is not None:
            chr_v = _xml_attr(_omml_first(naryPr, 'chr'), 'val') or chr_v
            hide_sup = _xml_attr(_omml_first(naryPr, 'supHide'), 'val') in ('1', 'on', 'true')
        op = _OMML_NARY.get(chr_v, _omml_text_to_latex(chr_v, flags)).strip()
        sub = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sub'))
        sup = '' if hide_sup else ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'sup'))
        body = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e'))
        if sub:
            op += '_{' + sub + '}'
        if sup:
            op += '^{' + sup + '}'
        return op + ' ' + body
    if loc == 'acc':
        accPr = _omml_first(el, 'accPr')
        chr_v = _xml_attr(_omml_first(accPr, 'chr'), 'val') if accPr is not None else None
        cmd = _OMML_ACCENT.get(chr_v or '', r'\hat')
        return cmd + '{' + ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e')) + '}'
    if loc == 'func':
        name = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'fName')).strip()
        arg = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e'))
        plain = re.sub(r'\\mathrm\{([^{}]+)\}', r'\1', name).strip()
        if plain in _OMML_FUNCS:
            return '\\' + plain + ' ' + arg
        return r'\operatorname{' + plain + '}' + arg
    if loc == 'rad':
        radPr = _omml_first(el, 'radPr')
        hide_deg = False
        if radPr is not None:
            hide_deg = _xml_attr(_omml_first(radPr, 'degHide'), 'val') in ('1', 'on', 'true')
        deg = '' if hide_deg else ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'deg'))
        body = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e'))
        if deg:
            return r'\sqrt[' + deg + ']{' + body + '}'
        return r'\sqrt{' + body + '}'
    if loc == 'limLow':
        base = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e'))
        lim = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'lim'))
        plain = re.sub(r'\\mathrm\{([^{}]+)\}', r'\1', base).strip()
        if plain in _OMML_FUNCS:
            return '\\' + plain + '_{' + lim + '}'
        return r'\underset{' + lim + '}{' + base + '}'
    if loc == 'limUpp':
        base = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e'))
        lim = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'lim'))
        return r'\overset{' + lim + '}{' + base + '}'
    if loc == 'm':
        rows = []
        for mr in _omml_all(el, 'mr'):
            rows.append(' & '.join(_omml_convert(c, flags) for c in _omml_all(mr, 'e')))
        return r'\begin{matrix}' + r' \\ '.join(rows) + r'\end{matrix}'
    if loc == 'eqArr':
        rows = [_omml_convert(c, flags) for c in _omml_all(el, 'e')]
        return r'\begin{aligned}' + r' \\ '.join(rows) + r'\end{aligned}'
    if loc == 'bar':
        # ECMA-376 default pos is top (overbar). bot is underbar. Anything else is evidence, not a silent success.
        pos = 'top'
        barPr = _omml_first(el, 'barPr')
        if barPr is not None:
            if barPr.attrib:
                flags['faithful'] = False
            for ch in barPr:
                cl = _xml_local(ch.tag)
                if cl == 'ctrlPr':
                    continue
                if cl == 'pos':
                    extra = [k.rsplit('}', 1)[-1] for k in ch.attrib if k.rsplit('}', 1)[-1] != 'val']
                    if extra:
                        flags['faithful'] = False
                    raw = _xml_attr(ch, 'val')
                    val = (raw or 'top').strip().lower()
                    if val in ('top', 'bot'):
                        pos = val
                    else:
                        flags['faithful'] = False
                else:
                    flags['faithful'] = False
        for ch in el:
            if _xml_local(ch.tag) not in ('barPr', 'e'):
                flags['faithful'] = False
        body = ''.join(_omml_convert(c, flags) for c in _omml_all(el, 'e'))
        if pos == 'bot':
            return r'\underline{' + body + '}'
        return r'\overline{' + body + '}'
    if loc == 'groupChr':
        return ''.join(_omml_convert(c, flags) for c in el)
    if loc not in _OMML_KNOWN:
        flags['faithful'] = False
        return ''.join(_omml_convert(c, flags) for c in el)
    return ''.join(_omml_convert(c, flags) for c in el)


def _omml_to_latex(el):
    flags = {'faithful': True}
    latex = _omml_convert(el, flags)
    return latex.strip(), flags['faithful']


def _omml_snippet(el):
    try:
        from lxml import etree
        raw = etree.tostring(el, encoding='unicode')
    except Exception:
        raw = str(el)
    raw = re.sub(r'\sxmlns(?::[A-Za-z0-9_]+)?="[^"]*"', '', raw)
    return re.sub(r'\s+', ' ', raw).strip()[:280]


def _import_html(path, raw, directory, add, issues):
    from bs4 import BeautifulSoup, NavigableString, Tag

    text = raw.decode('utf-8-sig')
    soup = BeautifulSoup(text, 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()

    # Search cursor into original HTML for approximate source offsets.
    cursor = 0

    def locate(fragment):
        nonlocal cursor
        frag = fragment.strip()
        if not frag:
            start = cursor
            return {'start': start, 'end': start, 'line_start': _line_span(text, start, start)[0],
                    'line_end': _line_span(text, start, start)[1]}
        idx = text.find(frag, cursor)
        if idx < 0:
            idx = text.find(frag)
        if idx < 0:
            # Fall back to sequential extracted offsets anchored at cursor.
            start = cursor
            end = start + len(frag)
            ls, le = _line_span(text, min(start, len(text)), min(end, len(text)))
            return {'start': start, 'end': min(end, len(text)), 'line_start': ls, 'line_end': le}
        start, end = idx, idx + len(frag)
        cursor = end
        ls, le = _line_span(text, start, end)
        return {'start': start, 'end': end, 'line_start': ls, 'line_end': le}

    BLOCK_TAGS = {
        'h1': 'heading', 'h2': 'heading', 'h3': 'heading', 'h4': 'heading',
        'h5': 'heading', 'h6': 'heading',
        'p': 'paragraph', 'blockquote': 'paragraph', 'li': 'paragraph',
        'pre': 'code', 'table': 'table',
    }
    # Block-level tags. Inline tags (span, em, a, …) must NOT be listed: TeX/LaTeX
    # logo letter-spacing is done with inline spans and must stay glued.
    HTML_BLOCK_BOUNDARY = {
        'address', 'article', 'aside', 'blockquote', 'dd', 'div', 'dl', 'dt',
        'fieldset', 'figcaption', 'figure', 'footer', 'form',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li',
        'main', 'nav', 'ol', 'p', 'pre', 'section', 'table',
        'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
    }
    CONTAINER_TAGS = {
        'div', 'article', 'section', 'main', 'aside',
        'header', 'footer', 'nav', 'body', 'html', 'figure', 'figcaption',
    }

    def math_text(math_el):
        alt = (math_el.get('alttext') or math_el.get('alt') or '').strip()
        display = (math_el.get('display') or '').lower() == 'block'
        if alt:
            if '\\' in alt or any(ch in alt for ch in '^_{}'):
                inner = alt.strip().strip('$')
                wrap = '$$' if display else '$'
                return wrap + inner + wrap
            return alt
        vis = []

        def rec(node):
            if type(node) is NavigableString:
                vis.append(str(node))
                return
            if not isinstance(node, Tag):
                return
            if (node.name or '').lower() in ('annotation', 'annotation-xml'):
                return
            for child in node.children:
                rec(child)

        rec(math_el)
        return re.sub(r'\s+', '', ''.join(vis))

    def visible_text(el, collapse=True):
        parts = []

        def rec(node):
            if type(node) is NavigableString:
                parts.append(str(node))
                return
            if not isinstance(node, Tag):
                return
            name = (node.name or '').lower()
            if name in ('script', 'style', 'noscript', 'annotation', 'annotation-xml'):
                return
            if name == 'br':
                parts.append('\n')
                return
            if name == 'math':
                parts.append(math_text(node))
                return
            if name == 'img':
                return
            if name in ('sup', 'sub'):
                inner = visible_text(node, collapse=False)
                mapped = _script_unicode(inner, 'superscript' if name == 'sup' else 'subscript')
                parts.append(mapped if mapped is not None else inner)
                return

            def boundary_sep():
                if parts and parts[-1] and not parts[-1][-1].isspace():
                    parts.append('\n')

            for child in node.children:
                if isinstance(child, Tag) and (child.name or '').lower() in HTML_BLOCK_BOUNDARY:
                    boundary_sep()
                    rec(child)
                    boundary_sep()
                else:
                    rec(child)

        rec(el)
        text_value = ''.join(parts)
        if collapse:
            return re.sub(r'[ \t\r\n\f\v]+', ' ', text_value).strip()
        return text_value

    def emit_img(img_tag):
        src = (img_tag.get('src') or '').strip()
        alt = (img_tag.get('alt') or '').strip() or '[image]'
        block = add(alt, locate(str(img_tag)), 'figure')
        if src:
            _copy_local_image(path, src, directory, block, issues)

    def walk(node):
        if isinstance(node, NavigableString):
            return
        if not isinstance(node, Tag):
            return
        name = node.name.lower() if node.name else ''
        if name in BLOCK_TAGS:
            kind = BLOCK_TAGS[name]
            if name == 'table':
                rows = []
                for tr in node.find_all('tr'):
                    cells = [visible_text(td) for td in tr.find_all(['td', 'th'])]
                    rows.append(' | '.join(cells))
                value = '\n'.join(rows).strip()
            elif name == 'pre':
                value = node.get_text()
                value = value.strip('\n')
            else:
                value = visible_text(node)
            if value.strip():
                add(value, locate(value if name != 'pre' else node.get_text()), kind)
            for img in node.find_all('img', recursive=True):
                emit_img(img)
            return
        if name == 'img':
            emit_img(node)
            return
        if name == 'math':
            value = math_text(node)
            if value.strip():
                kind = 'formula' if (node.get('display') or '').lower() == 'block' or value.startswith('$$') else 'paragraph'
                add(value, locate(value), kind)
            return

        buf = []

        def flush_buf():
            nonlocal buf
            if not buf:
                return
            value = re.sub(r'[ \t\r\n\f\v]+', ' ', ''.join(buf)).strip()
            buf = []
            if value:
                add(value, locate(value), 'paragraph')

        for child in list(node.children):
            if type(child) is NavigableString:
                buf.append(str(child))
                continue
            if isinstance(child, NavigableString) or not isinstance(child, Tag):
                continue
            cname = (child.name or '').lower()
            if cname in BLOCK_TAGS or cname == 'img' or cname in CONTAINER_TAGS:
                flush_buf()
                walk(child)
            elif cname == 'math':
                buf.append(math_text(child))
            else:
                t = visible_text(child, collapse=False)
                if t:
                    buf.append(t)
                for img in child.find_all('img', recursive=True):
                    flush_buf()
                    emit_img(img)
        flush_buf()

    root = soup.body if soup.body else soup
    walk(root)


def _import_docx(path, directory, add, issues):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
    W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    WRAPPERS = {
        'hyperlink', 'sdt', 'sdtContent', 'smartTag', 'ins', 'customXml',
        'fldSimple', 'ruby', 'rt', 'rubyBase',
    }

    document = Document(str(path))
    pos = 0
    index = 0

    def emit(value, kind, asset=None):
        nonlocal pos, index
        index += 1
        start = pos
        end = start + len(value)
        block = add(value, {'start': start, 'end': end, 'paragraph_index': index}, kind, asset)
        pos = end + 1
        return block

    def emit_blips(blips):
        seen = set()
        related = document.part.related_parts
        for blip in blips:
            rid = blip.get(f'{{{R_NS}}}embed')
            if not rid or rid in seen:
                continue
            seen.add(rid)
            if rid not in related:
                issues.append({
                    'id': f'docx-image-{len(issues)+1}',
                    'message': 'Word drawing/image not embedded (missing relationship)',
                    'resolution': None,
                })
                continue
            part = related[rid]
            ext = Path(str(part.partname)).suffix.lower()
            if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
                ctype = (part.content_type or '').lower()
                if 'jpeg' in ctype or 'jpg' in ctype:
                    ext = '.jpg'
                elif 'webp' in ctype:
                    ext = '.webp'
                elif 'gif' in ctype:
                    ext = '.gif'
                else:
                    ext = '.png'
            label = '[image]'
            block = emit(label, 'figure')
            name = f"image-{block['id']}{ext}"
            (directory / name).write_bytes(part.blob)
            block['asset'] = name

    def record_unfaithful(math_el):
        issues.append({
            'id': f'docx-omath-{len(issues)+1}',
            'message': (
                'Word equation (oMath) has unsupported constructs; '
                'best-effort math kept in sentence order with unresolved OMML evidence: '
                + _omml_snippet(math_el)
            ),
            'resolution': None,
        })

    def handle_run(run_el, pieces, blips):
        blips.extend(run_el.findall(f'.//{{{A_NS}}}blip'))
        vert = None
        rPr = run_el.find(f'{{{W_NS}}}rPr')
        if rPr is not None:
            va = rPr.find(f'{{{W_NS}}}vertAlign')
            if va is not None:
                vert = _xml_attr(va, 'val')
        texts = []
        for child in run_el:
            loc = _xml_local(child.tag)
            if loc == 't':
                texts.append(child.text or '')
            elif loc == 'tab':
                texts.append('\t')
            elif loc == 'br':
                texts.append('\n')
        raw = ''.join(texts)
        if vert in ('superscript', 'subscript'):
            pieces.append(_run_script_text(raw, vert))
        else:
            pieces.append(raw)

    def walk_inlines(el, pieces, blips, unfaithful):
        for child in el.iterchildren():
            loc = _xml_local(child.tag)
            ns = child.tag.rsplit('}', 1)[0][1:] if '}' in child.tag else ''
            if loc == 'r' and ns == W_NS:
                handle_run(child, pieces, blips)
            elif loc == 'oMath':
                latex, faithful = _omml_to_latex(child)
                pieces.append(_wrap_math(latex, display=False))
                if not faithful:
                    unfaithful.append(child)
            elif loc == 'oMathPara':
                # Every direct m:oMath sibling is a continuation line, in document order.
                inners = [c for c in child if _xml_local(c.tag) == 'oMath']
                if not inners:
                    latex, faithful = _omml_to_latex(child)
                    pieces.append(_wrap_math(latex, display=True))
                    if not faithful:
                        unfaithful.append(child)
                else:
                    lines = []
                    for inner in inners:
                        latex, faithful = _omml_to_latex(inner)
                        lines.append(latex)
                        if not faithful:
                            unfaithful.append(inner)
                    if len(lines) == 1:
                        joined = lines[0]
                    else:
                        # m:aln on a run already inserted '&'. aligned keeps the break and the column.
                        joined = r'\begin{aligned}' + r' \\ '.join(lines) + r'\end{aligned}'
                    pieces.append(_wrap_math(joined, display=True))
            elif loc == 'del':
                continue
            elif loc in WRAPPERS:
                walk_inlines(child, pieces, blips, unfaithful)
            elif loc in ('drawing', 'pict', 'object'):
                blips.extend(child.findall(f'.//{{{A_NS}}}blip'))
            else:
                if loc in ('pPr', 'bookmarkStart', 'bookmarkEnd', 'proofErr', 'commentRangeStart',
                           'commentRangeEnd', 'commentReference'):
                    continue
                walk_inlines(child, pieces, blips, unfaithful)

    def paragraph_content(p_el):
        pieces, blips, unfaithful = [], [], []
        walk_inlines(p_el, pieces, blips, unfaithful)
        value = ''.join(pieces)
        value = re.sub(r'[ \t]+\n', '\n', value).strip()
        return value, blips, unfaithful

    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn('w:p'):
            para = Paragraph(child, document)
            style = (para.style.name if para.style is not None else '') or ''
            heading = bool(re.match(r'Heading\s*[1-6]$', style, re.I) or style.lower().startswith('heading'))
            value, blips, unfaithful = paragraph_content(child)
            if value:
                kind = 'heading' if heading else ('formula' if _math_only_text(value) else 'paragraph')
                emit(value, kind)
            for math_el in unfaithful:
                record_unfaithful(math_el)
            emit_blips(blips)
        elif tag == qn('w:tbl'):
            table = Table(child, document)
            rows = []
            table_unfaithful = []
            try:
                row_iter = table.rows
            except Exception:
                row_iter = []
            for row in row_iter:
                cells = []
                try:
                    cell_iter = row.cells
                except Exception:
                    cell_iter = []
                for cell in cell_iter:
                    cell_parts = []
                    for para in cell.paragraphs:
                        value, _, unfaithful = paragraph_content(para._p)
                        if value:
                            cell_parts.append(value)
                        table_unfaithful.extend(unfaithful)
                    cells.append(' '.join(cell_parts).strip())
                rows.append(' | '.join(cells))
            value = '\n'.join(rows).strip()
            if value:
                emit(value, 'table')
            for math_el in table_unfaithful:
                record_unfaithful(math_el)
            emit_blips(child.findall(f'.//{{{A_NS}}}blip'))

    # Headers/footers, text boxes, OLE, tracked changes are out of scope for this batch.


def _tex_control_word(src, i):
    if i >= len(src) or src[i] != '\\':
        return None, i
    j = i + 1
    if j < len(src) and src[j].isalpha():
        while j < len(src) and src[j].isalpha():
            j += 1
        return src[i + 1:j], j
    if j < len(src):
        return src[j], j + 1
    return None, i


def _tex_verb_end(src, i):
    """Index after \\verb's closing delimiter, or None if src[i] is not \\verb."""
    name, j = _tex_control_word(src, i)
    if name not in ('verb', 'lstinline'):
        return None
    n = len(src)
    if name == 'verb' and j < n and src[j] == '*':
        j += 1
    while j < n and src[j] in ' \t':
        j += 1
    if j >= n or src[j] == '\n':
        return None
    delim = src[j]
    k = src.find(delim, j + 1)
    if k < 0:
        return n
    return k + 1


def _tex_verb_inner(src, i, end):
    name, j = _tex_control_word(src, i)
    if name == 'verb' and j < end and src[j] == '*':
        j += 1
    while j < end and src[j] in ' \t':
        j += 1
    if j >= end:
        return ''
    return src[j + 1:end - 1]


def _tex_expand_verbs(chunk):
    out = []
    i = 0
    n = len(chunk)
    while i < n:
        end = _tex_verb_end(chunk, i)
        if end is not None:
            out.append(_tex_verb_inner(chunk, i, end))
            i = end
            continue
        out.append(chunk[i])
        i += 1
    return ''.join(out)


def _tex_unclosed_end(src, open_at):
    """Index after the line that opened an unclosed group. Always moves past open_at when it can."""
    n = len(src)
    if open_at >= n:
        return n
    nl = src.find('\n', open_at)
    end = n if nl < 0 else nl + 1
    if end <= open_at:
        end = min(open_at + 1, n)
    return end


def _tex_skip_bracket(src, j):
    """Skip a `[...]` group starting at j. Returns (index, closed).

    An unclosed group stops at the end of its line and reports closed=False.
    The index is always greater than j when src[j] is `[`.
    """
    n = len(src)
    if j >= n or src[j] != '[':
        return j, True
    start = j
    depth = 1
    j += 1
    while j < n and depth:
        if src[j] == '\\' and j + 1 < n:
            j += 2
            continue
        if src[j] == '[':
            depth += 1
        elif src[j] == ']':
            depth -= 1
        j += 1
    if depth != 0:
        return _tex_unclosed_end(src, start), False
    return j, True


def _tex_read_brace(src, j):
    """Read a `{...}` argument at j.

    Returns (inner, index, closed). No brace yields (None, j, True).
    A closed brace yields the inner text and the index after `}`.
    An unclosed brace yields the rest of that line (without `{`) and an index
    past the line, with closed=False. The index always advances when a `{` was seen.
    """
    n = len(src)
    if j >= n or src[j] != '{':
        return None, j, True
    inner, nxt = _tex_brace_arg(src, j)
    if inner is not None and nxt > j:
        return inner, nxt, True
    end = _tex_unclosed_end(src, j)
    nl = src.find('\n', j)
    rough = src[j + 1:] if nl < 0 else src[j + 1:nl]
    return rough, end, False


def _tex_skip_groups(src, j):
    """Skip optional `*`, `[...]`, and `{...}` arguments after a command.

    Returns (index, closed). closed is False when a group never terminates.
    Each iteration moves forward or returns, so an unclosed `{` cannot spin.
    """
    n = len(src)
    origin = j
    # One more step than any character can justify; a stuck scanner fails instead of hanging.
    for _ in range(n + 2):
        while j < n and src[j].isspace():
            j += 1
        if j < n and src[j] == '*':
            j += 1
            continue
        if j < n and src[j] == '[':
            j, closed = _tex_skip_bracket(src, j)
            if not closed:
                return j, False
            continue
        if j < n and src[j] == '{':
            _, j, closed = _tex_read_brace(src, j)
            if not closed:
                return j, False
            continue
        return j, True
    if j <= origin and origin < n:
        j = origin + 1
    return min(j, n), False


_TEX_SKIP_CMDS = {
    'documentclass', 'usepackage', 'RequirePackage', 'setlength',
    'newcommand', 'renewcommand', 'providecommand',
    'addbibresource', 'nocite', 'makeindex',
    'titlerunning', 'authorrunning', 'pagestyle', 'thispagestyle',
    'maketitle', 'hypersetup', 'geometry', 'linespread',
    'lstset', 'sisetup', 'numberwithin', 'setcounter',
    'newtheorem', 'theoremstyle', 'newenvironment', 'renewenvironment',
    'DeclareMathOperator', 'makeatletter', 'makeatother',
    'newlength', 'newcounter',
}
_TEX_VERBATIM_ENVS = {'verbatim', 'lstlisting', 'alltt', 'minted'}


def _strip_tex_comments(text):
    """Remove TeX comments (% to EOL) while preserving \\% and verb/verbatim content."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        verb_end = _tex_verb_end(text, i)
        if verb_end is not None:
            out.append(text[i:verb_end])
            i = verb_end
            continue
        if text.startswith('\\begin{', i):
            close = text.find('}', i + 7)
            env = text[i + 7:close] if close > 0 else ''
            if env.rstrip('*') in _TEX_VERBATIM_ENVS or env in _TEX_VERBATIM_ENVS:
                end_tok = '\\end{' + env + '}'
                k = text.find(end_tok, i)
                if k < 0:
                    out.append(text[i:])
                    break
                out.append(text[i:k + len(end_tok)])
                i = k + len(end_tok)
                continue
        ch = text[i]
        if ch == '\\' and i + 1 < n:
            out.append(ch)
            out.append(text[i + 1])
            i += 2
            continue
        if ch == '%':
            while i < n and text[i] != '\n':
                i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _tex_brace_arg(src, start):
    """Return (inner, end_index_after_closing) for a {...} starting at start, or (None, start)."""
    if start >= len(src) or src[start] != '{':
        return None, start
    depth = 0
    i = start
    while i < len(src):
        verb_end = _tex_verb_end(src, i)
        if verb_end is not None:
            i = verb_end if verb_end > i else i + 1
            continue
        if src[i] == '\\' and i + 1 < len(src):
            i += 2
            continue
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[start + 1:i], i + 1
        i += 1
    return None, start


def _tex_unwrap_inline(s):
    """Unwrap a few common text-formatting commands; leave unknown commands mostly intact."""
    patterns = [
        (r'\\(?:textbf|textit|emph|textrm|textsf|texttt|underline|mathrm|mathbf|mathit)\s*\{([^{}]*)\}', r'\1'),
        (r'\\(?:textbf|textit|emph)\s+([A-Za-z0-9]+)', r'\1'),
    ]
    prev = None
    while prev != s:
        prev = s
        for pat, repl in patterns:
            s = re.sub(pat, repl, s)
    s = re.sub(r'\\(?:noindent|bigskip|medskip|smallskip|hfill|vfill|quad|qquad)\b\s*', '', s)
    return s


def _tex_clean_meta(value):
    value = _tex_unwrap_inline(_tex_expand_verbs(value))
    value = re.sub(r'\\and\b', ' ', value)
    value = re.sub(r'\\inst\s*\{[^{}]*\}', '', value)
    value = re.sub(r'\\email\s*\{([^{}]*)\}', r'\1', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def _import_tex(raw, add, issues):
    original = raw.decode('utf-8-sig')
    text = _strip_tex_comments(original)

    def locate_in(src, fragment, cursor):
        frag = fragment.strip()
        if not frag:
            ls, le = _line_span(src, cursor, cursor)
            return {'start': cursor, 'end': cursor, 'line_start': ls, 'line_end': le}, cursor
        idx = src.find(frag, cursor)
        if idx < 0:
            idx = src.find(frag)
        if idx < 0:
            end = min(cursor + len(frag), len(src))
            ls, le = _line_span(src, cursor, end)
            return {'start': cursor, 'end': end, 'line_start': ls, 'line_end': le}, end
        end = idx + len(frag)
        ls, le = _line_span(src, idx, end)
        return {'start': idx, 'end': end, 'line_start': ls, 'line_end': le}, end

    cursor = 0
    sectioning = re.compile(r'\\(title|chapter|section|subsection|subsubsection)(?![A-Za-z])\s*\*?')
    input_cmd = re.compile(r'\\(input|include)(?![A-Za-z])\s*\{')
    includegraphics = re.compile(r'\\includegraphics(?![A-Za-z])(?:\[[^\]]*\])?\s*\{')
    begin_env = re.compile(r'\\begin\{([a-zA-Z*]+)\}')
    CONTAINER_ENVS = {
        'document', 'abstract',
        'itemize', 'enumerate', 'description', 'list', 'trivlist',
        'center', 'quote', 'quotation', 'verse',
        'flushleft', 'flushright',
        'minipage',
        'titlepage', 'sloppypar',
    }

    def matching_end(src, env, start_body):
        begin_tok = '\\begin{' + env + '}'
        end_tok = '\\end{' + env + '}'
        depth = 1
        i = start_body
        nsrc = len(src)
        while i < nsrc:
            verb_end = _tex_verb_end(src, i)
            if verb_end is not None:
                i = verb_end
                continue
            if src.startswith(begin_tok, i):
                depth += 1
                i += len(begin_tok)
                continue
            if src.startswith(end_tok, i):
                depth -= 1
                if depth == 0:
                    return i
                i += len(end_tok)
                continue
            i += 1
        return -1

    def emit_text_run(src, run_start, run_end):
        nonlocal cursor
        chunk = _tex_expand_verbs(src[run_start:run_end])
        parts = re.split(r'(\n\s*\n)', chunk)
        for part in parts:
            if re.fullmatch(r'\n\s*\n', part or '') or not part.strip():
                continue
            value = _tex_unwrap_inline(part.strip())
            if not value or re.fullmatch(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})*', value):
                continue
            if not re.search(r'[A-Za-z0-9]', value):
                continue
            loc, cursor = locate_in(original, part.strip(), cursor)
            add(value, loc, 'paragraph')

    def note_unclosed(kind, detail):
        snippet = re.sub(r'\s+', ' ', (detail or '')).strip()[:80]
        issues.append({
            'id': f'tex-unclosed-{len(issues)+1}',
            'message': (
                f'TeX {kind} has an unclosed argument'
                + (f' ({snippet})' if snippet else '')
                + '; skipped through the end of that line (unresolved)'
            ),
            'resolution': None,
        })

    def parse_span(src):
        nonlocal cursor
        pending_start = 0
        i = 0
        n = len(src)

        def advance(pos, after):
            if after > pos:
                return after
            return min(pos + 1, n)

        last = -1
        while i < n:
            # Every branch must move i. A stuck scanner steps one character and continues.
            if i <= last:
                i = last + 1
                if i >= n:
                    break
            last = i
            verb_end = _tex_verb_end(src, i)
            if verb_end is not None:
                i = verb_end if verb_end > i else i + 1
                continue

            m_env = begin_env.match(src, i)
            m_sec = sectioning.match(src, i)
            m_in = input_cmd.match(src, i)
            m_img = includegraphics.match(src, i)

            if m_env:
                if i > pending_start:
                    emit_text_run(src, pending_start, i)
                env = m_env.group(1)
                end_marker = '\\end{' + env + '}'
                start_body = m_env.end()
                end_idx = matching_end(src, env, start_body)
                if end_idx < 0:
                    body = src[start_body:]
                    next_i = n
                else:
                    body = src[start_body:end_idx]
                    next_i = end_idx + len(end_marker)
                if next_i <= i:
                    next_i = min(i + 1, n)
                body_st = body.strip()
                if env in CONTAINER_ENVS:
                    parse_span(body)
                elif env.rstrip('*') in _TEX_VERBATIM_ENVS or env in _TEX_VERBATIM_ENVS:
                    loc, cursor = locate_in(original, body_st or env, cursor)
                    add(body_st, loc, 'code')
                elif env in ('equation', 'equation*', 'align', 'align*', 'displaymath', 'eqnarray', 'eqnarray*'):
                    loc, cursor = locate_in(original, body_st or env, cursor)
                    add(body_st, loc, 'formula')
                elif env in ('figure', 'table'):
                    cap = re.search(r'\\caption\s*\{', body)
                    if cap:
                        inner, _, cap_closed = _tex_read_brace(body, cap.end() - 1)
                        if not cap_closed:
                            note_unclosed('\\caption', inner)
                        if inner:
                            loc, cursor = locate_in(original, inner.strip(), cursor)
                            add(_tex_unwrap_inline(inner.strip()), loc, 'caption')
                    for _im in includegraphics.finditer(body):
                        issues.append({
                            'id': f'tex-includegraphics-{len(issues)+1}',
                            'message': 'TeX \\includegraphics not embedded (no LaTeX compilation; asset unresolved)',
                            'resolution': None,
                        })
                    cleaned = includegraphics.sub('', body)
                    cleaned = re.sub(r'\\caption\s*\{[^{}]*\}', '', cleaned)
                    cleaned = _tex_unwrap_inline(cleaned)
                    cleaned = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?', '', cleaned)
                    cleaned = cleaned.strip()
                    if cleaned and not cap:
                        loc, cursor = locate_in(original, cleaned[:80], cursor)
                        add(cleaned, loc, 'paragraph')
                else:
                    raw_body = body.strip()
                    if raw_body:
                        loc, cursor = locate_in(original, raw_body, cursor)
                        add(raw_body, loc, 'paragraph')
                        issues.append({
                            'id': f'tex-env-{len(issues)+1}',
                            'message': (
                                f'TeX environment {{{env}}} not fully interpreted '
                                '(raw fragment kept; unresolved)'
                            ),
                            'resolution': None,
                        })
                pending_start = next_i
                i = next_i
                continue

            if m_sec:
                if i > pending_start:
                    emit_text_run(src, pending_start, i)
                j = m_sec.end()
                if j < n and src[j] == '[':
                    j, br_ok = _tex_skip_bracket(src, j)
                    if not br_ok:
                        note_unclosed(f'\\{m_sec.group(1)} optional argument', '')
                        after = advance(i, j)
                        pending_start = after
                        i = after
                        continue
                while j < n and src[j].isspace():
                    j += 1
                title, after, closed = _tex_read_brace(src, j)
                if not closed:
                    note_unclosed(f'\\{m_sec.group(1)}', title)
                if title is None:
                    nl = src.find('\n', j)
                    if nl < 0:
                        nl = n
                    title = src[j:nl].strip()
                    after = nl if nl > i else n
                title = _tex_unwrap_inline((title or '').strip())
                if title:
                    loc, cursor = locate_in(original, title, cursor)
                    add(title, loc, 'heading')
                after = advance(i, after)
                pending_start = after
                i = after
                continue

            if m_in:
                if i > pending_start:
                    emit_text_run(src, pending_start, i)
                inner, after, closed = _tex_read_brace(src, m_in.end() - 1)
                if not closed:
                    note_unclosed(f'\\{m_in.group(1)}', inner)
                name = (inner or '').strip()
                issues.append({
                    'id': f'tex-input-{len(issues)+1}',
                    'message': f'TeX \\{m_in.group(1)}{{{name}}} not expanded (single-file import only; multi-file projects unsupported)',
                    'resolution': None,
                })
                after = advance(i, after)
                pending_start = after
                i = after
                continue

            if m_img:
                if i > pending_start:
                    emit_text_run(src, pending_start, i)
                inner, after, closed = _tex_read_brace(src, m_img.end() - 1)
                if not closed:
                    note_unclosed('\\includegraphics', inner)
                issues.append({
                    'id': f'tex-includegraphics-{len(issues)+1}',
                    'message': f'TeX \\includegraphics{{{(inner or "").strip()}}} not embedded (no LaTeX compilation; asset unresolved)',
                    'resolution': None,
                })
                after = advance(i, after)
                pending_start = after
                i = after
                continue

            name, name_end = _tex_control_word(src, i)
            if name in _TEX_SKIP_CMDS:
                if i > pending_start:
                    emit_text_run(src, pending_start, i)
                after, closed = _tex_skip_groups(src, name_end)
                if not closed:
                    note_unclosed(f'\\{name}', '')
                after = advance(i, after)
                pending_start = after
                i = after
                continue
            if name in ('author', 'institute', 'date'):
                if i > pending_start:
                    emit_text_run(src, pending_start, i)
                j = name_end
                while j < n and src[j].isspace():
                    j += 1
                if j < n and src[j] == '*':
                    j += 1
                    while j < n and src[j].isspace():
                        j += 1
                if j < n and src[j] == '[':
                    j, br_ok = _tex_skip_bracket(src, j)
                    if not br_ok:
                        note_unclosed(f'\\{name} optional argument', '')
                        after = advance(i, j)
                        pending_start = after
                        i = after
                        continue
                    while j < n and src[j].isspace():
                        j += 1
                inner, after, closed = _tex_read_brace(src, j)
                if not closed:
                    note_unclosed(f'\\{name}', inner)
                if inner:
                    value = _tex_clean_meta(inner)
                    if value:
                        loc, cursor = locate_in(original, inner.strip(), cursor)
                        add(value, loc, 'paragraph')
                after = advance(i, after)
                pending_start = after
                i = after
                continue

            i += 1

        if pending_start < n:
            emit_text_run(src, pending_start, n)

    parse_span(text)


def _import_pdf(source, add, pages, issues):
    rendered = pdfium.PdfDocument(source)
    with pdfplumber.open(source, unicode_norm='NFKC') as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            bitmap = rendered[pno-1].render(scale=1.5)
            image = bitmap.to_pil()
            name = f'page-{pno}.png'
            image.save(source.parent / name)
            pages.append({'page': pno, 'width': float(page.width), 'height': float(page.height), 'asset': name})
            words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
            if len(words) < 8 or '\ufffd' in ''.join(w['text'] for w in words):
                issues.append({'id': f'page-{pno}', 'message': f'Page {pno}: scan/low text/encoding uncertainty; inspect original', 'resolution': None})
            # Preserve each word once. Split lines at wide gutters so two columns cannot fuse.
            rows = []
            for word in sorted(words, key=lambda w: (round(w['top']/3), w['x0'])):
                row = next((r for r in rows[-4:] if abs(r[0]['top']-word['top']) < 3), None)
                if row is None:
                    rows.append([word])
                else:
                    row.append(word)
            segments = []
            for row in rows:
                group = []
                for word in sorted(row, key=lambda w: w['x0']):
                    if group and word['x0']-group[-1]['x1'] > 16:
                        segments.append(group)
                        group = []
                    group.append(word)
                if group:
                    segments.append(group)
            for group in sorted(segments, key=lambda g: (g[0]['top'], g[0]['x0'])):
                x0, y0 = min(w['x0'] for w in group), min(w['top'] for w in group)
                x1, y1 = max(w['x1'] for w in group), max(w['bottom'] for w in group)
                value = ' '.join(w['text'] for w in group)
                kind = 'caption' if re.match(r'^(Fig(?:ure)?\.?|Table)\s*\d', value) else 'paragraph'
                add(value, {'page': pno, 'bbox': [x0, y0, x1, y1]}, kind)
            # Full-page image is a guaranteed visual ledger of vector figures and equations.
            add('', {'page': pno, 'bbox': [0, 0, float(page.width), float(page.height)]}, 'page', name)
    rendered.close()


def import_document(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            'Unsupported input format. Supported: text PDF, UTF-8 Markdown (.md/.markdown), '
            'plain text (.txt), HTML archive (.html/.htm), Word (.docx), LaTeX (.tex). '
            'No EPUB, OCR, or URL fetching in this version.'
        )
    doc_id = digest(raw)[:24]
    directory = folder(doc_id)
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / '.import.lock')):
        if (directory / 'document.json').exists():
            return read(doc_id)
        source = directory / ('source' + extension)
        shutil.copyfile(path, source)
        blocks, atoms, pages, issues = [], [], [], []
        add = _add_factory(atoms, blocks)

        if extension == '.pdf':
            _import_pdf(source, add, pages, issues)
        elif extension in ('.md', '.markdown'):
            text = raw.decode('utf-8-sig')
            lines = text.splitlines(keepends=True)
            offsets = [0]
            for line in lines:
                offsets.append(offsets[-1] + len(line))
            covered = set()
            tokens = MarkdownIt('commonmark', {'html': False}).enable('table').parse(text)
            for token in tokens:
                if token.map and token.level == 0:
                    start, end = token.map
                    if any(i in covered for i in range(start, end)):
                        continue
                    covered.update(range(start, end))
                    value = ''.join(lines[start:end]).rstrip('\r\n')
                    kind = {'heading_open': 'heading', 'fence': 'code', 'code_block': 'code', 'table_open': 'table'}.get(token.type, 'paragraph')
                    if value.lstrip().startswith('$$'):
                        kind = 'formula'
                    add(value, {'start': offsets[start], 'end': offsets[end], 'line_start': start+1, 'line_end': end}, kind)
            for i, line in enumerate(lines):
                if i not in covered and line.strip():
                    add(line.rstrip(), {'start': offsets[i], 'end': offsets[i+1], 'line_start': i+1, 'line_end': i+1}, 'unclassified')
            blocks.sort(key=lambda b: atoms[int(b['source_ids'][0][1:])-1]['location']['start'])
            _finish_markdown_images(path, directory, blocks, issues)
        elif extension == '.txt':
            _import_txt(raw, add)
        elif extension in ('.html', '.htm'):
            _import_html(path, raw, directory, add, issues)
        elif extension == '.docx':
            _import_docx(path, directory, add, issues)
        elif extension == '.tex':
            _import_tex(raw, add, issues)

        doc = {'schema_version': 1, 'id': doc_id, 'title': path.stem, 'source_file': source.name,
               'source_sha256': digest(raw), 'revision': 0, 'stage': 'structure',
               'atoms': atoms, 'blocks': blocks, 'pages': pages, 'issues': issues,
               'terms': [], 'notes': [], 'reading': {}, 'submissions': {},
               'structure_review': None, 'full_review': None}
        atomic(directory / 'document.json', doc)
        return doc
