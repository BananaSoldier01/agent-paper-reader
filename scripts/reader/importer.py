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

    def cell_text(el):
        return ' '.join(el.stripped_strings)

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
                    cells = [cell_text(td) for td in tr.find_all(['td', 'th'])]
                    rows.append(' | '.join(cells))
                value = '\n'.join(rows).strip()
            elif name == 'pre':
                value = node.get_text()
                # Prefer inner text without outer whitespace-only padding extremes.
                value = value.strip('\n')
            else:
                value = ' '.join(node.stripped_strings)
            if value.strip():
                block = add(value, locate(value if name != 'pre' else node.get_text()), kind)
                # Images directly inside this block (e.g. lone figure paragraph).
                for img in node.find_all('img', recursive=True):
                    src = (img.get('src') or '').strip()
                    if src:
                        _copy_local_image(path, src, directory, block, issues)
            # Do not descend into children already captured as a block unit.
            return
        if name == 'img':
            src = (node.get('src') or '').strip()
            alt = (node.get('alt') or '').strip() or '[image]'
            block = add(alt, locate(str(node)), 'figure')
            if src:
                _copy_local_image(path, src, directory, block, issues)
            return
        for child in list(node.children):
            walk(child)

    root = soup.body if soup.body else soup
    walk(root)


def _import_docx(path, add, issues):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    document = Document(str(path))
    # Concatenated plaintext view for start/end; paragraph_index is 1-based over body block items.
    pos = 0
    index = 0

    def emit(value, kind):
        nonlocal pos, index
        index += 1
        start = pos
        end = start + len(value)
        add(value, {'start': start, 'end': end, 'paragraph_index': index}, kind)
        pos = end + 1  # newline separator in the virtual plaintext view

    # Walk body in document order (paragraphs and tables).
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn('w:p'):
            para = Paragraph(child, document)
            text = para.text
            if not text or not text.strip():
                continue
            style = (para.style.name if para.style is not None else '') or ''
            kind = 'heading' if re.match(r'Heading\s*[1-6]$', style, re.I) or style.lower().startswith('heading') else 'paragraph'
            emit(text, kind)
        elif tag == qn('w:tbl'):
            table = Table(child, document)
            rows = []
            for row in table.rows:
                try:
                    cells = [c.text.strip() for c in row.cells]
                except Exception:
                    cells = []
                rows.append(' | '.join(cells))
            value = '\n'.join(rows).strip()
            if value:
                emit(value, 'table')

    # Headers/footers, text boxes, OLE, tracked changes are out of scope for this batch.


def _strip_tex_comments(text):
    """Remove TeX comments (% to EOL) while preserving \\%."""
    out = []
    i = 0
    n = len(text)
    while i < n:
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
    # Soft-strip simple non-arg commands that are pure styling switches.
    s = re.sub(r'\\(?:noindent|bigskip|medskip|smallskip|hfill|vfill|quad|qquad)\b\s*', '', s)
    return s


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
    sectioning = re.compile(r'\\(title|chapter|section|subsection|subsubsection)\s*(\*?)')
    input_cmd = re.compile(r'\\(input|include)\s*\{')
    includegraphics = re.compile(r'\\includegraphics(?:\[[^\]]*\])?\s*\{')
    begin_env = re.compile(r'\\begin\{([a-zA-Z*]+)\}')

    def emit_text_run(run_start, run_end):
        nonlocal cursor
        chunk = text[run_start:run_end]
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

    pending_start = 0
    i = 0
    n = len(text)
    while i < n:
        m_env = begin_env.match(text, i)
        m_sec = sectioning.match(text, i)
        m_in = input_cmd.match(text, i)
        m_img = includegraphics.match(text, i)

        if m_env:
            if i > pending_start:
                emit_text_run(pending_start, i)
            env = m_env.group(1)
            end_marker = '\\end{' + env + '}'
            start_body = m_env.end()
            end_idx = text.find(end_marker, start_body)
            if end_idx < 0:
                body = text[start_body:]
                next_i = n
            else:
                body = text[start_body:end_idx]
                next_i = end_idx + len(end_marker)
            body_st = body.strip()
            if env in ('verbatim', 'lstlisting', 'alltt', 'minted'):
                loc, cursor = locate_in(original, body_st or env, cursor)
                add(body_st, loc, 'code')
            elif env in ('equation', 'equation*', 'align', 'align*', 'displaymath', 'eqnarray', 'eqnarray*'):
                loc, cursor = locate_in(original, body_st or env, cursor)
                add(body_st, loc, 'formula')
            elif env in ('figure', 'table'):
                cap = re.search(r'\\caption\s*\{', body)
                if cap:
                    inner, _ = _tex_brace_arg(body, cap.end() - 1)
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
                cleaned = _tex_unwrap_inline(body)
                cleaned = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?', ' ', cleaned)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                if cleaned:
                    loc, cursor = locate_in(original, cleaned[:80], cursor)
                    add(cleaned, loc, 'paragraph')
            pending_start = next_i
            i = next_i
            continue

        if m_sec:
            if i > pending_start:
                emit_text_run(pending_start, i)
            j = m_sec.end()
            if j < n and text[j] == '[':
                depth = 0
                while j < n:
                    if text[j] == '\\' and j + 1 < n:
                        j += 2
                        continue
                    if text[j] == '[':
                        depth += 1
                    elif text[j] == ']':
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                    j += 1
            while j < n and text[j].isspace():
                j += 1
            title, after = _tex_brace_arg(text, j)
            if title is None:
                nl = text.find('\n', j)
                if nl < 0:
                    nl = n
                title = text[j:nl].strip()
                after = nl
            title = _tex_unwrap_inline(title.strip())
            if title:
                loc, cursor = locate_in(original, title, cursor)
                add(title, loc, 'heading')
            pending_start = after
            i = after
            continue

        if m_in:
            if i > pending_start:
                emit_text_run(pending_start, i)
            inner, after = _tex_brace_arg(text, m_in.end() - 1)
            name = (inner or '').strip()
            issues.append({
                'id': f'tex-input-{len(issues)+1}',
                'message': f'TeX \\{m_in.group(1)}{{{name}}} not expanded (single-file import only; multi-file projects unsupported)',
                'resolution': None,
            })
            pending_start = after
            i = after
            continue

        if m_img:
            if i > pending_start:
                emit_text_run(pending_start, i)
            inner, after = _tex_brace_arg(text, m_img.end() - 1)
            issues.append({
                'id': f'tex-includegraphics-{len(issues)+1}',
                'message': f'TeX \\includegraphics{{{(inner or "").strip()}}} not embedded (no LaTeX compilation; asset unresolved)',
                'resolution': None,
            })
            pending_start = after
            i = after
            continue

        i += 1

    if pending_start < n:
        emit_text_run(pending_start, n)



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
            _import_docx(path, add, issues)
        elif extension == '.tex':
            _import_tex(raw, add, issues)

        doc = {'schema_version': 1, 'id': doc_id, 'title': path.stem, 'source_file': source.name,
               'source_sha256': digest(raw), 'revision': 0, 'stage': 'structure',
               'atoms': atoms, 'blocks': blocks, 'pages': pages, 'issues': issues,
               'terms': [], 'notes': [], 'reading': {}, 'submissions': {},
               'structure_review': None, 'full_review': None}
        atomic(directory / 'document.json', doc)
        return doc
