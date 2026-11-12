"""로컬 전용 서버: python3 app.py  (가상 시험 회원이 필요하면 WOKKIRI_DEMO=1 python3 app.py)"""
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
# 가상 회원(PIN 1111~4444)과 예시 리그를 만듭니다. 누구나 아는 PIN이라 배포 서버에서는 켜지 않습니다.
DEMO = os.environ.get('WOKKIRI_DEMO') == '1'
FILES = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/logo.png': ('logo.png', 'image/png')}


def fresh():
    if not DEMO:
        return dict(users={}, sessions={}, leagues=[])
    state = initial(time.time())
    auth.add_demo_users(state)
    return state


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    # shortcut: 로컬 소규모 상태만 단일 행에 저장하며, 실제 다중 사용자 운영 전에 테이블을 분리합니다.
    db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
    if db.execute('SELECT 1 FROM state WHERE id=1').fetchone() is None:
        db.execute('INSERT OR IGNORE INTO state VALUES (1, ?)', (json.dumps(fresh()),))
    db.commit()
    return db


def load():
    with connect() as db:
        return json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])


def change(fn, keep=False):
    """상태를 한 트랜잭션으로 바꿉니다. keep이면 규칙 오류가 나도 바뀐 상태(로그인 실패 횟수)를 저장합니다."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
        error = None
        try:
            result = fn(state, time.time())
        except RuleError as err:
            if not keep:
                raise
            result, error = None, err
        db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
    if error:
        raise error
    return result


def prepare():
    """이전 로컬 DB를 계정 구조로 올리고, 시험 모드면 가상 회원을 채웁니다."""
    def go(state, now):
        migrate(state)
        if DEMO:
            auth.add_demo_users(state)
    change(go)


def session_cookie(token, age=auth.SESSION_SECONDS):
    return f'wokkiri={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}'


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, content_type='application/json; charset=utf-8', cookie=None):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
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
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': '로컬 주소로 접속해 주세요.'})
        url = urlparse(self.path)
        if url.path == '/api/state':
            state, now = load(), time.time()
            actor = auth.session_user(state, self.token(), now)
            # 참여 중인 리그만 내려보냅니다. 다른 리그는 초대 링크를 아는 사람만 볼 수 있습니다.
            leagues = [view(l, now) for l in state['leagues'] if actor in l['members']]
            users = {u: n for l in leagues for u, n in l['names'].items()}
            if actor:
                users[actor] = state['users'][actor]['name']
            return self.send(200, dict(actor=actor, users=users, leagues=leagues, now=now))
        if url.path == '/api/invite':
            code = parse_qs(url.query).get('t', [''])[0]
            league = next((l for l in load()['leagues'] if code and l.get('invite') == code), None)
            if league is None:
                return self.send(404, {'error': '초대 링크를 확인해 주세요.'})
            return self.send(200, dict(name=league['name'], kind=league['kind'], start=league['start'], members=len(league['members']), open=time.time() < league['start']))
        if url.path not in FILES:
            return self.send(404, {'error': '페이지를 찾을 수 없습니다.'})
        name, mime = FILES[url.path]
        self.send(200, (ROOT / 'web' / name).read_bytes(), mime)

    def do_POST(self):
        if not self.allowed() or self.headers.get('Origin', f'http://{self.headers.get("Host")}') != f'http://{self.headers.get("Host")}' or self.headers.get('Content-Type') != 'application/json':
            return self.send(403, {'error': '허용되지 않은 요청입니다.'})
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 3000000:
                raise ValueError('요청 크기를 확인해 주세요.')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('요청 형식을 확인해 주세요.')
            token, path = self.token(), self.path
            if path in ('/api/signup', '/api/login', '/api/recover'):
                def enter(state, now):
                    if path == '/api/signup':
                        uid = auth.signup(state, data.get('name'), data.get('pin'))
                    elif path == '/api/login':
                        uid = auth.login(state, data.get('name'), data.get('pin'), now)
                    else:
                        uid = auth.recover(state, data.get('name'), data.get('code'), data.get('pin'), now)
                    auth.end_session(state, token)
                    return auth.start_session(state, uid, now)
                # 틀린 PIN 횟수는 오류가 나도 저장해야 잠금이 동작합니다.
                return self.send(200, {'ok': True}, cookie=session_cookie(change(enter, keep=True)))
            if path == '/api/logout':
                change(lambda state, now: auth.end_session(state, token))
                return self.send(200, {'ok': True}, cookie=session_cookie('', 0))
            if path == '/api/reset':
                def issue(state, now):
                    actor = auth.session_user(state, token, now)
                    require(actor, '먼저 로그인해 주세요.')
                    return auth.reset(state, actor, data.get('target'), now)
                return self.send(200, {'code': change(issue)})
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
    server = HTTPServer(('127.0.0.1', port), Handler)
    print(f'와끼리 로컬 MVP: http://127.0.0.1:{port}', flush=True)
    if DEMO:
        print('가상 회원(닉네임/PIN): 가람/1111 나래/2222 다온/3333 라온/4444', flush=True)
    server.serve_forever()
