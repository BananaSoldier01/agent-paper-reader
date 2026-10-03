import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from reader import store
from reader.importer import import_document
from reader.workflow import (
    submit, user_edit, tasks, validate, fingerprint, note_status, project_document, _short_resolution,
    assemble_payload, fill_pair_offsets, check_translation, pending_counts, DEFAULT_TASK_LIMIT,
    DEFAULT_SECTION_CONTEXT_LIMIT,
)
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
                       'history': [{'translation': {'text': 'HISTORY_BLOB_' + 'h'*500}, 'author': 'agent'}] if i == 1 else [],
                       'review': None, 'structure_note': 'STRUCTURE_NOTE_BLOB_' + 's'*400, 'source_change': 'SOURCE_CHANGE_BLOB',
                       'user_edited': False})
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
    kept = {'id', 'kind', 'text', 'source_ids', 'asset', 'translation', 'translation_hash', 'review', 'user_edited', 'has_structure_note'}
    chosen_ids = {b['id'] for b in view['blocks']}
    assert all(set(b) == kept for b in view['blocks'])
    heading = next(b for b in view['section_context'] if b['id'] == 'b00000')
    assert heading['id'] not in chosen_ids
    assert heading.get('text') == text and heading.get('translation_hash')
    assert set(heading) == kept
    for row in view['context'] + view['section_context']:
        if row['id'] in chosen_ids:
            assert set(row) == {'id', 'ref'} and row['ref'] is True
            assert 'text' not in row and 'translation_hash' not in row
        else:
            assert set(row) == kept
    assert all(b['has_structure_note'] is True for b in view['blocks'])
    assert 'HISTORY_BLOB_' not in dumped and 'STRUCTURE_NOTE_BLOB_' not in dumped and 'SOURCE_CHANGE_BLOB' not in dumped
    full_tasks = project_document(doc, full=True, view='tasks', limit=8)
    full_dump = json.dumps(full_tasks, ensure_ascii=False)
    assert 'HISTORY_BLOB_' in full_dump and 'STRUCTURE_NOTE_BLOB_' in full_dump
    assert 'history' in full_tasks['blocks'][1]


def test_default_projection_dedups_overlapping_window():
    n = 8
    atoms, blocks = [], []
    for i in range(n):
        text = f'Paragraph {i:03d} about cooling limits and nearby context.'
        atoms.append({'id': f'a{i:05d}', 'text': text, 'location': {'start': i}})
        blocks.append({'id': f'b{i:05d}', 'kind': 'heading' if i == 0 else 'paragraph', 'text': text,
                       'source_ids': [f'a{i:05d}'], 'asset': None, 'translation': None, 'history': [],
                       'review': None, 'structure_note': 'kept', 'user_edited': False})
    doc = {'schema_version': 1, 'id': 'e'*24, 'title': 'Overlap', 'source_file': 'source.md', 'revision': 2,
           'stage': 'translate', 'atoms': atoms, 'blocks': blocks, 'terms': [{'id': 't1', 'en': 'PUE', 'zh': '电能利用效率', 'definition': '比率'}],
           'notes': [], 'structure_review': {'agent': 'a', 'note': 'n'}, 'terms_review': {'agent': 'a', 'note': 'n'},
           'full_review': None, 'issues': []}
    limit = 3
    for view_name in ('tasks', 'show'):
        view = project_document(doc, view=view_name, limit=limit)
        chosen_ids = {row['id'] for row in view['blocks']}
        assert chosen_ids == {f'b{i:05d}' for i in range(limit)}
        assert all('text' in row and row.get('ref') is not True for row in view['blocks'])
        neighbor = next(row for row in view['context'] if row['id'] == 'b00003')
        assert 'text' in neighbor and neighbor.get('ref') is not True
        for key in ('context', 'section_context'):
            for row in view[key]:
                if row['id'] in chosen_ids:
                    assert row == {'id': row['id'], 'ref': True}
                    assert 'text' not in row and 'translation_hash' not in row
                else:
                    assert 'text' in row and row.get('ref') is not True
    full_tasks = project_document(doc, full=True, view='tasks', limit=limit)
    chosen_ids = {row['id'] for row in full_tasks['blocks']}
    for key in ('context', 'section_context'):
        for row in full_tasks[key]:
            assert 'text' in row
            assert row.get('ref') is not True
            if row['id'] in chosen_ids:
                assert row['text'] == next(b['text'] for b in doc['blocks'] if b['id'] == row['id'])


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


