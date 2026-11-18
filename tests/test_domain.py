import copy
import os
import time
import unittest
from domain import DEMO_NAMES, apply, initial, round_scores, view, record, schedule, stamp

NOW = stamp('2026-11-12') + 3600*12

def reps(n, division='rx'):
    return dict(division=division, type='reps', value=n)

class Rules(unittest.TestCase):
    def setUp(self):
        self.state = initial(NOW)
        self.state['users'] = {u: dict(name=n) for u, n in DEMO_NAMES.items()}
        self.l = self.state['leagues'][0]
        self.r = self.l['rounds'][0]

    def run_action(self, actor='ga', now=NOW, **data):
        return apply(self.state, actor, dict(league=self.l['id'], round=self.r['id'], **data), now)

    def test_rank_ties_attendance_n(self):
        self.r['records'] = {'ga': reps(10), 'na': reps(10), 'da': {'division':'attendance'}}
        self.assertEqual(round_scores(self.l,self.r)['points'],dict(ga=1,na=1,da=3,ra=4))
        self.r['records']['na'] = reps(1000,'scaled')
        self.assertEqual(round_scores(self.l,self.r)['order'],['ga','na','da','ra'])

    def test_time_amrap_weight_done(self):
        for records, order in [
            ([dict(type='time',finished=False,value=100),dict(type='time',finished=True,value=800)],['na','ga']),
            ([dict(type='time',finished=True,value=50),dict(type='time',finished=True,value=60)],['ga','na']),
            ([dict(type='time',finished=False,value=10),dict(type='time',finished=False,value=9)],['ga','na']),
            ([dict(type='amrap',rounds=3,extra=0),dict(type='amrap',rounds=2,extra=999)],['ga','na']),
            ([dict(type='weight',value=90),dict(type='weight',value=100)],['na','ga']),
            ([dict(type='done',finished=True),dict(type='done',finished=True)],['ga','na'])]:
            self.r['records'] = {u:dict(division='rx',**r) for u,r in zip(['ga','na'],records)}
            self.assertEqual(round_scores(self.l,self.r)['order'][:2],order)
        self.assertEqual(round_scores(self.l,self.r)['points']['na'],1)
        self.r['records']={'ga':dict(division='rx',type='done',finished=False),'na':dict(division='rx',type='done',finished=True),'da':dict(division='scaled',type='done',finished=True),'ra':{'division':'attendance'}}
        self.assertEqual(round_scores(self.l,self.r)['points'],dict(na=1,ga=2,da=3,ra=4))
        self.r['records']={'ga':dict(division='rx',type='time',finished=False,value=0),'na':dict(division='scaled',type='time',finished=True,value=1)}
        self.assertEqual(round_scores(self.l,self.r)['order'][0],'ga')

    def test_zero_is_a_valid_record(self):
        only={'division':'attendance'}
        zeros=[reps(0),dict(division='rx',type='amrap',rounds=0,extra=0),dict(division='rx',type='time',finished=False,value=0)]
        some=[reps(1),dict(division='rx',type='amrap',rounds=0,extra=1),dict(division='rx',type='time',finished=False,value=1)]
        for zero,one in zip(zeros,some):
            self.assertEqual(record(zero,'daily'),zero)
            # 0회는 같은 유형 기록 다음이고 출석만·미출석보다 앞이며, 0회끼리는 공동순위입니다. 유형이 같으면 불일치가 아닙니다.
            self.r['records']={'ga':one,'na':zero,'da':copy.deepcopy(zero),'ra':only}
            s=round_scores(self.l,self.r)
            self.assertEqual((s['mismatch'],s['points']),(False,dict(ga=1,na=2,da=2,ra=4)))
            self.r['records']={'ga':zero,'na':only}
            self.assertEqual(round_scores(self.l,self.r)['points'],dict(ga=1,na=2,da=4,ra=4))
        # 유형이 다른 0회는 다른 기록과 마찬가지로 불일치입니다.
        self.r['records']={'ga':zeros[0],'na':zeros[1]}
        self.assertTrue(round_scores(self.l,self.r)['mismatch'])
        # 0회 결정은 중량형을 바꾸지 않습니다. 성공한 중량이 없으면 기록이 아니라 출석만입니다.
        for v in (0,-1):
            with self.assertRaises(ValueError):
                record(dict(division='rx',type='weight',value=v),'daily')
        # 지정 WOD에서도 0회는 기록이라 참여로 인정합니다.
        self.assertEqual(record(reps(0),'designated'),reps(0))

    def test_pending_until_close(self):
        close=self.r['close']
        self.r['records']={'ga':reps(10),'na':{'division':'attendance'}}
        self.l['rounds']=[self.r]
        # 마감 직전: 기록·출석 회원은 잠정 등수, 미출석은 None("집계 전")이고 누적에는 넣지 않습니다.
        v=view(self.l,close-1)
        self.assertEqual(v['rounds'][0]['score']['points'],dict(ga=1,na=2,da=None,ra=None))
        self.assertEqual((v['rounds'][0]['counted'],v['provisional']),(False,True))
        self.assertTrue(all(x['points']==0 and x['rank']==1 for x in v['standings']))
        # 마감 시각부터 미출석 N점을 매기고 누적에 반영합니다.
        v=view(self.l,close)
        self.assertEqual(v['rounds'][0]['score']['points'],dict(ga=1,na=2,da=4,ra=4))
        self.assertTrue(v['rounds'][0]['counted'])
        self.assertEqual({x['user']:(x['points'],x['rank']) for x in v['standings']},dict(ga=(1,1),na=(2,2),da=(4,3),ra=(4,3)))
        # 불일치가 우선합니다. 마감 전에도 회차 전체가 보류(None)이고 마감 뒤에도 누적에 넣지 않습니다.
        self.r['records']['da']=dict(division='rx',type='amrap',rounds=1,extra=0)
        for now in (close-1,close):
            v=view(self.l,now)
            self.assertEqual((v['rounds'][0]['score']['mismatch'],v['rounds'][0]['counted'],set(v['rounds'][0]['score']['points'].values())),(True,False,{None}))
        # view는 저장된 리그를 바꾸지 않습니다.
        self.assertNotIn('score',self.r)

    def test_pending_until_designated_round_ends(self):
        lid=apply(self.state,'ga',dict(action='create',name='지정 리그',kind='designated',start='2026-12-01',count=2,duration=7),NOW)
        l=self.state['leagues'][-1]
        apply(self.state,'na',dict(action='join',invite=l['invite']),NOW)
        apply(self.state,'ga',dict(action='record',league=lid,round='0',version=0,record=reps(0)),stamp('2026-12-01'))
        # 1라운드는 종료일(12/7) 다음 날 00:00에 마감합니다. 그 전에는 미참여가 집계 전입니다.
        end=stamp('2026-12-08')
        v=view(l,end-1)
        self.assertEqual((v['rounds'][0]['score']['points'],v['rounds'][0]['counted']),(dict(ga=1,na=None),False))
        self.assertEqual([x['points'] for x in v['standings']],[0,0])
        v=view(l,end)
        self.assertEqual((v['rounds'][0]['score']['points'],v['rounds'][0]['counted']),(dict(ga=1,na=2),True))
        # 2라운드는 이제 막 시작해 아직 집계 전이라 누적은 1라운드뿐입니다.
        self.assertEqual((v['rounds'][1]['score']['points'],v['rounds'][1]['counted']),(dict(ga=None,na=None),False))
        self.assertEqual({x['user']:x['points'] for x in v['standings']},dict(ga=1,na=2))

    def test_mismatch_removes_every_point_and_restores(self):
        self.l['rounds']=[self.r]
        before=view(self.l,NOW)['standings']
        self.r['records']['na']=dict(division='rx',type='amrap',rounds=1,extra=0)
        v=view(self.l,NOW)
        self.assertTrue(v['rounds'][0]['score']['mismatch'])
        self.assertTrue(all(x['points']==0 for x in v['standings']))
        self.run_action('na',action='record',version=1,record=reps(120,'scaled'))
        self.assertEqual(view(self.l,NOW)['standings'],before)
        with self.assertRaises(ValueError):
            self.run_action('ga',action='record',version=1,record=reps(200))

    def test_deadline_and_no_late_new_record(self):
        close=self.r['close']
        self.run_action('ra',now=close-1,action='record',version=0,record=reps(1))
        with self.assertRaises(ValueError):
            self.run_action('ra',now=close,action='record',version=1,record=reps(2))
        self.r['records']['na']=dict(division='rx',type='amrap',rounds=1,extra=0)
        self.r['records'].pop('ra')
        with self.assertRaises(ValueError):
            self.run_action('ra',action='record',version=1,record=reps(2))
        with self.assertRaises(ValueError):
            self.run_action('da',now=close,action='record',version=1,record=reps(2))
        with self.assertRaises(ValueError):
            self.run_action('ga',now=close,action='record',version=1,record={'division':'attendance'})
        self.run_action('ga',now=close+1000000,action='record',version=1,record=reps(2))

    def test_attendance_only_switch(self):
        close=self.r['close']
        only={'division':'attendance'}
        # 마감 전에는 Rx 기록을 출석만으로 바꾸고 다시 기록으로 바꿀 수 있습니다.
        self.run_action('ga',now=close-1,action='record',version=1,record=only)
        self.assertEqual(self.r['records']['ga'],only)
        self.run_action('ga',now=close-1,action='record',version=2,record=reps(100))
        # 마감 뒤에는 불일치가 없을 때도, 불일치 예외 중에도 출석만으로 바꿀 수 없습니다.
        with self.assertRaises(ValueError):
            self.run_action('ga',now=close,action='record',version=3,record=only)
        self.r['records']['na']=dict(division='rx',type='amrap',rounds=1,extra=0)
        with self.assertRaises(ValueError):
            self.run_action('ga',now=close,action='record',version=3,record=only)
        self.assertEqual(self.r['records']['ga'],reps(100))
        # 지정 WOD에는 마감 전에도 출석만이 없습니다.
        lid=apply(self.state,'ga',dict(action='create',name='지정 리그',kind='designated',start='2026-12-01',count=1,duration=7),NOW)
        with self.assertRaises(ValueError):
            apply(self.state,'ga',dict(action='record',league=lid,round='0',version=0,record=only),stamp('2026-12-01'))
        apply(self.state,'ga',dict(action='record',league=lid,round='0',version=0,record=reps(1)),stamp('2026-12-01'))
        with self.assertRaises(ValueError):
            apply(self.state,'ga',dict(action='record',league=lid,round='0',version=1,record=only),stamp('2026-12-01'))

    def request(self, actor='na', target='ga', judge='da', now=NOW):
        self.run_action(actor,now=now,action='request',target=target,judge=judge,proposal=reps(99),reason='기록을 확인해 주세요.')
        return self.l['requests'][-1]

    def test_author_accepts_immediately(self):
        q=self.request()
        with self.assertRaises(ValueError):
            self.run_action('da',action='decide',request=q['id'],decision='modify',reason='확인했습니다.')
        with self.assertRaises(ValueError):
            self.run_action('ga',action='reply',request=q['id'],accept=False,reason='')
        self.run_action('ga',action='reply',request=q['id'],accept=True,reason='')
        self.assertEqual(q['status'],'closed')
        self.assertEqual(self.r['records']['ga'],reps(99))
        with self.assertRaises(ValueError):
            self.run_action('da',action='decide',request=q['id'],decision='invalidate',reason='확인했습니다.')

    def test_rejection_judge_and_permissions(self):
        q=self.request()
        self.run_action('ga',action='reply',request=q['id'],accept=False,reason='다시 확인해 주세요.')
        with self.assertRaises(ValueError):
            self.run_action('ra',action='decide',request=q['id'],decision='invalidate',reason='확인했습니다.')
        # 판정 값이 없거나 비었거나 목록 밖이면 거부하고 요청은 그대로 둡니다.
        before=copy.deepcopy(self.r['records']['ga'])
        for bad in ({}, {'decision':''}, {'decision':None}, {'decision':'bogus'}):
            with self.assertRaises(ValueError):
                self.run_action('da',action='decide',request=q['id'],reason='확인했습니다.',**bad)
        self.assertEqual(q['status'],'judge')
        self.assertEqual(q['decision'],'')
        self.assertEqual(self.r['records']['ga'],before)
        self.run_action('da',now=NOW+999999,action='decide',request=q['id'],decision='invalidate',reason='확인했습니다.')
        self.assertIsNone(self.r['records']['ga'])
        self.assertEqual(q['decision'],'기록 삭제')
        self.assertEqual(q['status'],'closed')

    def test_two_members_rejection_and_self_request(self):
        self.l['members']=['ga','na']
        q=self.request(judge=None)
        before=copy.deepcopy(self.r['records']['ga'])
        self.run_action('ga',action='reply',request=q['id'],accept=False,reason='원래 기록이 맞습니다.')
        self.assertEqual(q['status'],'closed')
        self.assertEqual(self.r['records']['ga'],before)
        q=self.request(actor='ga',target='ga',judge='na')
        self.assertEqual(q['status'],'judge')
        self.run_action('na',action='decide',request=q['id'],decision='modify',reason='확인했습니다.')
        self.assertEqual(self.r['records']['ga'],reps(99))

    def test_72_hours_finalization(self):
        end=self.l['rounds'][-1]['close']+72*3600
        self.request(now=end-1)
        with self.assertRaises(ValueError):
            self.run_action('ra',now=end,action='request',target='na',judge='da',proposal=reps(1),reason='확인해 주세요.')
        self.assertFalse(view(self.l,end)['final'])
        self.l['requests'][0]['status']='closed'
        self.assertFalse(view(self.l,end)['final'])
        self.l['rounds'][1]['records']['na']=dict(division='rx',type='amrap',rounds=4,extra=0)
        self.assertTrue(view(self.l,end)['final'])

    def test_stale_request_never_overwrites(self):
        q=self.request()
        self.r['records']['na']=dict(division='rx',type='amrap',rounds=1,extra=0)
        self.run_action('ga',action='record',version=1,record=reps(222))
        self.run_action('ga',action='reply',request=q['id'],accept=True,reason='동의합니다.')
        self.assertEqual(self.r['records']['ga'],reps(222))
        self.assertIn('원본 변경',q['decision'])

    def test_security_and_validation(self):
        with self.assertRaises(ValueError):
            self.run_action('ga',now=self.r['start'],action='record',target='na',version=1,record=reps(1))
        with self.assertRaises(ValueError):
            self.request(judge='ga')
        for invalid in [reps(-1),reps(1.5),dict(division='rx',type='weight',value=float('nan')),dict(division='rx',type='done')]:
            with self.assertRaises(ValueError):
                record(invalid,'daily')
        with self.assertRaises(ValueError):
            record(dict(division='attendance'),'designated')
        with self.assertRaises(ValueError):
            self.run_action('ga',action='guide',version=0,text='안내',photo='data:image/svg+xml;base64,AAAA')

    def test_schedule_and_members_freeze(self):
        rounds=schedule(dict(kind='designated',start='2026-12-01',count=3,duration=7))
        self.assertEqual(rounds[0]['close'],rounds[1]['start'])
        self.assertEqual(rounds[-1]['close'],stamp('2026-12-22'))
        lid=apply(self.state,'ga',dict(action='create',name='새 리그',kind='designated',start='2026-12-01',count=3,duration=7),NOW)
        apply(self.state,'na',dict(action='join',invite=self.state['leagues'][-1]['invite']),NOW)
        with self.assertRaises(ValueError):
            apply(self.state,'da',dict(action='join',invite=self.state['leagues'][-1]['invite']),stamp('2026-12-01'))
        l=self.state['leagues'][-1]
        self.assertTrue(all(s['points']==0 for s in view(l,NOW)['standings']))

    def test_daily_freezes_on_start_even_without_counted_day(self):
        lid=apply(self.state,'ga',dict(action='create',name='주말 제외 리그',kind='daily',start='2026-12-01',end='2026-12-03',dates=['2026-12-03']),NOW)
        with self.assertRaises(ValueError):
            apply(self.state,'na',dict(action='join',invite=self.state['leagues'][-1]['invite']),stamp('2026-12-01'))

    def test_join_needs_the_invite_and_keeps_the_nickname(self):
        apply(self.state,'ga',dict(action='create',name='새 리그',kind='daily',start='2026-12-01',end='2026-12-03',dates=['2026-12-01']),NOW)
        l=self.state['leagues'][-1]
        for bad in [None,'',123,'틀린코드']:
            with self.assertRaises(ValueError):
                apply(self.state,'na',dict(action='join',league=l['id'],invite=bad),NOW)
        apply(self.state,'na',dict(action='join',invite=l['invite']),NOW)
        self.assertEqual(l['names'],{'ga':'가람','na':'나래'})
        # 계정 닉네임은 겹칠 수 있지만 한 리그 안에서는 겹치지 않습니다. name을 주면 이 리그에서만 그 닉네임을 씁니다.
        self.state['users']['da']['name']='가람'
        for name in [None,'가람',' 가람 ','가\u200b람','나래','','가'*13,123]:
            with self.assertRaises(ValueError,msg=name):
                apply(self.state,'da',dict(action='join',invite=l['invite'],**({} if name is None else {'name':name})),NOW)
        self.assertNotIn('da',l['members'])
        apply(self.state,'da',dict(action='join',invite=l['invite'],name=' 가람2 '),NOW)
        self.state['users']['ra']['name']='tom'
        apply(self.state,'ra',dict(action='join',invite=l['invite']),NOW)
        self.assertEqual(l['names'],{'ga':'가람','na':'나래','da':'가람2','ra':'tom'})
        self.assertEqual(self.state['users']['da']['name'],'가람')
        # \ub300\uc18c\ubb38\uc790\u00b7\uc804\uac01 \ucc28\uc774\ub294 \uac19\uc740 \ub2c9\ub124\uc784\uc73c\ub85c \ubd05\ub2c8\ub2e4.
        l['members'].remove('ra'); del l['names']['ra']
        l['names']['na']='TOM'
        for extra in [{},{'name':'\uff34om'}]:
            with self.assertRaises(ValueError):
                apply(self.state,'ra',dict(action='join',invite=l['invite'],**extra),NOW)
        for actor in ['na',None]:
            with self.assertRaises(ValueError):
                apply(self.state,actor,dict(action='join',invite=l['invite']),NOW)

    def test_guide_photo_history_and_version(self):
        photo='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
        self.run_action(action='guide',version=0,text='오늘의 와드입니다.',photo=photo)
        # 상태에는 id만 남고, 본문은 app.change가 가져갈 state['photos']에 실립니다.
        pid=self.r['guide']['photo']
        self.assertRegex(pid,'^[0-9a-f]{32}$')
        stored=self.state.pop('photos')
        self.assertEqual((list(stored),stored[pid]['league'],stored[pid]['mime'],stored[pid]['body'][:4]),([pid],self.l['id'],'image/png',b'\x89PNG'))
        self.assertNotIn('base64',repr(self.state))
        # id를 그대로 보내면 사진을 유지하고 새로 싣지 않습니다. 다른 문자열은 사진으로 받지 않습니다.
        self.run_action('na',action='guide',version=1,text='글만 고쳤습니다.',photo=pid)
        self.assertEqual((self.r['guide']['photo'],'photos' in self.state),(pid,False))
        with self.assertRaises(ValueError):
            self.run_action(action='guide',version=2,text='남의 사진',photo='0'*32)
        self.run_action('na',action='guide',version=2,text='안내를 수정했습니다.',photo='')
        self.assertEqual(self.r['guide']['photo'],'')
        # 이력에는 사진 본문 없이 유무만 남습니다.
        self.assertEqual([(h['before']['photo'],h['after']['photo']) for h in self.l['history']],[(False,True),(True,True),(True,False)])
        self.assertIs(view(self.l,NOW)['history'][-1]['before']['photo'],True)
        with self.assertRaises(ValueError):
            self.run_action(action='guide',version=1,text='오래된 화면입니다.',photo='')

    def test_view_hides_removed_pin_reset_history(self):
        kept=dict(actor='na',action='리그 참가',at=NOW)
        old=[dict(actor='ga',action='PIN 초기화 코드 발급',at=NOW,member='na'),dict(actor='ga',action='PIN 초기화 코드 발급',at=NOW),dict(actor='ga',action='다른 구형 표시',at=NOW,member='na')]
        self.l['history']=[old[0],kept,old[1],old[2]]
        self.assertEqual(view(self.l,NOW)['history'],[kept])
        self.assertEqual(len(self.l['history']),4)

    def test_duplicate_request_and_version(self):
        self.request()
        with self.assertRaises(ValueError):
            self.request(actor='ra',judge='da')
        with self.assertRaises(ValueError):
            self.run_action('ga',now=self.r['start'],action='record',version=999,record=reps(1))

