/* web-player — the public player (the real application).
 *
 * The playlist comes from /api/player/playlist and contains **only** the
 * items the signed-in user may watch. The stream URLs point at
 * /player/stream/..., which re-checks the session and the visibility rules on
 * every request.
 */
(function () {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  let items = [];
  let current = null;
  let player = null;
  let filter = 'all';
  let jumping = false;

  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 2800);
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) { window.location.href = '/player/login'; throw new Error('signed out'); }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
    if (!res.ok) throw new Error((data && data.detail) || res.statusText);
    return data;
  }

  const fmtDuration = (sec) => {
    if (sec === null || sec === undefined) return '—';
    const s = Math.max(0, Math.round(sec));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
  };

  /* ------------------------------------------------------------------ */
  /* playlist                                                           */
  /* ------------------------------------------------------------------ */
  async function load() {
    try {
      const data = await api('/api/player/playlist');
      items = data.items;
      $('#who').textContent = 'signed in as ' + data.user.name;
      renderFilters();
      renderList();
      if (items.length) play(items[0]);
      else toast('No media is shared with this account', 'warn');
    } catch (e) {
      toast('Could not load the playlist: ' + e.message, 'err');
    }
  }

  function renderFilters() {
    const groups = [];
    items.forEach(m => { if (m.group && groups.indexOf(m.group) === -1) groups.push(m.group); });
    const kinds = [];
    items.forEach(m => { if (kinds.indexOf(m.kind) === -1) kinds.push(m.kind); });
    $('#p-filters').innerHTML =
      `<span class="chip click on" data-f="all">All (${items.length})</span>` +
      kinds.map(k => `<span class="chip click" data-f="kind:${k}">${esc(k)}</span>`).join('') +
      groups.map(g => `<span class="chip click" data-f="group:${g}">${esc(g)}</span>`).join('');
    $$('#p-filters .chip').forEach(c => c.addEventListener('click', () => {
      $$('#p-filters .chip').forEach(x => x.classList.remove('on'));
      c.classList.add('on');
      filter = c.dataset.f;
      renderList();
    }));
  }

  function renderList() {
    const q = $('#p-search').value.trim().toLowerCase();
    const rows = items.filter(m => {
      if (filter.startsWith('kind:') && m.kind !== filter.slice(5)) return false;
      if (filter.startsWith('group:') && m.group !== filter.slice(6)) return false;
      if (q && !m.title.toLowerCase().includes(q)) return false;
      return true;
    });
    $('#p-count').textContent = rows.length + ' of ' + items.length + ' titles';

    $('#p-list').innerHTML = rows.length ? rows.map(m => {
      const pct = m.progress_s && m.duration_s ? Math.min(100, Math.round(m.progress_s / m.duration_s * 100)) : 0;
      const badge = m.delivery === 'hls' ? 'HLS' : (m.delivery === 'embed' ? 'embed' : 'direct');
      const cls = m.delivery === 'direct' ? 'ok' : (m.delivery === 'hls' ? 'warn' : 'purple');
      const dl = m.downloadable
        ? `<a class="dl" href="${esc(m.download_url)}" download="${esc(m.download_name || '')}"
             title="Download the original file" aria-label="Download ${esc(m.title)}">⬇</a>`
        : `<span class="dl" aria-disabled="true" title="This item cannot be downloaded">⬇</span>`;
      return `<div class="pitem ${current && current.id === m.id ? 'on' : ''}" data-id="${m.id}">
        ${m.poster ? `<img src="${esc(m.poster)}" alt="">` : ''}
        <div class="grow">
          <div class="nm trunc">${esc(m.title)}</div>
          <div class="mt">${esc(m.year || '')} · ${fmtDuration(m.duration_s)} ·
            <span class="badge ${cls}" style="font-size:10px;padding:0 6px">${badge}</span>
          </div>
          ${pct ? `<div class="prog"><i style="width:${pct}%"></i></div>` : ''}
        </div>
        ${dl}
      </div>`;
    }).join('') : '<div class="mut center" style="padding:26px 14px">Nothing matches.</div>';

    $$('#p-list .pitem').forEach(el => el.addEventListener('click', (e) => {
      if (e.target.closest('.dl')) return;      // the download button plays nothing
      play(items.find(m => m.id === Number(el.dataset.id)));
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
      html5: { vhs: { overrideNative: true } },
      controlBar: {
        pictureInPictureToggle: true,
        playbackRateMenuButton: true,
        volumePanel: { inline: false },
      },
    });
    player.on('timeupdate', () => {
      if (jumping) return;
      const d = player.duration();
      if (isFinite(d) && d > 0) $('#jump-in').value = fmtDuration(player.currentTime());
    });
    player.on('ended', () => {
      remember();
      if ($('#opt-next').checked) move(1);
    });
    return player;
  }

  function play(item) {
    if (!item) return;
    current = item;
    renderList();

    $('#now-title').textContent = item.title;
    const bits = [item.year, fmtDuration(item.duration_s),
      item.width ? `${item.width}×${item.height}` : null,
      item.video ? item.video.toUpperCase() : null,
      item.container ? item.container.toUpperCase() : null].filter(Boolean);
    $('#now-meta').textContent = bits.join(' · ');
    if (item.poster) { const im = $('#now-poster'); im.src = item.poster; im.hidden = false; }
    else $('#now-poster').hidden = true;

    /* the download button points at the original file of this item */
    const dl = $('#btn-download');
    if (item.downloadable) {
      dl.href = item.download_url;
      dl.setAttribute('download', item.download_name || '');
      dl.removeAttribute('aria-disabled');
      dl.title = 'Download the original file (' + (item.download_name || '') + ')';
      dl.classList.remove('mut');
    } else {
      dl.removeAttribute('href');
      dl.setAttribute('aria-disabled', 'true');
      dl.title = 'This item cannot be downloaded (an HLS stream has no file behind it)';
      dl.classList.add('mut');
    }

    if (item.delivery === 'embed') {
      $('#v').classList.add('hidden');
      $('#yt').classList.remove('hidden');
      const id = (item.src || '').split('v=')[1] ? (item.src.split('v=')[1].split('&')[0]) : '';
      $('#yt').src = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1`;
      toast('yt-dlp could not resolve this link — the embedded player is used', 'warn');
      return;
    }
    $('#yt').classList.add('hidden');
    $('#v').classList.remove('hidden');

    const p = ensurePlayer();
    p.poster(item.poster || '');
    p.src(item.src);
    p.play().catch(() => { /* the browser may block the autoplay */ });

    if (item.progress_s && $('#opt-resume').checked && item.duration_s
        && item.progress_s < item.duration_s - 15) {
      p.one('loadedmetadata', () => {
        p.currentTime(item.progress_s);
        toast('Resumed at ' + fmtDuration(item.progress_s));
      });
    }
  }

  function remember() {
    if (!current || !player) return;
    const t = player.currentTime();
    if (!t || t < 5) return;
    api('/api/player/progress', {
      method: 'POST',
      body: { media_id: current.id, position_s: Math.floor(t) },
    }).catch(() => { /* the position is a nicety, not a must */ });
  }

  function step(sec) {
    if (!player || !current) return;
    if (current.delivery === 'embed') { toast('Seeking is handled by the embedded player', 'warn'); return; }
    const p = player;
    p.currentTime(Math.max(0, Math.min((p.duration() || 0), p.currentTime() + sec)));
  }

  function jumpTo(str) {
    if (!player || !current) return;
    const t = parseTime(str);
    if (t === null) { toast('Use hh:mm:ss or mm:ss', 'err'); return; }
    const dur = player.duration();
    if (isFinite(dur) && dur > 0 && t > dur) {
      toast('That is past the end of the file (' + fmtDuration(dur) + ')', 'warn');
    }
    jumping = true;
    player.currentTime(Math.max(0, t));
    setTimeout(() => { jumping = false; }, 400);
    toast('Jumped to ' + fmtDuration(t));
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
  $('#jump-in').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); jumpTo($('#jump-in').value); }
  });
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

  load();
})();
