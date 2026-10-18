// Dependency-free browser logic tests with controlled DOM, network and clocks.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../maagent/server/web/app.js'), 'utf8');

function harness(savedToken = '') {
  const nodes = new Map();
  function node() {
    return { textContent: '', innerHTML: '', children: [], disabled: false,
      classList: { add() {}, remove() {}, toggle() {} },
      listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; },
      setAttribute() {}, appendChild(child) { this.children.push(child); },
      scrollHeight: 0, scrollTop: 0, clientHeight: 0 };
  }
  const requests = [], timers = new Map(), intervals = [], streams = [];
  let timerId = 0;
  const storage = new Map([['maagent_token', savedToken]]);
  const document = { hidden: false, listeners: {},
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
    createElement: node, querySelectorAll: () => [],
    addEventListener(name, fn) { this.listeners[name] = fn; } };
  const window = { listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; } };
  const context = vm.createContext({ document, window, navigator: { onLine: true },
    localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    AbortController, console, Date,
    setTimeout(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); }, setInterval(fn) { intervals.push(fn); }, clearInterval() {},
    fetch(url, options) { return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('abort')));
      requests.push({ url, options, resolve: (data, status = 200) => resolve({ ok: status < 400, status, json: async () => data }), reject });
    }); },
    EventSource: class {
      constructor(url) { this.url = url; this.readyState = 0; streams.push(this); }
      close() { this.closed = true; }
    },
  });
  vm.runInContext(source, context);
  return { nodes, requests, timers, intervals, streams, storage, document, window,
    run: (code) => vm.runInContext(code, context) };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
const ready = "token = 'test'; statusReady = true; statusData = {software:[{software:'maa',enabled:true,can_start:true}]};";

test('initial load does not fetch hidden logs/reports and retains token on network failure', async () => {
  const h = harness('saved');
  assert.deepEqual(h.requests.map((r) => r.url), ['/api/v1/status', '/api/v1/account/me']);
  h.requests.forEach((r) => r.reject(new Error('offline')));
  await flush();
  assert.equal(h.storage.get('maagent_token'), 'saved');
  assert.equal(h.run('statusReady'), false);
});

test('unauthorized response clears session', async () => {
  const h = harness('expired');
  h.requests[0].resolve({message:'expired'}, 401);
  await flush();
  assert.equal(h.storage.has('maagent_token'), false);
  assert.equal(h.streams[0].closed, true);
});

test('concurrent refreshes share one request and stalled requests time out', async () => {
  const h = harness();
  h.run(ready);
  const first = h.run('refreshStatus()');
  assert.equal(h.run('refreshStatus()'), first);
  assert.equal(h.requests.length, 1);
  [...h.timers.values()].find((timer) => timer.delay === 15000).fn();
  await first;
  assert.equal(h.run('statusReady'), false);
  assert.equal(h.run('token'), 'test');
});

test('single start sends one software, chain sends no selection, double click is blocked', async () => {
  const h = harness();
  h.run(ready);
  const first = h.run("runAction('/workflow/start', {software:['maa']})");
  h.run("runAction('/workflow/start', {software:['bgi']})");
  assert.equal(h.requests.length, 1);
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {software:['maa']});
  h.requests[0].resolve({status:{running:true}});
  await first;
  h.requests[1].resolve({status:{running:false}});
  await flush();
  h.run("runAction('/workflow/start', {})");
  assert.deepEqual(JSON.parse(h.requests[2].options.body), {});
});

test('task button permits standalone software outside chain', () => {
  const h = harness();
  h.run("token='test'; statusReady=true; statusData={software:[{software:'maa', enabled:false, can_start:true}]}; renderStatus();");
  assert.equal(h.nodes.get('startBtn').disabled, true);
  const button = h.nodes.get('softwareList').children[0].children[1];
  assert.equal(button.disabled, false);
  button.listeners.click();
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {software:['maa']});
});

test('logs batch rendering and do not redraw a hidden panel', () => {
  const h = harness();
  h.run("for(let i=0;i<100;i++) appendLog({message:'line'});");
  assert.equal(h.timers.size, 0);
  h.run("activeTab='logs'; for(let i=0;i<100;i++) appendLog({message:'line'});");
  assert.equal([...h.timers.values()].filter((timer) => timer.delay === 150).length, 1);
});

test('background closes live connection; foreground reconnects', () => {
  const h = harness('saved');
  h.document.hidden = true;
  h.document.listeners.visibilitychange();
  assert.equal(h.streams[0].closed, true);
  h.document.hidden = false;
  h.document.listeners.visibilitychange();
  assert.equal(h.streams.length, 2);
});

test('late status response cannot overwrite newer stream state', async () => {
  const h = harness('saved');
  h.streams[0].onmessage({data:JSON.stringify({type:'status',data:{running:true}})});
  h.requests[0].resolve({status:{running:false}});
  await flush();
  assert.equal(h.run('statusData.running'), true);
});

test('late response from previous login cannot replace new session', async () => {
  const h = harness('saved');
  h.run('logout(false)');
  h.requests[0].resolve({status:{running:true}});
  await flush();
  assert.equal(h.run('statusReady'), false);
  assert.equal(h.run('statusData.running'), undefined);
});

test('versioned service worker serves cached shell without waiting on network', async () => {
  const listeners = {}, shell = {cached:true};
  let networkCalls = 0, response;
  const context = vm.createContext({URL,
    self: {location:{origin:'https://maagent.test'}, addEventListener:(name, fn) => listeners[name]=fn},
    caches:{open:async () => ({match:async () => shell})},
    fetch:() => { networkCalls++; return new Promise(() => {}); }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../maagent/server/web/sw.js'),'utf8'),context);
  listeners.fetch({request:{method:'GET',url:'https://maagent.test/'},respondWith:(value) => response=value});
  assert.equal(await response, shell);
  assert.equal(networkCalls,0);
  let intercepted = false;
  listeners.fetch({request:{method:'GET',url:'https://maagent.test/api/v1/status'},respondWith:() => intercepted=true});
  assert.equal(intercepted,false);
});
