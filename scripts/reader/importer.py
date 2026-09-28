from pathlib import Path
import re
import shutil
import unicodedata
from markdown_it import MarkdownIt
import pdfplumber
import pypdfium2 as pdfium
from filelock import FileLock
from .store import DATA, folder, atomic, digest, read


def norm(text):
    return unicodedata.normalize('NFKC', text)


def import_document(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    extension = path.suffix.lower()
    if extension not in ('.pdf', '.md', '.markdown'):
        raise ValueError('Only text PDF and UTF-8 Markdown are supported')
    doc_id = digest(raw)[:24]
    directory = folder(doc_id)
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / '.import.lock')):
        if (directory / 'document.json').exists():
            return read(doc_id)
        source = directory / ('source' + extension)
        shutil.copyfile(path, source)
        blocks, atoms, pages, issues = [], [], [], []
        def add(text, location, kind='paragraph', asset=None):
            aid = f'a{len(atoms)+1:05d}'
            atoms.append({'id': aid, 'text': text, 'location': location})
            bid = f'b{len(blocks)+1:05d}'
            blocks.append({'id': bid, 'kind': kind, 'text': text, 'source_ids': [aid],
                           'asset': asset, 'translation': None, 'history': [], 'review': None,
                           'structure_note': '', 'user_edited': False})
        if extension != '.pdf':
            text = raw.decode('utf-8-sig')
            lines = text.splitlines(keepends=True)
            offsets = [0]
            for line in lines:
                offsets.append(offsets[-1] + len(line))
            covered = set()
            # Outermost mapped nodes retain exact source, including nested lists/tables/code.
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
            # Local Markdown images are copied; never fetch remote URLs or paths outside source directory.
            for b in blocks:
                match = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', b['text'].strip())
                if match:
                    image = (path.parent / match[2]).resolve()
                    if image.is_relative_to(path.parent) and image.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and image.is_file():
                        name = f"image-{b['id']}{image.suffix.lower()}"
                        shutil.copyfile(image, directory / name)
                        b['asset'] = name
                        b['kind'] = 'figure'
                    else:
                        issues.append({'id': f'image-{b["id"]}', 'message': 'Image not embedded (remote, missing or unsafe path)', 'resolution': None})
        else:
            rendered = pdfium.PdfDocument(source)
            with pdfplumber.open(source, unicode_norm='NFKC') as pdf:
                for pno, page in enumerate(pdf.pages, 1):
                    bitmap = rendered[pno-1].render(scale=1.5)
                    image = bitmap.to_pil()
                    name = f'page-{pno}.png'
                    image.save(directory / name)
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
                        for word in sorted(row, key=lambda w:w['x0']):
                            if group and word['x0']-group[-1]['x1'] > 16:
                                segments.append(group)
                                group = []
                            group.append(word)
                        if group:
                            segments.append(group)
                    for group in sorted(segments, key=lambda g:(g[0]['top'],g[0]['x0'])):
                        x0, y0 = min(w['x0'] for w in group), min(w['top'] for w in group)
                        x1, y1 = max(w['x1'] for w in group), max(w['bottom'] for w in group)
                        value = ' '.join(w['text'] for w in group)
                        kind = 'caption' if re.match(r'^(Fig(?:ure)?\.?|Table)\s*\d', value) else 'paragraph'
                        add(value, {'page':pno,'bbox':[x0,y0,x1,y1]}, kind)
                    # Full-page image is a guaranteed visual ledger of vector figures and equations.
                    add('', {'page':pno,'bbox':[0,0,float(page.width),float(page.height)]}, 'page', name)
            rendered.close()
        doc = {'schema_version':1,'id':doc_id,'title':path.stem,'source_file':source.name,'source_sha256':digest(raw),
               'revision':0,'stage':'structure','atoms':atoms,'blocks':blocks,'pages':pages,'issues':issues,
               'terms':[],'notes':[],'reading':{},'submissions':{},'structure_review':None,'full_review':None}
        atomic(directory / 'document.json', doc)
        return doc
