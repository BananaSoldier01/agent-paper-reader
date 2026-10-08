import base64
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from bs4 import BeautifulSoup
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


def check_export_html(html, document, assets):
    """Check serialized content and static resources, without claiming visual QA."""
    def fail(detail):
        raise ValueError('Export integrity check failed: ' + detail)

    raw = html.encode('utf-8')
    if b'<meta charset="UTF-8">' not in raw[:1024]:
        fail('UTF-8 declaration must precede the snapshot')
    page = BeautifulSoup(html, 'html.parser')
    scripts = page.find_all('script')
    snapshots = [script.string for script in scripts
                 if script.string and script.string.startswith('window.__SNAPSHOT__=')]
    if len(snapshots) != 1:
        fail('exactly one document snapshot required')
    try:
        snapshot = json.loads(snapshots[0].removeprefix('window.__SNAPSHOT__=').removesuffix(';'))
    except (TypeError, ValueError):
        fail('document snapshot is not valid JSON')
    if snapshot != {'document': document, 'assets': assets}:
        fail('snapshot differs from reviewed document or source assets')
    if page.find(id='root') is None:
        fail('reader mount point missing')
    if any(script.has_attr('src') for script in scripts):
        fail('reader script was not embedded')
    if not any(script.get('type') == 'module' and script.string and script.string.strip()
               for script in scripts):
        fail('embedded reader script missing or empty')
    if any('stylesheet' in link.get('rel', []) for link in page.find_all('link')):
        fail('reader stylesheet was not embedded')
    styles = page.find_all('style')
    if not styles or any(not style.string or not style.string.strip() for style in styles):
        fail('embedded reader stylesheet missing or empty')
    font_count = 0
    for style in styles:
        if re.search(r'@import\b', style.string, re.IGNORECASE):
            fail('stylesheet still imports another resource')
        for match in re.finditer(r'url\(\s*["\']?([^\)"\']+)["\']?\s*\)', style.string):
            url = match[1].strip()
            if not url.startswith('data:'):
                fail('stylesheet still references an external resource')
            if ';base64,' in url:
                try:
                    content = base64.b64decode(url.split(';base64,', 1)[1], validate=True)
                except ValueError:
                    fail('embedded stylesheet resource has invalid base64')
                if not content:
                    fail('embedded stylesheet resource is empty')
                font_count += 1
    return {'ok': True, 'document_id': document['id'], 'revision': document['revision'],
            'embedded_assets': len(assets), 'embedded_style_resources': font_count,
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'browser_check': 'not_performed'}


def export_html(doc_id, output=None):
    doc=public_document(read(doc_id))
    if not doc['validation']['ok']:
        raise ValueError('Complete translation and review before export: '+ '; '.join(doc['validation']['errors']))
    directory=folder(doc_id)
    assets={}
    names={p['asset'] for p in doc['pages']} | {b['asset'] for b in doc['blocks'] if b.get('asset')}
    for name in names:
        mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
        content=(directory/name).read_bytes()
        if not content:
            raise ValueError(f'Empty export resource {name}')
        assets[name]='data:'+mime+';base64,'+base64.b64encode(content).decode()
    bundle=BUNDLE
    html=(bundle/'index.html').read_text('utf-8')
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
            content=path.read_bytes()
            if not content:
                raise ValueError(f'Empty export resource {path}')
            return 'url(data:'+mime+';base64,'+base64.b64encode(content).decode()+')'
        css=re.sub(r'url\(["\']?(/assets/[^)"\']+)["\']?\)',embed_font,css)
        html=re.sub(r'<link[^>]*href="'+re.escape(url)+r'"[^>]*>',lambda _: '<style>'+css+'</style>',html)
    payload=json.dumps({'document':doc,'assets':assets},ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    html=html.replace('<head>','<head><meta charset="UTF-8"><script>window.__SNAPSHOT__='+payload+';</script>')
    target=Path(output) if output else ROOT/'exports'/f'{doc_id}.html'
    if not target.resolve().is_relative_to(ROOT):
        raise ValueError('Exports must stay inside workspace')
    artifact_validation=check_export_html(html,doc,assets)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(html.encode('utf-8'))
    if target.read_bytes() != html.encode('utf-8'):
        raise ValueError('Export integrity check failed: saved HTML differs from checked content')
    return {'path':str(target.resolve()),'read_only':True,'validation':doc['validation'],
            'artifact_validation':artifact_validation}
