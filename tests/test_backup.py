import importlib.util
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import app

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


if __name__ == '__main__':
    unittest.main()
