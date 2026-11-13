import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('holidays', ROOT / 'scripts' / 'holidays.py')
holidays = importlib.util.module_from_spec(spec)
spec.loader.exec_module(holidays)

KEY, QUOTED = 'ab+/cd==', 'ab%2B%2Fcd%3D%3D'  # 포털이 주는 두 형태(디코딩 키, 인코딩 키) 모양
OLD = {'2027-12-25': '성탄절', '2029-01-01': '신정', '2026-12-25': '성탄절'}


def xml(*items, code='00', total=None):
    """공식 문서의 응답 항목(locdate, seq, dateKind, isHoliday, dateName, totalCount 등)으로 만든 가짜 응답입니다."""
    rows = ''.join(f'<item><dateKind>01</dateKind><dateName>{n}</dateName><isHoliday>{h}</isHoliday><locdate>{d}</locdate><seq>{i}</seq></item>' for i, (d, n, h) in enumerate(items, 1))
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response><header><resultCode>{code}</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>'
            f'<body><items>{rows}</items><numOfRows>100</numOfRows><pageNo>1</pageNo><totalCount>{len(items) if total is None else total}</totalCount></body></response>').encode()


def api(table):
    """{(해, 월): 응답}으로 답하는 가짜 호출입니다. 없는 달은 빈 응답입니다."""
    return lambda year, month: table.get((year, month), xml())


Y2028 = {(2028, 10): xml(('20281003', '개천절', 'Y'), ('20281005', '임시공휴일', 'Y'), ('20281003', '추석', 'Y'), ('20281009', '한글날', 'Y')),
         (2028, 3): xml(('20280301', '삼일절', 'Y')), (2028, 7): xml(('20280717', '제헌절', 'N'))}


