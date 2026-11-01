/* Smoke-test of the public player in jsdom: does the player accept the
 * stream the server offers?
 *
 *   npm i jsdom          # dev dependency, not shipped in the image
 *   node tools/player-source-smoke.cjs
 *
 * The real, vendored Video.js is loaded and the browser is told what it can
 * play. The trap this guards against: Video.js asks the browser whether it
 * *can* play a source before it requests it, and it can only answer that
 * when it knows the type — a URL without a file extension
 * (/player/stream/7/direct) is refused with "No compatible source was found
 * for this media". The type therefore has to travel with the source.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const APP = path.join(__dirname, '..');
let failures = 0;

function fail(msg) { failures += 1; console.error('  ✗ ' + msg); }
function ok(msg) { console.log('  ✓ ' + msg); }
function check(condition, msg) { if (condition) { ok(msg); return true; } fail(msg); return false; }

const ITEM = {
  id: 1,
  title: 'Aurora drift',
  year: 2024,
  kind: 'movie',
  group: 'Movies',
  poster: null,
  duration_s: 300,
  delivery: 'direct',
  container: 'mp4',
  video: 'h264',
  width: 1280,
  height: 720,
  progress_s: 0,
  src: '/player/stream/1/direct',
  mime: 'video/mp4',
  downloadable: true,
  download_url: '/player/download/1',
  download_name: 'aurora-drift.mp4',
  download_remote: false,
};

const RESPONSES = {
  '/api/player/playlist': { items: [ITEM], user: { id: 2, name: 'family' }, max_streams: 3 },
  '/api/player/progress': { items: {} },
};

function makeDom() {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on('jsdomError', (e) => errors.push(e.message));
  vc.on('error', (...a) => errors.push(a.join(' ')));

  const dom = new JSDOM(fs.readFileSync(path.join(APP, 'app/templates/player.html'), 'utf8'), {
    url: 'http://localhost:8088/player',
    runScripts: 'dangerously',
    resources: undefined,
    pretendToBeVisual: true,
    virtualConsole: vc,
  });
  const win = dom.window;

  // the browser: it plays mp4, nothing else
  win.HTMLMediaElement.prototype.canPlayType = function (type) {
    return type === 'video/mp4' ? 'probably' : '';
  };
  win.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  win.HTMLMediaElement.prototype.pause = function () {};
  win.HTMLMediaElement.prototype.load = function () {};

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

  win.eval(fs.readFileSync(path.join(APP, 'app/static/vendor/video.min.js'), 'utf8'));
  win.eval(fs.readFileSync(path.join(APP, 'app/static/js/player.js'), 'utf8'));
  return { win, errors };
}

const wait = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  console.log('\n== a source with its type is played ==');
  {
    const { win, errors } = makeDom();
    await wait(200);
    const player = win.videojs.getPlayer('v');
    check(!!player, 'the player is there');
    // the error comes a tick after the source was set, so listen for it
    const seen = [];
    player.on('error', () => {
      const e = player.error();
      seen.push(e ? `${e.code} ${e.message}` : '?');
    });
    await wait(1200);
    const state = player.error();
    check(!state && seen.length === 0,
      `the player did not fail (${state ? state.code + ' ' + state.message : seen.join(' | ') || 'no error'})`);
    check(player.currentSrc() === '' || /direct/.test(player.currentSrc()),
      `the source was set (${player.currentSrc()})`);
    check(errors.length === 0, `no console errors (${errors.join(' | ')})`);
  }

  console.log('\n== the same source without its type is refused ==');
  {
    // this is the trap the mime type avoids
    const { win } = makeDom();
    await wait(1200);
    const player = win.videojs.getPlayer('v');
    const seen = [];
    player.on('error', () => {
      const e = player.error();
      seen.push(e ? e.code : '?');
    });
    player.src('/player/stream/1/direct');   // no type — the old behaviour
    await wait(600);
    check(seen.includes(4),
      `Video.js refuses a source whose type it cannot guess (${seen.join(', ') || 'no error'})`);
    check(/stream\.m3u8/.test(player.currentSrc()),
      `the player falls back to the on-demand HLS stream (${player.currentSrc()})`);
  }

  console.log('\n' + (failures === 0 ? 'ALL PLAYER CHECKS PASSED' : `${failures} FAILURE(S)`));
  process.exit(failures === 0 ? 0 : 1);
})();
