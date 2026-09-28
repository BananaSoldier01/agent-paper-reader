from pathlib import Path
import pytest
from reader import store
from reader.importer import import_document
from reader.exporter import export_html

def test_incomplete_export_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path/'data')
    p=tmp_path/'incomplete.md';p.write_text('# Unfinished\n\nThis paper has not been translated yet.')
    d=import_document(p)
    with pytest.raises(ValueError,match='Complete translation'):
        export_html(d['id'])

def test_bundle_is_not_workspace():
    assert store.BUNDLE.is_dir()
    assert not store.ROOT.is_relative_to(store.PACKAGE_ROOT)

def test_large_snapshot_declares_encoding_before_payload(tmp_path,monkeypatch):
    from reader import exporter
    from reader.workflow import submit,fingerprint
    from reader.store import digest
    monkeypatch.setattr(store,'DATA',tmp_path/'data')
    monkeypatch.setattr(exporter,'ROOT',tmp_path)
    p=tmp_path/'encoding.md';p.write_text('Context matters. '*100)
    d=import_document(p)
    def send(op,**kw):
        nonlocal d
        d=submit(d['id'],dict(operation=op,revision=d['revision'],submission_id=f'enc-{op}',agent='test',**kw))
    send('structure',blocks=[dict(b,structure_note='Encoding fixture') for b in d['blocks']],note='Fixture')
    send('terms',terms=[])
    b=d['blocks'][0];text='语境很重要。'*100
    send('translate',blocks=[dict(id=b['id'],translation=dict(text=text,pairs=[dict(id='g1',source=[[0,len(b['text'])]],target=[[0,len(text)]])]))])
    send('review',blocks=[dict(id=b['id'],translation_hash=digest(d['blocks'][0]['translation']),note='Encoding fixture')])
    send('full_review',fingerprint=fingerprint(d),note='Fixture')
    result=exporter.export_html(d['id']);raw=Path(result['path']).read_bytes()
    # Browsers must discover the charset in the first 1024 bytes, even for large payloads.
    assert b'<meta charset="UTF-8">' in raw[:1024]
    assert text.encode() in raw
