"""와끼리의 점수·일정·권한·정정 상태 전이. 외부 서비스에 의존하지 않습니다."""
import base64
import copy
import math
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone, date

KST = timezone(timedelta(hours=9))
DEMO_NAMES = {'ga': '가람', 'na': '나래', 'da': '다온', 'ra': '라온'}
DEMO_LOGINS = {'ga': 'garam', 'na': 'narae', 'da': 'daon', 'ra': 'raon'}
TYPES = {'time': '시간형', 'amrap': 'AMRAP', 'reps': '횟수형', 'weight': '중량형', 'done': '완료형'}


class RuleError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise RuleError(message)


def stamp(day):
    return datetime.combine(date.fromisoformat(day), datetime.min.time(), KST).timestamp()


def text(value, limit=1000):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit, '입력 길이를 확인해 주세요.')
    return value.strip()


def clean(name):
    """전각·제로폭 문자로 같은 닉네임을 하나 더 만들지 못하게 정규화합니다. shortcut: 모양만 같은 다른 문자(키릴 문자 등)는 막지 못합니다."""
    name = unicodedata.normalize('NFKC', name)
    return ''.join(c for c in name if unicodedata.category(c) not in ('Cc', 'Cf')).strip()


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, '정수 입력 범위를 확인해 주세요.')
    return value


def record(value, kind):
    require(isinstance(value, dict), '기록을 입력해 주세요.')
    if value.get('division') == 'attendance':
        require(kind == 'daily', '지정 WOD는 기록을 등록해 주세요.')
        return {'division': 'attendance'}
    require(value.get('division') in ('rx', 'scaled') and value.get('type') in TYPES, '수행 부문과 기록 유형을 확인해 주세요.')
    r = {'division': value['division'], 'type': value['type']}
    t = r['type']
    if t == 'time':
        require(type(value.get('finished')) is bool, '완주 여부를 선택해 주세요.')
        r['finished'] = value['finished']
        r['value'] = integer(value.get('value'), 1 if r['finished'] else 0, 10000000)
    elif t == 'amrap':
        r['rounds'] = integer(value.get('rounds'), 0, 100000)
        r['extra'] = integer(value.get('extra'), 0, 100000)
    elif t == 'done':
        require(type(value.get('finished')) is bool, '완료 또는 실패를 선택해 주세요.')
        r['finished'] = value['finished']
    elif t == 'weight':
        v = value.get('value')
        require(type(v) in (int, float) and math.isfinite(v) and 0 < v <= 10000, '모든 지정 횟수를 성공한 중량(lb)을 입력해 주세요.')
        r['value'] = v
    else:
        r['value'] = integer(value.get('value'), 0, 10000000)
    return r


def score_key(r):
    d = {'rx': 0, 'scaled': 1, 'attendance': 2}[r['division']]
    if d == 2:
        return (d,)
    t = r['type']
    if t == 'time':
        return (d, 0 if r['finished'] else 1, r['value'] if r['finished'] else -r['value'])
    if t == 'amrap':
        return (d, -r['rounds'], -r['extra'])
    if t == 'done':
        return (d, 0 if r['finished'] else 1)
    return (d, -r['value'])


def round_scores(league, rnd):
    records = rnd['records']
    counts = {}
    for r in records.values():
        if r and r['division'] != 'attendance':
            counts[r['type']] = counts.get(r['type'], 0) + 1
    mismatch = len(counts) > 1
    members = league['members']
    names = lambda u: league['names'][u]
    ordered = sorted(members, key=names) if mismatch else sorted(members, key=lambda u: (score_key(records[u]) if records.get(u) else (3,), names(u)))
    points, previous, rank = {}, None, 0
    for i, u in enumerate(ordered):
        r = records.get(u)
        if mismatch:
            points[u] = None
        elif not r:
            points[u] = len(members)
        else:
            key = score_key(r)
            if key != previous:
                rank = i + 1
            points[u], previous = rank, key
    return {'mismatch': mismatch, 'counts': counts, 'order': ordered, 'points': points}


