/* web-player — management console (mock-up logic).
 *
 * Everything is client side: the table is edited in memory and written to
 * localStorage. The real application talks to the FastAPI backend
 * (see docs/MOCKUP.md for the API shape).
 */
(function () {
  'use strict';

  const { DB, save, reset } = WP;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ------------------------------------------------------------------ */
  /* toast + modal helpers                                              */
  /* ------------------------------------------------------------------ */
  function toast(msg, kind) {
    const box = $('#toasts');
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 2600);
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

  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  /* ------------------------------------------------------------------ */
  /* navigation                                                         */
  /* ------------------------------------------------------------------ */
  function showTab(name) {
    $$('#tabs .tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
    ['dash', 'media', 'users', 'e2', 'settings'].forEach(k => {
      $('#tab-' + k).classList.toggle('hidden', k !== name);
    });
    if (name === 'dash') renderDash();
    if (name === 'media') renderMedia();
    if (name === 'users') renderUsers();
    if (name === 'e2') renderE2();
    if (name === 'settings') renderSettings();
  }
  $$('#tabs .tab').forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));

  /* ------------------------------------------------------------------ */
  /* DASHBOARD                                                          */
  /* ------------------------------------------------------------------ */
  function renderDash() {
    const playable = DB.media.filter(m => m.probe.browserPlayable).length;
    const hls = DB.media.filter(m => m.probe.delivery === 'hls').length;
    const activeUsers = DB.users.filter(u => u.enabled).length;
    const pushed = DB.receivers.filter(r => r.enabled);

    $('#dash-stats').innerHTML = [
      stat('Media items', DB.media.length, `${playable} playable directly · ${hls} need HLS`),
      stat('Users', DB.users.length, `${activeUsers} enabled`),
      stat('Groups', DB.groups.length, 'playlist shelves'),
      stat('Enigma2 receivers', DB.receivers.length, `${pushed.length} enabled`),
    ].join('');

    const attention = [];
    DB.media.forEach(m => {
      if (!m.probe.browserPlayable && m.outputs.web) {
        attention.push(['warn', `${esc(m.title)} — ${esc(m.probe.container)}/${esc(m.probe.video)} is not browser-playable; it is served as on-demand HLS (VAAPI transcode).`]);
      }
      if (!m.enabled) attention.push(['mut', `${esc(m.title)} — globally disabled, hidden from every user.`]);
      if (m.source === 'youtube' && (m.resolved || {}).state === 'failed') {
        attention.push(['err', `${esc(m.title)} — yt-dlp could not resolve this link: ${esc((m.resolved || {}).error || 'unknown error')}. It falls back to the embedded player and cannot be pushed to a bouquet.`]);
      }
      if (m.source === 'youtube' && (m.resolved || {}).state === 'resolved' && m.outputs.enigma2) {
        attention.push(['ok', `${esc(m.title)} — resolved with yt-dlp, so it can be written into the bouquet.`]);
      }
    });
    DB.users.forEach(u => {
      if (!u.enabled) attention.push(['mut', `User <b>${esc(u.name)}</b> is disabled.`]);
      if (u.expires && u.expires < '2026-12-31') attention.push(['warn', `User <b>${esc(u.name)}</b> expires ${esc(u.expires)}.`]);
    });
    $('#dash-attention').innerHTML = attention.length
      ? `<table class="tbl"><tbody>${attention.map(a =>
          `<tr><td style="width:90px"><span class="badge ${a[0]}">${a[0] === 'mut' ? 'info' : a[0]}</span></td><td>${a[1]}</td></tr>`).join('')}</tbody></table>`
      : '<p class="mut">Nothing to report — everything is consistent.</p>';

    const rows = DB.users.map(u => {
      const vis = DB.media.filter(m => WP.visibleTo(m, u.id));
      const groups = (DB.user_groups[u.id] || []).map(gid => {
        const g = WP.groupById(gid);
        return g ? `<span class="chip">${esc(g.name)}</span>` : '';
      }).join(' ');
      const ovr = Object.keys(DB.user_media[u.id] || {}).length;
      return `<tr>
        <td><b>${esc(u.name)}</b></td>
        <td>${u.role}</td>
        <td>${u.enabled ? '<span class="badge ok">enabled</span>' : '<span class="badge err">disabled</span>'}</td>
        <td>${groups || '<span class="mut2">none</span>'}</td>
        <td><b>${vis.length}</b> / ${DB.media.length}</td>
        <td>${ovr ? `<span class="badge info">${ovr}</span>` : '<span class="mut2">—</span>'}</td>
        <td>${esc(u.maxStreams)}</td>
        <td>${esc(u.expires || '—')}</td>
      </tr>`;
    }).join('');
    $('#dash-visibility').innerHTML = `<table class="tbl">
      <thead><tr><th>User</th><th>Groups</th><th>Visible items</th><th>Overrides</th><th>Max streams</th><th>Expires</th></tr></thead>
      <tbody>${rows}</tbody></table>`;

    $('#dash-activity').innerHTML = `<table class="tbl"><tbody>
      ${activityRow('21:04', 'admin', 'signed in to the console')}
      ${activityRow('20:58', 'system', `pushed bouquet “web-player” to Living room VU+ Duo2 (4 entries)`)}
      ${activityRow('20:41', 'family', 'watched “The Long Way Home” until 01:36')}
      ${activityRow('19:12', 'admin', 'enabled “Nightfall” for user “family”')}
      ${activityRow('18:12', 'kids', 'signed in')}
    </tbody></table>`;
  }

  function activityRow(time, who, what) {
    return `<tr><td class="mono mut2" style="width:80px">${esc(time)}</td>
      <td style="width:110px"><span class="badge">${esc(who)}</span></td><td>${what}</td></tr>`;
  }

  function stat(k, v, d) {
    return `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="d">${esc(d)}</div></div>`;
  }

  /* ------------------------------------------------------------------ */
  /* MEDIA                                                              */
  /* ------------------------------------------------------------------ */

  /* How the item is served in the browser (D3) and whether the YouTube
   * resolver (D4) got a usable URL. */
  function deliveryBadges(m) {
    let out = '';
    if (m.source === 'youtube') {
      const r = m.resolved || {};
      if (r.state === 'resolved') {
        out += `<span class="badge ok" title="yt-dlp resolved ${esc(r.at || '')}">yt-dlp ✓ ${esc(r.quality || '')}</span>`;
      } else if (r.state === 'failed') {
        out += `<span class="badge err" title="${esc(r.error || 'resolution failed')}">yt-dlp ✕</span>`;
      } else {
        out += `<span class="badge warn">yt-dlp …</span>`;
      }
    }
    if (m.probe.delivery === 'direct') {
      out += `<span class="badge ok" title="served with HTTP Range — exact seeking, 0 % CPU">direct</span>`;
    } else if (m.probe.delivery === 'hls') {
      const vaapi = ['hevc', 'vp9', 'av1'].indexOf(String(m.probe.video || '').toLowerCase()) !== -1;
      out += vaapi
        ? `<span class="badge warn" title="VAAPI transcode to 1080p H.264 (Intel HD Graphics 500)">HLS · VAAPI</span>`
        : `<span class="badge warn" title="ffmpeg remux to segmented HLS — 0 % CPU, copy">HLS · remux</span>`;
    } else {
      out += `<span class="badge purple" title="the resolver failed — falls back to the embedded player">embed</span>`;
    }
    return out;
  }

  function renderMedia() {
    const sel = $('#m-group');
    if (sel.options.length <= 1) {
      DB.groups.forEach(g => sel.insertAdjacentHTML('beforeend', `<option value="${g.id}">${esc(g.name)}</option>`));
    }
    $('#c-media').textContent = DB.media.length;
    $('#c-users').textContent = DB.users.length;

    const q = $('#m-search').value.trim().toLowerCase();
    const kind = $('#m-kind').value, grp = $('#m-group').value, src = $('#m-source').value, play = $('#m-play').value;
    const rows = DB.media.filter(m => {
      if (q && !(m.title.toLowerCase().includes(q) || (m.path || '').toLowerCase().includes(q) || (m.url || '').toLowerCase().includes(q))) return false;
      if (kind && m.kind !== kind) return false;
      if (grp && m.group !== grp) return false;
      if (src && m.source !== src) return false;
      if (play && m.probe.delivery !== play) return false;
      return true;
    }).sort((a, b) => a.sort - b.sort);

    $('#m-count').textContent = rows.length + ' of ' + DB.media.length + ' items';

    const userCols = DB.users.map(u => `<th class="usercell" title="${esc(u.name)}">${esc(u.name)}</th>`).join('');
    /* The whole head is rebuilt below, so it must not be referenced by id
       anywhere else — a stale #m-users-head would be null after this. */
    $('#m-table thead').innerHTML = `<tr>
      <th style="width:44px"></th><th>Title</th><th>Source</th><th>Playable in browser</th>
      <th>Group</th><th>Outputs</th><th colspan="${DB.users.length}" class="center">Visible to (${DB.users.length})</th>
      <th style="width:120px">Order</th><th style="width:90px"></th></tr>`;

    const tbody = rows.map(m => {
      const visCells = DB.users.map(u => {
        const ov = (DB.user_media[u.id] || {})[m.id];
        const visible = WP.visibleTo(m, u.id);
        const cls = ov === 'allow' || ov === 'deny' ? 'ovr' : (visible ? 'yes' : 'no');
        const glyph = ov === 'allow' ? 'G' : ov === 'deny' ? '×' : (visible ? '✓' : '·');
        const why = WP.whyVisible(m, u.id);
        const next = ov === undefined ? 'grant' : (ov === 'allow' ? 'hide' : 'reset to the group rule');
        return `<td class="usercell"><span class="vis-dot ${cls}" data-item="${m.id}" data-user="${u.id}"
                  title="${esc(u.name)}: ${esc(why)} — click to ${next}">${glyph}</span></td>`;
      }).join('');

      const playBadge = deliveryBadges(m);
      const detail = m.probe.video
        ? `${esc(m.probe.container.toUpperCase())} · ${esc(m.probe.video)} ${m.probe.width ? m.probe.width + '×' + m.probe.height : ''}`
        : esc((m.probe.container || '').toUpperCase());

      return `<tr data-id="${m.id}">
        <td><img class="thumb" src="${esc(m.poster || 'assets/vendor/blank.png')}" alt="" onerror="this.style.visibility='hidden'"></td>
        <td><div class="col" style="gap:1px">
              <b class="trunc" style="max-width:260px">${esc(m.title)}</b>
              <span class="mut small">${esc(m.year || '')} · ${esc(WP.fmtDuration(m.duration))} · ${m.size ? m.size + ' MB' : '—'}</span>
            </div></td>
        <td><div class="col" style="gap:2px">
              <span class="badge ${m.source === 'local' ? '' : 'info'}">${esc(m.source)}</span>
              <span class="mut small mono trunc" style="max-width:190px" title="${esc(m.path || m.url)}">${esc(m.path || m.url)}</span>
            </div></td>
        <td><div class="col" style="gap:2px">${playBadge}<span class="mut small">${detail}</span></div></td>
        <td>${esc((WP.groupById(m.group) || {}).name || '—')}</td>
        <td class="nowrap">
          <label class="row" style="gap:4px"><input type="checkbox" data-out="web" ${m.outputs.web ? 'checked' : ''}> ▶ web</label>
          <label class="row" style="gap:4px"><input type="checkbox" data-out="enigma2" ${m.outputs.enigma2 ? 'checked' : ''}> 📺 e2</label>
        </td>
        ${visCells}
        <td><input type="number" class="sort-in" value="${m.sort}" style="width:60px"></td>
        <td class="nowrap">
          ${m.source === 'youtube' ? `<button class="btn sm" data-resolve="${m.id}" title="run yt-dlp again">⟳ resolve</button>` : ''}
          <button class="btn sm" data-edit="${m.id}">edit</button>
          <button class="btn sm danger" data-del="${m.id}">✕</button>
        </td>
      </tr>`;
    }).join('');

    $('#m-table tbody').innerHTML = rows.length ? tbody :
      '<tr><td colspan="12" class="center mut" style="padding:26px">No media matches these filters.</td></tr>';

    /* row interactions */
    $$('#m-table tbody [data-out]').forEach(cb => cb.addEventListener('change', () => {
      const id = cb.closest('tr').dataset.id;
      const item = WP.mediaById(id);
      item.outputs[cb.dataset.out] = cb.checked;
      save(); renderMedia(); renderDash();
      toast(`${item.title}: ${cb.dataset.out === 'web' ? 'web player' : 'Enigma2'} output ${cb.checked ? 'enabled' : 'disabled'}`, 'ok');
    }));
    $$('#m-table tbody .sort-in').forEach(inp => inp.addEventListener('change', () => {
      const item = WP.mediaById(inp.closest('tr').dataset.id);
      item.sort = parseInt(inp.value, 10) || 0; save(); renderMedia();
    }));
    $$('#m-table tbody [data-edit]').forEach(b => b.addEventListener('click', () => editMedia(b.dataset.edit)));
    $$('#m-table tbody [data-resolve]').forEach(b => b.addEventListener('click', () => {
      const item = WP.mediaById(b.dataset.resolve);
      item.resolved = { state: 'resolved', at: 'just now', quality: '1080p', codec: 'vp9', expires: 'in 24 h' };
      item.probe.delivery = 'direct';
      item.probe.container = 'mp4'; item.probe.video = 'vp9';
      save(); renderMedia(); renderDash();
      toast('yt-dlp resolved ' + item.title, 'ok');
    }));
    $$('#m-table tbody [data-del]').forEach(b => b.addEventListener('click', () => {
      const item = WP.mediaById(b.dataset.del);
      if (!confirm(`Delete “${item.title}” from the library?`)) return;
      DB.media = DB.media.filter(m => m.id !== item.id);
      Object.keys(DB.user_media).forEach(u => { delete DB.user_media[u][item.id]; });
      save(); renderMedia(); renderDash(); toast('Deleted ' + item.title, 'warn');
    }));
    $$('#m-table tbody .vis-dot').forEach(dot => dot.addEventListener('click', () => {
      const item = WP.mediaById(dot.dataset.item);
      const uid = dot.dataset.user;
      const cur = (DB.user_media[uid] || {})[item.id];
      const next = cur === undefined ? 'allow' : (cur === 'allow' ? 'deny' : undefined);
      DB.user_media[uid] = DB.user_media[uid] || {};
      if (next === undefined) delete DB.user_media[uid][item.id]; else DB.user_media[uid][item.id] = next;
      save(); renderMedia();
      toast(`${item.title} → ${WP.userById(uid).name}: ` +
        (next === 'allow' ? 'granted' : next === 'deny' ? 'hidden' : 'back to the group rule'), 'ok');
    }));
  }

  ['#m-search', '#m-kind', '#m-group', '#m-source', '#m-play'].forEach(s => {
    $(s).addEventListener('input', renderMedia);
    $(s).addEventListener('change', renderMedia);
  });

  $('#btn-add-media').addEventListener('click', () => editMedia(null));
  $('#btn-scan').addEventListener('click', () => {
    modal('Scan media root', `
      <p class="mut">In the real application this walks <code class="mono">${esc(DB.settings.mediaRoot)}</code>,
         runs <code class="mono">ffprobe</code> on every new file and adds what it finds.</p>
      <div class="picker" style="margin-top:12px">
        <div class="row-i"><input type="checkbox" checked><span class="mono">/volume1/media/aurora-drift.mp4</span><span class="spacer"></span><span class="badge ok">new</span></div>
        <div class="row-i"><input type="checkbox" checked><span class="mono">/volume1/media/long-way-home.mp4</span><span class="spacer"></span><span class="badge ok">new</span></div>
        <div class="row-i"><input type="checkbox" checked><span class="mono">/volume1/media/nightfall.mkv</span><span class="spacer"></span><span class="badge ok">new</span></div>
        <div class="row-i"><input type="checkbox"><span class="mono">/volume1/media/coffee-and-code.mp4</span><span class="spacer"></span><span class="badge">known</span></div>
        <div class="row-i"><input type="checkbox"><span class="mono">/volume1/media/thumbs.db</span><span class="spacer"></span><span class="badge err">not a video</span></div>
      </div>`, `<button class="btn" data-close>Cancel</button><button class="btn pri" id="scan-go">Add 3 items</button>`);
    $('#scan-go').addEventListener('click', () => { closeModal(); toast('3 items added to the library', 'ok'); });
  });

  function editMedia(id) {
    const item = id ? WP.mediaById(id) : null;
    const isNew = !item;
    const m = item || {
      id: 'm_' + Date.now(), title: '', year: new Date().getFullYear(), kind: 'movie',
      group: DB.groups[0].id, source: 'local', path: '', url: '', duration: null, size: null,
      poster: null, probe: { container: 'mp4', video: 'h264', browserPlayable: true, seekable: true, delivery: 'direct' },
      enabled: true, outputs: { web: true, enigma2: false }, sort: (DB.media.length + 1) * 10, added: 'now',
    };
    const body = `
      <div class="grid2">
        <label class="field"><span>Title</span><input type="text" id="f-title" value="${esc(m.title)}"></label>
        <label class="field"><span>Year</span><input type="number" id="f-year" value="${esc(m.year || '')}"></label>
        <label class="field"><span>Kind</span><select id="f-kind">
          ${['movie', 'series', 'clip', 'audio'].map(k => `<option ${m.kind === k ? 'selected' : ''}>${k}</option>`).join('')}
        </select></label>
        <label class="field"><span>Group (playlist shelf)</span><select id="f-group">
          ${DB.groups.map(g => `<option value="${g.id}" ${m.group === g.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}
        </select></label>
      </div>

      <div class="grid2" style="margin-top:12px">
        <label class="field"><span>Source type</span><select id="f-source">
          <option value="local" ${m.source === 'local' ? 'selected' : ''}>Local file (on the NAS)</option>
          <option value="url" ${m.source === 'url' ? 'selected' : ''}>Direct URL (mp4 / m3u8 / …)</option>
          <option value="youtube" ${m.source === 'youtube' ? 'selected' : ''}>YouTube</option>
        </select></label>
        <label class="field" id="f-path-wrap"><span>Path inside the media root</span>
          <input type="text" id="f-path" value="${esc(m.path || '')}" placeholder="movies/aurora-drift.mp4"></label>
        <label class="field hidden" id="f-url-wrap"><span>URL</span>
          <input type="url" id="f-url" value="${esc(m.url || '')}" placeholder="https://…"></label>
      </div>

      <div class="grid2" style="margin-top:12px">
        <label class="field"><span>Delivery</span><select id="f-delivery">
          <option value="direct" ${m.probe.delivery === 'direct' ? 'selected' : ''}>Direct (Range requests, no CPU)</option>
          <option value="hls" ${m.probe.delivery === 'hls' ? 'selected' : ''}>On-demand HLS (ffmpeg, seekable)</option>
          <option value="embed" ${m.probe.delivery === 'embed' ? 'selected' : ''}>Embedded player (YouTube)</option>
        </select></label>
        <label class="field"><span>Probe result (filled by ffprobe)</span>
          <input type="text" id="f-probe" value="${esc(m.probe.container || '')} ${esc(m.probe.video || '')} ${m.probe.width || ''} ${m.probe.width ? '×' + m.probe.height : ''}" disabled></label>
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

      <h4 style="margin:18px 0 8px;font-size:13px">Per-user visibility</h4>
      <div class="mut small" style="margin-bottom:8px">
        The group decides by default; an override here wins over the group.
      </div>
      <div class="picker">
        ${DB.users.map(u => {
          const ov = (DB.user_media[u.id] || {})[m.id];
          return `<div class="row-i">
            <img class="mini-poster" src="${esc(m.poster || 'assets/vendor/blank.png')}" onerror="this.style.visibility='hidden'">
            <b style="min-width:90px">${esc(u.name)}</b>
            <span class="mut small">${esc((WP.groupById(m.group) || {}).name || '')}</span>
            <span class="spacer"></span>
            <span class="ovr-pick">
              <button data-ov="allow" class="${ov === 'allow' ? 'on allow' : ''}">grant</button>
              <button data-ov="deny" class="${ov === 'deny' ? 'on deny' : ''}">hide</button>
              <button data-ov="inherit" class="${ov === undefined ? 'on' : ''}">group</button>
            </span></div>`;
        }).join('')}
      </div>`;

    const root = modal(isNew ? 'Add media' : 'Edit ' + m.title, body,
      `<button class="btn" data-close>Cancel</button><button class="btn pri" id="f-save">${isNew ? 'Add item' : 'Save'}</button>`, true);

    const srcSel = $('#f-source', root);
    function syncSource() {
      const s = srcSel.value;
      $('#f-path-wrap', root).classList.toggle('hidden', s !== 'local');
      $('#f-url-wrap', root).classList.toggle('hidden', s === 'local');
    }
    srcSel.addEventListener('change', syncSource); syncSource();

    $$('.ovr-pick button', root).forEach(b => b.addEventListener('click', () => {
      const uid = b.closest('.row-i').querySelector('b').textContent.trim();
      const u = DB.users.find(x => x.name === uid);
      const pick = b.dataset.ov;
      DB.user_media[u.id] = DB.user_media[u.id] || {};
      if (pick === 'inherit') delete DB.user_media[u.id][m.id];
      else DB.user_media[u.id][m.id] = pick;
      $$('.ovr-pick button', b.parentElement).forEach(x => x.className = '');
      b.classList.add('on', pick);
      if (pick !== 'inherit') b.classList.add(pick);
      save();
    }));

    $('#f-save', root).addEventListener('click', () => {
      m.title = $('#f-title', root).value.trim() || 'Untitled';
      m.year = parseInt($('#f-year', root).value, 10) || null;
      m.kind = $('#f-kind', root).value;
      m.group = $('#f-group', root).value;
      m.source = srcSel.value;
      m.path = $('#f-path', root).value.trim();
      m.url = $('#f-url', root).value.trim();
      m.probe.delivery = $('#f-delivery', root).value;
      m.outputs.web = $('#f-out-web', root).checked;
      m.outputs.enigma2 = $('#f-out-e2', root).checked;
      m.enabled = $('#f-enabled', root).checked;
      if (isNew) { DB.media.push(m); DB.media.sort((a, b) => a.sort - b.sort); }
      save(); closeModal(); renderMedia(); renderDash();
      toast(isNew ? 'Added ' + m.title : 'Saved ' + m.title, 'ok');
    });
  }

  /* ------------------------------------------------------------------ */
  /* USERS                                                              */
  /* ------------------------------------------------------------------ */
  function renderUsers() {
    $('#u-count').textContent = DB.users.length + ' users';
    const rows = DB.users.map(u => {
      const vis = DB.media.filter(m => WP.visibleTo(m, u.id));
      const ovr = Object.keys(DB.user_media[u.id] || {});
      return `<tr data-id="${u.id}">
        <td><b>${esc(u.name)}</b></td>
        <td>${u.role === 'admin' ? '<span class="badge info">administrator</span>' : 'user'}</td>
        <td>${u.enabled ? '<span class="badge ok">enabled</span>' : '<span class="badge err">disabled</span>'}</td>
        <td>${(DB.user_groups[u.id] || []).map(gid => {
              const g = WP.groupById(gid); return g ? `<span class="chip">${esc(g.name)}</span>` : '';
            }).join(' ') || '<span class="mut2">none</span>'}</td>
        <td><b>${vis.length}</b> / ${DB.media.length}</td>
        <td>${ovr.length ? `<span class="badge info" title="${esc(ovr.join(', '))}">${ovr.length}</span>` : '<span class="mut2">—</span>'}</td>
        <td>${esc(u.maxStreams)}</td>
        <td>${esc(u.expires || '—')}</td>
        <td class="mut small">${esc(u.lastLogin || '—')}</td>
        <td class="nowrap">
          <button class="btn sm" data-uedit="${u.id}">edit</button>
          <button class="btn sm" data-ustate="${u.id}">${u.enabled ? 'disable' : 'enable'}</button>
          <button class="btn sm danger" data-udel="${u.id}">✕</button>
        </td>
      </tr>`;
    }).join('');
    $('#u-table tbody').innerHTML = `<tr></tr>`.replace('<tr></tr>', rows);

    $$('#u-table tbody [data-uedit]').forEach(b => b.addEventListener('click', () => editUser(b.dataset.uedit)));
    $$('#u-table tbody [data-ustate]').forEach(b => b.addEventListener('click', () => {
      const u = WP.userById(b.dataset.ustate); u.enabled = !u.enabled; save(); renderUsers(); renderDash();
      toast(`${u.name} ${u.enabled ? 'enabled' : 'disabled'}`, u.enabled ? 'ok' : 'warn');
    }));
    $$('#u-table tbody [data-udel]').forEach(b => b.addEventListener('click', () => {
      const u = WP.userById(b.dataset.udel);
      if (u.role === 'admin') { toast('The administrator cannot be deleted', 'err'); return; }
      if (!confirm(`Delete user “${u.name}”?`)) return;
      DB.users = DB.users.filter(x => x.id !== u.id);
      delete DB.user_groups[u.id]; delete DB.user_media[u.id];
      save(); renderUsers(); renderDash(); toast('Deleted ' + u.name, 'warn');
    }));
  }

  function editUser(id) {
    const u = id ? WP.userById(id) : null;
    const isNew = !u;
    const user = u || {
      id: 'u_' + Date.now(), name: '', role: 'user', enabled: true, maxStreams: 2,
      expires: null, lastLogin: null, groups: [],
    };
    const body = `
      <div class="grid3">
        <label class="field"><span>Username</span><input type="text" id="u-name" value="${esc(user.name)}"></label>
        <label class="field"><span>Password</span><input type="password" id="u-pw" placeholder="${isNew ? 'required' : 'unchanged'}"></label>
        <label class="field"><span>Role</span><select id="u-role">
          <option value="user" ${user.role === 'user' ? 'selected' : ''}>user (public player)</option>
          <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>administrator (console)</option>
        </select></label>
        <label class="field"><span>Max simultaneous streams</span><input type="number" id="u-max" min="1" max="10" value="${esc(user.maxStreams)}"></label>
        <label class="field"><span>Expires (YYYY-MM-DD, empty = never)</span><input type="text" id="u-exp" value="${esc(user.expires || '')}"></label>
        <label class="field"><span>Status</span>
          <div class="row" style="gap:9px;padding-top:4px">
            <label class="switch"><input type="checkbox" id="u-enabled" ${user.enabled ? 'checked' : ''}><span class="track"></span></label>
            <span class="mut small">${user.enabled ? 'can sign in' : 'sign-in refused'}</span>
          </div></label>
      </div>

      <h4 style="margin:18px 0 4px;font-size:13px">Playlist access (groups)</h4>
      <p class="mut small" style="margin:0 0 10px">A group is a shelf of media. A user sees every item in the groups ticked here.</p>
      <div class="grp-pick">
        ${DB.groups.map(g => `<label class="chip click ${(DB.user_groups[user.id] || []).indexOf(g.id) !== -1 ? 'on' : ''}">
          <input type="checkbox" class="g-pick" value="${g.id}" ${(DB.user_groups[user.id] || []).indexOf(g.id) !== -1 ? 'checked' : ''}> ${esc(g.name)}</label>`).join('')}
      </div>

      <h4 style="margin:18px 0 4px;font-size:13px">Per-item exceptions</h4>
      <p class="mut small" style="margin:0 0 10px">Override the group rule for single items. Empty = the group rule decides.</p>
      <div class="picker" id="u-ovr">
        ${DB.media.map(m => {
          const ov = (DB.user_media[user.id] || {})[m.id];
          return `<div class="row-i" data-mid="${m.id}">
            <img class="mini-poster" src="${esc(m.poster || 'assets/vendor/blank.png')}" onerror="this.style.visibility='hidden'">
            <span class="trunc" style="max-width:260px">${esc(m.title)}</span>
            <span class="mut small">${esc((WP.groupById(m.group) || {}).name || '')}</span>
            <span class="spacer"></span>
            <span class="ovr-pick">
              <button data-ov="allow" class="${ov === 'allow' ? 'on allow' : ''}">grant</button>
              <button data-ov="deny" class="${ov === 'deny' ? 'on deny' : ''}">hide</button>
              <button data-ov="inherit" class="${ov === undefined ? 'on' : ''}">group</button>
            </span></div>`;
        }).join('')}
      </div>`;

    const root = modal(isNew ? 'Add user' : 'Edit ' + user.name, body,
      `<button class="btn" data-close>Cancel</button><button class="btn pri" id="u-save">${isNew ? 'Create user' : 'Save'}</button>`, true);

    $$('.g-pick', root).forEach(cb => cb.addEventListener('change', () => {
      const chip = cb.closest('.chip');
      chip.classList.toggle('on', cb.checked);
    }));

    $$('#u-ovr .ovr-pick button', root).forEach(b => b.addEventListener('click', () => {
      const mid = b.closest('.row-i').dataset.mid;
      const pick = b.dataset.ov;
      DB.user_media[user.id] = DB.user_media[user.id] || {};
      if (pick === 'inherit') delete DB.user_media[user.id][mid];
      else DB.user_media[user.id][mid] = pick;
      $$('.ovr-pick button', b.parentElement).forEach(x => x.className = '');
      b.classList.add('on');
      if (pick !== 'inherit') b.classList.add(pick);
      save();
    }));

    $('#u-save', root).addEventListener('click', () => {
      const name = $('#u-name', root).value.trim();
      if (!name) { toast('A username is required', 'err'); return; }
      if (isNew && !$('#u-pw', root).value) { toast('A password is required', 'err'); return; }
      user.name = name;
      user.role = $('#u-role', root).value;
      user.maxStreams = parseInt($('#u-max', root).value, 10) || 1;
      user.expires = $('#u-exp', root).value.trim() || null;
      user.enabled = $('#u-enabled', root).checked;
      DB.user_groups[user.id] = $$('.g-pick:checked', root).map(cb => cb.value);
      if (isNew) { DB.users.push(user); DB.user_media[user.id] = DB.user_media[user.id] || {}; }
      save(); closeModal(); renderUsers(); renderDash();
      toast(isNew ? 'Created ' + user.name : 'Saved ' + user.name, 'ok');
    });
  }

  $('#btn-add-user').addEventListener('click', () => editUser(null));

  /* ------------------------------------------------------------------ */
  /* ENIGMA2                                                            */
  /* ------------------------------------------------------------------ */
  function renderE2() {
    $('#recv-list').innerHTML = DB.receivers.map(r => `
      <div class="card" style="margin-bottom:10px;background:var(--panel)">
        <div class="row">
          <b>${esc(r.name)}</b>
          ${r.enabled ? '<span class="badge ok">enabled</span>' : '<span class="badge">disabled</span>'}
          <span class="spacer"></span>
          <button class="btn sm" data-redit="${r.id}">edit</button>
        </div>
        <div class="mut small mono" style="margin-top:5px">${esc(r.host)}:${esc(r.port)} · ${esc(r.transport)} · service type ${esc(r.serviceType)}</div>
        <div class="mut small">bouquet <span class="mono">${esc(r.bouquet)}</span> · last push ${esc(r.lastPush)}</div>
      </div>`).join('');
    $$('#recv-list [data-redit]').forEach(b => b.addEventListener('click', () => editReceiver(b.dataset.redit)));

    const sel = $('#e2-recv');
    const prev = sel.value;
    sel.innerHTML = DB.receivers.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
    if (prev) sel.value = prev;
    renderBouquet();
  }

  $('#e2-recv').addEventListener('change', renderBouquet);
  $('#e2-layout').addEventListener('change', renderBouquet);

  function serviceRef(player, sid, url, name) {
    const enc = String(url).replace(/:/g, '%3a').replace(/\s/g, '%20');
    const nm = String(name || '').replace(/:/g, ' -').replace(/\s+/g, ' ').trim() || 'Unnamed';
    return `#SERVICE ${player}:0:1:${sid.toString(16).toUpperCase().padStart(4, '0')}:0:0:0:0:0:0:${enc}:${nm}`;
  }

  function renderBouquet() {
    const r = DB.receivers.find(x => x.id === $('#e2-recv').value) || DB.receivers[0];
    if (!r) return;
    const layout = $('#e2-layout').value;
    const base = DB.settings.baseUrl;
    const items = DB.media.filter(m => m.enabled && m.outputs.enigma2);

    let text = '';
    let entries = 0;

    if (layout === 'per_user') {
      text = '# one bouquet file per user, filled with the items THAT user may watch\n';
      DB.users.forEach(u => {
        const mine = DB.media.filter(m => WP.visibleTo(m, u.id) && m.outputs.enigma2);
        text += `\n# ==== userbouquet.wp_${u.name}.tv ====\n`;
        text += `#NAME web-player — ${u.name}\n`;
        mine.forEach((m, i) => {
          const url = `${base}/player/stream/${m.id}.m3u8`;
          text += serviceRef(r.serviceType, sidFor(u.id + m.id), url, m.title) + '\n';
          text += `#DESCRIPTION ${m.title} (${m.year || ''})\n`;
          entries += 1;
        });
      });
    } else {
      text = `#NAME web-player (TV)\n`;
      let lastGroup = null;
      items.forEach((m) => {
        if (layout === 'group_markers' && m.group !== lastGroup) {
          lastGroup = m.group;
          const g = WP.groupById(m.group);
          text += `#SERVICE 1:64:0:0:0:0:0:0:0:0::${g ? g.name : ''}\n`;
          text += `#DESCRIPTION ${g ? g.name : ''}\n`;
        }
        const url = `${base}/player/stream/${m.id}.m3u8`;
        text += serviceRef(r.serviceType, sidFor(m.id), url, m.title) + '\n';
        text += `#DESCRIPTION ${m.title} (${m.year || ''}) — ${m.probe.container.toUpperCase()}\n`;
        entries += 1;
      });
      text += `\n# ---- bouquets.tv (edited, not overwritten) ----\n`;
      text += `#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.${r.bouquet}.tv" ORDER BY bouquet\n`;
    }

    $('#e2-preview').textContent = text;
    $('#e2-info').innerHTML = `Receiver <b>${esc(r.name)}</b> (${esc(r.host)}:${esc(r.port)}) · service type <b>${esc(r.serviceType)}</b>
      · <b>${entries}</b> entries · bouquets.tv is edited in place, the previous version is kept as a restore point.`;
  }

  function sidFor(seed) {
    let h = 0;
    for (let i = 0; i < seed.length; i += 1) h = (h * 31 + seed.charCodeAt(i)) % 0xffff;
    return h || 1;
  }

  $('#btn-push').addEventListener('click', () => {
    const r = DB.receivers.find(x => x.id === $('#e2-recv').value);
    if (!r) return;
    const entries = ($('#e2-preview').textContent.match(/#SERVICE/g) || []).length;
    r.lastPush = new Date().toISOString().slice(0, 16).replace('T', ' ');
    r.entries = entries; save(); renderE2();
    toast(`Bouquet pushed to ${r.name} (${entries} entries)`, 'ok');
  });

  $('#btn-add-recv').addEventListener('click', () => editReceiver(null));

  function editReceiver(id) {
    const r = id ? DB.receivers.find(x => x.id === id) : null;
    const isNew = !r;
    const rec = r || {
      id: 'r_' + Date.now(), name: '', host: '192.168.1.50', port: 21, transport: 'ftp',
      username: 'root', password: '', serviceType: 4097, bouquet: 'web-player', enabled: true,
      lastPush: '—', entries: 0,
    };
    const body = `
      <div class="grid3">
        <label class="field"><span>Name</span><input type="text" id="r-name" value="${esc(rec.name)}"></label>
        <label class="field"><span>Host / IP</span><input type="text" id="r-host" value="${esc(rec.host)}"></label>
        <label class="field"><span>Port</span><input type="number" id="r-port" value="${esc(rec.port)}"></label>
        <label class="field"><span>Username</span><input type="text" id="r-user" value="${esc(rec.username)}"></label>
        <label class="field"><span>Password</span><input type="password" id="r-pw" value=""></label>
        <label class="field"><span>Transport</span><select id="r-transport">
          ${['ftp', 'download', 'ssh'].map(t => `<option ${rec.transport === t ? 'selected' : ''}>${t}</option>`).join('')}
        </select></label>
        <label class="field"><span>Service type (player on the box)</span><select id="r-stype">
          <option value="4097" ${String(rec.serviceType) === '4097' ? 'selected' : ''}>4097 — servicemp3 / GStreamer</option>
          <option value="5001" ${String(rec.serviceType) === '5001' ? 'selected' : ''}>5001 — ServiceApp gstplayer</option>
          <option value="5002" ${String(rec.serviceType) === '5002' ? 'selected' : ''}>5002 — ServiceApp exteplayer3</option>
          <option value="1" ${String(rec.serviceType) === '1' ? 'selected' : ''}>1 — DVB pipeline (raw TS only)</option>
        </select></label>
        <label class="field"><span>Bouquet name</span><input type="text" id="r-bq" value="${esc(rec.bouquet)}"></label>
        <label class="field"><span>Enabled</span>
          <div class="row" style="gap:9px;padding-top:4px"><label class="switch"><input type="checkbox" id="r-enabled" ${rec.enabled ? 'checked' : ''}><span class="track"></span></label></div></label>
      </div>
      <p class="mut small" style="margin-top:14px">
        The bouquet file is written to <code class="mono">/etc/enigma2/userbouquet.&lt;name&gt;.tv</code> and the
        receiver is asked to reload its service list. 4097 is the safe default; 5002 is the one that can show soft
        subtitle tracks inside a Matroska stream.
      </p>`;
    const root = modal(isNew ? 'Add receiver' : 'Edit ' + rec.name, body,
      `<button class="btn" data-close>Cancel</button><button class="btn pri" id="r-save">Save</button>`, true);
    $('#r-save', root).addEventListener('click', () => {
      rec.name = $('#r-name', root).value.trim() || 'Receiver';
      rec.host = $('#r-host', root).value.trim();
      rec.port = parseInt($('#r-port', root).value, 10) || 21;
      rec.username = $('#r-user', root).value.trim();
      rec.transport = $('#r-transport', root).value;
      rec.serviceType = parseInt($('#r-stype', root).value, 10) || 4097;
      rec.bouquet = ($('#r-bq', root).value.trim() || 'web-player').replace(/[^a-z0-9_-]/gi, '');
      rec.enabled = $('#r-enabled', root).checked;
      if (isNew) DB.receivers.push(rec);
      save(); closeModal(); renderE2(); toast('Saved ' + rec.name, 'ok');
    });
  }

  /* ------------------------------------------------------------------ */
  /* SETTINGS                                                           */
  /* ------------------------------------------------------------------ */
  function renderSettings() {
    $('#s-base').value = DB.settings.baseUrl;
    $('#s-root').value = DB.settings.mediaRoot;
    $('#s-seg').value = DB.settings.hlsSegmentSeconds;
    $('#s-streams').value = DB.settings.maxConcurrentStreams;
    $('#s-profile').value = DB.settings.transcodeProfile;
    $('#s-guests').checked = DB.settings.allowGuests;
    $('#s-pw').value = '';
  }
  ['#s-base', '#s-root', '#s-seg', '#s-streams', '#s-profile', '#s-guests'].forEach(s => {
    $(s).addEventListener('change', () => {
      DB.settings.baseUrl = $('#s-base').value;
      DB.settings.mediaRoot = $('#s-root').value;
      DB.settings.hlsSegmentSeconds = parseInt($('#s-seg').value, 10) || 4;
      DB.settings.maxConcurrentStreams = parseInt($('#s-streams').value, 10) || 3;
      DB.settings.transcodeProfile = $('#s-profile').value;
      DB.settings.allowGuests = $('#s-guests').checked;
      save(); toast('Settings saved', 'ok');
    });
  });

  /* ------------------------------------------------------------------ */
  $('#btn-reset').addEventListener('click', () => {
    if (!confirm('Reset the mock-up to its original data?')) return;
    reset(); location.reload();
  });

  showTab('dash');
})();
