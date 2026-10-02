import copy
import re
from collections import Counter
from .store import mutate, digest, folder

KINDS = {'paragraph','heading','caption','figure','table','formula','reference','code','page','excluded','unclassified'}
VERBATIM = {'figure','formula','code','page','excluded'}


def fingerprint(doc):
    return digest({'blocks':[{k:b.get(k) for k in ('id','text','source_ids','translation','review','kind')} for b in doc['blocks']], 'terms':doc['terms'], 'issues':doc['issues']})


def spans_valid(text, spans):
    cursor = 0
    for start, end in sorted(spans):
        if not (isinstance(start,int) and isinstance(end,int) and cursor <= start < end <= len(text)):
            return False
        if text[cursor:start].strip():
            return False
        cursor = end
    return not text[cursor:].strip()


def check_translation(block, translation):
    if not isinstance(translation, dict) or not isinstance(translation.get('text'),str) or not translation['text'].strip():
        raise ValueError('Translation text required')
    pairs = translation.get('pairs', [])
    if not pairs or len({p['id'] for p in pairs}) != len(pairs):
        raise ValueError('Unique semantic alignment groups required')
    for side, text in [('source',block['text']),('target',translation['text'])]:
        spans = [s for pair in pairs for s in pair.get(side, [])]
        if not spans_valid(text, spans):
            raise ValueError(f'{block["id"]}: {side} alignment must cover all non-whitespace exactly once')
    if any(not p.get('source') or not p.get('target') for p in pairs):
        raise ValueError('Every alignment group needs both sides')


def differences(block):
    if not block.get('translation'):
        return []
    en, zh = block['text'], block['translation']['text']
    patterns = {'numbers': r'(?<!\w)[−-]?\d+(?:[.,]\d+)*(?:[eE][+−-]?\d+)?',
                'units': r'(?<![A-Za-z])(?:MPa|kPa|Pa|kW|MW|W|kg|mg|mm|cm|km|mL|m/s|Hz|GHz|MHz|K|°C|%)(?![A-Za-z])',
                'formulas': r'\$\$[\s\S]*?\$\$|(?<!\$)\$[^$\n]+\$|\{\{formula:[^}]+\}\}',
                'citations': r'\[\d+(?:\s*[-–,]\s*\d+)*\]'}
    return [key for key, pattern in patterns.items() if Counter(re.findall(pattern,en)) != Counter(re.findall(pattern,zh))]


def validate(doc):
    errors, warnings = [], []
    atoms = {a['id'] for a in doc['atoms']}
    used = [a for b in doc['blocks'] for a in b['source_ids']]
    if set(used) != atoms or len(used) != len(set(used)):
        errors.append('Source ledger is not an exact partition')
    if len({b['id'] for b in doc['blocks']}) != len(doc['blocks']):
        errors.append('Duplicate block ids')
    if not doc['structure_review']:
        errors.append('Structure not reviewed')
    if not doc.get('terms_review') and not doc['terms']:
        errors.append('Glossary decision required (empty glossary allowed with explicit submission)')
    for issue in doc['issues']:
        if not issue.get('resolution'):
            errors.append(f"Unresolved extraction issue: {issue['id']}")
    limitations = confirmed_limitations(doc)
    for item in limitations:
        warnings.append(
            'Confirmed known limitation (not actually fixed): '
            f"{item['id']} [{item['category']}] {_short_resolution(item['resolution'])}"
        )
    translated_texts = Counter(b['translation']['text'].strip() for b in doc['blocks'] if b.get('translation'))
    for b in doc['blocks']:
        if b['kind'] in VERBATIM:
            if not b.get('structure_note'):
                errors.append(f"{b['id']}: visual/verbatim/excluded content needs disposition reason")
            continue
        if not b.get('translation'):
            errors.append(f"{b['id']}: missing translation")
            continue
        try:
            check_translation(b,b['translation'])
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
        if translated_texts[b['translation']['text'].strip()] > 1 and not (b.get('review') or {}).get('duplicate_explanation'):
            errors.append(f"{b['id']}: repeated translation requires duplicate_explanation")
        diff = differences(b)
        if diff and not (b.get('review') or {}).get('difference_explanation'):
            errors.append(f"{b['id']}: unexplained differences {diff}")
        if not b.get('review') or b['review'].get('translation_hash') != digest(b['translation']):
            errors.append(f"{b['id']}: second-pass review required")
    if not doc.get('full_review') or doc['full_review'].get('fingerprint') != fingerprint(doc):
        errors.append('Current whole-document review required')
    if digest((folder(doc['id']) / doc['source_file']).read_bytes()) != doc['source_sha256']:
        errors.append('Immutable source hash mismatch')
    for note in doc['notes']:
        if note_status(doc,note) == 'orphaned':
            warnings.append(f"Note {note['id']} needs reattachment")
    return {'ok':not errors,'errors':errors,'warnings':warnings,'blocks':len(doc['blocks']),
            'translated':sum(bool(b['translation']) for b in doc['blocks']),
            'reviewed':sum(bool(b['review']) for b in doc['blocks']),
            'confirmed_limitations':limitations}


