/* Subvideo – trang điều khiển từ xa (2.3.0). Không thư viện ngoài; mọi chữ từ máy tính gắn bằng textContent (không
   innerHTML) – phụ đề / tên dự án không chạy được mã. App Android gắn cầu nối window.SubvideoApp (thông báo, tải file,
   chia sẻ, quét mã QR). */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const NATIVE = typeof window.SubvideoApp !== 'undefined' ? window.SubvideoApp : null;

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  for (const c of kids.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const ICON = {
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22"><path d="M8 5v14l11-7z"/></svg>',
};
function icon(name) { const s = h('span'); s.innerHTML = ICON[name]; return s.firstChild; }   // chỉ hằng số trong file

// ================================================================== trạng thái chung
const S = {
  token: store('sv_t') || '', me: null, jobs: [], projects: [], summary: '', opts: {}, seen: {}, offline: false,
  screen: null, notes: [], pollTimer: 0,
};
function store(k, v) {
  try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); }
  catch (e) { return null; }
}

class ApiErr extends Error { constructor(m, code) { super(m); this.code = code; } }

async function api(path, opt = {}) {
  const method = opt.method || (opt.body !== undefined ? 'POST' : 'GET');
  const headers = {};
  if (S.token) headers.Authorization = 'Bearer ' + S.token;
  if (method !== 'GET') { headers['X-SV'] = '1'; headers['Content-Type'] = 'application/json'; }
  let r;
  try {
    r = await fetch(path, { method, headers, credentials: 'same-origin',
      body: opt.body !== undefined ? JSON.stringify(opt.body) : undefined });
  } catch (e) { throw new ApiErr('Mất kết nối tới máy tính – kiểm tra mạng', 0); }
  if (opt.raw && r.ok) return r;
  let d = {};
  try { d = await r.json(); } catch (e) { /* không phải JSON */ }
  if (r.status === 401 && !opt.noAuthRedirect) { S.token = ''; store('sv_t', null); showLogin(d.error); throw new ApiErr(d.error || 'Chưa đăng nhập', 401); }
  if (r.status === 503 && d.offline) { setOffline(true); throw new ApiErr(d.error, 503); }
  if (!r.ok) throw new ApiErr(d.error || ('Lỗi ' + r.status), r.status);
  setOffline(false);
  return d;
}
const post = (path, body = {}) => api(path, { body });

function toast(msg, kind = '', ms) {
  if (!msg) return;
  const t = h('div', { class: 't ' + kind, text: msg });
  $('#toast').append(t);
  setTimeout(() => t.remove(), ms || (msg.length > 120 ? 9000 : 4500));
}
function err(e) { if (e && e.code !== 401) toast(e.message || String(e), 'err'); }
function setOffline(v) {
  if (S.offline === v) return;
  S.offline = v;
  const b = $('#offline');
  if (b) b.classList.toggle('hidden', !v);
}
async function guard(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { err(e); } finally { if (btn) btn.disabled = false; }
}

const fmtT = (s) => {
  s = Math.max(0, Number(s) || 0);
  const hh = Math.floor(s / 3600), mm = Math.floor(s % 3600 / 60), ss = Math.floor(s % 60);
  return (hh ? hh + ':' + String(mm).padStart(2, '0') : mm) + ':' + String(ss).padStart(2, '0');
};
const fmtT1 = (s) => fmtT(s) + '.' + Math.floor((Number(s) % 1) * 10);
const fmtN = (n) => Number(n || 0).toLocaleString('vi-VN');
const fmtMB = (b) => b > 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(1) + ' MB';
const fmtEta = (s) => !s ? '' : s > 3600 ? `còn ~${Math.round(s / 3600 * 10) / 10} giờ` : s > 90 ? `còn ~${Math.round(s / 60)} phút` : `còn ~${Math.round(s)} s`;
const ago = (t) => { const d = Date.now() / 1000 - t; return d < 60 ? 'vừa xong' : d < 3600 ? `${Math.round(d / 60)} phút trước` : d < 86400 ? `${Math.round(d / 3600)} giờ trước` : new Date(t * 1000).toLocaleDateString('vi-VN'); };

