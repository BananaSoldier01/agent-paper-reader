"""Tiny deterministic PDF fixture: two columns, cross-page prose, formula/figure, scan-like page."""
import pytest
from reader import store
from reader.importer import import_document
from reader.workflow import submit,validate

def make_pdf(path):
    contents=[b'BT /F1 12 Tf 40 750 Td (Left column one.) Tj 0 -20 Td (Left column two.) Tj 300 20 Td (Right column one.) Tj 0 -20 Td (Right column two.) Tj -300 -40 Td (Cross-page paragraph begins) Tj ET 40 550 200 80 re S BT /F1 12 Tf 50 570 Td (Q = m c dT) Tj ET',b'BT /F1 12 Tf 40 750 Td (and continues on page two.) Tj 0 -20 Td (Figure 1. A preserved rectangle.) Tj ET',b'40 400 300 300 re f']
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>']
    for i,c in enumerate(contents):
        objects += [f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents {4+2*i} 0 R >>'.encode(),b'<< /Length '+str(len(c)).encode()+b' >>\nstream\n'+c+b'\nendstream']
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    data=b'%PDF-1.4\n';offsets=[0]
    for i,o in enumerate(objects,1):offsets.append(len(data));data+=f'{i} 0 obj\n'.encode()+o+b'\nendobj\n'
    xref=len(data);data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    data+=b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets[1:]);data+=f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode();path.write_bytes(data)

def test_two_column_and_scan_detection(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path/'data')
    path=tmp_path/'fixture.pdf';make_pdf(path);d=import_document(path)
    texts=[a['text'] for a in d['atoms']]
    assert 'Left column one.' in texts and 'Right column one.' in texts
    assert not any('Left column one. Right' in x for x in texts)
    assert any(i['id']=='page-3' and not i['resolution'] for i in d['issues'])
    assert not validate(d)['ok']
    assert len(d['pages'])==3
    # Agent reorders left column before right, joins across pages, retains provenance.
    a={a['text']:a['id'] for a in d['atoms'] if a['text']}
    start=a['Cross-page paragraph begins'];end=a['and continues on page two.']
    blocks=[]
    for b in d['blocks']:
        if end in b['source_ids']:continue
        b['structure_note']='Fixture inspected'
        if start in b['source_ids']:b['source_ids'].append(end);b['text']+=' and continues on page two.'
        blocks.append(b)
    # Select reading order explicitly: left column lines precede right column lines.
    leading=['Left column one.','Left column two.','Right column one.','Right column two.']
    blocks.sort(key=lambda b:leading.index(b['text']) if b['text'] in leading else 4+int(b['id'][1:]))
    d=submit(d['id'],{'revision':0,'submission_id':'layout','operation':'structure','agent':'fixture','note':'Explicit cross-page merge','blocks':blocks})
    assert [b['text'] for b in d['blocks'][:4]]==leading
    assert any(len(b['source_ids'])==2 for b in d['blocks'])
    assert any('Unresolved' in e for e in validate(d)['errors'])


def test_full_structure_reused_page_id_does_not_attach_wrong_page(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    path = tmp_path / 'page-alias.pdf'
    make_pdf(path)
    imported = import_document(path)
    page = next(b for b in imported['blocks'] if b['kind'] == 'page' and b['asset'] == 'page-1.png')
    body = next(b for b in imported['blocks'] if b['text'] == 'and continues on page two.')
    rows = [
        {k: b[k] for k in ('id', 'kind', 'text', 'source_ids')}
        | {'structure_note': 'Synthetic PDF page and text checked'}
        for b in imported['blocks']
    ]
    by_id = {b['id']: b for b in rows}
    by_id[page['id']].update(kind='paragraph', text=body['text'], source_ids=body['source_ids'])
    by_id[body['id']].update(id='b99999', kind='excluded', text='', source_ids=page['source_ids'])
    saved = submit(imported['id'], {
        'revision': 0, 'submission_id': 'page-id-alias', 'operation': 'structure',
        'agent': 'fixture', 'note': 'Page provenance checked', 'blocks': rows,
    })
    aliased = next(b for b in saved['blocks'] if b['id'] == page['id'])
    assert aliased['source_ids'] == body['source_ids']
    assert aliased.get('asset') is None
    assert next(b for b in saved['blocks'] if b['id'] == 'b99999').get('asset') is None
    assert saved['pages'][0]['asset'] == 'page-1.png'


def test_full_structure_same_page_placeholder_keeps_page_image(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    path = tmp_path / 'same-page.pdf'
    make_pdf(path)
    imported = import_document(path)
    rows = [
        {k: b[k] for k in ('id', 'kind', 'text', 'source_ids')}
        | {'structure_note': 'Synthetic PDF source checked'}
        for b in imported['blocks']
    ]
    saved = submit(imported['id'], {
        'revision': 0, 'submission_id': 'same-page-placeholder', 'operation': 'structure',
        'agent': 'fixture', 'note': 'Same source checked', 'blocks': rows,
    })
    for old in (b for b in imported['blocks'] if b['kind'] == 'page'):
        kept = next(b for b in saved['blocks'] if b['id'] == old['id'])
        assert kept['source_ids'] == old['source_ids']
        assert kept['asset'] == old['asset']
    assert [p['asset'] for p in saved['pages']] == [p['asset'] for p in imported['pages']]