class HolidayChecks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'holidays.json'
        self.path.write_text(json.dumps(OLD, ensure_ascii=False), encoding='utf-8')

    def run_main(self, args, get=None, key=KEY):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = holidays.main(args, {'KASI_SERVICE_KEY': key} if key else {}, get, self.path)
        return code, out.getvalue() + err.getvalue()

    def saved(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_parse_keeps_only_rest_days(self):
        self.assertEqual(holidays.parse(Y2028[(2028, 10)], 2028, 10), [('2028-10-03', '개천절'), ('2028-10-05', '임시공휴일'), ('2028-10-03', '추석'), ('2028-10-09', '한글날')])
        self.assertEqual(holidays.parse(Y2028[(2028, 7)], 2028, 7), [])
        self.assertEqual(holidays.parse(xml(), 2028, 4), [])

    def test_update_replaces_only_that_year_sorted_and_idempotent(self):
        self.path.write_text(json.dumps({**OLD, '2028-05-05': '지워질 날'}, ensure_ascii=False), encoding='utf-8')
        code, text = self.run_main(['2028'], api(Y2028))
        self.assertEqual(code, 0, text)
        # 같은 날의 여러 이름은 합치고, 쉬는 날이 아닌 항목(N)은 빼고, 다른 해는 그대로 둡니다.
        want = {'2026-12-25': '성탄절', '2027-12-25': '성탄절', '2028-03-01': '삼일절', '2028-10-03': '개천절, 추석', '2028-10-05': '임시공휴일', '2028-10-09': '한글날', '2029-01-01': '신정'}
        self.assertEqual(list(self.saved().items()), list(want.items()))
        self.assertIn('- 2028-05-05 지워질 날', text)
        self.assertIn('+ 2028-10-05 임시공휴일', text)
        first = self.path.read_bytes()
        self.assertEqual(self.run_main(['2028'], api(Y2028))[0], 0)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertTrue(first.decode().endswith('\n') and '개천절' in first.decode())

    def test_dry_run_and_default_years(self):
        before = self.path.read_bytes()
        code, text = self.run_main(['--dry-run', '2028'], api(Y2028))
        self.assertEqual(code, 0)
        self.assertIn('+ 2028-03-01 삼일절', text)
        self.assertEqual(self.path.read_bytes(), before)
        # 연도를 주지 않으면 한국 시간 기준 올해와 내년입니다.
        with mock.patch.object(holidays, 'this_year', return_value=2028):
            code, _ = self.run_main([], lambda y, m: xml((f'{y}0101', '쉬는 날', 'Y')) if m == 1 else xml())
        self.assertEqual(code, 0)
        self.assertEqual(self.saved(), {'2026-12-25': '성탄절', '2027-12-25': '성탄절', '2028-01-01': '쉬는 날', '2029-01-01': '쉬는 날'})

    def test_default_years_read_the_clock_once(self):
        # 두 번 읽으면 그 사이 한국 시간 새해가 될 때 2026·2028처럼 어긋납니다.
        asked = set()
        with mock.patch.object(holidays, 'this_year', side_effect=[2026, 2027]) as clock:
            code, text = self.run_main([], lambda y, m: asked.add(y) or xml((f'{y}0101', '쉬는 날', 'Y')) if m == 1 else xml())
        self.assertEqual((code, asked, clock.call_count), (0, {2026, 2027}, 1), text)
        self.assertEqual(self.saved(), {'2026-01-01': '쉬는 날', '2027-01-01': '쉬는 날', '2029-01-01': '신정'})

    def test_empty_next_year_is_skipped_only_in_default_mode(self):
        self.path.write_text(json.dumps({**OLD, '2028-05-05': '지워질 날'}, ensure_ascii=False), encoding='utf-8')
        before = self.path.read_bytes()
        with mock.patch.object(holidays, 'this_year', return_value=2028):
            # 내년(2029)이 비면 그 해만 건너뛰고 기존 항목(2029-01-01)을 둔 채 올해를 갱신합니다.
            code, text = self.run_main([], api(Y2028))
            self.assertEqual(code, 0, text)
            self.assertIn('2029년 공휴일은 아직 API에 없어 건너뛰었습니다', text)
            self.assertEqual(self.saved(), {'2026-12-25': '성탄절', '2027-12-25': '성탄절', '2028-03-01': '삼일절', '2028-10-03': '개천절, 추석', '2028-10-05': '임시공휴일', '2028-10-09': '한글날', '2029-01-01': '신정'})
            # 올해가 비면 내년 응답이 있어도 오류이고 파일은 그대로입니다.
            self.path.write_bytes(before)
            code, text = self.run_main([], api({(2029, 1): xml(('20290101', '1월1일', 'Y'))}))
            self.assertEqual((code, self.path.read_bytes()), (1, before))
            self.assertIn('2028년 공휴일이 하나도', text)
            # 연도를 직접 지정하면 내년이어도 빈 해는 오류입니다.
            code, text = self.run_main(['2028', '2029'], api(Y2028))
            self.assertEqual((code, self.path.read_bytes()), (1, before))
            self.assertIn('2029년 공휴일이 하나도', text)

    def test_bad_responses_leave_file_untouched(self):
        before = self.path.read_bytes()
        bad = {
            '오류 코드': xml(code='30'),
            '인증 오류 틀': b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>',
            '범위 밖 해': xml(('20290301', '삼일절', 'Y')),
            '범위 밖 달': xml(('20280401', '다른 달', 'Y')),
            '없는 날짜': xml(('20280332', '삼일절', 'Y')),
            '날짜 형식': xml(('2028-03-01', '삼일절', 'Y')),
            '이름 없음': xml(('20280301', '', 'Y')),
            '휴일 여부': xml(('20280301', '삼일절', '')),
            '항목 수': xml(('20280301', '삼일절', 'Y'), total=12),
            'XML 아님': b'{"response": {}}',
            'HTML': b'<html><body>Service Unavailable</body></html>',
            'DOCTYPE': b'<!DOCTYPE r [<!ENTITY a "aaaa">]><response><header><resultCode>00</resultCode></header><body><totalCount>0</totalCount></body></response>',
            'UTF-8 아님': xml().decode().encode('utf-16'),
        }
        for label, raw in bad.items():
            code, text = self.run_main(['2028'], api({**Y2028, (2028, 3): raw}))
            self.assertEqual((code, self.path.read_bytes()), (1, before), label)
            self.assertIn('오류: 2028-03', text, label)
        self.assertIn('30 SERVICE_KEY_IS_NOT_REGISTERED_ERROR', self.run_main(['2028'], api({(2028, 3): bad['인증 오류 틀']}))[1])
        # 한 해가 통째로 비면 오류이고, 함께 요청한 다른 해도 쓰지 않습니다.
        code, text = self.run_main(['2028', '2030'], api(Y2028))
        self.assertEqual((code, self.path.read_bytes()), (1, before))
        self.assertIn('2030년 공휴일이 하나도', text)
        for args in (['28'], ['2028', 'x'], ['--force']):
            self.assertEqual(self.run_main(args, api(Y2028))[0], 1, args)
        self.path.write_text('[]')
        self.assertEqual(self.run_main(['2028'], api(Y2028))[0], 1)
        self.assertEqual(self.path.read_text(), '[]')

    def test_key_is_required_encoded_once_and_never_printed(self):
        code, text = self.run_main(['--dry-run', '2028'], key=None)
        self.assertEqual(code, 1)
        self.assertIn('KASI_SERVICE_KEY', text)
        urls = []

        def ok(url, timeout):
            urls.append(url)
            return io.BytesIO(xml(('20280301', '삼일절', 'Y')) if 'solMonth=03' in url else xml())
        for key in (KEY, QUOTED):
            with mock.patch.object(holidays.urllib.request, 'urlopen', ok):
                code, text = self.run_main(['2028'], key=key)
            self.assertEqual(code, 0, text)
            self.assertEqual(len(urls), 12)
            self.assertEqual(urls[2], f'{holidays.URL}?ServiceKey={QUOTED}&solYear=2028&solMonth=03&numOfRows=100&pageNo=1')
            urls.clear()
            self.assertEqual(self.saved()['2028-03-01'], '삼일절')
            # 요청 실패 문구에 URL이 섞여 와도 키는 어떤 형태로도 나오지 않습니다.
            for boom in (urllib.error.URLError(f'cannot open {holidays.URL}?ServiceKey={QUOTED} or {KEY}'), urllib.error.HTTPError('u', 500, 'Internal Server Error', {}, None), TimeoutError('timed out')):
                with mock.patch.object(holidays.urllib.request, 'urlopen', side_effect=boom):
                    code, text = self.run_main(['2029'], key=key)
                self.assertEqual(code, 1)
                self.assertIn('2029-01 요청 실패', text)
                self.assertFalse(KEY in text or QUOTED in text, text)
            saved = self.path.read_text(encoding='utf-8')
            self.assertFalse(KEY in saved or QUOTED in saved)
        # 응답 문구에 키가 되돌아와도 가립니다.
        echo = xml(code='30').replace(b'NORMAL SERVICE.', KEY.encode())
        code, text = self.run_main(['2028'], api({(2028, 1): echo}))
        self.assertEqual(code, 1)
        self.assertNotIn(KEY, text)

    def test_shipped_table(self):
        table = holidays.read(ROOT / 'web' / 'holidays.json')
        self.assertEqual(list(table), sorted(table))
        for day, name in {'2026-12-25': '성탄절', '2027-02-09': '대체공휴일', '2027-09-15': '추석', '2027-12-27': '대체공휴일'}.items():
            self.assertEqual(table.get(day), name)


if __name__ == '__main__':
    unittest.main()
