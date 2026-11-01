/* Smoke-test of the management console in jsdom: load the page, drive the
 * file browser and fail on any console error / uncaught exception.
 *
 *   npm i jsdom          # dev dependency, not shipped in the image
 *   node tools/console-smoke.cjs
 *
 * The API is stubbed, so the test needs neither a server nor a NAS.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const APP = path.join(__dirname, '..');
let failures = 0;

function fail(msg) { failures += 1; console.error('  ✗ ' + msg); }
function ok(msg) { console.log('  ✓ ' + msg); }
function check(condition, msg) { if (condition) { ok(msg); return true; } fail(msg); return false; }

/* what the console API answers */
const LISTING = {
  root: '/media',
  path: '',
  parent: null,
  breadcrumbs: [{ name: 'media root', path: '' }],
  entries: [
    { name: 'movies', type: 'dir', path: 'movies', size: null, media: false, kind: null, in_library: false },
    { name: 'notes.txt', type: 'file', path: 'notes.txt', size: 12, media: false, kind: null, in_library: false },
    { name: 'aurora-drift.mp4', type: 'file', path: 'aurora-drift.mp4', size: 25746116, media: true, kind: 'video', in_library: true },
  ],
  counts: { dirs: 1, files: 2, media: 1 },
  truncated: false,
};

const RESPONSES = {
  '/api/console/overview': {
    media_count: 1, user_count: 1, enabled_count: 1,
    log_counts: { DEBUG: 0, INFO: 12, WARNING: 1, ERROR: 0, CRITICAL: 0 },
  },
  '/api/console/media': {
    items: [{
      id: 1, title: 'Aurora drift', kind: 'movie', source_type: 'local',
      path: 'aurora-drift.mp4', url: null, group_id: 1, enabled: true,
      delivery: 'direct', probe: {}, per_user: {}, sort_order: 0,
      outputs: { web: true, enigma2: false },
    }],
  },
  '/api/console/users': {
    items: [{ id: 1, name: 'family', role: 'user', enabled: true, group_ids: [1], overrides: {}, max_streams: 2 }],
    groups: [{ id: 1, name: 'Movies' }],
  },
  '/api/console/groups': { items: [{ id: 1, name: 'Movies' }] },
  '/api/console/receivers': { items: [], players: {} },
  '/api/console/browse': LISTING,
  '/api/console/logs': {
    records: [
      { ts: '2026-11-01 15:10:43.120', level: 'INFO', logger: 'web-player.access',
        msg: 'GET /player/stream/1/direct → 200 in 12 ms (user=family, ip=192.168.1.20)',
        where: 'player.py:250' },
      { ts: '2026-11-01 15:10:44.001', level: 'WARNING', logger: 'web-player.hls',
        msg: 'hls ffmpeg for media=3 stopped (code 1): Invalid data found',
        where: 'hls.py:210' },
    ],
    counts: { DEBUG: 0, INFO: 1, WARNING: 1, ERROR: 0, CRITICAL: 0 },
    file: { path: '/config/web-player.log', exists: true, size: 2048,
            modified: '2026-11-01T15:10:45' },
    levels: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
    config: { level: 'INFO', lines: 2000, max_bytes: 5242880,
              path: '/config/web-player.log' },
  },
};

const vc = new VirtualConsole();
const errors = [];
vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.stack || e.message)));
vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));

const dom = new JSDOM(fs.readFileSync(path.join(APP, 'app/templates/console.html'), 'utf8'), {
  url: 'http://localhost:8088/console',
  runScripts: 'dangerously',
  resources: undefined,
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const win = dom.window;

/* the console talks to /api/console/* — here that is a stub */
win.fetch = async (url) => {
  const key = String(url).split('?')[0];
  const data = RESPONSES[key];
  return {
    ok: data !== undefined,
    status: data !== undefined ? 200 : 404,
    statusText: data !== undefined ? 'OK' : 'Not found',
    text: async () => (data !== undefined ? JSON.stringify(data) : '{"detail":"not found"}'),
  };
};

win.eval(fs.readFileSync(path.join(APP, 'app/static/js/console.js'), 'utf8'));

const $ = (s) => win.document.querySelector(s);
const $$ = (s) => Array.from(win.document.querySelectorAll(s));
const wait = (ms) => new Promise(r => setTimeout(r, ms));
const click = (el) => el.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));