function sw(label, on, onFlip, disabled) {
  const b = h('button', { class: 'switch', role: 'switch', 'aria-checked': String(!!on), 'aria-label': label, disabled });
  b.addEventListener('click', async () => {
    const v = b.getAttribute('aria-checked') !== 'true';
    b.setAttribute('aria-checked', String(v));
    try { await onFlip(v); } catch (e) { b.setAttribute('aria-checked', String(!v)); err(e); }
  });
  return h('div', { class: 'sw' }, h('span', { text: label }), b);
}
function select(label, items, value, onChange, opt = {}) {
  const s = h('select', { 'aria-label': label });
  for (const it of items) s.append(h('option', { value: it.id, text: it.name }));
  s.value = value == null ? '' : String(value);
  if (s.value !== String(value ?? '') && items.length && !opt.keepEmpty) s.selectedIndex = 0;
  s.addEventListener('change', async () => {
    const old = s.dataset.v ?? value;
    try { await onChange(s.value); s.dataset.v = s.value; } catch (e) { s.value = old; err(e); }
  });
  return label ? h('label', { class: 'fld' }, label, s) : s;
}
function sheet(title, ...kids) {
  const bg = h('div', { class: 'sheet-bg', onclick: (e) => { if (e.target === bg) close(); } });
  const close = () => { bg.remove(); document.removeEventListener('keydown', esc); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', esc);
  const sh = h('div', { class: 'sheet', role: 'dialog', 'aria-label': title },
    h('div', { class: 'row' }, h('h3', { class: 'grow', text: title }), h('button', { class: 'btn small ghost', text: 'Đóng', onclick: () => close() })),
    ...kids);
  bg.append(sh);
  document.body.append(bg);
  return { close, el: sh };
}
function confirmBox(text, okText = 'Đồng ý') {
  return new Promise((res) => {
    let done = false;
    const sh = sheet('Xác nhận', h('div', { text }), h('div', { class: 'row' },
      h('button', { class: 'btn grow', text: 'Huỷ', onclick: () => { done = true; sh.close(); res(false); } }),
      h('button', { class: 'btn primary grow', text: okText, onclick: () => { done = true; sh.close(); res(true); } })));
    const obs = new MutationObserver(() => { if (!sh.el.isConnected) { obs.disconnect(); if (!done) res(false); } });
    obs.observe(document.body, { childList: true });
  });
}
function playVideo(src, title) {
  const v = h('video', { src, controls: true, playsinline: true, preload: 'metadata', autoplay: true });
  const sh = sheet(title || 'Xem video', v);
  const stop = new MutationObserver(() => { if (!sh.el.isConnected) { v.pause(); v.removeAttribute('src'); v.load(); stop.disconnect(); } });
  stop.observe(document.body, { childList: true });
}
const player = () => $('#player');
function playAudio(src) {
  const a = player();
  if (a.dataset.src === src && !a.paused) { a.pause(); return; }
  a.dataset.src = src; a.src = src;
  a.play().catch(() => toast('Không phát được âm thanh này', 'err'));
}
async function playBlob(path, body) {
  const r = await api(path, { body, raw: true });
  const url = URL.createObjectURL(await r.blob());
  const a = player();
  a.dataset.src = url; a.src = url;
  await a.play();
}
function download(url, name, share) {
  if (NATIVE && NATIVE.download) { NATIVE.download(location.origin + url, name || '', !!share); toast(share ? 'Đang tải về để chia sẻ…' : 'Đang tải về điện thoại…'); return; }
  const a = h('a', { href: url, download: name || '' });
  document.body.append(a); a.click(); a.remove();
}

// ================================================================== hỏi trạng thái định kỳ + thông báo
async function poll() {
  clearTimeout(S.pollTimer);
  try {
    const d = await api('/api/state');
    const first = !Object.keys(S.seen).length && !S.polled;
    S.polled = true;
    for (const j of d.jobs) {
      const was = S.seen[j.id];
      if (!first && was && was !== j.status && (j.status === 'done' || j.status === 'error')) jobNote(j);
      S.seen[j.id] = j.status;
    }
    S.jobs = d.jobs; S.projects = d.projects; S.summary = d.summary;
    if (S.screen && S.screen.onState) S.screen.onState();
  } catch (e) { /* lỗi mạng: lần sau thử lại */ }
  S.pollTimer = setTimeout(poll, document.hidden ? 10000 : 2500);
}
function jobNote(j) {
  const ok = j.status === 'done';
  const text = ok ? (j.result_msg || 'Xong') : (j.error || 'Lỗi');
  S.notes.unshift({ title: j.title, text, ok, t: Date.now() / 1000, pid: j.project_id });
  S.notes = S.notes.slice(0, 30);
  toast((ok ? '✓ ' : '⚠ ') + j.title + ' – ' + text.slice(0, 220), ok ? 'ok' : 'err');
  try {   // app Android: đang ẩn -> thông báo của máy; đang mở -> chỉ ghi đã báo (bộ hỏi nền 15 phút không báo trùng)
    if (NATIVE && document.hidden && NATIVE.notify) NATIVE.notify((ok ? '✓ ' : '⚠ ') + j.title, text.slice(0, 300), String(j.finished || 0));
    else if (NATIVE && NATIVE.seen) NATIVE.seen(String(j.finished || 0));
  } catch (e) { /* bỏ qua */ }
  const b = $('#bellbadge'); if (b) b.classList.remove('hidden');
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
const jobsOf = (pid) => S.jobs.filter((j) => j.project_id === pid);
const activeOf = (pid) => jobsOf(pid).find((j) => j.status === 'running') || jobsOf(pid).find((j) => j.status === 'queued');

function jobLine(j, withCancel = true) {
  const run = j.status === 'running', wait = j.status === 'queued';
  const pct = Math.round((j.progress || 0) * 100);
  const right = run ? `${pct} %${j.eta ? ' · ' + fmtEta(j.eta) : ''}` : wait ? 'Đang chờ lượt' :
    j.status === 'done' ? '✓ Xong' : j.status === 'error' ? '⚠ Lỗi' : 'Đã huỷ';
  const cls = run ? 'st-run' : wait ? 'st-wait' : j.status === 'error' ? 'st-err' : 'st-done';
  const msg = run || wait ? j.message : j.status === 'error' ? j.error : j.result_msg;
  return h('div', { class: 'job' },
    h('div', { class: 't' }, h('span', { text: j.title }), h('span', { class: cls, text: right })),
    run ? h('div', { class: 'bar' }, h('div', { style: { width: pct + '%' } })) : null,
    (msg || (withCancel && (run || wait))) ? h('div', { class: 'm' }, h('span', { text: msg || '' }),
      withCancel && (run || wait) ? h('button', { class: 'btn small ghost', text: 'Huỷ', onclick: (e) => guard(e.target, async () => {
        await post(`/api/jobs/${j.id}/cancel`); toast('Đã gửi lệnh huỷ'); poll();
      }) }) : null) : null);
}

// ================================================================== đăng nhập / ghép máy
function showLogin(why) {
  S.screen = null;
  const app = $('#app');
  app.replaceChildren();
  const code = h('input', { type: 'text', inputmode: 'numeric', maxlength: '6', placeholder: 'Mã 6 số trên máy tính', autocomplete: 'one-time-code' });
  const pw = h('input', { type: 'password', placeholder: 'Mật khẩu đặt ở Cài đặt của tool', autocomplete: 'current-password' });
  const name = h('input', { type: 'text', placeholder: 'Tên điện thoại (vd: Điện thoại của tôi)', value: store('sv_name') || '' });
  const msg = h('div', { class: 'msg ' + (why ? 'warn' : 'info'),
    text: why || 'Trên máy tính: Cài đặt → Điều khiển từ xa → quét mã QR bằng app Subvideo, hoặc nhập mã 6 số / mật khẩu.' });
  const done = (d) => { S.token = d.token; store('sv_t', d.token); store('sv_name', name.value.trim());
    if (NATIVE && NATIVE.setToken) NATIVE.setToken(location.origin, d.token); boot(); };
  app.append(h('div', { class: 'page login' },
    h('div', { class: 'top' }, h('div', { class: 'logo', text: 'S' }), h('div', { class: 'grow' }, h('h1', { text: 'Subvideo' }), h('div', { class: 'sub', text: 'Điều khiển tool trên máy tính' }))),
    msg,
    h('div', { class: 'card' }, h('h2', { text: 'Ghép máy bằng mã' }), code, name,
      NATIVE && NATIVE.scanQr ? h('button', { class: 'btn', text: 'Quét mã QR trên máy tính', onclick: () => NATIVE.scanQr() }) : null,
      h('button', { class: 'btn primary', text: 'Ghép máy', onclick: (e) => guard(e.target, async () => {
        done(await api('/api/pair', { body: { code: code.value.trim(), name: name.value.trim() || 'Điện thoại' }, noAuthRedirect: true }));
      }) })),
    h('div', { class: 'card' }, h('h2', { text: 'Hoặc đăng nhập bằng mật khẩu' }), pw,
      h('button', { class: 'btn', text: 'Đăng nhập', onclick: (e) => guard(e.target, async () => {
        done(await api('/api/login', { body: { password: pw.value, name: name.value.trim() || 'Trình duyệt' }, noAuthRedirect: true }));
      }) }))));
}

// ================================================================== khung trang chung
function page(title, sub, opt = {}) {
  const top = h('div', { class: 'top' },
    opt.back ? h('a', { class: 'iconbtn', href: opt.back, 'aria-label': 'Quay lại' }, icon('back')) : h('div', { class: 'logo', text: 'S' }),
    h('div', { class: 'grow' }, h('h1', { text: title }), sub instanceof Node ? sub : h('div', { class: 'sub', text: sub || '' })),
    ...(opt.right || []));
  const pg = h('div', { class: 'page' }, top,
    h('div', { id: 'offline', class: 'offline' + (S.offline ? '' : ' hidden'),
      text: 'Máy tính chưa kết nối (tool tắt / máy ngủ / mất mạng) – mở tool rồi thử lại.' }));
  $('#app').replaceChildren(pg);
  window.scrollTo(0, 0);
  return pg;
}
function viaText() {
  if (!S.me) return '';
  return `Đã nối ${S.me.pc} · ${S.me.via === 'vps' ? 'qua VPS' : 'mạng nhà'}`;
}

// ================================================================== Trang chủ
function homeScreen() {
  const bell = h('button', { class: 'iconbtn', 'aria-label': 'Thông báo', onclick: showNotes }, icon('bell'),
    h('span', { id: 'bellbadge', class: 'badge hidden' }));
  const menu = h('button', { class: 'iconbtn', 'aria-label': 'Thêm', onclick: showMenu }, icon('menu'));
  const pg = page('Subvideo', h('div', { class: 'sub' }, h('span', { class: 'dot' }), viaText()), { right: [bell, menu] });

  const ta = h('textarea', { rows: '3', placeholder: 'Dán link Bilibili / Douyin / YouTube… mỗi dòng 1 link (dán cả dòng chia sẻ của app cũng được)' });
  ta.value = store('sv_links') || '';
  ta.addEventListener('input', () => store('sv_links', ta.value));
  const qmsg = h('div', { class: 'msg ok hidden', role: 'status' });
  const summ = h('span', { class: 'grow', text: 'Thiết lập: ' + (S.summary || '…') });
  const queue = (auto) => async (e) => guard(e.target, async () => {
    const d = await post('/api/links', { text: ta.value, auto });
    qmsg.textContent = d.message; qmsg.classList.remove('hidden');
    ta.value = ''; store('sv_links', '');
    poll();
  });
  pg.append(h('section', { class: 'card' }, h('h2', { text: 'Tải video từ link' }), ta,
    h('div', { class: 'row' }, h('button', { class: 'btn primary grow', text: 'Tải về & dịch tự động', onclick: queue(true) }),
      h('button', { class: 'btn', text: 'Chỉ tải về', onclick: queue(false) })),
    h('div', { class: 'row hint' }, summ, h('a', { href: '#', text: 'Sửa', onclick: (e) => { e.preventDefault(); presetSheet(); } })),
    qmsg));

  const jobsCard = h('section', { class: 'card' });
  const projBox = h('section', { class: 'lines' });
  pg.append(jobsCard, h('div', { class: 'row' }, h('h2', { class: 'grow', style: { fontSize: '15px', margin: '4px 2px 0' }, text: 'Dự án' })), projBox);

  const drawJobs = () => {
    const act = S.jobs.filter((j) => j.status === 'running' || j.status === 'queued');
    const wait = act.filter((j) => j.status === 'queued').length;
    jobsCard.replaceChildren(h('div', { class: 'head' }, h('h2', { text: 'Việc đang làm' }), h('span', { class: 'pill', text: act.length ? `${act.length} việc` : 'không có' })));
    if (!act.length) jobsCard.append(h('div', { class: 'hint', text: 'Không có việc nào đang chạy. Việc xong sẽ báo lên điện thoại.' }));
    act.slice(0, 8).forEach((j, i) => { if (i) jobsCard.append(h('div', { class: 'sep' })); jobsCard.append(jobLine(j)); });
    if (wait > 1) jobsCard.append(h('button', { class: 'btn small ghost', text: `Huỷ hết ${wait} việc đang chờ`, onclick: (e) => guard(e.target, async () => {
      if (!await confirmBox(`Huỷ ${wait} việc đang chờ?`, 'Huỷ việc')) return;
      const d = await post('/api/jobs/cancel_queued'); toast(`Đã huỷ ${d.cancelled} việc`); poll();
    }) }));
  };
  let projSig = '';
  const drawProjects = () => {
    const sig = JSON.stringify(S.projects.map((p) => [p.id, p.badge, p.n, p.nt, p.name]));
    if (sig === projSig) return;
    projSig = sig;
    projBox.replaceChildren();
    if (!S.projects.length) projBox.append(h('div', { class: 'hint', text: 'Chưa có dự án – dán link phía trên.' }));
    for (const p of S.projects) {
      const th = h('div', { class: 'th' });
      if (p.thumb) th.style.backgroundImage = `url("/api/p/${encodeURIComponent(p.id)}/thumb")`;
      projBox.append(h('a', { class: 'proj', href: '#/p/' + encodeURIComponent(p.id) }, th,
        h('div', { class: 'grow', style: { minWidth: 0 } },
          h('div', { class: 'nm', text: p.name }),
          h('div', { class: 'meta', text: [p.duration ? fmtT(p.duration) : '', p.n ? `${fmtN(p.n)} câu` : '', p.nt && p.nt < p.n ? `dịch ${fmtN(p.nt)}` : ''].filter(Boolean).join(' · ') }),
          h('span', { class: 'tag ' + p.bk, text: p.badge }))));
    }
  };
  S.screen = { onState: () => { drawJobs(); drawProjects(); summ.textContent = 'Thiết lập: ' + (S.summary || '…'); } };
  S.screen.onState();
  if (S.notes.length) $('#bellbadge').classList.remove('hidden');
}

function showNotes() {
  $('#bellbadge')?.classList.add('hidden');
  const done = S.jobs.filter((j) => j.status === 'done' || j.status === 'error');
  sheet('Thông báo', done.length ? h('div', { class: 'lines' }, done.slice(0, 20).map((j) => h('div', { class: 'card' }, jobLine(j, false),
    j.project_id ? h('a', { href: '#/p/' + encodeURIComponent(j.project_id), text: 'Mở dự án ›', onclick: () => document.querySelector('.sheet-bg')?.remove() }) : null)))
    : h('div', { class: 'hint', text: 'Chưa có việc nào xong trong lần mở tool này.' }));
}
function showMenu() {
  const sh = sheet('Subvideo',
    h('div', { class: 'hint', text: `${viaText()} · tool bản ${S.me?.version || '?'} · thiết bị “${S.me?.name || ''}”` }),
    h('button', { class: 'btn', text: 'Tải lại trang', onclick: () => location.reload() }),
    NATIVE && NATIVE.openSettings ? h('button', { class: 'btn', text: 'Cài đặt app (địa chỉ máy tính, thông báo)', onclick: () => NATIVE.openSettings() }) : null,
    h('button', { class: 'btn danger', text: 'Đăng xuất (ngắt điện thoại này)', onclick: async () => {
      if (!await confirmBox('Ngắt điện thoại này khỏi máy tính? Lần sau phải ghép lại bằng mã QR / mật khẩu.', 'Ngắt')) return;
      try { await post('/api/logout'); } catch (e) { /* đã ngắt */ }
      S.token = ''; store('sv_t', null); if (NATIVE && NATIVE.setToken) NATIVE.setToken(location.origin, '');
      sh.close(); showLogin();
    } }));
}
async function loadOptions(pid) {
  S.opts = await api('/api/options' + (pid ? '?pid=' + encodeURIComponent(pid) : ''));
  return S.opts;
}
async function presetSheet() {
  const o = await loadOptions().catch(err);
  if (!o) return;
  const v = o.values;
  const set = (k) => async (val) => { await post('/api/settings', { [k]: val }); poll(); };
  const voice = select('Giọng lồng (dự án mới)', [{ id: '', name: 'Theo giọng từng người nói' }, { id: '@auto', name: 'Tự gán giọng nam / nữ' }, ...o.voices], v.dub_voice_all || '', set('dub_voice_all'));
  sheet('Thiết lập dịch tự động',
    h('div', { class: 'hint', text: 'Dùng chung với tab Tự động trên máy tính. Nguồn phụ đề / bộ nghe đổi ở tool trên máy.' }),
    select('Dịch bằng', o.engines, v.translate_engine, set('translate_engine')),
    select('Dịch sang', o.targets, v.target_lang, set('target_lang')),
    select('Thể loại phim', o.styles, v.translate_style, set('translate_style')),
    select('Bộ đọc', o.tts, v.tts_engine, set('tts_engine')),
    voice,
    o.qualities ? select('Chất lượng tải video', o.qualities, v.dl_quality || '1080', set('dl_quality')) : null);
}

// ================================================================== Dự án
async function projectScreen(pid) {
  const pg = page('Dự án', 'Đang tải…', { back: '#/' });
  let d;
  try { d = await api('/api/p/' + encodeURIComponent(pid)); } catch (e) { err(e); return; }
  const info = d.info || {};
  const pgs = page(d.name, [info.duration ? fmtT(info.duration) : '', info.width ? `${info.width}×${info.height}` : '', `${fmtN(d.n)} câu`].filter(Boolean).join(' · '), { back: '#/' });
  const enc = encodeURIComponent(pid);
  if (d.has_video || d.thumb) {
    pgs.append(h('div', { class: 'hero' },
      d.thumb ? h('img', { src: `/api/p/${enc}/thumb`, alt: '' }) : null,
      d.has_video ? h('button', { class: 'play', 'aria-label': 'Xem video', onclick: () => playVideo(`/api/p/${enc}/video`, d.name) }, icon('play')) : null));
  }

  const box = d.box;
  const isOcr = ['ocr', 'hybrid'].includes(d.preset_method);
  const runBox = h('div', { class: 'lines' });
  const s1 = h('section', { class: 'card' },
    h('div', { class: 'step' }, h('span', { class: 'num' + (d.n ? ' done' : ' on'), text: d.n ? '✓' : '1' }),
      h('div', { class: 'grow' }, h('div', { class: 'tt', text: 'Nghe & tách câu' }),
        h('div', { class: 'ds', text: 'Nguồn: ' + ({ asr: 'nghe giọng nói', ocr: 'OCR phụ đề in sẵn', hybrid: 'OCR + nghe' }[d.preset_method] || d.preset_method) + (d.n ? ` · đã có ${fmtN(d.n)} câu` : '') }))),
    isOcr ? h('a', { class: 'regionrow', href: `#/p/${enc}/region` },
      h('div', { class: 'mini' }, box ? h('div', { style: { left: box.x * 100 + '%', top: box.y * 100 + '%', width: box.w * 100 + '%', height: box.h * 100 + '%' } }) : null),
      h('div', { class: 'grow' }, h('div', { class: 'tt', style: { fontWeight: 600 }, text: 'Vùng phụ đề' }),
        h('div', { class: 'ds hint', text: box ? `Đã chọn: dọc ${Math.round(box.y * 100)}–${Math.round((box.y + box.h) * 100)} %, ngang ${Math.round(box.x * 100)}–${Math.round((box.x + box.w) * 100)} %` : 'Chưa chọn – tool tự dò. Nên kiểm tra trước khi chạy.' })),
      h('span', { class: 'go', text: 'Chọn vùng' })) : null,
    runBox);
  pgs.append(s1);
  const step = (n, title, ds, href, done) => h('a', { class: 'step', href },
    h('span', { class: 'num' + (done ? ' done' : ''), text: done ? '✓' : String(n) }),
    h('div', { class: 'grow' }, h('div', { class: 'tt', text: title }), h('div', { class: 'ds', text: ds })), h('span', { class: 'go', text: 'Mở ›' }));
  pgs.append(h('section', { class: 'card' },
    step(2, 'Dịch & phụ đề', d.n ? `${fmtN(d.nt)}/${fmtN(d.n)} câu đã dịch · xem / sửa bảng câu` : 'Làm bước 1 trước', `#/p/${enc}/translate`, d.n && d.nt >= d.n),
    h('div', { class: 'sep' }),
    step(3, 'Lồng tiếng', d.dubbed ? 'Đã có bản lồng · giọng, tốc độ, nghe thử' : 'Giọng đọc, tốc độ, tạo bản lồng', `#/p/${enc}/dub`, d.dubbed),
    h('div', { class: 'sep' }),
    step(4, 'Xuất video', d.videos ? `${d.videos} bản đã xuất · tải về điện thoại` : 'Phụ đề, xoá chữ Trung, tỉ lệ, tải về', `#/p/${enc}/export`, d.videos)));
  const autoBtn = h('button', { class: 'btn block', text: 'Chạy cả 4 bước tự động (dừng trước khi xuất)', disabled: !d.has_video });
  autoBtn.onclick = (e) => guard(e.target, async () => {
    if (d.n && !await confirmBox('Chạy lại từ đầu: nghe & tách câu lại (thay phụ đề hiện có) → dịch → lồng tiếng?', 'Chạy')) return;
    await post(`/api/p/${enc}/run`, { step: 'auto' }); toast('Đã xếp việc dịch tự động'); poll();
  });
  pgs.append(autoBtn);

  const drawRun = () => {
    const j = activeOf(pid);
    runBox.replaceChildren();
    if (j) { runBox.append(jobLine(j)); return; }
    const b = h('button', { class: 'btn primary block', text: d.n ? '1. Nghe & tách câu lại' : '1. Chạy nghe & tách câu', disabled: !d.has_video });
    b.onclick = (e) => guard(e.target, async () => {
      if (d.n && !await confirmBox('Nghe & tách câu lại sẽ THAY toàn bộ phụ đề hiện có. Tiếp tục?', 'Chạy lại')) return;
      await post(`/api/p/${enc}/run`, { step: 'recognize' }); toast('Đã xếp việc – tool báo lên điện thoại khi xong'); poll();
    });
    runBox.append(b);
    if (!d.has_video) runBox.append(h('div', { class: 'msg warn', text: 'Dự án chưa có video (đang tải hoặc video gốc trên máy tính đã bị chuyển / xoá).' }));
  };
  let lastActive = !!activeOf(pid);
  S.screen = { onState: () => {
    const act = !!activeOf(pid);
    if (lastActive && !act) { lastActive = false; projectScreen(pid); return; }   // việc xong -> tải lại số liệu
    lastActive = act; drawRun();
  } };
  drawRun();
}

// ================================================================== Chọn vùng phụ đề
const PRESETS = [
  { id: 'auto', label: 'Tự dò' }, { id: 'one', label: 'Dải dưới 1 dòng', b: { t: 0.77, b: 0.93 } },
  { id: 'two', label: 'Dải dưới 2 dòng', b: { t: 0.66, b: 0.93 } }, { id: 'mid', label: 'Giữa (video dọc)', b: { t: 0.55, b: 0.72 } },
];
async function regionScreen(pid) {
  const enc = encodeURIComponent(pid);
  const pg = page('Vùng phụ đề', 'Đang tải…', { back: `#/p/${enc}` });
  let d;
  try { d = await api('/api/p/' + enc); } catch (e) { err(e); return; }
  const dur = (d.info && d.info.duration) || 0;
  const R = {
    t: Number(store('sv_rt_' + pid)) || Math.min(dur * 0.2, 60) || 0,
    box: d.box ? { t: d.box.y, b: d.box.y + d.box.h, l: d.box.x, r: d.box.x + d.box.w } : null,
    preset: '', picking: false, color: '', seq: 0, saved: !!d.box, w: 0, h: 0,
  };
  R.full = !R.box || (R.box.l <= 0.001 && R.box.r >= 0.999);
  const hint = h('div', { class: 'sub', text: 'Kéo khung xanh quanh dòng phụ đề' });
  pg.querySelector('.top .grow').replaceChildren(h('h1', { text: 'Vùng phụ đề' }), hint);

  const img = h('img', { alt: 'Khung hình video', draggable: 'false' });
  const boxEl = h('div', { class: 'box hidden' }, h('span', { class: 'lbl', text: 'Vùng quét' }));
  const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
  const hds = HANDLES.map((k) => h('span', { class: 'hd', 'data-h': k }));
  const timeEl = h('span', { class: 'time' });
  const busyEl = h('span', { class: 'busy hidden' });
  const stage = h('div', { class: 'stage' }, img, boxEl, ...hds, timeEl, busyEl);
  const slider = h('input', { type: 'range', min: '0', max: String(Math.max(1, dur)), step: '0.1', 'aria-label': 'Vị trí trong video' });
  const tlabel = h('div', { class: 'grow', style: { textAlign: 'center', fontSize: '13px', color: 'var(--text2)' } });
  const edgesBox = h('div', { class: 'edges' });
  const presetBox = h('div', { class: 'wrap' });
  const fullBtn = h('button', { class: 'chip', text: '↔ Hết chiều ngang' });
  const sw0 = h('span', { class: 'swatch', style: { background: 'transparent' } });
  const colorName = h('span', { class: 'hint', text: 'chưa hút màu' });
  const pickBtn = h('button', { class: 'btn small', text: 'Hút màu chữ' });
  const useColor = h('button', { class: 'btn small hidden', text: 'Chỉ lấy chữ màu này khi quét' });
  const strip = h('div', { class: 'strip' }, h('span', { class: 'hint', text: 'Kéo khung để đọc thử' }));
  const readMsg = h('div', { class: 'msg', role: 'status', text: R.box ? 'Đang đọc thử…' : 'Chưa có khung – khi chạy tool sẽ tự dò vùng phụ đề.' });
  const saveBtn = h('button', { class: 'btn primary grow', text: 'Dùng khung này' });
  const clearBtn = h('button', { class: 'btn', text: 'Bỏ khung' });

  pg.append(stage,
    h('div', { class: 'row' },
      h('button', { class: 'btn small', text: '‹ Câu trước', onclick: (e) => seek(-1, e.target) }), tlabel,
      h('button', { class: 'btn small', text: 'Câu sau ›', onclick: (e) => seek(1, e.target) })),
    slider, presetBox,
    h('section', { class: 'card' }, h('div', { class: 'head' }, h('h2', { text: 'Chỉnh từng mép' }), h('span', { class: 'hint', text: 'mỗi lần 1 %' })), edgesBox),
    h('section', { class: 'card' }, h('div', { class: 'row' }, h('span', { class: 'grow' }, 'Màu chữ ', sw0, ' ', colorName), pickBtn), useColor),
    h('section', { class: 'card' }, h('div', { class: 'head' }, h('h2', { text: 'Đọc thử trong khung' }), h('span', { class: 'hint', text: 'ảnh tool sẽ đọc' })), strip, readMsg),
    h('div', { class: 'row' }, saveBtn, clearBtn));

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const MIN = 0.03;
  function drawBox() {
    const b = R.box;
    boxEl.classList.toggle('hidden', !b);
    hds.forEach((x) => x.classList.toggle('hidden', !b || R.picking));
    if (b) {
      Object.assign(boxEl.style, { left: b.l * 100 + '%', top: b.t * 100 + '%', width: (b.r - b.l) * 100 + '%', height: (b.b - b.t) * 100 + '%' });
      boxEl.firstChild.classList.toggle('below', b.t < 0.08);
      const pos = { nw: [b.l, b.t], n: [(b.l + b.r) / 2, b.t], ne: [b.r, b.t], e: [b.r, (b.t + b.b) / 2], se: [b.r, b.b], s: [(b.l + b.r) / 2, b.b], sw: [b.l, b.b], w: [b.l, (b.t + b.b) / 2] };
      hds.forEach((x) => { const [px, py] = pos[x.dataset.h]; x.style.left = px * 100 + '%'; x.style.top = py * 100 + '%'; });
    }
    fullBtn.setAttribute('aria-pressed', String(!!b && b.l <= 0.001 && b.r >= 0.999));
    presetBox.querySelectorAll('[data-p]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.p === R.preset)));
    drawEdges();
    saveBtn.textContent = R.saved ? '✓ Đã dùng khung này' : 'Dùng khung này';
  }
  function drawEdges() {
    const b = R.box;
    edgesBox.replaceChildren();
    const E = [['Mép trên', 't', '▲', '▼', -1], ['Mép dưới', 'b', '▲', '▼', -1], ['Mép trái', 'l', '◀', '▶', -1], ['Mép phải', 'r', '◀', '▶', -1]];
    for (const [label, k, ia, ib] of E) {
      const val = b ? Math.round(b[k] * 1000) / 10 + ' %' : '–';
      const mk = (ic, dir) => {
        const bt = h('button', { text: ic, 'aria-label': `${label} ${dir < 0 ? (k === 't' || k === 'b' ? 'lên' : 'sang trái') : (k === 't' || k === 'b' ? 'xuống' : 'sang phải')}`, disabled: !b });
        let rep = 0;
        const step = () => nudge(k, dir * 0.01);
        bt.addEventListener('pointerdown', () => { step(); clearInterval(rep); rep = setInterval(step, 140); });
        bt.addEventListener('click', (e) => { if (e.detail === 0) step(); });   // bàn phím (Enter / Space)
        ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => bt.addEventListener(ev, () => { clearInterval(rep); }));
        return bt;
      };
      edgesBox.append(h('div', { class: 'edge' }, h('span', {}, label, h('br'), h('b', { text: val })), mk(ia, -1), mk(ib, 1)));
    }
  }
  function nudge(k, dv) {
    const b = { ...R.box };
    b[k] = clamp(b[k] + dv, 0, 1);
    if (k === 't') b.t = Math.min(b.t, b.b - MIN);
    if (k === 'b') b.b = Math.max(b.b, b.t + MIN);
    if (k === 'l') b.l = Math.min(b.l, b.r - MIN);
    if (k === 'r') b.r = Math.max(b.r, b.l + MIN);
    setBox(b, '');
  }
  function setBox(b, preset) {
    R.box = b; R.preset = preset || ''; R.saved = false;
    drawBox(); scheduleRead();
  }
  // ---------- kéo khung
  let drag = null;
  const posOf = (e) => { const r = stage.getBoundingClientRect(); return { x: clamp((e.clientX - r.left) / r.width, 0, 1), y: clamp((e.clientY - r.top) / r.height, 0, 1) }; };
  stage.addEventListener('pointerdown', (e) => {
    const p = posOf(e);
    if (R.picking) { pickAt(p); return; }
    stage.setPointerCapture(e.pointerId);
    const hd = e.target.closest('.hd');
    const b = R.box;
    if (hd && b) drag = { mode: hd.dataset.h, b0: { ...b }, p0: p };
    else if (b && p.x > b.l && p.x < b.r && p.y > b.t && p.y < b.b) drag = { mode: 'move', b0: { ...b }, p0: p };
    else drag = { mode: 'new', p0: p };
  });
  stage.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const p = posOf(e), b0 = drag.b0;
    let b;
    if (drag.mode === 'new') {
      if (Math.abs(p.x - drag.p0.x) < 0.02 && Math.abs(p.y - drag.p0.y) < 0.02) return;
      b = { l: Math.min(p.x, drag.p0.x), r: Math.max(p.x, drag.p0.x), t: Math.min(p.y, drag.p0.y), b: Math.max(p.y, drag.p0.y) };
    } else if (drag.mode === 'move') {
      const dx = clamp(p.x - drag.p0.x, -b0.l, 1 - b0.r), dy = clamp(p.y - drag.p0.y, -b0.t, 1 - b0.b);
      b = { l: b0.l + dx, r: b0.r + dx, t: b0.t + dy, b: b0.b + dy };
    } else {
      b = { ...b0 };
      const m = drag.mode;
      if (m.includes('n')) b.t = clamp(p.y, 0, b0.b - MIN);
      if (m.includes('s')) b.b = clamp(p.y, b0.t + MIN, 1);
      if (m.includes('w')) b.l = clamp(p.x, 0, b0.r - MIN);
      if (m.includes('e')) b.r = clamp(p.x, b0.l + MIN, 1);
    }
    R.box = b; R.preset = ''; R.saved = false; drag.moved = true; drawBox();
  });
  const endDrag = () => {
    if (!drag) return;
    const was = drag; drag = null;
    if (was.mode === 'new' && R.box && (R.box.r - R.box.l < MIN || R.box.b - R.box.t < MIN)) return;
    if (was.moved) scheduleRead();
  };
  stage.addEventListener('pointerup', endDrag);
  stage.addEventListener('pointercancel', endDrag);

  // ---------- khung hình
  function loadFrame() {
    R.t = clamp(R.t, 0, Math.max(0, dur - 0.1));
    store('sv_rt_' + pid, String(R.t));
    img.src = `/api/p/${enc}/frame?t=${R.t.toFixed(1)}&w=${Math.min(1280, Math.round(stage.clientWidth * (window.devicePixelRatio || 1)) || 720)}`;
    timeEl.textContent = fmtT1(R.t);
    tlabel.textContent = `${fmtT1(R.t)} / ${fmtT(dur)}`;
    slider.value = String(R.t);
  }
  img.addEventListener('load', () => { drawBox(); });
  img.addEventListener('error', () => { readMsg.className = 'msg err'; readMsg.textContent = 'Không chụp được khung hình (video gốc trên máy tính còn không?)'; });
  slider.addEventListener('change', () => { R.t = Number(slider.value); loadFrame(); scheduleRead(); });
  slider.addEventListener('input', () => { tlabel.textContent = `${fmtT1(Number(slider.value))} / ${fmtT(dur)}`; });
  async function seek(dir, btn) {
    if (!R.box) { toast('Kéo khung trước – tool tìm câu có chữ trong khung', 'warn'); return; }
    busyEl.textContent = 'Đang tìm câu ' + (dir < 0 ? 'trước' : 'sau') + '…'; busyEl.classList.remove('hidden');
    await guard(btn, async () => {
      const r = await post(`/api/p/${enc}/region/seek`, { t: R.t, box: boxApi(), dir });
      if (!r.found) toast(r.message || 'Không thấy câu nào nữa', 'warn');
      else { R.t = r.t; loadFrame(); scheduleRead(); }
    });
    busyEl.classList.add('hidden');
  }
  const boxApi = () => R.box && { x: R.box.l, y: R.box.t, w: R.box.r - R.box.l, h: R.box.b - R.box.t };
  // ---------- đọc thử
  let readTimer = 0;
  function scheduleRead() { clearTimeout(readTimer); readTimer = setTimeout(readNow, 650); }
  async function readNow() {
    if (!R.box) { readMsg.className = 'msg'; readMsg.textContent = 'Chưa có khung – khi chạy tool sẽ tự dò vùng phụ đề.'; strip.replaceChildren(h('span', { class: 'hint', text: '(không có khung)' })); return; }
    const seq = ++R.seq;
    readMsg.className = 'msg'; readMsg.replaceChildren(h('span', { class: 'spin' }), ' Đang đọc thử…');
    try {
      const r = await post(`/api/p/${enc}/region/read`, { t: R.t, box: boxApi() });
      if (seq !== R.seq) return;
      readMsg.className = 'msg ' + r.kind; readMsg.textContent = r.message;
      strip.replaceChildren(r.image ? h('img', { src: r.image, alt: 'Ảnh tool đọc' }) : h('span', { class: 'hint', text: '(không có chữ)' }));
    } catch (e) { if (seq === R.seq) { readMsg.className = 'msg err'; readMsg.textContent = e.message; } }
  }
  // ---------- mẫu nhanh
  for (const p of PRESETS) {
    presetBox.append(h('button', { class: 'chip', 'data-p': p.id, text: p.label, onclick: (e) => guard(e.target, async () => {
      if (p.id === 'auto') {
        busyEl.textContent = 'Đang tự dò phụ đề…'; busyEl.classList.remove('hidden');
        try {
          const r = await post(`/api/p/${enc}/region/detect`);
          toast(r.message, r.found ? 'ok' : 'warn');
          if (r.found) { const b = r.box; R.t = r.t || R.t; loadFrame(); setBox({ t: b.y, b: b.y + b.h, l: b.x, r: b.x + b.w }, 'auto'); }
        } finally { busyEl.classList.add('hidden'); }
        return;
      }
      const cur = R.box || { l: 0, r: 1 };
      setBox({ t: p.b.t, b: p.b.b, l: cur.l, r: cur.r }, p.id);
    }) }));
  }
  fullBtn.onclick = () => { const b = R.box || { t: 0.66, b: 0.93 }; setBox({ t: b.t, b: b.b, l: 0, r: 1 }, R.preset); };
  presetBox.append(fullBtn);
  // ---------- hút màu
  pickBtn.onclick = () => { R.picking = !R.picking; stage.classList.toggle('picking', R.picking); pickBtn.textContent = R.picking ? 'Chạm vào nét chữ…' : 'Hút màu chữ'; drawBox(); };
  async function pickAt(p) {
    R.picking = false; stage.classList.remove('picking'); pickBtn.textContent = 'Hút màu chữ'; drawBox();
    try {
      const r = await post(`/api/p/${enc}/region/pick`, { t: R.t, x: p.x, y: p.y });
      R.color = r.color; sw0.style.background = r.color; colorName.textContent = r.color;
      useColor.classList.remove('hidden');
    } catch (e) { err(e); }
  }
  useColor.onclick = (e) => guard(e.target, async () => { const r = await post('/api/region/color', { fill: R.color }); toast(r.message, 'ok'); scheduleRead(); });
  // ---------- lưu
  saveBtn.onclick = (e) => guard(e.target, async () => {
    if (!R.box) { toast('Chưa có khung', 'warn'); return; }
    const r = await post(`/api/p/${enc}/region/save`, { box: boxApi() });
    if (r.status === 'error') throw new ApiErr(r.error);
    R.saved = true; drawBox(); toast(r.result_msg || (r.status === 'queued' ? 'Sẽ lưu khi việc đang chạy của dự án xong' : 'Đã lưu vùng phụ đề'), 'ok');
  });
  clearBtn.onclick = (e) => guard(e.target, async () => {
    const r = await post(`/api/p/${enc}/region/save`, { box: null });
    R.box = null; R.saved = false; drawBox(); readNow(); toast(r.result_msg || 'Đã bỏ khung', 'ok');
  });
  S.screen = { onState: null };
  loadFrame(); drawBox(); if (R.box) scheduleRead();
}

