import base64
import contextlib
import importlib.util
import io
import json
import os
import runpy
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import app
from domain import apply

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('backup', ROOT / 'scripts' / 'backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def body(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute('SELECT body FROM state WHERE id=1').fetchone()[0]


class BackupChecks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db, self.dir = Path(tmp.name) / 'live.sqlite3', Path(tmp.name) / 'backups'
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
            db.execute("INSERT INTO state VALUES (1, '첫 상태')")
            db.commit()

    def test_copy_matches_and_only_owner_can_read(self):
        self.assertEqual(backup.DB, app.DB)
        # 서버가 쓰는 중(커밋 전 변경이 있는 연결이 열려 있음)에도 마지막으로 커밋된 내용이 복사됩니다.
        with closing(sqlite3.connect(self.db)) as live:
            live.execute("UPDATE state SET body='커밋 전'")
            made = backup.backup(self.db, self.dir, now=1000000000)
        self.assertRegex(made.name, r'^wokkiri-\d{8}-\d{6}\.db$')
        self.assertEqual(body(made), '첫 상태')
        self.assertEqual(stat.S_IMODE(made.stat().st_mode), 0o600)
        # 같은 초에 다시 돌려도 있던 백업을 덮어쓰거나 지우지 않습니다.
        with self.assertRaises(FileExistsError):
            backup.backup(self.db, self.dir, now=1000000000)
        self.assertEqual(body(made), '첫 상태')

    def test_backup_contains_photos(self):
        # 서버가 올린 사진은 같은 파일의 사진 테이블에 있어 백업에 함께 담깁니다.
        png = b'\x89PNG\r\n\x1a\n' + b'photo'
        with mock.patch.object(app, 'DB', self.dir.parent / 'server.sqlite3'), mock.patch.object(app, 'DEMO', True):
            app.prepare()
            app.change(lambda state, now: apply(state, 'ga', dict(action='guide', league='local-demo', round='0', version=0, text='WOD', photo='data:image/png;base64,' + base64.b64encode(png).decode()), now))
            made = backup.backup(app.DB, self.dir, now=1000000000)
        with closing(sqlite3.connect(made)) as db:
            pid = json.loads(db.execute('SELECT body FROM state').fetchone()[0])['leagues'][0]['rounds'][0]['guide']['photo']
            self.assertEqual(db.execute('SELECT id, league, mime, body FROM photos').fetchall(), [(pid, 'local-demo', 'image/png', png)])

    def test_keeps_newest_and_fails_cleanly(self):
        made = [backup.backup(self.db, self.dir, keep=2, now=1000000000 + i * 86400) for i in range(4)]
        self.assertEqual(sorted(self.dir.iterdir()), made[2:])
        with self.assertRaises(FileNotFoundError):
            backup.backup(self.dir / 'missing.sqlite3', self.dir, keep=2)
        with self.assertRaises(ValueError):
            backup.backup(self.db, self.dir, keep=0)
        # 깨진 DB는 백업 파일을 남기지 않고 실패하며, 있던 백업은 그대로입니다.
        broken = self.dir.parent / 'broken.sqlite3'
        broken.write_bytes(b'not a database' * 100)
        with self.assertRaises(sqlite3.Error):
            backup.backup(broken, self.dir, keep=2)
        self.assertEqual(sorted(self.dir.iterdir()), made[2:])

    def test_only_own_backups_are_removed(self):
        self.dir.mkdir()
        # 형식이 다른 이름, 폴더, 링크는 백업이 아니므로 지우지 않고 보관 개수에도 넣지 않습니다.
        others = [self.dir / name for name in ('wokkiri-000-not-backup.db', 'wokkiri-important.db', 'wokkiri-20010909-014640.db.bak', 'xwokkiri-20010909-014640.db', 'notes.txt')]
        for path in others:
            path.write_text('보존')
        (self.dir / 'wokkiri-20000101-000000.db').mkdir()
        (self.dir / 'wokkiri-20000102-000000.db').symlink_to(others[0])
        kept = set(self.dir.iterdir())
        made = [backup.backup(self.db, self.dir, keep=1, now=1000000000 + i * 86400) for i in range(3)]
        self.assertEqual(set(self.dir.iterdir()), kept | {made[-1]})
        self.assertEqual([p.read_text() for p in others], ['보존'] * 5)
        self.assertEqual(body(made[-1]), '첫 상태')
        # 시계가 뒤로 가 이름이 더 오래돼 보여도 방금 만든 백업은 남습니다.
        early = backup.backup(self.db, self.dir, keep=1, now=1000000000 - 86400)
        self.assertEqual(set(self.dir.iterdir()), kept | {early})

    def test_source_db_in_backup_folder_is_kept(self):
        # 원본 DB가 백업 폴더 안에 백업과 같은 이름 형식으로 있어도 지우지 않습니다.
        self.dir.mkdir()
        live = self.dir / 'wokkiri-19990101-000000.db'
        self.db.rename(live)
        made = [backup.backup(live, self.dir, keep=1, now=1000000000 + i * 86400) for i in range(2)]
        self.assertEqual(sorted(self.dir.iterdir()), [live, made[-1]])
        self.assertEqual((body(live), body(made[-1])), ('첫 상태', '첫 상태'))

    def test_command_line(self):
        run = lambda *args: subprocess.run([sys.executable, str(ROOT / 'scripts' / 'backup.py'), *args], capture_output=True, text=True)
        done = run('--db', str(self.db), '--dir', str(self.dir), '--keep', '1')
        self.assertEqual((done.returncode, done.stderr), (0, ''))
        self.assertEqual([str(p) for p in self.dir.iterdir()], [done.stdout.strip()])
        self.assertEqual(body(done.stdout.strip()), '첫 상태')
        failed = run('--db', str(self.dir / 'missing.sqlite3'), '--dir', str(self.dir))
        self.assertEqual(failed.returncode, 1)
        self.assertIn('백업 실패', failed.stderr)
        self.assertEqual(len(list(self.dir.iterdir())), 1)

    def test_upload_calls_gcloud_without_shell(self):
        made = backup.backup(self.db, self.dir, now=1000000000)
        with mock.patch.object(backup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            backup.upload(made, 'my-bucket')
        (argv,), options = run.call_args
        self.assertEqual(argv, ['gcloud', 'storage', 'cp', str(made), 'gs://my-bucket/'])
        self.assertFalse(options.get('shell'))
        # 버킷 이름이나 파일 이름이 형식에 맞지 않으면 명령을 부르지 않습니다.
        with mock.patch.object(backup.subprocess, 'run') as run:
            for path, bucket in ((made, 'x; echo y'), (made, 'gs://my-bucket'), (made, '-o'), (made, 'a/b'), (self.db, 'my-bucket')):
                with self.assertRaises(ValueError):
                    backup.upload(path, bucket)
        run.assert_not_called()
        # 상대 경로가 '-'로 시작해도 절대 경로로 바꿔 넘기므로 gcloud가 옵션으로 읽지 못합니다.
        odd = self.dir.parent / '-dir'
        odd.mkdir()
        (odd / 'wokkiri-20010909-014640.db').write_bytes(made.read_bytes())
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.dir.parent)
        with mock.patch.object(backup.subprocess, 'run') as run:
            backup.upload('-dir/wokkiri-20010909-014640.db', 'my-bucket')
        source = run.call_args.args[0][3]
        self.assertTrue(source.startswith('/') and source.endswith('/-dir/wokkiri-20010909-014640.db'), source)
        self.assertEqual(body(made), '첫 상태')

    def test_upload_failures_keep_local_copy_and_hide_output(self):
        # 명령줄 본문을 이 프로세스에서 돌려 gcloud 없음·시간 초과·실패 종료를 가짜로 일으킵니다.
        secret = 'Bearer ya29.secret'
        errors = (FileNotFoundError(2, 'No such file or directory', 'gcloud'), subprocess.TimeoutExpired(['gcloud', secret], 300, output=secret, stderr=secret), subprocess.CalledProcessError(7, ['gcloud', secret], output=secret, stderr=secret))
        for day, error in enumerate(errors):
            out, when = io.StringIO(), time.gmtime(1000000000 + day * 86400)  # 백업 이름이 초 단위라 실행마다 날짜를 다르게 줍니다.
            with mock.patch.object(sys, 'argv', ['backup.py', '--db', str(self.db), '--dir', str(self.dir), '--keep', '2']), mock.patch.dict(os.environ, WOKKIRI_BACKUP_BUCKET='my-bucket'), mock.patch('time.localtime', return_value=when), mock.patch('subprocess.run', side_effect=error) as run, contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
                runpy.run_path(str(ROOT / 'scripts' / 'backup.py'), run_name='__main__')
            made, message = Path(out.getvalue().strip()), stop.exception.code
            # 문자열로 끝내면 종료 코드는 1이고 그 문자열이 stderr에 나갑니다. 가짜 출력과 명령은 어디에도 나오지 않습니다.
            self.assertIsInstance(message, str)
            self.assertIn('버킷 올리기 실패', message)
            self.assertIn('종료 코드 7' if day == 2 else type(error).__name__, message)
            self.assertNotIn('ya29', message + out.getvalue())
            options = run.call_args.kwargs
            self.assertEqual((options['timeout'], options['stdout'], options['stderr']), (300, subprocess.DEVNULL, subprocess.DEVNULL))
            self.assertFalse(options.get('shell'))
            # 방금 만든 로컬 사본은 남고 보관 개수(2)도 지켜집니다.
            self.assertEqual(body(made), '첫 상태')
            self.assertEqual(len(list(self.dir.iterdir())), min(day + 1, 2))
            self.assertIn(made, self.dir.iterdir())

    def test_command_line_upload(self):
        # 가짜 gcloud를 PATH 맨 앞에 두어 실제 네트워크 없이 명령줄 동작을 봅니다.
        fake = self.dir.parent / 'bin'
        fake.mkdir()
        (fake / 'gcloud').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_LOG"\necho "Bearer ya29.secret"\necho "Bearer ya29.secret" >&2\nexit "$FAKE_EXIT"\n')
        (fake / 'gcloud').chmod(0o755)
        log = self.dir.parent / 'log'
        env = {**os.environ, 'PATH': f"{fake}{os.pathsep}{os.environ['PATH']}", 'FAKE_LOG': str(log), 'FAKE_EXIT': '0'}
        env.pop('WOKKIRI_BACKUP_BUCKET', None)
        run = lambda env: subprocess.run([sys.executable, str(ROOT / 'scripts' / 'backup.py'), '--db', str(self.db), '--dir', str(self.dir), '--keep', '2'], capture_output=True, text=True, env=env)
        # 환경변수가 없으면 올리지 않습니다.
        self.assertEqual(run(env).returncode, 0)
        self.assertFalse(log.exists())
        time.sleep(1.1)  # 백업 이름이 초 단위라 같은 초에는 다시 만들 수 없습니다.
        done = run({**env, 'WOKKIRI_BACKUP_BUCKET': 'my-bucket'})
        self.assertEqual((done.returncode, done.stderr), (0, ''))
        self.assertEqual(log.read_text().splitlines(), ['storage', 'cp', done.stdout.strip(), 'gs://my-bucket/'])
        time.sleep(1.1)
        # 올리기가 실패하면 0이 아닌 코드로 끝나지만 로컬 백업은 남고 보관 개수 정리도 평소처럼 됩니다.
        failed = run({**env, 'WOKKIRI_BACKUP_BUCKET': 'my-bucket', 'FAKE_EXIT': '1'})
        self.assertEqual(failed.returncode, 1)
        self.assertIn('버킷 올리기 실패(종료 코드 1)', failed.stderr)
        # gcloud가 낸 출력(인증 정보가 섞일 수 있음)은 성공·실패 어느 쪽에도 내보내지 않습니다.
        self.assertNotIn('ya29', done.stdout + done.stderr + failed.stdout + failed.stderr)
        self.assertEqual(body(failed.stdout.strip()), '첫 상태')
        self.assertEqual(len(list(self.dir.iterdir())), 2)
        # 버킷 이름이 형식에 맞지 않으면 gcloud를 부르지 않고 실패합니다.
        log.unlink()
        time.sleep(1.1)
        self.assertEqual(run({**env, 'WOKKIRI_BACKUP_BUCKET': 'bad name'}).returncode, 1)
        self.assertFalse(log.exists())


if __name__ == '__main__':
    unittest.main()