def test_batch_resolved_limitations_stay_visible_as_warnings(doc, monkeypatch):
    import reader.server as server_module
    seeded = _patch_issues(doc, [
        {'id': 'image-b00001', 'message': 'missing', 'resolution': None},
        {'id': 'tex-includegraphics-1', 'message': 'fig', 'resolution': None},
        {'id': 'tex-includegraphics-9', 'message': 'already', 'resolution': 'Figure was copied into the document folder by hand.'},
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
    result = validate(saved)
    unresolved = [item for item in result['errors'] if item.startswith('Unresolved extraction issue:')]
    assert unresolved == ['Unresolved extraction issue: page-1']
    assert any(
        'Confirmed known limitation' in item and 'not actually fixed' in item
        and 'image-b00001' in item and '[image]' in item
        for item in result['warnings']
    )
    assert any('tex-includegraphics-1' in item and '[tex-includegraphics]' in item for item in result['warnings'])
    assert not any('tex-includegraphics-9' in item or 'page-1' in item for item in result['warnings'])
    by_id = {item['id']: item for item in result['confirmed_limitations']}
    assert set(by_id) == {'image-b00001', 'tex-includegraphics-1'}
    assert by_id['image-b00001']['category'] == 'image'
    assert by_id['image-b00001']['resolution'].startswith('Markdown missing image')
    assert 'image-b00001' in by_id['image-b00001']['resolution_evidence']
    assert by_id['tex-includegraphics-1']['category'] == 'tex-includegraphics'
    for key in ('ok', 'errors', 'warnings', 'blocks', 'translated', 'reviewed'):
        assert key in result
    monkeypatch.setattr(server_module, 'DATA', store.DATA)
    rows = TestClient(app).get('/api/documents').json()
    listed = next(item for item in rows if item['id'] == saved['id'])
    assert listed['confirmed_limitations'] == 2
    assert listed['title'] and 'stage' in listed and 'revision' in listed


def test_blank_batch_mark_still_blocks_and_is_not_confirmed(doc):
    seeded = _patch_issues(doc, [
        {'id': 'image-b00001', 'message': 'missing', 'resolution': None, 'resolved_by': 'limitation_batch'},
        {'id': 'image-b00002', 'message': 'missing', 'resolution': '', 'resolved_by': 'limitation_batch',
         'resolution_evidence': 'checked sample image-b00002 against the source file.'},
    ])
    result = validate(seeded)
    assert result['confirmed_limitations'] == []
    assert 'Unresolved extraction issue: image-b00001' in result['errors']
    assert 'Unresolved extraction issue: image-b00002' in result['errors']
    assert not any('image-b0000' in item for item in result['warnings'])


def test_per_id_resolve_clears_stale_batch_evidence(doc):
    seeded = _patch_issues(doc, [
        {'id': 'image-b00001', 'message': 'missing', 'resolution': None},
        {'id': 'image-b00002', 'message': 'missing too', 'resolution': None},
    ])
    batched = send(seeded, 'resolve', limitations=[{
        'category': 'image',
        'resolution': 'HTML/Markdown remote or missing images are not fetched; local-archive product limit.',
        'evidence': 'checked sample image-b00001; source file has a missing local image path.',
    }])
    updated = 'Inspected the markdown source; this one image was replaced by an attached local crop.'
    saved = send(batched, 'resolve', issues=[{'id': 'image-b00001', 'resolution': updated}])
    by_id = {item['id']: item for item in saved['issues']}
    assert by_id['image-b00001']['resolution'] == updated
    assert 'resolution_evidence' not in by_id['image-b00001']
    assert 'resolved_by' not in by_id['image-b00001']
    assert by_id['image-b00002']['resolved_by'] == 'limitation_batch'
    assert by_id['image-b00002']['resolution_evidence']
    confirmed = {item['id'] for item in validate(saved)['confirmed_limitations']}
    assert confirmed == {'image-b00002'}
    assert not any('Unresolved extraction issue: image-b00001' in item for item in validate(saved)['errors'])


def test_same_submit_per_id_overrides_batch_metadata(doc):
    seeded = _patch_issues(doc, [
        {'id': 'tex-includegraphics-1', 'message': 'fig', 'resolution': None},
        {'id': 'tex-includegraphics-2', 'message': 'fig', 'resolution': None},
    ])
    saved = send(seeded, 'resolve', limitations=[{
        'category': 'tex-includegraphics',
        'resolution': _LIMITATION_RESOLUTION,
        'evidence': _LIMITATION_EVIDENCE,
    }], issues=[{
        'id': 'tex-includegraphics-1',
        'resolution': 'Attached the missing figure from the local tex directory after inspection.',
    }])
    by_id = {item['id']: item for item in saved['issues']}
    assert by_id['tex-includegraphics-1']['resolution'].startswith('Attached')
    assert 'resolution_evidence' not in by_id['tex-includegraphics-1']
    assert 'resolved_by' not in by_id['tex-includegraphics-1']
    assert by_id['tex-includegraphics-2']['resolved_by'] == 'limitation_batch'
    assert by_id['tex-includegraphics-2']['resolution_evidence'] == _LIMITATION_EVIDENCE
    confirmed = {item['id'] for item in validate(saved)['confirmed_limitations']}
    assert confirmed == {'tex-includegraphics-2'}


def test_public_document_and_export_keep_limitation_warnings(doc, tmp_path, monkeypatch):
    from pathlib import Path
    from reader import exporter
    from reader.exporter import public_document
    monkeypatch.setattr(exporter, 'ROOT', tmp_path)
    seeded = _patch_issues(doc, [
        {'id': 'image-b00001', 'message': 'missing', 'resolution': None},
        {'id': 'tex-includegraphics-1', 'message': 'fig', 'resolution': None},
    ])
    d = send(seeded, 'resolve', limitations=[
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
    shown = public_document(d)
    assert any('image-b00001' in item and 'Confirmed known limitation' in item for item in shown['validation']['warnings'])
    assert any('tex-includegraphics-1' in item for item in shown['validation']['warnings'])
    assert {item['id'] for item in shown['validation']['confirmed_limitations']} == {'image-b00001', 'tex-includegraphics-1'}
    assert not any('Unresolved extraction issue' in item for item in shown['validation']['errors'])
    d = translated(d)
    d = send(d, 'review', blocks=[{
        'id': b['id'], 'translation_hash': digest(b['translation']), 'note': 'Fixture exact source reviewed',
    } for b in d['blocks']])
    d = send(d, 'full_review', fingerprint=fingerprint(d), note='Fixture includes known image and tex limitations')
    result = validate(d)
    assert result['ok'] is True
    assert {item['id'] for item in result['confirmed_limitations']} == {'image-b00001', 'tex-includegraphics-1'}
    exported = exporter.export_html(d['id'])
    assert exported['validation']['ok'] is True
    assert any('Confirmed known limitation' in item and 'not actually fixed' in item for item in exported['validation']['warnings'])
    html = Path(exported['path']).read_text(encoding='utf-8')
    assert 'Confirmed known limitation' in html
    assert 'image-b00001' in html
    assert 'tex-includegraphics-1' in html


def _repetitive_pending_error(message):
    import re
    return re.match(r'^\S+: (?:missing\b.*|second-pass review required)$', message) is not None


def test_default_progress_summarizes_large_limitation_batches(doc):
    tex_resolution = (
        'TEX_RESOLUTION_BLOB Single-file TeX does not compile; includegraphics assets stay unembedded as a product limit. '
        * 3
    ).strip()
    tex_evidence = (
        'TEX_EVIDENCE_BLOB checked source file sample tex-includegraphics-1; ' + ('not embedded no compile. ' * 16)
    ).strip()
    image_resolution = 'Markdown missing image is not fetched; local archive product limit.'
    image_evidence = 'IMAGE_EVIDENCE_BLOB checked sample image-b00001; source file has a missing local image.'
    issues = [{'id': f'tex-includegraphics-{i}', 'message': 'fig', 'resolution': None} for i in range(1, 301)]
    issues.extend([
        {'id': 'image-b00001', 'message': 'missing', 'resolution': None},
        {'id': 'image-b00002', 'message': 'missing', 'resolution': None},
        {'id': 'page-1', 'message': 'scan body stays unresolved', 'resolution': None},
    ])
    seeded = _patch_issues(doc, issues)
    saved = send(seeded, 'resolve', limitations=[
        {'category': 'tex-includegraphics', 'resolution': tex_resolution, 'evidence': tex_evidence},
        {'category': 'image', 'resolution': image_resolution, 'evidence': image_evidence},
    ])

    def plant_orphan(current):
        current['notes'].append({
            'id': 'n-orphan', 'block_id': 'missing-block', 'side': 'source',
            'start': 0, 'end': 1, 'quote': 'Z', 'text': 'reattach me', 'difficult': False,
        })

    saved = store.mutate(saved['id'], saved['revision'], plant_orphan)
    checked = validate(saved)
    view = project_document(saved, view='progress')
    full = project_document(saved, view='progress', full=True)
    view_json = json.dumps(view, ensure_ascii=False)
    full_json = json.dumps(full, ensure_ascii=False)

    assert view['projection'] is True
    assert view['outline_length'] >= 1 and view['unresolved_issues'] == 1
    assert 'confirmed_limitations' not in view
    assert 'resolution_evidence' not in view_json
    assert view_json.count(tex_evidence) == 0 and view_json.count(image_evidence) == 0
    assert view_json.count(tex_resolution) == 0
    assert 'tex-includegraphics-300' not in view_json and 'tex-includegraphics-4' not in view_json
    assert len(view_json.encode('utf-8')) < 10_000
    assert len(view_json.encode('utf-8')) * 20 < len(full_json.encode('utf-8'))
    assert view['ok'] == checked['ok']
    structural = [item for item in checked['errors'] if not _repetitive_pending_error(item)]
    assert structural and view['errors'] == structural and view['errors'] != checked['errors']
    assert not any('missing translation' in item for item in view['errors'])
    assert any('missing translation' in item for item in checked['errors'])
    missing = view['error_summary']['by_kind']['missing translation']
    assert view['error_summary']['total'] == len(checked['errors']) - len(structural)
    assert missing['count'] == sum(item.endswith(': missing translation') for item in checked['errors'])
    assert missing['sample_ids'] and len(missing['sample_ids']) <= 5
    assert view['pending_translate'] == missing['count'] and view['pending_review'] == 0
    assert view['blocks'] == checked['blocks'] and view['translated'] == checked['translated']
    assert view['reviewed'] == checked['reviewed']
    summary = view['confirmed_limitations_summary']
    assert summary['total'] == 302
    assert summary['by_category']['tex-includegraphics'] == {
        'count': 300,
        'sample_ids': ['tex-includegraphics-1', 'tex-includegraphics-2', 'tex-includegraphics-3'],
        'summary': _short_resolution(tex_resolution),
    }
    assert summary['by_category']['image'] == {
        'count': 2,
        'sample_ids': ['image-b00001', 'image-b00002'],
        'summary': image_resolution,
    }
    assert tex_evidence not in summary['by_category']['tex-includegraphics']['summary']
    limitation_lines = [item for item in view['warnings'] if item.startswith('Confirmed known limitation')]
    assert len(limitation_lines) == 2
    assert any('tex-includegraphics x300' in item and 'tex-includegraphics-1' in item for item in limitation_lines)
    assert any('image x2' in item and 'image-b00002' in item for item in limitation_lines)
    assert any(item == 'Note n-orphan needs reattachment' for item in view['warnings'])
    assert view['warning_summary'] == {
        'total': 303,
        'confirmed_limitations': 302,
        'other': 1,
        'by_category': {'tex-includegraphics': 300, 'image': 2},
    }

    assert 'projection' not in full and 'outline_length' not in full
    assert 'confirmed_limitations_summary' not in full and 'warning_summary' not in full
    assert 'error_summary' not in full and 'pending_translate' not in full and 'pending_review' not in full
    assert full['confirmed_limitations'] == checked['confirmed_limitations']
    assert full['warnings'] == checked['warnings'] and full['errors'] == checked['errors']
    assert len(full['confirmed_limitations']) == 302
    assert len(full['warnings']) == 303
    tex_rows = [item for item in full['confirmed_limitations'] if item['category'] == 'tex-includegraphics']
    assert len(tex_rows) == 300
    assert all(item['resolution'] == tex_resolution and item['resolution_evidence'] == tex_evidence for item in tex_rows)
    assert {item['id'] for item in tex_rows} == {f'tex-includegraphics-{i}' for i in range(1, 301)}
    image_rows = {item['id']: item for item in full['confirmed_limitations'] if item['category'] == 'image'}
    assert set(image_rows) == {'image-b00001', 'image-b00002'}
    assert image_rows['image-b00001']['resolution'] == image_resolution
    assert image_rows['image-b00001']['resolution_evidence'] == image_evidence
    assert full_json.count(tex_evidence) == 300
    assert any('tex-includegraphics-300 [tex-includegraphics]' in item for item in full['warnings'])
    assert any(item == 'Note n-orphan needs reattachment' for item in full['warnings'])


def _batch_doc(n=20):
    atoms, blocks = [], []
    for i in range(n):
        text = f'word {i}'
        atoms.append({'id': f'a{i:05d}', 'text': text, 'location': {'start': i}})
        blocks.append({'id': f'b{i:05d}', 'kind': 'paragraph', 'text': text, 'source_ids': [f'a{i:05d}'],
                       'asset': None, 'translation': None, 'history': [], 'review': None,
                       'structure_note': 'kept', 'user_edited': False})
    return {'schema_version': 1, 'id': 'e'*24, 'title': 'Batch', 'source_file': 'source.md', 'revision': 2,
            'stage': 'translate', 'atoms': atoms, 'blocks': blocks, 'terms': [], 'notes': [],
            'structure_review': {'agent': 'a', 'note': 'n'}, 'terms_review': {'agent': 'a', 'note': 'n'},
            'full_review': None, 'issues': []}


def test_default_task_batch_is_sixteen():
    doc = _batch_doc(20)
    assert DEFAULT_TASK_LIMIT == 16 and DEFAULT_SECTION_CONTEXT_LIMIT == 24
    assert len(tasks(doc)['blocks']) == 16
    assert len(project_document(doc, view='tasks')['blocks']) == 16
    assert len(project_document(doc, view='show')['blocks']) == 16
    assert [b['id'] for b in project_document(doc, view='tasks', limit=8)['blocks']] == [f'b{i:05d}' for i in range(8)]


def test_progress_collapses_missing_review_but_validate_lists_them(doc):
    saved = translated(doc)
    view = project_document(saved, view='progress')
    full = project_document(saved, view='progress', full=True)
    checked = validate(saved)
    assert full['errors'] == checked['errors']
    assert any(item.endswith(': second-pass review required') for item in checked['errors'])
    assert not any('second-pass review required' in item for item in view['errors'])
    assert 'Current whole-document review required' in view['errors']
    kind = view['error_summary']['by_kind']['second-pass review required']
    countable = [b for b in saved['blocks'] if b['kind'] not in {'figure', 'formula', 'code', 'page', 'excluded'}]
    assert kind['count'] == len(countable)
    assert kind['sample_messages'][0].endswith(': second-pass review required')
    assert view['pending_translate'] == 0 and view['pending_review'] == len(countable)
    assert pending_counts(saved) == {'pending_translate': 0, 'pending_review': len(countable)}


def test_progress_keeps_alignment_and_number_errors(doc):
    saved = translated(doc)
    block = saved['blocks'][1]
    saved = user_edit(saved['id'], {'revision': saved['revision'], 'operation': 'translation',
                                    'block_id': block['id'], 'pair_id': 'g1', 'text': '6 MW'})
    view = project_document(saved, view='progress')
    checked = validate(saved)
    assert view['ok'] is False and view['ok'] == checked['ok']
    assert any('unexplained differences' in item for item in view['errors'])
    assert any('unexplained differences' in item for item in checked['errors'])
    assert any('second-pass review required' in item for item in checked['errors'])
    assert not any('second-pass review required' in item for item in view['errors'])


def test_assemble_binds_revision_and_keeps_agent_hash(doc):
    before = doc['revision']
    block = doc['blocks'][0]
    item = {'id': block['id'], 'translation': {'text': '标题', 'pairs': [
        {'id': 'g1', 'source': [[0, len(block['text'])]], 'target': [[0, 2]]}]}}
    snapshot = tasks(doc)
    with pytest.raises(ValueError, match='task snapshot'):
        assemble_payload(doc, 'translate', [item], 'asm-tr', 'agent name')
    payload = assemble_payload(doc, 'translate', [item], 'asm-tr', 'agent name', snapshot)
    assert payload['revision'] == before == snapshot['revision']
    assert payload['submission_id'] == 'asm-tr' and payload['agent'] == 'agent name'
    assert payload['operation'] == 'translate' and payload['blocks'] == [item]
    assert store.read(doc['id'])['revision'] == before
    wrapped = assemble_payload(doc, 'translate', [item], 'asm-wrap', 'agent name', {'ok': True, 'result': snapshot})
    assert wrapped['revision'] == snapshot['revision']
    with pytest.raises(ValueError, match='does not match'):
        assemble_payload(doc, 'translate', [item], 'asm-other', 'agent name', {'revision': before, 'document_id': 'a' * 24})

    reviewed = translated(doc)
    target = reviewed['blocks'][0]
    review_snapshot = tasks(reviewed)
    with pytest.raises(ValueError, match='task snapshot'):
        assemble_payload(reviewed, 'review', [{'id': target['id'], 'note': 'second pass'}], 'asm-rv', 'agent name')
    filled = assemble_payload(reviewed, 'review', [{'id': target['id'], 'note': 'second pass'}], 'asm-rv', 'agent name', review_snapshot)
    assert filled['revision'] == reviewed['revision'] == review_snapshot['revision']
    assert filled['blocks'][0]['translation_hash'] == digest(target['translation'])
    assert filled['blocks'][0]['note'] == 'second pass'
    full_row = copy.deepcopy(next(row for row in review_snapshot['blocks'] if row['id'] == target['id']))
    section_only = {
        'revision': review_snapshot['revision'], 'document_id': reviewed['id'], 'blocks': [],
        'section_context': [full_row],
    }
    from_section = assemble_payload(reviewed, 'review', [{'id': target['id'], 'note': 'from section'}], 'asm-sec', 'agent name', section_only)
    assert from_section['blocks'][0]['translation_hash'] == digest(target['translation'])
    for row in review_snapshot['context'] + review_snapshot['section_context']:
        if row['id'] == target['id']:
            assert set(row) == {'id', 'ref'} and row['ref'] is True
            assert 'text' not in row and 'translation_hash' not in row
    omitted = assemble_payload(reviewed, 'review', [{'id': target['id'], 'note': 'from blocks'}], 'asm-shell', 'agent name', review_snapshot)
    assert omitted['blocks'][0]['translation_hash'] == digest(target['translation'])
    shell_only = {
        'revision': review_snapshot['revision'], 'document_id': reviewed['id'],
        'blocks': [{'id': target['id'], 'ref': True}],
        'context': [{'id': target['id'], 'ref': True}],
        'section_context': [{'id': target['id'], 'ref': True}],
    }
    with pytest.raises(ValueError, match='translation_hash'):
        assemble_payload(reviewed, 'review', [{'id': target['id'], 'note': 'shells'}], 'asm-nohash', 'agent name', shell_only)
    kept = assemble_payload(reviewed, 'review', [{
        'id': target['id'], 'translation_hash': 'deadbeef', 'note': 'kept',
    }], 'asm-keep', 'agent name', review_snapshot)
    assert kept['blocks'][0]['translation_hash'] == 'deadbeef'
    assert kept['revision'] == review_snapshot['revision']
    blank = assemble_payload(reviewed, 'review', [{
        'id': target['id'], 'translation_hash': '', 'note': 'fill me',
    }], 'asm-blank', 'agent name', review_snapshot)
    assert blank['blocks'][0]['translation_hash'] == digest(target['translation'])
    missing_note = assemble_payload(reviewed, 'review', [{'id': target['id']}], 'asm-nonote', 'agent name', review_snapshot)
    with pytest.raises(ValueError, match='Review evidence'):
        submit(reviewed['id'], missing_note)
    with pytest.raises(ValueError, match='task snapshot'):
        assemble_payload(doc, 'review', [{'id': block['id'], 'note': 'too early'}], 'asm-early', 'agent name')
    with pytest.raises(ValueError, match='translation_hash'):
        assemble_payload(doc, 'review', [{'id': block['id'], 'note': 'too early'}], 'asm-early', 'agent name', tasks(doc))
    stale = {'revision': before, 'document_id': doc['id']}
    stale_payload = assemble_payload(reviewed, 'translate', [item], 'asm-stale', 'agent name', stale)
    assert stale_payload['revision'] == before
    assert stale_payload['revision'] != reviewed['revision']


def test_assemble_review_does_not_silently_approve_changed_translation(doc):
    reviewed = translated(doc)
    target = reviewed['blocks'][0]
    snapshot = tasks(reviewed)
    old_revision = snapshot['revision']
    old_hash = next(row['translation_hash'] for row in snapshot['blocks'] if row['id'] == target['id'])
    assert old_hash == digest(target['translation'])
    changed = user_edit(reviewed['id'], {
        'revision': reviewed['revision'], 'operation': 'translation',
        'block_id': target['id'], 'pair_id': 'g1', 'text': '用户改过的译文',
    })
    live = next(block for block in changed['blocks'] if block['id'] == target['id'])
    new_hash = digest(live['translation'])
    assert changed['revision'] != old_revision and new_hash != old_hash
    pending_before = pending_counts(changed)['pending_review']
    assert pending_before > 0
    notes = [{'id': row['id'], 'note': 'reviewed the text I read'} for row in snapshot['blocks']]
    assert notes and len(notes) == pending_before

    with pytest.raises(ValueError, match='task snapshot'):
        assemble_payload(changed, 'review', notes, 'silent', 'agent')

    stamped = assemble_payload(changed, 'review', notes, 'from-snapshot', 'agent', snapshot)
    assert stamped['revision'] == old_revision
    stamped_hash = next(row['translation_hash'] for row in stamped['blocks'] if row['id'] == target['id'])
    assert stamped_hash == old_hash and stamped_hash != new_hash
    with pytest.raises(Conflict):
        submit(changed['id'], stamped)

    current = {'revision': changed['revision'], 'document_id': changed['id']}
    explicit = []
    for block in changed['blocks']:
        if block['kind'] in {'figure', 'formula', 'code', 'page', 'excluded'} or not block.get('translation'):
            continue
        bound = old_hash if block['id'] == target['id'] else digest(block['translation'])
        explicit.append({'id': block['id'], 'translation_hash': bound, 'note': 'explicit hash from the read'})
    explicit_payload = assemble_payload(changed, 'review', explicit, 'explicit-old', 'agent', current)
    assert explicit_payload['revision'] == changed['revision']
    assert next(row['translation_hash'] for row in explicit_payload['blocks'] if row['id'] == target['id']) == old_hash
    with pytest.raises(ValueError, match='current translation hash'):
        submit(changed['id'], explicit_payload)

    saved = store.read(changed['id'])
    assert saved['revision'] == changed['revision']
    assert pending_counts(saved)['pending_review'] == pending_before
    assert pending_counts(saved)['pending_review'] != 0


def test_cli_limit_pretty_assemble_and_submit_counts(doc, monkeypatch, capsys, tmp_path):
    from reader.cli import main

    def run(*argv):
        monkeypatch.setattr(sys, 'argv', ['paper', *argv])
        try:
            main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr().out

    wide = tmp_path/'wide.md'
    wide.write_text('\n\n'.join(f'Paragraph {i} has enough words to be its own block.' for i in range(20)), encoding='utf-8')
    code, raw = run('import', str(wide))
    assert code == 0 and '\n' not in raw.strip() and raw.startswith('{"ok":true,')
    imported = json.loads(raw)['result']
    code, raw = run('tasks', imported['document_id'])
    assert code == 0 and '\n' not in raw.strip()
    assert len(json.loads(raw)['result']['blocks']) == 16
    code, raw = run('tasks', imported['document_id'], '--limit', '4')
    assert len(json.loads(raw)['result']['blocks']) == 4
    code, pretty = run('tasks', imported['document_id'], '--limit', '2', '--pretty')
    assert pretty.startswith('{\n') and '\n  "ok"' in pretty
    assert len(json.loads(pretty)['result']['blocks']) == 2

    code, raw = run('validate', doc['id'])
    assert code == 2 and '\n' not in raw.strip()
    validated = json.loads(raw)['result']
    assert validated['ok'] is False and any(item.endswith(': missing translation') for item in validated['errors'])
    code, raw = run('progress', doc['id'], '--full')
    full_progress = json.loads(raw)['result']
    assert code == 0 and full_progress['errors'] == validated['errors']
    assert 'error_summary' not in full_progress and 'pending_translate' not in full_progress
    code, raw = run('progress', doc['id'])
    projected = json.loads(raw)['result']
    assert projected['errors'] != validated['errors']
    assert not any(item.endswith(': missing translation') for item in projected['errors'])
    assert projected['error_summary']['by_kind']['missing translation']['count'] == sum(
        item.endswith(': missing translation') for item in validated['errors'])
    assert projected['pending_translate'] == projected['error_summary']['by_kind']['missing translation']['count']

    block = doc['blocks'][0]
    blocks_path = tmp_path/'blocks.json'
    blocks_path.write_text(json.dumps([{'id': block['id'], 'translation': {'text': '标题', 'pairs': [
        {'id': 'g1', 'source': [[0, len(block['text'])]], 'target': [[0, 2]]}]}}], ensure_ascii=False), encoding='utf-8')
    code, raw = run('assemble', doc['id'], 'translate', '--blocks', str(blocks_path),
                    '--submission-id', 'cli-missing-task', '--agent', 'tester')
    assert code == 1 and 'task snapshot' in json.loads(raw)['error']
    task_path = tmp_path/'tasks.json'
    task_path.write_text(json.dumps({'ok': True, 'result': {'revision': doc['revision'], 'document_id': doc['id']}},
                                    ensure_ascii=False), encoding='utf-8')
    code, raw = run('assemble', doc['id'], 'translate', '--blocks', str(blocks_path),
                    '--submission-id', 'cli-default', '--agent', 'tester', '--task', str(task_path))
    assert code == 0
    default_result = json.loads(raw)['result']
    default_path = store.ROOT / 'submissions' / f'{doc["id"]}-translate-cli-default.json'
    assert default_result['path'] == str(default_path) and default_path.is_file()
    assert json.loads(default_path.read_text(encoding='utf-8'))['revision'] == doc['revision']
    sentinel_task = tmp_path/'sentinel-task.json'
    sentinel_task.write_text(json.dumps({'revision': doc['revision'] + 99}), encoding='utf-8')
    sentinel_out = tmp_path/'sentinel.json'
    code, raw = run('assemble', doc['id'], 'translate', '--blocks', str(blocks_path),
                    '--submission-id', 'cli-sentinel', '--agent', 'tester', '--task', str(sentinel_task),
                    '--out', str(sentinel_out))
    assert code == 0 and json.loads(sentinel_out.read_text(encoding='utf-8'))['revision'] == doc['revision'] + 99
    out = tmp_path/'payload.json'
    code, raw = run('assemble', doc['id'], 'translate', '--blocks', str(blocks_path),
                    '--submission-id', 'cli-asm', '--agent', 'tester', '--task', str(task_path), '--out', str(out))
    assert code == 0
    result = json.loads(raw)['result']
    assert result['path'] == str(out) and result['revision'] == doc['revision']
    assert result['operation'] == 'translate' and result['block_count'] == 1
    written = json.loads(out.read_text(encoding='utf-8'))
    assert written['revision'] == doc['revision'] and written['blocks'][0]['translation']['text'] == '标题'
    assert store.read(doc['id'])['revision'] == doc['revision']
    code, raw = run('submit', doc['id'], str(out))
    assert code == 0 and '\n' not in raw.strip()
    submitted = json.loads(raw)['result']
    assert submitted['revision'] == doc['revision'] + 1
    assert submitted['stage'] == 'translate'
    assert submitted['pending_translate'] == len(doc['blocks']) - 1
    assert submitted['pending_review'] == 1
    assert 'blocks' not in submitted and 'atoms' not in submitted


def test_projection_json_size_microbenchmark(doc):
    # 80 pending blocks, each with two ~2.6k-char history entries and a repeated structure note.
    # Measured compact UTF-8 JSON on this fixture: tasks blocks at limit 16 are 4208 B without
    # history vs 116672 B with history (~27.7x). Default tasks view is 15618 B vs 416271 B when
    # history is restored on blocks/context/section_context (~26.7x). Default progress is 1023 B
    # vs progress --full 2801 B (~2.7x) while 80 missing-translation lines are listed in full.
    n = 80
    history_text = 'PREVIOUS TRANSLATION ' * 120
    note = 'Checked structure against the source page and kept the extracted order. ' * 12

    def expand(current):
        atoms, blocks = [], []
        for i in range(n):
            text = f'Paragraph {i:03d} reports 12.5 kW at 400 K under steady load. '
            atoms.append({'id': f'a{i:05d}', 'text': text, 'location': {'start': i, 'end': i + 1}})
            blocks.append({
                'id': f'b{i:05d}', 'kind': 'paragraph', 'text': text, 'source_ids': [f'a{i:05d}'],
                'asset': None, 'translation': None,
                'history': [
                    {'translation': {'text': history_text, 'pairs': [{'id': 'g1', 'source': [[0, 1]], 'target': [[0, 1]]}]}, 'author': 'agent'},
                    {'translation': {'text': history_text + ' v2', 'pairs': [{'id': 'g1', 'source': [[0, 1]], 'target': [[0, 1]]}]}, 'author': 'agent'},
                ],
                'review': None, 'structure_note': note, 'source_change': note, 'user_edited': False,
            })
        current['atoms'] = atoms
        current['blocks'] = blocks
        current['issues'] = []

    saved = store.mutate(doc['id'], doc['revision'], expand)
    view = project_document(saved, view='tasks')
    assert len(view['blocks']) == 16
    assert all('history' not in block and 'structure_note' not in block for block in view['blocks'])
    by_id = {block['id']: block for block in saved['blocks']}

    def inflate(rows):
        fat = []
        for row in rows:
            block = dict(by_id[row['id']])
            block['translation_hash'] = None
            fat.append(block)
        return fat

    slim_blocks = len(json.dumps(view['blocks'], ensure_ascii=False).encode())
    fat_blocks = len(json.dumps(inflate(view['blocks']), ensure_ascii=False).encode())
    slim_view = len(json.dumps(view, ensure_ascii=False).encode())
    fat_view = len(json.dumps({
        **view,
        'blocks': inflate(view['blocks']),
        'context': inflate(view['context']),
        'section_context': inflate(view['section_context']),
    }, ensure_ascii=False).encode())
    assert slim_blocks * 8 < fat_blocks, (slim_blocks, fat_blocks)
    assert slim_view * 8 < fat_view, (slim_view, fat_view)

    progress = project_document(saved, view='progress')
    progress_full = project_document(saved, view='progress', full=True)
    checked = validate(saved)
    assert progress_full['errors'] == checked['errors']
    assert progress['pending_translate'] == n and progress['pending_review'] == 0
    missing = progress['error_summary']['by_kind']['missing translation']
    assert missing['count'] == n and len(missing['sample_ids']) == 5
    assert len(progress['errors']) < 5
    assert sum(item.endswith(': missing translation') for item in progress_full['errors']) == n
    assert all(f'b{i:05d}: missing translation' in progress_full['errors'] for i in (0, n - 1))
    prog_bytes = len(json.dumps(progress, ensure_ascii=False).encode())
    full_bytes = len(json.dumps(progress_full, ensure_ascii=False).encode())
    assert prog_bytes * 2 < full_bytes, (prog_bytes, full_bytes, slim_blocks, fat_blocks, slim_view, fat_view)


def test_fill_pair_offsets_whole_block_assemble_submit(doc):
    block = doc['blocks'][0]
    items = [{'id': block['id'], 'translation': {'text': '标题', 'whole': True}}]
    out = fill_pair_offsets(doc, items)
    assert out == [{'id': block['id'], 'translation': {
        'text': '标题', 'pairs': [{'id': 'g1', 'source': [[0, len(block['text'])]], 'target': [[0, 2]]}]}}]
    check_translation(block, out[0]['translation'])
    payload = assemble_payload(doc, 'translate', out, 'po-whole', 'agent', tasks(doc))
    saved = submit(doc['id'], payload)
    assert next(b for b in saved['blocks'] if b['id'] == block['id'])['translation']['text'] == '标题'


def test_fill_pair_offsets_exact_and_discontiguous_fragments(doc):
    block = doc['blocks'][1]
    src = block['text']
    left, right = 'A is 5 kW.', 'B is safe.'
    assert src.find(left) >= 0 and src.find(right) > src.find(left)
    zh_left, zh_right = '甲是 5 千瓦。', '乙是安全的。'
    tgt = zh_left + ' ' + zh_right
    out = fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': tgt, 'groups': [
        {'id': 'g1', 'source': left, 'target': zh_left},
        {'id': 'g2', 'source': right, 'target': zh_right},
    ]}}])
    pairs = out[0]['translation']['pairs']
    assert pairs[0]['source'] == [[src.find(left), src.find(left) + len(left)]]
    assert pairs[1]['source'] == [[src.find(right), src.find(right) + len(right)]]
    assert pairs[0]['target'] == [[tgt.find(zh_left), tgt.find(zh_left) + len(zh_left)]]
    assert pairs[1]['target'] == [[tgt.find(zh_right), tgt.find(zh_right) + len(zh_right)]]
    check_translation(block, out[0]['translation'])
    first, second = 'A is', '5 kW.'
    listed = fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': tgt, 'groups': [
        {'source': [first, second], 'target': zh_left},
        {'source': right, 'target': zh_right},
    ]}}])
    listed_pairs = listed[0]['translation']['pairs']
    assert listed_pairs[0]['source'] == [
        [src.find(first), src.find(first) + len(first)],
        [src.find(second), src.find(second) + len(second)],
    ]
    assert listed_pairs[0]['id'] == 'g1'
    check_translation(block, listed[0]['translation'])


