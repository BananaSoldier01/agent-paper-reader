"""Readable labels and alias matching without rewriting document anchors."""
import copy
import json
from pathlib import Path
import subprocess

import pytest

MODULE = (Path(__file__).resolve().parents[1]/'dev/web/src/readingtext.mjs').as_uri()


def call(name, *args):
    script = (f'import {{{name}}} from {json.dumps(MODULE)};'
              f'process.stdout.write(JSON.stringify({name}(...{json.dumps(args)})));')
    return json.loads(subprocess.run(['node', '--input-type=module', '-e', script],
                                    capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize('text, term, expected', [
    ('A feed-forward network follows attention.', 'feed-forward network / FFN', True),
    ('The FFN is applied twice.', 'feed-forward network / FFN', True),
    ('A feed–forward network.', 'feed-forward network / FFN', True),
    ('ＦＦＮ is the abbreviation.', 'feed-forward network / FFN', True),
    ('The FFNN differs.', 'feed-forward network / FFN', False),
    ('A forward network alone.', 'feed-forward network / FFN', False),
    ('Compute the signal/noise ratio.', 'signal/noise ratio', True),
    ('Noise ratio alone.', 'signal/noise ratio', False),
    ('Use input/output.', 'input/output', True),
    ('Use output.', 'input/output', False),
    ('Choose a key.', 'query / key / value', True),
    ('Any paragraph.', ' / ', False),
])
def test_aliases_preserve_word_boundaries_and_lexical_slashes(text, term, expected):
    assert call('termMatches', text, term) is expected


def labels(rows):
    source = target = ''
    pairs = []
    for i, (en, zh) in enumerate(rows):
        pairs.append({'id': f'g{i}', 'source': [[len(source), len(source)+len(en)]],
                      'target': [[len(target), len(target)+len(zh)]]})
        source += en+'\n'
        target += zh+'\n'
    return {'id': 'b1', 'text': source, 'translation': {'text': target, 'pairs': pairs},
            'user_edited': False}


def test_only_identical_label_and_meaning_pairs_collapse_in_source_order():
    block = labels([('WSJ only', '仅 WSJ'), ('WSJ only', '仅 WSJ'),
                    ('WSJ only', '另一种含义'), ('N', '层数'), ('n', '长度'),
                    ('PPL (dev)', 'PPL (dev)')])
    block['translation']['pairs'].reverse()
    before = copy.deepcopy(block)
    assert [p['id'] for p in call('tableLabelPairs', block, [])] == ['g0', 'g2', 'g3', 'g4', 'g5']
    assert block == before


@pytest.mark.parametrize('side', ['source', 'target'])
def test_duplicate_with_valid_unicode_note_keeps_its_original_anchor(side):
    block = labels([('😀 WSJ', '😀 仅 WSJ'), ('😀 WSJ', '😀 仅 WSJ'), ('😀 WSJ', '😀 仅 WSJ')])
    start, end = block['translation']['pairs'][1][side][0]
    text = block['text'] if side == 'source' else block['translation']['text']
    note = {'block_id': 'b1', 'side': side, 'start': start, 'end': end, 'quote': text[start:end]}
    assert [p['id'] for p in call('tableLabelPairs', block, [note])] == ['g0', 'g1']
    assert [p['id'] for p in call('tableLabelPairs', block, [dict(note, status='orphaned')])] == ['g0']
    assert [p['id'] for p in call('tableLabelPairs', block, [dict(note, quote='old quote')])] == ['g0']


def test_user_edited_table_preserves_every_occurrence():
    block = labels([('WSJ only', '仅 WSJ'), ('WSJ only', '仅 WSJ')])
    block['user_edited'] = True
    assert [p['id'] for p in call('tableLabelPairs', block, [])] == ['g0', 'g1']


def test_empty_translation_does_not_invent_labels():
    assert call('tableLabelPairs', {'id': 'b1', 'text': 'WSJ', 'translation': None,
                                   'user_edited': False}, []) == []
