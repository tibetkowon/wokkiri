const $ = s => document.querySelector(s);
const e = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, selected = null, tab = 'ranking', roundId = null, timer;
// 초대 링크(/?join=코드)로 들어왔는지, 로그인 화면의 모드, 방금 만든 PIN 초기화 코드입니다.
const inviteToken = new URLSearchParams(location.search).get('join');
let invite = null, authMode = inviteToken ? 'signup' : 'login', resetInfo = null;
// 새 리그 달력: 날짜는 (요일이 켜짐) XOR (개별로 뒤집음)일 때 선택입니다.
let weekdaysOn = new Set([1, 2, 3, 4, 5]), flipped = new Set();
let pick = null;
// shortcut: 2027년 말까지의 공휴일·대체공휴일 표(2027년 월력요항 기준)입니다. 임시공휴일이 생기거나 2028년 리그를 열기 전에 갱신합니다.
const HOLIDAYS = {'2026-12-25':'성탄절', '2027-01-01':'신정', '2027-02-06':'설 연휴', '2027-02-07':'설날', '2027-02-08':'설 연휴', '2027-02-09':'대체공휴일', '2027-03-01':'삼일절', '2027-05-01':'노동절', '2027-05-03':'대체공휴일', '2027-05-05':'어린이날', '2027-05-13':'부처님오신날', '2027-06-06':'현충일', '2027-07-17':'제헌절', '2027-07-19':'대체공휴일', '2027-08-15':'광복절', '2027-08-16':'대체공휴일', '2027-09-14':'추석 연휴', '2027-09-15':'추석', '2027-09-16':'추석 연휴', '2027-10-03':'개천절', '2027-10-04':'대체공휴일', '2027-10-09':'한글날', '2027-10-11':'대체공휴일', '2027-12-25':'성탄절', '2027-12-27':'대체공휴일'};
const typeNames = {time:'For Time', amrap:'AMRAP', reps:'For Max Reps', weight:'For Load', done:'For Completion (EMOM 등)'};
const kindNames = {daily:'일상 WOD', designated:'지정 WOD'};
const statusNames = {author:'본인 답변을 기다리는 중', judge:'판정을 기다리는 중', closed:'마무리됨'};
const weekdayNames = ['일','월','화','수','목','금','토'];
const utc = s => new Date(s + 'T00:00:00Z');
const monthDay = s => `${+s.slice(5, 7)}/${+s.slice(8, 10)}`;
const addDays = (s, n) => new Date(utc(s).getTime() + n*86400000).toISOString().slice(0, 10);
const longDate = s => `${monthDay(s)} (${weekdayNames[utc(s).getUTCDay()]})`;
// 본인 요청은 "가람 → 가람" 대신 정정·지연 제출로 표시합니다.
const parties = q => q.requester === q.target ? `${name(q.requester)} · ${q.baseVersion ? '본인 정정' : '지연 제출'}` : `${name(q.requester)} → ${name(q.target)}`;
const format = o => n => new Date(n*1000).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', ...o});
const dateTime = format({month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false});
const dateOnly = format({month:'numeric', day:'numeric'});
const day = n => new Date(n*1000 + 9*3600000).toISOString().slice(0, 10);
const name = u => e(state.users[u]);
// 선택지가 적고 고정된 항목은 한눈에 보이는 버튼 묶음으로, 회원 목록처럼 길어지는 항목만 select로 둡니다.
const choices = (legend, field, items, current, cls = '') => {
  const on = items.some(([v]) => v === current) ? current : items[0][0];
  return `<fieldset class="choice ${cls}"><legend>${legend}</legend>${items.map(([v, t]) => `<label><input type="radio" name="${field}" value="${e(v)}" ${v === on ? 'checked' : ''}>${e(t)}</label>`).join('')}</fieldset>`;
};
const options = (items, current) => items.map(([v, t]) => `<option value="${e(v)}" ${v === current ? 'selected' : ''}>${e(t)}</option>`).join('');
const waiting = (q, u) => (q.status === 'author' && q.target === u) || (q.status === 'judge' && q.judge === u);

function notify(t) {
  clearTimeout(timer);
  $('#message').textContent = t;
  timer = setTimeout(() => $('#message').textContent = '', 5000);
}