def test_fill_pair_offsets_repeated_fragment_uses_remaining_text():
    src = 'ab ab'
    block = {'id': 'b00001', 'text': src}
    doc = {'blocks': [block]}
    out = fill_pair_offsets(doc, [{'id': 'b00001', 'translation': {'text': '甲 乙', 'groups': [
        {'source': 'ab', 'target': '甲'},
        {'source': 'ab', 'target': '乙'},
    ]}}])
    assert out[0]['translation']['pairs'][0]['source'] == [[0, 2]]
    assert out[0]['translation']['pairs'][1]['source'] == [[3, 5]]
    check_translation(block, out[0]['translation'])


def test_fill_pair_offsets_fragment_not_found_and_uncovered(doc):
    block = doc['blocks'][1]
    with pytest.raises(ValueError, match='fragment not found') as missing:
        fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': '甲', 'groups': [
            {'source': 'no-such-source-fragment', 'target': '甲'},
        ]}}])
    assert 'source' in str(missing.value)
    with pytest.raises(ValueError, match='uncovered'):
        fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': '甲', 'groups': [
            {'source': 'A is', 'target': '甲'},
        ]}}])


def test_fill_pair_offsets_refuses_automatic_split(doc):
    block = doc['blocks'][1]
    with pytest.raises(ValueError, match='semantic groups required') as refused:
        fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': '甲。乙。'}}])
    assert 'g1' not in str(refused.value) or 'pairs' not in str(refused.value)
    with pytest.raises(ValueError, match='semantic groups required'):
        fill_pair_offsets(doc, [{'id': block['id'], 'translation': {
            'text': '甲。乙。',
            'pairs': [{'id': 'g1', 'source': [[0, 1]], 'target': [[0, 1]]}],
        }}])


