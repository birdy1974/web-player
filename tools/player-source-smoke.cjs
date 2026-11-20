/* Smoke-test the public player's startup and playback with real Video.js.
 *
 *   npm ci
 *   node tools/player-source-smoke.cjs
 *
 * A stream URL without a file extension (/player/stream/1/direct) needs
 * its MIME type, or Video.js refuses it without even requesting the file.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole, requestInterceptor } = require('jsdom');

const APP = path.join(__dirname, '..', 'app');
const PLAYLIST_URL = '/api/player/playlist';
const VIDEO_JS = '/static/vendor/video.min.js';
const VIDEO_CSS = '/static/vendor/video-js.min.css';
let failures = 0;

function fail(msg) { failures += 1; console.error('  ✗ ' + msg); }
function check(condition, msg) { if (condition) console.log('  ✓ ' + msg); else fail(msg); }

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

const NEXT_ITEM = {
  ...ITEM, id: 2, title: 'City lights', src: '/player/stream/2/direct',
  download_url: '/player/download/2', download_name: 'city-lights.mp4',
};
const PLAYLIST = { items: [ITEM, NEXT_ITEM], user: { id: 2, name: 'family' }, max_streams: 3 };
const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function until(predicate, message) {
  const deadline = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(message);
    await wait(10);
  }
}

function template(name) {
  return fs.readFileSync(path.join(APP, 'templates', name), 'utf8')
    .replace(/\{\{ asset_url\('([^']+)'\) \}\}/g, '/static/$1?v=test-version');
}

function makeDom() {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on('jsdomError', (e) => errors.push(e.message));
  vc.on('error', (...a) => errors.push(a.join(' ')));

  const requests = new Set();
  const playlist = Promise.withResolvers();
  const assets = { [VIDEO_JS]: Promise.withResolvers(), [VIDEO_CSS]: Promise.withResolvers() };
  let playCalls = 0;
  const dom = new JSDOM(template('player.html'), {
    url: 'http://localhost:8088/player',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
    resources: { interceptors: [requestInterceptor(async (request) => {
      const file = new URL(request.url).pathname;
      requests.add(file);
      const status = assets[file] ? await assets[file].promise : 200;
      if (status !== 200 || !file.startsWith('/static/')) {
        return new Response(null, { status: status === 200 ? 404 : status });
      }
      return new Response(fs.readFileSync(path.join(APP, file)), {
        headers: { 'Content-Type': file.endsWith('.css') ? 'text/css' : 'text/javascript' },
      });
    })] },
    beforeParse(win) {
      win.HTMLMediaElement.prototype.canPlayType = type => type === 'video/mp4' ? 'probably' : '';
      win.HTMLMediaElement.prototype.play = () => { playCalls += 1; return Promise.resolve(); };
      win.HTMLMediaElement.prototype.pause = () => {};
      win.HTMLMediaElement.prototype.load = () => {};
      win.fetch = async (url) => {
        requests.add(url);
        if (url === PLAYLIST_URL) return playlist.promise;
        return new Response('{}', { status: 404 });
      };
    },
  });
  return {
    win: dom.window, errors, requests, playCalls: () => playCalls,
    sendPlaylist: (data = PLAYLIST, status = 200) => playlist.resolve(new Response(JSON.stringify(data), { status })),
    sendAssets: (failedAsset) => Object.entries(assets).forEach(([url, gate]) => gate.resolve(url === failedAsset ? 500 : 200)),
  };
}

(async () => {
  console.log('\n== the sign-in form responds immediately ==');
  {
    const { window: win } = new JSDOM(template('login.html').replace('{{ action }}', '/player/login'), {
      url: 'http://localhost:8088/player/login', runScripts: 'dangerously',
    });
    const button = win.document.querySelector('#login-submit');
    const status = win.document.querySelector('#login-status');
    const submit = () => button.form.dispatchEvent(new win.SubmitEvent('submit', {
      bubbles: true, cancelable: true, submitter: button,
    }));
    check(submit() && button.form.method === 'post' && button.form.action.endsWith('/player/login'),
      'the first submit is still the normal form POST');
    check(button.disabled && button.textContent === 'Signing in…' && status.textContent === 'Signing in…',
      'pending feedback is visible and announced before the network replies');
    check(!submit(), 'a second submit is blocked while signing in');
    win.dispatchEvent(new win.PageTransitionEvent('pageshow', { persisted: true }));
    check(!button.disabled && button.textContent === 'Sign in' && status.textContent === '',
      'returning with the browser Back button resets the form');
    win.close();
  }

  console.log('\n== the startup screen waits for the playlist and playback assets ==');
  {
    const { win, errors, requests, playCalls, sendPlaylist, sendAssets } = makeDom();
    const $ = s => win.document.querySelector(s);
    const startup = $('#player-startup');
    const content = $('#player-content');
    check(!startup.hidden && content.hidden, 'the waiting screen is present in the initial HTML');
    await until(() => [PLAYLIST_URL, VIDEO_JS, VIDEO_CSS].every(url => requests.has(url)),
      'the playlist and the playback assets should be requested in parallel');
    check(!startup.hidden && content.hidden, 'the waiting screen stays up while requests are in flight');
    sendPlaylist();
    await wait(25);
    check(!startup.hidden && content.hidden, 'finishing the playlist alone does not reveal an unfinished player');
    sendAssets();
    await until(() => !content.hidden, 'the player should be revealed when everything is ready');
    check(startup.hidden && $('#p-list .pitem') && $('#who').textContent.endsWith('family'),
      'the waiting screen is replaced by the signed-in playlist');
    const first = $('#p-list .pitem');
    const playButton = $('#btn-play-selected');
    check(first.classList.contains('on') && first.getAttribute('aria-current') === 'true' &&
      $('#now-title').textContent === ITEM.title && !playButton.classList.contains('hidden'),
      'the first title is highlighted and has a Play button');
    check(!win.videojs.getPlayer('v') && playCalls() === 0 &&
      !$('#v').hasAttribute('src') && !$('#yt').hasAttribute('src'),
      'selecting the first title does not load or play the stream');
    check(!$('#opt-next').checked, 'auto-play next starts unchecked');

    console.log('\n== pressing Play starts the selected source with its MIME type ==');
    playButton.click();
    const player = win.videojs.getPlayer('v');
    await until(() => $('#v video')?.getAttribute('src') === ITEM.src, 'the typed direct source should be set');
    // jsdom doesn't load video files. Simulate the event that unblocks Video.js's queued play().
    $('#v video').dispatchEvent(new win.Event('loadstart'));
    await until(() => playCalls() > 0, 'pressing Play should start playback');
    check(player.currentType() === ITEM.mime, `the direct source keeps its MIME type (${player.currentType()})`);
    check(!player.error() && $('#player-idle').classList.contains('hidden'),
      'the video replaces the idle screen without a source error');
    check(errors.length === 0, `no console errors (${errors.join(' | ') || 'none'})`);

    player.trigger('ended');
    check($('#p-list .pitem.on') === first && player.currentSrc().endsWith(ITEM.src),
      'finishing a video leaves the same title selected by default');

    console.log('\n== the same source without its type is refused ==');
    const seen = [];
    player.on('error', () => seen.push(player.error()?.code));
    player.src(ITEM.src);  // no type — the old behaviour
    await until(() => seen.includes(4), 'Video.js should refuse a source whose type it cannot guess');
    check(/stream\.m3u8/.test(player.currentSrc()),
      `the player falls back to the on-demand HLS stream (${player.currentSrc()})`);

    $('#opt-next').checked = true;
    player.trigger('ended');
    await until(() => $('#v video')?.getAttribute('src') === NEXT_ITEM.src, 'opt-in should advance to the next source');
    check($('#now-title').textContent === NEXT_ITEM.title, 'auto-play next still works when enabled');
    win.close();
  }

  console.log('\n== an empty playlist still finishes startup ==');
  {
    const { win, sendPlaylist, sendAssets } = makeDom();
    const $ = s => win.document.querySelector(s);
    sendAssets();
    await until(() => win.videojs, 'Video.js should finish loading before the held playlist');
    check(!$('#player-startup').hidden && $('#player-content').hidden,
      'finishing the playback assets alone does not hide the waiting screen');
    sendPlaylist({ ...PLAYLIST, items: [] });
    await until(() => !$('#player-content').hidden, 'an empty playlist should also reveal the player');
    check($('#player-startup').hidden && $('#p-list').textContent.includes('No media is shared') &&
      $('#btn-play-selected').classList.contains('hidden') && !$('#player-empty').hidden,
      'an empty playlist has an explanation and no Play button');
    win.close();
  }

  console.log('\n== failed startup offers a retry ==');
  for (const failure of [PLAYLIST_URL, VIDEO_JS]) {
    const { win, sendPlaylist, sendAssets } = makeDom();
    const $ = s => win.document.querySelector(s);
    sendPlaylist(PLAYLIST, failure === PLAYLIST_URL ? 503 : 200);
    sendAssets(failure);
    await until(() => !$('#startup-retry').classList.contains('hidden'), 'the retry link should be shown');
    check($('#startup-spinner').hidden && $('#player-content').hidden &&
      $('#startup-title').textContent.includes('Could not load') && $('#startup-retry').getAttribute('href') === '/player',
      `${failure} can fail without leaving the user at a spinner`);
    win.close();
  }

  console.log('\n' + (failures === 0 ? 'ALL PLAYER CHECKS PASSED' : `${failures} FAILURE(S)`));
  process.exit(failures === 0 ? 0 : 1);
})().catch(error => {
  fail(error.stack);
  process.exit(1);
});
