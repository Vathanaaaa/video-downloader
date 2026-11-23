const $ = (id) => document.getElementById(id);

chrome.storage.local.get(['port', 'token']).then((s) => {
  $('token').value = s.token || '';
  $('port').value = s.port || 8765;
});

function status(text, cls) {
  $('status').textContent = text;
  $('status').className = cls || '';
}

$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const token = $('token').value.trim();
  const port = Number($('port').value) || 8765;
  await chrome.storage.local.set({ token, port });
  status('Saved. Testing…');
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/ext/ping`, { headers: { 'X-VD-Token': token } });
    if (res.ok) status('Connected', 'ok');
    else if (res.status === 401) status('Saved, but the token doesn’t match. Copy it again from the app.', 'err');
    else status(`Saved, but the app answered HTTP ${res.status}.`, 'err');
  } catch (_) {
    status('Saved. Video Downloader isn’t running, so it couldn’t be tested.', 'err');
  }
});