def test_pair_offsets_cli_writes_assemble_blocks(doc, monkeypatch, capsys, tmp_path):
    from reader.cli import main

    def run(*argv):
        monkeypatch.setattr(sys, 'argv', ['paper', *argv])
        try:
            main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr().out

    block = doc['blocks'][0]
    groups_path = tmp_path / 'groups.json'
    groups_path.write_text(json.dumps([{'id': block['id'], 'translation': {'text': '标题', 'whole': True}}],
                                      ensure_ascii=False), encoding='utf-8')
    aligned = tmp_path / 'aligned.json'
    code, raw = run('pair-offsets', doc['id'], '--blocks', str(groups_path), '--out', str(aligned))
    assert code == 0
    envelope = json.loads(raw)
    assert envelope['ok'] is True
    assert envelope['result'] == {'path': str(aligned), 'block_count': 1}
    assert '标题' not in raw
    written = json.loads(aligned.read_text(encoding='utf-8'))
    code, raw = run('pair-offsets', doc['id'], '--blocks', str(groups_path))
    assert code == 0
    assert json.loads(raw)['result'] == written
    assert written[0]['translation']['pairs'][0]['source'] == [[0, len(block['text'])]]
    check_translation(block, written[0]['translation'])
    task_path = tmp_path / 'tasks.json'
    task_path.write_text(json.dumps({'ok': True, 'result': tasks(doc)}, ensure_ascii=False), encoding='utf-8')
    payload_path = tmp_path / 'payload.json'
    code, raw = run('assemble', doc['id'], 'translate', '--blocks', str(aligned),
                    '--submission-id', 'po-cli', '--agent', 'tester', '--task', str(task_path),
                    '--out', str(payload_path))
    assert code == 0
    code, raw = run('submit', doc['id'], str(payload_path))
    assert code == 0
    assert json.loads(raw)['result']['pending_review'] == 1


