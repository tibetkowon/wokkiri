# 배포 (Google Cloud e2-micro + Caddy)

VM 한 대에 Python 서버, SQLite 파일, Caddy(HTTPS 자동)를 올리는 순서입니다.

**확인한 범위(2026-11-15, Debian 12, Python 3.11.2, Caddy 2.6.2):** 5절의 스왑·"처음 설치", 6절 Caddy(인증서 발급), 7절의 cron 등록·수동 백업·내 컴퓨터로 가져오기는 실제 VM에서 실행해 확인했습니다. **확인되지 않음:** 1~3절의 콘솔 메뉴(VM·방화벽·고정 IP·예산은 `gcloud` 명령으로 만들었습니다), 5절 "코드 갱신", 7절의 되돌리기 명령, cron이 정해진 시각에 실제로 도는지, 인증서 자동 갱신입니다.

아래에서 `wokkiri.example.com`은 실제 주소로, `VM이름`·`영역`은 만든 값으로 바꿉니다. `gcloud`의 기본 프로젝트가 다른 프로젝트면 `gcloud compute` 명령마다 `--project 프로젝트ID`를 붙입니다. 기준은 Debian 12 이미지, 코드 `/opt/wokkiri`, 데이터 `/opt/wokkiri/data`입니다.

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

VM 안에서 한 번만 실행합니다. 코드는 root 소유(서버가 고칠 수 없음), `data/`만 `wokkiri` 소유입니다. `--no-same-owner`로 풀면 풀린 파일이 root 소유가 되므로 `chown -R`은 쓰지 않습니다.

```sh
python3 --version                      # 3.9 이상이어야 합니다
sudo useradd --system --home-dir /opt/wokkiri --shell /usr/sbin/nologin wokkiri
sudo mkdir -p /opt/wokkiri
sudo tar --no-same-owner -xzf /tmp/wokkiri.tar.gz -C /opt/wokkiri
sudo install -d -o wokkiri -g wokkiri -m 700 /opt/wokkiri/data

sudo install -m 640 /opt/wokkiri/deploy/wokkiri.env.example /etc/wokkiri.env
sudo nano /etc/wokkiri.env             # WOKKIRI_HOST를 실제 주소로 바꿉니다(또는 sudo sed -i 's/wokkiri\.example\.com/실제주소/' /etc/wokkiri.env)
sudo cp /opt/wokkiri/deploy/wokkiri.service /etc/systemd/system/
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

### 코드 갱신

내 컴퓨터에서 위와 같이 압축을 만들어 올린 뒤, VM 안에서 아래만 실행합니다. 압축에 `data/`가 없으므로 DB와 백업은 내용도 소유권도 그대로입니다. **갱신할 때 `/opt/wokkiri`에 `chown -R`을 하지 마세요.** `data/` 아래의 DB와 백업까지 root 소유가 되어 서버가 DB에 쓰지 못합니다.

```sh
sudo tar --no-same-owner -xzf /tmp/wokkiri.tar.gz -C /opt/wokkiri
sudo systemctl restart wokkiri
systemctl status wokkiri
sudo ls -ln /opt/wokkiri/data           # DB와 backups의 소유자가 그대로 wokkiri인지 봅니다
```

저장소에서 지운 파일은 VM에 남습니다(동작에는 영향이 없습니다). `deploy/wokkiri.service`나 환경변수 예시가 바뀌었으면 위 "처음 설치"의 해당 줄을 다시 실행합니다. 실수로 소유권을 바꿨다면 `sudo chown -R wokkiri:wokkiri /opt/wokkiri/data`로 되돌립니다.

## 6. Caddy

```sh
sudo apt update && sudo apt install -y caddy
sudo cp /opt/wokkiri/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile         # 주소를 실제 값으로 바꿉니다
sudo caddy validate --config /etc/caddy/Caddyfile   # "Valid configuration"이 나와야 합니다
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30         # "certificate obtained successfully"가 보이면 발급된 것입니다
```

Debian 12의 `apt`로 깔리는 Caddy는 2.6.2이고 설치하면 바로 켜져 재부팅 뒤에도 뜹니다. 발급은 reload 뒤 몇 초면 끝났습니다. 로그의 `no OCSP server specified in certificate` 경고는 무시해도 됩니다.

## 7. 백업

매일 한 번 백업하고 최근 14개를 둡니다. VM 시계는 UTC이므로 아래는 한국 시간 03:30입니다.

```sh
echo '30 18 * * * wokkiri /usr/bin/python3 /opt/wokkiri/scripts/backup.py' | sudo tee /etc/cron.d/wokkiri-backup
sudo -u wokkiri /usr/bin/python3 /opt/wokkiri/scripts/backup.py    # 한 번 직접 돌려 파일 경로가 출력되는지 봅니다
sudo ls -l /opt/wokkiri/data/backups
```

백업이 VM 안에만 있으면 디스크나 VM을 잃을 때 같이 사라집니다. 주 1회 정도 내 컴퓨터로 가져옵니다(내 컴퓨터에서 실행).

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
- [ ] 백업 파일이 매일 생기고, 내 컴퓨터로 가져온 사본이 열립니다. (수동 백업과 가져오기는 확인, cron의 자동 실행은 다음 날 `sudo ls -l /opt/wokkiri/data/backups`로 확인 필요)

## 알아 둘 한계

- 가입은 접속 주소당 1시간에 10회, 로그인·복구·복구 키 재발급은 합쳐서 접속 주소당 10분에 30회까지입니다. 넘으면 "요청이 너무 많아요. 잠시 뒤에 다시 시도해 주세요."가 보입니다. 같은 박스 와이파이를 쓰는 회원들은 접속 주소가 같아 함께 세어지므로, 한자리에서 10명 넘게 가입하면 일부는 기다리거나 휴대폰 데이터로 가입해야 합니다.
- 이 횟수는 서버 메모리에만 있어 서버를 다시 시작하면 초기화됩니다. PIN 5회 실패 잠금은 DB에 있어 그대로 유지됩니다.
- 서버는 요청을 한 번에 하나씩 처리합니다. 소규모 사용을 전제로 합니다.
