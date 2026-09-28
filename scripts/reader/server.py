from pathlib import Path
import secrets
import shutil
import tempfile
from fastapi import FastAPI, Request, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .store import ROOT, DATA, BUNDLE, read, folder, Conflict
from .importer import import_document
from .workflow import user_edit
from .exporter import public_document, export_html

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
TOKEN=secrets.token_urlsafe(32)

@app.middleware('http')
async def local_only(request:Request, call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost','testserver'):
        return JSONResponse({'error':'Loopback host required'},status_code=403)
    if request.method not in ('GET','HEAD') and request.headers.get('x-reader-token')!=TOKEN:
        return JSONResponse({'error':'Reload local reader for session token'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
    return response

@app.exception_handler(Conflict)
async def conflict(request,exc):
    return JSONResponse({'error':str(exc)},status_code=409)

@app.exception_handler(ValueError)
async def invalid(request,exc):
    return JSONResponse({'error':str(exc)},status_code=400)

@app.exception_handler(FileNotFoundError)
async def missing(request,exc):
    return JSONResponse({'error':'Document or asset not found'},status_code=404)

@app.get('/api/session')
def session():
    return {'token':TOKEN}

@app.get('/api/documents')
def documents():
    import json
    result=[]
    for path in sorted(DATA.glob('*/document.json')):
        d=json.loads(path.read_text('utf-8'))
        result.append({k:d[k] for k in ('id','title','stage','revision')})
    return result

@app.post('/api/import')
def upload(file:UploadFile):
    suffix=Path(file.filename or '').suffix.lower()
    directory=ROOT/'.cache'/'uploads'
    directory.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        path=Path(tmp)/Path(file.filename or 'document'+suffix).name
        with path.open('wb') as f:
            shutil.copyfileobj(file.file,f)
        doc=import_document(path)
    return public_document(doc)

@app.get('/api/documents/{doc_id}')
def document(doc_id:str):
    return public_document(read(doc_id))

@app.post('/api/documents/{doc_id}/edit')
def edit(doc_id:str,payload:dict):
    return public_document(user_edit(doc_id,payload))

@app.get('/api/documents/{doc_id}/assets/{name}')
def asset(doc_id:str,name:str):
    doc=read(doc_id)
    allowed={doc['source_file']}|{p['asset'] for p in doc['pages']}|{b['asset'] for b in doc['blocks'] if b.get('asset')}
    if name not in allowed or '/' in name or '\\' in name:
        raise HTTPException(404)
    return FileResponse(folder(doc_id)/name)

@app.post('/api/documents/{doc_id}/export')
def export(doc_id:str):
    result=export_html(doc_id)
    return FileResponse(result['path'],media_type='text/html',filename=f'{doc_id}.html')

if BUNDLE.exists():
    app.mount('/',StaticFiles(directory=BUNDLE,html=True),name='reader')
