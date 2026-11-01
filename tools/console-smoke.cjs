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
  '/api/console/overview': { media_count: 1, user_count: 1, enabled_count: 1 },
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

  console.log('\n== no errors ==');
  check(errors.length === 0, `no console errors (${errors.join(' | ')})`);

  console.log('\n' + (failures === 0 ? 'ALL CONSOLE CHECKS PASSED' : `${failures} FAILURE(S)`));
  process.exit(failures === 0 ? 0 : 1);
})();
