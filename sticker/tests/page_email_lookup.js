// Page checks for the email route. Supabase is mocked. Nothing is sent to the live project.
const { chromium, devices } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const SHOTS = path.resolve(__dirname, '..', 'email-lookup-screenshots');
const SUPA = fs.readFileSync(require.resolve('@supabase/supabase-js/dist/umd/supabase.js'));
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64');
const RECEIVED = "You're all set! Stickers go out by regular mail about once a week (US only) and usually arrive in 1-2 weeks.";
const EMPTY = "We couldn't find a print for that email. Check the spelling, or use the shop and date option below.";
const BUSY = 'Please try again later.';
const GAMES = [
  { game_title: 'Sap Tapper', bought_on: '2026-10-29' },
  { game_title: 'Hive Runner', bought_on: '2026-10-01' }
];

fs.mkdirSync(SHOTS, { recursive: true });
const results = [];
function rec(name, pass, detail) {
  results.push({ name, pass: !!pass, detail: detail || '' });
  console.log((pass ? 'PASS ' : 'FAIL ') + name + (detail ? ' :: ' + detail : ''));
}

function serve() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url, 'http://127.0.0.1');
      const rel = decodeURIComponent(url.pathname === '/' ? '/sticker.html' : url.pathname);
      const full = path.normalize(path.join(ROOT, rel));
      if (!full.startsWith(ROOT)) {
        res.writeHead(403);
        res.end();
        return;
      }
      fs.readFile(full, (err, data) => {
        if (err) {
          res.writeHead(404);
          res.end('missing');
          return;
        }
        const ext = path.extname(full);
        const type = ext === '.html' ? 'text/html; charset=utf-8'
          : ext === '.js' ? 'text/javascript'
          : 'application/octet-stream';
        res.writeHead(200, { 'content-type': type });
        res.end(data);
      });
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function fulfill(route, body, type) {
  return route.fulfill({
    status: 200,
    contentType: type || 'application/json',
    headers: {
      'access-control-allow-origin': '*',
      'access-control-allow-headers': '*',
      'access-control-allow-methods': 'GET,POST,PATCH,DELETE,OPTIONS'
    },
    body: typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body)
  });
}

async function noOverflow(page) {
  return page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    inner: window.innerWidth,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1
  }));
}

async function fillAddress(page) {
  await page.fill('#mail-name', 'Fake Tester');
  await page.fill('#mail-street', '1 Test St');
  await page.fill('#mail-city', 'Testville');
  await page.selectOption('#mail-state', 'FL');
  await page.fill('#mail-zip', '33601');
  await page.fill('#mail-email', 'fake.tester@example.com');
}