def note_status(doc,note):
    b = next((b for b in doc['blocks'] if b['id']==note['block_id']),None)
    if not b:
        return 'orphaned'
    value = b['text'] if note['side']=='source' else (b.get('translation') or {}).get('text','')
    return 'attached' if value[note['start']:note['end']] == note['quote'] else 'orphaned'


def _stage_and_pending(doc):
    stage = 'structure' if not doc['structure_review'] else 'terms' if not doc.get('terms_review') and not doc['terms'] else 'translate'
    pending = [b for b in doc['blocks'] if b['kind'] not in VERBATIM and not b['translation']]
    if stage not in ('structure','terms') and not pending:
        stage = 'review'
        pending = [b for b in doc['blocks'] if b['kind'] not in VERBATIM and (not b['review'] or b['review']['translation_hash']!=digest(b['translation']))]
        if not pending:
            stage = 'full_review' if not validate(doc)['ok'] else 'complete'
    return stage, pending


def _issue_prefix(issue):
    kind = issue.get('type')
    if isinstance(kind, str) and kind.strip():
        return kind.strip()
    iid = str(issue.get('id') or 'issue')
    return re.sub(r'-(?:\d+|[ab]\d+)$', '', iid) or iid


def _short_resolution(text, limit=120):
    compact = ' '.join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + '…'


def confirmed_limitations(doc):
    rows = []
    for issue in doc.get('issues') or []:
        if issue.get('resolved_by') != 'limitation_batch':
            continue
        resolution = issue.get('resolution')
        if not isinstance(resolution, str) or not resolution.strip():
            continue
        evidence = issue.get('resolution_evidence')
        rows.append({
            'id': issue.get('id'),
            'category': _issue_prefix(issue),
            'resolution': resolution,
            'resolution_evidence': evidence if isinstance(evidence, str) else '',
        })
    return rows


LIMITATION_CATEGORIES = {
    'tex-includegraphics', 'tex-input', 'tex-env', 'tex-unclosed',
    'image', 'docx-ole', 'docx-omath', 'docx-image', 'docx-crop',
}
_LIMITATION_PER_ID_ONLY = {'page', 'docx-sym'}
_WEAK_FILLER_EXACT = {
    'ignore', 'skip', 'n/a', 'na', 'ok', 'done', 'resolved', 'limitation',
    '已知限制', '忽略', '跳过',
}
_WEAK_EN_TOKENS = {
    'ignore', 'skip', 'na', 'ok', 'done', 'resolved', 'limitation', 'limitations', 'known',
}
_WEAK_PHRASES = ('known limitation', 'known-limitation', 'n/a', '已知限制', '忽略', '跳过')
_EVIDENCE_HINTS = (
    'sample', 'checked', 'inspected', 'confirmed', 'source', 'file', 'missing',
    'remote', 'not embedded', 'single-file', 'no compile', '核对', '确认', '源', '缺',
)
_ISSUE_ID_LIKE = re.compile(r'(?:[a-z][a-z0-9]*)(?:-[a-z][a-z0-9]*)*-(?:\d+|[ab]\d+)', re.I)
_LIMITATION_MIN_LEN = 24


