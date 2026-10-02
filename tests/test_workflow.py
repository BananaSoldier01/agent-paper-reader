import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from reader import store
from reader.importer import import_document
from reader.workflow import submit, user_edit, tasks, validate, fingerprint, note_status, project_document
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


def _synthetic_structure_doc():
    text = 'Alpha beta gamma delta epsilon. ' * 40
    emoji = '😀' * 200
    n = 40
    atoms = [{'id': f'a{i:05d}', 'text': emoji if i == 3 else text, 'location': {'start': i, 'end': i+1}} for i in range(n)]
    blocks = [{'id': f'b{i:05d}', 'kind': 'heading' if i % 10 == 0 else 'paragraph', 'text': atoms[i]['text'],
               'source_ids': [atoms[i]['id']], 'asset': None, 'translation': None, 'history': [], 'review': None,
               'structure_note': '', 'user_edited': False} for i in range(n)]
    message = 'Unresolved extraction detail. ' * 40
    issues = [{'id': f'page-{i}', 'message': message, 'resolution': None} for i in range(12)]
    issues.append({'id': 'page-99', 'message': message, 'resolution': 'checked the scan'})
    return {'schema_version': 1, 'id': 'a'*24, 'title': 'Big', 'source_file': 'source.md', 'revision': 0,
            'stage': 'structure', 'atoms': atoms, 'blocks': blocks, 'issues': issues, 'terms': [], 'notes': [],
            'structure_review': None, 'terms_review': None, 'full_review': None}, message


def test_structure_projection_smaller_than_full_document():
    doc, message = _synthetic_structure_doc()
    full = project_document(doc, full=True, view='show')
    view = project_document(doc, view='show')
    task_full = project_document(doc, full=True, view='tasks', limit=8)
    task_view = project_document(doc, view='tasks', limit=8)
    assert len(json.dumps(view)) < len(json.dumps(full)) * 0.5
    assert len(json.dumps(task_view)) < len(json.dumps(task_full)) * 0.5
    assert message in json.dumps(full) and message in json.dumps(task_full)
    projected = json.dumps(view, ensure_ascii=False) + json.dumps(task_view, ensure_ascii=False)
    assert message not in projected
    assert '"atoms"' not in projected and '"history"' not in projected and '"translation"' not in projected
    assert view['projection'] is True and view['block_count'] == 40 and view['atom_count'] == 40
    assert len(view['blocks']) == 40 and len(task_view['blocks']) == 8
    assert all(set(b) == {'id', 'kind', 'text', 'has_structure_note', 'source_count'} for b in view['blocks'])
    assert all(len(b['text']) <= 160 for b in view['blocks'])
    emoji = next(b for b in view['blocks'] if b['id'] == 'b00003')
    assert emoji['text'] == '😀' * 160 and len(emoji['text']) == 160
    summary = view['issue_summary']
    assert summary['unresolved'] == 12 and summary['resolved'] == 1
    assert summary['by_type']['page']['sample_ids'] == ['page-0', 'page-1', 'page-2']
    assert 'checked the scan' not in projected
    assert task_view['projection'] is True and 'issues' not in task_view
    assert 'issues' in task_full and task_full['issues'][0]['message'] == message


def test_translation_projection_keeps_section_without_atoms_or_issue_bodies():
    text = 'Sentence about turbines and limits. ' * 12
    atoms, blocks = [], []
    for i, kind in enumerate(('heading', 'paragraph', 'paragraph')):
        atoms.append({'id': f'a{i:05d}', 'text': text, 'location': {'start': i}})
        blocks.append({'id': f'b{i:05d}', 'kind': kind, 'text': text, 'source_ids': [f'a{i:05d}'], 'asset': None,
                       'translation': {'text': '译文', 'pairs': [{'id': 'g1', 'source': [[0, 1]], 'target': [[0, 1]]}]} if i == 0 else None,
                       'history': [], 'review': None, 'structure_note': 'kept', 'user_edited': False})
    unresolved = 'UNRESOLVED_BODY_' + 'x'*400
    resolved = 'RESOLVED_BODY_' + 'y'*400
    doc = {'schema_version': 1, 'id': 'b'*24, 'title': 'Terms', 'source_file': 'source.md', 'revision': 3,
           'stage': 'translate', 'atoms': atoms, 'blocks': blocks, 'terms': [{'id': 't1', 'en': 'turbine', 'zh': '涡轮', 'definition': '机器'}],
           'notes': [], 'structure_review': {'agent': 'a', 'note': 'n'}, 'terms_review': {'agent': 'a', 'note': 'n'},
           'full_review': None, 'issues': [{'id': 'page-1', 'message': unresolved, 'resolution': None},
                                           {'id': 'done-9', 'message': resolved, 'resolution': 'already handled'}]}
    view = project_document(doc, view='show')
    dumped = json.dumps(view, ensure_ascii=False)
    assert view['projection'] is True and view['stage'] == 'translate'
    assert 'atoms' not in view and '"location"' not in dumped
    assert unresolved not in dumped and resolved not in dumped and 'done-9' not in json.dumps(view['issue_summary'])
    assert any(b.get('text') == text for b in view['section_context'])
    assert view['terms'][0]['en'] == 'turbine'
    assert any(b.get('translation_hash') for b in view['section_context'])
    assert view['issue_summary']['unresolved'] == 1
    assert tasks(doc)['stage'] == 'translate'


