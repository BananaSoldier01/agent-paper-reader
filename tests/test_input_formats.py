"""Synthetic fixtures for txt/html/docx/tex import (no personal papers)."""
from pathlib import Path
import re
import pytest
from reader import store
from reader.importer import import_document


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    return tmp_path


def test_txt_paragraphs(data_dir):
    p = data_dir / 'note.txt'
    p.write_text('First paragraph.\n\nSecond paragraph has more text.\n\n\nThird.\n', encoding='utf-8')
    d = import_document(p)
    texts = [b['text'] for b in d['blocks']]
    assert texts == ['First paragraph.', 'Second paragraph has more text.', 'Third.']
    assert all(b['kind'] == 'paragraph' for b in d['blocks'])
    loc = d['atoms'][0]['location']
    assert loc['start'] == 0 and 'line_start' in loc and 'line_end' in loc
    assert d['source_file'] == 'source.txt'


def test_html_structure_strips_script_and_remote_image(data_dir):
    img = data_dir / 'local.png'
    img.write_bytes(
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    p = data_dir / 'article.html'
    p.write_text(
        '<html><head><style>.x{color:red}</style><script>alert(1)</script></head>'
        '<body><h1>Title One</h1><p>Hello body.</p>'
        '<img src="https://example.com/x.png" alt="remote">'
        '<img src="local.png" alt="local pic">'
        '<pre>code block</pre>'
        '<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>'
        '<ul><li>Item one</li></ul>'
        '</body></html>',
        encoding='utf-8',
    )
    d = import_document(p)
    kinds = [b['kind'] for b in d['blocks']]
    texts = [b['text'] for b in d['blocks']]
    assert 'Title One' in texts
    assert 'Hello body.' in texts
    assert any(b['kind'] == 'heading' for b in d['blocks'])
    assert any(b['kind'] == 'code' and 'code block' in b['text'] for b in d['blocks'])
    assert any(b['kind'] == 'table' and 'A' in b['text'] and '1' in b['text'] for b in d['blocks'])
    assert any(b['kind'] == 'paragraph' and 'Item one' in b['text'] for b in d['blocks'])
    assert not any('alert' in b['text'] for b in d['blocks'])
    assert any('remote' in (i['message'].lower()) or 'Image not embedded' in i['message'] for i in d['issues'])
    assert any(b.get('asset') for b in d['blocks'] if b['kind'] == 'figure')


def test_docx_heading_paragraph_table(data_dir):
    from docx import Document
    p = data_dir / 'paper.docx'
    doc = Document()
    doc.add_heading('Docx Title', level=1)
    doc.add_paragraph('Body paragraph about cooling.')
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = 'Name'
    table.cell(0, 1).text = 'Value'
    table.cell(1, 0).text = 'PUE'
    table.cell(1, 1).text = '1.2'
    doc.save(p)
    d = import_document(p)
    assert any(b['kind'] == 'heading' and 'Docx Title' in b['text'] for b in d['blocks'])
    assert any(b['kind'] == 'paragraph' and 'cooling' in b['text'] for b in d['blocks'])
    assert any(b['kind'] == 'table' and 'PUE' in b['text'] for b in d['blocks'])
    loc = d['atoms'][0]['location']
    assert 'paragraph_index' in loc and loc['paragraph_index'] == 1
    assert 'start' in loc and 'end' in loc


def test_tex_section_input_comment_includegraphics(data_dir):
    p = data_dir / 'main.tex'
    p.write_text(
        r'''\documentclass{article}
% This comment should vanish
\section{Intro}
Plain paragraph with \textbf{bold} text.

\input{other.tex}
\includegraphics{fig.png}

\begin{equation}
E=mc^2
\end{equation}

\begin{verbatim}
raw code
\end{verbatim}
''',
        encoding='utf-8',
    )
    d = import_document(p)
    texts = [b['text'] for b in d['blocks']]
    assert any(b['kind'] == 'heading' and b['text'] == 'Intro' for b in d['blocks'])
    assert any('bold' in b['text'] and 'comment' not in b['text'].lower() for b in d['blocks'])
    assert not any('This comment should vanish' in b['text'] for b in d['blocks'])
    assert any('not expanded' in i['message'] and 'other.tex' in i['message'] for i in d['issues'])
    assert any('includegraphics' in i['message'] for i in d['issues'])
    assert any(b['kind'] == 'formula' and 'E=mc^2' in b['text'].replace(' ', '') for b in d['blocks'])
    assert any(b['kind'] == 'code' and 'raw code' in b['text'] for b in d['blocks'])
    loc = d['atoms'][0]['location']
    assert 'line_start' in loc and 'start' in loc


def test_unsupported_extension(data_dir):
    p = data_dir / 'x.epub'
    p.write_bytes(b'PK fake')
    with pytest.raises(ValueError, match='Unsupported|Supported'):
        import_document(p)


def test_htm_alias(data_dir):
    p = data_dir / 'a.htm'
    p.write_text('<html><body><p>Hi there.</p></body></html>', encoding='utf-8')
    d = import_document(p)
    assert any('Hi there.' in b['text'] for b in d['blocks'])


def test_tex_full_document_wrapper(data_dir):
    """P1: begin{document}...end{document} must recurse, not collapse to one cleaned paragraph."""
    p = data_dir / 'wrapped.tex'
    content = (
        "\\documentclass{article}\n"
        "\\title{Measured Speeds}\n"
        "\\begin{document}\n"
        "\\section{Results}\n"
        "The measured speed is $v=\\frac{a}{b}$.\n"
        "\n"
        "\\begin{equation}\n"
        "E=mc^2\n"
        "\\end{equation}\n"
        "\n"
        "\\input{other.tex}\n"
        "\\includegraphics{fig.png}\n"
        "\\end{document}\n"
    )
    p.write_text(content, encoding='utf-8')
    d = import_document(p)
    # Title and/or section heading preserved (not wiped by document-env collapse).
    assert any(b['kind'] == 'heading' and ('Measured Speeds' in b['text'] or 'Results' in b['text']) for b in d['blocks'])
    assert any(b['kind'] == 'heading' and b['text'] == 'Results' for b in d['blocks'])
    # Fraction numerator/denominator not stripped by naive macro cleaning.
    para = next(b for b in d['blocks'] if 'The measured speed' in b['text'])
    assert '\\frac' in para['text']
    assert 'a' in para['text'] and 'b' in para['text']
    # Must not look like naive macro-strip residue (e.g. v= {t} / empty braces).
    assert '{t}' not in para['text']
    assert 'v= {}' not in para['text'].replace(' ', '')
    assert any(b['kind'] == 'formula' and 'E=mc^2' in b['text'].replace(' ', '') for b in d['blocks'])
    assert any('not expanded' in i['message'] and 'other.tex' in i['message'] for i in d['issues'])
    assert any('includegraphics' in i['message'] for i in d['issues'])
    # Must not collapse the whole document body into a single mangled paragraph.
    assert len(d['blocks']) >= 3


def test_html_div_article_span_body_text(data_dir):
    """P1: container tags with direct text must yield blocks."""
    p = data_dir / 'containers.html'
    p.write_text(
        '<html><body>'
        '<div>Visible body text.</div>'
        '<article>Article body.</article>'
        '<span>Span text.</span>'
        '<main>Main landmark text.</main>'
        '<section><p>Nested paragraph stays a block.</p>Trailing section text.</section>'
        '</body></html>',
        encoding='utf-8',
    )
    d = import_document(p)
    texts = [b['text'] for b in d['blocks']]
    assert any('Visible body text.' in t for t in texts)
    assert any('Article body.' in t for t in texts)
    assert any('Span text.' in t for t in texts)
    assert any('Main landmark text.' in t for t in texts)
    assert any('Nested paragraph stays a block.' in t for t in texts)
    assert any('Trailing section text.' in t for t in texts)


def test_html_image_only_paragraph(data_dir):
    """P2: <p><img></p> must still produce a figure asset (not gated on paragraph text)."""
    from PIL import Image as PILImage
    from reader.store import folder
    img = data_dir / 'solo.png'
    PILImage.new('RGB', (4, 4), color=(0, 128, 255)).save(img)
    p = data_dir / 'img_only.html'
    p.write_text(
        '<html><body><p><img src="solo.png" alt="solo"></p></body></html>',
        encoding='utf-8',
    )
    d = import_document(p)
    figs = [b for b in d['blocks'] if b['kind'] == 'figure']
    assert len(figs) >= 1
    assert any(b.get('asset') for b in figs)
    assert any('solo' in b['text'] for b in figs)
    directory = folder(d['id'])
    assert any((directory / b['asset']).is_file() for b in figs if b.get('asset'))


def test_html_multi_img_paragraph(data_dir):
    """P2: multiple imgs in one <p> get distinct figure blocks/assets (no overwrite)."""
    from PIL import Image as PILImage
    from reader.store import folder
    a = data_dir / 'a.png'
    b = data_dir / 'b.png'
    PILImage.new('RGB', (4, 4), color=(255, 0, 0)).save(a)
    PILImage.new('RGB', (4, 4), color=(0, 255, 0)).save(b)
    p = data_dir / 'multi_img.html'
    p.write_text(
        '<html><body><p>Two pics<img src="a.png" alt="red"><img src="b.png" alt="green"></p></body></html>',
        encoding='utf-8',
    )
    d = import_document(p)
    assert any(b['kind'] == 'paragraph' and 'Two pics' in b['text'] for b in d['blocks'])
    figs = [b for b in d['blocks'] if b['kind'] == 'figure' and b.get('asset')]
    assert len(figs) >= 2
    assets = [b['asset'] for b in figs]
    assert len(set(assets)) == len(assets), assets
    directory = folder(d['id'])
    for name in assets:
        assert (directory / name).is_file()


def test_docx_heading_caption_math_picture(data_dir):
    """P2: drawings become figure assets; oMath is detected (text fallback and/or issue), never silent."""
    from docx import Document
    from docx.oxml import parse_xml
    from PIL import Image as PILImage
    from reader.store import folder

    img = data_dir / 'pic.png'
    PILImage.new('RGB', (8, 8), color=(200, 100, 50)).save(img)

    p = data_dir / 'rich.docx'
    doc = Document()
    doc.add_heading('Docx Math Pic Title', level=1)
    cap = doc.add_paragraph('Figure caption about the sample.')
    cap.add_run().add_picture(str(img))
    only = doc.add_paragraph()
    only.add_run().add_picture(str(img))
    omath = parse_xml(
        '<m:oMathPara xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        '<m:oMath><m:r><m:t>E=mc2</m:t></m:r></m:oMath>'
        '</m:oMathPara>'
    )
    mp = doc.add_paragraph()
    mp._p.append(omath)
    doc.save(p)

    d = import_document(p)
    assert any(b['kind'] == 'heading' and 'Docx Math Pic Title' in b['text'] for b in d['blocks'])
    assert any('caption' in b['text'].lower() or 'Figure caption' in b['text'] for b in d['blocks'])
    figs = [b for b in d['blocks'] if b['kind'] == 'figure' and b.get('asset')]
    assert len(figs) >= 1
    directory = folder(d['id'])
    assert any((directory / b['asset']).is_file() for b in figs)
    math_blocks = [b for b in d['blocks'] if b['kind'] == 'formula' or 'E=mc2' in b['text'].replace(' ', '')]
    assert math_blocks, 'oMath must not be silently dropped'
    assert any('E=mc2' in b['text'].replace(' ', '') for b in math_blocks)


def test_html_comment_and_nonvisible_nodes_not_imported(data_dir):
    """P2 regression: Comment/Doctype/other non-visible strings are not body text."""
    p = data_dir / 'comment.html'
    p.write_text(
        '<html><body><!-- internal draft: do not publish --><p>Public article.</p></body></html>',
        encoding='utf-8',
    )
    d = import_document(p)
    assert [b['text'] for b in d['blocks']] == ['Public article.']

    other = data_dir / 'nonvisible.html'
    other.write_text(
        '<!DOCTYPE html><!-- top secret -->'
        '<html><body><![CDATA[hidden cdata]]><?pi secret?>'
        '<p>Visible only.</p></body></html>',
        encoding='utf-8',
    )
    d2 = import_document(other)
    assert [b['text'] for b in d2['blocks']] == ['Visible only.']


def test_html_inline_span_merges_into_one_paragraph(data_dir):
    p = data_dir / 'span_inline.html'
    p.write_text(
        '<html><body><div>The result is <span>not</span> significant.</div></body></html>',
        encoding='utf-8',
    )
    d = import_document(p)
    assert [b['text'] for b in d['blocks']] == ['The result is not significant.']


def test_tex_list_environment_preserves_math_and_issues(data_dir):
    """P1: itemize must recurse so \\frac survives and \\input/\\includegraphics emit issues."""
    p = data_dir / 'list.tex'
    p.write_text(
        "\\begin{itemize}\n"
        "\\item The speed is $v=\\frac{d}{t}$.\n"
        "\\input{missing}\n"
        "\\includegraphics{missing.png}\n"
        "\\end{itemize}\n"
        "\n"
        "\\begin{custombox}\n"
        "Unparsed $v=\\frac{a}{b}$ stays raw.\n"
        "\\end{custombox}\n",
        encoding='utf-8',
    )
    d = import_document(p)
    para = next(b for b in d['blocks'] if 'The speed is' in b['text'])
    assert r'\frac{d}{t}' in para['text']
    assert '{t}' not in para['text'].replace(r'\frac{d}{t}', '')
    assert any(
        'not expanded' in i['message'] and 'missing' in i['message'] for i in d['issues']
    )
    assert any(
        'includegraphics' in i['message'] and 'missing.png' in i['message'] for i in d['issues']
    )
    raw = next(b for b in d['blocks'] if r'\frac{a}{b}' in b['text'])
    assert 'stays raw' in raw['text']
    assert any(
        'custombox' in i['message'] and 'unresolved' in i['message'] and i['resolution'] is None
        for i in d['issues']
    )


def test_docx_table_cell_image_and_math(data_dir):
    """P2: table cells keep text plus drawing asset and oMath fallback (never silent)."""
    from docx import Document
    from docx.oxml import parse_xml
    from PIL import Image as PILImage
    from reader.store import folder

    img = data_dir / 'cell.png'
    PILImage.new('RGB', (6, 6), color=(1, 2, 3)).save(img)

    p = data_dir / 'cell.docx'
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = 'Cell content.'
    para = cell.paragraphs[0]
    para.add_run().add_picture(str(img))
    omath = parse_xml(
        '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        '<m:r><m:t>E=mc²</m:t></m:r></m:oMath>'
    )
    para._p.append(omath)
    doc.save(p)

    d = import_document(p)
    assert any(b['kind'] == 'table' and 'Cell content.' in b['text'] for b in d['blocks'])
    figs = [b for b in d['blocks'] if b['kind'] == 'figure' and b.get('asset')]
    assert figs, 'table cell image must become a figure asset'
    directory = folder(d['id'])
    assert any((directory / b['asset']).is_file() and (directory / b['asset']).stat().st_size > 0 for b in figs)
    table = next(b for b in d['blocks'] if b['kind'] == 'table')
    assert 'E=mc²' in table['text'].replace(' ', '') or 'E=mc^2' in table['text'].replace(' ', '')
    assert 'Cell content.' in table['text']


M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'


def _omath(xml):
    from docx.oxml import parse_xml
    return parse_xml(f'<m:oMath xmlns:m="{M_NS}">{xml}</m:oMath>')


def _omath_para(xml):
    from docx.oxml import parse_xml
    return parse_xml(
        f'<m:oMathPara xmlns:m="{M_NS}"><m:oMath>{xml}</m:oMath></m:oMathPara>'
    )


def test_docx_inline_math_keeps_sentence_order(data_dir):
    """P1: text runs and oMath stay in document order; fractions/scripts/grouping kept."""
    from docx import Document

    p = data_dir / 'inline_math.docx'
    doc = Document()
    para = doc.add_paragraph()
    para.add_run('Text A ')
    para._p.append(_omath('<m:sSub><m:e><m:r><m:t>h</m:t></m:r></m:e><m:sub><m:r><m:t>1</m:t></m:r></m:sub></m:sSub>'))
    para.add_run(' text B ')
    para._p.append(_omath('<m:sSub><m:e><m:r><m:t>p</m:t></m:r></m:e><m:sub><m:r><m:t>2</m:t></m:r></m:sub></m:sSub>'))
    para.add_run(' text C.')
    frac = doc.add_paragraph()
    frac.add_run('Ratio ')
    frac._p.append(_omath(
        '<m:f><m:num><m:r><m:t>a</m:t></m:r></m:num>'
        '<m:den><m:r><m:t>b</m:t></m:r></m:den></m:f>'
    ))
    frac.add_run(' follows.')
    group = doc.add_paragraph()
    group.add_run('Group ')
    group._p.append(_omath(
        '<m:d><m:dPr><m:begChr m:val="("/><m:endChr m:val=")"/></m:dPr>'
        '<m:e><m:r><m:t>x+y</m:t></m:r></m:e></m:d>'
    ))
    group.add_run('.')
    disp = doc.add_paragraph()
    disp._p.append(_omath_para(
        '<m:sSub><m:e><m:r><m:t>h</m:t></m:r></m:e><m:sub><m:r><m:t>0</m:t></m:r></m:sub></m:sSub>'
        '<m:r><m:t>=</m:t></m:r>'
        '<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>m</m:t></m:r></m:den></m:f>'
    ))
    table = doc.add_table(rows=1, cols=1)
    cell_para = table.cell(0, 0).paragraphs[0]
    cell_para.add_run('Cell ')
    cell_para._p.append(_omath(
        '<m:f><m:num><m:r><m:t>n</m:t></m:r></m:num>'
        '<m:den><m:r><m:t>m</m:t></m:r></m:den></m:f>'
    ))
    doc.save(p)

    d = import_document(p)
    inline = next(b for b in d['blocks'] if 'Text A' in b['text'])
    assert inline['kind'] == 'paragraph'
    assert re.search(r'Text A\s*\$.+\$\s*text B\s*\$.+\$\s*text C\.', inline['text'])
    assert 'h' in inline['text'] and 'p' in inline['text']
    assert not any(b['id'] != inline['id'] and b['kind'] == 'formula' and ('h_1' in b['text'] or b['text'] in ('h1', 'p2')) for b in d['blocks'])
    ratio = next(b for b in d['blocks'] if 'Ratio' in b['text'])
    assert r'\frac' in ratio['text'] and '{a}' in ratio['text'] and '{b}' in ratio['text']
    grouped = next(b for b in d['blocks'] if 'Group' in b['text'])
    assert '(x+y)' in grouped['text'].replace(' ', '') or r'(x+y)' in grouped['text']
    display = next(b for b in d['blocks'] if b['kind'] == 'formula')
    assert r'\frac' in display['text']
    cell = next(b for b in d['blocks'] if b['kind'] == 'table')
    assert 'Cell' in cell['text'] and r'\frac' in cell['text']


def test_docx_plain_super_subscript_runs(data_dir):
    """P1: vertAlign superscript/subscript stays distinct; mixed equals stays in place."""
    from docx import Document

    p = data_dir / 'scripts.docx'
    doc = Document()
    sq = doc.add_paragraph()
    sq.add_run('128')
    r = sq.add_run('2')
    r.font.superscript = True
    sq.add_run(' image.')
    cu = doc.add_paragraph()
    cu.add_run('128')
    r = cu.add_run('3')
    r.font.superscript = True
    cu.add_run(' volume.')
    mixed = doc.add_paragraph()
    mixed.add_run('38')
    r = mixed.add_run('2')
    r.font.superscript = True
    mixed._p.append(_omath('<m:r><m:t>=</m:t></m:r>'))
    mixed.add_run('1,444')
    sub = doc.add_paragraph()
    sub.add_run('H')
    r = sub.add_run('2')
    r.font.subscript = True
    sub.add_run('O.')
    doc.save(p)

    d = import_document(p)
    texts = [b['text'] for b in d['blocks']]
    assert any('128²' in t for t in texts)
    assert any('128³' in t for t in texts)
    assert not any(re.search(r'1282\b', t) for t in texts)
    assert not any(re.search(r'1283\b', t) for t in texts)
    mixed_t = next(t for t in texts if '1,444' in t or '1444' in t)
    eq_at = mixed_t.find('$=$')
    if eq_at < 0:
        eq_at = mixed_t.find('=')
    assert eq_at > mixed_t.find('38')
    assert '1,444' in mixed_t[eq_at:]
    assert any('H₂O' in t or 'H$_{2}$O' in t or 'H$_2$O' in t for t in texts)


def test_tex_verb_not_treated_as_structure(data_dir):
    """P1: \\verb delimiters hide section/input/env tokens from the structure scan."""
    p = data_dir / 'verb.tex'
    p.write_text(
        'Tags look like: \\verb |\\section| or \\verb |\\paragraph|.\n'
        '\\section{Actual heading}\n'
        'Actual body.\n'
        '\n'
        'Also \\verb |\\input{secret.tex}| and \\verb |\\begin{document}| stay literal.\n',
        encoding='utf-8',
    )
    d = import_document(p)
    headings = [b for b in d['blocks'] if b['kind'] == 'heading']
    assert [b['text'] for b in headings] == ['Actual heading']
    sample = next(b for b in d['blocks'] if 'Tags look like' in b['text'])
    assert r'\section' in sample['text']
    assert r'\paragraph' in sample['text']
    assert sample['kind'] == 'paragraph'
    literal = next(b for b in d['blocks'] if 'stay literal' in b['text'])
    assert r'\input{secret.tex}' in literal['text']
    assert r'\begin{document}' in literal['text']
    assert not any('secret.tex' in i['message'] for i in d['issues'])
    assert not any(b['kind'] == 'heading' and 'paragraph' in b['text'].lower() for b in d['blocks'])


def test_tex_full_control_words_and_preamble_metadata(data_dir):
    """P2: titlerunning is not title; preamble macros stay out of body; metadata is kept."""
    wrapped = data_dir / 'meta.tex'
    wrapped.write_text(
        '\\documentclass{article}\n'
        '\\usepackage{graphicx}\n'
        '\\newcommand{\\foo}{bar}\n'
        '\\titlerunning{HTML papers on arXiv}\n'
        '\\title{Real Title}\n'
        '\\author{Ada Lovelace}\n'
        '\\begin{document}\n'
        '\\section{Body}\n'
        'Hello body.\n'
        '\\end{document}\n',
        encoding='utf-8',
    )
    d = import_document(wrapped)
    texts = [b['text'] for b in d['blocks']]
    headings = [b['text'] for b in d['blocks'] if b['kind'] == 'heading']
    assert 'Real Title' in headings
    assert 'Body' in headings
    assert not any('running{' in t or t.startswith('running') for t in texts)
    assert not any('usepackage' in t or 'newcommand' in t or '\\foo' in t for t in texts)
    assert any('Ada Lovelace' in t for t in texts)
    assert any('Hello body.' in t for t in texts)

    bare = data_dir / 'bare.tex'
    bare.write_text(
        '\\title{Outside Title}\n'
        '\\author{Grace Hopper}\n'
        '\\titlerunning{should not be title}\n'
        '\\newcommand{\\x}{y}\n'
        'Hello body without document env.\n',
        encoding='utf-8',
    )
    d2 = import_document(bare)
    headings2 = [b['text'] for b in d2['blocks'] if b['kind'] == 'heading']
    texts2 = [b['text'] for b in d2['blocks']]
    assert 'Outside Title' in headings2
    assert any('Grace Hopper' in t for t in texts2)
    assert not any('should not be title' in t for t in texts2)
    assert not any(t.startswith('running') for t in headings2)
    assert any('Hello body without document env.' in t for t in texts2)
    assert not any('\\x' == t or t.startswith('\\newcommand') for t in texts2)


def test_html_mathml_annotation_and_inline_spacing(data_dir):
    """P2: TeX logos stay unspaced; MathML annotation is not duplicated; keep real word spaces."""
    p = data_dir / 'mathml.html'
    p.write_text(
        '<html><body>'
        '<p>From <span class="ltx_TeX_logo" style="letter-spacing:-0.2em;">'
        'T<span>e</span>X</span> '
        '<math alttext="\\rightarrow" display="inline">'
        '<semantics><mo stretchy="false">→</mo>'
        '<annotation encoding="application/x-tex">\\rightarrow</annotation>'
        '</semantics></math> PDF.</p>'
        '<p>Keep <em>inline</em> space.</p>'
        '<p>Glue<span>d</span>word</p>'
        '</body></html>',
        encoding='utf-8',
    )
    d = import_document(p)
    texts = [b['text'] for b in d['blocks']]
    logo = next(t for t in texts if 'PDF' in t)
    assert 'TeX' in logo
    assert 'T e X' not in logo
    assert logo.count('\\rightarrow') + logo.count('→') == 1
    assert not ('→' in logo and '\\rightarrow' in logo)
    assert any(t == 'Keep inline space.' for t in texts)
    assert any(t == 'Gluedword' for t in texts)