async function api(path, data) {
  const r = await fetch(path, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
  const body = await r.json();
  if (!r.ok) throw Error(body.error);
  return body;
}

async function load() {
  state = await api('/api/state');
  if (inviteToken && invite === null) {
    invite = await api('/api/invite?t=' + encodeURIComponent(inviteToken)).catch(() => false);
    if (!invite) notify('초대 링크가 올바르지 않아요. 보내 준 친구에게 다시 물어봐 주세요.');
  }
  if (inviteToken && invite && state.actor) await enterInvited();
  render();
}

// 초대 링크로 들어온 로그인 회원은 그 리그에 넣고 바로 열어 줍니다. 한 번만 처리합니다.
async function enterInvited() {
  try {
    let l = state.leagues.find(x => x.invite === inviteToken);
    if (!l) {
      const joined = await api('/api/action', {action:'join', invite:inviteToken});
      state = await api('/api/state');
      l = state.leagues.find(x => x.id === joined.league);
    }
    selected = l.id; tab = 'ranking'; roundId = null;
  } catch (err) { notify(err.message); }
  invite = false;
  history.replaceState(null, '', '/');
}

function authView(app) {
  const field = (label, field, attrs) => `<label>${label}<input name="${field}" ${attrs} required></label>`;
  const nick = field('닉네임', 'name', 'maxlength="12" autocomplete="username"');
  const pin = (label, auto) => field(label, 'pin', `type="password" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" autocomplete="${auto}"`);
  const card = invite ? `<p class="notice"><strong>${e(invite.name)}</strong>에 초대받았어요.<br><span class="sub">${kindNames[invite.kind]} · ${invite.members}명 · ${invite.open ? `${dateOnly(invite.start)} 시작 전까지 참가할 수 있어요` : '이미 시작해서 참가할 수 없어요'}</span></p>` : '';
  const forms = {
    login: `<form id="login">${nick}${pin('PIN (숫자 4자리)', 'current-password')}<button>로그인</button></form>`,
    signup: `<form id="signup">${nick}${pin('PIN (숫자 4자리)', 'new-password')}<p class="sub">이메일 같은 개인정보는 받지 않아요. PIN은 다른 기기에서 다시 들어올 때 써요. 잊어도 같은 리그 친구가 초기화해 줄 수 있어요.</p><button>가입하고 시작하기</button></form>`,
    recover: `<form id="recover">${nick}${field('초기화 코드 (숫자 6자리)', 'code', 'inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code"')}${pin('새 PIN (숫자 4자리)', 'new-password')}<p class="sub">같은 리그 친구에게 "내 PIN 초기화 코드 만들어 줘"라고 부탁해 주세요. 리그의 순위 탭에서 만들 수 있어요.</p><button>새 PIN 정하기</button></form>`,
  };
  const tabs = `<nav class="tabs" aria-label="로그인 방식">${[['login', '로그인'], ['signup', '가입']].map(([v, t]) => `<button data-auth="${v}" class="${authMode === v ? 'active' : ''}">${t}</button>`).join('')}</nav>`;
  app.innerHTML = `<h1>친구끼리 여는 리그</h1><p class="sub">닉네임과 PIN 4자리만 있으면 돼요.</p>${card}${authMode === 'recover' ? '<h2>PIN을 잊었어요</h2>' : tabs}${forms[authMode]}${authMode === 'recover' ? '<button class="link" data-auth="login">‹ 로그인으로 돌아가기</button>' : '<button class="link" data-auth="recover">PIN을 잊었어요</button>'}`;
}

function describe(r) {
  if (!r) return '미참여';
  if (r.division === 'attendance') return '출석';
  const v = r.type === 'time' ? (r.finished ? `${Math.floor(r.value/60)}:${String(r.value%60).padStart(2, '0')}` : `Time Cap · ${r.value} reps`)
    : r.type === 'amrap' ? `${r.rounds}R + ${r.extra}`
    : r.type === 'done' ? (r.finished ? '완료' : '실패')
    : `${r.value} ${r.type === 'weight' ? 'lb' : 'reps'}`;
  return `${r.division === 'rx' ? 'Rx' : 'Scaled'} · ${v}`;
}

function recordFields(attendance, r) {
  const divisions = [['rx','Rx'], ['scaled','Scaled'], ...(attendance ? [['attendance','출석만']] : [])];
  return `${choices('Rx / Scaled', 'division', divisions, r?.division)}${choices('WOD 방식', 'type', Object.entries(typeNames), r?.type, 'typed')}<div class="values typed"></div>`;
}

function syncFields(form, r) {
  const f = form.elements, type = f.type.value;
  const finished = r ? r.finished !== false : f.finished?.value !== 'no';
  const num = (field, label, value, attrs = 'min="0" step="1"') => `<label>${label}<input name="${field}" type="number" ${attrs} value="${e(value ?? '')}"></label>`;
  form.querySelectorAll('.typed').forEach(el => el.hidden = f.division.value === 'attendance');
  form.querySelector('.values').innerHTML =
    type === 'time' ? choices('결과', 'finished', [['yes','완주'], ['no','Time Cap (시간 안에 못 끝냄)']], finished ? 'yes' : 'no') + (finished
        ? `<div class="row">${num('min', '분', r?.finished ? Math.floor(r.value/60) : '')}${num('sec', '초', r?.finished ? r.value%60 : '', 'min="0" max="59" step="1"')}</div>`
        : num('value', 'Time Cap까지 완료한 Reps', r?.finished === false ? r.value : ''))
    : type === 'amrap' ? `<div class="row">${num('rounds', 'Rounds', r?.rounds ?? 0)}${num('extra', '+ Reps', r?.extra ?? 0)}</div>`
    : type === 'done' ? choices('결과', 'finished', [['yes','완료'], ['no','실패']], finished ? 'yes' : 'no')
    : type === 'weight' ? num('value', '지정 Reps를 모두 성공한 최고 중량 (lb)', r?.value, 'min="0.01" step="any"')
    : num('value', 'Total Reps', r?.value);
}

function readRecord(form) {
  const f = form.elements, r = {division:f.division.value, type:f.type.value};
  const need = el => { if (el.value === '') throw Error('기록값을 입력해 주세요.'); return Number(el.value); };
  if (r.division === 'attendance') return {division:r.division};
  if (r.type === 'time') {
    r.finished = f.finished.value === 'yes';
    if (!r.finished) r.value = need(f.value);
    else {
      if (f.min.value === '' && f.sec.value === '') throw Error('완주 시간을 입력해 주세요.');
      if (Number(f.sec.value) > 59) throw Error('초는 0~59로 입력해 주세요.');
      r.value = Number(f.min.value)*60 + Number(f.sec.value);
    }
  } else if (r.type === 'amrap') { r.rounds = Number(f.rounds.value); r.extra = Number(f.extra.value); }
  else if (r.type === 'done') r.finished = f.finished.value === 'yes';
  else r.value = need(f.value);
  return r;
}

function render() {
  const app = $('#app'), me = state.actor;
  $('#who').innerHTML = me ? `${name(me)} · <button class="link" data-logout>로그아웃</button>` : '';
  if (!me) { authView(app); return; }
  if (selected === 'new') { createView(app); return; }
  const l = state.leagues.find(x => x.id === selected);
  if (!l?.rounds) { leagueList(app); return; }
  const mine = l.requests.filter(q => waiting(q, me)).length;
  const tabs = [['ranking','순위'], ['round','기록'], ['requests', mine ? `요청 ${mine}` : '요청'], ['history','이력']];
  app.innerHTML = `<button class="link" data-back>‹ 리그 목록</button><h1>${e(l.name)}</h1>
    <p class="sub">${kindNames[l.kind]} · ${l.members.length}명 · ${dateOnly(l.rounds[0].start)} ~ ${dateOnly(l.rounds.at(-1).close - 1)} · ${l.final ? '최종 확정' : '잠정 순위'}</p>
    <p class="sub">기록 수정 요청은 ${dateTime(l.requestClose)}까지 받아요.</p>
    ${l.start > state.now ? '<button class="secondary" data-invite>친구 초대 링크 복사</button>' : ''}
    <nav class="tabs" aria-label="리그 메뉴">${tabs.map(([v, t]) => `<button data-tab="${v}" class="${tab === v ? 'active' : ''}">${t}</button>`).join('')}</nav><div id="panel"></div>`;
  ({round:roundPanel, ranking:rankingPanel, requests:requestsPanel, history:historyPanel})[tab](l, $('#panel'));
}

function leagueList(app) {
  const item = x => `<li><div><b>${e(x.name)}</b><span class="sub">${kindNames[x.kind]} · ${x.members.length}명 · ${x.start > state.now ? `${dateOnly(x.start)} 시작` : '진행 중'}</span></div><button class="secondary" data-open="${x.id}">보기</button></li>`;
  app.innerHTML = `<h1>리그</h1>${state.leagues.length ? `<ul class="leagues">${state.leagues.map(item).join('')}</ul>` : '<p class="sub">아직 참여 중인 리그가 없어요. 새 리그를 만들거나, 친구가 보내 준 초대 링크로 들어와 주세요.</p>'}<button data-new>새 리그 만들기</button>`;
}

function createView(app) {
  const tomorrow = day(state.now + 86400);
  weekdaysOn = new Set([1, 2, 3, 4, 5]);
  flipped = new Set();
  pick = {start:tomorrow, end:addDays(tomorrow, 6), mode:'days', month:tomorrow.slice(0, 8) + '01'};
  app.innerHTML = `<button class="link" data-back>‹ 리그 목록</button><h1>새 리그</h1><form id="create">
    <label>리그 이름<input name="name" required maxlength="60"></label>
    <div class="seg">
      <label><input type="radio" name="kind" value="daily" checked><b>${kindNames.daily}</b><span class="sub">고른 날마다 그날의 WOD로 겨뤄요. 출석만 해도 점수가 들어가요.</span></label>
      <label><input type="radio" name="kind" value="designated"><b>${kindNames.designated}</b><span class="sub">라운드마다 WOD 하나를 정해 기간 안에 기록을 내요.</span></label>
    </div>
    <div class="period"><button type="button" data-pick="start"><small>시작일</small><b id="startText"></b></button><button type="button" data-pick="end" id="endField"><small>종료일</small><b id="endText"></b></button></div>
    <div id="designatedSchedule" hidden><div class="row"><label>라운드 수<input name="count" type="number" value="3" min="1" max="52"></label><label>라운드당 기간(일)<input name="duration" type="number" value="7" min="1" max="30"></label></div></div>
    <p class="sub" id="calHint"></p><div id="dates"></div>
    <p class="sub">참가는 시작일 전까지 받아요.</p><button>리그 만들기</button></form>`;
  calendar();
}

function pickedDates() {
  const dates = [], last = utc(pick.end);
  for (let d = utc(pick.start); d <= last; d.setUTCDate(d.getUTCDate() + 1)) {
    const value = d.toISOString().slice(0, 10);
    if ((weekdaysOn.has(d.getUTCDay()) && !HOLIDAYS[value]) !== flipped.has(value)) dates.push(value);
  }
  return dates;
}

function roundPanel(l, panel) {
  const me = state.actor;
  const rnd = l.rounds.find(r => r.id === roundId) || l.rounds.filter(r => r.start <= state.now).at(-1) || l.rounds[0];
  roundId = rnd.id;
  const own = rnd.records[me], s = rnd.score, started = state.now >= rnd.start, open = state.now < rnd.close;
  const direct = started && (open || (own && own.division !== 'attendance' && s.mismatch));
  // 요청 대상: 기록이 있는 다른 회원, 마감 뒤에는 본인(정정·지연 제출)
  const targets = l.members.filter(u => u === me ? !open : rnd.records[u]);
  const row = u => `<tr class="${u === me ? 'me' : ''}"><td>${name(u)}</td><td>${e(describe(rnd.records[u]))}</td><td class="num">${s.mismatch || !started ? '보류' : s.points[u]}</td></tr>`;
  const chip = r => `<button data-round="${r.id}" class="${r.id === rnd.id ? 'active' : ''}${r.score.mismatch ? ' bad' : ''}"${r.id === rnd.id ? ' aria-current="true"' : ''}>${l.kind === 'daily' ? `<small>${weekdayNames[utc(r.label).getUTCDay()]}</small>${monthDay(r.label)}` : e(r.label)}${r.score.mismatch ? '<em>확인 필요</em>' : ''}</button>`;
  panel.innerHTML = `<nav class="days" aria-label="${l.kind === 'daily' ? '날짜' : '라운드'} 선택">${l.rounds.map(chip).join('')}</nav>
    <p class="sub">기록 마감 ${dateTime(rnd.close)}${started ? (open ? '' : ' · 마감됨') : ' · 시작 전'}</p>
    ${s.mismatch ? `<p class="notice"><strong>확인이 필요해요. WOD 방식이 서로 달라서 점수를 잠시 보류했어요.</strong><br>${Object.entries(s.counts).map(([t, n]) => `${typeNames[t]} ${n}명`).join(' · ')}<br>방식이 하나로 맞춰지면 바로 다시 계산돼요. 기록을 올린 분은 마감 뒤에도 본인 기록을 고칠 수 있어요.</p>` : ''}
    <section><h2>WOD</h2><p class="guide">${e(rnd.guide.text) || '<span class="sub">아직 WOD가 올라오지 않았어요.</span>'}</p>${rnd.guide.photo ? `<img class="photo" src="${e(rnd.guide.photo)}" alt="WOD 사진">` : ''}
      ${started ? `<details><summary>WOD 편집</summary><form id="guide"><label>WOD 내용<textarea name="text" maxlength="4000">${e(rnd.guide.text)}</textarea></label><label>사진 (PNG/JPEG, 2MB 이하)<input name="photo" type="file" accept="image/png,image/jpeg"></label>${rnd.guide.photo ? '<label><input name="remove" type="checkbox">기존 사진 삭제</label>' : ''}<button class="secondary">WOD 저장</button></form></details>` : ''}</section>
    ${!direct ? '' : l.kind === 'daily' && !own
      ? '<section><h2>내 기록</h2><p class="sub">출석을 누르면 기록을 적을 수 있어요. 출석만 해도 점수가 들어가요.</p><button data-attend>출석</button></section>'
      : `<section><h2>내 기록${own?.division === 'attendance' ? ' · 출석 완료' : ''}</h2><form id="record">${recordFields(false, own)}<button>${own?.type ? '기록 수정' : '기록 저장'}</button></form></section>`}
    <section><h2>회차 기록</h2><table><thead><tr><th>회원</th><th>기록</th><th>점수</th></tr></thead><tbody>${s.order.map(row).join('')}</tbody></table>
      <p class="sub">참여하지 않으면 ${l.members.length}점이에요.${started && open && !s.mismatch ? ' 마감 전 점수는 바뀔 수 있어요.' : ''}</p>
      ${started && state.now < l.requestClose && targets.length ? `<details><summary>기록 수정 요청하기</summary><form id="request">
        <label>누구 기록인가요?<select name="target">${options(targets.map(u => [u, u === me ? `${state.users[u]} (본인)` : state.users[u]]))}</select></label>
        ${choices('어떤 요청인가요?', 'invalidate', [['no','기록을 고쳐 주세요'], ['yes','잘못 올라온 기록이에요 (삭제 요청)']], 'no', 'invalidate')}
        <div class="proposal"><p class="sub">이렇게 고치면 좋겠다 싶은 기록을 적어 주세요.</p>${recordFields(l.kind === 'daily')}</div>
        <label>이유<textarea name="reason" required maxlength="1000"></textarea></label>
        <label>확인해 줄 회원 (판정자)<select name="judge"></select></label><button>요청 보내기</button></form></details>` : ''}</section>`;
  panel.querySelector('.days .active').scrollIntoView({inline:'center', block:'nearest'});
  const names = ['division', 'type', 'finished'], rec = $('#record'), req = $('#request');
  if (rec) {
    syncFields(rec, own);
    rec.addEventListener('change', ev => { if (names.includes(ev.target.name)) syncFields(rec, ev.target.name === 'type' ? {} : undefined); });
  }
  if (req) {
    syncRequest(req, l, rnd);
    req.addEventListener('change', ev => { if (ev.target.name === 'target') syncRequest(req, l, rnd); else if (ev.target.name === 'invalidate') req.querySelector('.proposal').hidden = ev.target.value === 'yes'; else if (names.includes(ev.target.name)) syncFields(req, ev.target.name === 'type' ? {} : undefined); });
  }
}

function syncRequest(f, l, rnd) {
  const target = f.elements.target.value, r = rnd.records[target], self = target === state.actor;
  if (r) { f.elements.division.value = r.division; if (r.type) f.elements.type.value = r.type; }
  syncFields(f, r);
  f.querySelector('.invalidate').hidden = self;
  if (self) f.elements.invalidate.value = 'no';
  f.querySelector('.proposal').hidden = f.elements.invalidate.value === 'yes';
  const eligible = l.members.filter(u => u !== state.actor && u !== target);
  f.elements.judge.innerHTML = eligible.length ? options(eligible.map(u => [u, state.users[u]])) : '<option value="">없음 (2명 리그)</option>';
}

function rankingPanel(l, panel) {
  const others = l.members.filter(u => u !== state.actor);
  const help = !others.length ? '' : `<details ${resetInfo ? 'open' : ''}><summary>친구가 로그인하지 못하나요?</summary>
    <p class="sub">PIN을 잊은 친구가 있으면 초기화 코드를 만들어 전해 주세요. 친구가 로그인 화면의 "PIN을 잊었어요"에서 코드를 넣고 새 PIN을 정하면 돼요. 코드는 24시간 동안만 쓸 수 있고 이력에 남아요. 만들면 그 친구가 로그인해 둔 기기는 모두 로그아웃돼요.</p>
    ${resetInfo ? `<p class="notice"><strong>${name(resetInfo.user)}</strong> 님의 초기화 코드<br><b class="code">${resetInfo.code}</b></p>` : ''}
    <ul class="leagues">${others.map(u => `<li><b>${name(u)}</b><button class="secondary" data-reset="${u}">초기화 코드 만들기</button></li>`).join('')}</ul></details>`;
  panel.innerHTML = `<section><table><thead><tr><th>순위</th><th>회원</th><th>누적 점수</th></tr></thead><tbody>${l.standings.map(r => `<tr class="${r.user === state.actor ? 'me' : ''}"><td class="num">${r.rank}</td><td>${name(r.user)}</td><td>${r.points}</td></tr>`).join('')}</tbody></table>
    <p class="sub">점수가 낮을수록 순위가 높아요. ${l.rounds.length}회차 중 ${l.rounds.filter(r => r.counted).length}회차가 반영됐어요. 아직 시작하지 않은 회차와 WOD 방식이 엇갈린 회차는 빠져 있어요.</p>${help}</section>`;
}

function requestsPanel(l, panel) {
  const item = q => `<section><p><b>${parties(q)}</b> <span class="sub">${e(l.rounds.find(r => r.id === q.round).label)} · ${statusNames[q.status]}</span></p>
    <p>${e(q.reason)}</p><p class="sub">요청: ${e(q.proposal ? describe(q.proposal) : '기록 빼기')} · 판정자: ${q.judge ? name(q.judge) : '없음 (2명 리그)'}</p>
    ${q.reply ? `<p class="sub">본인 답변: ${e(q.reply)}</p>` : ''}${q.decisionReason ? `<p class="sub">판정 이유: ${e(q.decisionReason)}</p>` : ''}${q.decision ? `<p class="ok">${e(q.decision)}</p>` : ''}
    ${waiting(q, state.actor) ? `<form class="resolve" data-request="${q.id}" data-mode="${q.status}">${q.status === 'author'
      ? '<label>한마디 (거절할 때는 이유를 꼭 적어 주세요)<textarea name="reason" maxlength="1000"></textarea></label><div class="row"><button name="choice" value="accept">수락하고 반영</button><button name="choice" value="reject" class="secondary">거절</button></div>'
      : choices('판정', 'decision', [['keep','기존 기록 유지'], ['modify','요청대로 반영'], ['invalidate','기록 빼기']]) + '<label>이유<textarea name="reason" required maxlength="1000"></textarea></label><button>판정 완료</button>'}</form>` : ''}</section>`;
  panel.innerHTML = `<p class="sub">다른 회원 기록에 대한 요청은 본인이 수락하면 바로 반영돼요. 생각이 다르면 판정자가 정해 줘요. 마감 뒤 내 기록 수정이나 늦은 제출도 판정자가 확인해 줘요.</p>${l.requests.slice().reverse().map(item).join('') || '<section>아직 요청이 없어요.</section>'}`;
}

function historyPanel(l, panel) {
  const label = id => e(l.rounds.find(r => r.id === id)?.label ?? '');
  const rec = r => e(r ? describe(r) : '없음');
  const detail = h => h.request ? `${label(h.request.round)} · ${parties(h.request)}${h.request.decision ? ' · ' + e(h.request.decision) : ''}`
    : h.target ? `${label(h.round)} · ${name(h.target)} · ${rec(h.before)} → ${rec(h.after)}`
    : h.member ? name(h.member)
    : h.round !== undefined ? label(h.round) + (h.before.photo !== h.after.photo ? ' · 사진 변경' : '')
    : '';
  panel.innerHTML = `<section>${l.history.length ? `<ul class="log">${l.history.slice().reverse().map(h => `<li><time>${dateTime(h.at)}</time><b>${name(h.actor)}</b> ${e(h.action)}<br><span class="sub">${detail(h)}</span></li>`).join('')}</ul>` : '아직 변경 이력이 없어요.'}</section>`;
}

function calendar() {
  const f = $('#create');
  if (!f) return;
  const daily = f.elements.kind.value === 'daily', count = Number(f.elements.count.value), duration = Number(f.elements.duration.value);
  const end = daily ? pick.end : addDays(pick.start, Math.max(count*duration || 1, 1) - 1);
  if (!daily && pick.mode === 'end') pick.mode = 'days';
  $('#endField').hidden = !daily;
  $('#designatedSchedule').hidden = daily;
  $('#startText').textContent = longDate(pick.start);
  $('#endText').textContent = longDate(pick.end);
  f.querySelectorAll('[data-pick]').forEach(b => b.setAttribute('aria-pressed', b.dataset.pick === pick.mode));
  const picked = new Set(daily ? pickedDates() : []), tomorrow = day(state.now + 86400);
  $('#calHint').textContent = pick.mode === 'start' ? '달력에서 시작일을 눌러 주세요.'
    : pick.mode === 'end' ? '달력에서 종료일을 눌러 주세요. 시작일부터 1년 안에서 고를 수 있어요.'
    : daily ? `운동하는 날 ${picked.size}일 · 요일이나 날짜를 눌러 넣고 뺄 수 있어요. 공휴일은 미리 빼 두었어요.`
    : `${monthDay(pick.start)} ~ ${monthDay(end)} · ${duration}일씩 ${count}라운드가 쉬는 날 없이 이어져요.`;
  // 누를 수 있는 날: 시작일 고르기는 내일부터, 종료일 고르기는 시작일부터 1년, 그 밖에는 기간 안의 날짜
  const can = v => pick.mode === 'start' ? v >= tomorrow : pick.mode === 'end' ? v >= pick.start && v <= addDays(pick.start, 366) : daily && v >= pick.start && v <= end;
  const tone = (w, v) => w === 0 || HOLIDAYS[v] ? 'red' : w === 6 ? 'blue' : '';
  const m = utc(pick.month);
  let html = `<div class="calnav"><button type="button" class="secondary" data-month="-1" aria-label="이전 달">‹</button><b>${m.getUTCFullYear()}년 ${m.getUTCMonth() + 1}월</b><button type="button" class="secondary" data-month="1" aria-label="다음 달">›</button></div>
    <div class="cal head">${weekdayNames.map((t, w) => daily ? `<button type="button" class="${tone(w)}" data-weekday="${w}" aria-pressed="${weekdaysOn.has(w)}">${t}</button>` : `<span class="${tone(w)}">${t}</span>`).join('')}</div>
    <div class="cal">${'<span></span>'.repeat(m.getUTCDay())}`;
  for (let d = new Date(m); d.getUTCMonth() === m.getUTCMonth(); d.setUTCDate(d.getUTCDate() + 1)) {
    const v = d.toISOString().slice(0, 10), inside = v >= pick.start && v <= end;
    const attrs = `class="${tone(d.getUTCDay(), v)}"${HOLIDAYS[v] ? ` title="${HOLIDAYS[v]}"` : ''}`;
    html += can(v) ? `<button type="button" ${attrs} data-date="${v}" aria-pressed="${daily ? picked.has(v) : inside}">${d.getUTCDate()}</button>` : `<span ${attrs}${!daily && inside ? ' data-in' : ''}>${d.getUTCDate()}</span>`;
  }
  $('#dates').innerHTML = html + '</div>';
}

document.addEventListener('click', async ev => {
  const b = ev.target.closest('button');
  if (!b) return;
  try {
    if (b.hasAttribute('data-logout')) { await api('/api/logout', {}); selected = null; roundId = null; tab = 'ranking'; resetInfo = null; await load(); }
    else if (b.dataset.auth) { authMode = b.dataset.auth; render(); }
    else if (b.hasAttribute('data-back')) { selected = null; await load(); }
    else if (b.hasAttribute('data-new')) { selected = 'new'; render(); }
    else if (b.dataset.open) { selected = b.dataset.open; roundId = null; tab = 'ranking'; await load(); }
    else if (b.hasAttribute('data-invite')) {
      const url = `${location.origin}/?join=${state.leagues.find(l => l.id === selected).invite}`;
      try { await navigator.clipboard.writeText(url); notify('초대 링크를 복사했어요. 단톡방에 붙여 넣어 주세요.'); }
      catch { prompt('이 링크를 복사해서 친구에게 보내 주세요.', url); }
    }
    else if (b.dataset.reset) {
      const issued = await api('/api/reset', {target:b.dataset.reset});
      resetInfo = {user:b.dataset.reset, code:issued.code};
      await load();
    }
    else if (b.dataset.round) { roundId = b.dataset.round; await load(); }
    else if (b.hasAttribute('data-attend')) {
      const rnd = state.leagues.find(l => l.id === selected).rounds.find(r => r.id === roundId);
      await api('/api/action', {action:'record', league:selected, round:roundId, record:{division:'attendance'}, version:rnd.versions[state.actor] || 0});
      await load();
      notify('출석했어요.');
    }
    else if (b.dataset.date) {
      const v = b.dataset.date;
      if (pick.mode === 'start') {
        pick.start = v;
        if (pick.end < v || pick.end > addDays(v, 366)) pick.end = addDays(v, 6);
        pick.mode = 'days';
      }
      else if (pick.mode === 'end') { pick.end = v; pick.mode = 'days'; }
      else flipped.has(v) ? flipped.delete(v) : flipped.add(v);
      calendar();
    }
    else if (b.dataset.pick) {
      pick.mode = pick.mode === b.dataset.pick ? 'days' : b.dataset.pick;
      pick.month = pick[b.dataset.pick].slice(0, 8) + '01';
      calendar();
    }
    else if (b.dataset.month) {
      const m = utc(pick.month);
      m.setUTCMonth(m.getUTCMonth() + Number(b.dataset.month));
      pick.month = m.toISOString().slice(0, 10);
      calendar();
    }
    else if (b.dataset.weekday) {
      const w = Number(b.dataset.weekday);
      weekdaysOn.has(w) ? weekdaysOn.delete(w) : weekdaysOn.add(w);
      // 요일을 바꾸면 그 요일에 개별로 뒤집어 둔 날짜는 버립니다.
      flipped.forEach(v => { if (utc(v).getUTCDay() === w) flipped.delete(v); });
      calendar();
    }
    // 다른 회원의 변경이 보이도록 탭을 옮길 때마다 다시 불러옵니다.
    else if (b.dataset.tab) { tab = b.dataset.tab; resetInfo = null; await load(); }
  } catch (err) { notify(err.message); }
});

document.addEventListener('change', ev => {
  if (ev.target.closest('#create') && ev.target.name !== 'name') calendar();
});

document.addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = ev.target, btn = ev.submitter;
  const l = state.leagues.find(x => x.id === selected), rnd = l?.rounds?.find(x => x.id === roundId);
  const data = {league:selected, round:roundId};
  try {
    if (btn) btn.disabled = true;
    if (['login', 'signup', 'recover'].includes(f.id)) {
      const v = n => f.elements[n]?.value;
      await api('/api/' + f.id, f.id === 'recover' ? {name:v('name'), code:v('code'), pin:v('pin')} : {name:v('name'), pin:v('pin')});
      authMode = 'login';
      await load();
      return;
    }
    if (f.id === 'create') Object.assign(data, {action:'create', name:f.elements.name.value, kind:f.elements.kind.value, start:pick.start, end:pick.end, dates:f.elements.kind.value === 'daily' ? pickedDates() : [], count:Number(f.elements.count.value), duration:Number(f.elements.duration.value)});
    else if (f.id === 'record') Object.assign(data, {action:'record', record:readRecord(f), version:rnd.versions[state.actor] || 0});
    else if (f.id === 'guide') {
      let photo = rnd.guide.photo;
      const file = f.elements.photo.files[0];
      if (file) {
        if (file.size > 2*1024*1024) throw Error('사진은 2MB 이하로 선택해 주세요.');
        photo = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = () => reject(Error('사진을 읽지 못했습니다. 다시 선택해 주세요.'));
          reader.readAsDataURL(file);
        });
      }
      if (f.elements.remove?.checked) photo = '';
      Object.assign(data, {action:'guide', text:f.elements.text.value, photo, version:rnd.guideVersion});
    }
    else if (f.id === 'request') Object.assign(data, {action:'request', target:f.elements.target.value, proposal:f.elements.invalidate.value === 'yes' ? null : readRecord(f), judge:f.elements.judge.value, reason:f.elements.reason.value});
    else if (f.classList.contains('resolve')) {
      const reason = f.elements.reason.value.trim();
      if (f.dataset.mode === 'judge') Object.assign(data, {action:'decide', request:f.dataset.request, reason, decision:f.elements.decision.value});
      else {
        if (btn.value === 'reject' && !reason) throw Error('거절하는 이유를 적어 주세요.');
        Object.assign(data, {action:'reply', request:f.dataset.request, reason, accept:btn.value === 'accept'});
      }
    }
    else return;
    const response = await api('/api/action', data);
    if (data.action === 'create') { selected = response.league; roundId = null; tab = 'ranking'; }
    if (data.action === 'request') tab = 'requests';
    await load();
    notify('저장했어요.');
  } catch (err) { notify(err.message); }
  finally { if (btn) btn.disabled = false; }
});

load().catch(err => notify('불러오지 못했어요. ' + err.message));
