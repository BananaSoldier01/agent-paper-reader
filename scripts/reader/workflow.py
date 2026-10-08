import copy
import re
from collections import Counter
from .store import mutate, digest, folder
from .regions import crop_coverage_error

KINDS = {'paragraph','metadata','footnote','heading','caption','figure','table','formula','reference','code','page','excluded','unclassified'}
VERBATIM = {'figure','formula','code','page','excluded'}


def needs_translation(block):
    # References retain their bibliographic source by default. Existing/explicit
    # translations still pass the same alignment and review gates as before.
    return block['kind'] not in VERBATIM and (
        block['kind'] != 'reference' or block.get('translation') is not None)


def fingerprint(doc):
    return digest({'blocks':[{**{k:b.get(k) for k in ('id','text','source_ids','translation','review','kind')},
                             **({'table_mode':b['table_mode']} if 'table_mode' in b else {})}
                            for b in doc['blocks']], 'terms':doc['terms'], 'issues':doc['issues']})


def visual_content_error(doc, block):
    mode = block.get('table_mode')
    if mode is not None:
        if block.get('kind') != 'table' or mode not in ('text', 'image'):
            return f'{block["id"]}: table_mode must be text/image on a table block'
        if mode == 'image':
            asset = block.get('asset')
            if (not isinstance(asset, str) or not asset or '/' in asset or '\\' in asset or
                    not (folder(doc['id']) / asset).is_file() or
                    (folder(doc['id']) / asset).stat().st_size == 0):
                return f'{block["id"]}: image table needs a non-empty verified source image'
            if not (block.get('text') or '').strip():
                return f'{block["id"]}: image table needs source labels for translation'
    if block.get('kind') == 'formula' and not block.get('asset'):
        text = block.get('text') or ''
        math = re.search(r'(?<!\\)\$\$([\s\S]+?)\$\$|(?<![\\$])\$([^\n$]+?)\$(?!\$)', text)
        if not math or not any(value and value.strip() for value in math.groups()):
            return f'{block["id"]}: formula needs $...$ or $$...$$ math formatting, or a verified source image'
    return crop_coverage_error(doc, block)


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
    if block.get('kind') == 'table':
        source_rows = [line for line in block['text'].splitlines() if '|' in line or '\t' in line]
        target_lines = [line for line in translation['text'].splitlines() if line.strip()]
        if len(source_rows) > 1 and len(target_lines) < 2:
            raise ValueError(f'{block["id"]}: table row boundaries lost; keep translated rows on separate lines')
    pairs = translation.get('pairs', [])
    if not pairs or len({p['id'] for p in pairs}) != len(pairs):
        raise ValueError('Unique semantic alignment groups required')
    for side, text in [('source',block['text']),('target',translation['text'])]:
        spans = [s for pair in pairs for s in pair.get(side, [])]
        if not spans_valid(text, spans):
            raise ValueError(f'{block["id"]}: {side} alignment must cover all non-whitespace exactly once')
    if any(not p.get('source') or not p.get('target') for p in pairs):
        raise ValueError('Every alignment group needs both sides')


def _alignment_cover_error(block_id, side, text, spans):
    # Same coverage rule as spans_valid, with overlap / out of range / uncovered.
    cursor = 0
    try:
        ordered = sorted(spans)
    except TypeError:
        return f'{block_id}: {side} out of range'
    for span in ordered:
        if not (isinstance(span, (list, tuple)) and len(span) == 2):
            return f'{block_id}: {side} out of range'
        start, end = span
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
            return f'{block_id}: {side} out of range'
        if start < cursor:
            return f'{block_id}: {side} overlap'
        gap = text[cursor:start]
        if gap.strip():
            return f'{block_id}: {side} uncovered: {gap!r}'
        cursor = end
    tail = text[cursor:]
    if tail.strip():
        return f'{block_id}: {side} uncovered: {tail!r}'
    return None


