"""deploy/wokkiri-deploy.sh를 임시 폴더와 명령 대역(sudo·curl·sleep)으로 돌려 봅니다. GNU 도구(flock, timeout, mv -T)가 필요해 리눅스에서만 실행됩니다."""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'deploy' / 'wokkiri-deploy.sh'
OLD = '20200101-000000'
# sudo는 받은 명령을 적기만 하고, fail-backup·fail-restart 파일이 있으면 백업 단위 시작·재시작을 실패시킵니다. curl은 current 릴리스에 healthy 파일이 있을 때만 성공합니다.
# mv는 fail-restore 파일이 있으면 current가 새 릴리스로 바뀐 뒤의 교체(되돌리기)만 실패시킵니다.
STUBS = {
    'sudo': '#!/bin/sh\necho "$*" >> "$WOKKIRI_DEPLOY_ROOT/sudo.log"\ncase "$*" in *wokkiri-backup*) [ ! -e "$WOKKIRI_DEPLOY_ROOT/fail-backup" ];; *restart*) [ ! -e "$WOKKIRI_DEPLOY_ROOT/fail-restart" ];; esac\n',
    'mv': '#!/bin/sh\n[ -e "$WOKKIRI_DEPLOY_ROOT/fail-restore" ] && [ "$(readlink "$WOKKIRI_DEPLOY_ROOT/opt/wokkiri/releases/current")" != %s ] && exit 1\nPATH=/usr/bin:/bin exec mv "$@"\n' % OLD,
    'curl': '#!/bin/sh\n[ -e "$WOKKIRI_DEPLOY_ROOT/opt/wokkiri/releases/current/healthy" ]\n',
    'sleep': '#!/bin/sh\n',
}


def archive(files, extra=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, text in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text.encode()))
        for info in extra:
            tar.addfile(info)
    return buf.getvalue()


def member(name, kind=tarfile.REGTYPE, link=''):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, link
    return info


APP = {'app.py': 'print(1)', 'scripts/backup.py': ''}
GOOD = {**APP, 'healthy': ''}


