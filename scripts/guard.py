#!/usr/bin/env python3
"""조율자 채팅이 코드를 직접 고치지 못하게 하는 훅입니다. 설정은 .claude/settings.json에 있습니다.

  guard.py mark   UserPromptSubmit: Orca 작업 세션의 첫 지시를 알아보고 그 세션을 기록합니다.
  guard.py check  PreToolUse: 기록된 작업 세션은 통과시키고, 그 외(조율자)는 허용 목록 밖의 수정·명령을 거부합니다.

차단한 기록은 ~/.cache/wokkiri-guard/log에 남습니다.
"""
import json
import os
import re
import shlex
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get('WOKKIRI_GUARD_DIR', Path.home() / '.cache' / 'wokkiri-guard'))
# Orca가 작업 세션에 처음 보내는 지시의 첫 문장입니다. Orca가 문구를 바꾸면 작업 세션도 못 알아봐서 막히므로(멈추는 쪽으로 실패) 그때 여기를 고칩니다.
WORKER_PROMPT = re.compile(r'\A\s*You are working inside Orca, a multi-agent IDE\.\s+You are a dispatched worker\.')
DOC_FILES = {'AGENTS.md', 'HANDOFF.md', 'README.md', 'orchestration.json'}
DOC_DIRS = ('docs/',)
SAFE_GIT = {'status', 'diff', 'log', 'show', 'rev-parse', 'ls-files', 'check-ignore', 'add', 'commit'}
SAFE_ORCA = {'orchestration', 'skills'}
SAFE_PLAIN = {'ls', 'pwd'}
USE_WORKER = '세션은 scripts/worker.py <reader|coder|verifier> <지시서>로 띄워 주세요. 모델은 orchestration.json에서 정합니다.'


def log(event, session, detail=''):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'log').open('a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")}\t{event}\t{session}\t{detail[:200]}\n')


def marker(session):
    name = re.sub(r'[^A-Za-z0-9_-]', '', session or '')
    return STATE / f'{name}.worker' if name else None


def is_worker(session):
    m = marker(session)
    return bool(m and m.exists())


def mark(event):
    m = marker(event.get('session_id'))
    if m and WORKER_PROMPT.match(event.get('prompt') or ''):
        STATE.mkdir(parents=True, exist_ok=True)
        m.touch()
        log('worker', event.get('session_id'))


def real(path, cwd):
    p = Path(path).expanduser()
    return Path(os.path.realpath(p if p.is_absolute() else Path(cwd or ROOT) / p))


def path_ok(path, cwd):
    p = real(path, cwd)
    root = Path(os.path.realpath(ROOT))
    if p == root or root in p.parents:
        rel = p.relative_to(root).as_posix()
        return rel in DOC_FILES or rel.startswith(DOC_DIRS)
    # 저장소 밖은 임시 폴더(지시서)와 메모리 폴더만 허용합니다. ~/.claude/settings.json 같은 설정은 막힙니다.
    allowed = [Path(os.path.realpath(d)) for d in ('/private/tmp', tempfile.gettempdir(), Path.home() / '.claude' / 'projects', os.environ.get('ORCA_CHAT_SCRATCH_DIR') or '/nonexistent')]
    return any(d == p or d in p.parents for d in allowed)


def bash_denied(command, cwd):
    """허용 목록 밖이면 이유를, 허용이면 None을 돌려줍니다. 연결·치환·리다이렉션 없는 단일 명령만 봅니다."""
    if re.search(r'[\n\r$`]', command):
        return '조율자는 줄바꿈·변수·명령 치환이 없는 단일 명령만 쓸 수 있어요.'
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split, lex.commenters = True, ''
    try:
        tokens = list(lex)
    except ValueError:
        return '명령을 해석하지 못했어요.'
    if not tokens or any(set(t) <= set('();<>|&') for t in tokens) or '=' in tokens[0]:
        return '조율자는 ; && | > < & 나 변수 지정이 없는 단일 명령만 쓸 수 있어요.'
    head, args = tokens[0], tokens[1:]
    if head in SAFE_PLAIN:
        return None
    if head == 'git':
        if not args or args[0] not in SAFE_GIT or any(a.startswith('--output') for a in args):
            return '조율자의 git은 status·diff·log·show·add·commit 등 기본 명령만 쓸 수 있어요.'
        return None
    if head == 'orca' or head == os.environ.get('ORCA_CLI_COMMAND'):
        if not args or args[0] not in SAFE_ORCA:
            return 'orca는 orchestration·skills 명령만 쓸 수 있어요.'
        if args[:2] == ['orchestration', 'worker-start']:
            return USE_WORKER
        return None
    script = head
    if head in ('python3', 'python') and args:
        script = args[0]
    if real(script, cwd) == Path(os.path.realpath(ROOT / 'scripts' / 'worker.py')):
        return None
    return '조율자는 코드를 읽거나 고치거나 실행하지 않고 작업 세션에 맡겨요. ' + USE_WORKER


def decide(event):
    """막아야 하면 이유를, 통과시키면 None을 돌려줍니다."""
    if is_worker(event.get('session_id')):
        return None
    tool, args, cwd = event.get('tool_name'), event.get('tool_input') or {}, event.get('cwd')
    if tool in ('Edit', 'Write', 'NotebookEdit'):
        path = args.get('file_path') or args.get('notebook_path') or ''
        if not path_ok(path, cwd):
            return f'조율자는 docs/, AGENTS.md, HANDOFF.md, README.md, orchestration.json만 고칠 수 있어요. 코드 변경은 scripts/worker.py coder로 작업 세션에 맡겨 주세요. ({path})'
    elif tool == 'Bash':
        return bash_denied(args.get('command') or '', cwd)
    return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode not in ('mark', 'check'):
        sys.exit('사용: guard.py mark|check')
    event = json.load(sys.stdin)
    if mode == 'mark':
        return mark(event)
    reason = decide(event)
    if reason:
        args = event.get('tool_input') or {}
        log('deny', event.get('session_id'), f'{event.get("tool_name")} {args.get("file_path") or args.get("command") or ""}')
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
