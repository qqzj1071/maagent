'use strict';

const API = '/api/v1';
const TOKEN_KEY = 'maagent_token';
const EMAIL_KEY = 'maagent_email';

let token = localStorage.getItem(TOKEN_KEY) || '';
let account = { email: localStorage.getItem(EMAIL_KEY) || '' };
let statusData = {};
let reports = [];
let logLines = [];
let es = null;
let codeTimer = null;

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

function toast(message) {
  const node = $('toast');
  node.textContent = message;
  node.classList.remove('hidden');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => node.classList.add('hidden'), 2600);
}

async function api(path, { method = 'GET', body = null, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && token) headers['Authorization'] = 'Bearer ' + token;
  let response;
  try {
    response = await fetch(API + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new Error('无法连接服务器');
  }
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (e) {
    data = {};
  }
  if (!response.ok) {
    if (response.status === 401 && auth) logout(false);
    throw new Error(data.message || '请求失败（' + response.status + '）');
  }
  return data;
}

function showAuth() {
  $('authView').classList.remove('hidden');
  $('mainView').classList.add('hidden');
}

function accountName() {
  return account.username || account.email || '';
}

function showMain() {
  $('authView').classList.add('hidden');
  $('mainView').classList.remove('hidden');
  $('whoami').textContent = accountName();
}

function onLoggedIn(data) {
  token = data.token || '';
  account = data.account || {};
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(EMAIL_KEY, accountName());
  showMain();
  refreshAll();
  connectEvents();
}

async function logout(callApi = true) {
  if (callApi && token) {
    try {
      await api('/auth/logout', { method: 'POST' });
    } catch (e) {
      /* ignore */
    }
  }
  token = '';
  account = {};
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
  disconnectEvents();
  showAuth();
}

/* ---------------------------------------------------------------- auth UI */
document.querySelectorAll('[data-auth]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-auth]').forEach((b) => b.classList.toggle('active', b === btn));
    const isLogin = btn.dataset.auth === 'login';
    $('loginForm').classList.toggle('hidden', !isLogin);
    $('registerForm').classList.toggle('hidden', isLogin);
  });
});

document.querySelectorAll('.toggle-pw').forEach((btn) => {
  btn.addEventListener('click', () => {
    const input = $(btn.dataset.target);
    if (!input) return;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.textContent = show ? '隐藏' : '显示';
  });
});

$('loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const msg = $('loginMsg');
  msg.textContent = '';
  msg.className = 'msg';
  try {
    const data = await api('/auth/login', {
      method: 'POST',
      auth: false,
      body: { account: $('loginAccount').value.trim(), password: $('loginPassword').value },
    });
    onLoggedIn(data);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = 'msg error';
  }
});

$('registerForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const msg = $('regMsg');
  msg.textContent = '';
  msg.className = 'msg';
  try {
    const data = await api('/auth/register', {
      method: 'POST',
      auth: false,
      body: {
        email: $('regEmail').value.trim(),
        username: $('regUsername').value.trim(),
        phone: $('regPhone').value.trim() || null,
        password: $('regPassword').value,
        code: $('regCode').value.trim(),
      },
    });
    onLoggedIn(data);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = 'msg error';
  }
});

$('sendCodeBtn').addEventListener('click', async () => {
  const email = $('regEmail').value.trim();
  const msg = $('regMsg');
  msg.textContent = '';
  msg.className = 'msg';
  if (!email) {
    msg.textContent = '请先填写邮箱';
    msg.className = 'msg error';
    return;
  }
  try {
    await api('/auth/email/code', {
      method: 'POST',
      auth: false,
      body: { email, purpose: 'register' },
    });
    msg.textContent = '验证码已发送，请查收邮箱';
    msg.className = 'msg ok';
    let left = 60;
    const btn = $('sendCodeBtn');
    btn.disabled = true;
    btn.textContent = left + 's';
    clearInterval(codeTimer);
    codeTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(codeTimer);
        btn.disabled = false;
        btn.textContent = '发送验证码';
      } else {
        btn.textContent = left + 's';
      }
    }, 1000);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = 'msg error';
  }
});

$('logoutBtn').addEventListener('click', () => logout(true));

/* ------------------------------------------------------------- main tabs */
document.querySelectorAll('[data-tab]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('active', b === btn));
    ['status', 'logs', 'reports'].forEach((name) => {
      $('tab-' + name).classList.toggle('hidden', name !== btn.dataset.tab);
    });
  });
});

/* ---------------------------------------------------------------- status */
function renderStatus() {
  const data = statusData || {};
  const running = !!data.running;
  const stopping = data.status === 'stopping';
  $('statusDot').className = 'dot' + (running ? (stopping ? ' stopping' : ' running') : '');
  $('statusText').textContent = stopping ? '停止中' : running ? '运行中' : '空闲';
  $('statusMessage').textContent = data.message || '';

  const kv = $('statusKv');
  kv.innerHTML = '';
  const rows = [];
  if (data.current) rows.push(['当前任务', data.current + (data.total ? '（' + data.index + '/' + data.total + '）' : '')]);
  if (data.started_at) rows.push(['开始', data.started_at]);
  if (data.finished_at) rows.push(['结束', data.finished_at]);
  if (data.next_run) rows.push(['下次定时', data.next_run]);
  rows.forEach(([key, value]) => {
    const row = el('div');
    row.appendChild(el('dt', null, key));
    row.appendChild(el('dd', null, value));
    kv.appendChild(row);
  });

  $('startBtn').disabled = running;
  $('stopBtn').disabled = !running;

  const list = $('softwareList');
  list.innerHTML = '';
  const software = data.software || [];
  if (!software.length) {
    list.appendChild(el('div', 'muted', '没有配置任务'));
  }
  software.forEach((item) => {
    const node = el('div', 'item');
    node.appendChild(el('div', 'line1', (item.name || item.software) + (item.enabled ? '' : '（已停用）')));
    list.appendChild(node);
  });
}

