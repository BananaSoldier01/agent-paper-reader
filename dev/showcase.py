"""Authored bilingual showcase; not an automatic translator. Uses public workflow APIs."""
import json,re,shutil
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from reader.store import ROOT,read,digest
from reader.importer import import_document
from reader.workflow import submit,user_edit,fingerprint,validate
from reader.exporter import export_html
repo=Path(__file__).resolve().parents[1]
# Explicit sentence pairs are authored for this synthetic example only.
rows=[
('# Context-Aware Cooling: A Reading Demonstration',['基于上下文的冷却分析：译读功能示例']),
('This is a fictional teaching document, not a published paper. All measurements below are invented to demonstrate the reader.',['这是一份虚构的教学文档，不是已发表论文。','以下测量值均为展示阅读器功能而编造。']),
('## 1 Introduction',['1 引言']),
('A digital twin represents a physical system through a computational model. In this example, it helps compare cooling strategies before applying them to equipment.',['数字孪生通过计算模型表示物理系统。','在本示例中，它帮助我们在将冷却策略应用于设备之前进行比较。']),
('The goal is to reduce energy use while keeping the outlet temperature below 30 °C. Lower electrical power does not always imply higher cooling efficiency.',['目标是在出口温度低于 30 °C 的前提下减少能耗。','电功率更低并不总意味着冷却效率更高。']),
('### 1.1 Research question',['1.1 研究问题']),
('Can a controller reduce pump power without violating the temperature constraint? A useful answer must consider both the energy measurement and the operating conditions.',['控制器能否在不违反温度约束的情况下减少泵功率？','有意义的回答必须同时考虑能耗测量和运行条件。']),
('### 1.2 How to read this example',['1.2 如何体验本示例']),
('Hover over a sentence to highlight its translation. Click the same sentence again to clear the persistent selection.',['将鼠标移到句子上，可以联动高亮对应译文。','再次点击同一句子，可以取消已固定的选择。']),
('Use the paragraph menu to inspect terms, source text, and revision history. The exported HTML displays saved notes but does not save new edits.',['通过段落菜单可以查看术语、原文和修订历史。','导出的 HTML 会展示已有笔记，但不会保存新的编辑。']),
('## 2 Method',['2 方法']),
('### 2.1 Model and assumptions',['2.1 模型与假设']),
('We assume a steady state and neglect heat loss to the surrounding room. These assumptions limit the situations in which the model can be used.',['我们假设系统处于稳态，并忽略向周围房间的热损失。','这些假设限定了模型的适用场景。']),
('The heat-transfer relation is $Q = \\dot{m} c_p \\Delta T$. Here, $Q$ is the heat-transfer rate and $\\Delta T$ is the temperature difference.',['传热关系为 $Q = \\dot{m} c_p \\Delta T$。','其中，$Q$ 为传热速率，$\\Delta T$ 为温差。']),
('#### 2.1.1 Boundary conditions',['2.1.1 边界条件']),
('The inlet temperature is fixed at 20 °C, and the heat load is 50 kW. A boundary condition describes the imposed environment, not a value predicted by the model.',['入口温度固定为 20 °C，热负荷为 50 kW。','边界条件描述外部施加的环境，而不是模型预测的数值。']),
('### 2.2 Validation',['2.2 验证']),
('A residual measures how strongly a candidate solution violates the governing equation. A small residual alone does not establish that the model matches real equipment.',['残差衡量候选解对控制方程的违反程度。','仅凭较小的残差，不能证明模型符合真实设备。']),
('![Cooling workflow](cooling-workflow.png)',None),
('Figure 1. The model proposes a setting, the constraint check evaluates it, and an operator decides whether to apply it.',['图 1：模型提出设定值，约束检查对其进行评估，再由操作人员决定是否实施。']),
('## 3 Illustrative results',['3 示例结果']),
('### 3.1 Energy and temperature',['3.1 能耗与温度']),
('The baseline uses 5 kW and reaches an outlet temperature of 27 °C. The candidate uses 4 kW and reaches 29 °C under the same assumed heat load.',['基线使用 5 kW，出口温度达到 27 °C。','在相同的假定热负荷下，候选方案使用 4 kW，出口温度达到 29 °C。']),
('Pump power falls by 20%, but the temperature margin decreases from 3 °C to 1 °C. The example therefore illustrates a trade-off rather than a universally better setting.',['泵功率下降 20%，但温度裕度从 3 °C 缩小到 1 °C。','因此，本示例展示的是一种权衡，而不是普遍更优的设定值。']),
('### 3.2 Uncertainty',['3.2 不确定性']),
('Sensor uncertainty and changing workloads may alter the comparison. These illustrative values are not sufficient evidence for a deployment decision.',['传感器不确定性与变化的工作负载可能改变比较结果。','这些示例数值不足以作为部署决策的依据。']),
('## 4 Limitations and reading notes',['4 限制与阅读笔记']),
('This demonstration has no experimental dataset or external references. Its purpose is to show bilingual navigation, terminology, annotations, equations, and traceable revisions.',['本演示没有实验数据集或外部参考文献。','其目的是展示双语导航、术语、批注、公式及可追溯修订。']),
('Open the optional local library to add notes or revise a translation. After reviewing those changes, export a new HTML snapshot for sharing.',['打开可选的本地文献库，可添加笔记或修订译文。','复核这些修改后，可以导出新的 HTML 快照供分享。']),
]
work=ROOT/'showcase-input';work.mkdir(exist_ok=True)
img=Image.new('RGB',(1080,190),'#f3f6f3');draw=ImageDraw.Draw(img);font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',24) if Path('/System/Library/Fonts/Supplemental/Arial.ttf').exists() else ImageFont.load_default()
for x,title in [(25,'Model proposal'),(390,'Constraint check'),(755,'Operator review')]:
 draw.rounded_rectangle((x,55,x+295,135),radius=10,fill='#e0ebe5',outline='#315a4c',width=2);draw.text((x+22,81),title,fill='#203d33',font=font)
