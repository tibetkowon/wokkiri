import copy
import json
import unittest
import uuid
from unittest import mock

import auth
from domain import RuleError, initial, migrate, view

NOW = 1_800_000_000.0


class Accounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = initial(NOW)
        auth.add_demo_users(cls.base)

    def setUp(self):
        self.state = copy.deepcopy(self.base)

    def test_signup_rules(self):
        uid, key = auth.signup(self.state, ' New_Friend1 ', ' 새친구 ', '5555')
        user = self.state['users'][uid]
        self.assertEqual((user['login'], user['name']), ('new_friend1', '새친구'))
        self.assertRegex(key, r'^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}(-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){2}$')
        saved = json.dumps(self.state)
        for secret in ('5555', key, key.replace('-', '')):
            self.assertNotIn(secret, saved)
        for login, name, pin in [('abc', '이름', '5555'), ('a' * 21, '이름', '5555'), ('한글아이디', '이름', '5555'), ('has space', '이름', '5555'),
                                 ('dash-id', '이름', '5555'), (None, '이름', '5555'), ('new_friend1', '이름', '5555'), ('NEW_FRIEND1', '이름', '5555'),
                                 ('garam', '이름', '5555'), ('fresh', '', '5555'), ('fresh', '가' * 13, '5555'), ('fresh', '​​', '5555'),
                                 ('fresh', None, '5555'), ('fresh', '이름', '555'), ('fresh', '이름', 'abcd'), ('fresh', '이름', 5555)]:
            with self.assertRaises(RuleError, msg=(login, name, pin)):
                auth.signup(self.state, login, name, pin)
        self.assertIsNone(auth.find(self.state, 'fresh'))
        # 닉네임은 사이트 전체에서 겹쳐도 되고, 전각·제로폭 문자는 정규화해 저장합니다.
        twin, _ = auth.signup(self.state, 'twin', '가람', '5555')
        wide, _ = auth.signup(self.state, 'wide', 'Ｔ​om', '5555')
        self.assertEqual((self.state['users'][twin]['name'], self.state['users'][wide]['name']), ('가람', 'Tom'))
        self.assertEqual(auth.login(self.state, ' TWIN ', '5555', NOW), twin)
        self.assertEqual(auth.login(self.state, 'garam', '1111', NOW), 'ga')

    def test_login_and_lockout(self):
        self.assertEqual(auth.login(self.state, 'garam', '1111', NOW), 'ga')
        for _ in range(5):
            with self.assertRaises(RuleError) as wrong:
                auth.login(self.state, 'garam', '0000', NOW)
            self.assertEqual(str(wrong.exception), auth.WRONG)
        with self.assertRaises(RuleError) as lock:
            auth.login(self.state, 'garam', '1111', NOW + 1)
        self.assertIn('분 뒤', str(lock.exception))
        self.assertEqual(auth.login(self.state, 'garam', '1111', NOW + 601), 'ga')
        self.assertTrue(any('잠갔어요' in h['action'] for h in self.state['leagues'][0]['history']))
        # 없는 아이디도 같은 문구이고, 닉네임으로는 로그인할 수 없습니다.
        for login, pin in [('nobody', '1111'), ('가람', '1111'), ('garam', None), (None, '1111')]:
            with self.assertRaises(RuleError) as wrong:
                auth.login(self.state, login, pin, NOW + 601)
            self.assertEqual(str(wrong.exception), auth.WRONG)

    def test_recover_rotates_key_and_ends_sessions(self):
        uid, key = auth.signup(self.state, 'owner', '주인', '5555')
        old = auth.start_session(self.state, uid, NOW)
        other = auth.start_session(self.state, 'ga', NOW)
        for login, bad, pin in [('owner', 'AAAA-AAAA-AAAA', '9999'), ('owner', '', '9999'), ('owner', None, '9999'), ('nobody', key, '9999'), ('owner', key, '99')]:
            with self.assertRaises(RuleError):
                auth.recover(self.state, login, bad, pin, NOW)
        self.assertEqual(auth.session_user(self.state, old, NOW), uid)
        # 대소문자·하이픈·공백은 무시합니다.
        got, fresh = auth.recover(self.state, 'OWNER', f' {key.lower().replace("-", " ")} ', '9999', NOW)
        self.assertEqual(got, uid)
        self.assertRegex(fresh, r'^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$')
        self.assertNotEqual(fresh, key)
        self.assertIsNone(auth.session_user(self.state, old, NOW))
        self.assertEqual(auth.session_user(self.state, other, NOW), 'ga')
        self.assertEqual(auth.login(self.state, 'owner', '9999', NOW), uid)
        with self.assertRaises(RuleError):
            auth.login(self.state, 'owner', '5555', NOW)
        # 쓴 키는 다시 쓸 수 없고 새 키만 통합니다.
        with self.assertRaises(RuleError):
            auth.recover(self.state, 'owner', key, '8888', NOW)
        self.assertEqual(auth.recover(self.state, 'owner', fresh, '8888', NOW)[0], uid)
        self.assertNotIn(fresh, json.dumps(self.state))

    def test_recover_lockout(self):
        for _ in range(5):
            with self.assertRaises(RuleError) as wrong:
                auth.recover(self.state, 'narae', 'AAAA-AAAA-AAAA', '9999', NOW)
            self.assertEqual(str(wrong.exception), auth.WRONG_KEY)
        # 복구 실패도 로그인과 같은 잠금을 씁니다.
        for attempt in (lambda: auth.recover(self.state, 'narae', auth.DEMO_KEY, '9999', NOW + 1), lambda: auth.login(self.state, 'narae', '2222', NOW + 1)):
            with self.assertRaises(RuleError) as lock:
                attempt()
            self.assertIn('분 뒤', str(lock.exception))
        self.assertEqual(auth.recover(self.state, 'narae', 'demo demo demo', '9999', NOW + 601)[0], 'na')

    def test_renew_key_needs_current_pin(self):
        uid, key = auth.signup(self.state, 'owner', '주인', '5555')
        session = auth.start_session(self.state, uid, NOW)
        fresh = auth.renew_key(self.state, uid, '5555', NOW)
        self.assertRegex(fresh, r'^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$')
        self.assertNotEqual(fresh, key)
        self.assertNotIn(fresh, json.dumps(self.state))
        # 세션은 그대로이고, 옛 키는 거부되며 새 키만 통합니다.
        self.assertEqual(auth.session_user(self.state, session, NOW), uid)
        with self.assertRaises(RuleError):
            auth.recover(self.state, 'owner', key, '9999', NOW)
        self.assertEqual(auth.recover(self.state, 'owner', fresh, '9999', NOW)[0], uid)
        # 틀린 PIN은 키를 바꾸지 않고 실패 횟수를 올리며, 로그인과 같은 잠금을 씁니다.
        saved = copy.deepcopy(self.state['users']['ga']['recovery'])
        for n, pin in enumerate(['0000', None, '11111', '0000'], 1):
            with self.assertRaises(RuleError):
                auth.renew_key(self.state, 'ga', pin, NOW)
            self.assertEqual((self.state['users']['ga']['fails'], self.state['users']['ga']['recovery']), (n, saved))
        with self.assertRaises(RuleError):
            auth.login(self.state, 'garam', '0000', NOW)
        for attempt in (lambda: auth.renew_key(self.state, 'ga', '1111', NOW + 1), lambda: auth.login(self.state, 'garam', '1111', NOW + 1)):
            with self.assertRaises(RuleError) as lock:
                attempt()
            self.assertIn('분 뒤', str(lock.exception))
        self.assertEqual(self.state['users']['ga']['recovery'], saved)
        auth.renew_key(self.state, 'ga', '1111', NOW + 601)
        with self.assertRaises(RuleError):
            auth.recover(self.state, 'garam', auth.DEMO_KEY, '9999', NOW + 601)

    def test_renew_key_gives_migrated_account_a_key(self):
        user = self.state['users']['na']
        del user['login']
        user['recovery'] = None
        migrate(self.state)
        self.assertEqual((user['login'], user['recovery']), ('narae', None))
        fresh = auth.renew_key(self.state, 'na', '2222', NOW)
        self.assertEqual(auth.recover(self.state, 'narae', fresh, '9999', NOW)[0], 'na')

    def test_migrate_fills_login_and_drops_reset(self):
        uid = 'a1b2c3d4e5'
        league = copy.deepcopy(self.state['leagues'][0])
        league['members'].append(uid)
        league['names'][uid] = '옛친구'
        league['history'].append(dict(actor=uid, action='리그 참가', at=NOW))
        old = dict(leagues=[league], users={
            'ga': dict(name='가람', salt='00', pin='x', fails=0, lockUntil=0, reset=dict(salt='00', code='x', until=NOW)),
            uid: dict(name='옛친구', salt='00', pin='x', fails=0, lockUntil=0, reset=None)})
        once = copy.deepcopy(migrate(old))
        self.assertEqual(migrate(old), once)
        self.assertEqual({u: (v['recovery'], 'reset' in v) for u, v in old['users'].items()}, {'ga': (None, False), uid: (None, False)})
        self.assertEqual(old['users']['ga']['login'], 'garam')
        # uid는 리그 명단·이력으로 다른 회원에게 보이므로 아이디가 되면 안 되고, 아이디는 조회 응답에 나오지 않습니다.
        login = old['users'][uid]['login']
        self.assertRegex(login, r'^[0-9a-f]{16}$')
        self.assertIsNone(auth.find(old, uid))
        with self.assertRaises(RuleError):
            auth.login(old, uid, 'x', NOW)
        shown = json.dumps(view(old['leagues'][0], NOW))
        self.assertIn(uid, shown)
        self.assertNotIn(login, shown)
        # 변환된 계정은 복구 키가 없어 복구할 수 없습니다.
        with self.assertRaises(RuleError):
            auth.recover(old, 'garam', auth.DEMO_KEY, '9999', NOW)
        # 이전 중간 수정본이 login=uid로 채운 계정도 무작위 값으로 바뀌고, 정상 아이디와 가상 회원은 그대로입니다.
        old['users'][uid]['login'] = uid
        old['users']['f0e1d2c3b4'] = dict(login='real_id', name='새친구', salt='00', pin='x', fails=0, lockUntil=0, recovery=None)
        once = copy.deepcopy(migrate(old))
        self.assertEqual(migrate(old), once)
        fixed = old['users'][uid]['login']
        self.assertRegex(fixed, r'^[0-9a-f]{16}$')
        self.assertNotIn(fixed, (uid, login))
        self.assertIsNone(auth.find(old, uid))
        self.assertNotIn(fixed, json.dumps(view(old['leagues'][0], NOW)))
        self.assertEqual((old['users']['f0e1d2c3b4']['login'], old['users']['ga']['login']), ('real_id', 'garam'))
        # 가입 아이디가 우연히 uid와 같아도 복구 키가 있는 정상 계정이면 바꾸지 않습니다.
        same = dict(login='0123456789', name='우연', salt='00', pin='x', fails=0, lockUntil=0, recovery=dict(salt='00', key='x'))
        old['users']['0123456789'] = copy.deepcopy(same)
        self.assertEqual(migrate(old)['users']['0123456789'], same)

    def test_signup_uid_never_equals_login_or_existing_uid(self):
        taken, _ = auth.signup(self.state, 'first', '먼저', '5555')
        made = [uuid.UUID(hex=h.ljust(32, '0')) for h in ('abcdef0123', taken, 'abcdef0123', '9876543210')]
        with mock.patch('auth.uuid.uuid4', side_effect=made):
            uid, _ = auth.signup(self.state, 'ABCDEF0123', '우연', '5555')
        self.assertEqual((uid, self.state['users'][uid]['login']), ('9876543210', 'abcdef0123'))
        self.assertEqual(self.state['users'][taken]['login'], 'first')
        # 이미 새 구조인 계정은 변환해도 그대로입니다.
        before = copy.deepcopy(self.state['users'])
        self.assertEqual(migrate(self.state)['users'], before)

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