def _group_fragments(value, block_id, side):
    if isinstance(value, (str, dict)):
        fragments = [value]
    elif isinstance(value, list):
        fragments = value
    else:
        raise ValueError(f'{block_id}: {side} fragment not found: {value!r}')
    if not fragments:
        raise ValueError(f'{block_id}: {side} fragment not found: {value!r}')
    return fragments


def _fragment_spec(fragment, block_id, side):
    if isinstance(fragment, str):
        if fragment == '':
            raise ValueError(f'{block_id}: {side} fragment not found: {fragment!r}')
        return fragment, None, None
    if not isinstance(fragment, dict):
        raise ValueError(f'{block_id}: {side} fragment not found: {fragment!r}')
    needle = fragment.get('text')
    if not isinstance(needle, str) or needle == '':
        raise ValueError(f'{block_id}: {side} fragment not found: {fragment!r}')
    occurrence = fragment.get('occurrence', None)
    if occurrence is not None and (isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 1):
        raise ValueError(f'{block_id}: {side} occurrence must be a positive integer')
    anchor = fragment.get('anchor', None)
    if anchor is not None and (not isinstance(anchor, str) or anchor == '' or needle not in anchor):
        raise ValueError(f'{block_id}: {side} anchor must contain fragment: {anchor!r}')
    return needle, occurrence, anchor


def _exact_matches(text, needle):
    spans = []
    start = 0
    while True:
        pos = text.find(needle, start)
        if pos < 0:
            return spans
        spans.append((pos, pos + len(needle)))
        start = pos + 1


def _span_free(span, used):
    start, end = span
    return all(end <= used_start or start >= used_end for used_start, used_end in used)


def _locate_fragment(text, fragment, block_id, side, used):
    needle, occurrence, anchor = _fragment_spec(fragment, block_id, side)
    matches = _exact_matches(text, needle)
    if anchor is not None:
        anchors = _exact_matches(text, anchor)
        matches = [span for span in matches if any(a0 <= span[0] and span[1] <= a1 for a0, a1 in anchors)]
    if occurrence is None:
        chosen = next((span for span in matches if _span_free(span, used)), None)
        if chosen is None:
            raise ValueError(f'{block_id}: {side} fragment not found: {fragment!r}')
    else:
        if occurrence > len(matches):
            raise ValueError(f'{block_id}: {side} fragment not found: {fragment!r}')
        chosen = matches[occurrence - 1]
        if not _span_free(chosen, used):
            raise ValueError(f'{block_id}: {side} fragment already used: {fragment!r}')
    used.append(chosen)
    return [chosen[0], chosen[1]]


def _locate_fragments(text, fragments, block_id, side, used):
    return [_locate_fragment(text, fragment, block_id, side, used) for fragment in fragments]


def fill_pair_offsets(doc, items):
    if not isinstance(items, list):
        raise ValueError('blocks must be a list')
    by_id = {block['id']: block for block in doc['blocks']}
    result = []
    for item in items:
        if not isinstance(item, dict) or not item.get('id'):
            raise ValueError('each block needs an id')
        block_id = item['id']
        block = by_id.get(block_id)
        if block is None:
            raise ValueError(f'{block_id}: block not found')
        translation = item.get('translation')
        if not isinstance(translation, dict) or not isinstance(translation.get('text'), str):
            raise ValueError('Translation text required')
        src = block['text']
        tgt = translation['text']
        has_whole = translation.get('whole') is True
        groups = translation.get('groups')
        has_groups = groups is not None
        if has_whole and has_groups:
            raise ValueError(f'{block_id}: cannot combine whole and groups')
        if has_whole:
            pairs = [{'id': 'g1', 'source': [[0, len(src)]], 'target': [[0, len(tgt)]]}]
        elif isinstance(groups, list) and groups:
            pairs = []
            src_used, tgt_used = [], []
            for index, group in enumerate(groups, 1):
                if not isinstance(group, dict):
                    raise ValueError(f'{block_id}: semantic groups required; refusing to split the block automatically')
                group_id = group['id'] if group.get('id') not in (None, '') else f'g{index}'
                source_spans = _locate_fragments(
                    src, _group_fragments(group.get('source'), block_id, 'source'), block_id, 'source', src_used)
                target_spans = _locate_fragments(
                    tgt, _group_fragments(group.get('target'), block_id, 'target'), block_id, 'target', tgt_used)
                pairs.append({'id': group_id, 'source': source_spans, 'target': target_spans})
        else:
            raise ValueError(f'{block_id}: semantic groups required; refusing to split the block automatically')
        aligned = {'text': tgt, 'pairs': pairs}
        for side, text in [('source', src), ('target', tgt)]:
            spans = [span for pair in pairs for span in pair[side]]
            error = _alignment_cover_error(block_id, side, text, spans)
            if error:
                raise ValueError(error)
        check_translation(block, aligned)
        result.append({'id': block_id, 'translation': aligned})
    return result


