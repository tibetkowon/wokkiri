const $ = s => document.querySelector(s);
const e = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, selected = null, tab = 'ranking', roundId = null, timer;
// 초대 링크(/?join=코드)로 들어왔는지, 로그인 화면의 모드, 방금 받아 아직 저장 확인을 안 한 복구 키, 초대받은 리그에서 닉네임이 겹쳤는지입니다.
const inviteToken = new URLSearchParams(location.search).get('join');
let invite = null, authMode = inviteToken ? 'signup' : 'login', recoveryKey = null, needNick = false, inviteOpen = false;
// 복구 키를 받고 아직 "계속하기"로 확인하지 않았다는 표시입니다. 키 평문은 메모리(recoveryKey)에만 두고,
// 새로고침해도 확인 단계를 건너뛰지 않도록 sessionStorage에는 이 표시(비밀값 아님)만 남깁니다.
let keyPending = false;
try { keyPending = sessionStorage.getItem('keyPending') === '1'; } catch {}
function setKeyPending(on) {
  keyPending = on;
  if (!on) recoveryKey = null;
  try { on ? sessionStorage.setItem('keyPending', '1') : sessionStorage.removeItem('keyPending'); } catch {}
}
// 열어 본 리그에서 쓰는 닉네임(리그마다 다를 수 있음)입니다.
let names = {};
// 새 리그 달력: 날짜는 (요일이 켜짐) XOR (개별로 뒤집음)일 때 선택입니다.
let weekdaysOn = new Set([1, 2, 3, 4, 5]), flipped = new Set();
let pick = null;
// 공휴일·대체공휴일 표({날짜: 이름})는 시작할 때 /holidays.json에서 읽습니다(갱신: scripts/holidays.py). 못 읽으면 빈 표로 동작합니다.
let holidays = {};
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
const name = u => e(names[u]);
// 선택지가 적고 고정된 항목은 한눈에 보이는 버튼 묶음으로, 회원 목록처럼 길어지는 항목만 select로 둡니다.
const choices = (legend, field, items, current, cls = '') => {
  // current가 null이면 아무것도 고르지 않은 채로 시작합니다.
  const on = items.some(([v]) => v === current) || current === null ? current : items[0][0];
  return `<fieldset class="choice ${cls}"><legend>${legend}</legend>${items.map(([v, t]) => `<label><input type="radio" name="${field}" value="${e(v)}" ${v === on ? 'checked' : ''}>${e(t)}</label>`).join('')}</fieldset>`;
};
const options = (items, current) => items.map(([v, t]) => `<option value="${e(v)}" ${v === current ? 'selected' : ''}>${e(t)}</option>`).join('');
// 문의·건의를 받는 주소입니다. 서버는 메일을 보내지 않고, 화면이 mailto 링크와 주소 복사만 제공합니다.
const CONTACT = 'tibetkowon@gmail.com';
// 도움말 문구는 여기에만 둡니다. tips는 ⓘ 버튼의 [이름, 설명], guide는 "사용 방법" 화면의 [제목, 문단들]입니다.
const HELP = {
  tips: {
    division: ['Rx와 Scaled', 'Rx는 WOD에 적힌 무게와 동작 그대로, Scaled는 무게나 동작을 조절해서 한 기록이에요. 순위는 Rx가 Scaled보다 앞이에요.'],
    attendance: ['Rx, Scaled, 출석만', 'Rx는 WOD에 적힌 무게와 동작 그대로, Scaled는 무게나 동작을 조절해서 한 기록이에요. 출석만은 기록 없이 출석만 남기고, 순위는 Rx, Scaled, 출석만 순서예요.'],
    type: ['WOD 방식', 'For Time은 끝낸 시간, AMRAP은 Rounds와 추가 Reps, For Max Reps는 총 횟수, For Load는 중량(lb), For Completion은 완료 여부를 적어요. 같은 회차에서는 모두 같은 방식으로 올려야 비교할 수 있어요.'],
    deadline: ['기록 마감', '일상 WOD는 그날이 지난 밤 12시, 지정 WOD는 라운드 마지막 날이 지난 밤 12시에 마감해요(한국 시간). 마감 전에는 내 기록을 마음대로 고칠 수 있어요.'],
    mismatch: ['WOD 방식이 다를 때', '같은 회차 기록은 WOD 방식이 같아야 비교할 수 있어요. 방식이 하나로 맞을 때까지 이 회차 점수는 누적 점수에서 빠져요.'],
    points: ['회차 점수', '그 회차의 등수가 곧 점수예요. 기록이 같으면 공동순위라서 1, 2, 2, 4처럼 매겨요.'],
    absent: ['참여하지 않았을 때', '출석도 기록도 없으면 등수와 상관없이 리그 인원수만큼 점수를 받아요. 출석만 한 회원은 기록을 올린 회원 다음 공동순위예요.'],
    pending: ['집계 전', '마감 전에는 아직 참여할 수 있어서 점수를 매기지 않아요. 마감하면 점수와 순위가 정해지고 누적 점수에 들어가요.'],
    total: ['누적 점수', '마감한 회차의 점수를 모두 더한 값이에요. 낮을수록 순위가 높고, 같으면 공동순위예요.'],
    final: ['잠정 순위와 최종 확정', '요청 접수가 끝나고, 모든 요청이 마무리되고, WOD 방식이 엇갈린 회차가 없으면 최종 확정돼요. 그 전에는 잠정 순위예요.'],
    window: ['요청 접수 기간', '마지막 회차가 마감한 뒤 3일 동안은 새 요청을 보낼 수 있어요. 이미 보낸 요청의 답변과 판정에는 기한이 없어요.'],
    request: ['기록 수정 요청', '마감 뒤에 내 기록을 고치면 정정, 늦게 내면 지연 제출, 다른 회원 기록을 고쳐 달라고 하면 이의 제기예요. 모두 요청으로 보내요.'],
    judge: ['판정자', '요청을 확인하고 결정해 줄 회원이에요. 요청한 사람과 기록 주인을 뺀 회원 중에서 골라요.'],
    key: ['복구 키', 'PIN을 잊었을 때 계정을 되찾는 열쇠예요. 한 번만 보여 드리니 안전한 곳에 저장해 주세요.'],
  },
  guide: [
    ['와끼리가 뭐예요', [
      '같은 박스 회원끼리 기간을 정해 여는 리그예요. 회차마다 WOD(그날의 운동) 기록을 올리고, 점수를 쌓아 순위를 겨뤄요.',
      '일상 WOD 리그는 고른 날마다 그날의 WOD로 겨뤄요. 지정 WOD 리그는 라운드마다 WOD 하나를 정해 기간 안에 기록을 내요.',
      '코치 승인 없이 회원끼리 운영해요. 기록은 각자 올리고, 잘못된 기록은 서로 요청해서 바로잡아요.',
    ]],
    ['처음 시작하기', [
      '가입은 아이디, 닉네임, PIN 숫자 4자리면 돼요. 이메일 같은 개인정보는 받지 않고, 다른 회원에게는 닉네임만 보여요.',
      '가입하면 복구 키를 한 번만 보여 드려요. PIN을 잊었을 때 필요하니 안전한 곳에 저장해 주세요.',
      '리그 목록의 "새 리그 만들기"에서 이름, 종류, 기간을 정해요. 시작일은 내일 이후로 고를 수 있어요.',
      '친구는 시작 전 리그 화면의 "친구 초대"에 있는 링크나 QR 코드로 들어와요. 시작일부터는 참가할 수 없어요.',
    ]],
    ['기록 올리기', [
      '리그의 "기록" 탭에서 날짜나 라운드를 고르고 기록을 올려요. WOD 내용은 누구나 글과 사진으로 올리고 고칠 수 있어요.',
      '일상 WOD는 "출석"을 먼저 누르면 기록 칸이 열려요. 기록 없이 출석만 해도 점수가 들어가요. 지정 WOD는 출석만이 없어서 기록을 올려야 참여예요.',
      'Rx는 WOD에 적힌 무게와 동작 그대로 한 기록, Scaled는 무게나 동작을 조절해서 한 기록이에요. 어느 쪽인지는 스스로 골라요.',
      'For Time(빨리 끝내기)은 완주 시간을 적어요. 시간 안에 못 끝냈으면 Time Cap을 고르고 그때까지 완료한 Reps(횟수)를 적어요.',
      'AMRAP(정해진 시간에 최대한 많이)은 완료한 Rounds와 추가 Reps를 적어요.',
      'For Max Reps는 총 Reps를 적고, For Completion(EMOM처럼 완료가 목표)은 완료 또는 실패를 골라요.',
      'For Load는 지정 Reps를 모두 성공한 가장 무거운 중량을 lb(파운드)로 적어요. 성공한 중량이 없으면 일상 WOD에서는 "출석만"을 골라 주세요.',
      '0회도 기록이에요. 해 봤지만 하나도 못 했다면 0으로 올려 주세요. 출석만 한 것보다 순위가 앞이에요.',
    ]],
    ['점수와 순위', [
      '회차마다 등수가 그대로 점수예요. 점수를 모두 더한 누적 점수가 낮을수록 순위가 높아요.',
      '등수는 Rx, Scaled, 출석만 순서로 매기고, 같은 부문 안에서는 기록으로 비교해요.',
      '기록이 같으면 공동순위예요. 1등이 한 명, 2등이 두 명이면 다음은 4등이에요(1, 2, 2, 4).',
      '출석만 한 회원은 기록을 올린 회원 다음 공동순위예요. 출석도 기록도 없으면 리그 인원수만큼 점수를 받아요.',
      '마감 전에는 참여하지 않은 회원이 "집계 전"으로 보여요. 마감하면 그 회차의 점수와 순위가 정해지고 누적 점수에 들어가요.',
      '예: 14명 리그에서 8명이 기록을 올리고 3명이 출석만 하고 3명이 안 나왔다면, 기록을 올린 8명은 1~8점, 출석만 한 3명은 모두 9점, 안 나온 3명은 14점이에요.',
    ]],
    ['마감과 수정', [
      '일상 WOD는 그날이 지난 밤 12시(다음 날 00:00), 지정 WOD는 라운드 마지막 날이 지난 밤 12시에 마감해요. 한국 시간 기준이에요.',
      '마감 전에는 내 기록을 마음대로 올리고 고칠 수 있어요.',
      '마감 뒤에 고치거나 늦게 내려면 "기록" 탭의 "기록 수정 요청하기"로 요청을 보내요. 기록과 이유를 적고 판정자를 고르면, 판정자가 확인하고 결정해요.',
    ]],
    ['WOD 방식이 서로 다를 때', [
      '같은 회차에 올라온 기록의 WOD 방식이 서로 다르면(예: 한 명은 AMRAP, 다른 한 명은 For Max Reps) 비교할 수 없어요. 그 회차 전체 점수를 보류하고 누적 점수에서도 빼 둬요.',
      '누가 틀렸는지는 따지지 않아요. 회차 위쪽에 방식별 인원이 보이니 서로 확인해 주세요. 출석만 한 회원은 세지 않아요.',
      '기록을 올린 회원은 마감 뒤에도 본인 기록의 방식과 값을 직접 고칠 수 있어요. 방식이 하나로 맞으면 바로 다시 계산돼요.',
    ]],
    ['이의 제기와 판정자', [
      '다른 회원의 기록이 잘못됐다고 생각하면 "기록 수정 요청하기"로 요청을 보내요. 고칠 기록(또는 기록 삭제)과 이유를 적고 판정자를 골라요.',
      '판정자는 요청한 사람과 기록 주인을 뺀 회원 중에서 요청한 사람이 골라요. 리그를 만든 사람도 남의 기록을 마음대로 고칠 수 없어요.',
      '기록 주인이 수락하면 바로 반영되고 끝나요. 거절하면 판정자가 기존 기록 유지, 요청대로 반영, 기록 삭제 중 하나로 정해요.',
      '2명 리그는 판정자가 없어요. 기록 주인이 거절하면 기존 기록을 그대로 두고 끝나요.',
      '받은 요청은 "요청" 탭에서 답하거나 판정해요.',
    ]],
    ['리그가 끝난 뒤', [
      '마지막 회차가 마감하면 리그 기간이 끝나요. 그 뒤 3일(72시간) 동안은 새 요청을 보낼 수 있어요.',
      '이미 보낸 요청의 답변과 판정에는 기한이 없어요.',
      '요청 접수가 끝나고, 모든 요청이 마무리되고, WOD 방식이 엇갈린 회차가 없으면 순위가 최종 확정돼요. 그 전에는 "잠정 순위"로 보여요.',
    ]],
    ['로그인과 복구', [
      '아이디와 PIN으로 로그인해요. PIN이나 복구 키를 5번 틀리면 10분 동안 잠겨요.',
      'PIN을 잊었으면 로그인 화면의 "PIN을 잊었어요"에서 아이디와 복구 키를 넣고 새 PIN을 정해요. 복구 키도 새로 드리고, 다른 기기는 모두 로그아웃돼요.',
      '복구 키를 잃어버렸으면 리그 목록 아래 "복구 키 다시 만들기"에서 PIN을 넣고 새 키를 받아요. 이전 키는 쓸 수 없어요.',
      'PIN과 복구 키를 모두 잃으면 계정을 되찾을 수 없어요.',
    ]],
  ],
};
// ⓘ 버튼과 바로 아래에 펼쳐지는 설명입니다. label 안에 넣으면 라벨이 이 버튼에 묶이므로 legend·h2·p에만 둡니다.
let tipSeq = 0, help = false;
const tip = key => {
  const id = `tip-${++tipSeq}`, [label, body] = HELP.tips[key];
  return `<button type="button" class="tip" aria-expanded="false" aria-controls="${id}" aria-label="${e(label)} 설명">ⓘ</button><span class="tipbox" id="${id}" hidden>${e(body)}</span>`;
};
const toggleTip = (b, on) => { b.setAttribute('aria-expanded', on); document.getElementById(b.getAttribute('aria-controls')).hidden = !on; };
const contact = () => `<p class="sub">쓰다가 불편한 점이나 바라는 점이 있으면 메일로 알려 주세요. PIN과 복구 키는 적지 마세요.</p><p><b class="addr">${CONTACT}</b></p>
  <div class="row"><a class="button" href="mailto:${CONTACT}?subject=${encodeURIComponent('[와끼리] 문의·건의')}&amp;body=${encodeURIComponent('어떤 화면에서, 무엇을 하다가, 어떤 일이 있었는지 적어 주세요.')}">메일 보내기</a><button type="button" class="secondary" data-copy-mail>메일 주소 복사</button></div>`;
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
  if (!state.actor && keyPending) setKeyPending(false);
  // 복구 키 확인(계속하기)을 마친 뒤에 리그 참가로 넘어갑니다.
  if (inviteToken && invite && state.actor && !keyPending && !needNick) await enterInvited();
  render();
}

