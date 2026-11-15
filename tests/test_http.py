import base64
import http.client
import json
import sqlite3
import tempfile
import importlib.util
import threading
import types
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from http.server import HTTPServer
from pathlib import Path
from unittest import mock

import app
from domain import KST

# 저장 상태 전체에서 비밀값을 찾는 검사는 test_auth.py의 것을 같이 씁니다.
spec = importlib.util.spec_from_file_location('leak_checks', Path(__file__).with_name('test_auth.py'))
leak_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leak_checks)
leaked = leak_checks.leaked


def parts(value):
    """JSON 값 안의 모든 dict 키와 문자열 값입니다."""
    if isinstance(value, dict):
        return set(value).union(*map(parts, value.values()))
    if isinstance(value, list):
        return set().union(*map(parts, value))
    return {value} if isinstance(value, str) else set()


class Server(unittest.TestCase):
    """임시 DB로 서버를 띄웁니다. 시험용 회차는 DB를 만든 날 기준이라 도중에 한국 시간 자정을 넘기면 마감됩니다. 서버 시계를 상수 시각에 고정해 이를 막고, 저장되는 시각 숫자도 매번 같게 합니다."""

    @classmethod
    def clock(cls):
        return datetime(2026, 11, 12, 12, tzinfo=KST).timestamp()

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.now = cls.clock()
        cls.old = app.DB, app.DEMO, app.time, app.LIMITS
        # 한 반의 요청이 모두 같은 주소·같은 시각이라 접속 주소별 제한에 걸립니다. 제한 검사는 DeployChecks가 값을 낮춰서 합니다.
        app.LIMITS = dict(signup=(10 ** 6, 3600), enter=(10 ** 6, 600))
        app.hits.clear()
        # app이 보는 time 이름만 바꿉니다. time.time 자체를 바꾸면 서버 로깅 등 프로세스 전체 시계가 멈춥니다.
        app.DB, app.DEMO, app.time = Path(cls.tmp.name) / 'test.sqlite3', True, types.SimpleNamespace(time=lambda: cls.now)
        cls.server = HTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        app.DB, app.DEMO, app.time, app.LIMITS = cls.old
        app.hits.clear()
        cls.tmp.cleanup()

    def request(self, method, path, data=None, cookie=None, **headers):
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        h = {'Content-Type': 'application/json', **headers}
        if cookie:
            h['Cookie'] = cookie
        con.request(method, path, json.dumps(data) if data is not None else None, h)
        res = con.getresponse()
        status, cookie, raw = res.status, res.getheader('Set-Cookie'), res.read()
        con.close()
        return status, cookie, json.loads(raw)

    def login(self, login, pin):
        return self.request('POST', '/api/login', dict(login=login, pin=pin))

    def state(self, cookie=None):
        return self.request('GET', '/api/state', cookie=cookie)[2]

    def assert_hashed(self, login, pin, *keys):
        """저장 상태에 PIN·복구 키 평문이 없는지 봅니다. PIN은 저장 상태 전체에서 찾되, 우연히 그 숫자열을 담을 수 있는 무작위 값은 가리고 찾습니다."""
        with app.connect() as db:
            raw = db.execute('SELECT body FROM state').fetchone()[0]
        saved = json.loads(raw)
        user = next(u for u in saved['users'].values() if u['login'] == login)
        self.assertEqual(leaked(saved, pin, 'DEMO', *keys), [])
        for value, size in ((user['salt'], 32), (user['pin'], 64), (user['recovery']['salt'], 32), (user['recovery']['key'], 64)):
            self.assertRegex(value, '^[0-9a-f]{%d}$' % size)
        # 복구 키는 12글자라 우연히 겹칠 일이 없어 저장 내용 전체에서 찾습니다.
        for key in keys:
            self.assertNotIn(key, raw)
        self.assertEqual(self.login(login, pin)[0], 200)