def differences(block):
    if not block.get('translation'):
        return []
    en, zh = block['text'], block['translation']['text']
    patterns = {'numbers': r'(?<!\w)[−-]?\d+(?:[.,]\d+)*(?:[eE][+−-]?\d+)?',
                'units': r'(?<![A-Za-z])(?:MPa|kPa|Pa|kW|MW|W|kg|mg|mm|cm|km|mL|m/s|Hz|GHz|MHz|K|°C|%)(?![A-Za-z])',
                'formulas': r'\$\$[\s\S]*?\$\$|(?<!\$)\$[^$\n]+\$|\{\{formula:[^}]+\}\}',
                'citations': r'\[\d+(?:\s*[-–,]\s*\d+)*\]'}
    return [key for key, pattern in patterns.items() if Counter(re.findall(pattern,en)) != Counter(re.findall(pattern,zh))]


_CONFIRMED_LIMITATION_WARNING = 'Confirmed known limitation (not actually fixed): '


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
            f"{_CONFIRMED_LIMITATION_WARNING}{item['id']} [{item['category']}] {_short_resolution(item['resolution'])}"
        )
    translated_texts = Counter(b['translation']['text'].strip() for b in doc['blocks'] if b.get('translation'))
    for b in doc['blocks']:
        visual_error = visual_content_error(doc, b)
        if visual_error:
            errors.append(visual_error)
        if b['kind'] == 'reference' and not (b.get('text') or '').strip():
            errors.append(f"{b['id']}: preserved reference text cannot be empty")
        if not needs_translation(b):
            if not b.get('structure_note'):
                reason = 'reference content needs source-check reason' if b['kind'] == 'reference' else 'visual/verbatim/excluded content needs disposition reason'
                errors.append(f"{b['id']}: {reason}")
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
    pending = [b for b in doc['blocks'] if needs_translation(b) and not b['translation']]
    if stage not in ('structure','terms') and not pending:
        stage = 'review'
        pending = [b for b in doc['blocks'] if needs_translation(b) and (not b['review'] or b['review']['translation_hash']!=digest(b['translation']))]
        if not pending:
            stage = 'full_review' if not validate(doc)['ok'] else 'complete'
    return stage, pending


def pending_counts(doc):
    # Independent of the current stage so a partial translate still shows both queues.
    # Untranslated blocks are not also counted as pending review.
    translate = review = 0
    for block in doc['blocks']:
        if not needs_translation(block):
            continue
        if not block.get('translation'):
            translate += 1
            continue
        review_obj = block.get('review') or {}
        if review_obj.get('translation_hash') != digest(block['translation']):
            review += 1
    return {'pending_translate': translate, 'pending_review': review}


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
    # Default translate/review/terms projection. History and the long structure_note stay on disk and in --full.
    shown = {key: block.get(key) for key in ('id', 'kind', 'text', 'source_ids', 'asset', 'translation', 'review', 'user_edited')}
    if block.get('table_mode') is not None:
        shown['table_mode'] = block['table_mode']
    shown['translation_hash'] = digest(block['translation']) if block.get('translation') else None
    shown['has_structure_note'] = bool(str(block.get('structure_note') or '').strip())
    return shown


