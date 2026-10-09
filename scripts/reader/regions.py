import pypdfium2 as pdfium
import json
from math import ceil
from PIL import Image
from .store import read,folder,digest,atomic


def crop_edge_risks(image):
    """Advisory: visible content near an edge can be clipped or a natural border."""
    rgba = image.convert('RGBA')
    background = Image.new('RGBA', rgba.size, 'white')
    background.alpha_composite(rgba)
    image = background.convert('RGB')
    width, height = image.size
    bands = {
        'top': image.crop((0, 0, width, min(2, height))),
        'right': image.crop((max(0, width-2), 0, width, height)),
        'bottom': image.crop((0, max(0, height-2), width, height)),
        'left': image.crop((0, 0, min(2, width), height)),
    }
    risks = []
    for side, band in bands.items():
        horizontal = side in ('top', 'bottom')
        length = width if horizontal else height
        required = max(3, ceil(length * .005))
        run = 0
        pixels = band.load()
        for i in range(length):
            visible = any(min(pixels[i, j] if horizontal else pixels[j, i]) < 245
                          for j in range(band.height if horizontal else band.width))
            run = run + 1 if visible else 0
            if run >= required:
                risks.append(side)
                break
    return risks


def _suggested_bbox(bbox, risks, info):
    # A starting point for source-page review, not an automatically certified crop.
    x0, y0, x1, y1 = bbox
    return [max(0, x0-10) if 'left' in risks else x0,
            max(0, y0-10) if 'top' in risks else y0,
            min(info['width'], x1+10) if 'right' in risks else x1,
            min(info['height'], y1+10) if 'bottom' in risks else y1]


def crop_edge_warning(doc, block):
    asset = block.get('asset')
    if not asset:
        return None
    path = folder(doc['id']) / asset
    metadata_path = path.with_suffix('.crop.json')
    if not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text('utf-8'))
        if (metadata['source_sha256'] != doc['source_sha256'] or
                metadata['asset_sha256'] != digest(path.read_bytes())):
            return None  # The existing provenance error is actionable first.
        risks = metadata.get('edge_risks')
        if not isinstance(risks, list) or any(s not in ('top', 'right', 'bottom', 'left') for s in risks):
            with Image.open(path) as image:
                risks = crop_edge_risks(image)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if risks:
        return (f'{block["id"]}: crop edge risk ({", ".join(risks)}); visible content reaches '
                'the border; compare with the source page and recrop if clipped '
                '(border contact alone is not proof of clipping)')
    return None


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
    with Image.open(path) as image:
        risks = crop_edge_risks(image)
    metadata={'page':page,'bbox':list(bbox),'source_sha256':doc['source_sha256'],
              'asset_sha256':digest(path.read_bytes()),'edge_risks':risks}
    atomic(path.with_suffix('.crop.json'),metadata)
    result = {'asset':name,'page':page,'bbox':bbox,'edge_risks':risks}
    if risks:
        result['suggested_bbox'] = _suggested_bbox(bbox, risks, info)
    return result
