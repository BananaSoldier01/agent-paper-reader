"""Synthetic fixtures for txt/html/docx/tex import (no personal papers)."""
from pathlib import Path
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
    omath_issues = [i for i in d['issues'] if 'oMath' in i['message'] or 'equation' in i['message'].lower()]
    assert math_blocks or omath_issues, 'oMath must not be silently dropped'
    if math_blocks:
        assert any('E=mc2' in b['text'].replace(' ', '') for b in math_blocks)
    assert omath_issues, 'deferred math support must still record an issue when oMath is present'


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
    math_blocks = [b for b in d['blocks'] if b['kind'] == 'formula' and 'E=mc²' in b['text']]
    omath_issues = [i for i in d['issues'] if 'oMath' in i['message'] or 'equation' in i['message'].lower()]
    assert math_blocks, 'oMath in a table cell needs a plain-text fallback'
    assert omath_issues, 'oMath in a table cell must record an unresolved issue'
