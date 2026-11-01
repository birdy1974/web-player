/* Smoke-test the web-player mock-up in jsdom: load the pages, drive the UI and
 * fail on any console error / uncaught exception.
 *
 *   npm i jsdom          # dev dependency, not shipped in the image
 *   node tools/mockup-smoke.cjs
 *
 * The ROOT path is derived from this file, so it works from anywhere. */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.join(__dirname, '..', 'mockup');
let failures = 0;

function fail(msg) { failures += 1; console.error('  ✗ ' + msg); }
function ok(msg) { console.log('  ✓ ' + msg); }

function load(file, extra) {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e.stack || e.message)));
  vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, file), 'utf8'), {
    url: 'http://localhost:8088/' + file,
    runScripts: 'dangerously',
    resources: undefined,
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      win.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
      win.HTMLMediaElement.prototype.pause = function () {};
      win.HTMLMediaElement.prototype.load = function () {};
      if (extra) extra(win);
    },
  });
  return { dom, win: dom.window, errors };
}

function injectScript(win, rel) {
  const code = fs.readFileSync(path.join(ROOT, rel), 'utf8');
  const el = win.document.createElement('script');
  el.textContent = code;
  win.document.body.appendChild(el);
}

(async function main() {
  /* ------------------------------------------------ console page ------ */
  console.log('\n== console.html ==');
  {
    const { win, errors } = load('console.html');
    injectScript(win, 'assets/fake-data.js');
    injectScript(win, 'assets/console.js');

    const stats = win.document.querySelectorAll('#dash-stats .stat').length;
    stats === 4 ? ok('dashboard renders 4 stat cards') : fail('dashboard stat cards: ' + stats);

    const visRows = win.document.querySelectorAll('#dash-visibility tbody tr').length;
    visRows === 4 ? ok('visibility table lists 4 users') : fail('visibility rows: ' + visRows);

    // switch to the media tab
    win.document.querySelector('[data-tab="media"]').click();
    const rows = win.document.querySelectorAll('#m-table tbody tr');
    rows.length === 8 ? ok('media table lists 8 items') : fail('media rows: ' + rows.length);

    const visDots = win.document.querySelectorAll('#m-table tbody .vis-dot');
    visDots.length === 32 ? ok('per-user visibility cells rendered (8 items x 4 users)') : fail('vis cells: ' + visDots.length);

    // toggle a per-user override: the cell cycles group -> grant -> hide -> group
    const dot = win.document.querySelector('#m-table tbody .vis-dot[data-item="m_coffee"][data-user="u_family"]');
    dot.click();                                   // deny -> group rule
    const s1 = win.WP.DB.user_media.u_family.m_coffee;
    s1 === undefined ? ok('override cycles back to the group rule') : fail('override: ' + s1);
    win.document.querySelector('#m-table tbody .vis-dot[data-item="m_coffee"][data-user="u_family"]').click();
    const s2 = win.WP.DB.user_media.u_family.m_coffee;
    s2 === 'allow' ? ok('override cycles to "grant"') : fail('override: ' + s2);

    // toggle an output
    const cb = win.document.querySelector('#m-table tbody tr[data-id="m_aurora"] input[data-out="enigma2"]');
    cb.checked = false;
    cb.dispatchEvent(new win.Event('change'));
    win.WP.DB.media.find((m) => m.id === 'm_aurora').outputs.enigma2 === false
      ? ok('output toggle writes to the model') : fail('output toggle failed');

    // add a media item through the modal
    win.document.querySelector('#btn-add-media').click();
    const titleInput = win.document.querySelector('#f-title');
    titleInput.value = 'Smoke Test Item';
    win.document.querySelector('#f-save').click();
    win.WP.DB.media.length === 9 ? ok('add-media modal saves an item') : fail('media count: ' + win.WP.DB.media.length);

    // users tab
    win.document.querySelector('[data-tab="users"]').click();
    const urows = win.document.querySelectorAll('#u-table tbody tr');
    urows.length === 4 ? ok('users table lists 4 users') : fail('user rows: ' + urows.length);

    win.document.querySelector('#btn-add-user').click();
    win.document.querySelector('#u-name').value = 'smoke';
    win.document.querySelector('#u-pw').value = 'x';
    win.document.querySelector('#u-save').click();
    win.WP.DB.users.length === 5 ? ok('add-user modal saves a user') : fail('users: ' + win.WP.DB.users.length);

    // enigma2 tab
    win.document.querySelector('[data-tab="e2"]').click();
    const preview = win.document.querySelector('#e2-preview').textContent;
    preview.includes('#SERVICE') && preview.includes('%3a')
      ? ok('bouquet preview renders service references with escaped colons')
      : fail('bouquet preview: ' + preview.slice(0, 120));
    const ref = preview.split('\n').find((l) => l.startsWith('#SERVICE 4097'));
    ref && ref.includes('%3a//') ? ok('bouquet URL is percent-encoded (: -> %3a)') : fail('url encoding: ' + ref);

    // settings tab
    win.document.querySelector('[data-tab="settings"]').click();
    win.document.querySelector('#s-seg').value = '6';
    win.document.querySelector('#s-seg').dispatchEvent(new win.Event('change'));
    win.WP.DB.settings.hlsSegmentSeconds === 6 ? ok('settings save works') : fail('settings save failed');

    const real = errors.filter((e) => !/Not implemented|Could not parse CSS/.test(e));
    real.length === 0 ? ok('no console errors') : real.forEach((e) => fail(e));
    win.close();
  }

  /* ------------------------------------------------- player page ------ */
  console.log('\n== player.html ==');
  {
    const { win, errors } = load('player.html', (w) => {
      w.videojs = function () {
        const api = {
          _t: 0, _d: 300, _src: '', _paused: true,
          src(s) { this._src = s; }, poster() {}, play() { this._paused = false; return Promise.resolve(); },
          pause() { this._paused = true; }, currentTime(t) { if (t === undefined) return this._t; this._t = t; },
          duration() { return this._d; }, paused() { return this._paused; }, muted() { return false; },
          requestFullscreen() {}, dispose() {}, on() {}, one() {},
        };
        w.__player = api;
        return api;
      };
    });
    injectScript(win, 'assets/fake-data.js');
    injectScript(win, 'assets/player.js');

    // sign in as 'family'
    win.document.querySelector('#l-user').value = 'family';
    win.document.querySelector('#l-pass').value = 'demo';
    win.document.querySelector('#login-form').dispatchEvent(new win.Event('submit', { cancelable: true }));

    const visible = win.document.querySelectorAll('#p-list .pitem').length;
    visible === 4 ? ok('family sees 4 items (groups + per-item grants)') : fail('visible items: ' + visible);

    const count = win.document.querySelector('#p-count').textContent;
    count.includes('4 of 4') ? ok('counter reads "4 of 4 titles" (search result / playlist)') : fail('counter: ' + count);

    // play the second item
    win.document.querySelectorAll('#p-list .pitem')[1].click();
    win.__player._src ? ok('video source set to ' + win.__player._src) : fail('no video source');

    // jump to a timestamp
    win.document.querySelector('#jump-in').value = '01:23:45';
    win.document.querySelector('#btn-jump').click();
    win.__player.currentTime() === 5025 ? ok('jump to 01:23:45 lands on 5025 s') : fail('jump: ' + win.__player.currentTime());

    // +10s / -10s
    win.__player._d = 100000;                      // long file so the jump is not clamped
    win.document.querySelector('#btn-jump').click();
    win.document.querySelector('#btn-fwd').click();
    win.__player.currentTime() === 5035 ? ok('+10 s works') : fail('fwd: ' + win.__player.currentTime());
    win.__player._d = 300;

    // search filter
    win.document.querySelector('#p-search').value = 'long';
    win.document.querySelector('#p-search').dispatchEvent(new win.Event('input'));
    win.document.querySelectorAll('#p-list .pitem').length === 1 ? ok('playlist search filters') : fail('search filter');

    // a disabled account cannot sign in
    win.document.querySelector('#btn-logout').click();
    win.document.querySelector('#l-user').value = 'guest';
    win.document.querySelector('#l-pass').value = 'demo';
    win.document.querySelector('#login-form').dispatchEvent(new win.Event('submit', { cancelable: true }));
    win.document.querySelector('#l-error').textContent.includes('disabled')
      ? ok('disabled account is refused') : fail('disabled account: ' + win.document.querySelector('#l-error').textContent);

    const real = errors.filter((e) => !/Not implemented|Could not parse CSS/.test(e));
    real.length === 0 ? ok('no console errors') : real.forEach((e) => fail(e));
    win.close();
  }

  console.log('\n' + (failures === 0 ? 'ALL SMOKE TESTS PASSED' : failures + ' FAILURE(S)'));
  process.exit(failures === 0 ? 0 : 1);
})();