def _block_ref(block):
    return {'id': block['id'], 'ref': True}


def _deduped_rows(blocks, chosen_ids):
    return [_block_ref(block) if block['id'] in chosen_ids else _public_block(block) for block in blocks]


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


DEFAULT_TASK_LIMIT = 16
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


def _tasks_view(doc, limit=DEFAULT_TASK_LIMIT, full=False, stage=None, section_limit=None, section_offset=None):
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
    chosen_ids = {block['id'] for block in chosen}
    return {**meta, 'terms': doc['terms'], 'outline': _outline(doc),
            'issue_summary': issue_summary(doc, unresolved_only=True),
            'blocks': [_public_block(b) for b in chosen],
            'context': _deduped_rows(context, chosen_ids),
            'section_context': _deduped_rows(section, chosen_ids), 'section_window': window}


def tasks(doc, limit=DEFAULT_TASK_LIMIT, full=False, section_limit=None, section_offset=None):
    return _tasks_view(doc, limit, full=full, section_limit=section_limit, section_offset=section_offset)


def _show_full(doc):
    result = copy.deepcopy(doc)
    result['fingerprint'] = fingerprint(doc)
    for block in result['blocks']:
        block['translation_hash'] = digest(block['translation']) if block['translation'] else None
    return result


def _show_view(doc, full=False, stage=None, limit=DEFAULT_TASK_LIMIT, section_limit=None, section_offset=None):
    if full:
        return _show_full(doc)
    computed, _pending = _stage_and_pending(doc)
    stage = stage or computed
    if stage == 'structure':
        return {**_projection_meta(doc, stage), 'outline': _outline(doc), 'issue_summary': issue_summary(doc),
                'blocks': [_block_summary(b) for b in doc['blocks']]}
    return _tasks_view(doc, limit, full=False, stage=stage, section_limit=section_limit, section_offset=section_offset)


def _confirmed_limitations_summary(limitations, samples=3):
    by_category = {}
    for item in limitations or []:
        category = item.get('category') or 'issue'
        bucket = by_category.get(category)
        if bucket is None:
            bucket = {
                'count': 0,
                'sample_ids': [],
                'summary': _short_resolution(item.get('resolution') or ''),
            }
            by_category[category] = bucket
        bucket['count'] += 1
        item_id = item.get('id')
        if item_id and len(bucket['sample_ids']) < samples and item_id not in bucket['sample_ids']:
            bucket['sample_ids'].append(item_id)
    return {'total': len(limitations or []), 'by_category': by_category}


def _progress_warning_projection(warnings, summary):
    # Per-item limitation warnings repeat one resolution hundreds of times; keep other warnings intact.
    prefix = _CONFIRMED_LIMITATION_WARNING
    other = [item for item in warnings or [] if not str(item).startswith(prefix)]
    notes = []
    for category, bucket in summary['by_category'].items():
        bits = [f"{category} x{bucket['count']}"]
        if bucket['sample_ids']:
            bits.append('samples: ' + ', '.join(str(item_id) for item_id in bucket['sample_ids']))
        if bucket['summary']:
            bits.append(bucket['summary'])
        notes.append(prefix + '; '.join(bits))
    collapsed = len(warnings or []) - len(other)
    warning_summary = {
        'total': len(warnings or []),
        'confirmed_limitations': collapsed,
        'other': len(other),
        'by_category': {category: bucket['count'] for category, bucket in summary['by_category'].items()},
    }
    return other + notes, warning_summary