@unittest.skipUnless(sys.platform == 'linux', 'GNU 도구가 필요합니다.')
class DeployChecks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.releases = self.root / 'opt/wokkiri/releases'
        (self.releases / OLD).mkdir(parents=True)
        (self.releases / OLD / 'healthy').touch()
        (self.releases / 'current').symlink_to(OLD)
        self.db = self.root / 'opt/wokkiri/data/wokkiri.sqlite3'
        self.db.parent.mkdir()
        self.db.write_bytes(b'db-bytes')
        (self.root / 'etc').mkdir()
        (self.root / 'etc/wokkiri-deploy.host').write_text('wokkiri.example.com\n')
        (self.root / 'bin').mkdir()
        for name, text in STUBS.items():
            (self.root / 'bin' / name).write_text(text)
            (self.root / 'bin' / name).chmod(0o755)

    def deploy(self, data):
        return subprocess.run(['bash', str(SCRIPT)], input=data, capture_output=True, env={**os.environ, 'WOKKIRI_DEPLOY_ROOT': str(self.root)})

    def current(self):
        return os.readlink(self.releases / 'current')

    def names(self):
        return sorted(p.name for p in self.releases.iterdir() if not p.name.startswith('.') and p.name != 'current')

    def calls(self):
        log = self.root / 'sudo.log'
        return log.read_text().splitlines() if log.exists() else []

    def test_good_archive_becomes_current_after_backup(self):
        done = self.deploy(archive(GOOD))
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        new = self.current()
        self.assertNotEqual(new, OLD)
        self.assertEqual(self.names(), sorted([OLD, new]))
        self.assertEqual(os.readlink(self.releases / new / 'data'), str(self.db.parent))
        self.assertEqual(self.calls(), ['-n /usr/bin/systemctl start wokkiri-backup.service', '-n /usr/bin/systemctl restart wokkiri'])

    def test_hostile_archives_change_nothing(self):
        cases = {
            '심볼릭 링크': archive(GOOD, [member('link', tarfile.SYMTYPE, '/etc/passwd')]),
            '하드 링크': archive(GOOD, [member('hard', tarfile.LNKTYPE, 'app.py')]),
            '절대 경로': archive(GOOD, [member('/tmp/wokkiri-test-evil')]),
            '.. 경로': archive(GOOD, [member('../../evil')]),
            'data 포함': archive({**GOOD, 'data/x': ''}),
            'gzip 아님': b'not a tarball',
            '빈 입력': b'',
        }
        for label, data in cases.items():
            with self.subTest(label):
                done = self.deploy(data)
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual((self.current(), self.names(), self.calls()), (OLD, [OLD], []))
                self.assertFalse((self.root / 'opt/evil').exists() or Path('/tmp/wokkiri-test-evil').exists())

    def test_sparse_archive_over_limit_is_rejected_by_size(self):
        # 앱 파일이 다 있고 압축도 작지만 풀면 200MB인 희소 파일이 들었습니다. 크기 합산 검사가 없으면 그대로 배포됩니다.
        src = self.root / 'src'
        (src / 'scripts').mkdir(parents=True)
        for name, text in GOOD.items():
            (src / name).write_text(text)
        os.truncate(src / 'healthy', 200 * 1024 * 1024)
        for form in ('gnu', 'posix'):
            with self.subTest(form):
                packed = self.root / f'{form}.tgz'
                subprocess.run(['tar', '--sparse', f'--format={form}', '-czf', str(packed), '-C', str(src), '.'], check=True)
                self.assertLess(packed.stat().st_size, 1024 * 1024)
                done = self.deploy(packed.read_bytes())
                self.assertNotEqual(done.returncode, 0)
                self.assertIn('압축을 푼 크기가 너무 큽니다.', done.stderr.decode())
                self.assertEqual((self.current(), self.names(), self.calls()), (OLD, [OLD], []))

    def test_backup_failure_keeps_link(self):
        (self.root / 'fail-backup').touch()
        done = self.deploy(archive(GOOD))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual((self.current(), self.names()), (OLD, [OLD]))
        self.assertNotIn('-n /usr/bin/systemctl restart wokkiri', self.calls())

    def test_unhealthy_release_rolls_back(self):
        done = self.deploy(archive(APP))  # healthy 파일이 없어 상태 확인이 실패합니다.
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('되돌렸습니다. 서비스는 정상입니다.', done.stderr.decode())
        self.assertEqual((self.current(), self.names()), (OLD, [OLD]))
        self.assertEqual(self.calls().count('-n /usr/bin/systemctl restart wokkiri'), 2)
        self.assertEqual(self.db.read_bytes(), b'db-bytes')


    def test_failed_restore_of_link_is_not_reported_as_restored(self):
        # 새 릴리스는 응답하지만 재시작이 실패해 되돌리려는데, 링크 되돌리기도 실패하는 경우입니다.
        (self.root / 'fail-restart').touch()
        (self.root / 'fail-restore').touch()
        done = self.deploy(archive(GOOD))
        err = done.stderr.decode()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('복원 실패: current 링크를', err)
        self.assertNotIn('서비스는 정상입니다', err)
        new = self.current()
        self.assertNotEqual(new, OLD)
        self.assertEqual(self.names(), sorted([OLD, new]))  # current가 가리키는 릴리스는 지우지 않습니다.
        self.assertEqual(self.calls().count('-n /usr/bin/systemctl restart wokkiri'), 1)

    def test_failed_restart_after_restore_is_reported(self):
        (self.root / 'fail-restart').touch()
        done = self.deploy(archive(GOOD))
        err = done.stderr.decode()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('서비스를 다시 시작하지 못했습니다. VM에서', err)
        self.assertNotIn('서비스는 정상입니다', err)
        self.assertEqual((self.current(), self.names()), (OLD, [OLD]))
        self.assertEqual(self.calls().count('-n /usr/bin/systemctl restart wokkiri'), 2)


if __name__ == '__main__':
    unittest.main()
