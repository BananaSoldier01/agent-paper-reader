from pathlib import Path
import base64
import copy
import hashlib
import json
import re
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


@pytest.fixture
def completed_export(tmp_path, monkeypatch):
    from PIL import Image
    from reader import exporter
    from reader.workflow import submit, fingerprint
    from reader.store import digest

    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    monkeypatch.setattr(exporter, 'ROOT', tmp_path)
    Image.new('RGB', (16, 16), 'blue').save(tmp_path/'figure.png')
    source = tmp_path/'complete.md'
    source.write_text('# Test\n\nA is safe.\n\n![Fixture](figure.png)\n', encoding='utf-8')
    doc = import_document(source)

    def send(op, **kw):
        nonlocal doc
        doc = submit(doc['id'], dict(operation=op, revision=doc['revision'],
                                    submission_id=f'export-{op}-{doc["revision"]}', agent='fixture', **kw))

    send('structure', blocks=[dict(b, structure_note='Synthetic source checked') for b in doc['blocks']],
         note='Synthetic structure checked')
    send('terms', terms=[dict(id='t1', en='safe', zh='安全', definition='本文指安全状态。')])
    translations = {'# Test': '测试', 'A is safe.': '甲是安全的。', '![Fixture](figure.png)': '示意图'}
    send('translate', blocks=[dict(id=b['id'], translation=dict(text=translations[b['text']], pairs=[
        dict(id='g1', source=[[0, len(b['text'])]], target=[[0, len(translations[b['text']])]])
    ])) for b in doc['blocks'] if b['text']])
    send('review', blocks=[dict(id=b['id'], translation_hash=digest(b['translation']),
                               note='Synthetic translation checked') for b in doc['blocks'] if b['translation']])
    send('full_review', fingerprint=fingerprint(doc), note='Complete synthetic document checked')
    result = exporter.export_html(doc['id'])
    html = Path(result['path']).read_text(encoding='utf-8')
    payload = json.loads(re.search(r'window\.__SNAPSHOT__=(.*?);</script>', html, re.DOTALL)[1])
    return doc, result, html, payload


def test_export_checks_complete_snapshot_and_source_image_bytes(completed_export):
    from reader.exporter import public_document
    doc, result, html, payload = completed_export
    assert payload['document'] == public_document(doc)
    names = {b['asset'] for b in doc['blocks'] if b.get('asset')}
    assert set(payload['assets']) == names
    for name in names:
        assert base64.b64decode(payload['assets'][name].split(';base64,', 1)[1], validate=True) == (store.folder(doc['id'])/name).read_bytes()
    check = result['artifact_validation']
    assert check['ok'] and check['embedded_assets'] == len(names)
    assert check['bytes'] == len(html.encode('utf-8'))
    assert check['sha256'] == hashlib.sha256(html.encode('utf-8')).hexdigest()
    assert check['browser_check'] == 'not_performed'


@pytest.mark.parametrize('damage', ['translation', 'pairs', 'terms', 'image'])
def test_changed_export_snapshot_is_rejected(completed_export, damage):
    from reader.exporter import check_export_html
    _doc, _result, html, payload = completed_export
    changed = copy.deepcopy(payload)
    if damage in {'translation', 'pairs'}:
        block = next(b for b in changed['document']['blocks'] if b['translation'])
        if damage == 'translation':
            block['translation']['text'] = '丢失原文含义'
        else:
            block['translation']['pairs'] = []
    elif damage == 'terms':
        changed['document']['terms'] = []
    else:
        changed['assets'] = {}
    damaged = re.sub(r'window\.__SNAPSHOT__=.*?;</script>',
                     lambda _: 'window.__SNAPSHOT__=' + json.dumps(changed, ensure_ascii=False) + ';</script>',
                     html, count=1, flags=re.DOTALL)
    with pytest.raises(ValueError, match='snapshot differs'):
        check_export_html(damaged, payload['document'], payload['assets'])


@pytest.mark.parametrize('damage, message', [
    ('root', 'mount point'), ('script', 'script was not embedded'),
    ('style_url', 'external resource'), ('font', 'invalid base64'),
    ('snapshot', 'exactly one document snapshot'), ('encoding', 'UTF-8 declaration'),
])
def test_broken_export_resources_are_rejected(completed_export, damage, message):
    from reader.exporter import check_export_html
    _doc, _result, html, payload = completed_export
    if damage == 'root':
        html = html.replace('id="root"', 'id="missing"')
    elif damage == 'script':
        html = html.replace('<script type="module">', '<script type="module" src="/assets/missing.js">', 1)
    elif damage == 'style_url':
        html = html.replace('</style>', '.broken{src:url(/assets/missing.woff2)}</style>', 1)
    elif damage == 'font':
        html = html.replace('</style>', '.broken{src:url(data:font/woff2;base64,!!!)}</style>', 1)
    elif damage == 'snapshot':
        html = html.replace('window.__SNAPSHOT__=', 'window.__MISSING__=', 1)
    else:
        html = html.replace('<meta charset="UTF-8">', '')
    with pytest.raises(ValueError, match=message):
        check_export_html(html, payload['document'], payload['assets'])


def test_failed_export_preserves_existing_file(completed_export, tmp_path, monkeypatch):
    from reader import exporter
    doc, result, _html, _payload = completed_export
    output = Path(result['path'])
    old_bytes = output.read_bytes()
    bundle = tmp_path/'broken-bundle'
    (bundle/'assets').mkdir(parents=True)
    (bundle/'index.html').write_text('<html><head><script type="module" src="/assets/app.js"></script>'
                                   '<link rel="stylesheet" href="/assets/app.css"></head>'
                                   '<body></body></html>')
    (bundle/'assets/app.js').write_text('export {};')
    (bundle/'assets/app.css').write_text('body{color:black}')
    monkeypatch.setattr(exporter, 'BUNDLE', bundle)
    with pytest.raises(ValueError, match='mount point'):
        exporter.export_html(doc['id'])
    assert output.read_bytes() == old_bytes


def test_empty_image_resource_blocks_export(completed_export):
    from reader import exporter
    doc, result, _html, _payload = completed_export
    image = next(b['asset'] for b in doc['blocks'] if b.get('asset'))
    (store.folder(doc['id'])/image).write_bytes(b'')
    old_bytes = Path(result['path']).read_bytes()
    with pytest.raises(ValueError, match='Empty export resource'):
        exporter.export_html(doc['id'])
    assert Path(result['path']).read_bytes() == old_bytes
