// Sends the current tab's link to the Video Downloader app on this Mac.
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const APP_URL = 'videodownloader://open';
let cfg = { port: 8765, token: '' };
let tabUrl = '';
let mode = 'video';

function show(state) {
  for (const s of ['ready', 'job', 'done', 'msg']) $('s-' + s).hidden = s !== state;
}

function message(title, text, action) {
  $('msg-title').textContent = title;
  $('msg-text').textContent = text || '';
  const b = $('msg-action');
  b.hidden = !action;
  if (action) { b.textContent = action.label; b.onclick = action.run; b.className = 'btn' + (action.primary ? ' btn-primary' : ''); }
  show('msg');
}

const openOptions = { label: 'Open extension options', primary: true, run: () => chrome.runtime.openOptionsPage() };
const openApp = { label: 'Open Video Downloader', primary: true, run: () => chrome.tabs.create({ url: APP_URL }) };

function notRunning() {
  message('Video Downloader isn’t running', 'Open it, then try again.', openApp);
}

async function call(path, body) {
  let res;
  try {
    res = await fetch(`http://127.0.0.1:${cfg.port}${path}`, {
      method: body ? 'POST' : 'GET',
      headers: { 'X-VD-Token': cfg.token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (_) {
    throw { code: 'offline' };
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw data.error || { code: 'http', title: 'The app returned an error', message: `HTTP ${res.status}` };
  return data;
}

function handle(e) {
  if (e.code === 'offline') return notRunning();
  if (e.code === 'token') return message('Not connected', 'Paste the token from Video Downloader › Settings into this extension’s options.', openOptions);
  message(e.title || 'Something went wrong', e.message || '', null);
}

function setMode(m) {
  mode = m;
  document.querySelectorAll('[data-mode]').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.mode === m)));
  $('send').textContent = m === 'video' ? 'Download video' : 'Download audio';
}

function renderJob(j) {
  const pct = typeof j.percent === 'number' ? Math.floor(j.percent) : null;
  $('job-label').textContent = j.label || 'Sending…';
  $('job-pct').textContent = pct === null ? '' : pct + '%';
  $('job-fill').parentElement.classList.toggle('indeterminate', pct === null);
  $('job-fill').style.width = pct === null ? '' : pct + '%';
  show('job');
}

async function follow(id, folder) {
  for (;;) {
    let j;
    try { j = await call('/api/ext/jobs/' + id); } catch (e) { return handle(e); }
    if (j.state === 'done') {
      $('done-folder').textContent = folder || 'your Downloads';
      $('done-name').textContent = j.result?.name || '';
      chrome.storage.local.remove('job');
      return show('done');
    }
    if (j.state === 'error') { chrome.storage.local.remove('job'); return handle(j.error || {}); }
    if (j.state === 'canceled') { chrome.storage.local.remove('job'); return message('Download canceled', 'It was canceled in the app.', null); }
    renderJob(j);
    await sleep(500);
  }
}

async function send() {
  $('send').disabled = true;
  renderJob({ label: 'Sending…' });
  try {
    const j = await call('/api/ext/download', { url: tabUrl, mode });
    chrome.storage.local.set({ job: { id: j.id, url: tabUrl, folder: j.folder_name } });
    follow(j.id, j.folder_name);
  } catch (e) {
    handle(e);
  } finally {
    $('send').disabled = false;
  }
}

(async () => {
  const stored = await chrome.storage.local.get(['port', 'token', 'job', 'mode']);
  cfg = { port: Number(stored.port) || 8765, token: stored.token || '' };
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabUrl = tab?.url || '';

  let host = '';
  try { host = new URL(tabUrl).host.replace(/^www\./, ''); } catch (_) { /* not a URL */ }
  $('page').textContent = tab?.title ? `${tab.title} · ${host}` : host;

  if (!/^https?:\/\//i.test(tabUrl)) {
    $('page').textContent = '';
    return message('Nothing to download here', 'Open a page with a video, like a YouTube or Instagram post, then click the extension again.', null);
  }
  if (!cfg.token) return message('Not connected', 'Paste the token from Video Downloader › Settings into this extension’s options.', openOptions);

  try { await call('/api/ext/ping'); } catch (e) { return handle(e); }

  // Reopened while this tab's download is still running: pick it back up.
  if (stored.job && stored.job.url === tabUrl) return follow(stored.job.id, stored.job.folder);

  setMode(stored.mode === 'audio' ? 'audio' : 'video');
  show('ready');
  $('send').focus();
})();

document.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => {
  setMode(b.dataset.mode);
  chrome.storage.local.set({ mode });
}));
document.querySelector('.seg').addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  const next = mode === 'video' ? 'audio' : 'video';
  setMode(next);
  document.querySelector(`[data-mode=${next}]`).focus();
  e.preventDefault();
});
$('send').addEventListener('click', send);
$('done-open').addEventListener('click', () => chrome.tabs.create({ url: APP_URL }));
