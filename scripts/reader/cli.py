import argparse
import json
import sys
from pathlib import Path
from .store import read
from .importer import import_document
from .workflow import submit, validate, fingerprint, project_document
from .exporter import export_html


def main():
    parser=argparse.ArgumentParser(description='Local Agent Paper Reader JSON CLI')
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('import'); p.add_argument('path')
    for name in ('show','tasks','progress','validate','submit','export'):
        p=commands.add_parser(name); p.add_argument('document_id')
        if name=='tasks': p.add_argument('--limit',type=int,default=8)
        if name in ('show','tasks','progress'): p.add_argument('--full',action='store_true')
        if name in ('show','tasks'):
            p.add_argument('--section-limit',type=int,default=None)
            p.add_argument('--section-offset',type=int,default=None)
        if name=='submit': p.add_argument('payload')
        if name=='export': p.add_argument('--output')
    p=commands.add_parser('crop'); p.add_argument('document_id'); p.add_argument('--page',type=int,required=True); p.add_argument('--bbox',nargs=4,type=float,required=True)
    p=commands.add_parser('serve'); p.add_argument('--port',type=int,default=8765)
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
        elif args.command=='submit':
            d=submit(args.document_id,json.loads(Path(args.payload).read_text('utf-8')))
            result={'document_id':d['id'],'revision':d['revision'],'stage':d['stage']}
        elif args.command=='export':
            result=export_html(args.document_id,args.output)
        else:
            d=read(args.document_id)
            if args.command=='show':
                result=project_document(d, full=args.full, view='show',
                                       section_limit=args.section_limit, section_offset=args.section_offset)
            elif args.command=='tasks':
                result=project_document(d, full=args.full, view='tasks', limit=max(1,args.limit),
                                       section_limit=args.section_limit, section_offset=args.section_offset)
            elif args.command=='progress':
                result=project_document(d, full=args.full, view='progress')
            else: result={'document_id':d['id'],'revision':d['revision'],'stage':d['stage'],'fingerprint':fingerprint(d),**validate(d)}
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False,indent=2))
        if args.command=='validate' and not result['ok']:
            sys.exit(2)
    except (ValueError,KeyError,StopIteration,OSError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        sys.exit(1)

if __name__=='__main__':
    main()