// ================================================================== Dịch & phụ đề
const FILTERS = [{ id: 'all', label: 'Tất cả' }, { id: 'empty', label: 'Chưa dịch' }, { id: 'unsure', label: 'Không chắc' }, { id: 'long', label: 'Quá dài' }];
async function translateScreen(pid) {
  const enc = encodeURIComponent(pid);
  const pg = page('Dịch & phụ đề', 'Đang tải…', { back: `#/p/${enc}` });
  let o;
  try { o = await loadOptions(pid); } catch (e) { err(e); return; }
  const v = o.values;
  const T = { rows: [], offset: 0, count: 0, total: 0, empty: 0, filter: 'all', q: '', picked: new Map(), open: -1, loading: false };
  const sub = pg.querySelector('.top .sub');
  const set = (k) => async (val) => { await post('/api/settings', { [k]: val }); };
  const vt = sw('Dịch có nhìn hình (Gemini xem video)', v.translate_video, set('translate_video'), v.translate_engine !== 'gemini');
  pg.append(h('section', { class: 'card' }, h('h2', { text: 'Cách dịch' }),
    h('div', { class: 'grid2' },
      select('Dịch bằng', o.engines, v.translate_engine, async (val) => {
        await post('/api/settings', { translate_engine: val });
        vt.querySelector('button').disabled = val !== 'gemini';
      }),
      select('Dịch sang', o.targets, v.target_lang, async (val) => { await post(`/api/p/${enc}/target`, { lang: val }); }),
      select('Thể loại phim', o.styles, v.translate_style, set('translate_style')),
      select('Kỹ năng AI', o.skills, v.translate_skill || '', set('translate_skill'))),
    sw('AI kiểm tra lại bản dịch', v.ai_review_after_translate, set('ai_review_after_translate')),
    sw('Tách câu dịch dài', v.split_long_trans, set('split_long_trans')),
    vt));
  const tmsg = h('div', { class: 'hidden' });
  const btnEmpty = h('button', { class: 'btn primary block', text: 'Dịch câu chưa dịch' });
  const btnAll = h('button', { class: 'btn grow', text: 'Dịch lại tất cả' });
  const btnPicked = h('button', { class: 'btn grow', text: 'Dịch câu đã tích (0)' });
  const runBox = h('div', { class: 'lines' });
  pg.append(h('section', { class: 'card' }, btnEmpty, h('div', { class: 'row' }, btnAll, btnPicked), runBox, tmsg));
  const doTranslate = async (mode, btn) => guard(btn, async () => {
    if (mode === 'all' && !await confirmBox('Dịch lại TẤT CẢ câu (câu đã sửa tay cũng bị dịch lại)?', 'Dịch lại')) return;
    const body = { mode };
    if (mode === 'rows') {
      if (!T.picked.size) { toast('Tích chọn câu trước', 'warn'); return; }
      body.rows = [...T.picked.keys()]; body.texts = [...T.picked.values()];
    }
    await post(`/api/p/${enc}/translate`, body); toast('Đã xếp việc dịch'); poll();
  });
  btnEmpty.onclick = (e) => doTranslate('empty', e.target);
  btnAll.onclick = (e) => doTranslate('all', e.target);
  btnPicked.onclick = (e) => doTranslate('rows', e.target);

  const q = h('input', { type: 'search', placeholder: 'Tìm chữ gốc hoặc bản dịch…', 'aria-label': 'Tìm câu' });
  const fbox = h('div', { class: 'wrap' });
  const list = h('div', { class: 'lines' });
  const more = h('button', { class: 'btn small ghost hidden', text: 'Xem thêm' });
  const pickBar = h('div', { class: 'picked hidden' });
  pg.append(h('section', { class: 'card' }, h('div', { class: 'head' }, h('h2', { text: 'Bảng câu' }), h('span', { class: 'hint', text: 'bấm câu để sửa' })), q, fbox),
    list, more, pickBar);
  for (const f of FILTERS) fbox.append(h('button', { class: 'chip', 'data-f': f.id, text: f.label, onclick: () => { T.filter = f.id; loadLines(true); } }));
  let qTimer = 0;
  q.addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(() => { T.q = q.value; loadLines(true); }, 400); });
  more.onclick = () => loadLines(false);

  function drawPicked() {
    const n = T.picked.size;
    btnPicked.textContent = `Dịch câu đã tích (${n})`;
    pickBar.classList.toggle('hidden', !n);
    pickBar.replaceChildren(h('span', { text: `Đã tích ${n} câu` }),
      h('button', { class: 'btn small', text: 'Dịch', onclick: (e) => doTranslate('rows', e.target) }),
      h('button', { class: 'btn small danger', text: 'Xoá', onclick: (e) => delRows([...T.picked.keys()], [...T.picked.values()], e.target) }),
      h('button', { class: 'btn small ghost', text: 'Bỏ tích', onclick: () => { T.picked.clear(); drawRows(); } }));
  }
  async function delRows(rows, texts, btn) {
    if (!await confirmBox(`Xoá ${rows.length} câu khỏi phụ đề (cả giọng lồng của câu đó)?`, 'Xoá')) return;
    await guard(btn, async () => {
      const r = await post(`/api/p/${enc}/lines/delete`, { rows, texts });
      if (r.status === 'error') throw new ApiErr(r.error);
      toast(r.result_msg || 'Đã xoá', 'ok'); T.picked.clear(); T.open = -1; loadLines(true);
    });
  }
  function drawRows() {
    fbox.querySelectorAll('[data-f]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.f === T.filter)));
    sub.textContent = `${fmtN(T.total)} câu · ${T.empty ? fmtN(T.empty) + ' câu chưa dịch' : 'đã dịch hết'}`;
    list.replaceChildren();
    if (!T.rows.length && !T.loading) list.append(h('div', { class: 'hint', text: T.total ? 'Không có câu nào khớp.' : 'Chưa có phụ đề – chạy bước 1 trước.' }));
    for (const r of T.rows) list.append(lineEl(r));
    more.classList.toggle('hidden', T.rows.length >= T.count);
    more.textContent = `Xem thêm (${fmtN(T.rows.length)} / ${fmtN(T.count)})`;
    drawPicked();
  }
  function lineEl(r) {
    const picked = T.picked.has(r.i);
    const ck = h('button', { class: 'ck', 'aria-pressed': String(picked), 'aria-label': `Tích câu ${r.i + 1}`, text: picked ? '✓' : '' });
    ck.onclick = () => { if (T.picked.has(r.i)) T.picked.delete(r.i); else T.picked.set(r.i, r.text); drawRows(); };
    const flags = (r.unsure ? ' · không chắc' : '') + (r.song ? ' · 🎵' : '') + (r.fit === 'long' ? ' · ⚠ dài' : '');
    const body = h('button', { class: 'bd', onclick: () => { T.open = T.open === r.i ? -1 : r.i; drawRows(); } },
      h('div', { class: 'mt', text: `#${r.i + 1} · ${fmtT1(r.start)} · ${r.spk || 'S1'}${flags}` }),
      h('div', { class: 'zh' + (r.unsure ? ' unsure' : ''), text: r.text }),
      h('div', { class: 'vi' + (r.trans ? '' : ' empty'), text: r.trans || '(chưa dịch)' }));
    const el = h('div', { class: 'ln' + (T.open === r.i ? ' open' : '') }, ck, h('div', { class: 'grow', style: { minWidth: 0, flex: 1 } }, body));
    if (T.open === r.i) {
      const ta = h('textarea', { rows: '2', 'aria-label': 'Sửa bản dịch' }); ta.value = r.trans;
      body.parentNode.append(h('div', { class: 'ed' }, ta, h('div', { class: 'wrap' },
        h('button', { class: 'btn small primary', text: 'Lưu', onclick: (e) => guard(e.target, async () => {
          const res = await post(`/api/p/${enc}/lines/edit`, { i: r.i, text: r.text, trans: ta.value });
          if (res.status === 'error') throw new ApiErr(res.error);
          r.trans = ta.value.trim(); T.open = -1; drawRows(); toast(res.result_msg || 'Đã lưu', 'ok');
        }) }),
        h('button', { class: 'btn small', text: 'Dịch lại', onclick: (e) => guard(e.target, async () => {
          await post(`/api/p/${enc}/translate`, { mode: 'rows', rows: [r.i], texts: [r.text] }); toast('Đã xếp việc dịch câu này'); poll();
        }) }),
        h('button', { class: 'btn small', text: 'Nghe', onclick: () => playAudio(`/api/p/${enc}/audio/line/${r.i}`) }),
        h('button', { class: 'btn small danger', text: 'Xoá', onclick: (e) => delRows([r.i], [r.text], e.target) }))));
      setTimeout(() => ta.focus(), 30);
    }
    return el;
  }
  async function loadLines(reset) {
    if (T.loading) return;
    T.loading = true;
    if (reset) { T.offset = 0; T.rows = []; }
    try {
      const d = await api(`/api/p/${enc}/lines?offset=${T.rows.length}&limit=60&filter=${T.filter}&q=${encodeURIComponent(T.q)}`);
      T.rows = T.rows.concat(d.rows); T.count = d.count; T.total = d.total; T.empty = d.empty;
    } catch (e) { err(e); } finally { T.loading = false; }
    drawRows();
  }
  let last = null;
  const drawRun = () => {
    const j = jobsOf(pid).find((x) => (x.status === 'running' || x.status === 'queued') && ['translate', 'redub', 'auto', 'recognize'].includes(x.kind));
    runBox.replaceChildren(...(j ? [jobLine(j)] : []));
    [btnEmpty, btnAll, btnPicked].forEach((b) => { b.disabled = !!j; });
    if (last && !j) {
      const done = S.jobs.find((x) => x.id === last.id);
      if (done) { tmsg.className = 'msg ' + (done.status === 'done' ? 'ok' : 'err'); tmsg.textContent = done.status === 'done' ? done.result_msg : done.error; }
      loadLines(true);
    }
    last = j || null;
  };
  S.screen = { onState: drawRun };
  drawRun(); loadLines(true);
}