// 초대 링크로 들어온 로그인 회원은 그 리그에 넣고 바로 열어 줍니다. 한 번만 처리합니다.
async function enterInvited(nick) {
  try {
    let l = state.leagues.find(x => x.invite === inviteToken);
    if (!l) {
      const joined = await api('/api/action', {action:'join', invite:inviteToken, name:nick});
      state = await api('/api/state');
      l = state.leagues.find(x => x.id === joined.league);
    }
    selected = l.id; tab = 'ranking'; roundId = null;
  } catch (err) {
    notify(err.message);
    // 닉네임이 겹친 경우(서버 문구로 구분)에는 초대를 버리지 않고 이 리그에서 쓸 닉네임을 묻습니다.
    if (err.message.includes('같은 닉네임')) { needNick = true; return; }
  }
  leaveInvite();
}

function leaveInvite() {
  needNick = false; invite = false;
  history.replaceState(null, '', '/');
}

// 현재 PIN을 다시 넣어 복구 키를 새로 받는 폼입니다. 받으면 이전 키는 못 쓰게 됩니다.
const renewForm = label => `<form id="renew"><label>현재 PIN (숫자 4자리)<input name="pin" type="password" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" autocomplete="current-password" required></label><button>${label}</button></form>`;

function keyView(app) {
  // 확인 전에 새로고침하면 키가 메모리에서 사라집니다. 서버에도 평문이 없어 다시 보여 줄 수 없습니다.
  if (!recoveryKey) {
    app.innerHTML = `<h1>복구 키를 다시 보여 드릴 수 없어요</h1>
      <p class="notice"><strong>복구 키 화면이 닫혔어요.</strong><br>복구 키는 한 번만 보여 드려서 다시 보여 드릴 수 없어요. 저장해 두지 않았다면 PIN을 입력해 새 복구 키를 받아 주세요. 복구 키가 없으면 PIN을 잊었을 때 계정을 되찾을 수 없어요.</p>
      ${renewForm('새 복구 키 받기')}<button class="secondary" data-continue>계속하기</button>`;
    return;
  }
  app.innerHTML = `<h1>복구 키를 저장해 주세요</h1><p class="notice"><b class="code">${e(recoveryKey)}</b></p>
    <button class="secondary" data-copy>복구 키 복사</button>
    <p class="sub">스크린샷이나 메모 등 안전한 곳에 저장해 주세요. 다시 보여 드릴 수 없고, PIN을 잊으면 이 키가 있어야 계정을 되찾을 수 있어요.</p>
    <label><input type="checkbox" id="saved">저장했어요</label><button data-continue disabled>계속하기</button>`;
}

