/* web-player — mock-up dataset.
 *
 * The shape of this object mirrors the PROPOSED database (see docs/MOCKUP.md):
 *   users, groups, media, user_groups, user_media (per-item overrides),
 *   receivers, settings.
 *
 * Everything here is fake. The console writes into localStorage so the mock-up
 * keeps its state between reloads; the real app writes into Postgres.
 */
(function (global) {
  'use strict';

  const DB = {
    settings: {
      baseUrl: 'http://nas.local:8088',
      mediaRoot: '/volume1/media',
      hlsSegmentSeconds: 4,
      maxConcurrentStreams: 3,
      transcodeProfile: '1080p H.264 (VAAPI)',
      allowGuests: false,
    },

    groups: [
      { id: 'g_movies', name: 'Movies', color: '#38bdf8' },
      { id: 'g_series', name: 'Series', color: '#a78bfa' },
      { id: 'g_youtube', name: 'YouTube', color: '#f87171' },
      { id: 'g_kids', name: 'Kids', color: '#34d399' },
      { id: 'g_docs', name: 'Documentaries', color: '#fbbf24' },
    ],

    users: [
      { id: 'u_admin', name: 'admin', role: 'admin', enabled: true, maxStreams: 5, expires: null, lastLogin: '2026-10-31 21:04', groups: ['g_movies', 'g_series', 'g_youtube', 'g_kids', 'g_docs'] },
      { id: 'u_family', name: 'family', role: 'user', enabled: true, maxStreams: 3, expires: null, lastLogin: '2026-10-31 20:41', groups: ['g_movies', 'g_series'] },
      { id: 'u_kids', name: 'kids', role: 'user', enabled: true, maxStreams: 1, expires: '2026-12-31', lastLogin: '2026-10-30 18:12', groups: ['g_kids'] },
      { id: 'u_guest', name: 'guest', role: 'user', enabled: false, maxStreams: 1, expires: '2026-11-15', lastLogin: '2026-10-20 12:30', groups: ['g_movies'] },
    ],

    /* user_id -> [group_id] */
    user_groups: {
      u_admin: ['g_movies', 'g_series', 'g_youtube', 'g_kids', 'g_docs'],
      u_family: ['g_movies', 'g_series'],
      u_kids: ['g_kids'],
      u_guest: ['g_movies'],
    },

    /* user_id -> { media_id: 'allow' | 'deny' }   (per-item override) */
    user_media: {
      u_admin: {},
      u_family: { m_nightfall: 'allow', m_coffee: 'deny' },
      u_kids: { m_aurora: 'allow' },
      u_guest: {},
    },

    media: [
      {
        id: 'm_aurora', title: 'Aurora Drift', year: 2019, kind: 'movie', group: 'g_movies',
        source: 'local', path: 'media/aurora-drift.mp4', url: null,
        duration: 300, size: 24.6, poster: 'media/poster-aurora-drift.jpg',
        probe: { container: 'mp4', video: 'h264', audio: 'aac', width: 1280, height: 720, browserPlayable: true, seekable: true, delivery: 'direct' },
        enabled: true, outputs: { web: true, enigma2: true }, sort: 10, added: '2026-10-20 10:12',
      },
      {
        id: 'm_longway', title: 'The Long Way Home', year: 2018, kind: 'movie', group: 'g_movies',
        source: 'local', path: 'media/long-way-home.mp4', url: null,
        duration: 180, size: 14.7, poster: 'media/poster-long-way-home.jpg',
        probe: { container: 'mp4', video: 'h264', audio: 'aac', width: 1280, height: 720, browserPlayable: true, seekable: true, delivery: 'direct' },
        enabled: true, outputs: { web: true, enigma2: true }, sort: 20, added: '2026-10-20 10:13',
      },
      {
        id: 'm_nightfall', title: 'Nightfall', year: 2021, kind: 'movie', group: 'g_movies',
        source: 'local', path: 'media/nightfall.mkv', url: null,
        duration: 120, size: 10.8, poster: 'media/poster-nightfall.jpg',
        probe: { container: 'mkv', video: 'hevc', audio: 'aac', width: 1920, height: 1080, browserPlayable: false, seekable: true, delivery: 'hls' },
        enabled: true, outputs: { web: true, enigma2: true }, sort: 30, added: '2026-10-22 19:40',
      },
      {
        id: 'm_coffee', title: 'Coffee & Code', year: 2022, kind: 'clip', group: 'g_docs',
        source: 'local', path: 'media/coffee-and-code.mp4', url: null,
        duration: 90, size: 4.2, poster: 'media/poster-coffee-and-code.jpg',
        probe: { container: 'mp4', video: 'h264', audio: 'aac', width: 640, height: 360, browserPlayable: true, seekable: true, delivery: 'direct' },
        enabled: true, outputs: { web: true, enigma2: false }, sort: 40, added: '2026-10-25 08:55',
      },
      {
        id: 'm_signal', title: 'Signal Test', year: 2023, kind: 'audio', group: 'g_docs',
        source: 'local', path: 'media/signal-test.mp3', url: null,
        duration: 60, size: 0.9, poster: null,
        probe: { container: 'mp3', video: null, audio: 'mp3', width: null, height: null, browserPlayable: true, seekable: true, delivery: 'direct' },
        enabled: true, outputs: { web: true, enigma2: false }, sort: 50, added: '2026-10-25 08:57',
      },
      {
        id: 'm_yt_bunny', title: 'Big Buck Bunny (YouTube)', year: 2008, kind: 'movie', group: 'g_youtube',
        source: 'youtube', path: null, url: 'https://www.youtube.com/watch?v=aqz-KE-bpKQ',
        duration: 596, size: null, poster: null,
        resolved: { state: 'resolved', at: '2026-10-31 09:12', quality: '1080p', codec: 'vp9', expires: '2026-11-01 09:12' },
        probe: { container: 'mp4', video: 'vp9', audio: 'opus', width: 1920, height: 1080, browserPlayable: true, seekable: true, delivery: 'direct' },
        enabled: true, outputs: { web: true, enigma2: true }, sort: 60, added: '2026-10-28 15:02',
      },
      {
        id: 'm_yt_ele', title: 'Elephants Dream (YouTube)', year: 2006, kind: 'movie', group: 'g_youtube',
        source: 'youtube', path: null, url: 'https://www.youtube.com/watch?v=TLkA0RELQ1g',
        duration: 653, size: null, poster: null,
        resolved: { state: 'failed', at: '2026-10-31 08:40', error: 'Sign in to confirm you are not a bot' },
        probe: { container: 'mp4', video: null, audio: null, width: null, height: null, browserPlayable: true, seekable: true, delivery: 'embed' },
        enabled: false, outputs: { web: true, enigma2: false }, sort: 70, added: '2026-10-28 15:05',
      },
      {
        id: 'm_url_hls', title: 'Live Test Pattern (HLS url)', year: 2026, kind: 'clip', group: 'g_movies',
        source: 'url', path: null, url: 'https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8',
        duration: null, size: null, poster: null,
        probe: { container: 'hls', video: 'h264', audio: 'aac', width: 1280, height: 720, browserPlayable: true, seekable: false, delivery: 'direct' },
        enabled: true, outputs: { web: true, enigma2: true }, sort: 80, added: '2026-10-29 09:30',
      },
    ],

    receivers: [
      {
        id: 'r_living', name: 'Living room VU+ Duo2', host: '192.168.1.50', port: 21, transport: 'ftp',
        username: 'root', password: '••••••••', serviceType: 4097, bouquet: 'web-player', enabled: true,
        lastPush: '2026-10-31 20:58', entries: 4,
      },
      {
        id: 'r_bedroom', name: 'Bedroom receiver', host: '192.168.1.51', port: 21, transport: 'ftp',
        username: 'root', password: '••••••••', serviceType: 5001, bouquet: 'web-player', enabled: false,
        lastPush: '—', entries: 0,
      },
    ],

    /* resume positions shown in the player */
    progress: { u_family: { m_longway: 96 } },
  };

  /* ------------------------------------------------------------------ */
  /* localStorage persistence (mock-up only)                              */
  /* ------------------------------------------------------------------ */
  const KEY = 'web-player-mockup-v1';

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved && saved.media) DB.media = saved.media;
      if (saved && saved.users) DB.users = saved.users;
      if (saved && saved.groups) DB.groups = saved.groups;
      if (saved && saved.user_groups) DB.user_groups = saved.user_groups;
      if (saved && saved.user_media) DB.user_media = saved.user_media;
      if (saved && saved.receivers) DB.receivers = saved.receivers;
    } catch (e) { /* first run / private mode: keep the in-memory copy */ }
  }

  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify({
        media: DB.media, users: DB.users, groups: DB.groups,
        user_groups: DB.user_groups, user_media: DB.user_media, receivers: DB.receivers,
      }));
    } catch (e) { /* ignore quota / private mode */ }
  }

  function reset() {
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
  }

  load();

  /* ------------------------------------------------------------------ */
  /* derived helpers — these mirror the visibility rules of the real app */
  /* ------------------------------------------------------------------ */
  function groupById(id) { return DB.groups.find(g => g.id === id) || null; }
  function userById(id) { return DB.users.find(u => u.id === id) || null; }
  function mediaById(id) { return DB.media.find(m => m.id === id) || null; }

  /* Is the item visible for the user?
   *   global enabled  AND  ( group allowed  OR  per-item 'allow' )
   *   AND NOT per-item 'deny'
   * and the item must have the web output switched on.
   */
  function visibleTo(item, userId) {
    if (!item.enabled) return false;
    if (!item.outputs || !item.outputs.web) return false;
    const ov = (DB.user_media[userId] || {})[item.id];
    if (ov === 'deny') return false;
    if (ov === 'allow') return true;
    const groups = DB.user_groups[userId] || [];
    return groups.indexOf(item.group) !== -1;
  }

  function visibleMedia(userId) {
    return DB.media.filter(m => visibleTo(m, userId)).sort((a, b) => a.sort - b.sort);
  }

  function whyVisible(item, userId) {
    if (!item.enabled) return 'globally disabled';
    if (!item.outputs.web) return 'web output off';
    const ov = (DB.user_media[userId] || {})[item.id];
    if (ov === 'deny') return 'hidden for this user';
    if (ov === 'allow') return 'granted to this user';
    const groups = DB.user_groups[userId] || [];
    if (groups.indexOf(item.group) !== -1) return 'via group';
    return 'not shared';
  }

  global.WP = {
    DB, save, reset, clone,
    groupById, userById, mediaById,
    visibleTo, visibleMedia, whyVisible,
    fmtDuration(sec) {
      if (sec === null || sec === undefined) return '—';
      const s = Math.max(0, Math.round(sec));
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
      const pad = (n) => String(n).padStart(2, '0');
      return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
    },
  };
})(window);
