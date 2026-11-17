(() => {
  const $ = (id) => document.getElementById(id);

  const S = {
    view: 'empty',        // empty, fetching, ready, job, done, error, missing, settings
    info: null,
    url: '',
    mode: 'video',
    afmt: 'mp3',
    job: null,
    doneJob: null,
    jobResult: null,
    lastAction: null,     // 'info' or 'download', for Retry
    returnTo: 'empty',
    settings: { folder: '', cookies: 'off' },
    env: null,
  };

  // --- API ---------------------------------------------------------------
  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    };
    let res;
    try {
      res = await fetch(path, opts);
    } catch (_) {
      throw { title: 'The app stopped', message: 'Video Downloader is not running. Double-click start.command, then retry.' };
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw data.error || { title: 'The app returned an error', message: `HTTP ${res.status}. Retry, or restart the app with start.command.` };
    return data;
  }

  // --- Formatting --------------------------------------------------------
  function fmtDuration(s) {
    if (!s && s !== 0) return '';
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const mm = h ? String(m).padStart(2, '0') : String(m);
    return (h ? h + ':' : '') + mm + ':' + String(sec).padStart(2, '0');
  }
  function fmtBytes(n) {
    if (!n) return '–';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
    return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
  }
  function fmtWhen(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(now) - day(d)) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  const CODEC = { h264: 'H.264', aac: 'AAC', mp3: 'MP3', hevc: 'HEVC', vp9: 'VP9', opus: 'Opus' };

  // --- View switching ----------------------------------------------------
  function show(view) {
    S.view = view;
    const vis = (id, on) => { $(id).hidden = !on; };
    const inFlow = !['missing', 'settings'].includes(view);
    vis('v-env', view === 'missing');
    vis('v-settings', view === 'settings');
    vis('v-main', inFlow);
    vis('v-recent', inFlow);
    vis('open-settings', view !== 'settings');
    vis('v-skeleton', view === 'fetching');
    vis('v-card', ['ready', 'job', 'done'].includes(view));
    vis('v-options', view === 'ready');
    vis('v-progress', view === 'job');
    vis('v-done', view === 'done');
    vis('v-error', view === 'error');
    $('url').disabled = view === 'job';
  }

  // --- Environment -------------------------------------------------------
  async function checkEnv() {
    try {
      S.env = await api('/api/env');
    } catch (e) {
      showError(e, null);
      return false;
    }
    $('ytdlp-version').textContent = 'Version ' + S.env.ytdlp;
    if (S.env.ok) return true;
    $('env-cmd').textContent = S.env.command;
    const rows = [['yt-dlp', S.env.ytdlp], ['ffmpeg', S.env.ffmpeg], ['ffprobe', S.env.ffprobe]];
    $('env-list').replaceChildren(...rows.map(([name, v]) => {
      const li = document.createElement('li');
      const a = document.createElement('span'); a.textContent = name;
      const b = document.createElement('span'); b.className = v ? 'ok' : 'missing'; b.textContent = v || 'Not found';
      li.append(a, b);
      return li;
    }));
    show('missing');
    return false;
  }

  // --- Fetch metadata ----------------------------------------------------
  async function fetchInfo(url) {
    S.url = url.trim();
    S.lastAction = 'info';
    S.info = null;
    show('fetching');
    try {
      const info = await api('/api/info', { url: S.url });
      if (S.url !== $('url').value.trim()) return; // a newer link was pasted meanwhile
      S.info = info;
      renderCard(info);
      if (!info.has_video) S.mode = 'audio';
      applyMode();
      show('ready');
      $('download').focus({ preventScroll: true });
    } catch (e) {
      showError(e, 'info');
    }
  }

  function renderCard(info) {
    $('media-title').textContent = info.title;
    const sub = [info.uploader, fmtDuration(info.duration)].filter(Boolean);
    $('media-sub').replaceChildren(...sub.map((t) => { const s = document.createElement('span'); s.textContent = t; return s; }));
    $('media-site').textContent = info.site || '';

    const thumb = $('thumb');
    thumb.querySelector('img')?.remove();
    if (info.thumbnail) {
      const img = document.createElement('img');
      img.alt = '';
      img.referrerPolicy = 'no-referrer';
      img.addEventListener('error', () => img.remove());
      img.src = info.thumbnail;
      thumb.prepend(img);
    }
    $('thumb-time').hidden = !info.duration;
    $('thumb-time').textContent = fmtDuration(info.duration);

    const sel = $('res');
    const opts = [['best', 'Best (up to 1080p)'], ...info.heights.map((h) => [String(h), h + 'p'])];
    sel.replaceChildren(...opts.map(([v, label]) => { const o = document.createElement('option'); o.value = v; o.textContent = label; return o; }));
    $('mode').querySelector('[data-mode=video]').disabled = !info.has_video;
  }

  // --- Mode controls -----------------------------------------------------
  function setRadio(group, attr, value) {
    group.querySelectorAll('[role=radio]').forEach((b) => {
      const on = b.dataset[attr] === value;
      b.setAttribute('aria-checked', on);
      b.tabIndex = on ? 0 : -1;
    });
  }
  function applyMode() {
    setRadio($('mode'), 'mode', S.mode);
    setRadio($('afmt'), 'afmt', S.afmt);
    $('row-res').hidden = S.mode !== 'video';
    $('row-afmt').hidden = S.mode !== 'audio';
    $('download').textContent = S.mode === 'video' ? 'Download video' : `Download ${S.afmt.toUpperCase()}`;
    $('opt-note').textContent = S.mode === 'video'
      ? 'MP4, H.264 and AAC. Plays in QuickTime and Quick Look.'
      : S.afmt === 'mp3' ? 'MP3 at 192 kbps, title saved in the file' : 'AAC in M4A, title saved in the file';
  }

  // --- Download job ------------------------------------------------------
  async function startDownload() {
    S.lastAction = 'download';
    S.canceling = false;
    const height = $('res').value === 'best' ? 'best' : Number($('res').value);
    const body = { url: S.url, mode: S.mode, height, audio_format: S.afmt };
    $('prog-note').textContent = S.mode === 'video'
      ? ($('res').value === 'best' ? 'Up to 1080p' : $('res').value + 'p') + ', MP4'
      : S.afmt.toUpperCase();
    renderJob({ state: 'fetching', label: 'Fetching', percent: null });
    show('job');
    $('cancel').disabled = false;
    $('cancel').focus({ preventScroll: true });
    try {
      const job = await api('/api/download', body);
      S.job = job.id;
      poll(job.id);
    } catch (e) {
      showError(e, 'download');
    }
  }

  async function poll(id) {
    while (S.job === id) {
      let j;
      try { j = await api('/api/jobs/' + id); } catch (e) { showError(e, 'download'); return; }
      if (S.job !== id) return;
      if (j.state === 'done') { S.job = null; S.doneJob = id; finish(j.result); return; }
      if (j.state === 'error') { S.job = null; showError(j.error, 'download'); return; }
      if (j.state === 'canceled') { S.job = null; canceled(); return; }
      renderJob(j);
      await new Promise((r) => setTimeout(r, 400));
    }
  }

  function setSteps(current, reencode) {
    const order = ['fetching', 'downloading', 'converting', 'done'];
    const idx = order.indexOf(current);
    $('steps').querySelectorAll('li').forEach((li, i) => {
      li.className = i < idx ? 'done' : i === idx ? 'current' + (reencode ? ' render' : '') : '';
      if (i === idx) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
    });
  }

  function renderJob(j) {
    const reencode = j.state === 'converting' && /QuickTime/.test(j.label || '');
    setSteps(j.state, reencode);
    const p = $('v-progress');
    p.classList.toggle('is-render', j.state === 'converting');
    const indeterminate = j.percent === null || j.percent === undefined;
    p.classList.toggle('is-indeterminate', indeterminate);
    $('prog-label').textContent = S.canceling ? 'Canceling…' : j.label || 'Fetching';
    const pct = indeterminate ? 0 : j.percent;
    $('prog-pct').textContent = indeterminate ? '' : Math.floor(pct) + '%';
    $('bar-fill').style.width = pct + '%';
    $('bar-head').style.left = pct + '%';
    if (indeterminate) $('bar').removeAttribute('aria-valuenow'); else $('bar').setAttribute('aria-valuenow', Math.floor(pct));
    $('prog-stats').hidden = j.state !== 'downloading';
    $('st-speed').textContent = j.speed ? fmtBytes(j.speed) + '/s' : '–';
    $('st-eta').textContent = j.eta || j.eta === 0 ? fmtDuration(j.eta) : '–';
    $('st-size').textContent = fmtBytes(j.total);
  }

  function finish(r) {
    S.jobResult = r;
    $('done-title').textContent = 'Saved to ' + r.folder.split('/').pop();
    $('done-file').textContent = r.name;
    const chips = r.kind === 'video'
      ? [CODEC[r.vcodec] || r.vcodec, r.acodec ? CODEC[r.acodec] || r.acodec : 'No audio', r.height ? r.height + 'p' : null, fmtBytes(r.size)]
      : [r.ext, fmtBytes(r.size)];
    $('done-checks').replaceChildren(...chips.filter(Boolean).map((t) => { const c = document.createElement('span'); c.className = 'chip'; c.textContent = t; return c; }));
    setSteps('done');
    show('done');
    $('done-reveal').focus({ preventScroll: true });
    loadRecent();
  }

  function canceled() {
    show('ready');
    $('opt-note').textContent = 'Download canceled. Partial files were removed.';
  }

  async function cancelJob() {
    if (!S.job) return;
    S.canceling = true;
    $('cancel').disabled = true;
    $('prog-label').textContent = 'Canceling…';
    try { await api(`/api/jobs/${S.job}/cancel`, {}); } catch (_) { /* poll reports the final state */ }
  }

  // --- Errors ------------------------------------------------------------
  function showError(e, action) {
    S.lastAction = action;
    $('err-title').textContent = e.title || 'The app returned an error';
    $('err-message').textContent = e.message || '';
    $('err-detail').textContent = e.detail || '';
    $('err-details').hidden = !e.detail;
    $('err-details').open = false;
    $('err-settings').hidden = !['ig_rate_limit', 'login_required', 'cookies'].includes(e.code);
    $('retry').hidden = !action;
    show('error');
    $('retry').focus({ preventScroll: true });
  }

  function retry() {
    if (S.lastAction === 'download' && S.info) { show('ready'); startDownload(); }
    else if (S.url) fetchInfo(S.url);
  }

  // --- Recent ------------------------------------------------------------
  async function loadRecent() {
    let items = [];
    try { items = await api('/api/recent'); } catch (_) { /* keep the list empty */ }
    const ul = $('recent');
    if (!items.length) {
      const li = document.createElement('li');
      li.className = 'empty-recent';
      li.textContent = 'Finished downloads appear here';
      ul.replaceChildren(li);
      return;
    }
    ul.replaceChildren(...items.map((r, i) => {
      const li = document.createElement('li');
      if (!r.exists) li.className = 'missing';
      const meta = r.kind === 'video'
        ? [r.ext, r.height ? r.height + 'p' : null, fmtBytes(r.size)]
        : [r.ext, fmtBytes(r.size)];
      meta.push(r.exists ? fmtWhen(r.at) : 'Moved or deleted');
      li.innerHTML = `
        <svg class="icon recent-kind" aria-hidden="true"><use href="#i-${r.kind === 'audio' ? 'wave' : 'film'}"/></svg>
        <div class="recent-main"><p class="recent-title"></p><p class="recent-meta"></p></div>
        <div class="recent-actions">
          <button class="btn btn-quiet btn-icon" data-act="reveal" title="Show in Finder"><svg class="icon"><use href="#i-folder"/></svg></button>
          <button class="btn btn-quiet btn-icon" data-act="open" title="Open"><svg class="icon"><use href="#i-play"/></svg></button>
        </div>`;
      li.querySelector('.recent-title').textContent = r.title;
      li.querySelector('.recent-title').title = r.name;
      li.querySelector('.recent-meta').replaceChildren(...meta.filter(Boolean).map((t) => { const s = document.createElement('span'); s.textContent = t; return s; }));
      li.querySelectorAll('[data-act]').forEach((b) => {
        b.disabled = !r.exists;
        b.setAttribute('aria-label', (b.dataset.act === 'reveal' ? 'Show in Finder: ' : 'Open: ') + r.title);
        b.addEventListener('click', () => fileAction(b.dataset.act, { recent: i }));
      });
      return li;
    }));
  }

  async function fileAction(act, body) {
    try { await api('/api/' + act, body); }
    catch (e) { loadRecent(); showError({ title: 'File not found', message: e.message }, null); }
  }

  // --- Settings ----------------------------------------------------------
  const COOKIE_HELP = {
    off: 'For Instagram and other sites that need a login. Log in to the site in that browser first.',
    safari: 'Safari keeps cookies in a protected folder. Give Terminal Full Disk Access in System Settings › Privacy & Security, then restart the app.',
    chrome: 'Uses your logged-in Chrome session. macOS may ask to allow access to “Chrome Safe Storage”: choose Always Allow.',
    arc: 'Uses your logged-in Arc session. macOS may ask to allow access to “Arc Safe Storage”: choose Always Allow.',
    firefox: 'Uses your logged-in Firefox session. Log in to the site in Firefox first.',
  };

  async function loadSettings() {
    try { S.settings = await api('/api/settings'); } catch (_) { return; }
    $('folder').value = S.settings.folder;
    setRadio($('cookies'), 'b', S.settings.cookies);
    $('cookie-help').textContent = COOKIE_HELP[S.settings.cookies];
  }

  let savedTimer;
  async function saveSetting(patch) {
    try {
      S.settings = await api('/api/settings', patch);
      $('saved').textContent = 'Saved';
      $('folder-help').textContent = 'Created if it doesn’t exist';
      $('folder-help').style.color = '';
    } catch (e) {
      $('saved').textContent = '';
      if ('folder' in patch) { $('folder-help').textContent = e.message; $('folder-help').style.color = 'var(--danger)'; }
      return;
    }
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { $('saved').textContent = ''; }, 1800);
  }

  function openSettings() {
    S.returnTo = S.view === 'settings' ? S.returnTo : S.view;
    loadSettings();
    show('settings');
    $('folder').focus();
  }
  function closeSettings() {
    const back = ['error', 'fetching', 'job'].includes(S.returnTo) ? (S.info ? 'ready' : 'empty') : S.returnTo;
    show(back === 'missing' ? 'missing' : back);
    if (back === 'empty') $('url').focus();
  }

  async function updateYtdlp() {
    const btn = $('update');
    btn.disabled = true;
    $('update-status').textContent = 'Updating…';
    try {
      const r = await api('/api/update-ytdlp', {});
      if (!r.restarting) {
        $('update-status').textContent = `yt-dlp ${r.after} is the latest version.`;
      } else {
        $('update-status').textContent = `Updated to ${r.after}. Restarting…`;
        for (let i = 0; i < 40; i++) {
          await new Promise((res) => setTimeout(res, 500));
          try { const env = await api('/api/env'); if (env.ytdlp === r.after) break; } catch (_) { /* still restarting */ }
        }
        $('ytdlp-version').textContent = 'Version ' + r.after;
        $('update-status').textContent = `Updated to ${r.after}.`;
      }
    } catch (e) {
      $('update-status').textContent = e.message || 'Update failed.';
    }
    btn.disabled = false;
  }

  // --- Events ------------------------------------------------------------
  $('url-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const url = $('url').value.trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) {
      showError({ code: 'bad_url', title: 'That isn’t a web link', message: 'Paste a link that starts with http:// or https://.' }, null);
      return;
    }
    fetchInfo(url);
  });
  $('mode').addEventListener('click', (e) => { const b = e.target.closest('[data-mode]'); if (b && !b.disabled) { S.mode = b.dataset.mode; applyMode(); } });
  $('afmt').addEventListener('click', (e) => { const b = e.target.closest('[data-afmt]'); if (b) { S.afmt = b.dataset.afmt; applyMode(); } });
  $('cookies').addEventListener('click', (e) => {
    const b = e.target.closest('[data-b]'); if (!b) return;
    setRadio($('cookies'), 'b', b.dataset.b);
    $('cookie-help').textContent = COOKIE_HELP[b.dataset.b];
    saveSetting({ cookies: b.dataset.b });
  });
  document.querySelectorAll('[role=radiogroup]').forEach((g) => g.addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const items = [...g.querySelectorAll('[role=radio]:not(:disabled)')];
    const i = items.indexOf(document.activeElement);
    const n = items[(i + (e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length];
    n.focus(); n.click(); e.preventDefault();
  }));
  $('download').addEventListener('click', startDownload);
  $('cancel').addEventListener('click', cancelJob);
  $('retry').addEventListener('click', retry);
  $('err-settings').addEventListener('click', openSettings);
  $('open-settings').addEventListener('click', openSettings);
  $('close-settings').addEventListener('click', closeSettings);
  $('folder').addEventListener('change', () => saveSetting({ folder: $('folder').value }));
  $('update').addEventListener('click', updateYtdlp);
  $('done-reveal').addEventListener('click', () => fileAction('reveal', { job_id: S.doneJob }));
  $('done-open').addEventListener('click', () => fileAction('open', { job_id: S.doneJob }));
  $('env-recheck').addEventListener('click', async () => { if (await checkEnv()) { show('empty'); $('url').focus(); } });
  $('copy-cmd').addEventListener('click', async () => {
    const label = $('copy-cmd').querySelector('span');
    try { await navigator.clipboard.writeText($('env-cmd').textContent); label.textContent = 'Copied'; }
    catch (_) { label.textContent = 'Select and copy'; }
    setTimeout(() => { label.textContent = 'Copy'; }, 1500);
  });

  // Cmd+V anywhere focuses the link field; the paste then lands in it.
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'v') {
      const t = document.activeElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
      const url = $('url');
      if (!$('v-main').hidden && !url.disabled) { url.value = ''; url.focus(); }
    }
    if (e.key === 'Escape' && S.view === 'settings') closeSettings();
  });

  // --- Start -------------------------------------------------------------
  (async () => {
    applyMode();
    loadSettings();
    loadRecent();
    if (await checkEnv()) { show('empty'); $('url').focus(); }
  })();
})();
