'use strict';

let TOKEN = sessionStorage.getItem('wpn_admin') || '';
const $ = id => document.getElementById(id);
const fmt = n => (n === null || n === undefined ? '--' : Number(n).toLocaleString('zh-CN'));
const ms = n => (n === null || n === undefined ? '--' : (Number(n) >= 1000 ? (Number(n) / 1000).toFixed(2) + ' s' : Math.round(n) + ' ms'));

function adminUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) throw new Error('invalid_url_origin');
  if (url.protocol !== window.location.protocol) throw new Error('invalid_url_protocol');
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  return url.pathname + url.search;
}

function textEl(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = String(text ?? '');
  if (className) node.className = className;
  return node;
}

function td(text, className) {
  return textEl('td', text, className);
}

function row(cells) {
  const tr = document.createElement('tr');
  cells.forEach(cell => tr.appendChild(cell));
  return tr;
}

function emptyRow(colspan, message) {
  const tr = document.createElement('tr');
  const cell = td(message);
  cell.colSpan = colspan;
  cell.style.color = 'var(--muted)';
  tr.appendChild(cell);
  return tr;
}

function pill(text, ok) {
  return textEl('span', text, 'pill ' + (ok ? 'ok' : 'bad'));
}

function login() {
  const value = $('tokenInput').value.trim();
  if (!value) {
    $('loginErr').textContent = '请输入管理 Token';
    return;
  }
  TOKEN = value;
  sessionStorage.setItem('wpn_admin', value);
  $('loginErr').textContent = '';
  $('login').classList.add('hidden');
  refresh();
}

function logout() {
  sessionStorage.removeItem('wpn_admin');
  TOKEN = '';
  window.location.reload();
}

async function api(path, opts = {}) {
  const headers = {'X-Admin-Token': TOKEN, ...(opts.headers || {})};
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(adminUrl(path), {...opts, headers});
  if (response.status === 401) {
    $('login').classList.remove('hidden');
    throw new Error('admin_auth_required');
  }
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function drawSpark(canvas, values, color) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#1b2c46';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = h * i / 4;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
  if (!values.length) return;
  const max = Math.max(1, ...values);
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = values.length === 1 ? 0 : w * i / (values.length - 1);
    const y = h - (v / max) * (h - 8) - 4;
    if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fillStyle = color.replace('rgb', 'rgba').replace(')', ',.12)');
  ctx.fill();
}

function renderKeys(keys) {
  const body = $('keyRows');
  const rows = keys.map(k => {
    const quota = k.quota_tokens ? `${fmt(k.used_tokens)} / ${fmt(k.quota_tokens)}` : `${fmt(k.used_tokens)} / 不限`;
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'ghost';
    toggle.textContent = k.deleted ? '已删除' : (k.enabled ? '停用' : '启用');
    toggle.disabled = Boolean(k.deleted);
    toggle.dataset.keyId = k.id;
    toggle.dataset.enabled = k.enabled ? '1' : '0';
    toggle.addEventListener('click', () => {
      toggleKey(toggle.dataset.keyId, toggle.dataset.enabled !== '1');
    });
    const action = document.createElement('td');
    action.appendChild(toggle);
    const status = k.deleted ? '已删除' : (k.enabled ? '启用' : '停用');
    return row([
      td(k.name), td(k.scope || 'production', 'mono'), td(k.model, 'mono'), td(quota),
      td(k.rpm_limit || '不限'), td(k.concurrent_limit),
      (() => { const cell = document.createElement('td'); cell.appendChild(pill(status, Boolean(k.enabled) && !k.deleted)); return cell; })(),
      td(k.last_used_at || '--'), action,
    ]);
  });
  body.replaceChildren(...(rows.length ? rows : [emptyRow(9, '暂无 Key，请先创建')]));
}