def test_cli_projection_and_full_switch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    path = tmp_path/'cli.md'
    path.write_text('# Title\n\nA short paragraph for the projection check.\n', encoding='utf-8')
    doc = import_document(path)
    from reader.cli import main

    def run(*argv):
        monkeypatch.setattr(sys, 'argv', ['paper', *argv])
        main()
        return json.loads(capsys.readouterr().out)

    shown = run('show', doc['id'])['result']
    assert shown['projection'] is True and 'atoms' not in shown and shown['block_count'] == len(doc['blocks'])
    full = run('show', doc['id'], '--full')['result']
    assert 'projection' not in full and 'atoms' in full and 'fingerprint' in full
    assert 'translation_hash' in full['blocks'][0]
    progress = run('progress', doc['id'])['result']
    assert progress['projection'] is True and 'atoms' not in progress
    assert progress['outline_length'] == 1 and 'unresolved_issues' in progress and 'errors' in progress
    legacy = run('progress', doc['id'], '--full')['result']
    assert 'projection' not in legacy and 'outline_length' not in legacy and 'errors' in legacy and 'atoms' not in legacy
    batch = run('tasks', doc['id'])['result']
    assert batch['projection'] is True and 'issues' not in batch and 'issue_summary' in batch
    old_tasks = run('tasks', doc['id'], '--full')['result']
    assert 'projection' not in old_tasks and 'issues' in old_tasks


