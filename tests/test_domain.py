import copy
import unittest
from domain import apply, initial, round_scores, view, record, schedule, stamp

NOW = stamp('2026-11-12') + 3600*12

def reps(n, division='rx'):
    return dict(division=division, type='reps', value=n)

class Rules(unittest.TestCase):
    def setUp(self):
        self.state = initial(NOW)
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
        self.run_action('da',now=NOW+999999,action='decide',request=q['id'],decision='invalidate',reason='확인했습니다.')
        self.assertIsNone(self.r['records']['ga'])
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
        apply(self.state,'na',dict(action='join',league=lid),NOW)
        with self.assertRaises(ValueError):
            apply(self.state,'da',dict(action='join',league=lid),stamp('2026-12-01'))
        l=self.state['leagues'][-1]
        self.assertTrue(all(s['points']==0 for s in view(l,NOW)['standings']))

    def test_daily_freezes_on_start_even_without_counted_day(self):
        lid=apply(self.state,'ga',dict(action='create',name='주말 제외 리그',kind='daily',start='2026-12-01',end='2026-12-03',dates=['2026-12-03']),NOW)
        with self.assertRaises(ValueError):
            apply(self.state,'na',dict(action='join',league=lid),stamp('2026-12-01'))

    def test_guide_photo_history_and_version(self):
        photo='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
        self.run_action(action='guide',version=0,text='오늘의 와드입니다.',photo=photo)
        self.run_action('na',action='guide',version=1,text='안내를 수정했습니다.',photo='')
        self.assertEqual(self.r['guide']['photo'],'')
        self.assertEqual(self.l['history'][-1]['before']['photo'],photo)
        self.assertIs(view(self.l,NOW)['history'][-1]['before']['photo'],True)
        with self.assertRaises(ValueError):
            self.run_action(action='guide',version=1,text='오래된 화면입니다.',photo='')

    def test_duplicate_request_and_version(self):
        self.request()
        with self.assertRaises(ValueError):
            self.request(actor='ra',judge='da')
        with self.assertRaises(ValueError):
            self.run_action('ga',now=self.r['start'],action='record',version=999,record=reps(1))

if __name__=='__main__':
    unittest.main()
