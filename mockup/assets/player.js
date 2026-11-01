/* web-player — public player (mock-up logic). */
(function () {
  'use strict';

  const { DB } = WP;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const SESSION_KEY = 'web-player-mockup-session';
  let session = null;      // { userId }
  let items = [];
  let current = null;
  let player = null;

  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 2600);
  }

  /* ------------------------------------------------------------------ */
  /* login                                                              */
  /* ------------------------------------------------------------------ */
  function storedSession() {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null'); } catch (e) { return null; }
  }

  $('#login-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const name = $('#l-user').value.trim();
    const pass = $('#l-pass').value;
    const user = DB.users.find(u => u.name.toLowerCase() === name.toLowerCase());
    $('#l-error').textContent = '';
    if (!user) { $('#l-error').textContent = 'Unknown user'; return; }
    if (!pass) { $('#l-error').textContent = 'A password is required'; return; }
    if (!user.enabled) { $('#l-error').textContent = 'This account is disabled'; return; }
    if (user.expires && user.expires < '2026-11-01') { $('#l-error').textContent = 'This account has expired'; return; }
    session = { userId: user.id };
    try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(session)); } catch (err) { /* ignore */ }
    start();
  });

  $('#btn-logout').addEventListener('click', () => {
    session = null;
    try { sessionStorage.removeItem(SESSION_KEY); } catch (e) { /* ignore */ }
    if (player) { player.dispose(); player = null; }
    $('#app').classList.add('hidden');
    $('#login').classList.remove('hidden');
  });

  /* ------------------------------------------------------------------ */
  /* playlist                                                           */
  /* ------------------------------------------------------------------ */
  function visibleItems() {
    if (!session) return [];
    return DB.media.filter(m => WP.visibleTo(m, session.userId)).sort((a, b) => a.sort - b.sort);
  }

  function renderFilters() {
    const groups = [];
    items.forEach(m => { if (groups.indexOf(m.group) === -1) groups.push(m.group); });
    const kinds = [];
    items.forEach(m => { if (kinds.indexOf(m.kind) === -1) kinds.push(m.kind); });
    $('#p-filters').innerHTML =
      `<span class="chip click on" data-f="all">All (${items.length})</span>` +
      kinds.map(k => `<span class="chip click" data-f="kind:${k}">${esc(k)}</span>`).join('') +
      groups.map(g => `<span class="chip click" data-f="group:${g}">${esc((WP.groupById(g) || {}).name || g)}</span>`).join('');
    $$('#p-filters .chip').forEach(c => c.addEventListener('click', () => {
      $$('#p-filters .chip').forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      renderList();
    }));
  }

  let filter = 'all';

  function renderList() {
    const q = $('#p-search').value.trim().toLowerCase();
    const rows = items.filter(m => {
      if (filter.startsWith('kind:') && m.kind !== filter.slice(5)) return false;
      if (filter.startsWith('group:') && m.group !== filter.slice(6)) return false;
      if (q && !m.title.toLowerCase().includes(q)) return false;
      return true;
    });
    $('#p-count').textContent = rows.length + ' of ' + items.length + ' titles';

    const prog = (DB.progress[session.userId] || {});
    $('#p-list').innerHTML = rows.length ? rows.map(m => {
      const p = prog[m.id];
      const pct = p && m.duration ? Math.min(100, Math.round(p / m.duration * 100)) : 0;
      return `<div class="pitem ${current && current.id === m.id ? 'on' : ''}" data-id="${m.id}">
        ${m.poster ? `<img src="${esc(m.poster)}" alt="">` : `<img src="assets/vendor/blank.png" alt="">`}
        <div class="grow">
          <div class="nm trunc">${esc(m.title)}</div>
          <div class="mt">${esc(m.year || '')} · ${esc(WP.fmtDuration(m.duration))} ·
            <span class="badge ${m.probe.delivery === 'direct' ? 'ok' : (m.probe.delivery === 'hls' ? 'warn' : 'purple')}"
              style="font-size:10px;padding:0 6px">${m.probe.delivery === 'hls' ? 'HLS' : (m.probe.delivery === 'embed' ? 'embed' : 'direct')}</span>
          </div>
          ${pct ? `<div class="prog"><i style="width:${pct}%"></i></div>` : ''}
        </div>
      </div>`;
    }).join('') : '<div class="mut center" style="padding:26px 14px">Nothing matches.</div>';

    $$('#p-list .pitem').forEach(el => el.addEventListener('click', () => {
      play(WP.mediaById(el.dataset.id));
    }));
  }

  $('#p-search').addEventListener('input', renderList);

  /* ------------------------------------------------------------------ */
  /* playback                                                           */
  /* ------------------------------------------------------------------ */
  function ensurePlayer() {
    if (player) return player;
    player = videojs('v', {
      controls: true,
      preload: 'metadata',
      fluid: true,
      playbackRates: [0.5, 0.75, 1, 1.25, 1.5, 2],
      controlBar: {
        pictureInPictureToggle: true,
        playbackRateMenuButton: true,
        volumePanel: { inline: false },
      },
    });
    player.on('timeupdate', () => {
      const d = player.duration();
      if (isFinite(d) && d > 0) {
        const t = player.currentTime();
        if (!jumping) $('#jump-in').value = WP.fmtDuration(t);
      }
    });
    return player;
  }

  let jumping = false;

  function play(item) {
    if (!item) return;
    current = item;
    renderList();

    $('#now-title').textContent = item.title;
    const bits = [item.year, WP.fmtDuration(item.duration),
      item.probe.width ? item.probe.width + '×' + item.probe.height : null,
      item.probe.video ? item.probe.video.toUpperCase() : null,
      item.probe.container ? item.probe.container.toUpperCase() : null].filter(Boolean);
    $('#now-meta').textContent = bits.join(' · ');
    if (item.poster) { const im = $('#now-poster'); im.src = item.poster; im.hidden = false; }
    else $('#now-poster').hidden = true;

    if (item.probe.delivery === 'embed') {
      $('#v').classList.add('hidden');
      $('#yt').classList.remove('hidden');
      const id = (item.url.split('v=')[1] || '').split('&')[0];
      $('#yt').src = 'https://www.youtube-nocookie.com/embed/' + id + '?autoplay=1';
      toast('YouTube item — shown with the embedded player', 'warn');
      return;
    }
    $('#yt').classList.add('hidden');
    $('#v').classList.remove('hidden');

    const p = ensurePlayer();
    let url = item.source === 'local' ? item.path : item.url;
    if (item.source === 'youtube' && item.probe.delivery === 'direct') {
      /* In the real application yt-dlp resolves the link to a direct media URL
       * that the NAS serves with Range support. The mock-up has no such file,
       * so a demo clip stands in for the resolved stream. */
      url = 'media/coffee-and-code.mp4';
      toast('yt-dlp resolved this link → served as a direct stream', 'ok');
    }
    p.src(url);
    p.poster(item.poster || '');
    p.play().catch(() => { /* autoplay may be blocked */ });

    /* resume */
    const saved = (DB.progress[session.userId] || {})[item.id];
    if (saved && $('#opt-resume').checked && item.duration && saved < item.duration - 15) {
      p.one('loadedmetadata', () => { p.currentTime(saved); toast('Resumed at ' + WP.fmtDuration(saved)); });
    }
  }

  function remember() {
    if (!current || !player || !session) return;
    DB.progress[session.userId] = DB.progress[session.userId] || {};
    const t = player.currentTime();
    if (t > 5 && t < (current.duration || 1e9) - 15) DB.progress[session.userId][current.id] = Math.floor(t);
    try { localStorage.setItem('web-player-mockup-v1', JSON.stringify({ progress: DB.progress })); } catch (e) { /* ignore */ }
  }

  function step(sec) {
    if (!player || !current) return;
    if (current.probe.delivery === 'embed') { toast('Seeking is handled by the embedded player', 'warn'); return; }
    const p = player;
    p.currentTime(Math.max(0, Math.min((p.duration() || 0), p.currentTime() + sec)));
  }

  function jumpTo(str) {
    if (!player || !current) return;
    const t = parseTime(str);
    if (t === null) { toast('Use hh:mm:ss or mm:ss', 'err'); return; }
    const dur = player.duration();
    if (isFinite(dur) && dur > 0 && t > dur) {
      toast('That is past the end of the file (' + WP.fmtDuration(dur) + ')', 'warn');
    }
    jumping = true;
    player.currentTime(Math.max(0, t));
    setTimeout(() => { jumping = false; }, 400);
    toast('Jumped to ' + WP.fmtDuration(t));
  }

  function parseTime(s) {
    s = String(s || '').trim();
    if (!s) return null;
    const parts = s.split(':').map(p => parseInt(p, 10));
    if (parts.some(p => isNaN(p) || p < 0)) return null;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 1) return parts[0];
    return null;
  }

  $('#btn-jump').addEventListener('click', () => jumpTo($('#jump-in').value));
  $('#jump-in').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); jumpTo($('#jump-in').value); } });
  $('#btn-back').addEventListener('click', () => step(-10));
  $('#btn-fwd').addEventListener('click', () => step(10));

  function move(delta) {
    const i = items.findIndex(m => m.id === (current && current.id));
    const n = i + delta;
    if (n >= 0 && n < items.length) play(items[n]);
    else toast('End of the playlist', 'warn');
  }
  $('#btn-next').addEventListener('click', () => move(1));
  $('#btn-prev').addEventListener('click', () => move(-1));

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const k = e.key.toLowerCase();
    if (k === ' ') { e.preventDefault(); if (player) player.paused() ? player.play() : player.pause(); }
    else if (k === 'arrowleft') { e.preventDefault(); step(-10); }
    else if (k === 'arrowright') { e.preventDefault(); step(10); }
    else if (k === 'j') step(-30);
    else if (k === 'l') step(30);
    else if (k === 'f' && player) player.requestFullscreen();
    else if (k === 'm' && player) player.muted(!player.muted());
    else if (k === '/') { e.preventDefault(); $('#p-search').focus(); }
    else if (k === 'n') move(1);
    else if (k === 'p') move(-1);
  });

  window.addEventListener('beforeunload', remember);

  /* ------------------------------------------------------------------ */
  /* start                                                              */
  /* ------------------------------------------------------------------ */
  function start() {
    $('#login').classList.add('hidden');
    $('#app').classList.remove('hidden');
    const user = WP.userById(session.userId);
    $('#who').textContent = 'signed in as ' + user.name;
    items = visibleItems();
    renderFilters();
    renderList();
    if (items.length) play(items[0]);
    else toast('No media is shared with this account', 'warn');
  }

  const s = storedSession();
  if (s && WP.userById(s.userId)) { session = s; start(); }
})();
