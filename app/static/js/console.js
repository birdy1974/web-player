/* web-player — management console (the real application).
 *
 * Everything is fetched from /api/console/*. The DOM shape is the same as the
 * mock-up in ../../mockup, so what was tested there is what runs here.
 */
(function () {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const state = {
    media: [], users: [], groups: [], receivers: [], overview: null,
    players: {},
  };

  /* ------------------------------------------------------------------ */
  /* transport                                                          */
  /* ------------------------------------------------------------------ */
  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      window.location.href = '/console/login';
      throw new Error('not authenticated');
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
    if (!res.ok) throw new Error((data && data.detail) || res.statusText);
    return data;
  }

  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 2800);
  }

  function modal(title, bodyHtml, footHtml, wide) {
    const root = $('#modal-root');
    root.innerHTML = `<div class="modal-bg"><div class="modal ${wide ? 'wide' : ''}">
      <div class="modal-head"><h3>${title}</h3><span class="spacer"></span><button class="x" data-close>&times;</button></div>
      <div class="modal-body">${bodyHtml}</div>
      ${footHtml ? `<div class="modal-foot">${footHtml}</div>` : ''}
    </div></div>`;
    const bg = $('.modal-bg', root);
    bg.addEventListener('click', (e) => { if (e.target === bg) closeModal(); });
    $('[data-close]', root).addEventListener('click', closeModal);
    document.addEventListener('keydown', escClose);
    return root;
  }
  function closeModal() {
    $('#modal-root').innerHTML = '';
    document.removeEventListener('keydown', escClose);
  }
  function escClose(e) { if (e.key === 'Escape') closeModal(); }

  const fmtDuration = (sec) => {
    if (sec === null || sec === undefined) return '—';
    const s = Math.max(0, Math.round(sec));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
  };
  const fmtSize = (bytes) => {
    if (!bytes) return '—';
    const mb = bytes / 1048576;
    return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb.toFixed(1) + ' MB';
  };

  /* ------------------------------------------------------------------ */
  /* navigation                                                         */
  /* ------------------------------------------------------------------ */
  function showTab(name) {
    $$('#tabs .tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
    ['dash', 'media', 'users', 'groups', 'e2', 'logs', 'settings'].forEach(k => {
      $('#tab-' + k).classList.toggle('hidden', k !== name);
    });
    if (name === 'dash') renderDash();
    if (name === 'media') renderMedia();
    if (name === 'users') renderUsers();
    if (name === 'groups') renderGroups();
    if (name === 'e2') renderE2();
    if (name === 'logs') { renderLogs(); startLogTimer(); } else stopLogTimer();
    if (name === 'settings') renderSettings();
  }
  $$('#tabs .tab').forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));

  /* ------------------------------------------------------------------ */
  /* DASHBOARD                                                          */
  /* ------------------------------------------------------------------ */
  function renderDash() {
    const o = state.overview || {};
    const vaapi = o.vaapi || {};
    $('#dash-stats').innerHTML = [
      stat('Media items', o.media_count ?? 0,
        `${o.enabled_count ?? 0} enabled · ${o.playable_direct ?? 0} direct · ${o.needs_hls ?? 0} HLS`),
      stat('Users', o.user_count ?? 0, `${(o.per_user || []).filter(u => u.enabled).length} enabled`),
      stat('Groups', o.group_count ?? 0, 'playlist shelves'),
      stat('Transcoding', (vaapi.mode || '?').toUpperCase(),
        vaapi.available ? 'Intel VAAPI is available' : 'no VAAPI — software fallback'),
    ].join('');

    // the trouble in the log, at a glance
    const counts = (o.log_counts || {});
    const bad = (counts.ERROR || 0) + (counts.CRITICAL || 0);
    const warn = counts.WARNING || 0;
    const logStat = document.getElementById('dash-log');
    if (logStat) {
      logStat.innerHTML = `<b>${bad}</b> error(s), <b>${warn}</b> warning(s) in the log`
        + (bad || warn
          ? ' — open the <a href="#" id="dash-log-open">Logs</a> tab to see them.'
          : ' — nothing to report.');
      const link = document.getElementById('dash-log-open');
      if (link) link.addEventListener('click', (e) => {
        e.preventDefault();
        const tab = $$('#tabs .tab').find(t => t.dataset.tab === 'logs');
        if (tab) tab.click();
      });
    }

    const attention = [];
    (state.media || []).forEach(m => {
      if (m.delivery === 'hls' && m.outputs.web) {
        attention.push(['warn', `<b>${esc(m.title)}</b> — ${esc((m.probe.container || '?').toUpperCase())}/${esc((m.probe.video || '?').toUpperCase())} is not browser-playable; it is served as on-demand HLS.`]);
      }
      if (!m.enabled) attention.push(['mut', `<b>${esc(m.title)}</b> — globally disabled, hidden from every user.`]);
      if (m.source_type === 'youtube' && m.resolved_state === 'failed') {
        attention.push(['err', `<b>${esc(m.title)}</b> — yt-dlp could not resolve this link: ${esc(m.resolved_error || 'unknown error')}. It falls back to the embedded player and cannot be pushed to a bouquet.`]);
      }
      if (m.source_type === 'youtube' && m.resolved_state === 'resolved' && m.outputs.enigma2) {
        attention.push(['ok', `<b>${esc(m.title)}</b> — resolved with yt-dlp, so it can be written into the bouquet.`]);
      }
    });
    $('#dash-attention').innerHTML = attention.length
      ? `<table class="tbl"><tbody>${attention.map(a =>
        `<tr><td style="width:90px"><span class="badge ${a[0] === 'mut' ? 'info' : a[0]}">${a[0] === 'mut' ? 'info' : a[0]}</span></td><td>${a[1]}</td></tr>`).join('')}</tbody></table>`
      : '<p class="mut">Nothing to report — everything is consistent.</p>';

    const rows = (o.per_user || []).map(u => `<tr>
      <td><b>${esc(u.name)}</b>${u.role === 'admin' ? ' <span class="badge info">admin</span>' : ''}</td>
      <td>${u.enabled ? '<span class="badge ok">enabled</span>' : '<span class="badge err">disabled</span>'}</td>
      <td><b>${u.visible}</b> / ${o.media_count ?? 0}</td>
      <td>${u.overrides ? `<span class="badge info">${u.overrides}</span>` : '<span class="mut2">—</span>'}</td>
      <td>${esc(u.max_streams)}</td>
      <td>${esc(u.expires_at || '—')}</td>
      <td class="mut small">${u.last_login_at ? esc(u.last_login_at.replace('T', ' ').slice(0, 16)) : '—'}</td>
    </tr>`).join('');
    $('#dash-visibility').innerHTML = `<table class="tbl">
      <thead><tr><th>User</th><th>Status</th><th>Visible items</th><th>Overrides</th><th>Max streams</th><th>Expires</th><th>Last login</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="mut center">No users yet.</td></tr>'}</tbody></table>`;
  }

  function stat(k, v, d) {
    return `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="d">${esc(d)}</div></div>`;
  }

  /* ------------------------------------------------------------------ */
  /* MEDIA                                                              */
  /* ------------------------------------------------------------------ */
  function deliveryBadges(m) {
    const delivery = m.effective_delivery || m.delivery;
    let out = '';
    if (m.source_type === 'youtube') {
      if (m.resolved_state === 'resolved') out += `<span class="badge ok">yt-dlp ✓</span>`;
      else if (m.resolved_state === 'failed') out += `<span class="badge err" title="${esc(m.resolved_error || '')}">yt-dlp ✕</span>`;
      else if (m.resolved_state === 'resolving') out += `<span class="badge warn">yt-dlp …</span>`;
      else out += `<span class="badge">yt-dlp —</span>`;
    }
    if (delivery === 'direct') out += `<span class="badge ok" title="served with HTTP Range — exact seeking, 0 % CPU">direct</span>`;
    else if (delivery === 'hls') {
      const vaapi = ['hevc', 'vp9', 'av1'].indexOf(String((m.probe || {}).video || '').toLowerCase()) !== -1;
      out += vaapi
        ? `<span class="badge warn" title="VAAPI transcode to 1080p H.264">HLS · VAAPI</span>`
        : `<span class="badge warn" title="ffmpeg remux to segmented HLS — 0 % CPU, copy">HLS · remux</span>`;
    } else out += `<span class="badge purple">embed</span>`;
    return out;
  }

  function renderMedia() {
    const gsel = $('#m-group');
    gsel.innerHTML = '<option value="">All groups</option>' +
      state.groups.map(g => `<option value="${g.id}">${esc(g.name)}</option>`).join('');

    $('#c-media').textContent = state.media.length;
    $('#c-users').textContent = state.users.length;

    const q = $('#m-search').value.trim().toLowerCase();
    const kind = $('#m-kind').value, grp = $('#m-group').value;
    const src = $('#m-source').value, play = $('#m-play').value;
    const rows = state.media.filter(m => {
      if (q && !(`${m.title} ${m.path || ''} ${m.url || ''}`.toLowerCase().includes(q))) return false;
      if (kind && m.kind !== kind) return false;
      if (grp && String(m.group_id) !== grp) return false;
      if (src && m.source_type !== src) return false;
      if (play && m.delivery !== play) return false;
      return true;
    });

    $('#m-count').textContent = rows.length + ' of ' + state.media.length + ' items';
    $('#m-table thead').innerHTML = `<tr>
      <th style="width:44px"></th><th>Title</th><th>Source</th><th>Playable in browser</th>
      <th>Group</th><th>Outputs</th><th colspan="${state.users.length}" class="center">Visible to (${state.users.length})</th>
      <th style="width:110px">Order</th><th style="width:150px"></th></tr>`;

    $('#m-table tbody').innerHTML = rows.length ? rows.map(m => {
      const visCells = state.users.map(u => {
        const info = (m.per_user || {})[String(u.id)] || {};
        const ov = info.override;
        const cls = ov ? 'ovr' : (info.visible ? 'yes' : 'no');
        const glyph = ov === 'allow' ? 'G' : ov === 'deny' ? '×' : (info.visible ? '✓' : '·');
        const next = ov === undefined ? 'grant' : (ov === 'allow' ? 'hide' : 'reset to the group rule');
        return `<td class="usercell"><span class="vis-dot ${cls}" data-item="${m.id}" data-user="${u.id}"
                  title="${esc(u.name)}: ${esc(info.reason || '')} — click to ${next}">${glyph}</span></td>`;
      }).join('');
      const detail = (m.probe || {}).width
        ? `${esc((m.probe.container || '').toUpperCase())} · ${esc((m.probe.video || '').toUpperCase())} ${m.probe.width}×${m.probe.height}`
        : esc((m.probe || {}).container ? (m.probe.container || '').toUpperCase() : '—');
      return `<tr data-id="${m.id}">
        <td>${m.poster ? `<img class="thumb" src="/poster/${esc(m.poster)}" alt="">` : ''}</td>
        <td><div class="col" style="gap:1px">
              <b class="trunc" style="max-width:240px">${esc(m.title)}</b>
              <span class="mut small">${esc(m.year || '')} · ${fmtDuration(m.duration_s)} · ${fmtSize(m.size_bytes)}</span>
            </div></td>
        <td><div class="col" style="gap:2px">
              <span class="badge ${m.source_type === 'local' ? '' : 'info'}">${esc(m.source_type)}</span>
              <span class="mut small mono trunc" style="max-width:180px" title="${esc(m.path || m.url)}">${esc(m.path || m.url || '')}</span>
            </div></td>
        <td><div class="col" style="gap:2px">${deliveryBadges(m)}<span class="mut small">${detail}</span></div></td>
        <td>${esc(m.group_name || '—')}</td>
        <td class="nowrap">
          <label class="row" style="gap:4px"><input type="checkbox" data-out="web" ${m.outputs.web ? 'checked' : ''}> ▶ web</label>
          <label class="row" style="gap:4px"><input type="checkbox" data-out="enigma2" ${m.outputs.enigma2 ? 'checked' : ''}> 📺 e2</label>
        </td>
        ${visCells}
        <td><input type="number" class="sort-in" value="${m.sort_order}" style="width:58px"></td>
        <td class="nowrap">
          ${m.source_type === 'youtube' ? `<button class="btn sm" data-resolve="${m.id}" title="run yt-dlp again">⟳ resolve</button>` : ''}
          <button class="btn sm" data-edit="${m.id}">edit</button>
          <button class="btn sm danger" data-del="${m.id}">✕</button>
        </td>
      </tr>`;
    }).join('') : '<tr><td colspan="12" class="center mut" style="padding:26px">No media matches these filters.</td></tr>';

    $$('#m-table tbody [data-out]').forEach(cb => cb.addEventListener('change', async () => {
      const id = Number(cb.closest('tr').dataset.id);
      const item = state.media.find(m => m.id === id);
      try {
        await api(`/api/console/media/${id}`, {
          method: 'PUT',
          body: { output_web: cb.dataset.out === 'web' ? cb.checked : item.outputs.web,
                  output_enigma2: cb.dataset.out === 'enigma2' ? cb.checked : item.outputs.enigma2 },
        });
        toast(`${item.title}: ${cb.dataset.out} output ${cb.checked ? 'enabled' : 'disabled'}`, 'ok');
        await refresh();
      } catch (e) { toast(e.message, 'err'); }
    }));

    $$('#m-table tbody .sort-in').forEach(inp => inp.addEventListener('change', async () => {
      const id = Number(inp.closest('tr').dataset.id);
      try {
        await api(`/api/console/media/${id}`, { method: 'PUT', body: { sort_order: Number(inp.value) || 0 } });
        await refresh();
      } catch (e) { toast(e.message, 'err'); }
    }));

    $$('#m-table tbody [data-edit]').forEach(b => b.addEventListener('click', () => editMedia(Number(b.dataset.edit))));
    $$('#m-table tbody [data-del]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.del);
      const item = state.media.find(m => m.id === id);
      if (!confirm(`Delete “${item.title}” from the library?`)) return;
      try {
        await api(`/api/console/media/${id}`, { method: 'DELETE' });
        toast('Deleted ' + item.title, 'warn');
        await refresh();
      } catch (e) { toast(e.message, 'err'); }
    }));
    $$('#m-table tbody [data-resolve]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.resolve);
      b.disabled = true; b.textContent = '…';
      try {
        const r = await api(`/api/console/media/${id}/resolve`, { method: 'POST' });
        toast(r.state === 'resolved' ? 'yt-dlp resolved the link' : 'yt-dlp failed: ' + (r.error || ''),
          r.state === 'resolved' ? 'ok' : 'err');
        await refresh();
      } catch (e) { toast(e.message, 'err'); b.disabled = false; b.textContent = '⟳ resolve'; }
    }));
    $$('#m-table tbody .vis-dot').forEach(dot => dot.addEventListener('click', async () => {
      const id = Number(dot.dataset.item), uid = Number(dot.dataset.user);
      const item = state.media.find(m => m.id === id);
      const cur = ((item.per_user || {})[String(uid)] || {}).override;
      const next = cur === undefined ? 'allow' : (cur === 'allow' ? 'deny' : null);
      try {
        await api(`/api/console/media/${id}/visibility`, { method: 'POST', body: { user_id: uid, state: next } });
        await refresh();
        toast(`${item.title} → ${state.users.find(u => u.id === uid).name}: ` +
          (next === 'allow' ? 'granted' : next === 'deny' ? 'hidden' : 'back to the group rule'), 'ok');
      } catch (e) { toast(e.message, 'err'); }
    }));
  }

  ['#m-search', '#m-kind', '#m-group', '#m-source', '#m-play'].forEach(s => {
    $(s).addEventListener('input', renderMedia);
    $(s).addEventListener('change', renderMedia);
  });

  $('#btn-add-media').addEventListener('click', () => editMedia(null));
  $('#btn-browse').addEventListener('click', () => openLibraryBrowser());
  $('#btn-scan').addEventListener('click', async () => {
    if (!confirm('Scan the media root and add everything that is not in the library yet?')) return;
    const btn = $('#btn-scan');
    btn.disabled = true; btn.textContent = 'scanning…';
    try {
      const r = await api('/api/console/media/scan', { method: 'POST', body: {} });
      toast(`Added ${r.added} item(s), skipped ${r.skipped}, probed ${r.probed}`, 'ok');
      await refresh();
    } catch (e) { toast(e.message, 'err'); }
    btn.disabled = false; btn.textContent = 'Scan media root…';
  });

  function editMedia(id) {
    const item = id ? state.media.find(m => m.id === id) : null;
    const isNew = !item;
    const m = item || {
      title: '', year: new Date().getFullYear(), kind: 'movie',
      group_id: state.groups[0] ? state.groups[0].id : null,
      source_type: 'local', path: '', url: '', delivery: 'auto',
      enabled: true, outputs: { web: true, enigma2: false }, sort_order: state.media.length * 10,
    };
    const body = `
      <div id="f-form">
      <div class="grid2">
        <label class="field"><span>Title</span><input type="text" id="f-title" value="${esc(m.title)}"></label>
        <label class="field"><span>Year</span><input type="number" id="f-year" value="${esc(m.year || '')}"></label>
        <label class="field"><span>Kind</span><select id="f-kind">
          ${['movie', 'series', 'clip', 'audio'].map(k => `<option ${m.kind === k ? 'selected' : ''}>${k}</option>`).join('')}
        </select></label>
        <label class="field"><span>Group (playlist shelf)</span><select id="f-group">
          <option value="">— none —</option>
          ${state.groups.map(g => `<option value="${g.id}" ${m.group_id === g.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}
        </select></label>
      </div>
      <div class="grid2" style="margin-top:12px">
        <label class="field"><span>Source type</span><select id="f-source">
          <option value="local" ${m.source_type === 'local' ? 'selected' : ''}>Local file (on the NAS)</option>
          <option value="url" ${m.source_type === 'url' ? 'selected' : ''}>Direct URL (mp4 / m3u8 / …)</option>
          <option value="youtube" ${m.source_type === 'youtube' ? 'selected' : ''}>YouTube</option>
        </select></label>
        <label class="field" id="f-path-wrap"><span>Path inside the media root</span>
          <div class="row" style="gap:6px;flex-wrap:nowrap">
            <input type="text" id="f-path" value="${esc(m.path || '')}" placeholder="movies/aurora-drift.mp4">
            <button class="btn sm" id="f-browse" type="button" title="Pick a file in the media root">📁 browse</button>
          </div></label>
        <label class="field hidden" id="f-url-wrap"><span>URL</span>
          <input type="url" id="f-url" value="${esc(m.url || '')}" placeholder="https://…"></label>
      </div>
      <div class="grid2" style="margin-top:12px">
        <label class="field"><span>Delivery</span><select id="f-delivery">
          <option value="auto" ${m.delivery === 'auto' ? 'selected' : ''}>Automatic (from the ffprobe result)</option>
          <option value="direct" ${m.delivery === 'direct' ? 'selected' : ''}>Direct (Range requests, no CPU)</option>
          <option value="hls" ${m.delivery === 'hls' ? 'selected' : ''}>On-demand HLS (ffmpeg, seekable)</option>
        </select></label>
        <label class="field"><span>Probe result</span>
          <input type="text" value="${esc((m.probe || {}).container || 'not probed yet')} ${esc((m.probe || {}).video || '')}" disabled></label>
      </div>
      <div class="grid2" style="margin-top:12px">
        <label class="field"><span>Outputs</span>
          <div class="row" style="gap:14px;padding-top:4px">
            <label class="row" style="gap:5px"><input type="checkbox" id="f-out-web" ${m.outputs.web ? 'checked' : ''}> ▶ public web player</label>
            <label class="row" style="gap:5px"><input type="checkbox" id="f-out-e2" ${m.outputs.enigma2 ? 'checked' : ''}> 📺 Enigma2 bouquet</label>
          </div></label>
        <label class="field"><span>Enabled</span>
          <div class="row" style="gap:9px;padding-top:4px">
            <label class="switch"><input type="checkbox" id="f-enabled" ${m.enabled ? 'checked' : ''}><span class="track"></span></label>
            <span class="mut small">off = hidden for everybody</span>
          </div></label>
      </div>
      </div>
      <div id="f-browser" class="hidden"></div>`;

    const root = modal(isNew ? 'Add media' : 'Edit ' + m.title, body,
      `<button class="btn" data-close>Cancel</button><button class="btn pri" id="f-save">${isNew ? 'Add item' : 'Save'}</button>`, true);

    const srcSel = $('#f-source', root);
    function syncSource() {
      const s = srcSel.value;
      $('#f-path-wrap', root).classList.toggle('hidden', s !== 'local');
      $('#f-url-wrap', root).classList.toggle('hidden', s === 'local');
    }
    srcSel.addEventListener('change', syncSource); syncSource();

    /* the file browser picks the path — the form hides while it is open */
    const panel = browserPanel({
      onPick: (entry) => {
        $('#f-path', root).value = entry.path;
        if (!$('#f-title', root).value.trim()) {
          $('#f-title', root).value = entry.name.replace(/\.[^.]+$/, '')
            .replace(/[._]+/g, ' ').trim();
        }
        showForm();
      },
    });
    const browserHost = $('#f-browser', root);
    browserHost.appendChild(panel.el);

    function showForm() {
      $('#f-form', root).classList.remove('hidden');
      browserHost.classList.add('hidden');
      $('#f-save', root).classList.remove('hidden');
      $('.modal-head h3', root).textContent = isNew ? 'Add media' : 'Edit ' + m.title;
    }
    function showBrowser() {
      $('#f-form', root).classList.add('hidden');
      browserHost.classList.remove('hidden');
      $('#f-save', root).classList.add('hidden');
      $('.modal-head h3', root).textContent = 'Pick a file in the media root';
      if (!panel.el.dataset.loaded) { panel.el.dataset.loaded = '1'; panel.load(''); }
    }
    $('#f-browse', root).addEventListener('click', showBrowser);

    $('#f-save', root).addEventListener('click', async () => {
      const body = {
        title: $('#f-title', root).value.trim(),
        year: Number($('#f-year', root).value) || null,
        kind: $('#f-kind', root).value,
        group_id: $('#f-group', root).value ? Number($('#f-group', root).value) : null,
        source_type: srcSel.value,
        path: $('#f-path', root).value.trim(),
        url: $('#f-url', root).value.trim(),
        delivery: $('#f-delivery', root).value,
        output_web: $('#f-out-web', root).checked,
        output_enigma2: $('#f-out-e2', root).checked,
        enabled: $('#f-enabled', root).checked,
        sort_order: m.sort_order || 0,
      };
      if (!body.title) { toast('A title is required', 'err'); return; }
      try {
        if (isNew) await api('/api/console/media', { method: 'POST', body });
        else await api(`/api/console/media/${id}`, { method: 'PUT', body });
        closeModal();
        toast(isNew ? 'Added ' + body.title : 'Saved ' + body.title, 'ok');
        await refresh();
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  /* ------------------------------------------------------------------ */
  /* FILE BROWSER                                                       */
  /* ------------------------------------------------------------------ */
  /* An explorer for the media root: folders on top, files below, the
   * breadcrumbs to jump somewhere else. It is used twice — as a picker for
   * the "path" field of a media item, and as a way to put files or whole
   * folders into the library without typing a path. */
  function browserPanel(opts) {
    const o = opts || {};
    const el = document.createElement('div');
    el.className = 'browser';
    el.innerHTML = `
      <div class="br-top">
        <button class="btn sm" type="button" data-br="up" title="One level up">↑</button>
        <div class="br-crumbs"></div>
      </div>
      <input type="search" class="br-filter" placeholder="Filter this folder…"
             aria-label="Filter this folder">
      <div class="br-list"></div>
      <div class="br-foot"><span class="br-sel mut small">nothing selected</span></div>`;

    const crumbs = $('.br-crumbs', el);
    const listEl = $('.br-list', el);
    const filter = $('.br-filter', el);
    const selLine = $('.br-sel', el);
    const upBtn = $('[data-br="up"]', el);

    let listing = { path: '', parent: null, breadcrumbs: [], entries: [] };
    let selected = null;

    async function load(path) {
      listEl.innerHTML = '<div class="mut small pad">loading…</div>';
      try {
        listing = await api('/api/console/browse?path=' + encodeURIComponent(path || ''));
      } catch (e) {
        listing = { path: '', parent: null, breadcrumbs: [], entries: [] };
        listEl.innerHTML = `<div class="br-err">${esc(e.message)}</div>`;
      }
      selected = null;
      renderCrumbs();
      renderList();
      update();
    }

    function renderCrumbs() {
      const parts = listing.breadcrumbs || [];
      const crumbHtml = parts.map((b, i) => (
        `${i ? '<span class="sep">/</span>' : ''}` +
        `<a href="#" data-crumb="${esc(b.path)}">${esc(b.name)}</a>`
      )).join('');
      crumbs.innerHTML = crumbHtml +
        (listing.truncated ? '<span class="sep">/</span><span class="mut2 small">…</span>' : '');
      $$('[data-crumb]', crumbs).forEach(a => a.addEventListener('click', (e) => {
        e.preventDefault();
        load(a.dataset.crumb);
      }));
    }

    function visibleEntries() {
      const q = filter.value.trim().toLowerCase();
      const entries = listing.entries || [];
      return q ? entries.filter(e => e.name.toLowerCase().includes(q)) : entries;
    }

    function renderList() {
      const entries = visibleEntries();
      if (!entries.length) {
        const any = (listing.entries || []).length;
        listEl.innerHTML = `<div class="mut small pad">${any
          ? 'Nothing matches the filter.' : 'This folder is empty.'}</div>`;
        return;
      }
      listEl.innerHTML = entries.map(e => {
        const icon = e.type === 'dir' ? '📁' : (e.kind === 'audio' ? '🎵'
          : (e.kind === 'video' ? '🎬' : '📄'));
        const badge = e.in_library ? '<span class="badge ok">in library</span>' : '';
        const on = selected && selected.path === e.path ? ' on' : '';
        return `<div class="br-row${on}" data-path="${esc(e.path)}" data-type="${e.type}"
                     data-media="${e.media ? '1' : '0'}" title="${esc(e.path)}">
          <span class="br-ic">${icon}</span>
          <span class="br-nm trunc">${esc(e.name)}</span>
          ${badge}
          <span class="spacer"></span>
          ${e.type === 'file'
            ? `<span class="mut small">${fmtSize(e.size)}</span>`
            : '<span class="mut2 small">folder</span>'}
        </div>`;
      }).join('');
      $$('.br-row', listEl).forEach(row => {
        const entry = entries.find(x => x.path === row.dataset.path);
        row.addEventListener('click', () => {
          selected = (selected && selected.path === entry.path) ? null : entry;
          renderList();
          update();
        });
        row.addEventListener('dblclick', () => {
          if (entry.type === 'dir') load(entry.path);
          else if (entry.media && o.onPick) o.onPick(entry);
        });
      });
    }

    function update() {
      const s = selected;
      selLine.textContent = !s ? 'nothing selected'
        : (s.type === 'dir' ? 'folder: ' + s.path : 'file: ' + s.path);
      upBtn.disabled = listing.path === '' && !listing.parent;
      if (o.onChange) o.onChange(selected, panel);
    }

    filter.addEventListener('input', renderList);
    upBtn.addEventListener('click', () => {
      if (listing.parent !== null && listing.parent !== undefined) load(listing.parent);
    });

    const panel = { el, load, get selected() { return selected; } };
    return panel;
  }

  /* The browser as its own window: pick files and folders for the library. */
  function openLibraryBrowser() {
    const groupOptions = state.groups
      .map(g => `<option value="${g.id}">${esc(g.name)}</option>`).join('');
    const root = modal('Browse the media root', '<div id="br-host"></div>',
      `<span class="mut small">new items go to</span>
       <select id="br-group"><option value="">— no group —</option>${groupOptions}</select>
       <span class="spacer"></span>
       <button class="btn" data-close>Close</button>
       <button class="btn" id="br-add-folder" disabled>Add the folder</button>
       <button class="btn pri" id="br-add-file" disabled>Add the file</button>`, true);

    const addFile = $('#br-add-file', root);
    const addFolder = $('#br-add-folder', root);
    const groupSel = $('#br-group', root);

    const panel = browserPanel({
      onChange: (selected) => {
        addFile.disabled = !(selected && selected.type === 'file' && selected.media);
        addFolder.disabled = !(selected && selected.type === 'dir');
      },
      onPick: (entry) => { addFile.click(); },
    });
    $('#br-host', root).appendChild(panel.el);
    panel.load('');

    addFile.addEventListener('click', async () => {
      const entry = panel.selected;
      if (!entry) return;
      addFile.disabled = true;
      try {
        const title = entry.name.replace(/\.[^.]+$/, '').replace(/[._]+/g, ' ').trim() || entry.name;
        await api('/api/console/media', {
          method: 'POST',
          body: {
            title, source_type: 'local', path: entry.path,
            group_id: groupSel.value ? Number(groupSel.value) : null,
            output_web: true, output_enigma2: false, enabled: true, delivery: 'auto',
          },
        });
        toast('Added ' + entry.name + ' to the library', 'ok');
        await refresh();
        panel.load(panel.selected ? panel.selected.path : '');
      } catch (e) { toast(e.message, 'err'); }
      addFile.disabled = false;
    });

    addFolder.addEventListener('click', async () => {
      const entry = panel.selected;
      if (!entry) return;
      if (!confirm(`Add every media file in “${entry.path}” to the library?`)) return;
      addFolder.disabled = true;
      addFolder.textContent = 'adding…';
      try {
        const r = await api('/api/console/media/scan', {
          method: 'POST',
          body: { folder: entry.path, group_id: groupSel.value ? Number(groupSel.value) : null },
        });
        toast(`Added ${r.added} item(s), skipped ${r.skipped}, probed ${r.probed}`, 'ok');
        await refresh();
        panel.load(entry.path);
      } catch (e) { toast(e.message, 'err'); }
      addFolder.disabled = false;
      addFolder.textContent = 'Add the folder';
    });
  }

  /* ------------------------------------------------------------------ */
  /* USERS                                                              */
  /* ------------------------------------------------------------------ */
  function renderUsers() {
    $('#u-count').textContent = state.users.length + ' users';
    const rows = state.users.map(u => {
      const groups = (u.group_ids || []).map(gid => {
        const g = state.groups.find(x => x.id === gid);
        return g ? `<span class="chip">${esc(g.name)}</span>` : '';
      }).join(' ');
      const ovr = Object.keys(u.overrides || {}).length;
      const visible = state.media.filter(m => ((m.per_user || {})[String(u.id)] || {}).visible).length;
      return `<tr data-id="${u.id}">
        <td><b>${esc(u.name)}</b></td>
        <td>${u.role === 'admin' ? '<span class="badge info">administrator</span>' : 'user'}</td>
        <td>${u.enabled ? '<span class="badge ok">enabled</span>' : '<span class="badge err">disabled</span>'}</td>
        <td>${groups || '<span class="mut2">none</span>'}</td>
        <td><b>${visible}</b> / ${state.media.length}</td>
        <td>${ovr ? `<span class="badge info">${ovr}</span>` : '<span class="mut2">—</span>'}</td>
        <td>${esc(u.max_streams)}</td>
        <td>${esc(u.expires_at || '—')}</td>
        <td class="mut small">${u.last_login_at ? esc(u.last_login_at.replace('T', ' ').slice(0, 16)) : '—'}</td>
        <td class="nowrap">
          <button class="btn sm" data-uedit="${u.id}">edit</button>
          <button class="btn sm" data-ustate="${u.id}">${u.enabled ? 'disable' : 'enable'}</button>
          ${u.role === 'admin' ? '' : `<button class="btn sm danger" data-udel="${u.id}">✕</button>`}
        </td>
      </tr>`;
    }).join('');
    $('#u-table tbody').innerHTML = rows || '<tr><td colspan="10" class="mut center">No users yet.</td></tr>';

    $$('#u-table tbody [data-uedit]').forEach(b => b.addEventListener('click', () => editUser(Number(b.dataset.uedit))));
    $$('#u-table tbody [data-ustate]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.ustate);
      const u = state.users.find(x => x.id === id);
      try {
        await api(`/api/console/users/${id}`, { method: 'PUT', body: { enabled: !u.enabled } });
        await refresh();
        toast(`${u.name} ${u.enabled ? 'disabled' : 'enabled'}`, u.enabled ? 'warn' : 'ok');
      } catch (e) { toast(e.message, 'err'); }
    }));
    $$('#u-table tbody [data-udel]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.udel);
      const u = state.users.find(x => x.id === id);
      if (!confirm(`Delete user “${u.name}”?`)) return;
      try {
        await api(`/api/console/users/${id}`, { method: 'DELETE' });
        await refresh();
        toast('Deleted ' + u.name, 'warn');
      } catch (e) { toast(e.message, 'err'); }
    }));
  }

  function editUser(id) {
    const u = id ? state.users.find(x => x.id === id) : null;
    const isNew = !u;
    const user = u || { name: '', role: 'user', enabled: true, max_streams: 2, expires_at: null, group_ids: [], overrides: {} };
    const body = `
      <div class="grid3">
        <label class="field"><span>Username</span><input type="text" id="u-name" value="${esc(user.name)}"></label>
        <label class="field"><span>Password</span><input type="password" id="u-pw" placeholder="${isNew ? 'required' : 'unchanged'}"></label>
        <label class="field"><span>Max simultaneous streams</span><input type="number" id="u-max" min="1" max="10" value="${esc(user.max_streams)}"></label>
        <label class="field"><span>Expires (YYYY-MM-DD, empty = never)</span><input type="text" id="u-exp" value="${esc(user.expires_at || '')}"></label>
        <label class="field"><span>Status</span>
          <div class="row" style="gap:9px;padding-top:4px">
            <label class="switch"><input type="checkbox" id="u-enabled" ${user.enabled ? 'checked' : ''}><span class="track"></span></label>
            <span class="mut small">${user.enabled ? 'can sign in' : 'sign-in refused'}</span>
          </div></label>
      </div>
      <h4 style="margin:18px 0 4px;font-size:13px">Playlist access (groups)</h4>
      <p class="mut small" style="margin:0 0 10px">A group is a shelf of media. A user sees every item in the groups ticked here.</p>
      <div class="grp-pick">
        ${state.groups.map(g => `<label class="chip click ${(user.group_ids || []).indexOf(g.id) !== -1 ? 'on' : ''}">
          <input type="checkbox" class="g-pick" value="${g.id}" ${(user.group_ids || []).indexOf(g.id) !== -1 ? 'checked' : ''}> ${esc(g.name)}</label>`).join('')
          || '<span class="mut small">No groups yet — create one on the Groups tab.</span>'}
      </div>
      <h4 style="margin:18px 0 4px;font-size:13px">Per-item exceptions</h4>
      <p class="mut small" style="margin:0 0 10px">Override the group rule for single items. Empty = the group rule decides.</p>
      <div class="picker" id="u-ovr">
        ${state.media.map(m => {
          const ov = (user.overrides || {})[String(m.id)];
          return `<div class="row-i" data-mid="${m.id}">
            <span class="trunc" style="max-width:240px">${esc(m.title)}</span>
            <span class="mut small">${esc(m.group_name || '')}</span>
            <span class="spacer"></span>
            <span class="ovr-pick">
              <button data-ov="allow" class="${ov === 'allow' ? 'on allow' : ''}">grant</button>
              <button data-ov="deny" class="${ov === 'deny' ? 'on deny' : ''}">hide</button>
              <button data-ov="inherit" class="${ov === undefined ? 'on' : ''}">group</button>
            </span></div>`;
        }).join('') || '<div class="row-i mut">No media yet.</div>'}
      </div>`;

    const root = modal(isNew ? 'Add user' : 'Edit ' + user.name, body,
      `<button class="btn" data-close>Cancel</button><button class="btn pri" id="u-save">${isNew ? 'Create user' : 'Save'}</button>`, true);

    $$('.g-pick', root).forEach(cb => cb.addEventListener('change', () => {
      cb.closest('.chip').classList.toggle('on', cb.checked);
    }));

    $$('#u-ovr .ovr-pick button', root).forEach(b => b.addEventListener('click', () => {
      const mid = Number(b.closest('.row-i').dataset.mid);
      const pick = b.dataset.ov;
      if (!user.overrides) user.overrides = {};
      if (pick === 'inherit') delete user.overrides[String(mid)];
      else user.overrides[String(mid)] = pick;
      $$('.ovr-pick button', b.parentElement).forEach(x => x.className = '');
      b.classList.add('on');
      if (pick !== 'inherit') b.classList.add(pick);
    }));

    $('#u-save', root).addEventListener('click', async () => {
      const body = {
        name: $('#u-name', root).value.trim(),
        password: $('#u-pw', root).value,
        max_streams: Number($('#u-max', root).value) || 1,
        expires_at: $('#u-exp', root).value.trim() || null,
        enabled: $('#u-enabled', root).checked,
        group_ids: $$('.g-pick:checked', root).map(cb => Number(cb.value)),
      };
      if (!body.name) { toast('A username is required', 'err'); return; }
      if (isNew && !body.password) { toast('A password is required', 'err'); return; }
      try {
        if (isNew) await api('/api/console/users', { method: 'POST', body });
        else await api(`/api/console/users/${id}`, { method: 'PUT', body });
        closeModal();
        toast(isNew ? 'Created ' + body.name : 'Saved ' + body.name, 'ok');
        await refresh();
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  $('#btn-add-user').addEventListener('click', () => editUser(null));

  /* ------------------------------------------------------------------ */
  /* GROUPS                                                             */
  /* ------------------------------------------------------------------ */
  function renderGroups() {
    $('#g-count').textContent = state.groups.length + ' groups';
    $('#g-table tbody').innerHTML = state.groups.map(g => `<tr data-id="${g.id}">
      <td><span class="chip" style="border-color:${esc(g.color)};color:${esc(g.color)}">${esc(g.name)}</span></td>
      <td>${g.items}</td>
      <td>${g.users}</td>
      <td class="nowrap">
        <button class="btn sm" data-gedit="${g.id}">rename</button>
        <button class="btn sm danger" data-gdel="${g.id}">✕</button>
      </td></tr>`).join('') || '<tr><td colspan="4" class="mut center">No groups yet.</td></tr>';

    $$('#g-table tbody [data-gedit]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.gedit);
      const g = state.groups.find(x => x.id === id);
      const name = prompt('New name for this group:', g.name);
      if (!name || name === g.name) return;
      try {
        await api(`/api/console/groups/${id}`, { method: 'PUT', body: { name } });
        await refresh(); toast('Renamed to ' + name, 'ok');
      } catch (e) { toast(e.message, 'err'); }
    }));
    $$('#g-table tbody [data-gdel]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.gdel);
      const g = state.groups.find(x => x.id === id);
      if (!confirm(`Delete the group “${g.name}”? Its items keep existing but lose their group.`)) return;
      try {
        await api(`/api/console/groups/${id}`, { method: 'DELETE' });
        await refresh(); toast('Deleted ' + g.name, 'warn');
      } catch (e) { toast(e.message, 'err'); }
    }));
  }

  $('#btn-add-group').addEventListener('click', async () => {
    const name = prompt('Name of the new group (a shelf of media):');
    if (!name) return;
    try {
      await api('/api/console/groups', { method: 'POST', body: { name } });
      await refresh(); toast('Created ' + name, 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });

  /* ------------------------------------------------------------------ */
  /* ENIGMA2                                                            */
  /* ------------------------------------------------------------------ */
  function renderE2() {
    $('#recv-list').innerHTML = state.receivers.map(r => `
      <div class="card" style="margin-bottom:10px;background:var(--panel)">
        <div class="row">
          <b class="trunc">${esc(r.name)}</b>
          ${r.enabled ? '<span class="badge ok">enabled</span>' : '<span class="badge">disabled</span>'}
          <span class="spacer"></span>
          <button class="btn sm" data-redit="${r.id}">edit</button>
        </div>
        <div class="mut small mono" style="margin-top:5px">${esc(r.host)}:${esc(r.port)} · ${esc(r.transport)} · service type ${esc(r.service_type)}</div>
        <div class="mut small">bouquet <span class="mono">${esc(r.bouquet_name)}</span> · last push ${r.last_push ? esc(r.last_push.replace('T', ' ').slice(0, 16)) : '—'} (${r.last_items} entries)</div>
      </div>`).join('') || '<p class="mut small">No receivers yet.</p>';
    $$('#recv-list [data-redit]').forEach(b => b.addEventListener('click', () => editReceiver(Number(b.dataset.redit))));

    const sel = $('#e2-recv');
    const prev = sel.value;
    sel.innerHTML = state.receivers.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
    if (prev) sel.value = prev;
    renderBouquet();
  }

  $('#e2-recv').addEventListener('change', renderBouquet);

  async function renderBouquet() {
    const id = $('#e2-recv').value;
    if (!id) { $('#e2-preview').textContent = 'No receiver configured yet.'; return; }
    try {
      const r = await api(`/api/console/bouquet/preview?receiver_id=${id}`);
      $('#e2-preview').textContent = r.content;
      $('#e2-info').innerHTML = `File <span class="mono">${esc(r.filename)}</span> ·
        <b>${r.entries}</b> entries · service type <b>${esc((state.players || {})[String(Object.keys(state.players)[0])] || '4097')}</b>
        <br><span class="mono">${esc(r.bouquets_tv)}</span>`;
    } catch (e) {
      $('#e2-preview').textContent = 'Could not build the preview: ' + e.message;
    }
  }

  $('#btn-push').addEventListener('click', async () => {
    const id = Number($('#e2-recv').value);
    if (!id) return;
    const btn = $('#btn-push');
    btn.disabled = true; btn.textContent = 'pushing…';
    try {
      const r = await api('/api/console/bouquet/push', { method: 'POST', body: { receiver_id: id } });
      toast(`Bouquet pushed to the receiver (${r.entries} entries)`, 'ok');
      await refresh();
    } catch (e) { toast('Push failed: ' + e.message, 'err'); }
    btn.disabled = false; btn.textContent = 'Push to receiver';
  });

  $('#btn-add-recv').addEventListener('click', () => editReceiver(null));

  function editReceiver(id) {
    const r = id ? state.receivers.find(x => x.id === id) : null;
    const isNew = !r;
    const rec = r || {
      name: '', host: '', port: 21, transport: 'ftp', username: 'root',
      service_type: 4097, bouquet_name: 'web-player', enabled: true,
    };
    const body = `
      <div class="grid3">
        <label class="field"><span>Name</span><input type="text" id="r-name" value="${esc(rec.name)}"></label>
        <label class="field"><span>Host / IP</span><input type="text" id="r-host" value="${esc(rec.host)}"></label>
        <label class="field"><span>Port</span><input type="number" id="r-port" value="${esc(rec.port)}"></label>
        <label class="field"><span>Username</span><input type="text" id="r-user" value="${esc(rec.username)}"></label>
        <label class="field"><span>Password</span><input type="password" id="r-pw" placeholder="${rec.has_password ? 'unchanged' : ''}"></label>
        <label class="field"><span>Transport</span><select id="r-transport">
          ${['ftp', 'download', 'ssh'].map(t => `<option ${rec.transport === t ? 'selected' : ''}>${t}</option>`).join('')}
        </select></label>
        <label class="field"><span>Service type (player on the box)</span><select id="r-stype">
          <option value="4097" ${String(rec.service_type) === '4097' ? 'selected' : ''}>4097 — servicemp3 / GStreamer</option>
          <option value="5001" ${String(rec.service_type) === '5001' ? 'selected' : ''}>5001 — ServiceApp gstplayer</option>
          <option value="5002" ${String(rec.service_type) === '5002' ? 'selected' : ''}>5002 — ServiceApp exteplayer3</option>
          <option value="1" ${String(rec.service_type) === '1' ? 'selected' : ''}>1 — DVB pipeline (raw TS only)</option>
        </select></label>
        <label class="field"><span>Bouquet name</span><input type="text" id="r-bq" value="${esc(rec.bouquet_name)}"></label>
        <label class="field"><span>Enabled</span>
          <div class="row" style="gap:9px;padding-top:4px"><label class="switch"><input type="checkbox" id="r-enabled" ${rec.enabled ? 'checked' : ''}><span class="track"></span></label></div></label>
      </div>
      <p class="mut small" style="margin-top:14px">
        The bouquet is written to <code class="mono">/etc/enigma2/userbouquet.&lt;name&gt;.tv</code>; the receiver is
        asked to reload its service list afterwards. 4097 is the safe default; 5002 is the one that can show soft
        subtitle tracks inside a Matroska stream.
      </p>`;
    const root = modal(isNew ? 'Add receiver' : 'Edit ' + rec.name, body,
      `<button class="btn" data-close>Cancel</button><button class="btn pri" id="r-save">Save</button>`, true);
    $('#r-save', root).addEventListener('click', async () => {
      const body = {
        name: $('#r-name', root).value.trim(),
        host: $('#r-host', root).value.trim(),
        port: Number($('#r-port', root).value) || 21,
        username: $('#r-user', root).value.trim(),
        password: $('#r-pw', root).value,
        transport: $('#r-transport', root).value,
        service_type: Number($('#r-stype', root).value) || 4097,
        bouquet_name: ($('#r-bq', root).value.trim() || 'web-player').replace(/[^a-z0-9_-]/gi, ''),
        enabled: $('#r-enabled', root).checked,
      };
      if (!body.name || !body.host) { toast('A name and a host are required', 'err'); return; }
      try {
        if (isNew) await api('/api/console/receivers', { method: 'POST', body });
        else await api(`/api/console/receivers/${id}`, { method: 'PUT', body });
        closeModal();
        toast('Saved ' + body.name, 'ok');
        await refresh();
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  /* ------------------------------------------------------------------ */
  /* LOGS                                                               */
  /* ------------------------------------------------------------------ */
  let logTimer = null;

  function logLine(entry) {
    const row = document.createElement('div');
    row.className = 'logline ' + entry.level;
    const time = document.createElement('span');
    time.className = 'lt';
    time.textContent = entry.ts;
    const level = document.createElement('span');
    level.className = 'll';
    level.textContent = entry.level;
    const logger = document.createElement('span');
    logger.className = 'lg';
    logger.textContent = entry.logger;
    const msg = document.createElement('span');
    msg.className = 'lm';
    msg.textContent = entry.msg;
    row.append(time, level, logger, msg);
    if (entry.exc) {
      const exc = document.createElement('span');
      exc.className = 'lx';
      exc.textContent = entry.exc;
      row.appendChild(exc);
    }
    return row;
  }

  async function renderLogs() {
    const level = $('#log-level').value;
    const search = $('#log-search').value.trim();
    const box = $('#log-box');
    let data;
    try {
      data = await api(`/api/console/logs?level=${encodeURIComponent(level)}`
        + `&search=${encodeURIComponent(search)}&limit=1000`);
    } catch (e) {
      box.innerHTML = `<div class="mut small">The log could not be read: ${esc(e.message)}</div>`;
      return;
    }
    const records = data.records || [];
    const file = data.file || {};
    const counts = data.counts || {};
    const countText = Object.keys(counts)
      .filter(k => counts[k]).map(k => `${k.toLowerCase()} ${counts[k]}`).join(' · ');
    const size = file.size ? (file.size > 1048576
      ? (file.size / 1048576).toFixed(1) + ' MB' : Math.round(file.size / 1024) + ' kB') : '—';
    $('#log-info').textContent =
      `${records.length} line(s) shown · buffer: ${countText || 'empty'}`
      + ` · file: ${file.path || 'none'} (${size})`
      + ` · level ${(data.config || {}).level || 'INFO'}`;

    box.innerHTML = '';
    if (!records.length) {
      box.innerHTML = '<div class="mut small">Nothing matches.</div>';
      return;
    }
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    records.forEach(r => box.appendChild(logLine(r)));
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  function startLogTimer() {
    stopLogTimer();
    if ($('#log-auto').checked) logTimer = setInterval(renderLogs, 4000);
  }
  function stopLogTimer() {
    if (logTimer) { clearInterval(logTimer); logTimer = null; }
  }

  $('#log-refresh').addEventListener('click', renderLogs);
  $('#log-level').addEventListener('change', renderLogs);
  $('#log-search').addEventListener('input', () => {
    clearTimeout(window.__logSearch);
    window.__logSearch = setTimeout(renderLogs, 250);
  });
  $('#log-auto').addEventListener('change', startLogTimer);
  $('#log-clear').addEventListener('click', async () => {
    try {
      const r = await api('/api/console/logs?file=1', { method: 'DELETE' });
      toast(`The log was cleared (${r.removed} line(s) in the buffer)`, 'ok');
      renderLogs();
    } catch (e) { toast(e.message, 'err'); }
  });

  /* ------------------------------------------------------------------ */
  /* SETTINGS                                                           */
  /* ------------------------------------------------------------------ */
  function renderSettings() {
    const o = state.overview || {};
    const vaapi = o.vaapi || {};
    $('#s-root').value = o.media_root || '';
    $('#s-base').value = o.base_url || '';
    $('#hw-report').innerHTML = `
      <table class="tbl"><tbody>
        <tr><td>VAAPI</td><td>${vaapi.available ? '<span class="badge ok">available</span>' : '<span class="badge warn">not available</span>'}</td></tr>
        <tr><td>Device</td><td class="mono">${esc(vaapi.device || '—')}</td></tr>
        <tr><td>Transcode mode</td><td>${esc((vaapi.mode || '?').toUpperCase())}</td></tr>
        <tr><td>HLS segment</td><td>${esc(vaapi.segment_seconds || '—')} s</td></tr>
      </tbody></table>
      <p style="margin-top:10px">${vaapi.available
        ? 'The DS918+ encodes H.264 with its Intel HD Graphics 500; a 4K HEVC source is transcoded to 1080p H.264, one stream at a time.'
        : 'No /dev/dri in this container — ffmpeg falls back to libx264 (CPU only). Add <span class="mono">devices: [/dev/dri:/dev/dri]</span> to the compose file to enable VAAPI.'}</p>`;
  }

  /* ------------------------------------------------------------------ */
  /* refresh                                                            */
  /* ------------------------------------------------------------------ */
  async function refresh() {
    try {
      const [overview, media, users, groups, receivers] = await Promise.all([
        api('/api/console/overview'),
        api('/api/console/media'),
        api('/api/console/users'),
        api('/api/console/groups'),
        api('/api/console/receivers'),
      ]);
      state.overview = overview;
      state.media = media.items;
      state.users = users.items;
      state.groups = users.groups;
      state.receivers = receivers.items;
      state.players = receivers.players || {};
      const active = $('#tabs .tab.on');
      showTab(active ? active.dataset.tab : 'dash');
    } catch (e) {
      toast('Could not load the console: ' + e.message, 'err');
    }
  }

  refresh();
})();
