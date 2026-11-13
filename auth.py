"""아이디·PIN 가입, 로그인, 세션, 복구 키. 외부 서비스에 의존하지 않습니다."""
import hashlib
import hmac
import math
import re
import secrets
import uuid

from domain import DEMO_LOGINS, DEMO_NAMES, RuleError, audit, clean, require, text

LOCK_AFTER, LOCK_SECONDS = 5, 600
SESSION_SECONDS = 365 * 86400
DEMO_PINS = {'ga': '1111', 'na': '2222', 'da': '3333', 'ra': '4444'}
DEMO_KEY = 'DEMO-DEMO-DEMO'
# 0·O, 1·I처럼 헷갈리는 글자를 뺀 32자입니다. 12글자면 60비트입니다.
KEY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
WRONG = '아이디 또는 PIN이 맞지 않아요.'
WRONG_KEY = '아이디 또는 복구 키가 맞지 않아요.'


def _hash(secret, salt):
    # shortcut: PIN이 4자리라 DB가 새면 해시와 무관하게 금방 풀립니다. 로그인 시도 제한이 진짜 방어이며, 더 강한 인증이 필요해지면 패스키로 올립니다.
    return hashlib.pbkdf2_hmac('sha256', secret.encode(), bytes.fromhex(salt), 200_000).hex()


def _same(secret, salt, digest):
    return hmac.compare_digest(_hash(secret, salt), digest)


def _pin(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{4}', value), 'PIN은 숫자 4자리로 입력해 주세요.')
    return value


def _login(value):
    return value.strip().lower() if isinstance(value, str) else ''


def find(state, login):
    key = _login(login)
    return next((u for u, v in state['users'].items() if v['login'] == key), None)


def _set_pin(user, pin):
    salt = secrets.token_hex(16)
    user.update(salt=salt, pin=_hash(pin, salt), fails=0)


def _key_text(key):
    return re.sub(r'[\s-]', '', key).upper()


def _new_key(user, key=None):
    """복구 키를 새로 정하고 평문을 돌려줍니다. 서버에는 해시만 남습니다."""
    key = key or '-'.join(''.join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(3))
    salt = secrets.token_hex(16)
    user['recovery'] = dict(salt=salt, key=_hash(_key_text(key), salt))
    return key


def _new(state, uid, login, name, pin, key=None):
    user = state['users'][uid] = dict(login=login, name=name, lockUntil=0)
    _set_pin(user, pin)
    return _new_key(user, key)


def signup(state, login, name, pin):
    """계정을 만들고 (uid, 복구 키 평문)을 돌려줍니다."""
    login = _login(login)
    require(re.fullmatch(r'[a-z0-9_]{4,20}', login), '아이디는 영문 소문자·숫자·밑줄 4~20자로 정해 주세요.')
    name = text(clean(name) if isinstance(name, str) else name, 12)
    _pin(pin)
    require(find(state, login) is None, '이미 쓰는 아이디예요. 다른 아이디를 골라 주세요.')
    uid = uuid.uuid4().hex[:10]
    # uid는 리그 명단·이력으로 다른 회원에게 보입니다. 아이디와 같거나 이미 있는 uid면 다시 만듭니다.
    while uid == login or uid in state['users']:
        uid = uuid.uuid4().hex[:10]
    return uid, _new(state, uid, login, name, pin)


def add_demo_users(state):
    # 로컬 시험 전용입니다. 누구나 아는 PIN·복구 키라 배포 서버에서는 WOKKIRI_DEMO를 켜지 않습니다.
    for uid, name in DEMO_NAMES.items():
        if uid not in state['users'] and find(state, DEMO_LOGINS[uid]) is None:
            _new(state, uid, DEMO_LOGINS[uid], name, DEMO_PINS[uid], DEMO_KEY)


def _locked(user, now):
    require(now >= user['lockUntil'], f'{math.ceil((user["lockUntil"] - now) / 60)}분 뒤에 다시 시도해 주세요.')


# shortcut: 아이디만 알면 누구나 그 계정을 10분간 잠글 수 있습니다. 공개 운영에서 장난이 늘면 접속 주소별 제한을 더합니다.
def _fail(state, uid, now, message):
    user = state['users'][uid]
    user['fails'] += 1
    if user['fails'] >= LOCK_AFTER:
        user['fails'], user['lockUntil'] = 0, now + LOCK_SECONDS
        for league in state['leagues']:
            if uid in league['members']:
                audit(league, uid, '로그인에 연달아 실패해 10분간 잠갔어요', now)
    raise RuleError(message)


def login(state, login, pin, now):
    uid = find(state, login)
    require(uid is not None and isinstance(pin, str), WRONG)
    _check_pin(state, uid, pin, now, WRONG)
    return uid


def _check_pin(state, uid, pin, now, message):
    user = state['users'][uid]
    _locked(user, now)
    if not (isinstance(pin, str) and _same(pin, user['salt'], user['pin'])):
        _fail(state, uid, now, message)
    user['fails'] = 0


def renew_key(state, uid, pin, now):
    """로그인한 계정이 현재 PIN을 다시 넣으면 복구 키를 새로 만들어 평문을 돌려줍니다. 기존 키는 폐기하고 세션은 그대로 둡니다."""
    _check_pin(state, uid, pin, now, 'PIN이 맞지 않아요.')
    return _new_key(state['users'][uid])


def recover(state, login, key, pin, now):
    """복구 키로 PIN을 바꾸고 (uid, 새 복구 키 평문)을 돌려줍니다. 쓴 키는 폐기하고 기존 세션을 모두 끊습니다."""
    uid = find(state, login)
    require(uid is not None and isinstance(key, str), WRONG_KEY)
    _pin(pin)
    user = state['users'][uid]
    _locked(user, now)
    r = user['recovery']
    if not (r and _same(_key_text(key), r['salt'], r['key'])):
        _fail(state, uid, now, WRONG_KEY)
    _set_pin(user, pin)
    for token in [k for k, s in state['sessions'].items() if s['user'] == uid]:
        del state['sessions'][token]
    return uid, _new_key(user)


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(state, uid, now):
    sessions = state['sessions']
    for key in [k for k, s in sessions.items() if now - s['at'] > SESSION_SECONDS]:
        del sessions[key]
    token = secrets.token_urlsafe(32)
    sessions[_digest(token)] = dict(user=uid, at=now)
    return token


def end_session(state, token):
    if token:
        state['sessions'].pop(_digest(token), None)


def session_user(state, token, now):
    s = state['sessions'].get(_digest(token)) if token else None
    return s['user'] if s and now - s['at'] <= SESSION_SECONDS else None
