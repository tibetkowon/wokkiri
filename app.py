"""로컬 전용 서버: python3 app.py"""
import json
import os
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse
from domain import USERS, RuleError, apply, initial, view

ROOT = Path(__file__).parent
DB = Path(os.environ.get('WOKKIRI_DB', str(ROOT / 'data' / 'wokkiri.sqlite3')))
SESSIONS = {}


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    # shortcut: 로컬 소규모 상태만 단일 행에 저장하며, 실제 다중 사용자 운영 전에 테이블을 분리합니다.
    db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO state VALUES (1, ?)', (json.dumps(initial(time.time())),))
    db.commit()
    return db


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

    def actor(self):
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        token = cookie.get('wokkiri')
        return SESSIONS.get(token.value) if token else None

    def allowed(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': '로컬 주소로 접속해 주세요.'})
        path = urlparse(self.path).path
        if path == '/api/state':
            actor = self.actor()
            with connect() as db:
                state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            leagues = [view(l, time.time()) if actor in l['members'] else {k: l[k] for k in ('id', 'name', 'kind', 'start', 'members')} for l in state['leagues']]
            return self.send(200, dict(actor=actor, users=USERS, leagues=leagues, now=time.time()))
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/logo.png': ('logo.png', 'image/png')}
        if path not in files:
            return self.send(404, {'error': '페이지를 찾을 수 없습니다.'})
        name, mime = files[path]
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
            if self.path == '/api/login':
                if data.get('user') not in USERS:
                    raise ValueError('시험 계정을 선택해 주세요.')
                token = secrets.token_urlsafe(32)
                old = SimpleCookie(self.headers.get('Cookie', '')).get('wokkiri')
                if old:
                    SESSIONS.pop(old.value, None)
                SESSIONS[token] = data['user']
                return self.send(200, {'ok': True}, cookie=f'wokkiri={token}; HttpOnly; SameSite=Strict; Path=/')
            if self.path != '/api/action':
                return self.send(404, {'error': '작업을 찾을 수 없습니다.'})
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
                lid = apply(state, self.actor(), data, time.time())
                db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
            self.send(200, {'league': lid})
        except (ValueError, KeyError, TypeError, OverflowError) as err:
            self.send(400, {'error': str(err) if isinstance(err, RuleError) else '입력 형식을 확인해 주세요.'})
        except sqlite3.Error:
            self.send(500, {'error': '저장하지 못했습니다. 잠시 후 다시 시도해 주세요.'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    server = HTTPServer(('127.0.0.1', port), Handler)
    print(f'와끼리 로컬 MVP: http://127.0.0.1:{port}', flush=True)
    server.serve_forever()