function scheduleStatusRefreshes() {
  [1200, 3000, 6000, 12000].forEach((delay) => setTimeout(refreshStatus, delay));
}

$('startBtn').addEventListener('click', async () => {
  try {
    const data = await api('/workflow/start', { method: 'POST', body: {} });
    if (data.status) {
      statusData = data.status;
      renderStatus();
    }
    scheduleStatusRefreshes();
  } catch (err) {
    toast(err.message);
  }
});

$('stopBtn').addEventListener('click', async () => {
  try {
    const data = await api('/workflow/stop', { method: 'POST' });
    if (data.status) {
      statusData = data.status;
      renderStatus();
    }
    scheduleStatusRefreshes();
  } catch (err) {
    toast(err.message);
  }
});

/* ------------------------------------------------------------------ logs */
function setLogs(lines) {
  logLines = lines.slice(-500);
  const view = $('logView');
  view.textContent = logLines.join('\n');
  view.scrollTop = view.scrollHeight;
}

function appendLog(entry) {
  const line = (entry.time ? entry.time + '  ' : '') + (entry.message || '');
  logLines.push(line);
  if (logLines.length > 500) logLines.shift();
  const view = $('logView');
  view.textContent = logLines.join('\n');
  view.scrollTop = view.scrollHeight;
}

/* --------------------------------------------------------------- reports */
function renderReports() {
  const list = $('reportList');
  list.innerHTML = '';
  if (!reports.length) {
    list.appendChild(el('div', 'muted', '暂无报告'));
    return;
  }
  reports.forEach((report) => {
    const node = el('div', 'item');
    node.appendChild(el('div', 'line1', (report.game || '') + '  ' + (report.status_label || '')));
    node.appendChild(
      el('div', 'line2', (report.started_at || '') + ' ~ ' + (report.finished_at || '') + '  ' + (report.duration || ''))
    );
    node.addEventListener('click', () => openReport(report.id));
    list.appendChild(node);
  });
}

async function openReport(id) {
  try {
    const data = await api('/reports/detail?id=' + encodeURIComponent(id));
    const report = data.report || {};
    $('modalTitle').textContent = (report.game || '') + ' ' + (report.status_label || '');
    const body = $('modalBody');
    body.innerHTML = '';
    body.appendChild(el('pre', null, report.text || ''));
    $('modal').classList.remove('hidden');
  } catch (err) {
    toast(err.message);
  }
}

$('clearReportsBtn').addEventListener('click', async () => {
  if (!reports.length) {
    toast('暂无报告');
    return;
  }
  if (!window.confirm('确定清空全部任务报告吗？')) return;
  try {
    await api('/reports/clear', { method: 'POST' });
    reports = [];
    renderReports();
    toast('报告已清空');
  } catch (err) {
    toast(err.message);
  }
});

$('modalClose').addEventListener('click', () => $('modal').classList.add('hidden'));
$('modal').addEventListener('click', (event) => {
  if (event.target === $('modal')) $('modal').classList.add('hidden');
});

/* ------------------------------------------------------------- lifecycle */
async function refreshStatus() {
  try {
    const wasRunning = !!statusData.running;
    const data = await api('/status');
    statusData = data.status || {};
    renderStatus();
    if (wasRunning && !statusData.running) refreshAll();
  } catch (e) {
    /* ignore */
  }
}

async function refreshAll() {
  try {
    const status = await api('/status');
    statusData = status.status || {};
    renderStatus();

    const logs = await api('/logs?limit=200');
    setLogs((logs.lines || []).map((e) => (e.time ? e.time + '  ' : '') + (e.message || '')));

    const list = await api('/reports?limit=30');
    reports = list.reports || [];
    renderReports();
  } catch (err) {
    toast(err.message);
  }
}

function connectEvents() {
  disconnectEvents();
  if (!token) return;
  try {
    es = new EventSource(API + '/events?token=' + encodeURIComponent(token));
    es.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      const data = payload.data;
      if (payload.type === 'status' || payload.type === 'snapshot') {
        statusData = Object.assign({}, statusData, data || {});
        renderStatus();
      } else if (payload.type === 'log') {
        appendLog(data || {});
      } else if (payload.type === 'report') {
        reports.unshift(data || {});
        if (reports.length > 30) reports.pop();
        renderReports();
        toast('任务报告已生成 ' + ((data && data.status_label) || ''));
      } else if (payload.type === 'reports_cleared') {
        reports = [];
        renderReports();
      }
    };
  } catch (e) {
    /* ignore */
  }
}

function disconnectEvents() {
  if (es) {
    es.close();
    es = null;
  }
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && token) refreshAll();
});

async function init() {
  if (token) {
    try {
      const me = await api('/account/me');
      account = me.account || account;
      showMain();
      await refreshAll();
      connectEvents();
      return;
    } catch (e) {
      token = '';
      localStorage.removeItem(TOKEN_KEY);
    }
  }
  showAuth();
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

setInterval(() => {
  if (token && !document.hidden) refreshStatus();
}, 10000);

init();
