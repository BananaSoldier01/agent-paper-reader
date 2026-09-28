import base64
import json
import mimetypes
from pathlib import Path
from .store import ROOT, BUNDLE, folder, read
from .workflow import validate, note_status


def public_document(doc):
    import copy
    value=copy.deepcopy(doc)
    value.pop('submissions',None)
    for n in value['notes']:
        n['status']=note_status(doc,n)
    value['validation']=validate(doc)
    return value


def export_html(doc_id, output=None):
    doc=public_document(read(doc_id))
    if not doc['validation']['ok']:
        raise ValueError('Complete translation and review before export: '+ '; '.join(doc['validation']['errors']))
    directory=folder(doc_id)
    assets={}
    names={p['asset'] for p in doc['pages']} | {b['asset'] for b in doc['blocks'] if b.get('asset')}
    for name in names:
        mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
        assets[name]='data:'+mime+';base64,'+base64.b64encode((directory/name).read_bytes()).decode()
    bundle=BUNDLE
    html=(bundle/'index.html').read_text('utf-8')
    import re
    for url in re.findall(r'<script[^>]*src="([^"]+)"[^>]*></script>',html):
        js=(bundle/url.lstrip('/')).read_text('utf-8').replace('</script','<\\/script')
        html=re.sub(r'<script[^>]*src="'+re.escape(url)+r'"[^>]*></script>',lambda _: '<script type="module">'+js+'</script>',html)
    for url in re.findall(r'<link[^>]*href="([^"]+\.css)"[^>]*>',html):
        css=(bundle/url.lstrip('/')).read_text('utf-8')
        def embed_font(match):
            path=bundle/match[1].lstrip('/')
            if not path.is_file():
                raise ValueError(f'Missing export resource {path}')
            mime=mimetypes.guess_type(path)[0] or 'application/octet-stream'
            return 'url(data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()+')'
        css=re.sub(r'url\(["\']?(/assets/[^)"\']+)["\']?\)',embed_font,css)
        html=re.sub(r'<link[^>]*href="'+re.escape(url)+r'"[^>]*>',lambda _: '<style>'+css+'</style>',html)
    payload=json.dumps({'document':doc,'assets':assets},ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    html=html.replace('<head>','<head><meta charset="UTF-8"><script>window.__SNAPSHOT__='+payload+';</script>')
    target=Path(output) if output else ROOT/'exports'/f'{doc_id}.html'
    if not target.resolve().is_relative_to(ROOT):
        raise ValueError('Exports must stay inside workspace')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(html,encoding='utf-8')
    return {'path':str(target.resolve()),'read_only':True,'validation':doc['validation']}