// ================================================================== Lồng tiếng
const SPEEDS = [0.85, 0.9, 1.0, 1.1, 1.2];
async function dubScreen(pid) {
  const enc = encodeURIComponent(pid);
  const pg = page('Lồng tiếng', 'Đang tải…', { back: `#/p/${enc}` });
  let D, o;
  try { [D, o] = await Promise.all([api(`/api/p/${enc}/dub`), loadOptions(pid)]); } catch (e) { err(e); return; }
  pg.querySelector('.top .sub').textContent = D.dubbed ? 'Đã có bản lồng tiếng' : 'Chưa lồng tiếng';
  const voiceItems = () => [{ id: '', name: 'Theo từng người nói (bên dưới)' }, { id: '@auto', name: 'Tự gán giọng nam / nữ' }, ...o.voices];
  const vcard = h('section', { class: 'card' });
  const drawVoices = () => {
    vcard.replaceChildren(h('h2', { text: 'Giọng đọc' }),
      select('Bộ đọc', o.tts, D.engine, async (val) => {
        await post('/api/settings', { tts_engine: val }); D.engine = val;
        o = await loadOptions(pid); drawVoices();
      }));
    const all = select('Giọng cho tất cả', voiceItems(), D.voice_all || '', async (val) => {
      const r = await post(`/api/p/${enc}/voices`, { voice_all: val });
      if (r.status === 'error') throw new ApiErr(r.error);
      toast(r.result_msg || 'Đã lưu giọng', 'ok');
      D = await api(`/api/p/${enc}/dub`); drawVoices();
    });
    const test = (vid, spk) => h('button', { class: 'iconbtn', 'aria-label': 'Nghe thử giọng', onclick: (e) => guard(e.currentTarget, async () => {
      toast('Đang tạo giọng nghe thử…'); await playBlob(`/api/p/${enc}/voice_test`, { voice: vid(), speaker: spk || '' });
    }) }, icon('play'));
    vcard.append(h('div', { class: 'row', style: { alignItems: 'flex-end' } }, h('div', { class: 'grow' }, all), test(() => all.querySelector('select').value, (D.speakers[0] || {}).id)));
    if (!D.voice_all && D.speakers.length) {
      for (const s of D.speakers) {
        const sel = select('', [{ id: '', name: '(Giọng mặc định của bộ đọc)' }, ...o.voices], s.voice || '', async (val) => {
          const r = await post(`/api/p/${enc}/voices`, { speakers: { [s.id]: val } });
          if (r.status === 'error') throw new ApiErr(r.error);
          toast(`Giọng ${s.id}: đã lưu`, 'ok');
        });
        vcard.append(h('div', { class: 'spk' }, h('div', { class: 'who', text: `${s.id}${s.gender ? ' · ' + ({ male: 'nam', female: 'nữ' }[s.gender] || s.gender) : ''} · ${fmtN(s.n)} câu` }),
          sel, test(() => sel.value, s.id)));
      }
    }
  };
  drawVoices();
  pg.append(vcard);
  const V = D.values;
  const set = (k) => async (val) => { await post('/api/settings', { [k]: val }); };
  pg.append(h('section', { class: 'card' }, h('h2', { text: 'Khi câu đọc dài hơn chỗ trống' }),
    h('div', { class: 'grid2' },
      select('Cách xếp', o.overflow, V.dub_overflow, set('dub_overflow')),
      select('Tăng tốc tối đa', o.speedups.map((x) => ({ id: String(x), name: '×' + x.toFixed(2).replace('.', ',') })), String(V.tts_max_speedup), (val) => set('tts_max_speedup')(Number(val))),
      select('Tốc độ đọc', o.rates, V.tts_rate, set('tts_rate'))),
    sw('Phụ đề chạy theo giọng lồng', V.dub_subs_follow, set('dub_subs_follow')),
    sw('AI tự rút gọn câu quá dài', V.dub_auto_shorten, set('dub_auto_shorten'))));
  const runBtn = h('button', { class: 'btn primary block', text: D.dubbed ? 'Tạo lại bản lồng tiếng' : 'Tạo bản lồng tiếng' });
  const runBox = h('div', { class: 'lines' });
  const dmsg = h('div', { class: D.summary ? 'msg' : 'hidden', text: D.summary || '' });
  runBtn.onclick = (e) => guard(e.target, async () => {
    if (D.dubbed && !await confirmBox('Tạo lại bản lồng tiếng? Câu không đổi lời / giọng dùng lại giọng đã đọc.', 'Tạo lại')) return;
    await post(`/api/p/${enc}/run`, { step: 'dub' }); toast('Đã xếp việc lồng tiếng'); poll();
  });
  pg.append(runBtn, runBox, dmsg);
  if (D.has_audio) pg.append(h('section', { class: 'card' }, h('h2', { text: 'Nghe bản lồng' }),
    h('audio', { controls: true, preload: 'none', src: `/api/p/${enc}/audio/dub`, style: { width: '100%' } }),
    h('div', { class: 'hint', text: 'Chỉ giọng lồng (chưa trộn nhạc nền). Bản đầy đủ: xuất video ở bước 4.' })));
  // ---------- độ vừa thời gian
  const catBox = h('div', { class: 'cats' });
  const catBar = h('div', { class: 'hidden' });
  const catLines = h('div', { class: 'lines' });
  let curCat = '';
  const drawCats = () => {
    catBox.replaceChildren(...D.cats.map((c) => h('button', { class: 'cat', 'aria-pressed': String(curCat === c.id), onclick: () => { curCat = curCat === c.id ? '' : c.id; drawCats(); loadCat(); } },
      h('b', { class: 'fit-' + c.id, text: fmtN(c.n) }), c.label)));
    const c = D.cats.find((x) => x.id === curCat);
    catBar.className = c ? 'card' : 'hidden';
    if (!c) { catLines.replaceChildren(); return; }
    const act = (action, value) => async (e) => guard(e.target, async () => {
      const txt = { shorten: `AI rút gọn ${c.n} câu loại “${c.label}” rồi đọc lại giọng?`, redub: `Tạo lại giọng ${c.n} câu loại “${c.label}”?`,
        speed: value ? `Đặt tốc độ ×${value.toFixed(2)} cho ${c.n} câu?` : `Về tốc độ gốc cho ${c.n} câu?` }[action];
      if (!await confirmBox(txt)) return;
      await post(`/api/p/${enc}/dub/cat`, { cat: c.id, action, value }); toast('Đã xếp việc'); poll();
    });
    catBar.replaceChildren(h('div', { text: `Đã chọn ${fmtN(c.n)} câu loại “${c.label}”` }),
      h('div', { class: 'wrap' }, h('button', { class: 'btn small', text: 'AI rút gọn', onclick: act('shorten') }),
        h('button', { class: 'btn small', text: 'Tạo lại giọng', onclick: act('redub') })),
      h('div', { class: 'hint', text: 'Đổi tốc độ (đúng tốc độ, chỉ xếp lại – không đọc lại):' }),
      h('div', { class: 'wrap' }, ...SPEEDS.map((x) => h('button', { class: 'chip', text: '×' + x.toFixed(2).replace('.', ','), onclick: act('speed', x) })),
        h('button', { class: 'chip', text: 'Về gốc', onclick: act('speed', null) })));
  };
  async function loadCat() {
    if (!curCat) return;
    try {
      const d = await api(`/api/p/${enc}/lines?filter=${curCat}&limit=40`);
      catLines.replaceChildren(...[...d.rows.map((r) => h('div', { class: 'ln' },
        h('button', { class: 'ck', 'aria-label': `Nghe câu ${r.i + 1}`, text: '▶', onclick: () => playAudio(`/api/p/${enc}/audio/line/${r.i}`) }),
        h('div', { class: 'bd' }, h('div', { class: 'mt', text: `#${r.i + 1} · ${r.spk || 'S1'}${r.speed ? ' · ×' + Number(r.speed).toFixed(2) : ''}` }),
          h('div', { class: 'vi', text: r.trans || '(chưa dịch)' })))),
        d.count > d.rows.length ? h('div', { class: 'hint', text: `… và ${fmtN(d.count - d.rows.length)} câu nữa` }) : null].filter(Boolean));
    } catch (e) { err(e); }
  }
  if (D.dubbed) pg.append(h('section', { class: 'card' }, h('div', { class: 'head' }, h('h2', { text: 'Độ vừa thời gian' }), h('span', { class: 'hint', text: 'bấm loại để chọn' })), catBox), catBar, catLines);
  drawCats();
  let last = null;
  const drawRun = () => {
    const j = jobsOf(pid).find((x) => (x.status === 'running' || x.status === 'queued') && ['dub', 'redub', 'relayout', 'auto'].includes(x.kind));
    runBox.replaceChildren(...(j ? [jobLine(j)] : []));
    runBtn.disabled = !!j;
    if (last && !j) { dubScreen(pid); return; }
    last = j || null;
  };
  S.screen = { onState: drawRun };
  drawRun();
}

