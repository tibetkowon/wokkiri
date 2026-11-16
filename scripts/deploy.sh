#!/bin/bash
# 현재 커밋을 VM에 배포합니다(이 컴퓨터 또는 GitHub Actions): docs/deploy.md 9절.
# 환경변수: DEPLOY_HOST(VM 주소), DEPLOY_KEY_FILE(배포 개인키 경로), DEPLOY_KNOWN_HOSTS_FILE(VM 호스트 키가 든 known_hosts 경로), DEPLOY_URL(배포 뒤 확인할 https 주소)
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DEPLOY_HOST:?}" "${DEPLOY_KEY_FILE:?}" "${DEPLOY_KNOWN_HOSTS_FILE:?}" "${DEPLOY_URL:?}"

python3 -m unittest discover -s tests
[ -z "$(git status --porcelain)" ] || { echo '커밋하지 않은 변경이 있습니다. 커밋한 내용만 배포됩니다.' >&2; exit 1; }
echo "배포할 커밋: $(git rev-parse HEAD)"

# 호스트 키는 주어진 known_hosts로만 검증하고, 내 ssh 설정·에이전트의 다른 키는 쓰지 않습니다.
git archive --format=tar.gz HEAD | ssh -F /dev/null -i "$DEPLOY_KEY_FILE" -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$DEPLOY_KNOWN_HOSTS_FILE" -o GlobalKnownHostsFile=/dev/null \
    -o ConnectTimeout=20 -o LogLevel=ERROR "wokkiri-deploy@$DEPLOY_HOST"

curl -fsS -o /dev/null --retry 5 --retry-delay 3 --retry-all-errors --max-time 10 "$DEPLOY_URL"
echo '배포 완료: https 응답 정상'
