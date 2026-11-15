"""서버: python3 app.py  (가상 시험 회원이 필요하면 WOKKIRI_DEMO=1 python3 app.py, 배포는 docs/deploy.md)"""
import json
import os
import sqlite3
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import auth
from domain import RuleError, apply, initial, migrate, require, view

ROOT = Path(__file__).parent
DB = Path(os.environ.get('WOKKIRI_DB', str(ROOT / 'data' / 'wokkiri.sqlite3')))
# 가상 회원(PIN 1111~4444, 복구 키 DEMO-DEMO-DEMO)과 예시 리그를 만듭니다. 누구나 아는 값이라 배포 서버에서는 켜지 않습니다.
DEMO = os.environ.get('WOKKIRI_DEMO') == '1'
# 아래 셋은 HTTPS 프록시(Caddy) 뒤에서만 켭니다. 기본값은 로컬 http 개발용입니다.
SECURE = os.environ.get('WOKKIRI_SECURE_COOKIE') == '1'
# 켜면 접속 주소를 X-Forwarded-For에서 읽습니다. 프록시 없이 켜면 헤더를 위조해 제한을 피할 수 있습니다.
TRUST_PROXY = os.environ.get('WOKKIRI_TRUST_PROXY') == '1'
# 공개 주소(예: wokkiri.example.com). 정하면 이 Host와 https 출처만 받고, 없으면 로컬 주소만 받습니다.
HOST = os.environ.get('WOKKIRI_HOST')
# 접속 주소별 제한: 묶음 이름 -> (횟수, 초). 테스트는 이 값을 바꿔 씁니다.
LIMITS = dict(signup=(10, 3600), enter=(30, 600))
# 리그 하나가 올려 둘 수 있는 사진 총량입니다. 사진 한 장 한도(2MB)는 domain.stash가 봅니다.
PHOTO_TOTAL = 50 * 1024 * 1024
LIMITED = {'/api/signup': 'signup', '/api/login': 'enter', '/api/recover': 'enter', '/api/recovery-key': 'enter'}
# shortcut: 요청 시각을 메모리에만 두어 재시작하면 초기화됩니다. 서버 프로세스를 둘 이상으로 늘리면 공유 저장소로 옮깁니다.
hits, swept = {}, [0.0]
FILES = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/logo.png': ('logo.png', 'image/png'), '/qrcode.js': ('qrcode.js', 'text/javascript; charset=utf-8'), '/holidays.json': ('holidays.json', 'application/json')}


def fresh():
    if not DEMO:
        return dict(users={}, sessions={}, leagues=[])
    state = initial(time.time())
    auth.add_demo_users(state)
    return state


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    # shortcut: 사진을 뺀 상태 전체를 한 행 JSON으로 둡니다. 회원·리그가 크게 늘거나 서버를 멀티스레드로 바꾸면 리그·기록 테이블로 나눕니다.
    db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
    # 사진 본문은 상태 JSON에 넣지 않고 여기에 둡니다. 같은 파일이라 백업 한 번에 함께 담깁니다.
    db.execute('CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, league TEXT NOT NULL, mime TEXT NOT NULL, body BLOB NOT NULL, created REAL NOT NULL)')
    if db.execute('SELECT 1 FROM state WHERE id=1').fetchone() is None:
        db.execute('INSERT OR IGNORE INTO state VALUES (1, ?)', (json.dumps(fresh()),))
    db.commit()
    return db


def load():
    with connect() as db:
        return json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])