function renderRequests(requests) {
  const body = $('reqRows');
  const rows = requests.map(r => {
    const rid = textEl('span', String(r.id || '').slice(0, 14) + '…', 'mono');
    rid.title = String(r.id || '');
    const idCell = document.createElement('td');
    idCell.appendChild(rid);
    const statusCell = document.createElement('td');
    statusCell.appendChild(pill(String(r.status), Number(r.status) < 400));
    return row([
      td(String(r.ts || '').slice(11, 19)), idCell, td(r.consumer_name || r.consumer),
      td(r.key_scope || 'production'), td(r.model, 'mono'), td(r.upstream_total_tokens ?? r.total_tokens),
      td(r.estimated_tokens), td(r.billed_tokens), td(r.usage_source || 'none'),
      td(ms(r.ttft_ms)), td(ms(r.latency_ms)), statusCell,
      td(r.stream ? '是' : '否'), td(r.tool_calls || 0),
    ]);
  });
  body.replaceChildren(...(rows.length ? rows : [emptyRow(14, '暂无请求')]));
}

function renderErrors(errors) {
  const body = $('errRows');
  const rows = errors.map(e => {
    const rid = textEl('span', String(e.id || '').slice(0, 14) + '…', 'mono');
    rid.title = String(e.id || '');
    const idCell = document.createElement('td');
    idCell.appendChild(rid);
    const statusCell = document.createElement('td');
    statusCell.appendChild(pill(String(e.status), false));
    return row([
      td(String(e.ts || '').replace('T', ' ').slice(0, 19)), idCell,
      td(e.consumer_name || e.consumer), statusCell, td(e.error || ''),
      td(e.estimated_tokens || 0), td(e.billed_tokens || 0), td(ms(e.latency_ms)),
    ]);
  });
  body.replaceChildren(...(rows.length ? rows : [emptyRow(8, '暂无错误')]));
}

