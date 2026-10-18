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
let session = 0;
let statusReady = false;
let actionPending = false;
let activeTab = 'status';
let lastEventAt = 0;
let statusRequest = null;
let panelRequest = null;
let logRenderTimer = null;
let statusRevision = 0;
let panelRevision = 0;
const panelLoaded = { logs: false, reports: false };

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
  const requestToken = token;
  const headers = { 'Content-Type': 'application/json' };
  if (auth && token) headers['Authorization'] = 'Bearer ' + token;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  let response;
  let data;
  try {
    response = await fetch(API + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      cache: 'no-store',
    });
    data = await response.json();
  } catch (e) {
    throw new Error(controller.signal.aborted ? '连接超时，请检查电脑和网络后重试' : '无法连接服务器，请检查电脑和网络');
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    if (response.status === 401 && auth && token === requestToken) logout(false);
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
  session += 1;
  statusReady = false;
  token = data.token || '';
  account = data.account || {};
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(EMAIL_KEY, accountName());
  showMain();
  refreshStatus();
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
  session += 1;
  statusReady = false;
  statusData = {};
  actionPending = false;
  statusRequest = null;
  panelRequest = null;
  panelLoaded.logs = panelLoaded.reports = false;
  reports = [];
  setLogs([]);
  renderReports();
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
    btn.classList.toggle('showing', show);
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
    activeTab = btn.dataset.tab;
    if (activeTab === 'logs') renderLogs();
    if (activeTab === 'reports') renderReports();
    refreshPanel();
  });
});

/* ---------------------------------------------------------------- status */
function renderStatus() {
  const data = statusData || {};
  const running = !!data.running;
  const stopping = data.status === 'stopping';
  $('statusDot').className = 'dot' + (running ? (stopping ? ' stopping' : ' running') : '');
  $('statusText').textContent = !statusReady ? '等待同步' : stopping ? '停止中' : running ? '运行中' : '空闲';
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

  const unavailable = !statusReady || actionPending || !navigator.onLine;
  $('startBtn').disabled = unavailable || running || !(data.software || []).some((item) => item.enabled);
  $('stopBtn').disabled = unavailable || !running || stopping;

  const list = $('softwareList');
  list.innerHTML = '';
  const software = data.software || [];
  if (!software.length) {
    list.appendChild(el('div', 'muted', '没有配置任务'));
  }
  software.forEach((item) => {
    const node = el('div', 'item task-item');
    const info = el('div');
    const canStart = item.can_start == null ? item.enabled : item.can_start;
    info.appendChild(el('div', 'line1', item.name || item.software));
    info.appendChild(el('div', 'line2', !canStart ? '软件已停用，请在电脑端启用' : item.enabled ? '已加入日常序列' : '仅单独运行 · 未加入日常序列'));
    node.appendChild(info);
    const button = el('button', 'task-start small', running && data.current === item.software ? '运行中' : '单独启动');
    button.type = 'button';
    button.disabled = unavailable || running || !canStart;
    button.setAttribute('aria-label', '单独启动 ' + (item.name || item.software));
    button.addEventListener('click', () => runAction('/workflow/start', { software: [item.software] }));
    node.appendChild(button);
    list.appendChild(node);
  });
}

async function runAction(path, body = null) {
  if (actionPending || !statusReady) return;
  const currentSession = session;
  actionPending = true;
  statusRevision += 1;
  renderStatus();
  try {
    const revision = statusRevision;
    const data = await api(path, { method: 'POST', body });
    if (currentSession !== session) return;
    if (data.status && revision === statusRevision) {
      statusData = data.status;
      renderStatus();
    }
  } catch (err) {
    if (currentSession === session) {
      statusReady = false;
      toast(err.message + '；正在核实任务状态，请勿重复启动');
    }
  } finally {
    if (currentSession === session) {
      actionPending = false;
      renderStatus();
      refreshStatus();
    }
  }
}

$('startBtn').addEventListener('click', () => runAction('/workflow/start', {}));
$('stopBtn').addEventListener('click', () => runAction('/workflow/stop'));

/* ------------------------------------------------------------------ logs */
function setLogs(lines) {
  logLines = lines.slice(-500);
  queueLogRender();
}

function renderLogs() {
  const view = $('logView');
  const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 60;
  view.textContent = logLines.join('\n');
  if (atBottom) view.scrollTop = view.scrollHeight;
}

function queueLogRender() {
  if (activeTab !== 'logs' || document.hidden || logRenderTimer) return;
  logRenderTimer = setTimeout(() => {
    logRenderTimer = null;
    renderLogs();
  }, 150);
}