for x in [330,695]:draw.line((x,95,x+45,95),fill='#315a4c',width=3);draw.polygon([(x+45,95),(x+35,89),(x+35,101)],fill='#315a4c')
img.save(work/'cooling-workflow.png')
source=work/'reading-showcase.md';source.write_text('\n\n'.join(s for s,z in rows)+'\n')
d=import_document(source)
def send(op,**kw):
 global d
 d=submit(d['id'],dict(operation=op,revision=d['revision'],submission_id=f'showcase-{op}-{d["revision"]}',agent='Codex authored demonstration',**kw))
blocks=[]
for b in d['blocks']:
 b=dict(b,structure_note='逐段核对自产演示文档；不引用任何真实研究结果。')
 if b['kind']=='figure':b['text']=''
 if b['kind']=='heading':b['text']=re.sub(r'^#+\s+','',b['text']);b['source_change']='移除Markdown标题标记，标题层级由原有编号保留。'
 blocks.append(b)
send('structure',blocks=blocks,note='核对全部多级标题、段落、图示及公式。')
terms=[('digital twin','数字孪生','本文指表示物理系统的计算模型；并未主张实时数据同步。'),('controller','控制器','根据目标和约束选择控制输入的组件；本示例不自动操作设备。'),('steady state','稳态','系统状态随时间不再显著变化的建模假设。'),('boundary condition','边界条件','求解模型时给定的外部条件，例如入口温度。'),('residual','残差','候选解代入控制方程后留下的不平衡量；小残差不等于实物验证通过。'),('baseline','基线','用于比较候选方案的参考设定。'),('temperature margin','温度裕度','允许温度上限与当前温度之差；示例中为30 °C减去出口温度。'),('uncertainty','不确定性','测量或模型结果可能变化的范围和来源；本文没有提供定量区间。')]
send('terms',terms=[dict(id=f't{i+1}',en=e,zh=z,definition=t) for i,(e,z,t) in enumerate(terms)])
items=[]
for b,(raw,z) in zip(d['blocks'],rows):
 if z is None:continue
 s=[b['text']] if len(z)==1 else re.split(r'(?<=[.?])\s+(?=[A-Z])',b['text']);assert len(s)==len(z),(b['text'],s,z)
 target=' '.join(z);pairs=[];pos=0
 for i,(a,c) in enumerate(zip(s,z)):
  start=b['text'].index(a);pairs.append(dict(id=f'g{i+1}',source=[[start,start+len(a)]],target=[[pos,pos+len(c)]]));pos+=len(c)+1
 items.append(dict(id=b['id'],translation=dict(text=target,pairs=pairs)))
send('translate',blocks=items)
# Demonstrate a real user revision and its preserved old version.
b=next(b for b in d['blocks'] if b['text'].startswith('A residual'))
d=user_edit(d['id'],dict(operation='translation',revision=d['revision'],block_id=b['id'],pair_id='g1',text='残差表示候选解代入控制方程后仍存在的不平衡程度。'))
for n,prefix,body,difficult in [(1,'The goal','阅读提示：这里的温度上限是硬约束，不能只比较功率。',False),(2,'Pump power falls','计算核对：(5−4)/5=20%；温度裕度则从30−27=3降至30−29=1。',False),(3,'Sensor uncertainty','待追问：如果传感器存在误差，1 °C的裕度是否仍然足够？原文没有给出误差范围。',True)]:
 b=next(b for b in d['blocks'] if b['text'].startswith(prefix));s,e=b['translation']['pairs'][0]['source'][0]
 d=user_edit(d['id'],dict(operation='note',revision=d['revision'],note=dict(id=f'n{n}',block_id=b['id'],side='source',start=s,end=e,quote=b['text'][s:e],text=body,difficult=difficult)))
send('review',blocks=[dict(id=b['id'],translation_hash=digest(b['translation']),note='第二遍对照自产原文：数字、单位、公式和否定限定保留。'+('修订后的残差定义保持原意。' if b['user_edited'] else '未将虚构示例写成真实实验。')) for b in d['blocks'] if b.get('translation')])
send('full_review',fingerprint=fingerprint(d),note='核对全篇多级目录、语义组、8术语、3笔记及修订记录；20%和温度裕度计算一致；全部数据明确标为虚构。')
r=export_html(d['id']);shutil.copy2(r['path'],repo/'examples/reading-showcase.html')
for name in ['reading-showcase.md','cooling-workflow.png']:shutil.copy2(work/name,repo/'examples'/name)
(repo/'.verification/showcase-result.json').write_text(json.dumps(dict(id=d['id'],**r),ensure_ascii=False,indent=2));print(json.dumps(dict(id=d['id'],**validate(d)),ensure_ascii=False))
