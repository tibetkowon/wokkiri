"""닉네임·PIN 가입, 로그인, 세션, PIN 초기화. 외부 서비스에 의존하지 않습니다."""
import hashlib
import hmac
import math
import re
import secrets
import unicodedata
import uuid

from domain import DEMO_NAMES, RuleError, audit, require, text

LOCK_AFTER, LOCK_SECONDS = 5, 600
SESSION_SECONDS = 365 * 86400
RESET_SECONDS = 86400
DEMO_PINS = {'ga': '1111', 'na': '2222', 'da': '3333', 'ra': '4444'}
WRONG = '닉네임 또는 PIN이 맞지 않아요.'


def _hash(secret, salt):
    # shortcut: PIN이 4자리라 DB가 새면 해시와 무관하게 금방 풀립니다. 로그인 시도 제한이 진짜 방어이며, 더 강한 인증이 필요해지면 패스키로 올립니다.
    return hashlib.pbkdf2_hmac('sha256', secret.encode(), bytes.fromhex(salt), 200_000).hex()


def _same(secret, salt, digest):
    return hmac.compare_digest(_hash(secret, salt), digest)


def _pin(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{4}', value), 'PIN은 숫자 4자리로 입력해 주세요.')
    return value


def clean(name):
    """전각·제로폭 문자로 같은 닉네임을 하나 더 만들지 못하게 정규화합니다. shortcut: 모양만 같은 다른 문자(키릴 문자 등)는 막지 못합니다."""
    name = unicodedata.normalize('NFKC', name)
    return ''.join(c for c in name if unicodedata.category(c) not in ('Cc', 'Cf')).strip()


def find(state, name):
    key = clean(name).casefold() if isinstance(name, str) else ''
    return next((u for u, v in state['users'].items() if clean(v['name']).casefold() == key), None)


def _new(state, uid, name, pin):
    salt = secrets.token_hex(16)
    state['users'][uid] = dict(name=name, salt=salt, pin=_hash(pin, salt), fails=0, lockUntil=0, reset=None)
    return uid


def signup(state, name, pin):
    name = text(clean(name) if isinstance(name, str) else name, 12)
    _pin(pin)
    require(find(state, name) is None, '이미 쓰는 닉네임이에요. 다른 닉네임을 골라 주세요.')
    return _new(state, uuid.uuid4().hex[:10], name, pin)


def add_demo_users(state):
    # 로컬 시험 전용입니다. 누구나 아는 PIN이라 배포 서버에서는 WOKKIRI_DEMO를 켜지 않습니다.
    for uid, name in DEMO_NAMES.items():
        if uid not in state['users']:
            _new(state, uid, name, DEMO_PINS[uid])


def _locked(user, now):
    require(now >= user['lockUntil'], f'{math.ceil((user["lockUntil"] - now) / 60)}분 뒤에 다시 시도해 주세요.')


# shortcut: 닉네임만 알면 누구나 그 계정을 10분간 잠글 수 있습니다. 공개 운영에서 장난이 늘면 접속 주소별 제한을 더합니다.
def _fail(state, uid, now, message):
    user = state['users'][uid]
    user['fails'] += 1
    if user['fails'] >= LOCK_AFTER:
        user['fails'], user['lockUntil'] = 0, now + LOCK_SECONDS
        for league in state['leagues']:
            if uid in league['members']:
                audit(league, uid, '로그인에 연달아 실패해 10분간 잠갔어요', now)
    raise RuleError(message)


def login(state, name, pin, now):
    uid = find(state, name)
    require(uid is not None and isinstance(pin, str), WRONG)
    user = state['users'][uid]
    _locked(user, now)
    if not _same(pin, user['salt'], user['pin']):
        _fail(state, uid, now, WRONG)
    # 본인이 들어오면 누군가 발급해 둔 초기화 코드는 더 쓸 수 없습니다.
    user['fails'], user['reset'] = 0, None
    return uid


def reset(state, actor, target, now):
    shared = [l for l in state['leagues'] if actor in l['members'] and target in l['members']]
    require(target != actor and shared, '같은 리그 회원의 PIN만 초기화해 줄 수 있어요.')
    code, salt = f'{secrets.randbelow(10**6):06d}', secrets.token_hex(16)
    state['users'][target]['reset'] = dict(salt=salt, code=_hash(code, salt), until=now + RESET_SECONDS)
    for key in [k for k, s in state['sessions'].items() if s['user'] == target]:
        del state['sessions'][key]
    for league in shared:
        audit(league, actor, 'PIN 초기화 코드 발급', now, member=target)
    return code


def recover(state, name, code, pin, now):
    uid = find(state, name)
    require(uid is not None and isinstance(code, str), '닉네임과 초기화 코드를 확인해 주세요.')
    _pin(pin)
    user = state['users'][uid]
    _locked(user, now)
    r = user['reset']
    if not (r and now < r['until'] and _same(code, r['salt'], r['code'])):
        _fail(state, uid, now, '초기화 코드가 맞지 않거나 만료됐어요.')
    salt = secrets.token_hex(16)
    user.update(salt=salt, pin=_hash(pin, salt), fails=0, reset=None)
    return uid


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
