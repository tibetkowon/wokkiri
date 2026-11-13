import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import HTTPServer
from pathlib import Path

import app
from domain import KST


class HTTPChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old = app.DB, app.DEMO
        app.DB, app.DEMO = Path(cls.tmp.name) / 'test.sqlite3', True
        cls.server = HTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        app.DB, app.DEMO = cls.old
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
        self.assertNotIn('garam', json.dumps(state))
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
        for secret in ('1111', 'DEMO'):
            self.assertNotIn(secret, json.dumps(saved))
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

    def test_invite_join_and_hidden_leagues(self):
        _, ga, _ = self.login('garam', '1111')
        start = (datetime.now(KST).date() + timedelta(days=2)).isoformat()
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
        with app.connect() as db:
            saved = db.execute('SELECT body FROM state').fetchone()[0]
        for secret in (body['recoveryKey'], signed['recoveryKey'], '5555'):
            self.assertNotIn(secret, saved)
        self.assertEqual(self.request('POST', '/api/recover', dict(login='renewer', key=signed['recoveryKey'], pin='9999'))[0], 400)
        # 실패 횟수는 요청 사이에도 남습니다. 위의 틀린 복구 키 1회에 틀린 PIN 4회를 더해 5회째에 잠기고, 잠긴 동안에는 맞는 PIN도 거부합니다.
        for _ in range(4):
            self.assertEqual(renew('0000')[0], 400)
        status, _, err = renew('5555')
        self.assertEqual(status, 400)
        self.assertIn('분 뒤', err['error'])
        self.assertIn('분 뒤', self.login('renewer', '5555')[2]['error'])


if __name__ == '__main__':
    unittest.main()