def _prepared_doc(tmp_path, monkeypatch, text):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    path = tmp_path / 'case.md'
    path.write_text(text, encoding='utf-8')
    current = import_document(path)
    blocks = [{**block, 'structure_note': 'Checked against Markdown source'} for block in current['blocks']]
    current = send(current, 'structure', blocks=blocks, note='Structure checked')
    return send(current, 'terms', terms=[])


def test_fill_pair_offsets_reordered_translation_submits(tmp_path, monkeypatch):
    doc = _prepared_doc(tmp_path, monkeypatch, 'A. B.\n')
    block = doc['blocks'][0]
    assert block['text'] == 'A. B.'
    out = fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': '乙。甲。', 'groups': [
        {'source': 'A.', 'target': '甲。'},
        {'source': 'B.', 'target': '乙。'},
    ]}}])
    pairs = out[0]['translation']['pairs']
    assert [pair['source'] for pair in pairs] == [[[0, 2]], [[3, 5]]]
    assert [pair['target'] for pair in pairs] == [[[2, 4]], [[0, 2]]]
    check_translation(block, out[0]['translation'])
    payload = assemble_payload(doc, 'translate', out, 'po-reorder', 'agent', tasks(doc))
    saved = submit(doc['id'], payload)
    stored = next(item for item in saved['blocks'] if item['id'] == block['id'])['translation']
    assert stored['text'] == '乙。甲。'
    check_translation(block, stored)
    reversed_out = fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': '乙。甲。', 'groups': [
        {'source': ['B.', 'A.'], 'target': ['乙。', '甲。']},
    ]}}])
    pair = reversed_out[0]['translation']['pairs'][0]
    assert pair['source'] == [[3, 5], [0, 2]]
    assert pair['target'] == [[0, 2], [2, 4]]
    check_translation(block, reversed_out[0]['translation'])


