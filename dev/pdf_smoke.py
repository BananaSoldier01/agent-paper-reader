"""Real PDF processing of a small authored, redistribution-safe fixture."""
from pathlib import Path
import json
from reader.store import ROOT,digest
from reader.importer import import_document
from reader.workflow import submit,fingerprint
from reader.exporter import export_html
lines=['Cooling Study','The cooling system uses 5 kW.','Lower power does not always mean higher efficiency.']
c=('BT /F1 12 Tf 40 750 Td ('+lines[0]+') Tj 0 -25 Td ('+lines[1]+') Tj 0 -25 Td ('+lines[2]+') Tj ET').encode()
objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',b'<< /Length '+str(len(c)).encode()+b' >>\nstream\n'+c+b'\nendstream',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
buf=b'%PDF-1.4\n';offsets=[]
for i,o in enumerate(objects,1):offsets.append(len(buf));buf+=f'{i} 0 obj\n'.encode()+o+b'\nendobj\n'
x=len(buf);buf+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets)+f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{x}\n%%EOF'.encode()
p=ROOT/'cooling-study.pdf';p.write_bytes(buf);d=import_document(p)
def send(op,**kw):
 global d
 d=submit(d['id'],dict(operation=op,revision=d['revision'],submission_id=f'pdf-{op}-{d["revision"]}',agent='Codex synthetic PDF fixture',**kw))
blocks=[]
for b in d['blocks']:
 b=dict(b,structure_note='核对自产PDF单栏三行，空白页备份单独保留。')
 if b['text']=='Cooling Study':b['kind']='heading'
 blocks.append(b)
send('structure',blocks=blocks,note='合成PDF文字、顺序与区域核对。')
send('terms',terms=[])
zh=dict(zip(lines,['冷却研究','冷却系统使用 5 kW。','功率更低并不总意味着效率更高。']))
send('translate',blocks=[dict(id=b['id'],translation=dict(text=zh[b['text']],pairs=[dict(id='g1',source=[[0,len(b['text'])]],target=[[0,len(zh[b['text']])]])])) for b in d['blocks'] if b['text'] in zh])
send('review',blocks=[dict(id=b['id'],translation_hash=digest(b['translation']),note='第二遍核对：5 kW保留；not always保留非必然限定，efficiency为效率。') for b in d['blocks'] if b.get('translation')])
send('full_review',fingerprint=fingerprint(d),note='核对全部三行文字、原图与语义组，无遗漏，合成PDF仅用于功能验证。')
r=export_html(d['id']);(ROOT/'pdf-result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps(r,ensure_ascii=False))
