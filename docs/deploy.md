# 배포 (Google Cloud e2-micro + Caddy)

VM 한 대에 Python 서버, SQLite 파일, Caddy(HTTPS 자동)를 올리는 순서입니다.

**확인한 범위(2026-11-15, Debian 12, Python 3.11.2, Caddy 2.6.2):** 5절의 스왑·"처음 설치", 6절 Caddy(인증서 발급), 7절의 cron 등록·수동 백업·내 컴퓨터로 가져오기는 실제 VM에서 실행해 확인했습니다. 매일 18:30(UTC)의 cron 자동 실행은 2026-11-15 18:30(UTC)에 돌아 버킷에 파일이 생긴 것을 2026-11-16에 확인했습니다. **확인되지 않음:** 1~3절의 콘솔 메뉴(VM·방화벽·고정 IP·예산은 `gcloud` 명령으로 만들었습니다), 5절 "코드 갱신", 7절의 되돌리기 명령, 인증서 자동 갱신입니다. 7절의 오프사이트 백업(버킷·권한·범위·cron)과 "버킷 사본으로 복원"은 같은 날 실제로 실행해 확인했습니다. 9절(CI/CD)과 7절의 백업 단위는 2026-11-16에 기존 VM을 릴리스 폴더 구조로 옮기면서 확인했고(cron이 백업 단위를 시작하는 자동 실행은 아직 확인되지 않음), 그래서 5절 "처음 설치"의 릴리스 폴더 명령을 새 VM에서 처음부터 실행해 보지는 않았습니다(확인되지 않음).

아래에서 `wokkiri.example.com`은 실제 주소로, `VM이름`·`영역`은 만든 값으로 바꿉니다. `gcloud`의 기본 프로젝트가 다른 프로젝트면 `gcloud compute` 명령마다 `--project 프로젝트ID`를 붙입니다. 기준은 Debian 12 이미지, 코드 `/opt/wokkiri/releases/current`(릴리스 폴더를 가리키는 링크, 9절), 데이터 `/opt/wokkiri/data`입니다.

## 1. 결제 계정과 예산 알림

1. Google Cloud 콘솔에서 프로젝트를 만들고 결제 계정을 연결합니다.
2. 결제 → 예산 및 알림에서 월 예산(예: 크레딧 금액)을 만들고 50%·90%·100% 알림을 켭니다. 예산 알림은 알려 주기만 하고 과금을 멈추지는 않습니다.

## 2. VM 만들기

