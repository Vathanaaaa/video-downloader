(() => {
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const S = {
    view: 'empty',        // empty, fetching, ready, job, done, error, convert, settings, missing, pin
    info: null,
    url: '',
    mode: 'video',
    afmt: 'mp3',
    job: null,
    doneJob: null,
    lastAction: null,     // 'info' or 'download', for Retry
    returnTo: 'empty',
    settings: { folder: '', cookies: 'off', height: 'best', audio_format: 'mp3' },
    phone: false,
    clipDismissed: new Set(),
    files: [],            // convert jobs, in the order they were added
    cfmt: 'mp3',
    cq: 192,
  };
  const DOWNLOAD_VIEWS = ['empty', 'fetching', 'ready', 'job', 'done', 'error'];

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
      throw { title: 'The app stopped', message: 'Video Downloader isn’t running. Open it, then retry.' };
    }
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && S.phone && data.error?.code === 'pin') { show('pin'); }
    if (!res.ok) throw data.error || { title: 'The app returned an error', message: `HTTP ${res.status}. Retry, or quit and reopen the app.` };
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
  function fmtDay(ts) {
    const w = fmtWhen(ts);
    return w === 'Today' || w === 'Yesterday' ? w.toLowerCase() : 'on ' + w;
  }
  const CODEC = { h264: 'H.264', aac: 'AAC', mp3: 'MP3', hevc: 'HEVC', vp9: 'VP9', opus: 'Opus' };
  const BROWSER_NAME = { safari: 'Safari', chrome: 'Chrome', arc: 'Arc', firefox: 'Firefox' };
  const spans = (items) => items.filter(Boolean).map((t) => { const s = document.createElement('span'); s.textContent = t; return s; });

  // --- View switching ----------------------------------------------------
  function show(view) {
    S.view = view;
    const vis = (id, on) => { $(id).hidden = !on; };
    const inDownload = DOWNLOAD_VIEWS.includes(view);
    vis('v-pin', view === 'pin');
    vis('v-env', view === 'missing');
    vis('v-settings', view === 'settings');
    vis('v-main', inDownload);
    vis('v-recent', inDownload);
    vis('v-convert', view === 'convert');
    vis('tabs', inDownload || view === 'convert');
    vis('open-settings', view !== 'settings' && view !== 'pin');
    vis('v-skeleton', view === 'fetching');
    vis('v-card', ['ready', 'job', 'done'].includes(view));
    vis('v-options', view === 'ready');
    vis('v-progress', view === 'job');
    vis('v-done', view === 'done');
    vis('v-error', view === 'error');
    if (view !== 'empty' && view !== 'ready' && view !== 'done' && view !== 'error') $('clip').hidden = true;
    $('url').disabled = view === 'job';
    setTab(view === 'convert' ? 'convert' : 'download');
  }

  function setTab(which) {
    ['download', 'convert'].forEach((t) => {
      const b = $('tab-' + t);
      b.setAttribute('aria-selected', t === which);
      b.tabIndex = t === which ? 0 : -1;
    });
  }

  // --- Environment -------------------------------------------------------
  async function checkEnv() {
    let env;
    try { env = await api('/api/env'); } catch (e) { showError(e, null); return false; }
    $('ytdlp-version').textContent = env.ytdlp ? 'Version ' + env.ytdlp : 'Not found';
    if (env.ok) return true;
    const rows = [['yt-dlp', env.ytdlp], ['ffmpeg', env.ffmpeg], ['ffprobe', env.ffprobe]];
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

  // --- Clipboard pickup --------------------------------------------------
  async function checkClipboard() {
    if (S.phone || !['empty', 'ready', 'done', 'error'].includes(S.view)) return;
    let r;
    try { r = await api('/api/clipboard', {}); } catch (_) { return; }
    const url = r.url;
    const current = $('url').value.trim();
    if (!url || url === current || url === S.url || S.clipDismissed.has(url)) { $('clip').hidden = true; return; }
    $('clip-url').textContent = url.replace(/^https?:\/\/(www\.)?/, '');
    $('clip-url').title = url;
    $('clip').dataset.url = url;
    $('clip').hidden = false;
  }
  function useClip() {
    const url = $('clip').dataset.url;
    $('clip').hidden = true;
    if (!url) return;
    S.clipDismissed.add(url);
    $('url').value = url;
    fetchInfo(url);
  }
  function dismissClip() {
    if ($('clip').dataset.url) S.clipDismissed.add($('clip').dataset.url);
    $('clip').hidden = true;
  }

  // --- Fetch metadata ----------------------------------------------------
  async function fetchInfo(url) {
    S.url = url.trim();
    S.lastAction = 'info';
    S.info = null;
    $('clip').hidden = true;
    show('fetching');
    try {
      const info = await api('/api/info', { url: S.url });
      if (S.url !== $('url').value.trim()) return; // a newer link was pasted meanwhile
      S.info = info;
      renderCard(info);
      S.mode = info.has_video ? 'video' : 'audio';
      S.afmt = info.defaults?.audio_format || S.afmt;
      applyMode();
      show('ready');
      $('download').focus({ preventScroll: true });
    } catch (e) {
      showError(e, 'info');
    }
  }

  function renderCard(info) {
    $('media-title').textContent = info.title;
    $('media-sub').replaceChildren(...spans([info.uploader, fmtDuration(info.duration)]));
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

    // Resolution menu with the size each choice will take on disk.
    const sizes = info.sizes || [];
    const best = sizes.find((s) => s.height <= 1080) || sizes[sizes.length - 1];
    const label = (h, size) => h + 'p' + (size ? ' · ' + fmtBytes(size) : '');
    const opts = [['best', best ? `Best (${label(best.height, best.size)})` : 'Best (up to 1080p)'],
      ...sizes.map((s) => [String(s.height), label(s.height, s.size)])];
    const sel = $('res');
    sel.replaceChildren(...opts.map(([v, text]) => { const o = document.createElement('option'); o.value = v; o.textContent = text; return o; }));
    const want = String(info.defaults?.height ?? 'best');
    sel.value = opts.some(([v]) => v === want) ? want : 'best';
    $('mode').querySelector('[data-mode=video]').disabled = !info.has_video;

    // Already downloaded?
    const ex = info.existing;
    $('existing').hidden = !ex;
    if (ex) {
      $('existing-title').textContent = `You saved this ${fmtDay(ex.at)}`;
      const meta = [ex.name, ex.height ? ex.height + 'p' : null, ex.size ? fmtBytes(ex.size) : null].filter(Boolean);
      $('existing-file').textContent = meta.join(' · ');
      $('existing-file').title = ex.name;
    }
  }

  // --- Mode controls -----------------------------------------------------
  function setRadio(group, attr, value) {
    group.querySelectorAll('[role=radio]').forEach((b) => {
      const on = b.dataset[attr] === String(value);
      b.setAttribute('aria-checked', on);
      b.tabIndex = on ? 0 : -1;
    });
  }
  function applyMode() {
    setRadio($('mode'), 'mode', S.mode);
    setRadio($('afmt'), 'afmt', S.afmt);
    $('row-res').hidden = S.mode !== 'video';
    $('row-afmt').hidden = S.mode !== 'audio';
    const again = S.info?.existing ? ' again' : '';
    $('download').textContent = S.mode === 'video' ? `Download video${again}` : `Download ${S.afmt.toUpperCase()}${again}`;
    const aSize = S.info?.audio_sizes?.[S.afmt];
    const about = aSize ? `, about ${fmtBytes(aSize)}` : '';
    $('opt-note').textContent = S.mode === 'video'
      ? 'MP4, H.264 and AAC. Plays in QuickTime and Quick Look.'
      : S.afmt === 'mp3' ? `MP3 at 192 kbps${about}. Title saved in the file.` : `AAC in M4A${about}. Title saved in the file.`;
  }

  // --- Download job ------------------------------------------------------
  async function startDownload() {
    if (!S.info) return;
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
      try { j = await api('/api/jobs/' + id); } catch (e) { S.job = null; showError(e, 'download'); return; }
      if (S.job !== id) return;
      if (j.state === 'done') { S.job = null; S.doneJob = id; finish(j.result); return; }
      if (j.state === 'error') { S.job = null; showError(j.error, 'download'); return; }
      if (j.state === 'canceled') { S.job = null; canceled(); return; }
      renderJob(j);
      await sleep(400);
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
    $('done-title').textContent = S.phone ? 'Finished on your Mac' : 'Saved to ' + r.folder_name;
    $('done-file').textContent = r.name;
    const chips = r.kind === 'video'
      ? [CODEC[r.vcodec] || r.vcodec, r.acodec ? CODEC[r.acodec] || r.acodec : 'No audio', r.height ? r.height + 'p' : null, fmtBytes(r.size)]
      : [r.ext, fmtBytes(r.size)];
    $('done-checks').replaceChildren(...chips.filter(Boolean).map((t) => { const c = document.createElement('span'); c.className = 'chip'; c.textContent = t; return c; }));
    $('done-save').href = '/api/file?job=' + encodeURIComponent(S.doneJob);
    setSteps('done');
    show('done');
    (S.phone ? $('done-save') : $('done-reveal')).focus({ preventScroll: true });
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

    // One-click fix: retry with the first installed browser's login. Other browsers
    // are a quiet line below, so the card keeps one primary and one secondary action.
    const actions = $('err-actions');
    actions.querySelectorAll('[data-browser]').forEach((b) => b.remove());
    const browsers = !S.phone && action ? (e.browsers || []) : [];
    if (browsers.length) {
      const btn = document.createElement('button');
      btn.className = 'btn btn-lg btn-primary';
      btn.dataset.browser = browsers[0];
      btn.textContent = `Retry with ${BROWSER_NAME[browsers[0]]}`;
      btn.addEventListener('click', () => retryWithBrowser(browsers[0]));
      actions.insertBefore(btn, $('retry'));
    }
    $('retry').className = 'btn btn-lg' + (browsers.length ? '' : ' btn-primary');
    $('retry').hidden = !action;
    const more = $('err-more');
    more.replaceChildren();
    more.hidden = browsers.length < 2;
    if (browsers.length > 1) {
      more.append('Logged in somewhere else? ');
      browsers.slice(1).forEach((b, i) => {
        if (i) more.append(' · ');
        const link = document.createElement('button');
        link.className = 'link';
        link.textContent = BROWSER_NAME[b];
        link.addEventListener('click', () => retryWithBrowser(b));
        more.append(link);
      });
    }
    show('error');
    (actions.querySelector('.btn-primary') || $('retry')).focus({ preventScroll: true });
  }

  async function retryWithBrowser(browser) {
    try { S.settings = await api('/api/settings', { cookies: browser }); } catch (_) { /* retry anyway */ }
    setRadio($('cookies'), 'b', browser);
    $('cookie-help').textContent = COOKIE_HELP[browser];
    retry();
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
    ul.replaceChildren(...items.map((r) => {
      const li = document.createElement('li');
      if (!r.exists) li.className = 'missing';
      const meta = r.kind === 'video'
        ? [r.ext, r.height ? r.height + 'p' : null, fmtBytes(r.size)]
        : [r.ext, fmtBytes(r.size)];
      meta.push(r.exists ? fmtWhen(r.at) : 'Moved or deleted');
      li.innerHTML = `
        <svg class="icon recent-kind" aria-hidden="true"><use href="#i-${r.kind === 'audio' ? 'wave' : 'film'}"/></svg>
        <div class="recent-main"><p class="recent-title"></p><p class="recent-meta"></p></div>
        <div class="recent-actions only-mac">
          <button class="btn btn-quiet btn-icon" data-act="reveal" title="Show in Finder"><svg class="icon"><use href="#i-folder"/></svg></button>
          <button class="btn btn-quiet btn-icon" data-act="open" title="Open"><svg class="icon"><use href="#i-play"/></svg></button>
        </div>
        <div class="recent-actions only-phone">
          <a class="btn btn-quiet btn-icon" data-act="save" title="Save to this device" download><svg class="icon"><use href="#i-down"/></svg></a>
        </div>`;
      li.querySelector('.recent-title').textContent = r.title;
      li.querySelector('.recent-title').title = r.name;
      li.querySelector('.recent-meta').replaceChildren(...spans(meta));
      li.querySelectorAll('button[data-act]').forEach((b) => {
        b.disabled = !r.exists;
        b.setAttribute('aria-label', (b.dataset.act === 'reveal' ? 'Show in Finder: ' : 'Open: ') + r.title);
        b.addEventListener('click', () => fileAction(b.dataset.act, { recent: r.index }));
      });
      const save = li.querySelector('a[data-act=save]');
      save.setAttribute('aria-label', 'Save to this device: ' + r.title);
      if (r.exists) save.href = '/api/file?recent=' + r.index; else save.setAttribute('aria-disabled', 'true');
      return li;
    }));
  }

  async function fileAction(act, body) {
    try { await api('/api/' + act, body); }
    catch (e) { loadRecent(); showError({ title: 'File not found', message: e.message }, null); }
  }

  // --- Convert -----------------------------------------------------------
  function convertNote() {
    const fmt = S.cfmt === 'mp3' ? `${S.cq} kbps MP3.` : S.cfmt === 'm4a' ? 'AAC in M4A.' : 'Uncompressed WAV.';
    $('c-note').textContent = `${fmt} Saved next to each original with the same name. Existing files are never replaced.`;
    $('row-cq').hidden = S.cfmt !== 'mp3';
  }

  async function chooseFiles() {
    let paths = [];
    try {
      if (window.pywebview?.api?.choose_files) paths = await window.pywebview.api.choose_files();
      else paths = (await api('/api/choose-files', {})).paths;
    } catch (_) { return; }
    if (paths?.length) addFiles(paths);
  }

  async function addFiles(paths) {
    let r;
    try { r = await api('/api/convert', { paths, format: S.cfmt, quality: S.cq }); }
    catch (e) { $('files-status').textContent = e.message || 'Couldn’t start converting.'; return; }
    r.jobs.forEach((j) => S.files.push({ ...j, fmt: S.cfmt }));
    r.rejected.forEach((x) => S.files.push({ id: null, name: x.name, state: 'error', error: { message: x.message } }));
    renderConvert();
    pollConverts();
  }

  let convertPolling = false;
  async function pollConverts() {
    if (convertPolling) return;
    convertPolling = true;
    try {
      while (S.files.some((f) => f.id && (f.state === 'waiting' || f.state === 'converting'))) {
        await Promise.all(S.files.filter((f) => f.id && (f.state === 'waiting' || f.state === 'converting')).map(async (f) => {
          try {
            const j = await api('/api/convert/' + f.id);
            if (j.state === 'done' && f.state !== 'done') $('files-status').textContent = `${f.name} converted`;
            Object.assign(f, j);
          } catch (_) { /* try again next tick */ }
        }));
        renderConvert();
        await sleep(400);
      }
    } finally { convertPolling = false; }
  }

  function fileRow(f, i) {
    const li = document.createElement('li');
    let icon = f.kind || (/\.(wav|mp3|m4a|aac|flac|aiff?|ogg|opus)$/i.test(f.name) ? 'wave' : 'film');
    let cls = '';
    let name = f.name;
    const meta = document.createElement('p');
    meta.className = 'file-meta';
    let extra = null;
    let action = null;
    const iconBtn = (sym, label, title, onClick) => {
      const b = document.createElement('button');
      b.className = 'btn btn-quiet btn-icon';
      b.setAttribute('aria-label', label);
      b.title = title;
      b.innerHTML = `<svg class="icon"><use href="#i-${sym}"/></svg>`;
      b.addEventListener('click', onClick);
      return b;
    };
    const cancel = () => iconBtn('x', `Cancel ${f.name}`, 'Cancel', async () => {
      try { await api(`/api/convert/${f.id}/cancel`, {}); } catch (_) { /* polling shows the result */ }
    });
    if (f.state === 'converting') {
      const p = Math.round(f.percent || 0);
      meta.replaceChildren(...spans([fmtBytes(f.size), `Converting ${p}%`]));
      extra = document.createElement('div');
      extra.className = 'timeline sm';
      extra.setAttribute('role', 'progressbar');
      extra.setAttribute('aria-label', 'Converting ' + f.name);
      extra.setAttribute('aria-valuemin', '0');
      extra.setAttribute('aria-valuemax', '100');
      extra.setAttribute('aria-valuenow', String(p));
      extra.innerHTML = `<div class="timeline-fill" style="width:${p}%"></div><div class="timeline-head" style="left:${p}%"></div>`;
      action = cancel();
    } else if (f.state === 'waiting') {
      meta.replaceChildren(...spans([fmtBytes(f.size), 'Waiting']));
      action = cancel();
    } else if (f.state === 'canceled') {
      meta.replaceChildren(...spans(['Canceled. Nothing was saved.']));
    } else if (f.state === 'done') {
      icon = 'check'; cls = 'ok'; name = f.out_name;
      const stem = f.name.replace(/\.[^.]+$/, '');
      meta.replaceChildren(...spans(['From ' + f.name, fmtBytes(f.out_size), f.renamed ? `${stem}.${f.fmt} already existed` : null]));
      action = iconBtn('folder', `Show ${f.out_name} in Finder`, 'Show in Finder', () => fileAction('reveal', { convert_id: f.id }));
    } else {
      icon = 'alert'; cls = 'err';
      meta.classList.add('err');
      meta.textContent = f.error?.message || 'Couldn’t convert this file.';
      action = iconBtn('x', `Remove ${f.name} from the list`, 'Remove', () => { S.files.splice(i, 1); renderConvert(); });
    }
    li.innerHTML = `<svg class="icon file-icon ${cls}" aria-hidden="true"><use href="#i-${icon}"/></svg><div class="file-main"><p class="file-name"></p></div>`;
    li.querySelector('.file-name').textContent = name;
    li.querySelector('.file-name').title = name;
    li.querySelector('.file-main').append(meta, ...(extra ? [extra] : []));
    if (action) li.append(action);
    return li;
  }

  function renderConvert() {
    const has = S.files.length > 0;
    $('drop').classList.toggle('compact', has);
    $('drop-title').textContent = has ? 'Drop more files' : 'Drop video or audio files here';
    $('v-files').hidden = !has;
    $('files').replaceChildren(...S.files.map(fileRow));
  }

  // Files dropped on the app window. In the Mac app, main.py passes full paths here.
  window.vdDropFiles = (paths) => {
    if (S.view !== 'convert') show('convert');
    $('drop').classList.remove('is-over');
    if (paths?.length) addFiles(paths);
  };

  // --- Settings ----------------------------------------------------------
  const COOKIE_HELP = {
    off: 'For Instagram and other sites that need a login. Log in to the site in that browser first.',
    safari: 'Safari keeps cookies in a protected folder. Give Video Downloader Full Disk Access in System Settings › Privacy & Security, then reopen the app.',
    chrome: 'Uses your logged-in Chrome session. macOS may ask to allow access to “Chrome Safe Storage”: choose Always Allow.',
    arc: 'Uses your logged-in Arc session. macOS may ask to allow access to “Arc Safe Storage”: choose Always Allow.',
    firefox: 'Uses your logged-in Firefox session. Log in to the site in Firefox first.',
  };

  async function loadSettings() {
    try { S.settings = await api('/api/settings'); } catch (_) { return; }
    $('folder').value = S.settings.folder;
    setRadio($('cookies'), 'b', S.settings.cookies);
    $('cookie-help').textContent = COOKIE_HELP[S.settings.cookies];
    S.afmt = S.settings.audio_format || S.afmt;
  }

  let savedTimer;
  function flashSaved() {
    $('saved').textContent = 'Saved';
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { $('saved').textContent = ''; }, 1800);
  }
  async function saveSetting(patch) {
    try {
      S.settings = await api('/api/settings', patch);
      $('folder-help').textContent = 'Created if it doesn’t exist';
      $('folder-help').style.color = '';
      flashSaved();
    } catch (e) {
      $('saved').textContent = '';
      if ('folder' in patch) { $('folder-help').textContent = e.message; $('folder-help').style.color = 'var(--danger)'; }
    }
  }

  async function loadToken() {
    try { $('token').textContent = (await api('/api/extension-token')).token; } catch (_) { /* phone or error */ }
  }

  function renderPhone(p) {
    const on = !!p.on;
    $('phone-switch').setAttribute('aria-checked', on);
    $('phone-on').hidden = !on;
    $('phone-off-help').hidden = on;
    if (on) {
      $('qr').innerHTML = p.qr || '';
      $('phone-url').textContent = p.url;
      $('phone-pin').textContent = p.pin.slice(0, 3) + ' ' + p.pin.slice(3);
    }
  }
  async function loadPhone() {
    try { renderPhone(await api('/api/phone')); } catch (_) { /* ignore */ }
  }
  async function togglePhone() {
    const on = $('phone-switch').getAttribute('aria-checked') !== 'true';
    $('phone-switch').disabled = true;
    try { renderPhone(await api('/api/phone', { on })); }
    catch (e) { $('phone-off-help').hidden = false; $('phone-off-help').textContent = e.message; }
    $('phone-switch').disabled = false;
  }

  function openSettings() {
    if (S.phone || S.view === 'settings' || S.view === 'pin') return;
    S.returnTo = S.view;
    loadSettings();
    loadToken();
    loadPhone();
    show('settings');
    $('folder').focus();
  }
  function closeSettings() {
    const back = ['error', 'fetching', 'job'].includes(S.returnTo) ? (S.info ? 'ready' : 'empty') : S.returnTo;
    show(back);
    if (back === 'empty') $('url').focus();
  }

  async function updateYtdlp() {
    const btn = $('update');
    btn.disabled = true;
    $('update-status').textContent = 'Downloading the latest yt-dlp…';
    try {
      const r = await api('/api/update-ytdlp', {});
      $('ytdlp-version').textContent = 'Version ' + r.after;
      $('update-status').textContent = r.before === r.after ? `yt-dlp ${r.after} is the latest version.` : `Updated to ${r.after}.`;
    } catch (e) {
      $('update-status').textContent = e.message || 'Update failed.';
    }
    btn.disabled = false;
  }

  // --- Phone: PIN --------------------------------------------------------
  async function login(e) {
    e.preventDefault();
    $('pin-go').disabled = true;
    try {
      await api('/api/phone/login', { pin: $('pin').value });
      $('pin').value = '';
      $('pin-error').hidden = true;
      $('pin').removeAttribute('aria-invalid');
      await startMain();
    } catch (err) {
      $('pin').setAttribute('aria-invalid', 'true');
      $('pin-error').textContent = err.message || 'That PIN didn’t match.';
      $('pin-error').hidden = false;
      $('pin').select();
    }
    $('pin-go').disabled = false;
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
    if (S.view === 'ready' && url === S.url) { startDownload(); return; }
    fetchInfo(url);
  });
  $('mode').addEventListener('click', (e) => { const b = e.target.closest('[data-mode]'); if (b && !b.disabled) { S.mode = b.dataset.mode; applyMode(); } });
  $('afmt').addEventListener('click', (e) => { const b = e.target.closest('[data-afmt]'); if (b) { S.afmt = b.dataset.afmt; applyMode(); } });
  $('cfmt').addEventListener('click', (e) => { const b = e.target.closest('[data-cf]'); if (b) { S.cfmt = b.dataset.cf; setRadio($('cfmt'), 'cf', S.cfmt); convertNote(); } });
  $('cq').addEventListener('click', (e) => { const b = e.target.closest('[data-q]'); if (b) { S.cq = Number(b.dataset.q); setRadio($('cq'), 'q', S.cq); convertNote(); } });
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
  $('tab-download').addEventListener('click', () => { if (S.view === 'convert') { show(S.info ? 'ready' : 'empty'); $('url').focus(); } });
  $('tab-convert').addEventListener('click', () => { if (S.view !== 'convert') { show('convert'); renderConvert(); } });
  $('tabs').addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const other = document.activeElement === $('tab-download') ? $('tab-convert') : $('tab-download');
    other.focus(); other.click(); e.preventDefault();
  });
  $('download').addEventListener('click', startDownload);
  $('cancel').addEventListener('click', cancelJob);
  $('retry').addEventListener('click', retry);
  $('open-settings').addEventListener('click', openSettings);
  $('close-settings').addEventListener('click', closeSettings);
  $('folder').addEventListener('change', () => saveSetting({ folder: $('folder').value }));
  $('choose-folder').addEventListener('click', async () => {
    if (window.pywebview?.api?.choose_folder) {
      let path = null;
      try { path = await window.pywebview.api.choose_folder(); } catch (_) { /* canceled */ }
      if (path) { $('folder').value = path; saveSetting({ folder: path }).then(() => { $('folder').value = S.settings.folder; }); }
      return;
    }
    try { S.settings = await api('/api/choose-folder', {}); $('folder').value = S.settings.folder; flashSaved(); } catch (_) { /* canceled */ }
  });
  $('update').addEventListener('click', updateYtdlp);
  $('done-reveal').addEventListener('click', () => fileAction('reveal', { job_id: S.doneJob }));
  $('done-open').addEventListener('click', () => fileAction('open', { job_id: S.doneJob }));
  $('existing-reveal').addEventListener('click', () => fileAction('reveal', { existing: S.info?.id }));
  $('env-recheck').addEventListener('click', async () => { if (await checkEnv()) { show('empty'); $('url').focus(); } });
  $('clip-use').addEventListener('click', useClip);
  $('clip-dismiss').addEventListener('click', dismissClip);
  $('choose').addEventListener('click', chooseFiles);
  $('phone-switch').addEventListener('click', togglePhone);
  $('pin-form').addEventListener('submit', login);
  $('copy-token').addEventListener('click', async () => {
    const label = $('copy-token').querySelector('span');
    try { await navigator.clipboard.writeText($('token').textContent); label.textContent = 'Copied'; }
    catch (_) { label.textContent = 'Select and copy'; }
    setTimeout(() => { label.textContent = 'Copy'; }, 1500);
  });

  // Drag and drop. A plain browser gives no file paths, so there the drop asks for Choose files…
  const drop = $('drop');
  ['dragenter', 'dragover'].forEach((t) => document.addEventListener(t, (e) => {
    if (S.phone || S.view === 'pin') return;
    e.preventDefault();
    if (S.view === 'convert') drop.classList.add('is-over');
  }));
  document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) drop.classList.remove('is-over'); });
  document.addEventListener('drop', (e) => {
    if (S.phone || S.view === 'pin') return;
    e.preventDefault();
    drop.classList.remove('is-over');
    const link = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain');
    if (!e.dataTransfer.files.length && /^https?:\/\//i.test(link?.trim())) {
      const url = link.trim().split('\n')[0];
      show(S.info ? 'ready' : 'empty');
      $('url').value = url;
      fetchInfo(url);
      return;
    }
    if (e.dataTransfer.files.length && !window.pywebview) {
      if (S.view !== 'convert') show('convert');
      $('files-status').textContent = 'Use Choose files… to add files in a browser.';
      $('drop-title').textContent = 'In a browser, use Choose files…';
    }
    // In the Mac app, main.py receives the drop with full paths and calls vdDropFiles().
  });

  // Keyboard: ⌘V paste, Return download, Esc cancel or close, ⌘, settings.
  document.addEventListener('keydown', (e) => {
    const t = document.activeElement;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'v' && !typing) {
      const url = $('url');
      if (DOWNLOAD_VIEWS.includes(S.view) && !url.disabled) { url.value = ''; url.focus(); }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === ',') { e.preventDefault(); openSettings(); return; }
    if (e.key === 'Escape') {
      if (S.view === 'settings') closeSettings();
      else if (S.view === 'job') cancelJob();
      else if (!$('clip').hidden) dismissClip();
      return;
    }
    if (e.key === 'Enter' && S.view === 'ready' && !typing && t?.tagName !== 'BUTTON' && t?.tagName !== 'A') {
      e.preventDefault();
      startDownload();
    }
  });

  window.addEventListener('focus', checkClipboard);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkClipboard(); });

  // --- Start -------------------------------------------------------------
  async function startMain() {
    applyMode();
    convertNote();
    setRadio($('cfmt'), 'cf', S.cfmt);
    setRadio($('cq'), 'q', S.cq);
    if (!S.phone) await loadSettings();
    applyMode();
    loadRecent();
    if (await checkEnv()) {
      show('empty');
      $('url').focus();
      checkClipboard();
    }
  }

  (async () => {
    let session = { phone: false };
    try { session = await api('/api/phone/session'); } catch (_) { /* Mac */ }
    S.phone = !!session.phone;
    document.body.classList.toggle('is-phone', S.phone);
    if (S.phone && !session.authed) { show('pin'); $('pin').focus(); return; }
    startMain();
  })();
})();
