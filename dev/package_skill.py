"""Build a relocatable skill zip using a publish allowlist."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root=Path(__file__).resolve().parents[1]
out=root/'dist';out.mkdir(exist_ok=True)
with ZipFile(out/'agent-paper-reader.zip','w',ZIP_DEFLATED) as z:
    for name in ['SKILL.md','LICENSE','THIRD_PARTY_NOTICES.md','scripts','references','agents','assets']:
        path=root/name
        for p in ([path] if path.is_file() else sorted(path.rglob('*'))):
            if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store':
                z.write(p,Path('agent-paper-reader')/p.relative_to(root))
print(out/'agent-paper-reader.zip')
