"""Authored fixtures: table label mode, retained sources, and cell offsets."""
import copy
import json
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from reader import store
from reader.importer import import_document
from reader.workflow import submit, tasks, validate, fingerprint, fill_pair_offsets

ROOT = Path(__file__).resolve().parents[1]


def parse_table(text):
    script = ('import {parseTable} from ' + json.dumps((ROOT/'dev/web/src/tabletext.mjs').as_uri()) + ';'
              'process.stdout.write(JSON.stringify(parseTable(' + json.dumps(text) + ')));')
    return json.loads(subprocess.run(['node', '--input-type=module', '-e', script],
                                    capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize('text, expected', [
    ('| Item | Value |\n| --- | ---: |\n| Alpha | 8 |', [['Item', 'Value'], ['Alpha', '8']]),
    ('Item | Value\nAlpha | 8\nBeta | ', [['Item', 'Value'], ['Alpha', '8'], ['Beta', '']]),
    ('Item\tValue\n\t8\nBeta\t', [['Item', 'Value'], ['', '8'], ['Beta', '']]),
    ('A | B | C\nx || 8', [['A', 'B', 'C'], ['x', '', '8']]),
    ('😀 Item | Value\n模型 | 8', [['😀 Item', 'Value'], ['模型', '8']]),
    (r'Expression | Value'+'\n'+r'$a\|b$ | 8', [['Expression', 'Value'], [r'$a\|b$', '8']]),
])
def test_cell_structure_and_unicode_offsets(text, expected):
    parsed = parse_table(text)
    assert [[c['text'] for c in row] for row in parsed['rows']] == expected
    for row in parsed['rows']:
        for cell in row:
            assert text[cell['start']:cell['end']] == cell['text']
    assert parsed['header'] == ('---' in text)


@pytest.mark.parametrize('text', ['Ordinary prose.', 'A | B\nx | y | z', 'A | B\nnot a row'])
def test_unreliable_table_shape_is_not_guessed(text):
    assert parse_table(text) is None


@pytest.fixture
def study(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path/'data')
    source = tmp_path/'study.md'
    source.write_text('# Study\n\nAlice (Example Lab, alice@example.test).\n\n'
                      'Equal contribution; work performed at Example Lab.\n\n'
                      'Item | Value\n--- | ---\nBase | 8\nLarge | 9\n', encoding='utf-8')
    doc = import_document(source)
    Image.new('RGB', (400, 180), 'white').save(store.folder(doc['id'])/'table.png')
    return doc


def send(doc, op, **kw):
    return submit(doc['id'], {'revision': doc['revision'], 'submission_id': f'{op}-{doc["revision"]}',
                             'operation': op, 'agent': 'authored fixture', **kw})


def image_rows(doc):
    rows = copy.deepcopy(doc['blocks'])
    rows[1]['kind'] = 'metadata'
    rows[2]['kind'] = 'footnote'
    table = next(b for b in rows if b['kind'] == 'table')
    table.update(table_mode='image', asset='table.png', text='Item\nValue\nBase\nLarge',
                 source_change='Numbers remain in the verified original table image and immutable atoms; list its labels only.')
    for b in rows:
        b['structure_note'] = 'Authored source and table image inspected.'
    return rows


def test_image_table_preserves_numeric_ledger_and_requires_translation_review(study):
    doc = send(study, 'structure', blocks=image_rows(study), note='Original image and labels checked')
    doc = send(doc, 'terms', terms=[])
    table = next(b for b in doc['blocks'] if b['kind'] == 'table')
    assert table['text'] == 'Item\nValue\nBase\nLarge'
    assert table['source_ids'] == next(b for b in study['blocks'] if b['kind'] == 'table')['source_ids']
    assert doc['atoms'] == study['atoms']
    assert any('8' in a['text'] and '9' in a['text'] for a in doc['atoms'])
    projected = next(b for b in tasks(doc)['blocks'] if b['id'] == table['id'])
    assert projected['table_mode'] == 'image'
    assert projected['asset'] == 'table.png'
    assert len(tasks(doc)['blocks']) == len(doc['blocks'])  # Metadata, footnotes and labels still need translation.
    translations = fill_pair_offsets(doc, [
        {'id': b['id'], 'translation': {'text': b['text'], 'whole': True}} for b in doc['blocks']
    ])
    doc = send(doc, 'translate', blocks=translations)
    assert any('second-pass review required' in e for e in validate(doc)['errors'])
    doc = send(doc, 'review', blocks=[{'id': b['id'], 'translation_hash': store.digest(b['translation']),
                                     'note': 'Labels checked against original image; all text reviewed.'} for b in doc['blocks']])
    doc = send(doc, 'full_review', fingerprint=fingerprint(doc), note='Authored original table and all labels checked.')
    assert validate(doc)['ok']
    changed = copy.deepcopy(doc)
    next(b for b in changed['blocks'] if b['kind'] == 'table')['table_mode'] = 'text'
    assert fingerprint(changed) != fingerprint(doc)


def test_label_source_reduction_needs_explicit_reason(study):
    rows = image_rows(study)
    next(b for b in rows if b['kind'] == 'table').pop('source_change')
    with pytest.raises(ValueError, match='source_change'):
        send(study, 'structure', blocks=rows, note='Source labels changed')
    assert store.read(study['id'])['revision'] == 0


@pytest.mark.parametrize('mode, kind, asset, text, error', [
    ('image', 'table', None, 'Item', 'source image'),
    ('image', 'table', 'empty.png', 'Item', 'source image'),
    ('image', 'table', 'table.png', '', 'source labels'),
    ('image', 'paragraph', 'table.png', 'Item', 'table_mode'),
    ('unknown', 'table', 'table.png', 'Item', 'table_mode'),
])
def test_invalid_image_table_rejected_atomically(study, mode, kind, asset, text, error):
    (store.folder(study['id'])/'empty.png').write_bytes(b'')
    rows = image_rows(study)
    rows[-1].update(kind=kind, table_mode=mode, asset=asset, text=text)
    if asset is None:
        rows[-1].pop('asset')
    with pytest.raises(ValueError, match=error):
        send(study, 'structure', blocks=rows, note='Invalid authored table')
    assert store.read(study['id'])['revision'] == 0


def test_incremental_structure_keeps_table_mode(study):
    table = next(b for b in study['blocks'] if b['kind'] == 'table')
    update = next(b for b in image_rows(study) if b['kind'] == 'table')
    doc = send(study, 'structure', keep_extracted=True, updates=[update],
               default_structure_note='Authored source checked', note='Select image table layout')
    assert next(b for b in doc['blocks'] if b['id'] == table['id'])['table_mode'] == 'image'


def test_legacy_fingerprint_stays_compatible(study):
    expected = store.digest({'blocks': [{k: b.get(k) for k in ('id','text','source_ids','translation','review','kind')}
                                        for b in study['blocks']], 'terms': study['terms'], 'issues': study['issues']})
    assert fingerprint(study) == expected