class HTTPChecks(Server):

    def test_login_storage_and_boundaries(self):
        status, _, _ = self.request('POST', '/api/action', dict(action='join', invite='x'))
        self.assertEqual(status, 400)
        status, cookie, err = self.login('garam', '0000')
        self.assertEqual((status, cookie, err['error']), (400, None, '아이디 또는 PIN이 맞지 않아요.'))
        self.assertEqual(self.login('nobody', '0000')[2], err)
        self.assertEqual(self.login('가람', '1111')[0], 400)
        status, cookie, body = self.login('Garam', '1111')
        self.assertEqual((status, body), (200, {'ok': True}))
        for part in ('HttpOnly', 'SameSite=Strict', 'Max-Age='):
            self.assertIn(part, cookie)
        state = self.state(cookie)
        self.assertEqual((state['actor'], state['users']), ('ga', {'ga': '가람'}))
        # 아이디는 조회 응답 어디에도 나가지 않습니다.
        self.assertNotIn('garam', json.dumps(state))
        self.assertNotIn('login', parts(state))
        l = state['leagues'][0]
        rnd = l['rounds'][-1]
        data = dict(action='record', league=l['id'], round=rnd['id'], version=0, record=dict(division='rx', type='reps', value=50))
        status, _, _ = self.request('POST', '/api/action', data, cookie, Origin='https://evil.example')
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/action', data, cookie)
        self.assertEqual(status, 200)
        with app.connect() as db:
            saved = json.loads(db.execute('SELECT body FROM state').fetchone()[0])
        self.assertEqual(saved['leagues'][0]['rounds'][-1]['records']['ga']['value'], 50)
        self.assert_hashed('garam', '1111', app.auth.DEMO_KEY, 'DEMODEMODEMO')
        status, _, _ = self.request('POST', '/api/action', data, cookie)
        self.assertEqual(status, 400)
        status, _, _ = self.request('GET', '/api/state', Host='evil.example')
        self.assertEqual(status, 403)
        state = self.state()
        self.assertEqual((state['actor'], state['users'], state['leagues']), (None, {}, []))
        status, _, err = self.request('POST', '/api/action', dict(action='create', kind='daily', start='invalid'), cookie)
        self.assertEqual(status, 400)
        self.assertEqual(err['error'], '입력 형식을 확인해 주세요.')
        self.assertEqual(self.request('POST', '/api/reset', dict(target='na'), cookie)[0], 404)

    def test_static_files_and_cache(self):
        web = Path(app.__file__).parent / 'web'
        for path, name, mime, cache in [('/', 'index.html', 'text/html; charset=utf-8', 'no-store'), ('/app.js', 'app.js', 'text/javascript; charset=utf-8', 'no-store'),
                                        ('/qrcode.js', 'qrcode.js', 'text/javascript; charset=utf-8', 'no-store'), ('/holidays.json', 'holidays.json', 'application/json', 'no-store'), ('/logo.png', 'logo.png', 'image/png', 'public, max-age=86400')]:
            con = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            con.request('GET', path)
            res = con.getresponse()
            self.assertEqual((res.status, res.getheader('Content-Type'), res.getheader('Cache-Control'), res.read()), (200, mime, cache, (web / name).read_bytes()), path)
            con.close()
        self.assertEqual(self.request('GET', '/qrcode.LICENSE.txt')[0], 404)
        self.assertEqual(self.request('GET', '/api/state')[0], 200)
        # 헤더 로고는 42px로만 쓰므로 원본(약 800KB)이 다시 들어오지 않게 합니다.
        self.assertLess((web / 'logo.png').stat().st_size, 50000)

    def test_invite_join_and_hidden_leagues(self):
        _, ga, _ = self.login('garam', '1111')
        start = (datetime.fromtimestamp(self.now, KST).date() + timedelta(days=2)).isoformat()
        _, _, made = self.request('POST', '/api/action', dict(action='create', name='초대 시험', kind='daily', start=start, end=start, dates=[start]), ga)
        invite = next(l['invite'] for l in self.state(ga)['leagues'] if l['id'] == made['league'])
        # 계정 닉네임은 겹쳐도 가입되고, 아이디는 겹치면 안 됩니다.
        status, cookie, body = self.request('POST', '/api/signup', dict(login='friend', name='가람', pin='5555'))
        self.assertEqual(status, 200)
        self.assertRegex(body['recoveryKey'], r'^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$')
        self.assertEqual(self.state(cookie)['leagues'], [])
        for login in ('friend', 'FRIEND', 'garam', 'abc'):
            self.assertEqual(self.request('POST', '/api/signup', dict(login=login, name='다른이름', pin='6666'))[0], 400)
        self.assertEqual(self.request('GET', '/api/invite?t=wrong')[0], 404)
        status, _, info = self.request('GET', f'/api/invite?t={invite}')
        self.assertEqual((status, info['name'], info['members'], info['open']), (200, '초대 시험', 1, True))
        status, _, _ = self.request('POST', '/api/action', dict(action='join', league=made['league']), cookie)
        self.assertEqual(status, 400)
        # 리그 안에서는 닉네임이 겹치면 안 되고, 이 리그에서 쓸 닉네임을 정해 참가합니다.
        status, _, err = self.request('POST', '/api/action', dict(action='join', invite=invite), cookie)
        self.assertEqual((status, err['error']), (400, '이 리그에 같은 닉네임이 있어요. 다른 닉네임을 정해 주세요.'))
        status, _, _ = self.request('POST', '/api/action', dict(action='join', invite=invite, name='가람2'), cookie)
        self.assertEqual(status, 200)
        joined = self.state(cookie)
        self.assertEqual([l['id'] for l in joined['leagues']], [made['league']])
        self.assertEqual(sorted(joined['leagues'][0]['names'].values()), ['가람', '가람2'])
        self.assertEqual(list(joined['users'].values()), ['가람'])

    def test_lockout_survives_between_requests(self):
        for _ in range(5):
            self.assertEqual(self.login('raon', '0000')[0], 400)
        status, cookie, err = self.login('raon', '4444')
        self.assertEqual((status, cookie), (400, None))
        self.assertIn('분 뒤', err['error'])
        # 복구 키를 틀려도 같은 잠금이 요청 사이에 유지됩니다.
        for _ in range(5):
            self.assertEqual(self.request('POST', '/api/recover', dict(login='daon', key='AAAA-AAAA-AAAA', pin='9999'))[0], 400)
        status, cookie, err = self.request('POST', '/api/recover', dict(login='daon', key='DEMO-DEMO-DEMO', pin='9999'))
        self.assertEqual((status, cookie), (400, None))
        self.assertIn('분 뒤', err['error'])

    def test_recover_and_logout(self):
        _, na, _ = self.login('narae', '2222')
        status, cookie, err = self.request('POST', '/api/recover', dict(login='narae', key='XXXX-XXXX-XXXX', pin='9999'))
        self.assertEqual((status, cookie, err['error']), (400, None, '아이디 또는 복구 키가 맞지 않아요.'))
        self.assertEqual(self.state(na)['actor'], 'na')
        status, cookie, body = self.request('POST', '/api/recover', dict(login='narae', key='demo-demo-demo', pin='9999'))
        self.assertEqual(status, 200)
        self.assertRegex(body['recoveryKey'], r'^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$')
        # 기존 기기는 로그아웃되고 새 쿠키로 들어옵니다. 쓴 키는 다시 쓸 수 없습니다.
        self.assertIsNone(self.state(na)['actor'])
        self.assertEqual(self.state(cookie)['actor'], 'na')
        self.assertEqual(self.request('POST', '/api/recover', dict(login='narae', key='DEMO-DEMO-DEMO', pin='8888'))[0], 400)
        self.assertEqual(self.login('narae', '2222')[0], 400)
        status, cookie, _ = self.login('narae', '9999')
        self.assertEqual(status, 200)
        self.assertEqual(self.request('POST', '/api/recover', dict(login='narae', key=body['recoveryKey'], pin='7777'))[0], 200)
        self.assertIsNone(self.state(cookie)['actor'])
        status, cookie, _ = self.login('narae', '7777')
        status, cleared, _ = self.request('POST', '/api/logout', {}, cookie)
        self.assertIn('Max-Age=0', cleared)
        self.assertIsNone(self.state(cookie)['actor'])

    def test_recovery_key_reissue(self):
        _, _, signed = self.request('POST', '/api/signup', dict(login='renewer', name='재발급', pin='5555'))
        _, cookie, _ = self.login('renewer', '5555')
        renew = lambda pin, c=cookie: self.request('POST', '/api/recovery-key', dict(pin=pin), c)
        status, _, err = renew('5555', None)
        self.assertEqual((status, err['error']), (400, '먼저 로그인해 주세요.'))
        status, _, err = renew('0000')
        self.assertEqual((status, err), (400, {'error': 'PIN이 맞지 않아요.'}))
        status, set_cookie, body = renew('5555')
        self.assertEqual((status, set_cookie), (200, None))
        self.assertRegex(body['recoveryKey'], r'^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$')
        self.assertNotEqual(body['recoveryKey'], signed['recoveryKey'])
        # 세션은 그대로이고, 키와 PIN 평문은 저장되지 않습니다.
        self.assertEqual(self.state(cookie)['users'], {self.state(cookie)['actor']: '재발급'})
        self.assert_hashed('renewer', '5555', body['recoveryKey'], signed['recoveryKey'])
        self.assertEqual(self.request('POST', '/api/recover', dict(login='renewer', key=signed['recoveryKey'], pin='9999'))[0], 400)
        # 실패 횟수는 요청 사이에도 남습니다. 위의 틀린 복구 키 1회에 틀린 PIN 4회를 더해 5회째에 잠기고, 잠긴 동안에는 맞는 PIN도 거부합니다.
        for _ in range(4):
            self.assertEqual(renew('0000')[0], 400)
        status, _, err = renew('5555')
        self.assertEqual(status, 400)
        self.assertIn('분 뒤', err['error'])
        self.assertIn('분 뒤', self.login('renewer', '5555')[2]['error'])


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def url(raw, mime='image/png'):
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