function helpView(app) {
  app.innerHTML = `<button class="link" data-help>‹ 돌아가기</button><h1>사용 방법</h1><p class="sub">제목을 누르면 내용이 펼쳐져요.</p>
    ${HELP.guide.map(([title, lines], i) => `<details class="help"${i ? '' : ' open'}><summary>${e(title)}</summary>${lines.map(t => `<p>${e(t)}</p>`).join('')}</details>`).join('')}
    <details class="help"><summary>문의·건의</summary>${contact()}</details>`;
}

function nickView(app) {
  app.innerHTML = `<h1>이 리그에서 쓸 닉네임</h1><p class="notice"><strong>${e(invite.name)}</strong>에 같은 닉네임을 쓰는 회원이 있어요.<br><span class="sub">이 리그에서만 쓸 닉네임을 정해 주세요. 계정 닉네임은 그대로예요.</span></p>
    <form id="nick"><label>이 리그에서 쓸 닉네임 (12자 이내)<input name="name" maxlength="12" required></label><button>참가하기</button></form>
    <button class="link" data-skip>참가하지 않기</button>`;
}

function authView(app) {
  const field = (label, field, attrs) => `<label>${label}<input name="${field}" ${attrs} required></label>`;
  const id = label => field(label, 'login', 'maxlength="20" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false"');
  const pin = (label, auto) => field(label, 'pin', `type="password" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" autocomplete="${auto}"`);
  const card = invite ? `<p class="notice"><strong>${e(invite.name)}</strong>에 초대받았어요.<br><span class="sub">${kindNames[invite.kind]} · ${invite.members}명 · ${invite.open ? `${dateOnly(invite.start)} 시작 전까지 참가할 수 있어요` : '이미 시작해서 참가할 수 없어요'}</span></p>` : '';
  const forms = {
    login: `<form id="login">${id('아이디')}${pin('PIN (숫자 4자리)', 'current-password')}<button>로그인</button></form>`,
    signup: `<form id="signup">${id('아이디 (영문 소문자·숫자·밑줄 4~20자)')}${field('닉네임 (12자 이내)', 'name', 'maxlength="12" autocomplete="nickname"')}${pin('PIN (숫자 4자리)', 'new-password')}<p class="sub">이메일 같은 개인정보는 받지 않아요. 아이디는 로그인할 때만 쓰고, 다른 회원에게는 닉네임만 보여요. 가입하면 PIN을 잊었을 때 쓸 복구 키를 드려요.${tip('key')}</p><button>가입하고 시작하기</button></form>`,
    recover: `<form id="recover">${id('아이디')}${field('복구 키', 'key', 'maxlength="20" placeholder="XXXX-XXXX-XXXX" autocomplete="off" autocapitalize="characters" autocorrect="off" spellcheck="false"')}${pin('새 PIN (숫자 4자리)', 'new-password')}<p class="sub">가입할 때 저장해 둔 복구 키를 넣어 주세요. 새 PIN을 정하면 복구 키도 새로 드리고, 로그인해 둔 다른 기기는 모두 로그아웃돼요.</p><button>새 PIN 정하기</button></form>`,
  };
  const tabs = `<nav class="tabs" aria-label="로그인 방식">${[['login', '로그인'], ['signup', '가입']].map(([v, t]) => `<button data-auth="${v}" class="${authMode === v ? 'active' : ''}">${t}</button>`).join('')}</nav>`;
  app.innerHTML = `<h1>친구끼리 여는 리그</h1><p class="sub">아이디와 PIN 4자리만 있으면 돼요.</p>${card}${authMode === 'recover' ? '<h2>PIN을 잊었어요</h2>' : tabs}${forms[authMode]}${authMode === 'recover' ? '<button class="link" data-auth="login">‹ 로그인으로 돌아가기</button>' : '<button class="link" data-auth="recover">PIN을 잊었어요</button>'}`;
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
  return `${choices('Rx / Scaled' + tip(attendance ? 'attendance' : 'division'), 'division', divisions, r?.division)}${choices('WOD 방식' + tip('type'), 'type', Object.entries(typeNames), r?.type, 'typed')}<div class="values typed"></div>`;
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
    : type === 'weight' ? num('value', '지정 Reps를 모두 성공한 최고 중량 (lb)', r?.value, 'min="0.01" step="any"') + (form.querySelector('[name=division][value=attendance]') ? '<p class="sub">지정 횟수를 모두 성공한 무게가 없으면 \'출석만\'을 선택해 주세요.</p>' : '')
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
  const app = $('#app');
  if (help) { helpView(app); return; }
  const me = state.actor;
  $('#who').innerHTML = me ? `<span class="nick">${e(state.users[me])}</span>&nbsp;· <button class="link" data-logout>로그아웃</button>` : '';
  if (keyPending) { keyView(app); return; }
  if (!me) { authView(app); return; }
  if (needNick) { nickView(app); return; }
  if (selected === 'new') { createView(app); return; }
  const l = state.leagues.find(x => x.id === selected);
  if (!l?.rounds) { leagueList(app); return; }
  names = l.names;
  const mine = l.requests.filter(q => waiting(q, me)).length;
  const tabs = [['ranking','순위'], ['round','기록'], ['requests', mine ? `요청 ${mine}` : '요청'], ['history','이력']];
  app.innerHTML = `<button class="link" data-back>‹ 리그 목록</button><h1>${e(l.name)}</h1>
    <p class="sub">${kindNames[l.kind]} · ${l.members.length}명 · ${dateOnly(l.rounds[0].start)} ~ ${dateOnly(l.rounds.at(-1).close - 1)} · ${l.final ? '최종 확정' : '잠정 순위'}${tip('final')}</p>
    <p class="sub">기록 수정 요청은 ${dateTime(l.requestClose)}까지 받아요.${tip('window')}</p>
    ${l.start > state.now ? `<button class="secondary" data-invite aria-expanded="${inviteOpen}">친구 초대</button>${inviteOpen ? inviteCard(l) : ''}` : ''}
    <nav class="tabs" aria-label="리그 메뉴">${tabs.map(([v, t]) => `<button data-tab="${v}" class="${tab === v ? 'active' : ''}">${t}</button>`).join('')}</nav><div id="panel"></div>`;
  ({round:roundPanel, ranking:rankingPanel, requests:requestsPanel, history:historyPanel})[tab](l, $('#panel'));
}