def change(fn, keep=False, quota=True):
    """상태를 한 트랜잭션으로 바꿉니다. keep이면 규칙 오류가 나도 바뀐 상태(로그인 실패 횟수)를 저장합니다. quota를 끄면(이전 DB 올리기) 사진 총량을 보지 않습니다."""
    with connect() as db:
        # 쓰기 잠금을 먼저 잡아, 읽고 고쳐 쓰는 사이에 다른 변경이 끼어들지 못합니다.
        db.execute('BEGIN IMMEDIATE')
        state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
        error = None
        try:
            now = time.time()
            result = fn(state, now)
        except RuleError as err:
            if not keep:
                raise
            result, error = None, err
        # domain이 새로 올린 사진을 테이블로 옮기고, 어느 안내도 가리키지 않는 사진(교체·삭제된 것)은 지웁니다.
        new = state.pop('photos', {})
        db.executemany('INSERT INTO photos VALUES (?, ?, ?, ?, ?)', [(pid, p['league'], p['mime'], p['body'], now) for pid, p in new.items()])
        used = {r['guide']['photo'] for l in state['leagues'] for r in l['rounds']}
        db.executemany('DELETE FROM photos WHERE id=?', [row for row in db.execute('SELECT id FROM photos').fetchall() if row[0] not in used])
        for league in {p['league'] for p in new.values()} if quota else ():
            total = db.execute('SELECT SUM(LENGTH(body)) FROM photos WHERE league=?', (league,)).fetchone()[0] or 0
            require(total <= PHOTO_TOTAL, '이 리그의 사진이 모두 합쳐 50MB를 넘어요. 다른 회차의 사진을 지운 뒤 다시 올려 주세요.')
        db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
    if error:
        raise error
    return result


def prepare():
    """이전 로컬 DB를 아이디·복구 키 구조로 올리고 상태 안의 사진을 사진 테이블로 옮기며, 시험 모드면 가상 회원을 채웁니다."""
    def go(state, now):
        migrate(state)
        if DEMO:
            auth.add_demo_users(state)
    change(go, quota=False)


def session_cookie(token, age=auth.SESSION_SECONDS):
    return f'wokkiri={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}' + ('; Secure' if SECURE else '')