def rows():
    with app.connect() as db:
        return db.execute('SELECT id, league, mime, body FROM photos ORDER BY id').fetchall()


def saved():
    with app.connect() as db:
        return db.execute('SELECT body FROM state').fetchone()[0]


class PhotoChecks(Server):
    """사진은 상태 JSON 밖 사진 테이블에 두고, 그 리그 회원에게만 내려보냅니다."""

    def photo(self, pid, cookie=None):
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        con.request('GET', f'/photo/{pid}', headers={'Cookie': cookie} if cookie else {})
        res = con.getresponse()
        out = res.status, res.getheader('Content-Type'), res.getheader('Cache-Control'), res.read()
        con.close()
        return out

    def guide(self, cookie, index, photo):
        l = self.state(cookie)['leagues'][0]
        rnd = l['rounds'][index]
        status, _, body = self.request('POST', '/api/action', dict(action='guide', league=l['id'], round=rnd['id'], version=rnd['guideVersion'], text='WOD', photo=photo), cookie)
        return status, body.get('error'), self.state(cookie)['leagues'][0]['rounds'][index]['guide']['photo']

    def test_upload_access_replace_and_remove(self):
        _, ga, _ = self.login('garam', '1111')
        status, _, pid = self.guide(ga, 0, url(PNG))
        self.assertEqual(status, 200)
        self.assertRegex(pid, '^[0-9a-f]{32}$')
        # 상태 JSON에는 id만 있고 사진 본문은 테이블에 있습니다.
        self.assertNotIn('base64', saved())
        self.assertNotIn('photos', json.loads(saved()))
        self.assertEqual(rows(), [(pid, 'local-demo', 'image/png', PNG)])
        self.assertEqual(self.photo(pid, ga), (200, 'image/png', 'private, max-age=86400', PNG))
        # 같은 리그의 다른 회원은 받고, 비로그인과 다른 리그 회원은 받지 못합니다.
        _, na, _ = self.login('narae', '2222')
        self.assertEqual(self.photo(pid, na)[3], PNG)
        _, other, _ = self.request('POST', '/api/signup', dict(login='outsider', name='밖', pin='5555'))
        start = (datetime.fromtimestamp(self.now, KST).date() + timedelta(days=2)).isoformat()
        self.assertEqual(self.request('POST', '/api/action', dict(action='create', name='다른 리그', kind='daily', start=start, end=start, dates=[start]), other)[0], 200)
        for cookie in (None, other, 'wokkiri=wrong'):
            self.assertEqual(self.photo(pid, cookie)[0], 404)
        self.assertEqual([self.photo(p, ga)[0] for p in ('', '0' * 32, 'x/../y')], [404, 404, 404])
        # 글만 고치면 사진은 그대로이고, 다른 사진으로 바꾸면 옛 사진은 지워져 주소로도 받을 수 없습니다.
        self.assertEqual(self.guide(ga, 0, pid), (200, None, pid))
        self.assertEqual([r[0] for r in rows()], [pid])
        jpeg = b'\xff\xd8\xff' + b'0' * 20
        status, _, second = self.guide(na, 0, url(jpeg, 'image/jpeg'))
        self.assertEqual(rows(), [(second, 'local-demo', 'image/jpeg', jpeg)])
        self.assertEqual((self.photo(pid, ga)[0], self.photo(second, ga)[:2]), (404, (200, 'image/jpeg')))
        # 지우면 고아가 남지 않고, 이력에는 사진 유무만 남습니다.
        self.assertEqual(self.guide(ga, 0, ''), (200, None, ''))
        self.assertEqual(rows(), [])
        history = json.loads(saved())['leagues'][0]['history']
        self.assertEqual([(h['before']['photo'], h['after']['photo']) for h in history if h['action'] == 'WOD 수정'][-4:], [(False, True), (True, True), (True, True), (True, False)])
        self.assertNotIn(pid, saved())

    def test_size_and_league_total_limits(self):
        _, ga, _ = self.login('garam', '1111')
        big = b'\x89PNG\r\n\x1a\n' + bytes(2 * 1024 * 1024)
        self.assertEqual(self.guide(ga, 1, url(big)), (400, '사진은 2MB 이하로 선택해 주세요.', ''))
        self.assertEqual(self.guide(ga, 1, 'data:image/svg+xml;base64,AAAA'), (400, 'PNG 또는 JPEG 사진을 선택해 주세요.', ''))
        with mock.patch.object(app, 'PHOTO_TOTAL', len(PNG) + 10):
            status, _, first = self.guide(ga, 1, url(PNG))
            self.assertEqual(status, 200)
            before = saved()
            status, error, photo = self.guide(ga, 2, url(PNG))
            self.assertEqual((status, photo), (400, ''))
            self.assertIn('50MB', error)
            # 거부된 요청은 상태도 사진도 남기지 않습니다.
            self.assertEqual((saved(), [r[0] for r in rows()]), (before, [first]))
            # 한도에 찬 리그도 있던 사진을 바꾸는 것은 됩니다(옛 사진이 먼저 빠집니다).
            status, _, second = self.guide(ga, 1, url(PNG))
            self.assertEqual((status, [r[0] for r in rows()]), (200, [second]))
        self.assertEqual(self.guide(ga, 1, ''), (200, None, ''))
        self.assertEqual(rows(), [])


