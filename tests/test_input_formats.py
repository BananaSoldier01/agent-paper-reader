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