def view(league, now):
    result = copy.deepcopy(league)
    # 없앤 기능(회원이 만들어 주던 PIN 초기화 코드)의 이력은 DB에만 두고 내려보내지 않습니다.
    result['history'] = [h for h in result['history'] if 'member' not in h and h['action'] != 'PIN 초기화 코드 발급']
    totals = {u: 0 for u in league['members']}
    pending = False
    for rnd in result['rounds']:
        rnd['score'] = round_scores(league, rnd)
        active, closed = now >= rnd['start'], now >= rnd['close']
        # 마감 전에는 미출석·미참여에 N점을 매기지 않고(화면은 "집계 전"), 회차 점수도 누적에 넣지 않습니다.
        if not closed and not rnd['score']['mismatch']:
            rnd['score']['points'].update({u: None for u in league['members'] if not rnd['records'].get(u)})
        rnd['counted'] = closed and not rnd['score']['mismatch']
        pending |= active and (rnd['score']['mismatch'] or not closed)
        if rnd['counted']:
            for u, p in rnd['score']['points'].items():
                totals[u] += p
    ordered = sorted(totals, key=lambda u: (totals[u], league['names'][u]))
    result['standings'] = []
    last, rank = None, 0
    for i, u in enumerate(ordered):
        if totals[u] != last:
            rank = i + 1
        result['standings'].append({'user': u, 'points': totals[u], 'rank': rank})
        last = totals[u]
    result['requestClose'] = league['rounds'][-1]['close'] + 72 * 3600
    result['final'] = now >= result['requestClose'] and not any(r['score']['mismatch'] for r in result['rounds']) and all(q['status'] == 'closed' for q in league['requests'])
    result['provisional'] = pending or not result['final']
    return result


def audit(league, actor, action, now, **detail):
    league['history'].append(dict(actor=actor, action=action, at=now, **copy.deepcopy(detail)))


def put(league, rnd, target, proposal, actor, now, reason):
    before = rnd['records'].get(target)
    rnd['records'][target] = proposal
    rnd['versions'][target] = rnd['versions'].get(target, 0) + 1
    audit(league, actor, reason, now, round=rnd['id'], target=target, before=before, after=proposal)


