import pypdfium2 as pdfium
import json
from .store import read,folder,digest,atomic


def crop_coverage_error(doc, block):
    """Check known crop coordinates against a figure/table's source bounds."""
    asset = block.get('asset')
    if block.get('kind') not in ('figure', 'table') or not asset:
        return None
    path = folder(doc['id']) / asset
    metadata_path = path.with_suffix('.crop.json')
    if not metadata_path.is_file():
        return None  # Older/custom images have no crop provenance to compare.
    prefix = f'{block["id"]}: '
    label = 'table' if block['kind'] == 'table' else 'figure'
    try:
        metadata = json.loads(metadata_path.read_text('utf-8'))
        page, bbox = metadata['page'], metadata['bbox']
        if (metadata['source_sha256'] != doc['source_sha256'] or
                metadata['asset_sha256'] != digest(path.read_bytes()) or
                not isinstance(bbox, list) or len(bbox) != 4 or
                not all(isinstance(v, (int, float)) for v in bbox) or
                not (bbox[0] < bbox[2] and bbox[1] < bbox[3])):
            return prefix + 'crop provenance differs from source/image; regenerate the crop'
    except (OSError, ValueError, TypeError, KeyError):
        return prefix + 'invalid crop provenance; regenerate the crop'
    source_ids = set(block['source_ids'])
    sources = [a for a in doc['atoms'] if a['id'] in source_ids and a['text'].strip()
               and a.get('location', {}).get('page') is not None
               and len(a['location'].get('bbox') or []) == 4]
    if any(a['location']['page'] != page for a in sources):
        return prefix + f'single-page {label} crop has sources on other pages; recheck image placement'
    tolerance = 2  # PDF points: allow minor glyph/border rounding.
    outside = [a['id'] for a in sources if
               a['location']['bbox'][0] < bbox[0] - tolerance or
               a['location']['bbox'][1] < bbox[1] - tolerance or
               a['location']['bbox'][2] > bbox[2] + tolerance or
               a['location']['bbox'][3] > bbox[3] + tolerance]
    if outside:
        return prefix + f'{label} crop excludes source bounds on page {page}: ' + ', '.join(outside[:5]) + '; recrop all panels/labels'
    return None

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
    metadata={'page':page,'bbox':list(bbox),'source_sha256':doc['source_sha256'],
              'asset_sha256':digest(path.read_bytes())}
    atomic(path.with_suffix('.crop.json'),metadata)
    return {'asset':name,'page':page,'bbox':bbox}