(async () => {
  const server = await serve();
  const port = server.address().port;
  const origin = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch();
  let mode = 'happy';
  const calls = [];
  const seen = [];

  async function wire(context) {
    await context.route('**/*', async (route) => {
      const req = route.request();
      const u = new URL(req.url());
      if (u.hostname === '127.0.0.1') return route.continue();
      if (req.method() === 'OPTIONS') return fulfill(route, '');
      if (u.hostname === 'cdn.jsdelivr.net') return fulfill(route, SUPA, 'application/javascript');
      if (u.hostname === 'example.com') return fulfill(route, PNG, 'image/png');
      if (u.hostname !== 'tskbfytfjsiavwysecuk.supabase.co') {
        seen.push(u.href);
        return route.abort();
      }
      seen.push(u.pathname);
      const fn = u.pathname.split('/').pop();
      let sent = null;
      try { sent = req.postDataJSON(); } catch (err) { sent = null; }
      if (req.method() !== 'GET') calls.push({ fn, body: sent });
      if (u.pathname.includes('/games') && !u.pathname.includes('game_upsells')) {
        return fulfill(route, [
          { title: 'Bee Quest' },
          { title: 'Hive Runner' },
          { title: 'Sap Tapper' }
        ]);
      }
      if (u.pathname.includes('/game_upsells')) {
        return fulfill(route, [{
          game_title: 'Sap Tapper',
          item_type: 'Holographic sticker',
          price: 5,
          inventory: 3,
          image_url: 'https://example.com/sap.png'
        }]);
      }
      if (fn === 'sticker_shop_list') {
        return fulfill(route, [{ shop_key: 'shopa', display_name: 'Test Shop A' }]);
      }
      if (fn === 'sticker_games_for_email') {
        if (mode === 'busy') return fulfill(route, { ok: false, status: 'busy' });
        if (mode === 'empty') return fulfill(route, { games: [] });
        return fulfill(route, { games: GAMES });
      }
      if (fn === 'sticker_check') {
        if (mode === 'review') return fulfill(route, { result: 'no_match', game: null, shop: null });
        if (sent && sent.p_route === 'shop') {
          return fulfill(route, { result: 'match', game: sent.p_game, shop: 'Test Shop A' });
        }
        return fulfill(route, { result: 'match', game: 'Sap Tapper', shop: 'Test Shop A' });
      }
      if (fn === 'sticker_submit') return fulfill(route, { ok: true, status: 'received' });
      return fulfill(route, []);
    });
  }

  async function openPage(width, height) {
    const context = await browser.newContext({
      ...devices['iPhone 13'],
      viewport: { width, height },
      hasTouch: true,
      isMobile: true
    });
    await wire(context);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (err) => errors.push(String(err)));
    await page.goto(origin + '/sticker.html', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#kiosk-email');
    return { context, page, errors };
  }

  const phone = devices['iPhone 13'];
  let matchThanks = '';

  try {
    for (const [w, h] of [[390, 844], [375, 667], [320, 568]]) {
      mode = 'happy';
      calls.length = 0;
      const tag = `${w}x${h}`;
      const { context, page, errors } = await openPage(w, h);
      const shot = (name) => page.screenshot({ path: path.join(SHOTS, `${tag}-${name}.png`), fullPage: true });
      const checkFit = async (where) => {
        const box = await noOverflow(page);
        rec(`${tag} no horizontal scroll on ${where}`, !box.overflow, `scroll ${box.scroll} inner ${box.inner}`);
      };
      try {
        rec(`${tag} email question is first`,
          (await page.textContent('#email-prompt')).trim() === 'Did you type your email at the kiosk?'
          && (await page.textContent('#choose-shop')).includes("didn't enter an email"));
        const emailPx = await page.$eval('#kiosk-email', (el) => getComputedStyle(el).fontSize);
        rec(`${tag} email input is 16px`, emailPx === '16px', emailPx);
        await shot('email');
        await checkFit('email step');

        await page.fill('#kiosk-email', 'Buyer@Example.com');
        await page.tap('#email-check');
        await page.waitForSelector('#email-games:not([hidden])');
        await page.waitForFunction(() => !document.getElementById('email-check').disabled);
        const labels = await page.$$eval('#email-game option', (opts) => opts.map((o) => o.textContent));
        rec(`${tag} game list labels`,
          JSON.stringify(labels) === JSON.stringify([
            'Select a game',
            'Sap Tapper - bought Oct 29',
            'Hive Runner - bought Oct 1'
          ]), labels.join(' | '));
        const gamePx = await page.$eval('#email-game', (el) => getComputedStyle(el).fontSize);
        rec(`${tag} game select is 16px`, gamePx === '16px', gamePx);
        await shot('games');
        await checkFit('game list');

        await page.selectOption('#email-game', { label: 'Sap Tapper - bought Oct 29' });
        await page.tap('#email-use');
        await page.waitForSelector('#screen-result:not([hidden])');
        const shopVisible = await page.locator('#bought-on').isVisible();
        rec(`${tag} address step does not ask shop or date`, shopVisible === false, `date visible=${shopVisible}`);
        const namePx = await page.$eval('#mail-name', (el) => getComputedStyle(el).fontSize);
        rec(`${tag} address inputs are 16px`, namePx === '16px', namePx);
        await shot('address');
        await checkFit('address form');

        if (w === 390) {
          await fillAddress(page);
          await page.tap('#send-btn');
          await page.waitForSelector('#screen-done:not([hidden])');
          await page.waitForSelector('#upsells:not([hidden])');
          matchThanks = (await page.textContent('#done-text')).trim();
          const upsell = await page.locator('.upsell-item').first().getAttribute('aria-label');
          rec('email happy path thank-you', matchThanks === RECEIVED, matchThanks);
          rec('upsells render after a received email claim',
            upsell === 'Holographic sticker - $5.00', upsell);
          const lookup = calls.find((c) => c.fn === 'sticker_games_for_email');
          const check = calls.find((c) => c.fn === 'sticker_check');
          const submit = calls.find((c) => c.fn === 'sticker_submit');
          rec('lookup sends the typed email', lookup && lookup.body.p_email === 'Buyer@Example.com', JSON.stringify(lookup && lookup.body));
          rec('check uses email, game, and day',
            check && check.body.p_route === 'email' && check.body.p_game === 'Sap Tapper'
            && check.body.p_bought_on === '2026-10-29' && check.body.p_kiosk_email === 'Buyer@Example.com'
            && !check.body.p_shop_key, JSON.stringify(check && check.body));
          rec('submit binds that email, game, and day',
            submit && submit.body.p_route === 'email' && submit.body.p_game === 'Sap Tapper'
            && submit.body.p_bought_on === '2026-10-29' && submit.body.p_kiosk_email === 'Buyer@Example.com'
            && !submit.body.p_shop_key && !submit.body.p_print_log_id, JSON.stringify(submit && submit.body));
          await shot('done');
          await checkFit('thank-you');
        }

        await page.goto(origin + '/sticker.html', { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#choose-shop');
        await page.tap('#choose-shop');
        await page.waitForSelector('#screen-shop:not([hidden])');
        await page.waitForFunction(() => document.getElementById('game').options.length > 1);
        const focusInfo = await page.evaluate(() => ({
          id: (document.activeElement && document.activeElement.id) || '',
          coarse: window.matchMedia('(pointer: coarse)').matches
        }));
        // The page skips focus() on a coarse pointer so iOS does not open the picker.
        // Chromium's touch preset in this VM still reports a fine pointer, so focus is expected there.
        const focusOk = focusInfo.coarse
          ? !['game', 'shop', 'mail-state'].includes(focusInfo.id)
          : focusInfo.id === 'game';
        rec(`${tag} shop select focus follows the pointer`, focusOk,
          `coarse=${focusInfo.coarse} active=${focusInfo.id || 'none'}`);
        const datePx = await page.$eval('#bought-on', (el) => getComputedStyle(el).fontSize);
        rec(`${tag} date input is 16px`, datePx === '16px', datePx);
        await shot('shop');
        await checkFit('shop fallback');

        if (w === 375) {
          calls.length = 0;
          const today = await page.locator('#bought-on').getAttribute('max');
          await page.selectOption('#game', 'Sap Tapper');
          await page.selectOption('#shop', 'shopa');
          await page.fill('#bought-on', today);
          await page.tap('#shop-check');
          await page.waitForSelector('#screen-result:not([hidden])');
          await fillAddress(page);
          await page.tap('#send-btn');
          await page.waitForSelector('#screen-done:not([hidden])');
          await page.waitForSelector('#upsells:not([hidden])');
          const thanks = (await page.textContent('#done-text')).trim();
          const submit = calls.find((c) => c.fn === 'sticker_submit');
          rec('shop fallback reaches the same thank-you', thanks === RECEIVED, thanks);
          rec('shop submit still sends shop, game, and date',
            submit && submit.body.p_route === 'shop' && submit.body.p_game === 'Sap Tapper'
            && submit.body.p_shop_key === 'shopa' && submit.body.p_bought_on === today,
            JSON.stringify(submit && submit.body));
          await shot('shop-done');
          await checkFit('shop thank-you');
        }
        rec(`${tag} no JavaScript errors`, errors.length === 0, errors.join('; '));
      } catch (err) {
        rec(`${tag} flow`, false, err.message.split('\n')[0]);
      }
      await context.close();
    }

    mode = 'empty';
    calls.length = 0;
    {
      const { context, page, errors } = await openPage(390, 844);
      try {
        await page.fill('#kiosk-email', 'nobody@example.com');
        await page.tap('#email-check');
        await page.waitForFunction((text) => document.getElementById('email-status').textContent === text, EMPTY);
        const gamesHidden = await page.locator('#email-games').getAttribute('hidden');
        const status = (await page.textContent('#email-status')).trim();
        rec('empty result uses the neutral message', status === EMPTY, status);
        rec('empty result does not show games', gamesHidden !== null, `hidden=${gamesHidden}`);
        await page.screenshot({ path: path.join(SHOTS, '390x844-empty.png'), fullPage: true });
        rec('empty screen has no JavaScript errors', errors.length === 0, errors.join('; '));
      } catch (err) {
        rec('empty result', false, err.message.split('\n')[0]);
      }
      await context.close();
    }

    mode = 'busy';
    {
      const { context, page, errors } = await openPage(390, 844);
      try {
        await page.fill('#kiosk-email', 'nobody@example.com');
        await page.tap('#email-check');
        await page.waitForFunction((text) => document.getElementById('email-status').textContent === text, BUSY);
        const status = (await page.textContent('#email-status')).trim();
        rec('busy result asks to try again later', status === BUSY, status);
        rec('busy result does not use the empty-email message', !status.includes("couldn't find"), status);
        await page.screenshot({ path: path.join(SHOTS, '390x844-busy.png'), fullPage: true });
        rec('busy screen has no JavaScript errors', errors.length === 0, errors.join('; '));
      } catch (err) {
        rec('busy result', false, err.message.split('\n')[0]);
      }
      await context.close();
    }

    mode = 'review';
    calls.length = 0;
    {
      const { context, page, errors } = await openPage(390, 844);
      try {
        await page.fill('#kiosk-email', 'Buyer@Example.com');
        await page.tap('#email-check');
        await page.waitForSelector('#email-games:not([hidden])');
        await page.selectOption('#email-game', { label: 'Sap Tapper - bought Oct 29' });
        await page.tap('#email-use');
        await page.waitForSelector('#screen-result:not([hidden])');
        await fillAddress(page);
        await page.tap('#send-btn');
        await page.waitForSelector('#screen-done:not([hidden])');
        const thanks = (await page.textContent('#done-text')).trim();
        rec('review claim uses the same thank-you as a match', thanks === matchThanks && thanks === RECEIVED, thanks);
        rec('review screen has no JavaScript errors', errors.length === 0, errors.join('; '));
      } catch (err) {
        rec('review thank-you', false, err.message.split('\n')[0]);
      }
      await context.close();
    }

    const leaked = seen.filter((u) => /print_logs|sticker_claims|email_lookup/.test(u));
    rec('page does not read print_logs, claims, or the lookup log', leaked.length === 0, leaked.join(', '));
    rec('no unexpected hosts', seen.every((u) => !u.startsWith('http') || u.includes('supabase.co') || u.includes('example.com')),
      seen.filter((u) => u.startsWith('http')).join(', '));
  } finally {
    await browser.close();
    server.close();
  }

  const passed = results.filter((r) => r.pass).length;
  console.log(`${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