function appendLog(entry) {
  const line = (entry.time ? entry.time + '  ' : '') + (entry.message || '');
  logLines.push(line);
  if (logLines.length > 500) logLines.shift();
  queueLogRender();
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
function connection(message) {
  $('connectionText').textContent = message;
}

function refreshStatus() {
  if (!token || document.hidden || actionPending) return Promise.resolve();
  if (statusRequest) return statusRequest;
  const currentSession = session;
  const revision = statusRevision;
  const request = (async () => {
    try {
      const data = await api('/status');
      if (currentSession !== session || revision !== statusRevision) return;
      statusData = data.status || {};
      statusReady = true;
      connection(es && es.readyState === 1 ? '已连接 · 实时同步' : '已连接 · 定时同步');
      renderStatus();
    } catch (err) {
      if (currentSession !== session || revision !== statusRevision) return;
      statusReady = false;
      connection(err.message + ' · 将自动重试');
      renderStatus();
    }
  })();
  statusRequest = request;
  request.finally(() => { if (statusRequest === request) statusRequest = null; });
  return request;
}

function refreshPanel(force = false) {
  const tab = activeTab;
  if (!token || document.hidden || tab === 'status') return Promise.resolve();
  if (panelRequest) return panelRequest;
  if (panelLoaded[tab] && !force) return Promise.resolve();
  const currentSession = session;
  const revision = panelRevision;
  const request = (async () => {
    try {
      const data = await api(tab === 'logs' ? '/logs?limit=200' : '/reports?limit=30');
      if (currentSession !== session) return;
      // The fetched snapshot can precede a live report/clear event.
      if (tab === 'reports' && revision !== panelRevision) return;
      if (tab === 'logs') {
        setLogs((data.lines || []).map((entry) => (entry.time ? entry.time + '  ' : '') + (entry.message || '')));
      } else {
        reports = data.reports || [];
        renderReports();
      }
      panelLoaded[tab] = true;
    } catch (err) {
      if (currentSession === session) toast(err.message);
    }
  })();
  panelRequest = request;
  request.finally(() => {
    if (panelRequest === request) {
      panelRequest = null;
      if (activeTab !== tab) refreshPanel();
    }
  });
  return request;
}

function connectEvents() {
  disconnectEvents();
  if (!token || document.hidden || !navigator.onLine) return;
  const currentSession = session;
  try {
    const source = new EventSource(API + '/events?token=' + encodeURIComponent(token));
    es = source;
    lastEventAt = Date.now();
    source.onmessage = (event) => {
      if (source !== es || currentSession !== session) return;
      lastEventAt = Date.now();
      let payload;
      try { payload = JSON.parse(event.data); } catch (e) { return; }
      const data = payload.data;
      if (payload.type === 'status' || payload.type === 'snapshot') {
        statusRevision += 1;
        statusData = Object.assign({}, statusData, data || {});
        statusReady = true;
        connection('已连接 · 实时同步');
        renderStatus();
        if (payload.type === 'snapshot') {
          panelLoaded.logs = panelLoaded.reports = false;
          refreshPanel(true);
        }
      } else if (payload.type === 'log') {
        appendLog(data || {});
      } else if (payload.type === 'report') {
        panelRevision += 1;
        reports = [data, ...reports.filter((report) => report.id !== data.id)].slice(0, 30);
        if (activeTab === 'reports') renderReports();
        toast('任务报告已生成 ' + (data.status_label || ''));
      } else if (payload.type === 'reports_cleared') {
        panelRevision += 1;
        reports = [];
        renderReports();
      }
    };
    source.onerror = () => {
      if (source !== es) return;
      connection('实时连接中断，正在重连…');
      refreshStatus();
    };
  } catch (e) {
    connection('正在使用定时同步');
  }
}

function disconnectEvents() {
  if (es) {
    es.close();
    es = null;
  }
}

function resume() {
  if (!token || document.hidden) return;
  connection('正在连接电脑…');
  refreshStatus();
  refreshPanel(true);
  connectEvents();
}

$('retryBtn').addEventListener('click', resume);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) disconnectEvents();
  else resume();
});
window.addEventListener('online', resume);
window.addEventListener('offline', () => {
  disconnectEvents();
  statusReady = false;
  connection('手机网络已断开，恢复后自动连接');
  renderStatus();
});
window.addEventListener('pagehide', disconnectEvents);
window.addEventListener('pageshow', (event) => { if (event.persisted) resume(); });

async function init() {
  if (!token) { showAuth(); return; }
  const currentSession = session;
  // Show the cached shell immediately. Transient network failures keep the login.
  showMain();
  renderStatus();
  resume();
  try {
    const me = await api('/account/me');
    if (currentSession !== session) return;
    account = me.account || account;
    $('whoami').textContent = accountName();
  } catch (err) {
    if (currentSession === session && !statusReady) connection(err.message + ' · 将自动重试');
  }
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' }).catch(() => {});
}

setInterval(() => {
  if (!token || document.hidden || !navigator.onLine) return;
  if (!es || Date.now() - lastEventAt > 45000) connectEvents();
  refreshStatus();
  if (!es || es.readyState !== 1) refreshPanel(true);
}, 10000);

init();