(async () => {
  await wait(300);

  console.log('\n== the file browser ==');
  check(!!$('#btn-browse'), 'the console has a "browse files" button');

  click($('#btn-browse'));
  await wait(300);

  check(!!$('#modal-root .modal'), 'the browser opens as a window');
  const rows = $$('.br-row');
  check(rows.length === 3, `the browser lists the folder (${rows.length} rows)`);
  check(!!$('.br-crumbs a'), 'the breadcrumbs are drawn');
  check($('.br-crumbs a').textContent === 'media root', 'the first crumb is the media root');
  check(!!rows.find(r => r.textContent.includes('movies')), 'the folder is listed');
  const media = rows.find(r => r.textContent.includes('aurora-drift.mp4'));
  check(!!media, 'the media file is listed');
  check(media.textContent.includes('in library'), 'a file that is in the library is marked');

  click(media);
  await wait(100);
  check($('#br-add-file') && !$('#br-add-file').disabled, 'the file can be added');
  check($('#br-add-folder') && $('#br-add-folder').disabled, 'the folder button waits for a folder');

  click(rows.find(r => r.textContent.includes('movies')));
  await wait(100);
  check(!$('#br-add-folder').disabled, 'the folder can be added');

  $('#modal-root .br-filter').value = 'aurora';
  $('#modal-root .br-filter').dispatchEvent(new win.Event('input', { bubbles: true }));
  await wait(100);
  check($$('.br-row').length === 1, `the filter narrows the list (${$$('.br-row').length})`);

  console.log('\n== the picker in the media form ==');
  click($('#modal-root .x'));
  await wait(100);
  click($('#btn-add-media'));
  await wait(200);
  check(!!$('#f-browse'), 'the media form has a browse button');
  click($('#f-browse'));
  await wait(300);
  check(!$('#f-browser').classList.contains('hidden'), 'the form switches to the browser');
  const picker = $$('#f-browser .br-row').find(r => r.textContent.includes('aurora-drift.mp4'));
  check(!!picker, 'the picker lists the files');
  picker.dispatchEvent(new win.MouseEvent('dblclick', { bubbles: true }));
  await wait(200);
  check($('#f-path').value === 'aurora-drift.mp4', `the path is filled in (${$('#f-path').value})`);
  check(!$('#f-form').classList.contains('hidden'), 'the form comes back');

  console.log('\n== the dashboard shows the trouble ==');
  const dashLog = $('#dash-log');
  check(!!dashLog, 'the dashboard has a line for the log');
  check(dashLog.textContent.includes('error(s)'), `the errors are counted (${dashLog.textContent.trim()})`);

  console.log('\n== the log tab ==');
  const logTab = $$('#tabs .tab').find(t => t.dataset.tab === 'logs');
  check(!!logTab, 'the console has a "Logs" tab');
  click(logTab);
  await wait(400);
  check(!$('#tab-logs').classList.contains('hidden'), 'the log tab opens');
  const lines = $$('#log-box .logline');
  check(lines.length === 2, `the log lines are shown (${lines.length})`);
  check(lines[0].textContent.includes('web-player.access'), 'the line says where it came from');
  check(lines[1].classList.contains('WARNING'), 'the level is shown');
  check($('#log-info').textContent.includes('/config/web-player.log'),
    'the log file is named');

  console.log('\n== the symbol ==');
  const logo = $('.brand img.logo');
  check(!!logo, 'the header shows the symbol');
  check(logo.getAttribute('src').includes('brand/web-player.svg'), 'it is the symbol of the player');
  check(!!$('link[rel="icon"]'), 'the page has a favicon');
  check($('link[rel="icon"]').getAttribute('href').includes('favicon.svg'), 'it is the symbol as an icon');

  console.log('\n== no errors ==');
  check(errors.length === 0, `no console errors (${errors.join(' | ')})`);

  console.log('\n' + (failures === 0 ? 'ALL CONSOLE CHECKS PASSED' : `${failures} FAILURE(S)`));
  process.exit(failures === 0 ? 0 : 1);
})();
