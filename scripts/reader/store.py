from pathlib import Path
import hashlib
import json
import os
import tempfile
from filelock import FileLock

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if not os.environ.get('PAPER_READER_WORKSPACE'):
    raise ValueError('Use paper_reader.py --workspace PATH; a writable workspace is required')
ROOT = Path(os.environ['PAPER_READER_WORKSPACE']).expanduser().resolve()
if ROOT == PACKAGE_ROOT or ROOT.is_relative_to(PACKAGE_ROOT):
    raise ValueError('Workspace must be outside the installed skill')
BUNDLE = PACKAGE_ROOT / 'assets' / 'reader'
DATA = ROOT / 'data'

class Conflict(ValueError):
    pass

def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()

def folder(doc_id):
    if not isinstance(doc_id, str) or len(doc_id) != 24 or any(c not in '0123456789abcdef' for c in doc_id):
        raise ValueError('Invalid document id')
    return DATA / doc_id

def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read(doc_id):
    return json.loads((folder(doc_id) / 'document.json').read_text('utf-8'))

def mutate(doc_id, revision, action, submission_id=None, payload=None):
    directory = folder(doc_id)
    with FileLock(str(directory / '.lock'), timeout=15):
        doc = read(doc_id)
        if submission_id:
            old = doc['submissions'].get(submission_id)
            if old:
                if old['hash'] != digest(payload):
                    raise Conflict('Submission id reused with different content')
                return doc
        if revision != doc['revision']:
            raise Conflict(f"Revision conflict: expected {revision}, current {doc['revision']}; reload and reconcile")
        action(doc)
        doc['revision'] += 1
        if submission_id:
            doc['submissions'][submission_id] = {'hash': digest(payload), 'revision': doc['revision']}
        atomic(directory / 'document.json', doc)
        return doc