// ================================================================== Xuất video
async function exportScreen(pid) {
  const enc = encodeURIComponent(pid);
  const pg = page('Xuất video', 'Đang tải…', { back: `#/p/${enc}` });
  let E, o, d;
  try { [E, o, d] = await Promise.all([api(`/api/p/${enc}/export`), loadOptions(pid), api('/api/p/' + enc)]); } catch (e) { err(e); return; }
  pg.querySelector('.top .sub').textContent = [d.nt ? 'dịch xong' : 'chưa dịch', d.dubbed ? 'đã lồng tiếng' : 'chưa lồng tiếng'].join(' · ');
  const info = d.info || {};
  const srcR = info.width && info.height ? info.width / info.height : 16 / 9;
  const t = E.sample ? E.sample.t : Math.min((info.duration || 0) * 0.2, 30);
  const frameUrl = `/api/p/${enc}/frame?t=${t.toFixed(1)}&w=720`;
  const pv = h('div', { class: 'pv' }, h('div', { class: 'bgblur', style: { backgroundImage: `url("${frameUrl}")` } }),
    h('img', { class: 'fg', src: frameUrl, alt: 'Xem trước khung hình' }));
  const subEl = h('div', { class: 'sub' });
  pv.append(subEl);
  const pvCap = h('div', { class: 'hint', style: { textAlign: 'center' } });
  pg.append(pv, pvCap);
  const set = async (k, val) => {
    const r = await post(`/api/p/${enc}/export`, { [k]: val });
    if (r.status === 'error') throw new ApiErr(r.error);
    E[k] = val; drawPv();
  };
  function drawPv() {
    const ar = E.aspect === 'original' ? srcR : (() => { const [a, b] = E.aspect.split(':').map(Number); return a / b; })();
    pv.style.aspectRatio = String(ar);
    pv.style.width = ar < 1 ? `min(100%, ${Math.round(420 * ar)}px)` : '100%';
    subEl.replaceChildren();
    if (E.which !== 'none' && E.sample) {
      if (E.which === 'source') subEl.append(E.sample.text);
      else { subEl.append(E.sample.trans); if (E.which === 'both') subEl.append(h('small', { text: E.sample.text })); }
    }
    pvCap.textContent = `Xem trước khung ${fmtT(t)} · ${E.aspect === 'original' ? 'tỉ lệ gốc' : E.aspect}`;
    ratioBox.querySelectorAll('[data-r]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.r === E.aspect)));
    whichBox.querySelectorAll('[data-w]').forEach((x) => x.setAttribute('aria-checked', String(x.dataset.w === E.which)));
  }
  const whichBox = h('div', { class: 'wrap', role: 'radiogroup', 'aria-label': 'Ghép phụ đề' });
  for (const w of o.which) {
    const short = { trans: 'Chỉ ' + o.lang_name.replace(/^Tiếng /, 'tiếng '), both: 'Song ngữ', source: 'Chỉ tiếng Trung', none: 'Không ghép' }[w.id] || w.name;
    whichBox.append(h('button', { class: 'chip', role: 'radio', 'data-w': w.id, text: short, onclick: (e) => guard(e.target, () => set('which', w.id)) }));
  }
  const ratioBox = h('div', { class: 'wrap' }, ...o.aspects.map((a) => h('button', { class: 'chip', 'data-r': a.id, text: a.name, onclick: (e) => guard(e.target, () => set('aspect', a.id)) })));
  pg.append(h('section', { class: 'card' }, h('h2', { text: 'Phụ đề & âm thanh' }),
    h('div', { class: 'hint', text: 'Ghép phụ đề' }), whichBox,
    select('Phụ đề Trung cũ trên video', o.hardsub, E.hardsub, (v) => set('hardsub', v)),
    E.has_dub ? sw('Dùng bản lồng tiếng', E.dub, (v) => set('dub', v)) : h('div', { class: 'hint', text: 'Chưa có bản lồng tiếng – video giữ tiếng gốc.' }),
    sw('Xuất thêm bản dọc 9:16 (TikTok / Reels / Shorts)', E.extra916, (v) => set('extra916', v))));
  pg.append(h('section', { class: 'card' }, h('h2', { text: 'Khung hình & chất lượng' }),
    h('div', { class: 'hint', text: 'Tỉ lệ khung' }), ratioBox,
    h('div', { class: 'grid2' },
      select('Độ phân giải', o.resolutions, E.resolution, (v) => set('resolution', v)),
      select('Chất lượng', o.crfs, E.crf, (v) => set('crf', v), { keepEmpty: true }))));
  const expBtn = h('button', { class: 'btn primary block', text: 'Xuất video', disabled: !d.has_video });
  const runBox = h('div', { class: 'lines' });
  expBtn.onclick = (e) => guard(e.target, async () => { await post(`/api/p/${enc}/run`, { step: 'render' }); toast('Đã xếp việc xuất video – báo lên điện thoại khi xong'); poll(); });
  pg.append(expBtn, runBox);
  const outs = h('section', { class: 'card' }, h('h2', { text: 'Bản đã xuất' }));
  if (!E.outputs.length) outs.append(h('div', { class: 'hint', text: 'Chưa xuất bản nào.' }));
  for (const f of E.outputs) {
    const url = `/api/p/${enc}/out/${f.idx}`;
    outs.append(h('div', { class: 'out' }, h('div', { class: 'grow' }, h('div', { class: 'nm', text: f.name }),
      h('div', { class: 'hint', text: [fmtMB(f.size), f.ratio, f.time ? ago(f.time) : ''].filter(Boolean).join(' · ') })),
      f.kind === 'video' ? h('button', { class: 'btn small', text: 'Xem', onclick: () => playVideo(url, f.name) }) : null,
      h('button', { class: 'btn small', text: 'Tải về', onclick: () => download(url + '?dl=1', f.name) })));
  }
  const vids = E.outputs.filter((f) => f.kind === 'video');
  outs.append(h('div', { class: 'wrap' },
    h('button', { class: 'btn small', text: 'Tải SRT ' + o.lang_name.toLowerCase(), disabled: !d.nt, onclick: () => download(`/api/p/${enc}/srt`, '') }),
    h('button', { class: 'btn small', text: 'Đăng video…', disabled: !vids.length, onclick: () => {
      const f = vids[0];
      if (NATIVE && NATIVE.download) { download(`/api/p/${enc}/out/${f.idx}?dl=1`, f.name, true); return; }
      sheet('Đăng video', h('div', { text: 'Tải video về điện thoại rồi đăng bằng app TikTok / YouTube / Facebook (app Subvideo trên Android có nút chia sẻ thẳng).' }),
        h('button', { class: 'btn primary', text: 'Tải ' + f.name, onclick: () => download(`/api/p/${enc}/out/${f.idx}?dl=1`, f.name) }),
        h('div', { class: 'hint', text: 'Đăng bằng tài khoản đã đăng nhập trên máy tính: tool → tab Xuất → Đăng video.' }));
    } })));
  pg.append(outs);
  drawPv();
  let last = null;
  const drawRun = () => {
    const j = jobsOf(pid).find((x) => (x.status === 'running' || x.status === 'queued') && ['render', 'inpaint'].includes(x.kind));
    runBox.replaceChildren(...(j ? [jobLine(j)] : []));
    expBtn.disabled = !!j || !d.has_video;
    if (last && !j) { exportScreen(pid); return; }
    last = j || null;
  };
  S.screen = { onState: drawRun };
  drawRun();
}

// ================================================================== điều hướng
function route() {
  const hsh = location.hash || '#/';
  player().pause();
  document.querySelectorAll('.sheet-bg').forEach((x) => x.remove());
  const m = hsh.match(/^#\/p\/([^/]+)(?:\/(region|translate|dub|export))?$/);
  if (m) {
    const pid = decodeURIComponent(m[1]);
    ({ region: regionScreen, translate: translateScreen, dub: dubScreen, export: exportScreen }[m[2]] || projectScreen)(pid);
  } else homeScreen();
}
window.addEventListener('hashchange', () => { if (S.me) route(); });
// app Android: chia sẻ link từ app Bilibili / Douyin / YouTube sang Subvideo -> điền sẵn ô link ở Trang chủ
window.svShared = (text) => {
  store('sv_links', ((store('sv_links') || '').trim() + '\n' + text).trim());
  if (location.hash && location.hash !== '#/') location.hash = '#/'; else if (S.me) route();
  toast('Đã dán link được chia sẻ – bấm "Tải về & dịch tự động"', 'ok');
};

async function boot() {
  // app Android mở trang kèm mã ghép (#pair=123456&name=…) hoặc thẻ đã có (#tok=…) -> dùng rồi xoá khỏi địa chỉ
  const hp = new URLSearchParams(location.hash.startsWith('#pair=') || location.hash.startsWith('#tok=') ? location.hash.slice(1) : '');
  if (hp.get('pair')) {
    history.replaceState(null, '', location.pathname + '#/');
    try {
      const d = await api('/api/pair', { body: { code: hp.get('pair'), name: hp.get('name') || 'Điện thoại' }, noAuthRedirect: true });
      S.token = d.token; store('sv_t', d.token);
      if (NATIVE && NATIVE.setToken) NATIVE.setToken(location.origin, d.token);
      toast('Đã ghép máy', 'ok');
    } catch (e) { showLogin(e.message); return; }
  } else if (hp.get('tok')) {
    history.replaceState(null, '', location.pathname + '#/');
    S.token = hp.get('tok'); store('sv_t', S.token);
  }
  if (S.token) { try { await api('/api/session', { body: { token: S.token }, noAuthRedirect: true }); } catch (e) { if (e.code === 401) { S.token = ''; store('sv_t', null); } } }
  try {
    S.me = await api('/api/me', { noAuthRedirect: true });
  } catch (e) {
    if (e.code === 401) { S.token = ''; store('sv_t', null); showLogin(); }
    else { $('#app').replaceChildren(h('div', { class: 'page login' }, h('div', { class: 'offline', text: e.message }),
      h('button', { class: 'btn', text: 'Thử lại', onclick: () => boot() }))); }
    return;
  }
  route();
  poll();
}
boot();
