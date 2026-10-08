import argparse
import json
import os
import sys
from pathlib import Path
from .store import read, ROOT
from .importer import import_document
from .workflow import (
    submit, validate, fingerprint, project_document, assemble_payload, fill_pair_offsets,
    pending_counts, DEFAULT_TASK_LIMIT,
)
from .exporter import export_html


def _print_json(payload, pretty):
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))


def _fs_dir_is_case_insensitive(directory):
    """Probe whether directory treats names case-insensitively. Always cleans up."""
    directory = Path(directory)
    try:
        if not directory.is_dir():
            return False
    except OSError:
        return False
    name = f'.Apr_Case_Probe_{os.getpid()}'
    primary = directory / name
    alternate = directory / name.swapcase()
    if primary == alternate:
        return False
    created = False
    try:
        primary.mkdir()
        created = True
        return alternate.exists()
    except OSError:
        return False
    finally:
        if created:
            try:
                primary.rmdir()
            except OSError:
                pass


def _deepest_existing(path):
    cur = Path(path)
    while True:
        try:
            if cur.exists():
                return cur
        except OSError:
            return None
        if cur.parent == cur:
            return None
        cur = cur.parent


def _fs_paths_equivalent(a, b):
    """True if a and b name the same FS location (symlinks + case aliases)."""
    a = Path(a).expanduser()
    b = Path(b).expanduser()
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
    except OSError:
        pass
    try:
        ar = a.resolve()
        br = b.resolve()
    except OSError:
        return False
    if ar == br:
        return True
    ae = _deepest_existing(ar)
    be = _deepest_existing(br)
    if ae is None or be is None:
        return False
    try:
        anchors_same = os.path.samefile(ae, be)
    except OSError:
        anchors_same = False
    if anchors_same:
        try:
            rel_a = ar.relative_to(ae)
            rel_b = br.relative_to(be)
        except ValueError:
            return False
        if rel_a == rel_b:
            return True
        if not _fs_dir_is_case_insensitive(ae):
            return False
        return rel_a.as_posix().casefold() == rel_b.as_posix().casefold()
    # One path may already exist as a leaf while the other is only a case alias
    # of that name (on case-insensitive FS both exist() and samefile above;
    # here we still catch parent+casefolded-name when the probe says insensitive).
    try:
        if ae.is_file() and os.path.samefile(ae.parent, be):
            rel_b = br.relative_to(be)
            if len(rel_b.parts) == 1:
                if ae.name == rel_b.name:
                    return True
                if _fs_dir_is_case_insensitive(ae.parent) and ae.name.casefold() == rel_b.name.casefold():
                    return True
        if be.is_file() and os.path.samefile(be.parent, ae):
            rel_a = ar.relative_to(ae)
            if len(rel_a.parts) == 1:
                if be.name == rel_a.name:
                    return True
                if _fs_dir_is_case_insensitive(be.parent) and be.name.casefold() == rel_a.name.casefold():
                    return True
    except (OSError, ValueError):
        pass
    return False


def _fs_path_is_under_root(path, root):
    """True if path is root or inside root under actual filesystem semantics."""
    path = Path(path).expanduser()
    root = Path(root).expanduser()
    try:
        root_res = root.resolve()
    except OSError:
        return False
    try:
        resolved = path.resolve()
        if resolved == root_res or resolved.is_relative_to(root_res):
            return True
    except (ValueError, OSError):
        pass
    cur = path
    seen = set()
    while True:
        key = str(cur)
        if key in seen:
            break
        seen.add(key)
        try:
            if cur.exists():
                try:
                    anchor = root_res if root_res.exists() else root
                    if os.path.samefile(cur, anchor):
                        return True
                except OSError:
                    pass
        except OSError:
            pass
        if cur.parent == cur:
            break
        cur = cur.parent
    try:
        if not root_res.exists() or not _fs_dir_is_case_insensitive(root_res):
            return False
        r = root_res.as_posix().casefold().rstrip('/')
        p = path.resolve().as_posix().casefold()
        return p == r or p.startswith(r + '/')
    except OSError:
        return False