async function refresh() {
  if (!TOKEN) return;
  try {
    const [ov, st, keys, reqs, errs, pers] = await Promise.all([
      api('/admin/api/overview'),
      api('/admin/api/status'),
      api('/admin/api/keys'),
      api(adminUrl('/admin/api/requests', {limit: 50})),
      api(adminUrl('/admin/api/errors', {limit: 50})),
      api('/admin/api/persistence'),
    ]);
    const o = ov.overview;
    $('kTotal').textContent = fmt(o.total_requests);
    $('kTotalHint').textContent = `成功 ${fmt(o.ok_requests)} · 错误 ${fmt(o.error_requests)} · 取消 ${fmt(o.cancelled)}`;
    $('kRpm').textContent = fmt(o.rpm);
    $('kPrompt').textContent = fmt(o.prompt_tokens);
    $('kCompletion').textContent = fmt(o.completion_tokens);
    $('kTokens').textContent = fmt(o.total_tokens);
    $('kBilled').textContent = fmt(o.billed_tokens);
    $('kSuccess').textContent = o.success_rate === null ? '--' : (o.success_rate * 100).toFixed(2) + '%';
    $('kSuccessHint').textContent = `成功 ${fmt(o.ok_requests)} / 总 ${fmt(o.total_requests)}`;
    $('kLatency').textContent = ms(o.avg_latency_ms);
    $('kLatencyHint').textContent = `P95 ${ms(o.latency_p95_ms)}`;
    $('kTtft').textContent = ms(o.ttft_p95_ms);
    $('kTtftHint').textContent = `P50 ${ms(o.ttft_p50_ms)}`;

    const stt = st.readiness;
    $('modelState').textContent = stt.state;
    const dot = $('modelDot');
    dot.className = 'dot ' + (stt.state === 'ready' ? 'ok' : (stt.state === 'offline' ? 'bad' : 'warn'));
    $('stState').textContent = stt.state;
    $('stDetail').textContent = `${stt.upstream} · 进程 ${stt.process_running ? '运行' : '未运行'} · 预热 ${stt.warmup_done ? '完成' : '未完成'}`;
    $('ppuSource').textContent = st.ppu.available ? 'ppu-smi 实时' : 'ppu-smi 不可用';
    [0, 1].forEach(i => {
      const card = $('gpu' + i);
      const g = (st.ppu.cards || [])[i];
      if (!g) {
        card.querySelector('.row span:last-child').textContent = '--';
        return;
      }
      const pct = g.mem_total_mib ? Math.min(100, 100 * g.mem_used_mib / g.mem_total_mib) : 0;
      card.querySelector('.bar > i').style.width = pct.toFixed(1) + '%';
      const rows = card.querySelectorAll('.row');
      rows[0].querySelector('span').textContent = `#${g.index} ${g.name}`;
      rows[1].querySelector('span:last-child').textContent = g.mem_used_mib === null ? '--' : `${fmt(g.mem_used_mib)} / ${fmt(g.mem_total_mib)} MiB (${pct.toFixed(1)}%)`;
      rows[2].querySelector('span:last-child').textContent = `${g.util_pct ?? '--'}% / ${g.temp_c ?? '--'}C / ${g.power_w ?? '--'}W`;
    });

    const pts = ov.timeseries.points || [];
    drawSpark($('sparkRpm'), pts.map(p => p.requests), 'rgb(77,163,255)');
    drawSpark($('sparkTokens'), pts.map(p => p.total_tokens), 'rgb(46,204,143)');

    $('keyCount').textContent = `${keys.keys.length} 个 Key`;
    renderKeys(keys.keys);
    $('reqCount').textContent = `${reqs.requests.length} 条`;
    renderRequests(reqs.requests);
    $('errCount').textContent = `${errs.errors.length} 条`;
    renderErrors(errs.errors);

    $('dbPath').textContent = pers.db_path;
    $('dbMode').textContent = pers.db_mode;
    $('backupDir').textContent = pers.backup_dir;
    $('backupEncryption').textContent = pers.backup_encryption;
    $('backupPassSource').textContent = pers.backup_passphrase_source;
    $('lastBackup').textContent = pers.last_backup ? `${pers.last_backup.at} · ${fmt(pers.last_backup.size)} B · ${pers.last_backup.encrypted ? '已加密' : '未加密'}` : '尚无';
    $('backupCount').textContent = fmt(pers.backup_count);
    $('legacyCount').textContent = fmt(pers.legacy_plaintext_backup_count);
    $('lossWindow').textContent = `${pers.loss_window_seconds} 秒`;
    $('restoredFrom').textContent = pers.restored_from || '未触发';
    $('updated').textContent = '更新 ' + new Date().toLocaleTimeString('zh-CN');
  } catch (e) {
    console.error(e);
  }
}

async function createKey() {
  const body = {
    name: $('newName').value.trim() || 'local',
    scope: $('newScope').value,
    quota_tokens: Number($('newQuota').value),
    rpm_limit: Number($('newRpm').value),
    concurrent_limit: Number($('newConc').value),
  };
  const result = await api('/admin/api/keys', {method: 'POST', body: JSON.stringify(body)});
  const box = $('newKeyBox');
  const title = textEl('div', '新 Key（仅显示一次，刷新后不可再取回）：');
  const value = textEl('div', result.key, 'mono');
  value.style.marginTop = '6px';
  value.style.wordBreak = 'break-all';
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'copy';
  copy.textContent = '复制';
  copy.addEventListener('click', () => navigator.clipboard.writeText(result.key));
  value.appendChild(copy);
  box.replaceChildren(title, value);
  box.classList.remove('hidden');
  refresh();
}

async function toggleKey(id, enabled) {
  const path = adminUrl('/admin/api/keys/' + encodeURIComponent(id));
  await api(path, {method: 'PATCH', body: JSON.stringify({enabled})});
  refresh();
}

$('loginBtn').addEventListener('click', login);
$('logoutBtn').addEventListener('click', logout);
$('createKeyBtn').addEventListener('click', createKey);
$('tokenInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') login();
});
if (TOKEN) $('login').classList.add('hidden');
refresh();
setInterval(() => {
  if (!document.hidden) refresh();
}, 5000);