// 초대 링크에는 참가용 비밀 코드가 있어 QR은 외부 서비스 없이 브라우저 안(web/qrcode.js)에서 만듭니다.
function inviteCard(l) {
  const url = `${location.origin}/?join=${l.invite}`;
  let img = '';
  if (typeof qrcode === 'function') {
    const qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    img = `<img src="${qr.createDataURL(6)}" alt="초대 링크 QR 코드">`;
  }
  return `<div class="invite"><label>초대 링크<input readonly value="${e(url)}"></label><button class="secondary" data-copy-link>링크 복사</button>${img}<p class="sub">링크를 보내거나 QR 코드를 보여 주세요. 시작일 전까지 참가할 수 있어요.</p></div>`;
}

function leagueList(app) {
  const item = x => `<li><div><b>${e(x.name)}</b><span class="sub">${kindNames[x.kind]} · ${x.members.length}명 · ${x.start > state.now ? `${dateOnly(x.start)} 시작` : '진행 중'}</span></div><button class="secondary" data-open="${x.id}">보기</button></li>`;
  app.innerHTML = `<h1>리그</h1>${state.leagues.length ? `<ul class="leagues">${state.leagues.map(item).join('')}</ul>` : '<p class="sub">아직 참여 중인 리그가 없어요. 새 리그를 만들거나, 친구가 보내 준 초대 링크로 들어와 주세요.</p>'}<button data-new>새 리그 만들기</button>
    <details><summary>복구 키 다시 만들기</summary><p class="sub">복구 키를 잃어버렸거나 저장하지 못했다면 PIN을 입력해 새로 받아 주세요. 새 키를 받으면 이전 키는 쓸 수 없어요.${tip('key')}</p>${renewForm('새 복구 키 받기')}</details>
    <details><summary>문의·건의</summary>${contact()}</details>`;
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
    <p class="sub" id="calHint"></p><p class="sub" id="holidayNote" hidden></p><div id="dates"></div>
    <p class="sub">참가는 시작일 전까지 받아요.</p><button>리그 만들기</button></form>`;
  calendar();
}

function pickedDates() {
  const dates = [], last = utc(pick.end);
  for (let d = utc(pick.start); d <= last; d.setUTCDate(d.getUTCDate() + 1)) {
    const value = d.toISOString().slice(0, 10);
    if ((weekdaysOn.has(d.getUTCDay()) && !holidays[value]) !== flipped.has(value)) dates.push(value);
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
  const row = u => `<tr class="${u === me ? 'me' : ''}"><td>${name(u)}</td><td>${e(describe(rnd.records[u]))}</td><td class="num">${s.mismatch ? '보류' : s.points[u] ?? '집계 전'}</td></tr>`;
  const chip = r => `<button data-round="${r.id}" class="${r.id === rnd.id ? 'active' : ''}${r.score.mismatch ? ' bad' : ''}"${r.id === rnd.id ? ' aria-current="true"' : ''}>${l.kind === 'daily' ? `<small>${weekdayNames[utc(r.label).getUTCDay()]}</small>${monthDay(r.label)}` : e(r.label)}${r.score.mismatch ? '<em>확인 필요</em>' : ''}</button>`;
  panel.innerHTML = `<nav class="days" aria-label="${l.kind === 'daily' ? '날짜' : '라운드'} 선택">${l.rounds.map(chip).join('')}</nav>
    <p class="sub">기록 마감 ${dateTime(rnd.close)}${started ? (open ? '' : ' · 마감됨') : ' · 시작 전'}${tip('deadline')}</p>
    ${s.mismatch ? `<p class="notice"><strong>확인이 필요해요. WOD 방식이 서로 달라서 점수를 잠시 보류했어요.</strong>${tip('mismatch')}<br>${Object.entries(s.counts).map(([t, n]) => `${typeNames[t]} ${n}명`).join(' · ')}<br>방식이 하나로 맞춰지면 바로 다시 계산돼요. 기록을 올린 분은 마감 뒤에도 본인 기록을 고칠 수 있어요.</p>` : ''}
    <section><h2>WOD</h2><p class="guide">${e(rnd.guide.text) || '<span class="sub">아직 WOD가 올라오지 않았어요.</span>'}</p>${rnd.guide.photo ? `<img class="photo" src="/photo/${e(rnd.guide.photo)}" alt="WOD 사진">` : ''}
      ${started ? `<details><summary>WOD 편집</summary><form id="guide"><label>WOD 내용<textarea name="text" maxlength="4000">${e(rnd.guide.text)}</textarea></label><label>사진 (PNG/JPEG, 2MB 이하)<input name="photo" type="file" accept="image/png,image/jpeg"></label>${rnd.guide.photo ? '<label><input name="remove" type="checkbox">기존 사진 삭제</label>' : ''}<button class="secondary">WOD 저장</button></form></details>` : ''}</section>
    ${!direct ? '' : l.kind === 'daily' && !own
      ? '<section><h2>내 기록</h2><p class="sub">출석을 누르면 기록을 적을 수 있어요. 출석만 해도 점수가 들어가요.</p><button data-attend>출석</button></section>'
      : `<section><h2>내 기록${own?.division === 'attendance' ? ' · 출석 완료' : ''}</h2><form id="record">${recordFields(l.kind === 'daily', own)}<button>${own?.type ? '기록 수정' : '기록 저장'}</button></form></section>`}
    <section><h2>회차 기록${tip('points')}</h2><table><thead><tr><th>회원</th><th>기록</th><th>점수</th></tr></thead><tbody>${s.order.map(row).join('')}</tbody></table>
      ${open && !s.mismatch ? `<p class="sub">마감 전이라 점수는 바뀔 수 있고, 아직 참여하지 않은 회원은 '집계 전'이에요.${tip('pending')}</p>` : ''}
      <p class="sub">참여하지 않으면 ${open ? '마감 뒤에 ' : ''}${l.members.length}점이에요.${tip('absent')}</p>
      ${started && state.now < l.requestClose && targets.length ? `<details><summary>기록 수정 요청하기</summary><form id="request">
        <p class="sub">내 기록 정정과 지연 제출, 다른 회원 기록에 대한 이의 제기를 여기서 보내요.${tip('request')}</p>
        <label>누구 기록인가요?<select name="target">${options(targets.map(u => [u, u === me ? `${names[u]} (본인)` : names[u]]))}</select></label>
        ${choices('어떤 요청인가요?', 'invalidate', [['no','기록을 고쳐 주세요'], ['yes','잘못 올라온 기록이에요 (기록 삭제 요청)']], 'no', 'invalidate')}
        <div class="proposal"><p class="sub">이렇게 고치면 좋겠다 싶은 기록을 적어 주세요.</p>${recordFields(l.kind === 'daily')}</div>
        <label>이유<textarea name="reason" required maxlength="1000"></textarea></label>
        <label>확인해 줄 회원 (판정자)<select name="judge"></select></label><p class="sub">판정자가 요청을 확인하고 결정해요.${tip('judge')}</p><button>요청 보내기</button></form></details>` : ''}</section>`;
  panel.querySelector('.days .active').scrollIntoView({inline:'center', block:'nearest'});
  const fields = ['division', 'type', 'finished'], rec = $('#record'), req = $('#request');
  if (rec) {
    syncFields(rec, own);
    rec.addEventListener('change', ev => { if (fields.includes(ev.target.name)) syncFields(rec, ev.target.name === 'type' ? {} : undefined); });
  }
  if (req) {
    syncRequest(req, l, rnd);
    req.addEventListener('change', ev => { if (ev.target.name === 'target') syncRequest(req, l, rnd); else if (ev.target.name === 'invalidate') req.querySelector('.proposal').hidden = ev.target.value === 'yes'; else if (fields.includes(ev.target.name)) syncFields(req, ev.target.name === 'type' ? {} : undefined); });
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
  f.elements.judge.innerHTML = eligible.length ? options(eligible.map(u => [u, names[u]])) : '<option value="">없음 (2명 리그)</option>';
}

function rankingPanel(l, panel) {
  panel.innerHTML = `<section><table><thead><tr><th>순위</th><th>회원</th><th>누적 점수</th></tr></thead><tbody>${l.standings.map(r => `<tr class="${r.user === state.actor ? 'me' : ''}"><td class="num">${r.rank}</td><td>${name(r.user)}</td><td>${r.points}</td></tr>`).join('')}</tbody></table>
    <p class="sub">점수가 낮을수록 순위가 높아요. ${l.rounds.length}회차 중 ${l.rounds.filter(r => r.counted).length}회차가 반영됐어요. 아직 마감하지 않은 회차와 WOD 방식이 엇갈린 회차는 빠져 있어요.${tip('total')}</p></section>`;
}

function requestsPanel(l, panel) {
  const item = q => `<section><p><b>${parties(q)}</b> <span class="sub">${e(l.rounds.find(r => r.id === q.round).label)} · ${statusNames[q.status]}</span></p>
    <p>${e(q.reason)}</p><p class="sub">요청: ${e(q.proposal ? describe(q.proposal) : '기록 삭제')} · 판정자: ${q.judge ? name(q.judge) : '없음 (2명 리그)'}</p>
    ${q.reply ? `<p class="sub">본인 답변: ${e(q.reply)}</p>` : ''}${q.decisionReason ? `<p class="sub">판정 이유: ${e(q.decisionReason)}</p>` : ''}${q.decision ? `<p class="ok">${e(q.decision)}</p>` : ''}
    ${waiting(q, state.actor) ? `<form class="resolve" data-request="${q.id}" data-mode="${q.status}">${q.status === 'author'
      ? '<label>한마디 (거절할 때는 이유를 꼭 적어 주세요)<textarea name="reason" maxlength="1000"></textarea></label><div class="row"><button name="choice" value="accept">수락하고 반영</button><button name="choice" value="reject" class="secondary">거절</button></div>'
      : choices('판정', 'decision', [['keep','기존 기록 유지'], ['modify','요청대로 반영'], ['invalidate','기록 삭제']], null) + '<label>이유<textarea name="reason" required maxlength="1000"></textarea></label><p class="sub">판정을 하나 골라야 완료할 수 있어요.</p><button disabled>판정 완료</button>'}</form>` : ''}</section>`;
  panel.innerHTML = `<p class="sub">다른 회원 기록에 대한 요청은 본인이 수락하면 바로 반영돼요. 생각이 다르면 판정자가 정해 줘요. 마감 뒤 내 기록 수정이나 늦은 제출도 판정자가 확인해 줘요.${tip('request')}</p>${l.requests.slice().reverse().map(item).join('') || '<section>아직 요청이 없어요.</section>'}`;
}

function historyPanel(l, panel) {
  const label = id => e(l.rounds.find(r => r.id === id)?.label ?? '');
  const rec = r => e(r ? describe(r) : '없음');
  const detail = h => h.request ? `${label(h.request.round)} · ${parties(h.request)}${h.request.decision ? ' · ' + e(h.request.decision) : ''}`
    : h.target ? `${label(h.round)} · ${name(h.target)} · ${rec(h.before)} → ${rec(h.after)}`
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
    : daily ? `운동하는 날 ${picked.size}일 · 요일이나 날짜를 눌러 넣고 뺄 수 있어요.${Object.keys(holidays).length ? ' 공휴일은 미리 빼 두었어요.' : ''}`
    : `${monthDay(pick.start)} ~ ${monthDay(end)} · ${duration}일씩 ${count}라운드가 쉬는 날 없이 이어져요.`;
  // 누를 수 있는 날: 시작일 고르기는 내일부터, 종료일 고르기는 시작일부터 1년, 그 밖에는 기간 안의 날짜
  const can = v => pick.mode === 'start' ? v >= tomorrow : pick.mode === 'end' ? v >= pick.start && v <= addDays(pick.start, 366) : daily && v >= pick.start && v <= end;
  // 공휴일 표가 끝나는 해보다 뒤까지 걸친 기간이면, 표에 없는 해를 알려 줍니다(표를 못 읽었으면 기간의 모든 해).
  const known = Math.max(0, ...Object.keys(holidays).map(v => +v.slice(0, 4))), missing = [];
  for (let y = Math.max(+pick.start.slice(0, 4), known + 1); y <= +end.slice(0, 4); y++) missing.push(y);
  $('#holidayNote').hidden = !daily || !missing.length;
  $('#holidayNote').textContent = `${missing.join('·')}년 공휴일은 아직 반영되지 않았어요. 쉬는 날은 직접 빼 주세요.`;
  const tone = (w, v) => w === 0 || holidays[v] ? 'red' : w === 6 ? 'blue' : '';
  const m = utc(pick.month);
  let html = `<div class="calnav"><button type="button" class="secondary" data-month="-1" aria-label="이전 달">‹</button><b>${m.getUTCFullYear()}년 ${m.getUTCMonth() + 1}월</b><button type="button" class="secondary" data-month="1" aria-label="다음 달">›</button></div>
    <div class="cal head">${weekdayNames.map((t, w) => daily ? `<button type="button" class="${tone(w)}" data-weekday="${w}" aria-pressed="${weekdaysOn.has(w)}">${t}</button>` : `<span class="${tone(w)}">${t}</span>`).join('')}</div>
    <div class="cal">${'<span></span>'.repeat(m.getUTCDay())}`;
  for (let d = new Date(m); d.getUTCMonth() === m.getUTCMonth(); d.setUTCDate(d.getUTCDate() + 1)) {
    const v = d.toISOString().slice(0, 10), inside = v >= pick.start && v <= end;
    const attrs = `class="${tone(d.getUTCDay(), v)}"${holidays[v] ? ` title="${e(holidays[v])}"` : ''}`;
    html += can(v) ? `<button type="button" ${attrs} data-date="${v}" aria-pressed="${daily ? picked.has(v) : inside}">${d.getUTCDate()}</button>` : `<span ${attrs}${!daily && inside ? ' data-in' : ''}>${d.getUTCDate()}</span>`;
  }
  $('#dates').innerHTML = html + '</div>';
}

document.addEventListener('click', async ev => {
  // ⓘ 설명은 하나만 열어 둡니다. 다른 곳을 누르면 닫습니다.
  const t = ev.target.closest('.tip');
  document.querySelectorAll('.tip[aria-expanded=true]').forEach(x => x === t || toggleTip(x, false));
  if (t) return toggleTip(t, t.getAttribute('aria-expanded') !== 'true');
  const b = ev.target.closest('button');
  if (!b) return;
  try {
    if (b.hasAttribute('data-logout')) { await api('/api/logout', {}); selected = null; roundId = null; tab = 'ranking'; setKeyPending(false); needNick = false; await load(); }
    else if (b.hasAttribute('data-copy')) {
      try { await navigator.clipboard.writeText(recoveryKey); notify('복구 키를 복사했어요. 안전한 곳에 붙여 넣어 주세요.'); }
      catch { prompt('이 복구 키를 복사해서 안전한 곳에 저장해 주세요.', recoveryKey); }
    }
    else if (b.hasAttribute('data-help')) { help = !help; render(); scrollTo(0, 0); }
    else if (b.hasAttribute('data-copy-mail')) {
      try { await navigator.clipboard.writeText(CONTACT); notify('메일 주소를 복사했어요.'); }
      catch {
        // 클립보드를 못 쓰는 브라우저에서는 화면의 주소를 선택해 두어 직접 복사할 수 있게 합니다.
        getSelection().selectAllChildren(b.closest('details').querySelector('.addr'));
        notify('복사하지 못했어요. 선택된 주소를 길게 눌러 복사해 주세요.');
      }
    }
    else if (b.hasAttribute('data-continue')) { setKeyPending(false); await load(); }
    else if (b.hasAttribute('data-skip')) { leaveInvite(); render(); }
    else if (b.dataset.auth) { authMode = b.dataset.auth; render(); }
    else if (b.hasAttribute('data-back')) { selected = null; inviteOpen = false; await load(); }
    else if (b.hasAttribute('data-new')) { selected = 'new'; render(); }
    else if (b.dataset.open) { selected = b.dataset.open; roundId = null; tab = 'ranking'; inviteOpen = false; await load(); }
    else if (b.hasAttribute('data-invite')) { inviteOpen = !inviteOpen; render(); }
    else if (b.hasAttribute('data-copy-link')) {
      const url = $('.invite input').value;
      try { await navigator.clipboard.writeText(url); notify('초대 링크를 복사했어요. 단톡방에 붙여 넣어 주세요.'); }
      catch { prompt('이 링크를 복사해서 친구에게 보내 주세요.', url); }
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
    else if (b.dataset.tab) { tab = b.dataset.tab; await load(); }
  } catch (err) { notify(err.message); }
});

document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') document.querySelectorAll('.tip[aria-expanded=true]').forEach(x => toggleTip(x, false));
});

document.addEventListener('change', ev => {
  if (ev.target.id === 'saved') $('[data-continue]').disabled = !ev.target.checked;
  if (ev.target.name === 'decision') ev.target.form.querySelector('button').disabled = false;
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
      // 폼에 없는 칸은 undefined라 요청에서 빠집니다.
      const v = n => f.elements[n]?.value;
      const entered = await api('/api/' + f.id, {login:v('login'), name:v('name'), key:v('key'), pin:v('pin')});
      recoveryKey = entered.recoveryKey || null;
      if (recoveryKey) setKeyPending(true);
      authMode = 'login';
      await load();
      return;
    }
    if (f.id === 'renew') {
      recoveryKey = (await api('/api/recovery-key', {pin:f.elements.pin.value})).recoveryKey;
      setKeyPending(true);
      render();
      return;
    }
    if (f.id === 'nick') { await enterInvited(f.elements.name.value); render(); return; }
    if (f.id === 'create') Object.assign(data, {action:'create', name:f.elements.name.value, kind:f.elements.kind.value, start:pick.start, end:pick.end, dates:f.elements.kind.value === 'daily' ? pickedDates() : [], count:Number(f.elements.count.value), duration:Number(f.elements.duration.value)});
    else if (f.id === 'record') Object.assign(data, {action:'record', record:readRecord(f), version:rnd.versions[state.actor] || 0});
    else if (f.id === 'guide') {
      // 사진을 바꾸지 않으면 지금 사진의 id를 그대로 보냅니다.
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

fetch('/holidays.json').then(r => r.ok ? r.json() : {}).then(h => { if (h && typeof h === 'object') holidays = h; }).catch(() => {})
  .then(load).catch(err => notify('불러오지 못했어요. ' + err.message));