def test_keep_extracted_updates_merges_and_default_note(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    path = tmp_path/'keep.md'
    path.write_text('# Title\n\nFirst sentence stays.\n\nSecond part.\n\nThird part.\n', encoding='utf-8')
    doc = import_document(path)
    assert any('Structure not reviewed' in e for e in validate(doc)['errors'])
    ids = [b['id'] for b in doc['blocks']]
    assert len(ids) >= 4
    before_issues = copy.deepcopy(doc['issues'])
    original_sources = {b['id']: list(b['source_ids']) for b in doc['blocks']}
    merged = doc['blocks'][1]['text'] + ' ' + doc['blocks'][2]['text']
    doc = submit(doc['id'], {'revision': doc['revision'], 'submission_id': 'keep-1', 'operation': 'structure',
                             'agent': 'test', 'note': 'Keep extraction', 'keep_extracted': True,
                             'default_structure_note': 'Extraction matches source',
                             'updates': [{'id': ids[0], 'kind': 'heading', 'structure_note': 'Title line'}],
                             'merges': [{'into': ids[1], 'from': [ids[2]], 'text': merged, 'structure_note': 'Join adjacent paragraphs'}]})
    assert doc['schema_version'] == 1 and doc['structure_review']['note'] == 'Keep extraction'
    assert doc['issues'] == before_issues
    kept = {b['id']: b for b in doc['blocks']}
    assert ids[2] not in kept and kept[ids[0]]['structure_note'] == 'Title line'
    assert kept[ids[1]]['structure_note'] == 'Join adjacent paragraphs'
    assert kept[ids[1]]['source_ids'] == original_sources[ids[1]] + original_sources[ids[2]]
    assert kept[ids[1]]['text'] == merged
    assert kept[ids[3]]['structure_note'] == 'Extraction matches source'
    sources = [sid for b in doc['blocks'] for sid in b['source_ids']]
    assert sorted(sources) == sorted(a['id'] for a in doc['atoms']) and len(sources) == len(set(sources))
    errors = validate(doc)['errors']
    assert not any('Structure not reviewed' in e or 'Source ledger' in e for e in errors)
    plain = tmp_path/'plain.md'
    plain.write_text('# H\n\nOnly one change is the note.\n', encoding='utf-8')
    fresh = import_document(plain)
    patched = submit(fresh['id'], {'revision': fresh['revision'], 'submission_id': 'patch-1', 'operation': 'structure',
                                    'agent': 'test', 'note': 'Patch', 'mode': 'patch',
                                    'default_structure_note': 'Extraction already correct'})
    assert len(patched['blocks']) == len(fresh['blocks'])
    assert all(b['structure_note'] == 'Extraction already correct' for b in patched['blocks'])
    assert {tuple(b['source_ids']) for b in patched['blocks']} == {tuple(b['source_ids']) for b in fresh['blocks']}


def test_full_blocks_still_replace_when_keep_flag_present(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    path = tmp_path/'full.md'
    path.write_text('# Title\n\nBody stays whole.\n', encoding='utf-8')
    doc = import_document(path)
    blocks = [{**b, 'structure_note': 'full path'} for b in doc['blocks']]
    saved = submit(doc['id'], {'revision': doc['revision'], 'submission_id': 'full-1', 'operation': 'structure',
                                'agent': 'test', 'note': 'Full list', 'blocks': blocks, 'keep_extracted': True,
                                'default_structure_note': 'should not apply'})
    assert [b['structure_note'] for b in saved['blocks']] == ['full path'] * len(blocks)
    assert len(saved['blocks']) == len(blocks)


def test_keep_extracted_rejects_missing_note_atom_and_source_change(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    path = tmp_path/'bad.md'
    path.write_text('# Title\n\nAlpha sentence.\n\nBeta sentence.\n', encoding='utf-8')
    doc = import_document(path)
    ids = [b['id'] for b in doc['blocks']]
    base = {'revision': doc['revision'], 'operation': 'structure', 'agent': 'test', 'note': 'no'}
    with pytest.raises(ValueError, match='reason'):
        submit(doc['id'], {**base, 'submission_id': 'missing-note', 'keep_extracted': True})
    with pytest.raises(ValueError, match='Merge requires structure_note'):
        submit(doc['id'], {**base, 'submission_id': 'merge-note', 'keep_extracted': True,
                           'default_structure_note': 'fill the rest',
                           'merges': [{'into': ids[1], 'from': [ids[2]]}]})
    with pytest.raises(ValueError, match='exactly once'):
        submit(doc['id'], {**base, 'submission_id': 'drop-atom', 'keep_extracted': True,
                           'default_structure_note': 'noted',
                           'updates': [{'id': ids[-1], 'source_ids': [doc['blocks'][0]['source_ids'][0]]}]})
    with pytest.raises(ValueError, match='source_change'):
        submit(doc['id'], {**base, 'submission_id': 'rewrite', 'mode': 'keep',
                           'default_structure_note': 'noted',
                           'updates': [{'id': ids[1], 'text': 'Completely different wording'}]})
    changed = submit(doc['id'], {**base, 'submission_id': 'rewrite-ok', 'mode': 'keep',
                                 'default_structure_note': 'noted',
                                 'updates': [{'id': ids[1], 'text': 'Completely different wording', 'source_change': 'Joined a hyphenated line'}]})
    assert next(b for b in changed['blocks'] if b['id'] == ids[1])['source_change'] == 'Joined a hyphenated line'
    assert not any('Source ledger' in e or 'Structure not reviewed' in e for e in validate(changed)['errors'])


def test_keep_extracted_frozen_after_translation(doc):
    with pytest.raises(ValueError, match='frozen'):
        send(translated(doc), 'structure', keep_extracted=True, default_structure_note='nope', note='late')

def test_keep_extracted_rejects_figure_merge_without_explicit_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    path = tmp_path/'figs.md'
    path.write_text('# Title\n\nIntro paragraph.\n\n![one](one.png)\n\n![two](two.png)\n', encoding='utf-8')
    (tmp_path/'one.png').write_bytes(b'png-one')
    (tmp_path/'two.png').write_bytes(b'png-two')
    doc = import_document(path)
    figs = [b for b in doc['blocks'] if b['kind'] == 'figure' and b.get('asset')]
    assert len(figs) >= 2
    a, b = figs[0], figs[1]
    assert a['asset'] != b['asset']
    base = {'revision': doc['revision'], 'operation': 'structure', 'agent': 'test', 'note': 'figs',
            'keep_extracted': True, 'default_structure_note': 'kept'}
    with pytest.raises(ValueError, match='figure assets'):
        submit(doc['id'], {**base, 'submission_id': 'merge-drop-img',
                           'merges': [{'into': a['id'], 'from': [b['id']], 'structure_note': 'join figures'}]})
    for label, bad in (('null', None), ('empty', ''), ('false', False)):
        with pytest.raises(ValueError):
            submit(doc['id'], {**base, 'submission_id': f'merge-bad-asset-{label}',
                               'merges': [{'into': a['id'], 'from': [b['id']], 'structure_note': 'join figures',
                                           'asset': bad}]})
    kept = submit(doc['id'], {**base, 'revision': doc['revision'], 'submission_id': 'merge-keep-img',
                              'merges': [{'into': a['id'], 'from': [b['id']], 'structure_note': 'join figures',
                                         'asset': a['asset']}]})
    merged = next(block for block in kept['blocks'] if block['id'] == a['id'])
    assert merged['asset'] == a['asset']
    assert b['id'] not in {block['id'] for block in kept['blocks']}
    assert not any('Source ledger' in e for e in validate(kept)['errors'])


def test_keep_extracted_requires_fresh_source_change_on_reedit(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    path = tmp_path/'reedit.md'
    path.write_text('# Title\n\nOriginal wording stays here.\n', encoding='utf-8')
    doc = import_document(path)
    para = next(b for b in doc['blocks'] if b['kind'] == 'paragraph')
    base = {'revision': doc['revision'], 'operation': 'structure', 'agent': 'test', 'note': 'edit',
            'mode': 'keep', 'default_structure_note': 'noted'}
    first = submit(doc['id'], {**base, 'submission_id': 'edit-1',
                               'updates': [{'id': para['id'], 'text': 'OCR fixed wording once.',
                                            'source_change': 'OCR hyphenation fix'}]})
    assert next(b for b in first['blocks'] if b['id'] == para['id'])['source_change'] == 'OCR hyphenation fix'
    with pytest.raises(ValueError, match='source_change'):
        submit(first['id'], {'revision': first['revision'], 'submission_id': 'edit-2', 'operation': 'structure',
                             'agent': 'test', 'note': 'edit again', 'mode': 'keep',
                             'updates': [{'id': para['id'], 'text': 'Completely different second edit'}]})
    second = submit(first['id'], {'revision': first['revision'], 'submission_id': 'edit-2-ok', 'operation': 'structure',
                                  'agent': 'test', 'note': 'edit again', 'mode': 'keep',
                                  'updates': [{'id': para['id'], 'text': 'Completely different second edit',
                                               'source_change': 'Author note rewritten after OCR pass'}]})
    assert next(b for b in second['blocks'] if b['id'] == para['id'])['source_change'] == 'Author note rewritten after OCR pass'


def test_long_section_context_is_bounded_by_default_projection():
    n = 200
    atoms, blocks = [], []
    for i in range(n):
        text = f'Paragraph {i:03d} about turbines with enough body text to inflate payloads. '
        atoms.append({'id': f'a{i:05d}', 'text': text, 'location': {'start': i}})
        blocks.append({'id': f'b{i:05d}', 'kind': 'paragraph', 'text': text, 'source_ids': [f'a{i:05d}'],
                       'asset': None, 'translation': None, 'history': [], 'review': None,
                       'structure_note': 'kept', 'user_edited': False})
    doc = {'schema_version': 1, 'id': 'c'*24, 'title': 'Long', 'source_file': 'source.md', 'revision': 2,
           'stage': 'translate', 'atoms': atoms, 'blocks': blocks, 'terms': [], 'notes': [],
           'structure_review': {'agent': 'a', 'note': 'n'}, 'terms_review': {'agent': 'a', 'note': 'n'},
           'full_review': None, 'issues': []}
    view = project_document(doc, view='tasks', limit=1)
    assert view['projection'] is True
    assert len(view['section_context']) <= 24
    window = view['section_window']
    assert window['section_total'] == 200 and window['truncated'] is True
    assert window['section_limit'] == 24
    assert len(json.dumps(view['section_context'])) < 80_000
    page = project_document(doc, view='tasks', limit=1, section_limit=10, section_offset=40)
    assert [b['id'] for b in page['section_context']] == [f'b{i:05d}' for i in range(40, 50)]
    assert page['section_window']['section_offset'] == 40 and page['section_window']['truncated'] is True
    full = project_document(doc, full=True, view='tasks', limit=1)
    assert len(full['section_context']) == 200


def test_sparse_pending_context_is_local_neighbors_not_span():
    n = 200
    body = 'x' * 4000
    atoms, blocks = [], []
    for i in range(n):
        text = f'{i:03d} {body}'
        translation = {'text': f'zh {i:03d}', 'pairs': [{'id': 'g1', 'source': [[0, 1]], 'target': [[0, 1]]}]}
        pending = i in (0, n - 1)
        review = None if pending else {
            'agent': 'a', 'translation_hash': digest(translation), 'note': 'checked',
            'difference_explanation': '', 'duplicate_explanation': '',
        }
        atoms.append({'id': f'a{i:05d}', 'text': text, 'location': {'start': i}})
        blocks.append({'id': f'b{i:05d}', 'kind': 'paragraph', 'text': text, 'source_ids': [f'a{i:05d}'],
                       'asset': None, 'translation': translation, 'history': [], 'review': review,
                       'structure_note': 'kept', 'user_edited': False})
    doc = {'schema_version': 1, 'id': 'd'*24, 'title': 'Sparse', 'source_file': 'source.md', 'revision': 4,
           'stage': 'review', 'atoms': atoms, 'blocks': blocks, 'terms': [], 'notes': [],
           'structure_review': {'agent': 'a', 'note': 'n'}, 'terms_review': {'agent': 'a', 'note': 'n'},
           'full_review': None, 'issues': []}
    view = project_document(doc, view='tasks', limit=8)
    assert view['stage'] == 'review'
    assert [b['id'] for b in view['blocks']] == ['b00000', 'b00199']
    assert [b['id'] for b in view['context']] == ['b00000', 'b00001', 'b00198', 'b00199']
    assert len(view['context']) <= 6
    assert len(json.dumps(view['context'])) < 761_000
    assert len(view['section_context']) <= 24


def _patch_issues(doc, issues):
    def apply(current):
        current['issues'] = issues
    return store.mutate(doc['id'], doc['revision'], apply)


_LIMITATION_RESOLUTION = (
    'Single-file TeX does not compile; includegraphics assets stay unembedded as a product limit.'
)
_LIMITATION_EVIDENCE = (
    'checked sample ids tex-includegraphics-1 and tex-includegraphics-2 against the source file; no compile.'
)


def test_limitation_batch_applies_to_matching_unresolved_only(doc):
    seeded = _patch_issues(doc, [
        {'id': 'tex-includegraphics-1', 'message': 'fig 1', 'resolution': None},
        {'id': 'tex-includegraphics-2', 'message': 'fig 2', 'resolution': None},
        {'id': 'tex-includegraphics-3', 'message': 'fig 3', 'resolution': None},
        {'id': 'tex-includegraphics-99', 'message': 'already', 'resolution': 'already resolved previously by hand'},
        {'id': 'image-b00001', 'message': 'remote or missing', 'resolution': None},
    ])
    saved = send(seeded, 'resolve', limitations=[{
        'category': 'tex-includegraphics',
        'resolution': _LIMITATION_RESOLUTION,
        'evidence': _LIMITATION_EVIDENCE,
    }])
    by_id = {item['id']: item for item in saved['issues']}
    for key in ('tex-includegraphics-1', 'tex-includegraphics-2', 'tex-includegraphics-3'):
        assert by_id[key]['resolution'] == _LIMITATION_RESOLUTION
        assert by_id[key]['resolution_evidence'] == _LIMITATION_EVIDENCE
        assert by_id[key]['resolved_by'] == 'limitation_batch'
    assert by_id['tex-includegraphics-99']['resolution'] == 'already resolved previously by hand'
    assert by_id['tex-includegraphics-99'].get('resolved_by') is None
    assert by_id['image-b00001']['resolution'] is None
    aliased = send(saved, 'resolve_limitations', limitations=[{
        'category': 'image',
        'resolution': 'HTML/Markdown remote or missing images are not fetched; local-archive product limit.',
        'evidence': 'checked sample image-b00001; source file has a missing local image path.',
    }])
    image = next(item for item in aliased['issues'] if item['id'] == 'image-b00001')
    assert image['resolution'].startswith('HTML/Markdown')
    assert image['resolved_by'] == 'limitation_batch'
    assert next(item for item in aliased['issues'] if item['id'] == 'tex-includegraphics-99')['resolution'] == (
        'already resolved previously by hand'
    )


def test_limitation_batch_rejects_empty_or_weak_evidence(doc):
    seeded = _patch_issues(doc, [
        {'id': 'tex-includegraphics-1', 'message': 'fig', 'resolution': None},
    ])
    base = {'category': 'tex-includegraphics', 'resolution': _LIMITATION_RESOLUTION}
    with pytest.raises(ValueError, match='evidence'):
        send(seeded, 'resolve', limitations=[{**base}])
    for evidence in (
        '',
        '   ',
        'too short to count',
        'ignore',
        '跳过',
        'ignore ignore ignore ignore ignore ignore ignore',
        '已知限制已知限制已知限制已知限制已知限制',
        'x' * 24,
    ):
        with pytest.raises(ValueError):
            send(seeded, 'resolve', limitations=[{**base, 'evidence': evidence}])
    with pytest.raises(ValueError, match='explicit resolution'):
        send(seeded, 'resolve', issues=[{'id': 'tex-includegraphics-1', 'resolution': '  '}])
    leftover = store.read(seeded['id'])['issues']
    assert leftover[0]['resolution'] is None


def test_limitation_batch_rejects_disallowed_body_categories(doc):
    seeded = _patch_issues(doc, [
        {'id': 'page-1', 'message': 'scan', 'resolution': None},
        {'id': 'docx-sym-1', 'message': 'unknown symbol', 'resolution': None},
    ])
    body = {
        'resolution': 'Inspected original pages and recorded an unreadable-body limitation for honesty.',
        'evidence': 'checked sample page-1 against the scanned source file; encoding is garbled.',
    }
    with pytest.raises(ValueError, match='page'):
        send(seeded, 'resolve', limitations=[{'category': 'page', **body}])
    with pytest.raises(ValueError, match='docx-sym'):
        send(seeded, 'resolve', limitations=[{'category': 'docx-sym', **body}])
    frozen = store.read(seeded['id'])
    assert all(item['resolution'] is None for item in frozen['issues'])
    long_res = (
        'Inspected original page PNG; body text is an unreadable scan and OCR is outside product scope.'
    )
    saved = send(seeded, 'resolve', issues=[{'id': 'page-1', 'resolution': long_res}])
    by_id = {item['id']: item for item in saved['issues']}
    assert by_id['page-1']['resolution'] == long_res
    assert by_id['docx-sym-1']['resolution'] is None


def test_limitation_batch_rejects_unknown_category_and_zero_matches(doc):
    seeded = _patch_issues(doc, [
        {'id': 'image-b00001', 'message': 'missing', 'resolution': None},
    ])
    good = {
        'resolution': 'HTML remote image is not fetched; this is the local-archive product limit.',
        'evidence': 'checked sample image-b00001; source file has a remote src and missing local file.',
    }
    with pytest.raises(ValueError, match='issues and/or limitations'):
        send(seeded, 'resolve')
    with pytest.raises(ValueError, match='Unknown or disallowed'):
        send(seeded, 'resolve', limitations=[{'category': 'not-a-real-kind', **good}])
    with pytest.raises(ValueError, match='category is required'):
        send(seeded, 'resolve', limitations=[{'category': '  ', **good}])
    with pytest.raises(ValueError, match='No unresolved issues match'):
        send(seeded, 'resolve', limitations=[{'category': 'tex-input', **good}])
    assert next(item for item in store.read(seeded['id'])['issues'] if item['id'] == 'image-b00001')['resolution'] is None


def test_limitation_batch_validate_still_lists_unresolved_body_issues(doc):
    seeded = _patch_issues(doc, [
        {'id': 'image-b00001', 'message': 'missing', 'resolution': None},
        {'id': 'tex-includegraphics-1', 'message': 'fig', 'resolution': None},
        {'id': 'page-1', 'message': 'scan', 'resolution': None},
    ])
    saved = send(seeded, 'resolve', limitations=[
        {
            'category': 'image',
            'resolution': 'Markdown missing image is not fetched; local-archive product known limit.',
            'evidence': 'checked sample image-b00001; source file has a missing local image.',
        },
        {
            'category': 'tex-includegraphics',
            'resolution': _LIMITATION_RESOLUTION,
            'evidence': _LIMITATION_EVIDENCE,
        },
    ])
    errors = validate(saved)['errors']
    assert any('page-1' in item for item in errors)
    assert not any('image-b00001' in item or 'tex-includegraphics-1' in item for item in errors)
    resolved = send(saved, 'resolve', issues=[{
        'id': 'page-1',
        'resolution': 'Inspected page PNG; body text is an unreadable scan and stays an explicit per-id limit.',
    }])
    leftover = [item for item in validate(resolved)['errors'] if 'Unresolved extraction issue' in item]
    assert leftover == []