def _write_structure_candidates(doc, out=None, preview=None, preview_pages=3):
    from . import store
    from .structure_candidates import build_payload, render_preview

    data_root=Path(store.DATA).expanduser().resolve()
    path=Path(out).expanduser() if out else ROOT/'candidates'/f'{doc["id"]}-structure-candidates.json'
    preview_path=Path(preview).expanduser() if preview else None
    targets=[path]
    if preview_path is not None:
        targets.append(preview_path)
    for target in targets:
        if _fs_path_is_under_root(target, data_root) or _fs_path_is_under_root(target.parent, data_root):
            raise ValueError('structure candidates output must not be inside the document data directory')
    if preview_path is not None and _fs_paths_equivalent(path, preview_path):
        raise ValueError('structure candidates --out and --preview must not resolve to the same path')
    payload=build_payload(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if preview_path is not None:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(render_preview(payload, preview_pages), encoding='utf-8')
    cov=payload['coverage']
    return {
        'path':str(path),
        'preview':str(preview_path) if preview_path else None,
        'document_id':doc['id'],
        'revision':doc['revision'],
        'version':payload['version'],
        'atoms':cov['atom_count'],
        'candidates':cov['candidate_count'],
        'coverage_ok':cov['coverage_ok'],
        'orphans':len(cov['orphan_atom_ids']),
        'hard_spot_counts':payload['hard_spot_counts'],
        'pages':[{k:info[k] for k in ('page','mode','gutter')} for info in payload['pages']],
    }


def main():
    parser=argparse.ArgumentParser(description='Local Agent Paper Reader JSON CLI')
    common=argparse.ArgumentParser(add_help=False)
    common.add_argument('--pretty', action='store_true', help='Indent JSON for humans')
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('import', parents=[common]); p.add_argument('path')
    for name in ('show','tasks','progress','validate','submit','export'):
        p=commands.add_parser(name, parents=[common]); p.add_argument('document_id')
        if name in ('show','tasks'): p.add_argument('--limit',type=int,default=DEFAULT_TASK_LIMIT)
        if name in ('show','tasks','progress'): p.add_argument('--full',action='store_true')
        if name in ('show','tasks'):
            p.add_argument('--section-limit',type=int,default=None)
            p.add_argument('--section-offset',type=int,default=None)
        if name=='submit': p.add_argument('payload')
        if name=='export': p.add_argument('--output')
    p=commands.add_parser('assemble', parents=[common])
    p.add_argument('document_id')
    p.add_argument('operation', choices=('translate','review'))
    p.add_argument('--blocks', required=True)
    p.add_argument('--submission-id', required=True)
    p.add_argument('--agent', required=True)
    p.add_argument('--task', help='tasks/show JSON captured when the agent read the work')
    p.add_argument('--out')
    p=commands.add_parser('pair-offsets', parents=[common])
    p.add_argument('document_id')
    p.add_argument('--blocks', required=True)
    p.add_argument('--out')
    p=commands.add_parser('crop', parents=[common]); p.add_argument('document_id'); p.add_argument('--page',type=int,required=True); p.add_argument('--bbox',nargs=4,type=float,required=True)
    p=commands.add_parser('structure-candidates', parents=[common])
    p.add_argument('document_id')
    p.add_argument('--out')
    p.add_argument('--preview')
    p.add_argument('--preview-pages', type=int, default=3)
    p=commands.add_parser('serve', parents=[common]); p.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    try:
        if args.command=='serve':
            import uvicorn
            uvicorn.run('reader.server:app',host='127.0.0.1',port=args.port)
            return
        if args.command=='crop':
            from .regions import crop
            result=crop(args.document_id,args.page,args.bbox)
        elif args.command=='import':
            d=import_document(args.path)
            result={'document_id':d['id'],'revision':d['revision'],'stage':d['stage'],'blocks':len(d['blocks'])}
            if Path(d['source_file']).suffix.lower()=='.pdf' and d['stage']=='structure':
                try:
                    result['structure_candidates']={'status':'ready', **_write_structure_candidates(d)}
                except (ValueError, OSError) as exc:
                    result['structure_candidates']={'status':'unavailable','error':str(exc)}
        elif args.command=='assemble':
            d=read(args.document_id)
            blocks=json.loads(Path(args.blocks).read_text('utf-8'))
            task=json.loads(Path(args.task).read_text('utf-8')) if args.task else None
            payload=assemble_payload(d, args.operation, blocks, args.submission_id, args.agent, task)
            if args.out:
                path=Path(args.out)
            else:
                if '/' in args.submission_id or '\\' in args.submission_id:
                    raise ValueError('submission_id must not contain a path')
                path=ROOT/'submissions'/f'{d["id"]}-{args.operation}-{args.submission_id}.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            result={'path':str(path),'revision':payload['revision'],'operation':payload['operation'],'block_count':len(payload['blocks'])}
        elif args.command=='pair-offsets':
            d=read(args.document_id)
            items=json.loads(Path(args.blocks).read_text('utf-8'))
            aligned=fill_pair_offsets(d, items)
            if args.out:
                path=Path(args.out)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(aligned, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                result={'path':str(path),'block_count':len(aligned)}
            else:
                result=aligned
        elif args.command=='submit':
            d=submit(args.document_id,json.loads(Path(args.payload).read_text('utf-8')))
            result={'document_id':d['id'],'revision':d['revision'],'stage':d['stage'],**pending_counts(d)}
        elif args.command=='export':
            result=export_html(args.document_id,args.output)
        elif args.command=='structure-candidates':
            d=read(args.document_id)
            result=_write_structure_candidates(d, args.out, args.preview, args.preview_pages)
        else:
            d=read(args.document_id)
            if args.command=='show':
                result=project_document(d, full=args.full, view='show', limit=max(1,args.limit),
                                       section_limit=args.section_limit, section_offset=args.section_offset)
            elif args.command=='tasks':
                result=project_document(d, full=args.full, view='tasks', limit=max(1,args.limit),
                                       section_limit=args.section_limit, section_offset=args.section_offset)
            elif args.command=='progress':
                result=project_document(d, full=args.full, view='progress')
            else: result={'document_id':d['id'],'revision':d['revision'],'stage':d['stage'],'fingerprint':fingerprint(d),**validate(d)}
        _print_json({'ok':True,'result':result}, args.pretty)
        if args.command=='validate' and not result['ok']:
            sys.exit(2)
    except (ValueError,KeyError,StopIteration,OSError) as exc:
        _print_json({'ok':False,'error':str(exc)}, getattr(args,'pretty',False))
        sys.exit(1)

if __name__=='__main__':
    main()