class PhotoMigration(unittest.TestCase):
    """상태 JSON 안에 사진을 두던 DB를 서버 시작 처리(prepare)로 올립니다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, value in (('DB', Path(tmp.name) / 'old.sqlite3'), ('DEMO', True)):
            patch = mock.patch.object(app, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def old_db(self, state):
        """사진 테이블이 없던 이전 형식 DB를 만듭니다."""
        with closing(sqlite3.connect(app.DB)) as db:
            db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
            db.execute('INSERT INTO state VALUES (1, ?)', (json.dumps(state),))
            db.commit()

    def test_old_photos_move_once_and_nothing_else_changes(self):
        old = app.fresh()
        league = old['leagues'][0]
        gone, kept = url(b'\xff\xd8\xff' + b'old', 'image/jpeg'), url(PNG)
        league['rounds'][0]['guide'] = dict(text='첫 안내', photo=kept)
        league['rounds'][1]['guide'] = dict(text='둘째 안내', photo=kept)
        league['history'] += [dict(actor='ga', action='WOD 수정', at=1, round='0', before=dict(text='', photo=''), after=dict(text='옛 안내', photo=gone)),
                              dict(actor='ga', action='WOD 수정', at=2, round='0', before=dict(text='옛 안내', photo=gone), after=dict(text='첫 안내', photo=kept))]
        self.old_db(old)
        app.prepare()
        state = json.loads(saved())
        ids = [r['guide']['photo'] for r in state['leagues'][0]['rounds']]
        # 지금 보이는 안내 사진은 그대로 옮겨지고, 이력 속 옛 사진 본문은 유무 표시로 바뀝니다.
        self.assertEqual((len(set(ids[:2])), ids[2]), (2, ''))
        self.assertEqual(sorted(rows()), sorted((i, 'local-demo', 'image/png', PNG) for i in ids[:2]))
        self.assertEqual([(h['before']['photo'], h['after']['photo']) for h in state['leagues'][0]['history'][-2:]], [(False, True), (True, True)])
        self.assertNotIn('base64', saved())
        # 사진 말고는 바뀐 것이 없습니다.
        for s in (state, old):
            for rnd in s['leagues'][0]['rounds']:
                rnd['guide']['photo'] = bool(rnd['guide']['photo'])
            for h in s['leagues'][0]['history'][-2:]:
                h['before']['photo'], h['after']['photo'] = bool(h['before']['photo']), bool(h['after']['photo'])
        self.assertEqual(state, old)
        # 두 번째 실행은 아무것도 바꾸지 않습니다.
        once = saved(), rows()
        app.prepare()
        self.assertEqual((saved(), rows()), once)

    def test_db_without_photos(self):
        self.old_db(app.fresh())
        app.prepare()
        once = saved()
        app.prepare()
        self.assertEqual((saved(), rows()), (once, []))


class DeployChecks(Server):
    """배포 설정: Secure 쿠키, 공개 주소, 접속 주소별 제한, 프록시 헤더."""

    def setUp(self):
        app.hits.clear()
        self.addCleanup(setattr, type(self), 'now', self.now)

    def wrong(self, **headers):
        """없는 아이디로 로그인합니다. 제한에 안 걸리면 400이고 계정 잠금은 건드리지 않습니다."""
        return self.request('POST', '/api/login', dict(login='nobody', pin='0000'), **headers)[0]

    def test_secure_cookie_only_when_enabled(self):
        _, cookie, _ = self.login('garam', '1111')
        self.assertNotIn('Secure', cookie)
        with mock.patch.object(app, 'SECURE', True):
            _, cookie, _ = self.login('garam', '1111')
            self.assertIn('; Secure', cookie)
            self.assertIn('; Secure', self.request('POST', '/api/logout', {}, cookie)[1])

    def test_public_host_and_https_origin(self):
        local = f'127.0.0.1:{self.server.server_port}'
        with mock.patch.object(app, 'HOST', 'wokkiri.example.com'):
            self.assertEqual(self.request('GET', '/api/state')[0], 403)
            self.assertEqual(self.request('GET', '/api/state', Host='wokkiri.example.com')[0], 200)
            self.assertEqual(self.wrong(Host='wokkiri.example.com', Origin='https://wokkiri.example.com'), 400)
            self.assertEqual(self.wrong(Host='wokkiri.example.com'), 400)
            for origin in ('http://wokkiri.example.com', 'https://evil.example', f'http://{local}'):
                self.assertEqual(self.wrong(Host='wokkiri.example.com', Origin=origin), 403)
            self.assertEqual(self.wrong(Origin=f'http://{local}'), 403)
        self.assertEqual(self.wrong(Origin=f'http://{local}'), 400)

    def test_limit_returns_429_until_window_passes(self):
        with mock.patch.object(app, 'LIMITS', dict(signup=(2, 3600), enter=(3, 600))):
            # 로그인·복구·키 재발급은 한 묶음으로 셉니다.
            self.assertEqual(self.wrong(), 400)
            self.assertEqual(self.request('POST', '/api/recover', dict(login='nobody', key='AAAA-AAAA-AAAA', pin='9999'))[0], 400)
            self.assertEqual(self.request('POST', '/api/recovery-key', dict(pin='0000'))[0], 400)
            status, cookie, err = self.login('garam', '1111')
            self.assertEqual((status, cookie, err), (429, None, {'error': '요청이 너무 많아요. 잠시 뒤에 다시 시도해 주세요.'}))
            self.assertEqual(self.request('POST', '/api/recover', dict(login='nobody', key='AAAA-AAAA-AAAA', pin='9999'))[0], 429)
            self.assertEqual(self.request('POST', '/api/recovery-key', dict(pin='0000'))[0], 429)
            # 가입은 따로 세고, 제한과 상관없는 요청은 그대로 됩니다.
            signup = lambda login: self.request('POST', '/api/signup', dict(login=login, name='제한', pin='5555'))[0]
            self.assertEqual([signup('limit_a'), signup('limit_b'), signup('limit_c')], [200, 200, 429])
            self.assertEqual(self.request('GET', '/api/state')[0], 200)
            self.assertEqual(self.request('POST', '/api/logout', {})[0], 200)
            type(self).now += 599
            self.assertEqual(self.wrong(), 429)
            # 10분이 지나면 로그인 묶음만 풀리고, 가입은 1시간이 지나야 풀립니다.
            type(self).now += 1
            self.assertEqual(self.login('garam', '1111')[0], 200)
            self.assertEqual(signup('limit_c'), 429)
            type(self).now += 3000
            self.assertEqual(signup('limit_c'), 200)

    def test_forwarded_for_only_with_trusted_proxy(self):
        with mock.patch.object(app, 'LIMITS', dict(signup=(1, 3600), enter=(1, 600))):
            # 설정이 없으면 헤더를 무시하므로 값을 바꿔 보내도 같은 주소로 셉니다.
            self.assertEqual(self.wrong(**{'X-Forwarded-For': '1.1.1.1'}), 400)
            self.assertEqual(self.wrong(**{'X-Forwarded-For': '2.2.2.2'}), 429)
            self.assertEqual(set(app.hits), {('enter', '127.0.0.1')})
            app.hits.clear()
            with mock.patch.object(app, 'TRUST_PROXY', True):
                # 프록시가 붙인 가장 오른쪽 값만 봅니다. 왼쪽 값은 접속자가 지어낼 수 있습니다.
                self.assertEqual(self.wrong(**{'X-Forwarded-For': '9.9.9.9, 1.1.1.1'}), 400)
                self.assertEqual(self.wrong(**{'X-Forwarded-For': '8.8.8.8, 1.1.1.1'}), 429)
                self.assertEqual(self.wrong(**{'X-Forwarded-For': '1.1.1.1, 2.2.2.2'}), 400)
                self.assertEqual(self.wrong(), 400)
                self.assertEqual(set(app.hits), {('enter', '1.1.1.1'), ('enter', '2.2.2.2'), ('enter', '127.0.0.1')})

    def test_old_hits_are_dropped(self):
        with mock.patch.object(app, 'LIMITS', dict(signup=(10, 3600), enter=(30, 600))):
            for i in range(50):
                self.assertFalse(app.limited('enter', f'10.0.0.{i}', 1000))
                self.assertFalse(app.limited('signup', f'10.0.0.{i}', 1000))
            self.assertEqual(len(app.hits), 100)
            app.limited('enter', 'new', 1700)
            self.assertEqual(len(app.hits), 51)
            app.limited('enter', 'new', 4700)
            self.assertEqual(set(app.hits), {('enter', 'new')})


class MidnightChecks(Server):
    """한국 시간 자정 1초 전에 고정해도 오늘 회차가 열려 있어 로그인과 기록 저장이 됩니다."""

    @classmethod
    def clock(cls):
        return datetime(2027, 1, 1, tzinfo=KST).timestamp() - 1

    def test_login_and_record_one_second_before_midnight(self):
        status, cookie, _ = self.login('garam', '1111')
        state = self.state(cookie)
        self.assertEqual((status, state['actor'], state['now']), (200, 'ga', self.now))
        l = state['leagues'][0]
        rnd = l['rounds'][-1]
        self.assertEqual((rnd['label'], rnd['close']), ('2026-12-31', self.now + 1))
        data = dict(action='record', league=l['id'], round=rnd['id'], version=0, record=dict(division='rx', type='reps', value=50))
        self.assertEqual(self.request('POST', '/api/action', data, cookie)[0], 200)
        self.assertEqual(self.state(cookie)['leagues'][0]['rounds'][-1]['records']['ga']['value'], 50)


if __name__ == '__main__':
    unittest.main()