# Per-block pending lines repeat once per block. Projection only; validate() keeps the full list.
_REPETITIVE_PENDING_ERROR = re.compile(r'^(?P<id>\S+): (?P<detail>missing\b.*|second-pass review required)$')
_PROGRESS_ERROR_SAMPLES = 5


def _progress_error_projection(errors, samples=_PROGRESS_ERROR_SAMPLES):
    other = []
    by_kind = {}
    for message in errors or []:
        text = message if isinstance(message, str) else str(message)
        match = _REPETITIVE_PENDING_ERROR.match(text)
        if not match:
            other.append(message)
            continue
        detail = match.group('detail')
        bucket = by_kind.get(detail)
        if bucket is None:
            bucket = {'count': 0, 'sample_ids': [], 'sample_messages': []}
            by_kind[detail] = bucket
        bucket['count'] += 1
        if len(bucket['sample_ids']) < samples:
            bucket['sample_ids'].append(match.group('id'))
            bucket['sample_messages'].append(text)
    total = sum(bucket['count'] for bucket in by_kind.values())
    return other, {'total': total, 'by_kind': by_kind}


def _progress_view(doc, full=False):
    summary = validate(doc)
    if full:
        return {'document_id': doc['id'], 'revision': doc['revision'], 'stage': doc['stage'],
                'fingerprint': fingerprint(doc), **summary}
    stage, _pending = _stage_and_pending(doc)
    unresolved = sum(1 for issue in doc.get('issues') or [] if not issue.get('resolution'))
    limitation_summary = _confirmed_limitations_summary(summary.get('confirmed_limitations'))
    warnings, warning_summary = _progress_warning_projection(summary.get('warnings'), limitation_summary)
    errors, error_summary = _progress_error_projection(summary.get('errors'))
    projected = {key: value for key, value in summary.items() if key not in ('warnings', 'confirmed_limitations', 'errors')}
    return {**_projection_meta(doc, stage),
            'outline_length': sum(block['kind'] == 'heading' for block in doc['blocks']),
            'unresolved_issues': unresolved,
            **projected,
            'errors': errors,
            'error_summary': error_summary,
            **pending_counts(doc),
            'warnings': warnings,
            'warning_summary': warning_summary,
            'confirmed_limitations_summary': limitation_summary}


def project_document(doc, *, full=False, stage=None, view='show', limit=DEFAULT_TASK_LIMIT, section_limit=None, section_offset=None):
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
    for block in blocks:
        if block['kind'] == 'reference' and not (block.get('text') or '').strip():
            raise ValueError(f"{block['id']}: preserved reference text cannot be empty")
        error = visual_content_error(doc, block)
        if error:
            raise ValueError(error)
    doc['blocks'] = blocks
    doc['structure_review'] = {'agent': payload['agent'], 'note': payload['note']}


def _resolve_structure_assets(doc, doc_id, blocks, explicit):
    # Block ids can be reused for entirely different source atoms. Follow source
    # provenance instead; page placeholders belong to doc.pages, not body figures.
    source_sets = {b['id']: set(b['source_ids']) for b in blocks}
    if len(source_sets) != len(blocks):
        raise ValueError('Duplicate block ids')
    images = [(set(b['source_ids']), b['asset']) for b in doc['blocks']
              if b.get('asset') and b['kind'] != 'page']
    pages = [(set(b['source_ids']), b['asset']) for b in doc['blocks']
             if b.get('asset') and b['kind'] == 'page']

    original_names = {asset for _, asset in images}
    for old_sources, old_asset in images:
        if not any(old_sources <= new_sources for new_sources in source_sets.values()):
            affected = [bid for bid, new_sources in source_sets.items() if old_sources & new_sources]
            if not any(isinstance(explicit.get(bid), str) and explicit[bid] and
                       (explicit[bid] == old_asset or explicit[bid] not in original_names)
                       for bid in affected):
                raise ValueError('Split source image asset requires explicit non-empty asset placement')

    for block in blocks:
        new_sources = source_sets[block['id']]
        inherited = {asset for old_sources, asset in images if old_sources <= new_sources}
        if block['kind'] == 'page':
            inherited.update(asset for old_sources, asset in pages if old_sources == new_sources)
        if block['id'] in explicit:
            if explicit[block['id']]:
                _assign_asset(doc_id, block, explicit[block['id']])
            elif inherited or any(old_sources & new_sources for old_sources, _ in images):
                raise ValueError('Empty asset cannot clear an associated source image')
            else:
                block['asset'] = None
        elif len(inherited) > 1:
            raise ValueError('Multiple source image assets require an explicit non-empty asset')
        else:
            block['asset'] = next(iter(inherited), None)


