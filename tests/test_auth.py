import copy
import json
import unittest

import auth
from domain import RuleError, initial

NOW = 1_800_000_000.0


class Accounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = initial(NOW)
        auth.add_demo_users(cls.base)

    def setUp(self):
        self.state = copy.deepcopy(self.base)

    def test_signup_rules(self):
        uid = auth.signup(self.state, ' 새친구 ', '5555')
        self.assertEqual(self.state['users'][uid]['name'], '새친구')
        self.assertNotIn('5555', json.dumps(self.state))
        for name, pin in [('가람', '5555'), (' 가람 ', '5555'), ('', '5555'), ('가' * 13, '5555'),
                          ('새이름', '555'), ('새이름', 'abcd'), ('새이름', '55555'), ('새이름', 5555)]:
            with self.assertRaises(RuleError):
                auth.signup(self.state, name, pin)
        auth.signup(self.state, 'Tom', '1234')
        with self.assertRaises(RuleError):
            auth.signup(self.state, 'tom', '1234')

    def test_login_and_lockout(self):
        self.assertEqual(auth.login(self.state, '가람', '1111', NOW), 'ga')
        for _ in range(5):
            with self.assertRaises(RuleError):
                auth.login(self.state, '가람', '0000', NOW)
        with self.assertRaises(RuleError) as lock:
            auth.login(self.state, '가람', '1111', NOW + 1)
        self.assertIn('분 뒤', str(lock.exception))
        self.assertEqual(auth.login(self.state, '가람', '1111', NOW + 601), 'ga')
        self.assertTrue(any('잠갔어요' in h['action'] for h in self.state['leagues'][0]['history']))
        for name, pin in [('없는사람', '1111'), ('가람', None)]:
            with self.assertRaises(RuleError):
                auth.login(self.state, name, pin, NOW)

    def test_reset_then_recover(self):
        token = auth.start_session(self.state, 'na', NOW)
        code = auth.reset(self.state, 'ga', 'na', NOW)
        self.assertIsNone(auth.session_user(self.state, token, NOW))
        self.assertEqual(self.state['leagues'][0]['history'][-1]['member'], 'na')
        with self.assertRaises(RuleError):
            auth.recover(self.state, '나래', '000000' if code != '000000' else '111111', '9999', NOW)
        self.assertEqual(auth.recover(self.state, '나래', code, '9999', NOW), 'na')
        self.assertEqual(auth.login(self.state, '나래', '9999', NOW), 'na')
        with self.assertRaises(RuleError):
            auth.login(self.state, '나래', '2222', NOW)
        with self.assertRaises(RuleError):
            auth.recover(self.state, '나래', code, '8888', NOW)

    def test_reset_limits(self):
        stranger = auth.signup(self.state, '외톨이', '5555')
        for actor, target in [(stranger, 'na'), ('ga', 'ga'), ('ga', 'nobody')]:
            with self.assertRaises(RuleError):
                auth.reset(self.state, actor, target, NOW)
        code = auth.reset(self.state, 'ga', 'na', NOW)
        with self.assertRaises(RuleError):
            auth.recover(self.state, '나래', code, '9999', NOW + auth.RESET_SECONDS)
        with self.assertRaises(RuleError):
            auth.recover(self.state, '나래', code, '99', NOW)

    def test_owner_login_voids_a_pending_code(self):
        code = auth.reset(self.state, 'ga', 'na', NOW)
        auth.login(self.state, '나래', '2222', NOW)
        with self.assertRaises(RuleError):
            auth.recover(self.state, '나래', code, '9999', NOW)

    def test_sessions(self):
        token = auth.start_session(self.state, 'ga', NOW)
        self.assertEqual(auth.session_user(self.state, token, NOW + 1), 'ga')
        self.assertNotIn(token, json.dumps(self.state['sessions']))
        self.assertIsNone(auth.session_user(self.state, token, NOW + auth.SESSION_SECONDS + 1))
        self.assertIsNone(auth.session_user(self.state, None, NOW))
        auth.end_session(self.state, token)
        self.assertIsNone(auth.session_user(self.state, token, NOW + 1))


if __name__ == '__main__':
    unittest.main()
