"""Authored bibliography: preserve sources without redundant translation work."""
import copy

import pytest

from reader import store
from reader.importer import import_document
from reader.workflow import (
    submit, tasks, validate, fingerprint, pending_counts, fill_pair_offsets, user_edit,
)


def send(doc, op, **kw):
    return submit(doc['id'], {'revision': doc['revision'], 'submission_id': f'{op}-{doc["revision"]}',
                             'operation': op, 'agent': 'authored reference fixture', **kw})


@pytest.fixture
def bibliography(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    source = tmp_path/'study.md'
    source.write_text('# Study\n\nThe device uses 7 kW.\n\n## References\n\n'
                      '[1] Alice Example. A fictional study. Example Journal, 2024, 2:10–12.\n\n'
                      '[2] Bob Example. Another fictional study. 2025. https://example.test/reference\n',
                      encoding='utf-8')
    doc = import_document(source)
    rows = copy.deepcopy(doc['blocks'])
    for b in rows:
        b['structure_note'] = 'Authored source checked, including bibliography numbering and link.'
        if b['text'].startswith(('[1]', '[2]')):
            b['kind'] = 'reference'
    doc = send(doc, 'structure', blocks=rows, note='All authored bibliography entries preserved.')
    return send(doc, 'terms', terms=[])


def translate_body(doc):
    targets = {'# Study':'研究示例', 'The device uses 7 kW.':'该设备使用 7 kW。',
               '## References':'参考文献'}
    blocks = fill_pair_offsets(doc, [
        {'id': b['id'], 'translation': {'text': targets[b['text']], 'whole': True}}
        for b in doc['blocks'] if b['kind'] != 'reference'
    ])
    return send(doc, 'translate', blocks=blocks)


def test_reference_sources_complete_without_translation_or_pair_work(bibliography):
    refs = [b for b in bibliography['blocks'] if b['kind'] == 'reference']
    body = [b for b in bibliography['blocks'] if b['kind'] != 'reference']
    assert {b['id'] for b in tasks(bibliography)['blocks']} == {b['id'] for b in body}
    assert pending_counts(bibliography) == {'pending_translate':3, 'pending_review':0}
    doc = translate_body(bibliography)
    assert {b['id'] for b in tasks(doc)['blocks']} == {b['id'] for b in body}
    doc = send(doc, 'review', blocks=[{'id': b['id'], 'translation_hash': store.digest(b['translation']),
                                     'note':'Authored text meaning checked.'} for b in doc['blocks'] if b['kind']!='reference'])
    assert doc['stage'] == 'full_review'
    doc = send(doc, 'full_review', fingerprint=fingerprint(doc),
               note='All authored sources checked, including complete references and link.')
    assert validate(doc)['ok']
    assert doc['stage'] == 'complete'
    assert pending_counts(doc) == {'pending_translate':0, 'pending_review':0}
    assert [b for b in doc['blocks'] if b['kind'] == 'reference'] == refs
    assert doc['atoms'] == bibliography['atoms']
    changed = copy.deepcopy(doc)
    next(b for b in changed['blocks'] if b['kind']=='reference')['text'] += 'changed'
    assert fingerprint(changed) != fingerprint(doc)
    assert any('whole-document review required' in e for e in validate(changed)['errors'])


def test_reference_source_changes_still_need_fresh_reason(bibliography):
    rows = copy.deepcopy(bibliography['blocks'])
    next(b for b in rows if b['kind']=='reference')['text'] = '[1] Invented replacement.'
    with pytest.raises(ValueError, match='source_change'):
        send(bibliography, 'structure', blocks=rows, note='Changed source fixture')
    assert store.read(bibliography['id'])['revision'] == bibliography['revision']


def test_preserved_references_keep_ledger_and_source_review_gate(bibliography):
    missing_reason = copy.deepcopy(bibliography)
    next(b for b in missing_reason['blocks'] if b['kind']=='reference')['structure_note'] = ''
    assert any('reference content needs source-check reason' in e for e in validate(missing_reason)['errors'])
    missing_source = copy.deepcopy(bibliography)
    missing_source['blocks'] = [b for b in missing_source['blocks'] if not b['text'].startswith('[2]')]
    assert 'Source ledger is not an exact partition' in validate(missing_source)['errors']


def test_empty_preserved_entry_is_not_accepted_as_complete(bibliography):
    rows = copy.deepcopy(bibliography['blocks'])
    ref = next(b for b in rows if b['kind']=='reference')
    ref.update(text='', source_change='Authored empty-display failure fixture.')
    with pytest.raises(ValueError, match='reference text cannot be empty'):
        send(bibliography, 'structure', blocks=rows, note='Empty entry fixture')
    assert store.read(bibliography['id'])['revision'] == bibliography['revision']
    candidate = copy.deepcopy(bibliography)
    candidate['blocks'] = rows
    assert any('reference text cannot be empty' in e for e in validate(candidate)['errors'])


@pytest.mark.parametrize('translate_title', [False, True])
def test_explicit_or_legacy_reference_translation_still_requires_review(bibliography, translate_title):
    doc = translate_body(bibliography)
    ref = next(b for b in doc['blocks'] if b['kind']=='reference')
    text = ref['text'].replace('A fictional study.', '一项虚构研究。') if translate_title else ref['text']
    blocks = fill_pair_offsets(doc, [{'id': ref['id'], 'translation': {'text':text, 'whole':True}}])
    doc = send(doc, 'translate', blocks=blocks)
    assert pending_counts(doc)['pending_review'] == 4
    assert ref['id'] in {b['id'] for b in tasks(doc)['blocks']}
    assert any(ref['id']+': second-pass review required' in e for e in validate(doc)['errors'])
    doc = send(doc, 'review', blocks=[{'id': b['id'], 'translation_hash':store.digest(b['translation']),
                                     'note':'Current authored translation checked.'} for b in doc['blocks'] if b['translation']])
    doc = send(doc, 'full_review', fingerprint=fingerprint(doc), note='All sources and explicit title translation checked.')
    assert validate(doc)['ok']
    current = next(b for b in doc['blocks'] if b['id']==ref['id'])
    doc = user_edit(doc['id'], {'revision':doc['revision'], 'operation':'translation',
                              'block_id':ref['id'], 'pair_id':current['translation']['pairs'][0]['id'],
                              'text':text.replace('Example Journal', 'Example Journal (user note)')})
    assert pending_counts(doc)['pending_review'] == 1
    assert not validate(doc)['ok']
    assert next(b for b in doc['blocks'] if b['id']==ref['id'])['user_edited']
