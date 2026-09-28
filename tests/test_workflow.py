import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from reader import store
from reader.importer import import_document
from reader.workflow import submit, user_edit, tasks, validate, fingerprint, note_status
from reader.store import digest, Conflict
from reader.server import app, TOKEN

@pytest.fixture
def doc(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path/'data')
    p=tmp_path/'test.md';p.write_text('# Test\n\nA is 5 kW. B is safe.\n',encoding='utf-8')
    d=import_document(p)
    blocks=[{**b,'structure_note':'Checked against Markdown source'} for b in d['blocks']]
    d=send(d,'structure',blocks=blocks,note='Structure checked')
    return send(d,'terms',terms=[])

def send(d,op,**kw):
    return submit(d['id'],{'revision':d['revision'],'submission_id':f'{op}-{d["revision"]}','operation':op,'agent':'test fixture',**kw})

def translated(doc):
    return send(doc,'translate',blocks=[{'id':b['id'],'translation':{'text':b['text'],'pairs':[{'id':'g1','source':[[0,len(b['text'])]],'target':[[0,len(b['text'])]]}]}} for b in doc['blocks']])

def test_resume_idempotence_conflict(doc):
    b=doc['blocks'][0]
    payload={'revision':doc['revision'],'submission_id':'once','operation':'translate','agent':'test','blocks':[{'id':b['id'],'translation':{'text':'标题','pairs':[{'id':'g1','source':[[0,len(b['text'])]],'target':[[0,2]]}]}}]}
    d=submit(doc['id'],payload)
    assert submit(doc['id'],payload)['revision']==d['revision']
    assert len(tasks(d)['blocks'])==1
    wrong=copy.deepcopy(payload);wrong['blocks'][0]['translation']['text']='别的'
    with pytest.raises(Conflict):submit(doc['id'],wrong)
    with pytest.raises(Conflict):user_edit(doc['id'],{'revision':doc['revision'],'operation':'reading','reading':{}})

def test_user_edit_and_orphan(doc):
    d=translated(doc);b=d['blocks'][1]
    n={'id':'n1','block_id':b['id'],'side':'target','start':0,'end':1,'quote':'A','text':'note','difficult':True}
    d=user_edit(d['id'],{'revision':d['revision'],'operation':'note','note':n})
    d=user_edit(d['id'],{'revision':d['revision'],'operation':'translation','block_id':b['id'],'pair_id':'g1','text':'用户修订'})
    assert note_status(d,n)=='orphaned'
    assert d['blocks'][1]['history'][0]['translation']['text']==b['text']
    with pytest.raises(ValueError,match='protected'):send(d,'translate',blocks=[{'id':b['id'],'translation':b['translation']}])

def test_coverage_and_review_gate(doc):
    with pytest.raises(ValueError,match='exactly once'):send(doc,'structure',blocks=doc['blocks'][:1],note='missing')
    d=translated(doc)
    assert not validate(d)['ok']
    d=send(d,'review',blocks=[{'id':b['id'],'translation_hash':digest(b['translation']),'note':'Fixture exact source reviewed'} for b in d['blocks']])
    d=send(d,'full_review',fingerprint=fingerprint(d),note='Fixture all blocks checked')
    assert validate(d)['ok']
    d=user_edit(d['id'],{'revision':d['revision'],'operation':'translation','block_id':d['blocks'][0]['id'],'pair_id':'g1','text':'new'})
    assert not validate(d)['ok']

def test_malformed_alignment_and_numeric_gate(doc):
    b=doc['blocks'][1]
    with pytest.raises(ValueError,match='cover all'):send(doc,'translate',blocks=[{'id':b['id'],'translation':{'text':'5 千瓦','pairs':[{'id':'x','source':[[0,1]],'target':[[0,4]]}]}}])
    d=translated(doc)
    d=user_edit(d['id'],{'revision':d['revision'],'operation':'translation','block_id':b['id'],'pair_id':'g1','text':'6 MW'})
    assert any('unexplained' in e for e in validate(d)['errors'])