def limited(name, address, now):
    """접속 주소가 이 묶음의 한도를 넘었으면 True입니다. 거부한 요청은 세지 않습니다."""
    count, seconds = LIMITS[name]
    if not 0 <= now - swept[0] < 60:
        # 창이 지난 주소를 1분마다 지워 메모리가 계속 커지지 않게 합니다. 시계가 뒤로 가면 전부 지웁니다.
        for key in [k for k, times in hits.items() if not 0 <= now - times[-1] < LIMITS[k[0]][1]]:
            del hits[key]
        swept[0] = now
    times = [t for t in hits.get((name, address), []) if 0 <= now - t < seconds]
    if len(times) >= count:
        hits[name, address] = times
        return True
    hits[name, address] = times + [now]
    return False


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, content_type='application/json; charset=utf-8', cookie=None, cache='no-store'):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def token(self):
        cookie = SimpleCookie(self.headers.get('Cookie', '')).get('wokkiri')
        return cookie.value if cookie else None

    def allowed(self):
        if HOST:
            return self.headers.get('Host') == HOST
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def origin(self):
        return f'https://{HOST}' if HOST else f'http://{self.headers.get("Host")}'

    def address(self):
        """접속 주소입니다. 프록시가 붙인 값은 X-Forwarded-For의 가장 오른쪽이고, 그 왼쪽은 접속자가 지어낼 수 있습니다."""
        forwarded = ','.join(self.headers.get_all('X-Forwarded-For') or []).split(',')[-1].strip() if TRUST_PROXY else ''
        return forwarded or self.client_address[0]

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': '로컬 주소로 접속해 주세요.'})
        url = urlparse(self.path)
        if url.path == '/api/state':
            state, now = load(), time.time()
            actor = auth.session_user(state, self.token(), now)
            # 참여 중인 리그만 내려보냅니다. 다른 리그는 초대 링크를 아는 사람만 볼 수 있습니다.
            leagues = [view(l, now) for l in state['leagues'] if actor in l['members']]
            # 리그 안 닉네임은 각 리그의 names에 있습니다. 여기에는 본인 계정 닉네임만 담고 아이디는 내보내지 않습니다.
            users = {actor: state['users'][actor]['name']} if actor else {}
            return self.send(200, dict(actor=actor, users=users, leagues=leagues, now=now))
        if url.path == '/api/invite':
            code = parse_qs(url.query).get('t', [''])[0]
            league = next((l for l in load()['leagues'] if code and l.get('invite') == code), None)
            if league is None:
                return self.send(404, {'error': '초대 링크를 확인해 주세요.'})
            return self.send(200, dict(name=league['name'], kind=league['kind'], start=league['start'], members=len(league['members']), open=time.time() < league['start']))
        if url.path.startswith('/photo/'):
            pid, state, now = url.path[7:], load(), time.time()
            actor = auth.session_user(state, self.token(), now)
            # 로그인한 회원이 참여 중인 리그에서 지금 안내에 걸린 사진만 내려보냅니다.
            shown = pid and actor and any(r['guide']['photo'] == pid for l in state['leagues'] if actor in l['members'] for r in l['rounds'])
            with connect() as db:
                row = shown and db.execute('SELECT mime, body FROM photos WHERE id=?', (pid,)).fetchone()
            if not row:
                return self.send(404, {'error': '사진을 찾을 수 없어요.'})
            # id마다 내용이 바뀌지 않으므로 본인 브라우저에만 하루 캐시합니다.
            return self.send(200, row[1], row[0], cache='private, max-age=86400')
        if url.path not in FILES:
            return self.send(404, {'error': '페이지를 찾을 수 없습니다.'})
        name, mime = FILES[url.path]
        # 로고만 하루 캐시합니다. 나머지는 고치면 바로 보이도록 no-store 그대로입니다.
        self.send(200, (ROOT / 'web' / name).read_bytes(), mime, cache='public, max-age=86400' if name == 'logo.png' else 'no-store')

    def do_POST(self):
        if not self.allowed() or self.headers.get('Origin', self.origin()) != self.origin() or self.headers.get('Content-Type') != 'application/json':
            return self.send(403, {'error': '허용되지 않은 요청입니다.'})
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 3000000:
                raise ValueError('요청 크기를 확인해 주세요.')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('요청 형식을 확인해 주세요.')
            token, path = self.token(), self.path
            # 본문을 다 읽은 뒤에 거부합니다. 읽지 않고 끊으면 프록시가 응답을 못 받을 수 있습니다.
            if path in LIMITED and limited(LIMITED[path], self.address(), time.time()):
                return self.send(429, {'error': '요청이 너무 많아요. 잠시 뒤에 다시 시도해 주세요.'})
            if path in ('/api/signup', '/api/login', '/api/recover'):
                def enter(state, now):
                    key = None
                    if path == '/api/signup':
                        uid, key = auth.signup(state, data.get('login'), data.get('name'), data.get('pin'))
                    elif path == '/api/login':
                        uid = auth.login(state, data.get('login'), data.get('pin'), now)
                    else:
                        uid, key = auth.recover(state, data.get('login'), data.get('key'), data.get('pin'), now)
                    auth.end_session(state, token)
                    return auth.start_session(state, uid, now), key
                # 틀린 PIN·복구 키 횟수는 오류가 나도 저장해야 잠금이 동작합니다.
                session, key = change(enter, keep=True)
                # 복구 키 평문은 이 응답에서 한 번만 나갑니다.
                return self.send(200, {'ok': True, 'recoveryKey': key} if key else {'ok': True}, cookie=session_cookie(session))
            if path == '/api/logout':
                change(lambda state, now: auth.end_session(state, token))
                return self.send(200, {'ok': True}, cookie=session_cookie('', 0))
            if path == '/api/recovery-key':
                def renew(state, now):
                    actor = auth.session_user(state, token, now)
                    require(actor, '먼저 로그인해 주세요.')
                    return auth.renew_key(state, actor, data.get('pin'), now)
                return self.send(200, {'ok': True, 'recoveryKey': change(renew, keep=True)})
            if path != '/api/action':
                return self.send(404, {'error': '작업을 찾을 수 없습니다.'})
            lid = change(lambda state, now: apply(state, auth.session_user(state, token, now), data, now))
            self.send(200, {'league': lid})
        except (ValueError, KeyError, TypeError, OverflowError) as err:
            self.send(400, {'error': str(err) if isinstance(err, RuleError) else '입력 형식을 확인해 주세요.'})
        except sqlite3.Error:
            self.send(500, {'error': '저장하지 못했습니다. 잠시 후 다시 시도해 주세요.'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    prepare()
    bind = os.environ.get('WOKKIRI_BIND', '127.0.0.1')
    server = HTTPServer((bind, port), Handler)
    print(f'와끼리: http://{bind}:{port}', flush=True)
    if DEMO:
        # 아이디·PIN·복구 키 값은 로그에 남기지 않습니다. README에 있습니다.
        print('시험 모드(WOKKIRI_DEMO=1): 가상 회원과 예시 리그가 있습니다. 로그인 정보는 README를 보세요.', flush=True)
    server.serve_forever()