def stash(state, league, photo):
    """data URL 사진을 검사해 state['photos']에 잠시 싣고 id를 돌려줍니다. app.change가 꺼내 사진 테이블에 넣으므로 상태 JSON에는 id만 남습니다."""
    prefix, encoded = photo.split(',', 1)
    raw = base64.b64decode(encoded, validate=True)
    require(len(raw) <= 2*1024*1024, '사진은 2MB 이하로 선택해 주세요.')
    require(((prefix == 'data:image/png;base64' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (prefix == 'data:image/jpeg;base64' and raw.startswith(b'\xff\xd8\xff'))), 'PNG 또는 JPEG 사진을 선택해 주세요.')
    pid = uuid.uuid4().hex
    state.setdefault('photos', {})[pid] = dict(league=league['id'], mime=prefix[5:-7], body=raw)
    return pid


def schedule(data):
    start = date.fromisoformat(data['start'])
    if data['kind'] == 'daily':
        end = date.fromisoformat(data['end'])
        require(0 <= (end-start).days <= 366, '리그 기간은 1년 이내로 선택해 주세요.')
        days = sorted(set(data['dates']))
        require(days and len(days) <= 367, '집계일을 선택해 주세요.')
        require(all(start <= date.fromisoformat(d) <= end for d in days), '기간 안의 집계일을 선택해 주세요.')
        spans = [(d, stamp(d), stamp(d) + 86400) for d in days]
    else:
        count = integer(data.get('count'), 1, 52)
        duration = integer(data.get('duration'), 1, 30)
        spans = [(f'{i+1}라운드', stamp(start.isoformat()) + i*duration*86400, stamp(start.isoformat()) + (i+1)*duration*86400) for i in range(count)]
    return [dict(id=str(i), label=label, start=s, close=e, records={}, versions={}, guide={'text': '', 'photo': ''}, guideVersion=0) for i, (label, s, e) in enumerate(spans)]


def apply(state, actor, data, now):
    require(actor in state['users'], '먼저 로그인해 주세요.')
    action = data.get('action')
    if action == 'create':
        require(data.get('kind') in ('daily', 'designated'), '리그 종류를 선택해 주세요.')
        rounds = schedule(data)
        require(stamp(data['start']) > now, '참가자를 모집할 수 있도록 첫 집계일은 내일 이후로 선택해 주세요.')
        lid = uuid.uuid4().hex[:12]
        state['leagues'].append(dict(id=lid, name=text(data.get('name'), 60), kind=data['kind'], start=stamp(data['start']), owner=actor, members=[actor], names={actor: state['users'][actor]['name']}, invite=secrets.token_urlsafe(12), rounds=rounds, requests=[], history=[]))
        return lid
    if action == 'join':
        token = data.get('invite')
        league = next((l for l in state['leagues'] if isinstance(token, str) and l.get('invite') == token), None)
        require(league is not None, '초대 링크를 확인해 주세요.')
        require(now < league['start'], '이미 시작한 리그라 참가할 수 없어요.')
        require(actor not in league['members'], '이미 참가하셨어요.')
        # 계정 닉네임은 겹쳐도 되지만 한 리그 안에서는 겹치지 않게, 이 리그에서 쓸 닉네임을 따로 받을 수 있습니다.
        name = data.get('name')
        name = state['users'][actor]['name'] if name is None else text(clean(name) if isinstance(name, str) else name, 12)
        taken = {clean(n).casefold() for n in league['names'].values()}
        require(clean(name).casefold() not in taken, '이 리그에 같은 닉네임이 있어요. 다른 닉네임을 정해 주세요.')
        league['members'].append(actor)
        league['names'][actor] = name
        audit(league, actor, '리그 참가', now)
        return league['id']
    league = next((l for l in state['leagues'] if l['id'] == data.get('league')), None)
    require(league is not None, '리그를 찾을 수 없습니다.')
    require(actor in league['members'], '리그 참가자만 이용하실 수 있습니다.')
    rnd = next((r for r in league['rounds'] if r['id'] == data.get('round')), None)
    if action in ('record', 'guide', 'request'):
        require(rnd is not None and now >= rnd['start'], '시작된 회차를 선택해 주세요.')
    if action == 'record':
        require(data.get('target', actor) == actor, '다른 회원의 기록은 직접 고칠 수 없어요.')
        require(data.get('version') == rnd['versions'].get(actor, 0), '기록이 변경되었습니다. 새로 확인해 주세요.')
        own, new = rnd['records'].get(actor), record(data.get('record'), league['kind'])
        # 출석만 → 실제 기록은 늦은 신규 등록이므로 불일치 예외에 넣지 않습니다.
        typed = own and own['division'] != 'attendance' and new['division'] != 'attendance'
        require(now < rnd['close'] or (typed and round_scores(league, rnd)['mismatch']), '마감 후에는 정정 또는 지연 제출 요청을 이용해 주세요.')
        put(league, rnd, actor, new, actor, now, '본인 기록 저장')
    elif action == 'guide':
        require(data.get('version') == rnd['guideVersion'], '안내가 변경되었습니다. 새로 확인해 주세요.')
        content = data.get('text', '')
        require(isinstance(content, str) and len(content) <= 4000, '안내는 4,000자 이내로 작성해 주세요.')
        photo = data.get('photo', '')
        require(isinstance(photo, str) and len(photo) <= 2800000, '사진은 2MB 이하로 선택해 주세요.')
        before = rnd['guide']
        # 지금 사진의 id를 그대로 보내면 유지, 빈 값이면 삭제, 그 밖에는 새로 올린 data URL입니다.
        if photo and photo != before['photo']:
            photo = stash(state, league, photo)
        rnd['guide'] = dict(text=content, photo=photo)
        rnd['guideVersion'] += 1
        # 이력에는 사진 유무만 남깁니다. 바뀌거나 지워진 사진은 app.change가 테이블에서 지웁니다.
        audit(league, actor, 'WOD 수정', now, round=rnd['id'], before=dict(before, photo=bool(before['photo'])), after=dict(rnd['guide'], photo=bool(photo)))
    elif action == 'request':
        require(now < league['rounds'][-1]['close'] + 72*3600, '신규 요청 접수가 마감되었습니다.')
        target = data.get('target')
        require(target in league['members'], '대상 참가자를 확인해 주세요.')
        old = rnd['records'].get(target)
        require(target == actor or old, '다른 회원의 기록은 이미 올라온 것에만 요청할 수 있어요.')
        require(target != actor or now >= rnd['close'], '마감 전에는 본인 기록을 직접 저장해 주세요.')
        require(not any(q['round'] == rnd['id'] and q['target'] == target and q['status'] != 'closed' for q in league['requests']), '이 기록에는 처리 중인 요청이 있습니다.')
        judge = data.get('judge') or None
        eligible = set(league['members']) - {actor, target}
        require(judge in eligible if eligible else judge is None, '당사자를 제외한 참가자 판정자를 지정해 주세요.')
        proposal = record(data.get('proposal'), league['kind']) if data.get('proposal') is not None else None
        require(proposal is not None or target != actor, '본인 정정·지연 제출에는 기록값을 입력해 주세요.')
        q = dict(id=uuid.uuid4().hex, round=rnd['id'], target=target, requester=actor, judge=judge, proposal=proposal, reason=text(data.get('reason')), baseVersion=rnd['versions'].get(target, 0), status='author' if target != actor else 'judge', created=now, reply='', decision='')
        require(q['status'] != 'judge' or judge is not None, '다른 참가자 판정자가 필요합니다.')
        league['requests'].append(q)
        audit(league, actor, '요청 접수', now, request=q)
    elif action in ('reply', 'decide'):
        q = next((q for q in league['requests'] if q['id'] == data.get('request')), None)
        require(q is not None and q['status'] != 'closed', '진행 중인 요청을 선택해 주세요.')
        rnd = next(r for r in league['rounds'] if r['id'] == q['round'])
        if action == 'reply':
            require(q['status'] == 'author' and actor == q['target'], '작성자만 답변하실 수 있습니다.')
            require(data.get('accept') in (True, False) and type(data.get('accept')) is bool, '답변을 선택해 주세요.')
            q['reply'] = text(data.get('reason')) if data.get('reason') or not data['accept'] else ''
            decision = 'modify' if data['accept'] else None
            if not data['accept']:
                q['status'] = 'judge' if q['judge'] else 'closed'
                if not q['judge']:
                    q['decision'] = '2명 리그: 거절로 기존 기록 유지'
        else:
            require(q['status'] == 'judge' and actor == q['judge'], '지정 판정자만 판정하실 수 있습니다.')
            decision = data.get('decision')
            require(decision in ('keep', 'modify', 'invalidate'), '판정 결과를 선택해 주세요.')
            q['decisionReason'] = text(data.get('reason'))
        if decision:
            if decision != 'keep' and rnd['versions'].get(q['target'], 0) != q['baseVersion']:
                q['decision'] = '원본 변경으로 적용하지 않고 종결'
            else:
                if decision != 'keep':
                    put(league, rnd, q['target'], q['proposal'] if decision == 'modify' else None, actor, now, '요청 반영')
                q['decision'] = {'keep': '기존 기록 유지', 'modify': '요청대로 반영', 'invalidate': '기록 삭제'}[decision]
            q['status'] = 'closed'
        audit(league, actor, '요청 답변' if action == 'reply' else '요청 판정', now, request=q)
    else:
        raise RuleError('지원하지 않는 작업입니다.')
    return league['id']


def initial(now):
    today = datetime.fromtimestamp(now, KST).date()
    days = [(today - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    league = dict(id='local-demo', name='우리끼리 3일 챌린지', kind='daily', start=stamp(days[0]), owner='ga', members=list(DEMO_NAMES), names=dict(DEMO_NAMES), invite=secrets.token_urlsafe(12), rounds=schedule(dict(kind='daily', start=days[0], end=days[-1], dates=days)), requests=[], history=[])
    league['rounds'][0]['records'] = {'ga': {'division': 'rx', 'type': 'reps', 'value': 100}, 'na': {'division': 'scaled', 'type': 'reps', 'value': 120}, 'da': {'division': 'attendance'}}
    league['rounds'][0]['versions'] = dict.fromkeys(('ga', 'na', 'da'), 1)
    league['rounds'][1]['records'] = {'ga': {'division': 'rx', 'type': 'amrap', 'rounds': 5, 'extra': 3}, 'na': {'division': 'rx', 'type': 'reps', 'value': 95}}
    league['rounds'][1]['versions'] = {'ga': 1, 'na': 1}
    return {'users': {}, 'sessions': {}, 'leagues': [league]}


def migrate(state):
    """이전 로컬 DB를 아이디·복구 키·초대 구조로 올리고, 상태 안의 사진(data URL)을 사진 테이블로 보냅니다. 여러 번 실행해도 같습니다."""
    state.setdefault('users', {})
    state.setdefault('sessions', {})
    for uid, user in state['users'].items():
        # 닉네임으로 로그인하던 계정입니다. uid는 리그 명단·이력으로 다른 회원에게 보이므로 아이디로 쓰지 않고,
        # 아무도 모르는 값을 넣어 로그인할 수 없게 둡니다. 복구 키도 없습니다(로컬 시험 데이터 한정).
        # login이 uid와 같고 복구 키가 없는 계정은 이전 중간 수정본이 uid를 아이디로 채운 것이라 함께 바꿉니다.
        # 가입 아이디도 10자리 16진수일 수 있어 login == uid만으로는 구분할 수 없으므로, 가입 계정에 항상 있는 복구 키로 가릅니다.
        login = user.get('login')
        if login is None or (login == uid and not user.get('recovery')):
            user['login'] = DEMO_LOGINS.get(uid) or secrets.token_hex(8)
        user.setdefault('recovery', None)
        user.pop('reset', None)
    for league in state['leagues']:
        league.setdefault('names', {u: DEMO_NAMES.get(u, u) for u in league['members']})
        league.setdefault('invite', secrets.token_urlsafe(12))
        # 이력 속 옛 사진 본문은 화면에 나간 적이 없어 유무 표시로 바꾸고, 지금 보이는 안내 사진만 옮깁니다.
        for h in league['history']:
            for k in ('before', 'after'):
                if isinstance(h.get(k), dict) and 'photo' in h[k]:
                    h[k]['photo'] = bool(h[k]['photo'])
        for rnd in league['rounds']:
            if rnd['guide']['photo'].startswith('data:'):
                rnd['guide']['photo'] = stash(state, league, rnd['guide']['photo'])
    return state