def test_simultaneous_writers(doc):
    def write(i):
        try:return user_edit(doc['id'],{'revision':doc['revision'],'operation':'reading','reading':{'block_id':str(i)}})['revision']
        except Conflict:return 'conflict'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(write,range(2)))
    assert results.count('conflict')==1
    assert json.loads((store.folder(doc['id'])/'document.json').read_text())['revision']==doc['revision']+1

def test_security(doc):
    c=TestClient(app)
    url=f'/api/documents/{doc["id"]}/edit'
    assert c.post(url,json={'revision':doc['revision'],'operation':'reading','reading':{}}).status_code==403
    assert c.get('/api/session',headers={'host':'evil.example'}).status_code==403
    assert c.post(url,headers={'x-reader-token':TOKEN},json={'revision':0,'operation':'reading','reading':{}}).status_code==409
    assert c.get(f'/api/documents/{doc["id"]}/assets/document.json').status_code==404

def test_markdown_injection_and_duplicate_import(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path/'data')
    p=tmp_path/'x.md';p.write_text('# Hi\n\n<script>alert(1)</script>\n\n$$E=mc^2$$\n\n```html\n<img onerror=alert(1)>\n```\n')
    d=import_document(p)
    assert d['revision']==import_document(p)['revision']
    assert any('<script>' in b['text'] for b in d['blocks'])
    assert any(b['kind']=='formula' for b in d['blocks'])
    assert any(b['kind']=='code' for b in d['blocks'])

def test_discontinuous_semantic_edit_and_unicode(doc):
    b=doc['blocks'][1];en=b['text'];zh='甲😀乙。'
    d=doc
    # Many-to-one: discontiguous target fragments in one group, source coverage still exact.
    d=send(d,'translate',blocks=[{'id':b['id'],'translation':{'text':zh,'pairs':[{'id':'g1','source':[[0,2]],'target':[[0,1],[2,4]]},{'id':'g2','source':[[2,len(en)]],'target':[[1,2]]}]}}])
    d=user_edit(d['id'],{'revision':d['revision'],'operation':'translation','block_id':b['id'],'pair_id':'g1','span_index':1,'text':'乙句。'})
    t=d['blocks'][1]['translation']
    assert t['text']=='甲😀乙句。'
    assert t['pairs'][0]['target']==[[0,1],[2,5]]
    assert t['pairs'][1]['target']==[[1,2]]

def test_numeric_difference_cannot_be_certified_without_explanation(doc):
    d=translated(doc);b=d['blocks'][1]
    d=user_edit(d['id'],{'revision':d['revision'],'operation':'translation','block_id':b['id'],'pair_id':'g1','text':'6 MW'})
    d=send(d,'review',blocks=[{'id':x['id'],'translation_hash':digest(x['translation']),'note':'Checked'} for x in d['blocks']])
    with pytest.raises(ValueError,match='unexplained'):send(d,'full_review',fingerprint=fingerprint(d),note='Must fail')


def test_attach_visual_preserves_frozen_anchors(doc):
    blocks=[{**b,'kind':'caption'} for b in doc['blocks']]
    d=send(doc,'structure',blocks=blocks,note='Caption fixture')
    d=translated(d);b=d['blocks'][0]
    n={'id':'visual-note','block_id':b['id'],'side':'source','start':0,'end':1,'quote':b['text'][:1],'text':'keep','difficult':False}
    d=user_edit(d['id'],{'revision':d['revision'],'operation':'note','note':n})
    before=copy.deepcopy(d)
    asset='region-'+'a'*16+'.png';(store.folder(d['id'])/asset).write_bytes(b'fixture')
    with pytest.raises(ValueError):send(d,'attach_asset',block_id=b['id'],asset='../source.md',note='bad path')
    d=send(d,'attach_asset',block_id=b['id'],asset=asset,note='Original crop verified')
    for key in ('text','source_ids','translation','history','user_edited'):
        assert d['blocks'][0][key]==before['blocks'][0][key]
    assert d['notes']==before['notes'] and note_status(d,d['notes'][0])=='attached'
    assert d['full_review'] is None
    with pytest.raises(ValueError):send(d,'attach_asset',block_id=b['id'],asset=asset,note='no replacement')
