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
            'reviewed':sum(bool(b['review']) for b in doc['blocks'])}


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


def _window(doc, chosen):
    indices = [doc['blocks'].index(b) for b in chosen]
    context = doc['blocks'][max(0, min(indices)-1):max(indices)+2] if indices else []
    # Include complete enclosing section, even when batch is smaller.
    section = []
    if indices:
        start = min(indices)
        while start > 0 and doc['blocks'][start]['kind'] != 'heading':
            start -= 1
        end = max(indices)+1
        while end < len(doc['blocks']) and doc['blocks'][end]['kind'] != 'heading':
            end += 1
        section = doc['blocks'][start:end]
    return context, section


def _tasks_view(doc, limit=8, full=False, stage=None):
    computed, pending = _stage_and_pending(doc)
    stage = computed if full else (stage or computed)
    chosen = (doc['blocks'] if stage == 'structure' else pending)[:limit]
    context, section = _window(doc, chosen)
    if full:
        return {'document_id': doc['id'], 'revision': doc['revision'], 'stage': computed, 'terms': doc['terms'],
                'outline': [{'id': b['id'], 'text': b['text']} for b in doc['blocks'] if b['kind'] == 'heading'],
                'blocks': chosen, 'context': context, 'section_context': section, 'issues': doc['issues']}
    meta = _projection_meta(doc, stage)
    if stage == 'structure':
        return {**meta, 'terms': doc['terms'], 'outline': _outline(doc), 'issue_summary': issue_summary(doc),
                'blocks': [_block_summary(b) for b in chosen], 'context': [_block_summary(b) for b in context],
                'section_context': [_block_summary(b) for b in section]}
    return {**meta, 'terms': doc['terms'], 'outline': _outline(doc),
            'issue_summary': issue_summary(doc, unresolved_only=True),
            'blocks': [_public_block(b) for b in chosen], 'context': [_public_block(b) for b in context],
            'section_context': [_public_block(b) for b in section]}


def tasks(doc, limit=8, full=False):
    return _tasks_view(doc, limit, full=full)


def _show_full(doc):
    result = copy.deepcopy(doc)
    result['fingerprint'] = fingerprint(doc)
    for block in result['blocks']:
        block['translation_hash'] = digest(block['translation']) if block['translation'] else None
    return result


def _show_view(doc, full=False, stage=None, limit=8):
    if full:
        return _show_full(doc)
    computed, _pending = _stage_and_pending(doc)
    stage = stage or computed
    if stage == 'structure':
        return {**_projection_meta(doc, stage), 'outline': _outline(doc), 'issue_summary': issue_summary(doc),
                'blocks': [_block_summary(b) for b in doc['blocks']]}
    return _tasks_view(doc, limit, full=False, stage=stage)


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


def project_document(doc, *, full=False, stage=None, view='show', limit=8):
    if view == 'progress':
        return _progress_view(doc, full=full)
    if view == 'tasks':
        return _tasks_view(doc, limit, full=full, stage=stage)
    if view == 'show':
        return _show_view(doc, full=full, stage=stage, limit=limit)
    raise ValueError('Unknown projection view')


def _canonical(value):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def _assign_asset(doc_id, block, asset):
    if asset and (not isinstance(asset, str) or '/' in asset or '\\' in asset or not (folder(doc_id) / asset).is_file()):
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
            _assign_asset(doc_id, block, item['asset'])
        if not block['source_ids'] or not block['structure_note'].strip():
            raise ValueError('Each structural decision requires source ids and a reason')
        if block['kind'] not in VERBATIM:
            if _canonical(_source_text(doc, block['source_ids'])) != _canonical(block['text']) and not item.get('source_change', '').strip():
                raise ValueError('Changed source text requires explicit source_change explanation')
            block['source_change'] = item.get('source_change', '')
        blocks.append(block)
    _commit_structure(doc, blocks, payload)


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
        if 'text' in item:
            block['text'] = item['text']
        if 'source_ids' in item:
            block['source_ids'] = list(item['source_ids'])
        if 'structure_note' in item:
            block['structure_note'] = item['structure_note']
        if 'source_change' in item:
            block['source_change'] = item['source_change']
        if 'asset' in item:
            _assign_asset(doc_id, block, item['asset'])
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
        for fid in from_ids:
            src = by_id.get(fid)
            if not src:
                raise ValueError(f"Unknown block {fid}")
            gained.extend(src['source_ids'])
        into['source_ids'] = list(into['source_ids']) + gained
        if 'text' in merge:
            into['text'] = merge['text']
        if 'kind' in merge:
            if merge['kind'] not in KINDS:
                raise ValueError('Unknown content kind')
            into['kind'] = merge['kind']
        if 'source_change' in merge:
            into['source_change'] = merge['source_change']
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
        elif op=='resolve':
            for item in payload['issues']:
                if not item['resolution'].strip():
                    raise ValueError('Extraction issue requires explicit resolution/limitation')
                next(i for i in doc['issues'] if i['id']==item['id'])['resolution']=item['resolution']
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