- Compute Engine → VM 인스턴스 → 만들기. 머신 유형 `e2-micro`, 리전은 무료 구간인 `us-west1`·`us-central1`·`us-east1` 중 하나입니다.
- 부팅 디스크는 Debian 12, 표준 영구 디스크 30GB 이하로 합니다.
- 무료 구간의 정확한 조건(디스크 종류, 외부 IP 요금, 나가는 트래픽 한도)은 만들기 전에 [Google Cloud 무료 등급 안내](https://cloud.google.com/free/docs/free-cloud-features)에서 확인해 주세요. 확인되지 않음.

## 3. 방화벽과 고정 IP

- VM을 만들 때 "HTTP 트래픽 허용"과 "HTTPS 트래픽 허용"을 켭니다(80, 443). 인증서 발급에 80도 필요합니다. 서버 포트(8000)는 열지 않습니다.
- VPC 네트워크 → IP 주소에서 VM의 외부 IP를 고정(정적)으로 바꿉니다. 그대로 두면 VM을 껐다 켤 때 주소가 바뀔 수 있습니다.

## 4. 주소 연결

셋 중 하나를 고릅니다. 어느 쪽이든 주소가 VM의 외부 IP를 가리키게 합니다.

- 가입 없이 쓰는 와일드카드 DNS `sslip.io`: IP의 점을 하이픈으로 바꾼 `1-2-3-4.sslip.io`가 `1.2.3.4`로 풀립니다. 이 이름으로 인증서 발급까지 확인했습니다. 주소가 IP에 묶여 있어 고정 IP를 바꾸면 주소도 바뀝니다.
- 무료 DDNS 서브도메인(예: DuckDNS)에서 이름을 만들고 IP를 넣습니다.
- 구매한 도메인이면 DNS에 A 레코드를 추가합니다.

`dig +short wokkiri.example.com`이 VM의 IP를 돌려주면 다음으로 갑니다.

## 5. 코드 올리기와 서비스 등록

내 컴퓨터의 저장소 폴더에서 실행합니다. `git archive`는 커밋된 파일만 담으므로 로컬 시험 DB(`data/`)는 올라가지 않습니다. 커밋하지 않은 변경도 빠집니다.

```sh
git archive --format=tar.gz -o /tmp/wokkiri.tar.gz HEAD
gcloud compute scp /tmp/wokkiri.tar.gz VM이름:/tmp/ --zone 영역
gcloud compute ssh VM이름 --zone 영역
```

### 스왑과 패키지 갱신

e2-micro는 메모리가 1GB 미만이고 스왑이 없습니다. VM 안에서 한 번만 실행합니다. `mkswap`·`swapon`은 `/sbin`에 있어 일반 사용자 PATH에는 없습니다.

```sh
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
sudo /sbin/mkswap /swapfile && sudo /sbin/swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab    # 재부팅 뒤에도 켜지게 합니다
sudo apt update && sudo apt -y upgrade
/sbin/swapon --show
```

### 처음 설치

VM 안에서 한 번만 실행합니다. 코드는 배포 계정 `wokkiri-deploy` 소유의 릴리스 폴더에 두고(서버 계정은 고칠 수 없음), `data/`만 서버 계정 `wokkiri` 소유입니다. 릴리스마다 `data` 링크가 `/opt/wokkiri/data`를 가리켜 데이터는 한 벌만 있습니다.

```sh
python3 --version                      # 3.9 이상이어야 합니다
sudo useradd --system --home-dir /opt/wokkiri --shell /usr/sbin/nologin wokkiri
sudo useradd --system --create-home --home-dir /var/lib/wokkiri-deploy --shell /bin/sh wokkiri-deploy
sudo install -d -o wokkiri -g wokkiri -m 700 /opt/wokkiri/data
R=/opt/wokkiri/releases; ID=$(date -u +%Y%m%d-%H%M%S)
sudo install -d -o wokkiri-deploy -g wokkiri-deploy -m 755 $R $R/$ID
sudo -u wokkiri-deploy tar --no-same-owner -xzf /tmp/wokkiri.tar.gz -C $R/$ID
sudo -u wokkiri-deploy ln -s /opt/wokkiri/data $R/$ID/data
sudo -u wokkiri-deploy ln -s $ID $R/current

sudo install -m 640 $R/current/deploy/wokkiri.env.example /etc/wokkiri.env
sudo nano /etc/wokkiri.env             # WOKKIRI_HOST를 실제 주소로 바꿉니다(또는 sudo sed -i 's/wokkiri\.example\.com/실제주소/' /etc/wokkiri.env)
sudo cp $R/current/deploy/wokkiri.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wokkiri
systemctl status wokkiri
curl -s -H 'Host: wokkiri.example.com' http://127.0.0.1:8000/api/state   # {"actor": null, ...}이 나오면 정상입니다
```

환경변수는 다음과 같습니다. `WOKKIRI_DEMO`는 넣지 않습니다.

| 이름 | 뜻 |
| --- | --- |
| `PORT` | 서버 포트(기본 8000). Caddyfile의 포트와 같아야 합니다. |
| `WOKKIRI_HOST` | 공개 주소. 정하면 이 주소와 `https` 출처의 요청만 받습니다. 없으면 로컬 주소만 받아 배포 서버가 모두 403이 됩니다. |
| `WOKKIRI_SECURE_COOKIE=1` | 로그인 쿠키에 `Secure`를 붙입니다. |
| `WOKKIRI_TRUST_PROXY=1` | 접속 주소를 Caddy가 붙인 `X-Forwarded-For`의 가장 오른쪽 값으로 봅니다. Caddy 없이 켜면 안 됩니다. |
| `WOKKIRI_BIND` | 바인딩 주소(기본 `127.0.0.1`). Caddy와 같은 VM이면 바꾸지 않습니다. |
| `WOKKIRI_DB` | DB 경로(기본 `data/wokkiri.sqlite3`). 바꾸면 systemd의 `ReadWritePaths`와 백업 명령도 같이 바꿉니다. |

백업 스크립트만 읽는 `WOKKIRI_BACKUP_BUCKET`은 이 파일이 아니라 `/etc/wokkiri-backup.env`에 둡니다(7절).

### 코드 갱신

9절의 배포 스크립트로 합니다(태그 push, 수동 실행, 또는 내 컴퓨터의 `scripts/deploy.sh`). 압축에 `data/`가 없으므로 DB와 백업은 내용도 소유권도 그대로입니다. `deploy/wokkiri.service`나 환경변수 예시, `deploy/wokkiri-deploy.sh`가 바뀌었으면 배포로는 반영되지 않으니 해당 설치 줄을 VM에서 다시 실행합니다.

## 6. Caddy

```sh
sudo apt update && sudo apt install -y caddy
sudo cp /opt/wokkiri/releases/current/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile         # 주소를 실제 값으로 바꿉니다
sudo caddy validate --config /etc/caddy/Caddyfile   # "Valid configuration"이 나와야 합니다
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30         # "certificate obtained successfully"가 보이면 발급된 것입니다
```

Debian 12의 `apt`로 깔리는 Caddy는 2.6.2이고 설치하면 바로 켜져 재부팅 뒤에도 뜹니다. 발급은 reload 뒤 몇 초면 끝났습니다. 로그의 `no OCSP server specified in certificate` 경고는 무시해도 됩니다.

## 7. 백업

매일 한 번 백업하고 최근 14개를 둡니다. 백업은 systemd 단위 `wokkiri-backup.service`가 합니다. 지금 릴리스의 `scripts/backup.py`를 서버와 같은 계정·같은 보호 옵션(`ProtectSystem=strict` 등, 쓸 수 있는 곳은 데이터 폴더뿐)으로 한 번 실행하며, 매일 cron과 배포 스크립트(9절)가 같은 단위를 시작합니다. VM 시계는 UTC이므로 아래는 한국 시간 03:30입니다.

```sh
sudo cp /opt/wokkiri/releases/current/deploy/wokkiri-backup.service /etc/systemd/system/
sudo systemctl daemon-reload
echo '30 18 * * * root /usr/bin/systemctl start wokkiri-backup.service' | sudo tee /etc/cron.d/wokkiri-backup
sudo systemctl start wokkiri-backup.service     # 한 번 직접 돌립니다. 실패하면 0이 아닌 코드로 끝납니다
sudo journalctl -u wokkiri-backup -n 20         # 만든 파일 경로가 보이면 성공입니다. cron이 돌린 결과도 여기에 남습니다
sudo ls -l /opt/wokkiri/data/backups
```

### 오프사이트 백업 (Cloud Storage)

백업이 VM 안에만 있으면 디스크나 VM을 잃을 때 같이 사라집니다. 환경변수 `WOKKIRI_BACKUP_BUCKET`이 있으면 `scripts/backup.py`가 방금 만든 파일을 `gcloud storage cp`로 `gs://<버킷>/`에도 올립니다(Debian 12 GCE 이미지에는 `gcloud`가 들어 있습니다). 올리기가 실패해도 로컬 백업과 보관 개수 정리는 그대로이고, 종료 코드 1과 함께 "버킷 올리기 실패(종료 코드 N)…" 한 줄이 stderr에 남습니다. `gcloud`의 출력은 인증 정보가 섞일 수 있어 내보내지 않으므로, 자세한 원인은 VM에서 `gcloud storage cp`를 직접 실행해 봅니다.

버킷은 한 번만 만듭니다(내 컴퓨터에서 실행). `<서비스 계정>`은 `gcloud compute instances describe VM이름 --zone 영역 --format='value(serviceAccounts[0].email)'`로 봅니다.

```sh
gcloud storage buckets create gs://<버킷> --project <프로젝트 ID> --location us-central1 --default-storage-class STANDARD --uniform-bucket-level-access --public-access-prevention
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > /tmp/lifecycle.json
gcloud storage buckets update gs://<버킷> --lifecycle-file /tmp/lifecycle.json      # 만든 지 30일 지난 사본은 지워집니다
# VM은 새 객체를 만들 수만 있습니다(삭제·덮어쓰기·목록·읽기 불가). 받는 것은 프로젝트 소유자 계정으로 합니다.
gcloud storage buckets add-iam-policy-binding gs://<버킷> --member serviceAccount:<서비스 계정> --role roles/storage.objectCreator
# 기본 서비스 계정은 프로젝트 편집자라서, 버킷에 자동으로 붙는 편집자·뷰어 권한을 빼야 위 제한이 실제로 걸립니다. 소유자 권한은 남깁니다.
gcloud storage buckets remove-iam-policy-binding gs://<버킷> --member projectEditor:<프로젝트 ID> --role roles/storage.legacyBucketOwner
gcloud storage buckets remove-iam-policy-binding gs://<버킷> --member projectEditor:<프로젝트 ID> --role roles/storage.legacyObjectOwner
gcloud storage buckets remove-iam-policy-binding gs://<버킷> --member projectViewer:<프로젝트 ID> --role roles/storage.legacyBucketReader
gcloud storage buckets remove-iam-policy-binding gs://<버킷> --member projectViewer:<프로젝트 ID> --role roles/storage.legacyObjectReader
```

VM의 접근 범위에 스토리지 쓰기를 더합니다. 범위는 VM이 꺼져 있을 때만 바꿀 수 있어 잠깐 끊깁니다. `--scopes`는 통째로 바뀌므로 있던 범위도 같이 적습니다.

```sh
gcloud compute instances stop VM이름 --zone 영역
gcloud compute instances set-service-account VM이름 --zone 영역 --service-account <서비스 계정> --scopes logging-write,monitoring-write,storage-rw
gcloud compute instances start VM이름 --zone 영역
```

버킷 이름은 백업 단위가 읽는 `/etc/wokkiri-backup.env`에 둡니다(없으면 로컬 백업만 합니다). 매일 백업과 배포 전 백업이 같은 단위를 쓰므로 둘 다 버킷에 올라가고, 버킷 올리기가 실패하면 그 배포는 링크를 바꾸기 전에 멈춥니다. VM 안에서 실행합니다.

```sh
echo 'WOKKIRI_BACKUP_BUCKET=<버킷>' | sudo tee /etc/wokkiri-backup.env
sudo systemctl start wokkiri-backup.service
sudo journalctl -u wokkiri-backup -n 20     # 파일 경로만 있으면 성공, "실패"가 있으면 종료 코드가 같이 남습니다
```

내 컴퓨터에서 `gcloud storage ls -l gs://<버킷>/`에 방금 파일이 보이면 된 것입니다. 지운 객체는 버킷 기본값(소프트 삭제)으로 7일 동안 되살릴 수 있습니다.

### 버킷 사본으로 복원

내 컴퓨터에서 사본을 받아 열리는지 본 뒤 VM으로 보냅니다.

```sh
gcloud storage ls -l gs://<버킷>/
(umask 077 && gcloud storage cp gs://<버킷>/wokkiri-날짜-시각.db .)
sqlite3 wokkiri-날짜-시각.db 'PRAGMA integrity_check'      # ok가 나와야 합니다
gcloud compute scp wokkiri-날짜-시각.db VM이름:/tmp/ --zone 영역
```

VM 안에서 서버를 멈추고, **지금 DB를 지우지 않고 따로 옮겨 둔 뒤** 사본으로 바꿉니다. 복원하면 사본 시각 이후의 기록은 새 DB에 없으므로, 옮겨 둔 원본이 그 기록과 되돌릴 길의 전부입니다. VM에는 `sqlite3` 명령이 없어 검사는 `python3`로 합니다.

```sh
F=/tmp/wokkiri-날짜-시각.db
sudo systemctl stop wokkiri
systemctl is-active wokkiri             # inactive가 나와야 합니다. active면 여기서 멈춥니다
# 받은 사본을 VM에서 한 번 더 검사합니다. ok가 아니면 여기서 멈추고 서비스를 다시 켭니다(아직 아무것도 바꾸지 않았습니다)
python3 -c "import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" $F

# 지금 DB와 남아 있는 -wal, -shm 파일을 소유자만 볼 수 있는 폴더(0700, 파일 0600)로 함께 옮깁니다
KEEP=/opt/wokkiri/data/before-restore-$(date +%Y%m%d-%H%M%S)
sudo install -d -o wokkiri -g wokkiri -m 700 "$KEEP"
sudo sh -c "mv /opt/wokkiri/data/wokkiri.sqlite3* \"$KEEP\"/ && chmod 600 \"$KEEP\"/*"
sudo ls -l "$KEEP" /opt/wokkiri/data    # 원본이 KEEP에 있고 data에는 wokkiri.sqlite3로 시작하는 파일이 없어야 합니다

sudo install -o wokkiri -g wokkiri -m 600 $F /opt/wokkiri/data/wokkiri.sqlite3
sudo systemctl start wokkiri
systemctl is-active wokkiri             # active
curl -s -H 'Host: wokkiri.example.com' http://127.0.0.1:8000/api/state   # 켠 직후에는 빈 응답일 수 있어 몇 초 뒤에 봅니다. {"actor": null, ...}이 나오면 정상입니다
echo "$KEEP"                            # 나중에 지우거나 되돌릴 때 쓸 경로이니 적어 둡니다
```

서비스가 뜨지 않거나 내용이 기대와 다르면 옮겨 둔 원본으로 되돌립니다.

```sh
sudo systemctl stop wokkiri
sudo sh -c "rm -f /opt/wokkiri/data/wokkiri.sqlite3* && mv \"$KEEP\"/wokkiri.sqlite3* /opt/wokkiri/data/"
sudo systemctl start wokkiri
systemctl is-active wokkiri
```

브라우저로 로그인과 기록이 정상인지 확인한 뒤에만 정리합니다. 옮겨 둔 원본에도 회원 기록과 PIN 해시가 있으므로 필요 없다고 판단되면 지웁니다. 지우는 시점은 직접 정합니다(급하지 않으면 며칠 두어도 됩니다. `backup.py`의 보관 개수 정리는 이 폴더를 건드리지 않습니다).

```sh
rm /tmp/wokkiri-날짜-시각.db
sudo rm -r "$KEEP"                      # 되돌린 뒤라 폴더가 비어 있으면 sudo rmdir "$KEEP"
```

VM 자체를 잃었으면 5~6절대로 새 VM에 설치한 뒤 같은 순서로 바꿉니다. 내 컴퓨터의 사본도 다 쓴 뒤 지웁니다.

### VM의 백업을 내 컴퓨터로 가져오기

버킷을 쓰지 않을 때는 주 1회 정도 내 컴퓨터로 가져옵니다(내 컴퓨터에서 실행).

```sh
# umask 077: 받는 파일을 나만 읽게(0600) 만듭니다. 괄호 안에서만 적용됩니다.
(umask 077 && gcloud compute ssh VM이름 --zone 영역 --command 'sudo tar -C /opt/wokkiri/data -cz backups' > wokkiri-backups-$(date +%Y%m%d).tar.gz)
chmod 600 wokkiri-backups-*.tar.gz      # 같은 이름이 이미 있었거나 전에 받아 둔 파일도 나만 읽게 합니다
ls -l wokkiri-backups-*.tar.gz          # 권한이 -rw------- 인지 봅니다
tar -tzf wokkiri-backups-$(date +%Y%m%d).tar.gz   # 파일 목록이 보이는지 확인합니다
```

압축을 풀 때도 `umask 077`인 셸에서 풀거나 푼 폴더에 `chmod -R go-rwx`를 해 주세요.

백업 파일에는 회원 기록과 PIN 해시가 있습니다(PIN은 4자리라 파일이 새면 금방 풀립니다). 공유 폴더나 공개 저장소에 두지 않습니다.

되돌릴 때는 서버를 멈추고 파일을 바꿉니다.

```sh
sudo systemctl stop wokkiri
sudo install -o wokkiri -g wokkiri -m 600 /opt/wokkiri/data/backups/wokkiri-날짜-시각.db /opt/wokkiri/data/wokkiri.sqlite3
sudo systemctl start wokkiri
```

## 8. 확인 항목

`[x]`는 2026-11-15 설치 때 `curl`과 VM 안 명령으로 확인한 항목입니다. 다시 설치하면 전부 다시 봅니다.

- [x] `https://wokkiri.example.com`이 인증서 경고 없이 열리고, `http://`로 들어가면 `https://`로 넘어갑니다(308).
- [ ] 가입 → 복구 키 화면 → 로그아웃 → 로그인이 됩니다. (가입 API 응답 200까지만 확인했고 브라우저 흐름은 확인되지 않음)
- [x] `wokkiri` 쿠키에 Secure·HttpOnly·SameSite=Strict가 있습니다(가입 응답의 `Set-Cookie`로 확인).
- [x] `garam` / `1111`로 로그인되지 않습니다(시험 모드가 꺼져 있음).
- [x] 밖에서 `http://VM의IP:8000`은 열리지 않습니다.
- [x] VM을 다시 시작한 뒤에도 서비스·Caddy·스왑이 뜨고 DB와 백업 파일이 남아 있습니다.
- [x] 백업 파일이 매일 생기고, 내 컴퓨터로 가져온 사본이 열립니다. (수동 백업과 가져오기는 2026-11-15, cron의 자동 실행은 2026-11-16에 버킷 업로드까지 확인)
- [x] 버킷에 사본이 올라가고(`gcloud storage ls -l gs://<버킷>/`), 받은 사본이 `PRAGMA integrity_check`에서 `ok`이며, VM에서는 버킷 객체를 지우거나 읽지 못합니다.

## 9. CI/CD (GitHub Actions)

- `.github/workflows/test.yml`: `main`에 push하거나 PR을 열 때마다 테스트를 돌립니다. 비밀값을 쓰지 않습니다.
- `.github/workflows/deploy.yml`: `v*` 태그를 push하거나 수동으로 실행하면 테스트를 돌린 뒤 통과했을 때만 `scripts/deploy.sh`로 VM에 배포합니다.
- `scripts/deploy.sh`: 테스트 → 커밋하지 않은 변경이 없는지 확인 → `git archive HEAD` → ssh로 VM에 전송 → `https` 응답 확인.
- `deploy/wokkiri-deploy.sh`(VM의 `/usr/local/bin/wokkiri-deploy`): 받은 압축을 검사해(20MB 이하, 풀면 100MB 이하, 절대 경로·`..`·링크 거부) `/opt/wokkiri/releases/<UTC 시각>/`에 풀고 → `wokkiri-backup.service`(7절)로 DB를 백업하고(실패하면 중단) → `current` 링크를 바꾸고 → `systemctl restart wokkiri` → `127.0.0.1`에 2초 간격으로 10번까지 물어봅니다. 링크를 바꾼 뒤에는 응답이 없든 중간에 끊기든 끝까지 가지 못하면 링크를 이전 릴리스로 되돌려 다시 시작하고 실패로 끝납니다. 성공하면 릴리스는 최근 5개만 남기며, 지금 릴리스와 직전 릴리스는 지우지 않습니다. 동시에 두 번 실행되지 않습니다. 압축의 "풀면 100MB 이하"는 압축 스트림 길이와 목록에 적힌 항목 크기의 합을 둘 다 봅니다(희소 파일 포함). 푸는 시간은 2분까지입니다.

배포 키로는 이 스크립트 한 가지만 실행됩니다(`authorized_keys`의 강제 명령). 셸, 다른 명령, sftp, 포트 전달은 되지 않는 것을 확인했습니다. 배포마다 `data/backups`에 백업이 하나 생기므로 보관 개수 14개에는 배포 전 백업도 들어갑니다(매일 백업의 사본은 버킷에 따로 있습니다). 실패한 배포를 되돌리는 동안에는 20초 남짓 응답이 끊깁니다.

**보안 모델.** 배포 키를 가진 쪽(GitHub의 `production` 환경, 곧 저장소 쓰기 권한자)은 새 앱 코드를 올려 서버 계정 `wokkiri`로 실행시킬 수 있습니다. 그러므로 배포 키는 DB를 읽고 고칠 수 있는 권한과 같은 수준으로 다뤄야 하며, "DB에는 닿지 못하는 키"가 아닙니다. 강제 명령과 sudoers가 막는 것은 그 바깥입니다. 배포 계정은 셸을 얻지 못하고, root로는 `systemctl restart wokkiri`와 `systemctl start wokkiri-backup.service` 두 명령만 실행할 수 있으며, 올린 코드는 앱이든 백업 스크립트든 두 단위의 보호 옵션(`ProtectSystem=strict`, `NoNewPrivileges`, 쓰기는 데이터 폴더만) 안에서만 돕니다. 배포 스크립트·단위 파일·sudoers·키 목록은 root 소유라 배포로는 바뀌지 않습니다.

### VM에 한 번 설치

5절 "처음 설치"로 배포 계정과 릴리스 폴더가 있는 상태에서, 내 컴퓨터의 저장소 폴더에서 스크립트를 올리고 VM 안에서 설치합니다.

```sh
gcloud compute scp deploy/wokkiri-deploy.sh VM이름:/tmp/ --zone 영역
gcloud compute ssh VM이름 --zone 영역
```

```sh
sudo install -o root -g root -m 755 /tmp/wokkiri-deploy.sh /usr/local/bin/wokkiri-deploy
# 상태 확인에 쓸 Host 값입니다(배포 계정은 /etc/wokkiri.env를 읽지 못합니다)
sudo grep '^WOKKIRI_HOST=' /etc/wokkiri.env | cut -d= -f2 | sudo tee /etc/wokkiri-deploy.host > /dev/null
# 배포 계정이 sudo로 할 수 있는 일: 서비스 재시작, 백업 단위 시작(7절의 wokkiri-backup.service가 설치되어 있어야 합니다)
printf '%s\n' 'wokkiri-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart wokkiri' 'wokkiri-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl start wokkiri-backup.service' > /tmp/sudoers-wd
sudo visudo -cf /tmp/sudoers-wd         # "parsed OK"가 나와야 합니다
sudo install -o root -g root -m 440 /tmp/sudoers-wd /etc/sudoers.d/wokkiri-deploy && rm /tmp/sudoers-wd
# 배포 계정이 자기 키 목록을 고치지 못하게 홈과 .ssh는 root 소유로 둡니다
sudo chown root:root /var/lib/wokkiri-deploy && sudo chmod 755 /var/lib/wokkiri-deploy
sudo install -d -o root -g root -m 755 /var/lib/wokkiri-deploy/.ssh
```

`deploy/wokkiri-deploy.sh`를 고치면 위 첫 두 줄(올리기와 `install`)을 다시 실행합니다.

### 배포 키 만들기와 교체

내 컴퓨터에서 실행합니다. 키는 저장소 밖 임시 폴더에 만들고, Secret에 넣은 뒤 지웁니다. 교체할 때도 같은 순서이고, `authorized_keys`를 새 파일로 덮어쓰므로 이전 키는 그 순간 못 쓰게 됩니다.

```sh
umask 077; K=$(mktemp -d)
ssh-keygen -q -t ed25519 -N '' -C wokkiri-deploy-ci -f $K/key
printf 'command="/usr/local/bin/wokkiri-deploy",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,restrict %s\n' "$(cat $K/key.pub)" > $K/authorized_keys
gcloud compute scp $K/authorized_keys VM이름:/tmp/wd-ak --zone 영역
gcloud compute ssh VM이름 --zone 영역 --command 'sudo install -o root -g root -m 644 /tmp/wd-ak /var/lib/wokkiri-deploy/.ssh/authorized_keys && rm /tmp/wd-ak'
gh secret set DEPLOY_SSH_KEY --env production < $K/key
rm -r $K
```

내 컴퓨터에서 `scripts/deploy.sh`를 계속 쓰려면 Secret용과 별도로 키를 하나 더 만들어 `authorized_keys`에 같은 형식으로 한 줄 더 넣고 개인키를 저장소 밖에 보관합니다(두 줄짜리 구성은 확인되지 않음).

### Secrets

GitHub 저장소의 `production` 환경에 둡니다(`gh secret set 이름 --env production`). 이 환경은 `main` 브랜치와 `v*` 태그에서만 배포할 수 있게 해 두었습니다.

| 이름 | 값 |
| --- | --- |
| `DEPLOY_SSH_KEY` | 배포 개인키(위에서 만든 `key` 파일 내용) |
| `DEPLOY_HOST` | VM 주소(고정 IP) |
| `DEPLOY_KNOWN_HOSTS` | `<고정 IP> ssh-ed25519 AAAA...` 한 줄. VM 안에서 `cat /etc/ssh/ssh_host_ed25519_key.pub`로 본 값의 앞 두 칸에 주소를 붙입니다. 이 값과 다른 호스트 키면 배포가 멈춥니다. |
| `DEPLOY_URL` | 배포 뒤 확인할 주소(`https://wokkiri.example.com/`) |

VM을 새로 만들거나 호스트 키가 바뀌면 `DEPLOY_KNOWN_HOSTS`를, 고정 IP나 주소가 바뀌면 `DEPLOY_HOST`·`DEPLOY_KNOWN_HOSTS`·`DEPLOY_URL`과 VM의 `/etc/wokkiri-deploy.host`를 다시 넣습니다.

### 배포하기

```sh
git tag v0.1.0 && git push origin v0.1.0       # 태그로 배포
gh workflow run deploy.yml --ref main          # 태그 없이 main의 최신 커밋을 배포(수동 실행)
gh run watch                                   # 진행 상황
```

내 컴퓨터에서 직접 배포할 때는 값을 환경변수로 줍니다(실제 값은 `docs/ops.local.md`).

```sh
DEPLOY_HOST=<고정 IP> DEPLOY_KEY_FILE=<개인키 경로> DEPLOY_KNOWN_HOSTS_FILE=<known_hosts 경로> DEPLOY_URL=https://wokkiri.example.com/ scripts/deploy.sh
```

### 되돌리기

새 릴리스가 뜨지 않으면 배포 스크립트가 스스로 이전 릴리스로 되돌립니다. 떴지만 내용에 문제가 있어 직접 되돌릴 때는 이전 커밋에 태그를 붙여 다시 배포하거나, VM 안에서 링크를 바꿉니다.

```sh
ls /opt/wokkiri/releases; readlink /opt/wokkiri/releases/current
sudo -u wokkiri-deploy ln -sfn <릴리스 폴더 이름> /opt/wokkiri/releases/.current.new
sudo -u wokkiri-deploy mv -T /opt/wokkiri/releases/.current.new /opt/wokkiri/releases/current
sudo systemctl restart wokkiri
```

**코드만 되돌아가고 DB는 되돌아가지 않습니다.** 자동 되돌리기도 마찬가지입니다. 새 버전이 뜨면서(또는 쓰이면서) 상태·테이블 형식을 바꿨다면, 되돌아간 구버전이 그 DB를 읽지 못하거나 잘못 읽을 수 있습니다.

- 형식이 바뀌는 배포는 직전 버전이 새 형식을 읽을 수 있게 만듭니다(한 버전 호환). 예를 들어 새 필드는 없어도 동작하게 먼저 배포하고, 그 필드에 의존하는 변경은 다음 배포로 나눕니다.
- 그렇게 할 수 없는 변경이었다면 코드만 되돌려서는 안 됩니다. 서비스를 멈추고 7절 "버킷 사본으로 복원"의 순서로 배포 전 백업(`data/backups`의 배포 시각 파일)을 복원한 뒤 구버전을 켭니다. **배포 뒤부터 복원까지 회원이 남긴 기록은 사라집니다.** 그 기록이 필요한지는 복원 전에 판단하고, 옮겨 둔 원본 DB는 판단이 끝날 때까지 지우지 않습니다.

### 배포 키 폐기와 CI/CD 없애기

```sh
# 키만 폐기: VM 안에서
sudo rm /var/lib/wokkiri-deploy/.ssh/authorized_keys
# 내 컴퓨터에서
gh secret delete DEPLOY_SSH_KEY --env production
# 전부 없애기: VM 안에서(릴리스 폴더와 서비스는 그대로 둡니다)
sudo rm /etc/sudoers.d/wokkiri-deploy /usr/local/bin/wokkiri-deploy /etc/wokkiri-deploy.host   # 백업 단위와 cron은 매일 백업이 쓰므로 남깁니다
```

이 절의 지우는 명령은 실행해 보지 않았습니다(확인되지 않음).

## 알아 둘 한계

- 가입은 접속 주소당 1시간에 10회, 로그인·복구·복구 키 재발급은 합쳐서 접속 주소당 10분에 30회까지입니다. 넘으면 "요청이 너무 많아요. 잠시 뒤에 다시 시도해 주세요."가 보입니다. 같은 박스 와이파이를 쓰는 회원들은 접속 주소가 같아 함께 세어지므로, 한자리에서 10명 넘게 가입하면 일부는 기다리거나 휴대폰 데이터로 가입해야 합니다.
- 이 횟수는 서버 메모리에만 있어 서버를 다시 시작하면 초기화됩니다. PIN 5회 실패 잠금은 DB에 있어 그대로 유지됩니다.
- 서버는 요청을 한 번에 하나씩 처리합니다. 소규모 사용을 전제로 합니다.
