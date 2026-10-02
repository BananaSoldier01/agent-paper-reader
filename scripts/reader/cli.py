import argparse
import json
import sys
from pathlib import Path
from .store import read, ROOT
from .importer import import_document
from .workflow import (
    submit, validate, fingerprint, project_document, assemble_payload, pending_counts, DEFAULT_TASK_LIMIT,
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
    p=commands.add_parser('crop', parents=[common]); p.add_argument('document_id'); p.add_argument('--page',type=int,required=True); p.add_argument('--bbox',nargs=4,type=float,required=True)
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
        elif args.command=='submit':
            d=submit(args.document_id,json.loads(Path(args.payload).read_text('utf-8')))
            result={'document_id':d['id'],'revision':d['revision'],'stage':d['stage'],**pending_counts(d)}
        elif args.command=='export':
            result=export_html(args.document_id,args.output)
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
