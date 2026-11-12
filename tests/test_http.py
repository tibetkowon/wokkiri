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

    def login(self, name, pin):
        return self.request('POST', '/api/login', dict(name=name, pin=pin))

    def state(self, cookie=None):
        return self.request('GET', '/api/state', cookie=cookie)[2]

    def test_login_storage_and_boundaries(self):
        status, _, _ = self.request('POST', '/api/action', dict(action='join', invite='x'))
        self.assertEqual(status, 400)
        status, cookie, _ = self.login('가람', '0000')
        self.assertEqual((status, cookie), (400, None))
        status, cookie, _ = self.login('가람', '1111')
        self.assertEqual(status, 200)
        for part in ('HttpOnly', 'SameSite=Strict', 'Max-Age='):
            self.assertIn(part, cookie)
        state = self.state(cookie)
        self.assertEqual((state['actor'], state['users']['ga']), ('ga', '가람'))
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
        self.assertNotIn('1111', json.dumps(saved))
        status, _, _ = self.request('POST', '/api/action', data, cookie)
        self.assertEqual(status, 400)
        status, _, _ = self.request('GET', '/api/state', Host='evil.example')
        self.assertEqual(status, 403)
        state = self.state()
        self.assertEqual((state['actor'], state['leagues']), (None, []))
        status, _, err = self.request('POST', '/api/action', dict(action='create', kind='daily', start='invalid'), cookie)
        self.assertEqual(status, 400)
        self.assertEqual(err['error'], '입력 형식을 확인해 주세요.')

    def test_invite_join_and_hidden_leagues(self):
        _, ga, _ = self.login('가람', '1111')
        start = (datetime.now(KST).date() + timedelta(days=2)).isoformat()
        _, _, made = self.request('POST', '/api/action', dict(action='create', name='초대 시험', kind='daily', start=start, end=start, dates=[start]), ga)
        invite = next(l['invite'] for l in self.state(ga)['leagues'] if l['id'] == made['league'])
        status, cookie, _ = self.request('POST', '/api/signup', dict(name='새친구', pin='5555'))
        self.assertEqual(status, 200)
        self.assertEqual(self.state(cookie)['leagues'], [])
        status, _, _ = self.request('POST', '/api/signup', dict(name='새친구', pin='6666'))
        self.assertEqual(status, 400)
        self.assertEqual(self.request('GET', '/api/invite?t=wrong')[0], 404)
        status, _, info = self.request('GET', f'/api/invite?t={invite}')
        self.assertEqual((status, info['name'], info['members'], info['open']), (200, '초대 시험', 1, True))
        status, _, _ = self.request('POST', '/api/action', dict(action='join', league=made['league']), cookie)
        self.assertEqual(status, 400)
        status, _, _ = self.request('POST', '/api/action', dict(action='join', invite=invite), cookie)
        self.assertEqual(status, 200)
        joined = self.state(cookie)
        self.assertEqual([l['id'] for l in joined['leagues']], [made['league']])
        self.assertEqual(joined['users']['ga'], '가람')

    def test_lockout_survives_between_requests(self):
        for _ in range(5):
            self.assertEqual(self.login('라온', '0000')[0], 400)
        status, cookie, err = self.login('라온', '4444')
        self.assertEqual((status, cookie), (400, None))
        self.assertIn('분 뒤', err['error'])

    def test_reset_recover_and_logout(self):
        _, ga, _ = self.login('가람', '1111')
        _, na, _ = self.login('나래', '2222')
        _, stranger, _ = self.request('POST', '/api/signup', dict(name='외톨이', pin='5555'))
        self.assertEqual(self.request('POST', '/api/reset', dict(target='na'), stranger)[0], 400)
        self.assertEqual(self.request('POST', '/api/reset', dict(target='na'))[0], 400)
        status, _, issued = self.request('POST', '/api/reset', dict(target='na'), ga)
        self.assertEqual(status, 200)
        self.assertIsNone(self.state(na)['actor'])
        status, cookie, _ = self.request('POST', '/api/recover', dict(name='나래', code=issued['code'], pin='9999'))
        self.assertEqual(status, 200)
        self.assertEqual(self.state(cookie)['actor'], 'na')
        self.assertEqual(self.login('나래', '2222')[0], 400)
        status, cookie, _ = self.login('나래', '9999')
        self.assertEqual(status, 200)
        status, cleared, _ = self.request('POST', '/api/logout', {}, cookie)
        self.assertIn('Max-Age=0', cleared)
        self.assertIsNone(self.state(cookie)['actor'])


if __name__ == '__main__':
    unittest.main()
