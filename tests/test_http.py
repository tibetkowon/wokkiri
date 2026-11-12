import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import HTTPServer
import app


class HTTPChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_db = app.DB
        app.DB = Path(cls.tmp.name) / 'test.sqlite3'
        cls.server = HTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        app.DB = cls.old_db
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

    def test_login_storage_and_boundaries(self):
        status, _, _ = self.request('POST', '/api/action', dict(action='join',league='local-demo'))
        self.assertEqual(status, 400)
        status, cookie, _ = self.request('POST','/api/login',dict(user='ga'))
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',cookie)
        self.assertIn('SameSite=Strict',cookie)
        status, _, state = self.request('GET','/api/state',cookie=cookie)
        self.assertEqual(status,200)
        l=state['leagues'][0]
        rnd=l['rounds'][-1]
        data=dict(action='record',league=l['id'],round=rnd['id'],version=0,record=dict(division='rx',type='reps',value=50))
        status, _, _ = self.request('POST','/api/action',data,cookie,Origin='https://evil.example')
        self.assertEqual(status,403)
        status, _, _ = self.request('POST','/api/action',data,cookie)
        self.assertEqual(status,200)
        with app.connect() as db:
            saved=json.loads(db.execute('SELECT body FROM state').fetchone()[0])
        self.assertEqual(saved['leagues'][0]['rounds'][-1]['records']['ga']['value'],50)
        status, _, _ = self.request('POST','/api/action',data,cookie)
        self.assertEqual(status,400)
        status, _, _ = self.request('GET','/api/state',Host='evil.example')
        self.assertEqual(status,403)
        status, _, state = self.request('GET','/api/state')
        self.assertNotIn('rounds',state['leagues'][0])
        status, _, err = self.request('POST','/api/action',dict(action='create',kind='daily',start='invalid'),cookie)
        self.assertEqual(status,400)
        self.assertEqual(err['error'],'입력 형식을 확인해 주세요.')
