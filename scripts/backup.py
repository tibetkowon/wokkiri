#!/usr/bin/env python3
"""운영 DB 백업: python3 scripts/backup.py [--db DB경로] [--dir 백업폴더] [--keep 보관개수]

sqlite3 backup API로 복사하므로 서버가 실행 중이어도 안전합니다. <백업폴더>/wokkiri-YYYYmmdd-HHMMSS.db(소유자만 읽기)로 만들고
보관 개수를 넘으면 오래된 것부터 지웁니다. 복사나 검사가 실패하면 만들던 파일을 지우고 0이 아닌 코드로 끝납니다.
환경변수 WOKKIRI_BACKUP_BUCKET이 있으면 방금 만든 파일을 gs://<버킷>/ 에도 올립니다. 올리기가 실패해도 로컬 백업은 남고 0이 아닌 코드로 끝나며, gcloud의 출력은 내보내지 않습니다.
"""
import argparse
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import DB  # 서버가 쓰는 경로와 같게(WOKKIRI_DB 포함)

NAME = re.compile(r'wokkiri-\d{8}-\d{6}\.db')
BUCKET = re.compile(r'[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]')


def backup(db, folder, keep=14, now=None):
    db, folder = Path(db), Path(folder)
    if keep < 1:
        raise ValueError('보관 개수는 1 이상이어야 합니다.')
    if not db.is_file():
        raise FileNotFoundError(f'DB 파일이 없습니다: {db}')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / time.strftime('wokkiri-%Y%m%d-%H%M%S.db', time.localtime(now))
    # 0600으로 먼저 만들어 두어 복사 도중에도 다른 사용자가 읽지 못합니다. 같은 초에 이미 있으면 덮어쓰지 않고 실패합니다.
    os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    try:
        with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('백업 파일 검사에 실패했습니다.')
    except BaseException:
        target.unlink()
        raise
    # 이 스크립트가 만든 이름 형식의 일반 파일만 지웁니다. 방금 만든 백업과 원본 DB는 대상에서 뺍니다. 이름이 시각순이라 정렬하면 오래된 것이 앞입니다.
    olds = sorted(p for p in folder.iterdir() if NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink() and p != target and not p.samefile(db))
    for old in olds[:max(0, len(olds) - (keep - 1))]:
        old.unlink()
    return target


def upload(path, bucket):
    path = Path(os.path.abspath(path))  # 항상 '/'로 시작해 gcloud가 경로를 옵션으로 읽지 못합니다.
    if not BUCKET.fullmatch(bucket) or not NAME.fullmatch(path.name):
        raise ValueError('버킷 이름이나 백업 파일 이름이 올바르지 않습니다.')
    # 서버 계정은 홈 폴더에 쓸 수 없어 gcloud 설정 폴더를 임시 폴더로 둡니다. 인증은 VM의 서비스 계정을 씁니다.
    # gcloud 출력에는 인증 정보가 섞일 수 있어 받지 않고 버립니다.
    with tempfile.TemporaryDirectory() as config:
        subprocess.run(['gcloud', 'storage', 'cp', str(path), f'gs://{bucket}/'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300, env={**os.environ, 'CLOUDSDK_CONFIG': config})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='와끼리 DB 백업')
    parser.add_argument('--db', default=str(DB), help='DB 경로(기본: 서버가 쓰는 경로)')
    parser.add_argument('--dir', help='백업 폴더(기본: DB 폴더 아래 backups)')
    parser.add_argument('--keep', type=int, default=14, help='보관 개수(기본 14)')
    args = parser.parse_args()
    try:
        made = backup(args.db, args.dir or Path(args.db).parent / 'backups', args.keep)
    except (OSError, ValueError, sqlite3.Error) as err:
        sys.exit(f'백업 실패: {err}')
    print(made)
    bucket = os.environ.get('WOKKIRI_BACKUP_BUCKET')
    if bucket:
        try:
            upload(made, bucket)
        except (OSError, ValueError, subprocess.SubprocessError) as err:
            # 예외 문구에는 명령과 출력이 담길 수 있어 종료 코드나 예외 종류만 남깁니다.
            reason = f'종료 코드 {err.returncode}' if isinstance(err, subprocess.CalledProcessError) else type(err).__name__
            sys.exit(f'버킷 올리기 실패({reason}). 로컬 백업은 남아 있습니다. VM에서 gcloud storage cp를 직접 실행해 원인을 확인하세요.')
