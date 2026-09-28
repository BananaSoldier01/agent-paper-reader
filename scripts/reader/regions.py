import pypdfium2 as pdfium
from .store import read,folder,digest

def crop(doc_id,page,bbox):
    doc=read(doc_id)
    info=next((p for p in doc['pages'] if p['page']==page),None)
    if not info or not (0<=bbox[0]<bbox[2]<=info['width'] and 0<=bbox[1]<bbox[3]<=info['height']):
        raise ValueError('Invalid page or top-left PDF-point bounding box')
    name='region-'+digest({'page':page,'bbox':bbox})[:16]+'.png'
    path=folder(doc_id)/name
    if not path.exists():
        pdf=pdfium.PdfDocument(folder(doc_id)/doc['source_file'])
        try:
            image=pdf[page-1].render(scale=2).to_pil()
            image.crop(tuple(round(v*2) for v in bbox)).save(path)
        finally:pdf.close()
    return {'asset':name,'page':page,'bbox':bbox}
