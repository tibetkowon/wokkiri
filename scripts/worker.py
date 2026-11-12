#!/usr/bin/env python3
"""역할별 모델로 Orca 작업 세션을 새로 띄웁니다. 모델은 orchestration.json에서 바꿉니다.

사용: scripts/worker.py <reader|coder|verifier> <지시서 파일> [worker-start 추가 옵션]
"""
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
roles = json.loads((root / 'orchestration.json').read_text(encoding='utf-8'))
if len(sys.argv) < 3 or sys.argv[1] not in roles:
    sys.exit(f'사용: {sys.argv[0]} <{"|".join(roles)}> <지시서 파일> [worker-start 추가 옵션]')
role, spec, extra = roles[sys.argv[1]], os.path.abspath(sys.argv[2]), sys.argv[3:]
if any(a.split('=')[0] in ('--agent', '--model', '--effort', '--terminal') for a in extra):
    sys.exit('에이전트와 모델은 orchestration.json에서만 바꿀 수 있어요.')
cmd = [os.environ.get('ORCA_CLI_COMMAND', 'orca'), 'orchestration', 'worker-start', '--spec-file', spec, '--agent', role['agent']]
for flag in ('model', 'effort'):
    if role.get(flag):
        cmd += [f'--{flag}', role[flag]]
if '--worktree' not in extra:
    cmd += ['--worktree', 'current']
sys.exit(subprocess.call(cmd + extra + ['--json'], cwd=root))
