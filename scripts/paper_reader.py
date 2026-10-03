#!/usr/bin/env python3
"""Portable entrypoint. Keeps all writable state outside the installed skill."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

SKILL = Path(__file__).resolve().parents[1]
LOCK = SKILL / 'scripts/requirements.lock.txt'

def main():
    parser = argparse.ArgumentParser(description='Agent Paper Reader: offline HTML first; serve is optional')
    parser.add_argument('--workspace', required=True, type=Path, help='Persistent library and runtime directory outside this skill')
    parser.add_argument('command', help='setup, doctor, or reader CLI command (import/tasks/submit/validate/export/pair-offsets/serve)')
    parser.add_argument('args', nargs=argparse.REMAINDER)
    opt = parser.parse_args()
    workspace = opt.workspace.expanduser().resolve()
    if workspace == SKILL or workspace.is_relative_to(SKILL):
        parser.error('workspace must be outside the skill installation directory')
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or newer is required; do not change the global Python automatically')
    runtime = workspace / '.runtime'
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    stamp = runtime / 'dependencies.sha256'
    expected = hashlib.sha256(LOCK.read_bytes()).hexdigest()
    ready = python.is_file() and (runtime/'pyvenv.cfg').is_file() and stamp.is_file() and stamp.read_text() == expected
    env = dict(os.environ, PAPER_READER_WORKSPACE=str(workspace), PYTHONDONTWRITEBYTECODE='1',
               PYTHONPYCACHEPREFIX=str(workspace/'.cache/pycache'), PIP_CACHE_DIR=str(workspace/'.cache/pip'))
    if opt.command == 'doctor':
        emf_preview = {'ready': False, 'missing': ['emf preview check failed to run']}
        try:
            if str(SKILL / 'scripts') not in sys.path:
                sys.path.insert(0, str(SKILL / 'scripts'))
            from reader.emfconv import emf_env_status
            emf_preview = emf_env_status()
        except Exception as exc:
            emf_preview = {'ready': False, 'missing': [str(exc)]}
        # emf_preview is informational. PDF-only use stays ready when the venv matches the lock.
        print(json.dumps({'ready': ready, 'workspace': str(workspace), 'python': str(python),
                          'reader_assets': (SKILL/'assets/reader/index.html').is_file(),
                          'emf_preview': emf_preview}, ensure_ascii=False))
        return 0 if ready else 1
    if opt.command == 'setup':
        workspace.mkdir(parents=True, exist_ok=True)
        if not python.is_file() or not (runtime/'pyvenv.cfg').is_file():
            venv.EnvBuilder(with_pip=True).create(runtime)
        if not ready:
            result = subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(LOCK)], env=env)
            if result.returncode:
                return result.returncode
            stamp.write_text(expected)
        for name in ['data', 'exports', '.cache', 'submissions']:
            (workspace/name).mkdir(exist_ok=True)
        print(json.dumps({'ready': True, 'workspace': str(workspace)}, ensure_ascii=False))
        return 0
    if not ready:
        parser.error('runtime missing or outdated; run the same command with setup first')
    # A direct script path avoids dependence on cwd, PYTHONPATH or a global install.
    return subprocess.call([str(python), str(SKILL/'scripts/reader_entry.py'), opt.command, *opt.args], env=env)

if __name__ == '__main__':
    raise SystemExit(main())