def _structure_replace(doc, doc_id, payload):
    old = {b['id']: b for b in doc['blocks']}
    blocks = []
    explicit_assets = {}
    for item in payload['blocks']:
        if item['kind'] not in KINDS:
            raise ValueError('Unknown content kind')
        block = dict(old.get(item['id'], {'translation': None, 'history': [], 'review': None, 'user_edited': False, 'asset': None}))
        block.update({k: item[k] for k in ('id', 'kind', 'text', 'source_ids', 'structure_note')})
        if 'table_mode' in item:
            block['table_mode'] = item['table_mode']
        if 'asset' in item:
            explicit_assets[item['id']] = item['asset']
        if not block['source_ids'] or not block['structure_note'].strip():
            raise ValueError('Each structural decision requires source ids and a reason')
        if block['kind'] not in VERBATIM:
            if _canonical(_source_text(doc, block['source_ids'])) != _canonical(block['text']) and not item.get('source_change', '').strip():
                raise ValueError('Changed source text requires explicit source_change explanation')
            block['source_change'] = item.get('source_change', '')
        blocks.append(block)
    _resolve_structure_assets(doc, doc_id, blocks, explicit_assets)
    _commit_structure(doc, blocks, payload)


def _structure_keep(doc, doc_id, payload):
    # Start from the extracted ledger. Explicit update/merge notes beat default_structure_note.
    blocks = [dict(block, source_ids=list(block['source_ids'])) for block in doc['blocks']]
    by_id = {block['id']: block for block in blocks}
    updates = payload.get('updates') or []
    merges = payload.get('merges') or []
    explicit_assets = {}
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
        if 'table_mode' in item:
            block['table_mode'] = item['table_mode']
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
            if item['asset']:
                _assign_asset(doc_id, block, item['asset'])
            explicit_assets[block['id']] = item['asset']
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
        merged_sources = set(into['source_ids']) | set(gained)
        participants = [into, *sources]
        old_assets = set()
        for old in doc['blocks']:
            if not old.get('asset') or old['kind'] == 'page':
                continue
            old_sources = set(old['source_ids'])
            if old_sources <= merged_sources:
                # A crop update replaces this old image only when its updated
                # block still contains the image's entire source set.
                replacements = {explicit_assets[b['id']] for b in participants
                                if explicit_assets.get(b['id'])
                                and old_sources <= set(b['source_ids'])}
                old_assets.update(replacements or {old['asset']})
        staged_assets = {explicit_assets[b['id']] for b in participants
                         if explicit_assets.get(b['id'])}
        if 'asset' in merge:
            # The key alone is not an explicit choice; null/empty is rejected.
            _assign_asset(doc_id, into, merge['asset'])
            explicit_assets[into['id']] = merge['asset']
        elif len(old_assets | staged_assets) > 1:
            raise ValueError('Merge would discard figure assets; provide explicit asset for the merged block')
        elif staged_assets:
            # An explicitly attached source image follows a merged source atom.
            explicit_assets[into['id']] = next(iter(staged_assets))
        for fid in from_ids:
            explicit_assets.pop(fid, None)
        into['source_ids'] = list(into['source_ids']) + gained
        if 'text' in merge:
            into['text'] = merge['text']
        if 'kind' in merge:
            if merge['kind'] not in KINDS:
                raise ValueError('Unknown content kind')
            into['kind'] = merge['kind']
        if 'table_mode' in merge:
            into['table_mode'] = merge['table_mode']
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
    _resolve_structure_assets(doc, doc_id, blocks, explicit_assets)
    for block in blocks:
        _require_source_change(doc, block)
    _commit_structure(doc, blocks, payload)


