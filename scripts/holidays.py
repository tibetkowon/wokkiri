#!/usr/bin/env python3
"""공휴일 표(web/holidays.json) 갱신: KASI_SERVICE_KEY=... python3 scripts/holidays.py [--dry-run] [연도 ...]

연도를 주지 않으면 한국 시간 기준 올해와 내년을 갱신합니다(내년이 아직 API에 없으면 올해만). 공공데이터포털 "한국천문연구원_특일 정보"의
공휴일 정보 조회(getRestDeInfo)를 월별로 불러 그 해의 항목만 바꾸고 다른 해는 그대로 둡니다.
하나라도 이상하면 파일을 건드리지 않고 0이 아닌 코드로 끝납니다.
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote

URL = 'https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo'
PATH = Path(__file__).resolve().parent.parent / 'web' / 'holidays.json'
# API가 주는 이름을 달력에 보일 익숙한 이름으로 바꿉니다. 여기에도 KNOWN에도 없는 이름은 그대로 넣고 한 줄로 알립니다.
RENAME = {'1월1일': '신정', '기독탄신일': '성탄절'}
EVE = {'설날': '설 연휴', '추석': '추석 연휴'}  # API는 연휴 사흘에 모두 같은 이름을 줍니다. 가운데 날만 그 이름으로 둡니다.
KNOWN = {'설날', '삼일절', '노동절', '어린이날', '부처님오신날', '현충일', '제헌절', '광복절', '추석', '개천절', '한글날', '대체공휴일', '임시공휴일',
         '국회의원선거일', *RENAME.values(), *EVE.values()}
USAGE = '사용법: KASI_SERVICE_KEY=... python3 scripts/holidays.py [--dry-run] [연도 ...] (연도는 2000~2099)'


class Fail(Exception):
    pass


class Empty(Fail):
    """한 해의 공휴일이 하나도 오지 않았습니다(아직 발표 전일 수 있음)."""


def encoded(key):
    # 포털은 인코딩된 키와 아닌 키를 둘 다 줍니다. 먼저 풀고 한 번만 인코딩합니다.
    return quote(unquote(key), safe='')


def hide(text, key):
    """오류 문구에 인증키가 어떤 형태로든 섞여 나가지 않게 가립니다."""
    for form in {key, unquote(key), encoded(key)} - {''}:
        text = text.replace(form, '***')
    return text


def this_year():
    return datetime.now(timezone(timedelta(hours=9))).year


def fetch(key, year, month):
    url = f'{URL}?ServiceKey={encoded(key)}&solYear={year}&solMonth={month:02d}&numOfRows=100&pageNo=1'
    try:
        with urllib.request.urlopen(url, timeout=20) as res:
            return res.read(1000001)
    except OSError as err:  # HTTP 오류, 연결 실패, 시간 초과
        raise Fail(f'{year}-{month:02d} 요청 실패: {hide(str(err), key)}')


def parse(raw, year, month):
    """한 달치 응답에서 쉬는 날(isHoliday=Y)만 [(날짜, 이름)]으로 돌려줍니다."""
    where = f'{year}-{month:02d}'
    try:
        # 표준 XML 파서는 엔티티 폭탄을 막지 못하므로 DOCTYPE이 있으면 파싱하지 않습니다. UTF-8 글자로 풀어서 봐야 다른 인코딩으로 숨기지 못합니다.
        text = raw.decode('utf-8')
        if len(raw) > 1000000 or '<!DOCTYPE' in text or '<!ENTITY' in text:
            raise ValueError
        root = ET.fromstring(text)
    except (ValueError, ET.ParseError):
        raise Fail(f'{where} 응답이 XML이 아닙니다.')
    code = root.findtext('.//resultCode')
    if code != '00':
        # 인증 오류는 다른 틀(returnReasonCode·returnAuthMsg)로 온다고 알려져 있어 함께 찾습니다.
        code = code or root.findtext('.//returnReasonCode') or '없음'
        msg = root.findtext('.//resultMsg') or root.findtext('.//returnAuthMsg') or ''
        raise Fail(f'{where} 응답 오류 코드 {code} {msg}'.strip())
    items, total = root.findall('.//item'), root.findtext('.//totalCount', '')
    if not total.isdigit() or int(total) != len(items):
        raise Fail(f'{where} 응답의 항목 수(totalCount)가 맞지 않습니다.')
    days = []
    for item in items:
        day, name, rest = (item.findtext(k, '').strip() for k in ('locdate', 'dateName', 'isHoliday'))
        if not re.fullmatch(r'\d{8}', day) or not name or len(name) > 50 or rest not in ('Y', 'N'):
            raise Fail(f'{where} 응답 항목 형식이 이상합니다.')
        try:
            iso = date(int(day[:4]), int(day[4:6]), int(day[6:])).isoformat()
        except ValueError:
            raise Fail(f'{where} 응답에 없는 날짜 {day}가 있습니다.')
        if not iso.startswith(where):
            raise Fail(f'{where} 요청에 범위 밖 날짜 {iso}가 왔습니다.')
        if rest == 'Y':
            days.append((iso, name))
    return days


def tidy(days):
    """{날짜: [API 이름]}의 이름을 달력에 보일 이름으로 바꿉니다. 이미 바꾼 이름을 다시 넣어도 그대로입니다."""
    out = {}
    for iso, names in days.items():
        d = date.fromisoformat(iso)
        near = [days.get((d + timedelta(n)).isoformat(), []) for n in (-1, 1)]
        for name in names:
            if name in EVE and sum(name in n for n in near) == 1:  # 같은 이름이 한쪽에만 이어지면 연휴의 첫날이나 끝날
                name = EVE[name]
            elif name.startswith('대체공휴일('):
                name = '대체공휴일'
            name = RENAME.get(name, name)
            if name not in out.setdefault(iso, []):
                out[iso].append(name)
    return out


def year_holidays(year, get):
    days = {}
    for month in range(1, 13):
        for iso, name in parse(get(year, month), year, month):
            if name not in days.setdefault(iso, []):
                days[iso].append(name)
    if not days:
        raise Empty(f'{year}년 공휴일이 하나도 오지 않았습니다. 아직 발표 전이면 그 해를 빼고 실행해 주세요.')
    return {iso: ', '.join(names) for iso, names in tidy(days).items()}


def read(path):
    try:
        old = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise Fail(f'{path.name}을 읽지 못했습니다.')
    if not isinstance(old, dict) or not all(re.fullmatch(r'\d{4}-\d\d-\d\d', d) and isinstance(n, str) for d, n in old.items()):
        raise Fail(f'{path.name}의 형식이 이상합니다.')
    return old


def main(argv=None, env=os.environ, get=None, path=PATH):
    args = sys.argv[1:] if argv is None else argv
    dry, years = '--dry-run' in args, [a for a in args if a != '--dry-run']
    key = env.get('KASI_SERVICE_KEY', '').strip()
    try:
        if not all(re.fullmatch(r'20\d\d', y) for y in years):
            raise Fail(USAGE)
        if not key:
            raise Fail('환경변수 KASI_SERVICE_KEY가 없습니다. 공공데이터포털에서 받은 인증키를 넣어 주세요.\n' + USAGE)
        given = bool(years)
        years = sorted({int(y) for y in years})
        if not years:
            # 현재 연도는 한 번만 읽습니다. 두 번 읽으면 그 사이에 새해가 될 때 올해와 내년이 어긋납니다.
            first = this_year()
            years = [first, first + 1]
        old = read(path)
        get = get or (lambda year, month: fetch(key, year, month))
        fresh = {}
        for year in list(years):
            try:
                fresh.update(year_holidays(year, get))
            except Empty:
                # 연도 없이 돌린 실행에서 내년만 비면(보통 월력요항이 나오는 6월 전) 그 해만 건너뛰고 기존 항목을 둡니다.
                if given or year == years[0]:
                    raise
                years.remove(year)
                print(f'{year}년 공휴일은 아직 API에 없어 건너뛰었습니다. 그 해의 기존 항목은 그대로 둡니다.')
    except Fail as err:
        print(hide(f'오류: {err}', key), file=sys.stderr)
        return 1
    new = dict(sorted({**{d: n for d, n in old.items() if int(d[:4]) not in years}, **fresh}.items()))
    changed = [d for d in sorted({*old, *new}) if old.get(d) != new.get(d)]
    for d in changed:
        if d in old:
            print(f'- {d} {old[d]}')
        if d in new:
            print(f'+ {d} {new[d]}')
    unknown = sorted({n for names in fresh.values() for n in names.split(', ')} - KNOWN)
    if unknown:
        print(f'매핑 없는 이름: {", ".join(unknown)} (그대로 넣었습니다. 어색하면 scripts/holidays.py의 RENAME에 추가해 주세요)')
    label = '·'.join(map(str, years))
    print(f'{label}년: 바뀐 날짜 {len(changed)}개' + (' (--dry-run: 파일은 쓰지 않았습니다)' if dry else ''))
    if not dry:
        # 쓰다가 끊겨도 반쪽 파일이 남지 않게 임시 파일로 쓴 뒤 바꿉니다.
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