def test_fill_pair_offsets_interleaved_groups_submits(tmp_path, monkeypatch):
    doc = _prepared_doc(tmp_path, monkeypatch, 'A B C\n')
    block = doc['blocks'][0]
    assert block['text'] == 'A B C'
    out = fill_pair_offsets(doc, [{'id': block['id'], 'translation': {'text': '甲 乙 丙', 'groups': [
        {'source': ['A', 'C'], 'target': ['甲', '丙']},
        {'source': 'B', 'target': '乙'},
    ]}}])
    pairs = out[0]['translation']['pairs']
    assert pairs[0]['source'] == [[0, 1], [4, 5]]
    assert pairs[1]['source'] == [[2, 3]]
    assert pairs[0]['target'] == [[0, 1], [4, 5]]
    assert pairs[1]['target'] == [[2, 3]]
    check_translation(block, out[0]['translation'])
    payload = assemble_payload(doc, 'translate', out, 'po-interleave', 'agent', tasks(doc))
    saved = submit(doc['id'], payload)
    stored = next(item for item in saved['blocks'] if item['id'] == block['id'])['translation']
    assert stored['text'] == '甲 乙 丙'
    check_translation(block, stored)


def test_fill_pair_offsets_occurrence_and_anchor_disambiguate():
    block = {'id': 'b00001', 'text': 'ab ab'}
    doc = {'blocks': [block]}
    out = fill_pair_offsets(doc, [{'id': 'b00001', 'translation': {'text': '乙 甲', 'groups': [
        {'source': {'text': 'ab', 'occurrence': 2}, 'target': '乙'},
        {'source': {'text': 'ab', 'occurrence': 1}, 'target': '甲'},
    ]}}])
    pairs = out[0]['translation']['pairs']
    assert [pair['source'] for pair in pairs] == [[[3, 5]], [[0, 2]]]
    assert [pair['target'] for pair in pairs] == [[[0, 1]], [[2, 3]]]
    check_translation(block, out[0]['translation'])
    anchored = fill_pair_offsets(doc, [{'id': 'b00001', 'translation': {'text': '乙 甲', 'groups': [
        {'source': {'text': 'ab', 'anchor': ' ab'}, 'target': '乙'},
        {'source': 'ab', 'target': '甲'},
    ]}}])
    assert anchored[0]['translation']['pairs'][0]['source'] == [[3, 5]]
    assert anchored[0]['translation']['pairs'][1]['source'] == [[0, 2]]
    check_translation(block, anchored[0]['translation'])
    with pytest.raises(ValueError, match='fragment already used'):
        fill_pair_offsets(doc, [{'id': 'b00001', 'translation': {'text': '甲乙', 'groups': [
            {'source': {'text': 'ab', 'occurrence': 1}, 'target': '甲'},
            {'source': {'text': 'ab', 'occurrence': 1}, 'target': '乙'},
        ]}}])
    with pytest.raises(ValueError, match='fragment not found'):
        fill_pair_offsets(doc, [{'id': 'b00001', 'translation': {'text': '甲 乙', 'groups': [
            {'source': {'text': 'ab', 'occurrence': 3}, 'target': '甲'},
            {'source': 'ab', 'target': '乙'},
        ]}}])
    with pytest.raises(ValueError, match='anchor must contain fragment'):
        fill_pair_offsets(doc, [{'id': 'b00001', 'translation': {'text': '甲 乙', 'groups': [
            {'source': {'text': 'ab', 'anchor': 'zz'}, 'target': '甲'},
        ]}}])
    for bad in (True, 0):
        with pytest.raises(ValueError, match='occurrence must be a positive integer'):
            fill_pair_offsets(doc, [{'id': 'b00001', 'translation': {'text': '甲 乙', 'groups': [
                {'source': {'text': 'ab', 'occurrence': bad}, 'target': '甲'},
            ]}}])