def _keep_extracted(payload):
    return bool(payload.get('keep_extracted')) or payload.get('mode') in ('keep', 'patch', 'keep_extracted')


def _task_view(task):
    if not isinstance(task, dict):
        raise ValueError('task snapshot must be the JSON object returned by tasks or show')
    # CLI prints {"ok": true, "result": {...}}; tasks()/show() return the view itself.
    if 'revision' not in task and isinstance(task.get('result'), dict):
        return task['result']
    return task


def _snapshot_translation_hashes(view):
    # Shells omit translation_hash; a row without one reuses this snapshot's blocks
    # row of the same id. Never hash the live document.
    found = {}
    from_blocks = {}
    rows_by_key = {}
    for key in ('blocks', 'context', 'section_context'):
        rows = view.get(key)
        if not isinstance(rows, list):
            continue
        rows_by_key[key] = rows
        for block in rows:
            if not isinstance(block, dict):
                continue
            block_id = block.get('id')
            value = block.get('translation_hash')
            if block_id and isinstance(value, str) and value.strip() and block_id not in found:
                found[block_id] = value
            if key == 'blocks' and block_id and isinstance(value, str) and value.strip() and block_id not in from_blocks:
                from_blocks[block_id] = value
    for key in ('blocks', 'context', 'section_context'):
        for block in rows_by_key.get(key) or []:
            if not isinstance(block, dict):
                continue
            block_id = block.get('id')
            value = block.get('translation_hash')
            if not block_id or block_id in found:
                continue
            if not (isinstance(value, str) and value.strip()) and block_id in from_blocks:
                found[block_id] = from_blocks[block_id]
    return found


def assemble_payload(doc, operation, blocks, submission_id, agent, task=None):
    # Bind revision and review hashes to the tasks/show snapshot the agent read.
    # Does not invent translations, review notes, or hashes of the live document.
    if operation not in ('translate', 'review'):
        raise ValueError('assemble operation must be translate or review')
    if not isinstance(blocks, list) or not blocks:
        raise ValueError('blocks must be a non-empty list')
    if not isinstance(submission_id, str) or not submission_id.strip():
        raise ValueError('submission_id is required')
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError('agent is required')
    if task is None:
        raise ValueError('task snapshot is required to bind revision; refusing to stamp the latest document revision')
    view = _task_view(task)
    revision = view.get('revision')
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError('task snapshot is required to bind revision; refusing to stamp the latest document revision')
    snapshot_id = view.get('document_id') or view.get('id')
    if isinstance(snapshot_id, str) and doc is not None and snapshot_id != doc.get('id'):
        raise ValueError('task snapshot document_id does not match this document')
    hashes = _snapshot_translation_hashes(view) if operation == 'review' else {}
    prepared = []
    for item in blocks:
        if not isinstance(item, dict) or not item.get('id'):
            raise ValueError('each assembled block needs an id')
        row = dict(item)
        if operation == 'review' and not row.get('translation_hash'):
            bound = hashes.get(row['id'])
            if not bound:
                raise ValueError(
                    f"{row['id']}: task snapshot is required to bind translation_hash; "
                    'refusing to hash the latest translation'
                )
            row['translation_hash'] = bound
        prepared.append(row)
    return {'revision': revision, 'submission_id': submission_id, 'agent': agent,
            'operation': operation, 'blocks': prepared}


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