class KoreanClock(unittest.TestCase):
    def test_stamp_is_korean_midnight(self):
        # 2026-12-21 15:00:00 UTC. 기대값은 stamp로 다시 계산하지 않고 고정 숫자로 둡니다.
        self.assertEqual(stamp('2026-12-22'), 1797865200.0)

    def test_deadline_is_korean_midnight_not_utc_midnight(self):
        # 12/01 라운드는 한국 12/02 00:00(UTC 12/01 15:00)에 마감합니다. UTC 자정(한국 12/02 09:00)에는 아무 일도 없습니다.
        cases = [(1796137199, True), (1796137200, False), (1796137200 + 1800, False),
                 (1796137200 + 9*3600 - 1, False), (1796137200 + 9*3600, False)]
        old_tz = os.environ.get('TZ')
        # tzset이 없는 플랫폼은 시간대를 바꾸지 않고 지금 시간대로 한 번만 확인합니다.
        zones = ['Asia/Seoul', 'UTC', 'America/Chicago'] if hasattr(time, 'tzset') else [None]
        try:
            for zone in zones:
                if zone:
                    os.environ['TZ'] = zone
                if hasattr(time, 'tzset'):
                    time.tzset()
                for now, open_ in cases:
                    with self.subTest(zone=zone, now=now):
                        state = initial(NOW)
                        state['users'] = {u: dict(name=n) for u, n in DEMO_NAMES.items()}
                        lid = apply(state, 'ga', dict(action='create', name='경계', kind='designated', start='2026-12-01', count=1, duration=1), NOW)
                        self.assertEqual(state['leagues'][-1]['rounds'][0]['close'], 1796137200.0)
                        apply(state, 'na', dict(action='join', invite=state['leagues'][-1]['invite']), NOW)
                        record_it = lambda: apply(state, 'na', dict(action='record', league=lid, round='0', version=0, record=reps(1)), now)
                        if open_:
                            record_it()
                        else:
                            with self.assertRaises(ValueError):
                                record_it()
        finally:
            if old_tz is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old_tz
            if hasattr(time, 'tzset'):
                time.tzset()

if __name__=='__main__':
    unittest.main()