def _is_weak_filler(text):
    folded = text.strip().casefold()
    if folded in _WEAK_FILLER_EXACT:
        return True
    tmp = folded
    for phrase in _WEAK_PHRASES:
        tmp = tmp.replace(phrase, ' ')
    leftover_en = [tok for tok in re.findall(r'[a-z0-9]+', tmp) if tok not in _WEAK_EN_TOKENS]
    leftover_cjk = ''.join(ch for ch in tmp if '\u4e00' <= ch <= '\u9fff')
    return len(''.join(leftover_en) + leftover_cjk) < 8


def _has_concrete_evidence(text):
    folded = text.casefold()
    if re.search(r'\d', folded) or _ISSUE_ID_LIKE.search(folded):
        return True
    return any(hint.casefold() in folded for hint in _EVIDENCE_HINTS)


def _limitation_text(value, field):
    if not isinstance(value, str):
        raise ValueError(f'limitation {field} must be a string')
    text = value.strip()
    if len(text) < _LIMITATION_MIN_LEN:
        raise ValueError(f'limitation {field} must be at least {_LIMITATION_MIN_LEN} characters')
    if _is_weak_filler(text):
        raise ValueError(f'limitation {field} is too weak; write a concrete known-limitation explanation')
    return text


def _limitation_category(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('limitation category is required')
    category = value.strip()
    if category in _LIMITATION_PER_ID_ONLY:
        raise ValueError(
            f'Limitation category {category} cannot be batch-resolved; '
            'unreadable or garbled body text needs per-id issues or must stay unresolved'
        )
    if category not in LIMITATION_CATEGORIES:
        raise ValueError(f'Unknown or disallowed limitation category: {category}')
    return category


def _resolve_limitations(doc, limitations):
    if not isinstance(limitations, list):
        raise ValueError('limitations must be a list')
    prepared, seen = [], set()
    for entry in limitations:
        if not isinstance(entry, dict):
            raise ValueError('each limitations entry must be an object')
        category = _limitation_category(entry.get('category'))
        if category in seen:
            raise ValueError(f'Duplicate limitation category: {category}')
        seen.add(category)
        resolution = _limitation_text(entry.get('resolution'), 'resolution')
        evidence = _limitation_text(entry.get('evidence'), 'evidence')
        if not _has_concrete_evidence(evidence):
            raise ValueError(
                'limitation evidence must cite a sample id, a digit, or a concrete check '
                '(sample/checked/inspected/confirmed/source/file/missing/remote/核对/确认/源/缺)'
            )
        matches = [issue for issue in doc.get('issues') or []
                   if not issue.get('resolution') and _issue_prefix(issue) == category]
        if not matches:
            raise ValueError(f'No unresolved issues match limitation category: {category}')
        prepared.append((matches, resolution, evidence))
    for matches, resolution, evidence in prepared:
        for issue in matches:
            issue['resolution'] = resolution
            issue['resolution_evidence'] = evidence
            issue['resolved_by'] = 'limitation_batch'


def _resolve_extraction_issues(doc, payload, op):
    issues = payload.get('issues')
    limitations = payload.get('limitations')
    if op == 'resolve_limitations':
        if not limitations:
            raise ValueError('resolve_limitations requires a non-empty limitations list')
        _resolve_limitations(doc, limitations)
        return
    has_issues = isinstance(issues, list) and len(issues) > 0
    has_limitations = isinstance(limitations, list) and len(limitations) > 0
    if limitations is not None and not isinstance(limitations, list):
        raise ValueError('limitations must be a list')
    if not has_issues and not has_limitations:
        raise ValueError('resolve requires issues and/or limitations')
    if has_limitations:
        _resolve_limitations(doc, limitations)
    if has_issues:
        for item in issues:
            if not item['resolution'].strip():
                raise ValueError('Extraction issue requires explicit resolution/limitation')
            target = next(i for i in doc['issues'] if i['id'] == item['id'])
            target['resolution'] = item['resolution']
            # Per-id text replaces any earlier batch mark in this same submit.
            target.pop('resolution_evidence', None)
            target.pop('resolved_by', None)


def issue_summary(doc, unresolved_only=False, samples=3):
    grouped = {}
    for issue in doc.get('issues') or []:
        resolved = bool(issue.get('resolution'))
        if unresolved_only and resolved:
            continue
        bucket = grouped.setdefault(_issue_prefix(issue), {'unresolved': 0, 'resolved': 0, 'sample_ids': []})
        if resolved:
            bucket['resolved'] += 1
        else:
            bucket['unresolved'] += 1
            if len(bucket['sample_ids']) < samples:
                bucket['sample_ids'].append(issue['id'])
    if not unresolved_only:
        for issue in doc.get('issues') or []:
            if not issue.get('resolution'):
                continue
            bucket = grouped[_issue_prefix(issue)]
            if len(bucket['sample_ids']) < samples and issue['id'] not in bucket['sample_ids']:
                bucket['sample_ids'].append(issue['id'])
    unresolved = sum(bucket['unresolved'] for bucket in grouped.values())
    if unresolved_only:
        by_type = {key: {'unresolved': bucket['unresolved'], 'sample_ids': bucket['sample_ids']} for key, bucket in grouped.items()}
        return {'unresolved': unresolved, 'by_type': by_type}
    return {'unresolved': unresolved, 'resolved': sum(bucket['resolved'] for bucket in grouped.values()), 'by_type': grouped}


def _block_summary(block, width=160):
    text = block.get('text') or ''
    return {'id': block['id'], 'kind': block['kind'], 'text': text[:width],
            'has_structure_note': bool(str(block.get('structure_note') or '').strip()),
            'source_count': len(block.get('source_ids') or [])}


def _public_block(block):
    shown = dict(block)
    shown['translation_hash'] = digest(block['translation']) if block.get('translation') else None
    return shown


def _outline(doc, width=160):
    rows = []
    for block in doc['blocks']:
        if block['kind'] == 'heading':
            text = block.get('text') or ''
            rows.append({'id': block['id'], 'text': text if width is None else text[:width]})
    return rows


def _projection_meta(doc, stage):
    return {'document_id': doc['id'], 'id': doc['id'], 'revision': doc['revision'], 'stage': stage,
            'title': doc.get('title'), 'fingerprint': fingerprint(doc), 'projection': True,
            'block_count': len(doc['blocks']), 'atom_count': len(doc['atoms']),
            'source_file': doc.get('source_file'), 'schema_version': doc.get('schema_version')}


DEFAULT_SECTION_CONTEXT_LIMIT = 24


def _window(doc, chosen, *, section_limit=None, section_offset=None):
    blocks = doc['blocks']
    indices = [blocks.index(b) for b in chosen]
    # Neighbors of each chosen block (±1), not the contiguous span from first to last.
    context = []
    if indices:
        seen = set()
        for index in sorted(set(indices)):
            for pos in range(max(0, index - 1), min(len(blocks), index + 2)):
                if pos not in seen:
                    seen.add(pos)
                    context.append(blocks[pos])
    # Include enclosing section, then optionally window it for default projections.
    section = []
    start = end = 0
    if indices:
        start = min(indices)
        while start > 0 and doc['blocks'][start]['kind'] != 'heading':
            start -= 1
        end = max(indices)+1
        while end < len(doc['blocks']) and doc['blocks'][end]['kind'] != 'heading':
            end += 1
        section = doc['blocks'][start:end]
    total = len(section)
    limit = None if section_limit is None else max(1, int(section_limit))
    if limit is None or total <= limit:
        offset = 0
        returned = section
        truncated = False
    else:
        if section_offset is None:
            first = (min(indices) - start) if indices else 0
            offset = max(0, min(first, total - limit))
        else:
            offset = max(0, min(int(section_offset), max(0, total - limit)))
        returned = section[offset:offset + limit]
        truncated = True
    window = {
        'section_start': start,
        'section_end': end,
        'section_total': total,
        'section_offset': offset,
        'section_limit': limit if limit is not None else total,
        'returned_start': start + offset,
        'returned_end': start + offset + len(returned),
        'truncated': truncated,
    }
    return context, returned, window


def _tasks_view(doc, limit=8, full=False, stage=None, section_limit=None, section_offset=None):
    computed, pending = _stage_and_pending(doc)
    stage = computed if full else (stage or computed)
    chosen = (doc['blocks'] if stage == 'structure' else pending)[:limit]
    if full:
        context, section, window = _window(doc, chosen, section_limit=None, section_offset=None)
        return {'document_id': doc['id'], 'revision': doc['revision'], 'stage': computed, 'terms': doc['terms'],
                'outline': [{'id': b['id'], 'text': b['text']} for b in doc['blocks'] if b['kind'] == 'heading'],
                'blocks': chosen, 'context': context, 'section_context': section, 'issues': doc['issues'],
                'section_window': window}
    cap = DEFAULT_SECTION_CONTEXT_LIMIT if section_limit is None else section_limit
    context, section, window = _window(doc, chosen, section_limit=cap, section_offset=section_offset)
    meta = _projection_meta(doc, stage)
    if stage == 'structure':
        return {**meta, 'terms': doc['terms'], 'outline': _outline(doc), 'issue_summary': issue_summary(doc),
                'blocks': [_block_summary(b) for b in chosen], 'context': [_block_summary(b) for b in context],
                'section_context': [_block_summary(b) for b in section], 'section_window': window}
    return {**meta, 'terms': doc['terms'], 'outline': _outline(doc),
            'issue_summary': issue_summary(doc, unresolved_only=True),
            'blocks': [_public_block(b) for b in chosen], 'context': [_public_block(b) for b in context],
            'section_context': [_public_block(b) for b in section], 'section_window': window}


def tasks(doc, limit=8, full=False, section_limit=None, section_offset=None):
    return _tasks_view(doc, limit, full=full, section_limit=section_limit, section_offset=section_offset)


def _show_full(doc):
    result = copy.deepcopy(doc)
    result['fingerprint'] = fingerprint(doc)
    for block in result['blocks']:
        block['translation_hash'] = digest(block['translation']) if block['translation'] else None
    return result


def _show_view(doc, full=False, stage=None, limit=8, section_limit=None, section_offset=None):
    if full:
        return _show_full(doc)
    computed, _pending = _stage_and_pending(doc)
    stage = stage or computed
    if stage == 'structure':
        return {**_projection_meta(doc, stage), 'outline': _outline(doc), 'issue_summary': issue_summary(doc),
                'blocks': [_block_summary(b) for b in doc['blocks']]}
    return _tasks_view(doc, limit, full=False, stage=stage, section_limit=section_limit, section_offset=section_offset)


def _progress_view(doc, full=False):
    summary = validate(doc)
    if full:
        return {'document_id': doc['id'], 'revision': doc['revision'], 'stage': doc['stage'],
                'fingerprint': fingerprint(doc), **summary}
    stage, _pending = _stage_and_pending(doc)
    unresolved = sum(1 for issue in doc.get('issues') or [] if not issue.get('resolution'))
    return {**_projection_meta(doc, stage),
            'outline_length': sum(block['kind'] == 'heading' for block in doc['blocks']),
            'unresolved_issues': unresolved, **summary}


def project_document(doc, *, full=False, stage=None, view='show', limit=8, section_limit=None, section_offset=None):
    if view == 'progress':
        return _progress_view(doc, full=full)
    if view == 'tasks':
        return _tasks_view(doc, limit, full=full, stage=stage, section_limit=section_limit, section_offset=section_offset)
    if view == 'show':
        return _show_view(doc, full=full, stage=stage, limit=limit, section_limit=section_limit, section_offset=section_offset)
    raise ValueError('Unknown projection view')


def _canonical(value):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def _assign_asset(doc_id, block, asset):
    # A local asset is a non-empty filename with no path separators, already in the doc folder.
    if not isinstance(asset, str) or not asset or '/' in asset or '\\' in asset or not (folder(doc_id) / asset).is_file():
        raise ValueError('Unknown local asset')
    block['asset'] = asset


def _apply_optional_asset(doc_id, block, asset):
    # Full-block and update payloads repeat asset: null for text. That is not an assignment
    # and must not clear a file that is already there.
    if not asset:
        if block.get('asset'):
            raise ValueError('Unknown local asset')
        return
    _assign_asset(doc_id, block, asset)


def _source_text(doc, source_ids):
    return ' '.join(atom['text'] for sid in source_ids for atom in doc['atoms'] if atom['id'] == sid)


def _require_source_change(doc, block):
    if block['kind'] in VERBATIM:
        return
    if _canonical(_source_text(doc, block['source_ids'])) != _canonical(block.get('text') or '') and not str(block.get('source_change') or '').strip():
        raise ValueError('Changed source text requires explicit source_change explanation')


def _commit_structure(doc, blocks, payload):
    sources = [sid for block in blocks for sid in block['source_ids']]
    if set(sources) != {atom['id'] for atom in doc['atoms']} or len(sources) != len(set(sources)):
        raise ValueError('Structure must account for every source atom exactly once (including excluded items)')
    if len({block['id'] for block in blocks}) != len(blocks):
        raise ValueError('Duplicate block ids')
    doc['blocks'] = blocks
    doc['structure_review'] = {'agent': payload['agent'], 'note': payload['note']}


def _structure_replace(doc, doc_id, payload):
    old = {b['id']: b for b in doc['blocks']}
    blocks = []
    for item in payload['blocks']:
        if item['kind'] not in KINDS:
            raise ValueError('Unknown content kind')
        block = dict(old.get(item['id'], {'translation': None, 'history': [], 'review': None, 'user_edited': False, 'asset': None}))
        block.update({k: item[k] for k in ('id', 'kind', 'text', 'source_ids', 'structure_note')})
        if 'asset' in item:
            _apply_optional_asset(doc_id, block, item['asset'])
        if not block['source_ids'] or not block['structure_note'].strip():
            raise ValueError('Each structural decision requires source ids and a reason')
        if block['kind'] not in VERBATIM:
            if _canonical(_source_text(doc, block['source_ids'])) != _canonical(block['text']) and not item.get('source_change', '').strip():
                raise ValueError('Changed source text requires explicit source_change explanation')
            block['source_change'] = item.get('source_change', '')
        blocks.append(block)
    _commit_structure(doc, blocks, payload)


def _merge_assets(doc_id, into, sources, merge):
    # Refuse silent figure loss: multiple distinct assets need an explicit merged asset.
    assets = []
    for block in [into, *sources]:
        asset = block.get('asset')
        if asset and asset not in assets:
            assets.append(asset)
    if 'asset' in merge:
        # The key alone is not an explicit choice. null, "", and false are rejected.
        _assign_asset(doc_id, into, merge['asset'])
        return
    if len(assets) > 1:
        raise ValueError('Merge would discard figure assets; provide explicit asset for the merged block')
    if len(assets) == 1:
        into['asset'] = assets[0]


def _structure_keep(doc, doc_id, payload):
    # Start from the extracted ledger. Explicit update/merge notes beat default_structure_note.
    blocks = [dict(block, source_ids=list(block['source_ids'])) for block in doc['blocks']]
    by_id = {block['id']: block for block in blocks}
    updates = payload.get('updates') or []
    merges = payload.get('merges') or []
    if not isinstance(updates, list) or not isinstance(merges, list):
        raise ValueError('updates and merges must be lists')
    for item in updates:
        block = by_id.get(item.get('id'))
        if not block:
            raise ValueError(f"Unknown block {item.get('id')}")
        if 'kind' in item:
            if item['kind'] not in KINDS:
                raise ValueError('Unknown content kind')
            block['kind'] = item['kind']
        touched_source = False
        if 'text' in item:
            block['text'] = item['text']
            touched_source = True
        if 'source_ids' in item:
            block['source_ids'] = list(item['source_ids'])
            touched_source = True
        if 'structure_note' in item:
            block['structure_note'] = item['structure_note']
        if 'source_change' in item:
            block['source_change'] = item['source_change']
        elif touched_source:
            # Incremental text/source edits need a fresh reason; do not reuse a stale one.
            block['source_change'] = ''
        if 'asset' in item:
            _apply_optional_asset(doc_id, block, item['asset'])
    for merge in merges:
        into = by_id.get(merge.get('into'))
        if not into:
            raise ValueError(f"Unknown block {merge.get('into')}")
        note = merge.get('structure_note')
        if not isinstance(note, str) or not note.strip():
            raise ValueError('Merge requires structure_note')
        from_ids = merge.get('from') or []
        if not isinstance(from_ids, list) or not from_ids or len(from_ids) != len(set(from_ids)) or into['id'] in from_ids:
            raise ValueError('Merge from must list other blocks once')
        gained = []
        sources = []
        for fid in from_ids:
            src = by_id.get(fid)
            if not src:
                raise ValueError(f"Unknown block {fid}")
            sources.append(src)
            gained.extend(src['source_ids'])
        _merge_assets(doc_id, into, sources, merge)
        into['source_ids'] = list(into['source_ids']) + gained
        if 'text' in merge:
            into['text'] = merge['text']
        if 'kind' in merge:
            if merge['kind'] not in KINDS:
                raise ValueError('Unknown content kind')
            into['kind'] = merge['kind']
        if 'source_change' in merge:
            into['source_change'] = merge['source_change']
        else:
            # Merges always change source_ids; stale reasons must not cover the new partition.
            into['source_change'] = ''
        into['structure_note'] = note
        drop = set(from_ids)
        blocks = [block for block in blocks if block['id'] not in drop]
        by_id = {block['id']: block for block in blocks}
    default_note = payload.get('default_structure_note')
    if default_note is not None:
        if not isinstance(default_note, str) or not default_note.strip():
            raise ValueError('default_structure_note must be a non-empty string')
        for block in blocks:
            if not str(block.get('structure_note') or '').strip():
                block['structure_note'] = default_note
    for block in blocks:
        if block.get('kind') not in KINDS:
            raise ValueError('Unknown content kind')
        if not block.get('source_ids') or not str(block.get('structure_note') or '').strip():
            raise ValueError('Each structural decision requires source ids and a reason')
    sources = [sid for block in blocks for sid in block['source_ids']]
    if set(sources) != {atom['id'] for atom in doc['atoms']} or len(sources) != len(set(sources)):
        raise ValueError('Structure must account for every source atom exactly once (including excluded items)')
    for block in blocks:
        _require_source_change(doc, block)
    _commit_structure(doc, blocks, payload)


def _keep_extracted(payload):
    return bool(payload.get('keep_extracted')) or payload.get('mode') in ('keep', 'patch', 'keep_extracted')


def submit(doc_id, payload):
    def apply(doc):
        op = payload['operation']
        if op == 'structure':
            if any(b['translation'] for b in doc['blocks']) or doc['notes']:
                raise ValueError('Structure is frozen after translation/annotation; start structural correction before translation')
            if 'blocks' in payload:
                _structure_replace(doc, doc_id, payload)
            elif _keep_extracted(payload):
                _structure_keep(doc, doc_id, payload)
            else:
                raise ValueError('Structure requires a complete blocks list or keep_extracted')
        elif op == 'terms':
            if not doc['structure_review']:
                raise ValueError('Review structure first')
            terms = payload['terms']
            if len({t['id'] for t in terms}) != len(terms) or any(not all(isinstance(t.get(k),str) and t[k].strip() for k in ('id','en','zh','definition')) for t in terms):
                raise ValueError('Terms require unique ids, en, zh, definition')
            if any(t.get('user_edited') for t in doc['terms']):
                raise ConflictError('User-edited terms must be preserved via UI')
            doc['terms'] = terms
            doc['terms_review'] = {'agent':payload['agent'],'note':payload.get('note','Glossary established from full document context')}
        elif op in ('translate','review'):
            if not doc.get('terms_review') and not doc['terms']:
                raise ValueError('Submit glossary before translation (empty list permitted)')
            if not doc['structure_review']:
                raise ValueError('Review structure first')
            seen = set()
            for item in payload['blocks']:
                if item['id'] in seen:
                    raise ValueError('Duplicate block submission')
                seen.add(item['id'])
                b = next(b for b in doc['blocks'] if b['id']==item['id'])
                if op=='translate':
                    if b['user_edited']:
                        raise ValueError(f"{b['id']}: user-edited translation is protected")
                    check_translation(b,item['translation'])
                    if b['translation']:
                        b['history'].append({'translation':b['translation'],'author':'agent'})
                    b['translation'] = item['translation']
                    b['review'] = None
                else:
                    if not b['translation'] or item['translation_hash'] != digest(b['translation']):
                        raise ValueError('Review must target the current translation hash')
                    if not item.get('note','').strip():
                        raise ValueError('Review evidence note required')
                    b['review'] = {'agent':payload['agent'],'translation_hash':item['translation_hash'],
                                   'note':item['note'],'difference_explanation':item.get('difference_explanation',''),'duplicate_explanation':item.get('duplicate_explanation','')}
        elif op=='attach_asset':
            # Attach a missing visual without changing frozen text or annotation anchors.
            b = next(b for b in doc['blocks'] if b['id']==payload['block_id'])
            asset = payload.get('asset')
            if b['kind'] not in {'caption','figure','formula'} or b.get('asset'):
                raise ValueError('Only a missing visual on a caption/figure/formula may be attached')
            if not isinstance(asset,str) or not re.fullmatch(r'region-[a-f0-9]{16}\.png',asset) or not (folder(doc_id)/asset).is_file():
                raise ValueError('Existing local crop asset required')
            if not payload.get('note','').strip():
                raise ValueError('Visual attachment evidence required')
            b['asset'] = asset
            b['structure_note'] += '\n' + payload['note']
            doc['full_review'] = None
        elif op in ('resolve', 'resolve_limitations'):
            _resolve_extraction_issues(doc, payload, op)
        elif op=='full_review':
            if payload['fingerprint'] != fingerprint(doc):
                raise ValueError('Whole review fingerprint stale')
            if not payload.get('note','').strip():
                raise ValueError('Full-review evidence required')
            doc['full_review'] = {'fingerprint':fingerprint(doc),'agent':payload['agent'],'note':payload['note']}
            result = validate(doc)
            if not result['ok']:
                raise ValueError(result['errors'])
        else:
            raise ValueError('Unknown operation')
        doc['stage'] = 'complete' if validate(doc)['ok'] else tasks(doc,1)['stage']
    return mutate(doc_id,payload['revision'],apply,payload['submission_id'],payload)

# Keep all validation errors on the structured ValueError path.
ConflictError = ValueError


def user_edit(doc_id, payload):
    def apply(doc):
        op = payload['operation']
        if op=='reading':
            doc['reading'] = payload['reading']
        elif op=='translation':
            b = next(b for b in doc['blocks'] if b['id']==payload['block_id'])
            if not b['translation']:
                raise ValueError('Translate with Agent first')
            # Edit one semantic group; preserve many-to-many mapping and all other groups.
            pair = next(p for p in b['translation']['pairs'] if p['id']==payload['pair_id'])
            span_index = payload.get('span_index',0)
            if not isinstance(span_index,int) or not 0 <= span_index < len(pair['target']):
                raise ValueError('Invalid target span')
            start,end = pair['target'][span_index]
            new = payload['text']
            if not new.strip():
                raise ValueError('Translation cannot be empty')
            import copy
            previous = copy.deepcopy(b['translation'])
            b['history'].append({'translation':previous,'author':'user','revision':doc['revision']})
            b['translation']['text']=previous['text'][:start]+new+previous['text'][end:]
            delta=len(new)-(end-start)
            for p in b['translation']['pairs']:
                p['target'] = [[s+(delta if s>=end else 0),e+(delta if s>=end else 0)] if not (p['id']==pair['id'] and i==span_index) else [start,start+len(new)] for i,(s,e) in enumerate(p['target'])]
            check_translation(b,b['translation'])
            b['user_edited']=True
            b['review']=None
        elif op=='note':
            note = payload['note']
            if note['side'] not in ('source','target') or not (0 <= note['start'] < note['end']):
                raise ValueError('Invalid annotation anchor')
            if note_status(doc,note)!='attached':
                raise ValueError('Selection changed; reselect text to attach note')
            doc['notes']=[n for n in doc['notes'] if n['id']!=note['id']]+[note]
        elif op=='delete_note':
            doc['notes']=[n for n in doc['notes'] if n['id']!=payload['note_id']]
        elif op=='term':
            t=next(t for t in doc['terms'] if t['id']==payload['term_id'])
            t.update({'zh':payload['zh'],'definition':payload['definition'],'user_edited':True})
        else:
            raise ValueError('Unknown edit operation')
        if op not in ('reading','note','delete_note'):
            doc['stage']='review'
    return mutate(doc_id,payload['revision'],apply)
