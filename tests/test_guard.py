import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = tempfile.TemporaryDirectory()
os.environ['WOKKIRI_GUARD_DIR'] = STATE.name
spec = importlib.util.spec_from_file_location('guard', ROOT / 'scripts' / 'guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

PREAMBLE = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\nYour coordinator's Orca session ID is: x"


def edit(path, session='coord', tool='Edit'):
    return dict(session_id=session, tool_name=tool, tool_input=dict(file_path=path), cwd=str(ROOT))


def bash(command, session='coord'):
    return dict(session_id=session, tool_name='Bash', tool_input=dict(command=command), cwd=str(ROOT))


class Guard(unittest.TestCase):
    def test_coordinator_edits_only_docs(self):
        for path in ['domain.py', 'web/app.js', 'tests/test_domain.py', '.claude/settings.json', 'scripts/guard.py',
                     '../outside.md', str(Path.home() / '.claude' / 'settings.json'), '/etc/hosts', '']:
            self.assertIsNotNone(guard.decide(edit(path)), path)
        for path in ['docs/x.md', 'AGENTS.md', 'HANDOFF.md', 'README.md', 'orchestration.json',
                     '/private/tmp/spec.md', str(Path.home() / '.claude' / 'projects' / 'p' / 'memory' / 'a.md')]:
            self.assertIsNone(guard.decide(edit(path)), path)
        self.assertIsNotNone(guard.decide(edit('domain.py', tool='NotebookEdit')))

    def test_symlink_cannot_reach_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'link.py'
            link.symlink_to(ROOT / 'domain.py')
            self.assertIsNotNone(guard.decide(edit(str(link))))

    def test_bash_allow_list(self):
        for command in ['git status', 'git diff --stat', 'git add -A', 'git commit -m "a; b" -m "Co-Authored-By: x"', 'ls docs', 'pwd',
                        'orca orchestration worker-list --json', 'orca skills get orchestration',
                        'scripts/worker.py coder /tmp/s.md', 'python3 scripts/worker.py verifier /tmp/s.md --keep-open']:
            self.assertIsNone(guard.decide(bash(command)), command)
        for command in ['cat domain.py', 'sed -i s/a/b/ domain.py', 'python3 -c "print(1)"', 'python3 -m unittest', 'echo hi',
                        'git -c core.pager=x log', 'git diff --output=x', 'git push', 'git status; rm x', 'git status && ls', 'ls | wc',
                        'ls > x', 'ls x# ; rm y', 'git log $(whoami)', 'git log `x`', 'FOO=1 git status', 'git status\nrm x',
                        'orca orchestration worker-start --spec x --agent claude', 'orca exec --command x', 'orca terminal send x', '']:
            self.assertIsNotNone(guard.decide(bash(command)), command)

    def test_worker_session_is_recognized_and_passes(self):
        self.assertIsNotNone(guard.decide(edit('domain.py', 'w1')))
        guard.mark(dict(session_id='w1', prompt='안녕하세요 코드를 고쳐 주세요'))
        guard.mark(dict(session_id='w1', prompt='From: STRUCTWORKER (worker_done)\nSubject: done'))
        self.assertIsNotNone(guard.decide(edit('domain.py', 'w1')))
        guard.mark(dict(session_id='w1', prompt=PREAMBLE))
        self.assertIsNone(guard.decide(edit('domain.py', 'w1')))
        self.assertIsNone(guard.decide(bash('python3 -m unittest', 'w1')))
        self.assertIsNotNone(guard.decide(edit('domain.py', 'w2')))
        guard.mark(dict(session_id='', prompt=PREAMBLE))
        self.assertIsNotNone(guard.decide(edit('domain.py', '')))

    def test_worker_script_cannot_override_models(self):
        for flag in ['--model', '--agent=codex', '--effort', '--terminal']:
            run = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'worker.py'), 'coder', '/tmp/s.md', flag, 'x'], capture_output=True, text=True)
            self.assertEqual(run.returncode, 1, flag)
            self.assertIn('orchestration.json', run.stderr)


if __name__ == '__main__':
    unittest.main()
