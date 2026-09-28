"""Synthetic document fixture. This is not a general translation script."""
import json
from pathlib import Path
from reader.store import ROOT,read,digest
from reader.importer import import_document
from reader.workflow import submit,fingerprint,validate,user_edit
from reader.exporter import export_html

text='# Cooling Study\n\nThe cooling system uses 5 kW. Lower power does not always mean higher efficiency.\n\nA digital twin represents the physical system.\n'
source=ROOT/'sample.md';source.write_text(text)
d=import_document(source)
def send(op,**args):
 global d
 p=dict(operation=op,revision=d['revision'],submission_id=f'smoke-{op}-{d["revision"]}',agent='Codex authored synthetic fixture',**args)
 (ROOT/'submissions'/f'{d["revision"]}.json').write_text(json.dumps(p,ensure_ascii=False,indent=2));d=submit(d['id'],p)
send('structure',blocks=[dict(b,structure_note='与合成Markdown原文逐段核对，顺序与边界完整。') for b in d['blocks']],note='标题与两个正文段落，无省略。')
send('terms',terms=[dict(id='t1',en='digital twin',zh='数字孪生',definition='用于表示物理系统的数字模型；原文未声称实时同步。')])
zh=[['冷却研究'],['冷却系统使用 5 kW。','功率更低并不总意味着效率更高。'],['数字孪生表示该物理系统。']]
items=[]
for b,z in zip(d['blocks'],zh):
 src=[b['text']] if len(z)==1 else ['The cooling system uses 5 kW.','Lower power does not always mean higher efficiency.']
 target=' '.join(z);pairs=[];cursor=0
 for i,(s,t) in enumerate(zip(src,z)):
  start=b['text'].index(s);pairs.append(dict(id=f'g{i+1}',source=[[start,start+len(s)]],target=[[cursor,cursor+len(t)]]));cursor+=len(t)+1
 items.append(dict(id=b['id'],translation=dict(text=target,pairs=pairs)))
send('translate',blocks=items)
send('review',blocks=[dict(id=b['id'],translation_hash=digest(b['translation']),note=['标题对应无新增含义。','5 kW原样保留，not always译为并不总，功率与效率没有混淆。','represents译为表示，没有加入实时控制或同步能力。'][i]) for i,b in enumerate(d['blocks'])])
send('full_review',fingerprint=fingerprint(d),note='复核三段全部原文与译文、四组语义边界、单位与否定限定；仅用于运行验证。')
r=export_html(d['id']);print(json.dumps(r,ensure_ascii=False));(ROOT/'smoke-result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
