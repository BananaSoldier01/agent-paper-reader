import argparse
import json
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
            from . import store
            data_root=Path(store.DATA).expanduser().resolve()
            for raw in (args.out, args.preview):
                if not raw: continue
                resolved=Path(raw).expanduser().resolve()
                if resolved==data_root or resolved.is_relative_to(data_root):
                    raise ValueError('structure candidates output must not be inside the document data directory')
            from .structure_candidates import build_payload, render_preview
            d=read(args.document_id)
            payload=build_payload(d)
            if args.out:
                path=Path(args.out)
            else:
                path=ROOT/'candidates'/f'{d["id"]}-structure-candidates.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            preview_path=None
            if args.preview:
                preview_path=Path(args.preview)
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                preview_path.write_text(render_preview(payload, args.preview_pages), encoding='utf-8')
            cov=payload['coverage']
            result={
                'path':str(path),
                'preview':str(preview_path) if preview_path else None,
                'document_id':d['id'],
                'revision':d['revision'],
                'version':payload['version'],
                'atoms':cov['atom_count'],
                'candidates':cov['candidate_count'],
                'coverage_ok':cov['coverage_ok'],
                'orphans':len(cov['orphan_atom_ids']),
                'hard_spot_counts':payload['hard_spot_counts'],
                'pages':[{k:info[k] for k in ('page','mode','gutter')} for info in payload['pages']],
            }
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
