#!/bin/bash
# VM에 /usr/local/bin/wokkiri-deploy 로 설치합니다(root 소유, docs/deploy.md 9절). 배포 키의 강제 명령으로 실행됩니다.
# 표준입력의 tar.gz를 새 릴리스 폴더에 풀고, DB를 백업하고, current 링크를 바꿔 재시작합니다.
# 상태 확인이 실패하면 이전 릴리스로 되돌리고 0이 아닌 코드로 끝납니다. 인자와 환경변수는 읽지 않습니다.
set -euo pipefail
umask 022
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C

BASE=/opt/wokkiri/releases
DATA=/opt/wokkiri/data
MAX_PACKED=$((20 * 1024 * 1024))
MAX_UNPACKED=$((100 * 1024 * 1024))
KEEP=5
PORT=8000   # /etc/wokkiri.env의 PORT와 같아야 합니다(배포 계정은 그 파일을 읽지 못합니다).
ID='^[0-9]{8}-[0-9]{6}$'

die() { printf '배포 실패: %s\n' "$*" >&2; exit 1; }

point() {   # current 링크를 원자적으로 바꿉니다.
    ln -sfn "$1" "$BASE/.current.new"
    mv -T "$BASE/.current.new" "$BASE/current"
}

healthy() {
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        curl -fsS -o /dev/null --max-time 5 -H "Host: $host" "http://127.0.0.1:$PORT/api/state" 2>/dev/null && return 0
        sleep 2
    done
    return 1
}

# 앱은 WOKKIRI_HOST와 같은 Host만 받습니다. 설치할 때 root가 적어 둔 값을 씁니다.
host=$(head -n 1 /etc/wokkiri-deploy.host)
[[ $host =~ ^[A-Za-z0-9.-]+$ ]] || die '/etc/wokkiri-deploy.host 값이 올바르지 않습니다.'

exec 9> "$BASE/.lock"
flock -n 9 || die '다른 배포가 진행 중입니다.'

prev=$(readlink "$BASE/current") || die 'current 링크가 없습니다.'
[[ $prev =~ $ID && -d $BASE/$prev ]] || die 'current 링크가 릴리스 폴더를 가리키지 않습니다.'

work=$(mktemp -d "$BASE/.incoming.XXXXXX")
new=
trap 'rm -rf -- "$work" ${new:+"$new"}' EXIT
src=$work/src.tar.gz

# 입력을 끝내지 않는 연결이 잠금을 쥐고 있지 못하게 받는 시간을 제한합니다.
timeout 300 head -c $((MAX_PACKED + 1)) > "$src" || die '입력을 5분 안에 받지 못했습니다.'
size=$(stat -c %s "$src")
[ "$size" -gt 0 ] || die '입력이 없습니다. 이 계정은 배포 압축 파일만 받습니다.'
[ "$size" -le "$MAX_PACKED" ] || die '압축 파일이 너무 큽니다.'
unpacked=$(gzip -dc "$src" 2>/dev/null | head -c $((MAX_UNPACKED + 1)) | wc -c) || true
[ "$unpacked" -le "$MAX_UNPACKED" ] || die '압축을 푼 크기가 너무 큽니다.'

names=$(tar -tzf "$src") || die '압축 파일을 읽을 수 없습니다.'
kinds=$(tar -tvzf "$src") || die '압축 파일을 읽을 수 없습니다.'
[ -n "$names" ] || die '압축 파일이 비어 있습니다.'
if grep -Eq '^/|(^|/)\.\.(/|$)' <<< "$names"; then die '절대 경로나 ..이 든 경로는 받지 않습니다.'; fi
if grep -vq '^[-d]' <<< "$kinds"; then die '일반 파일과 폴더만 받습니다(링크·장치 파일 거부).'; fi

id=$(date -u +%Y%m%d-%H%M%S)
[ ! -e "$BASE/$id" ] || die '같은 초에 만든 릴리스가 이미 있습니다. 다시 시도해 주세요.'
new=$BASE/$id
mkdir "$new"
tar --no-same-owner --no-same-permissions -xzf "$src" -C "$new"
[ -f "$new/app.py" ] && [ -f "$new/scripts/backup.py" ] || die '앱 파일이 없는 압축입니다.'
[ ! -e "$new/data" ] && [ ! -L "$new/data" ] || die 'data가 든 압축은 받지 않습니다.'
ln -s "$DATA" "$new/data"   # 앱은 자기 폴더의 data/를 씁니다. 실제 데이터는 릴리스 밖에 한 벌만 있습니다.

# 지금 돌고 있는 릴리스의 백업 스크립트로 백업합니다. 실패하면 아무것도 바꾸지 않고 멈춥니다.
sudo -n -u wokkiri /usr/bin/python3 "$BASE/current/scripts/backup.py" || die '배포 전 DB 백업에 실패했습니다.'

point "$id"
if sudo -n /usr/bin/systemctl restart wokkiri && healthy; then
    kept=$new
    new=
else
    point "$prev"
    sudo -n /usr/bin/systemctl restart wokkiri || true
    if healthy; then
        die "새 릴리스가 응답하지 않아 이전 릴리스($prev)로 되돌렸습니다. 서비스는 정상입니다."
    fi
    die "새 릴리스가 응답하지 않아 이전 릴리스($prev)로 되돌렸지만 서비스가 아직 응답하지 않습니다. VM에서 확인해 주세요."
fi

# 오래된 릴리스는 이름(시각)순으로 최근 KEEP개만 남깁니다.
for old in $(ls -1 "$BASE" | grep -E "$ID" | sort | head -n -"$KEEP"); do
    [ "$BASE/$old" = "$kept" ] || rm -rf -- "${BASE:?}/$old"
done
printf '배포 완료: %s (이전 %s)\n' "$id" "$prev"
