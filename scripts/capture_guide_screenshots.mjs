#!/usr/bin/env node
/**
 * Regenerates the screenshots used by the user guide (the .webp files under guide-src, in each section img folder) from the demo account.
 *
 *   python manage.py runserver 8000          # in one terminal (the demo user is seeded on first visit)
 *   node scripts/capture_guide_screenshots.mjs                 # every shot
 *   node scripts/capture_guide_screenshots.mjs accounts-list   # shots whose file name contains the text
 *
 * Needs Google Chrome (path via CHROME_PATH) and Node 22+ (built-in WebSocket); no npm packages.
 * Shots are laid out at 1440x900, light theme, and saved 1100px wide (the size of the existing guide images).
 */
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.BASE_URL || 'http://localhost:8000';
const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9333 + Math.floor(Math.random() * 300);
const FILTER = process.argv[2] || '';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------------------------------
// Shot list. `file` is relative to guide-src. `url` is opened as the demo user. `before` runs in the page
// (after load) to put the UI in the state the guide describes; `clip` crops to a CSS selector's box.
// ---------------------------------------------------------------------------------------------------
// Laid out like a 1440x900 desktop (what the guide has always shown) and saved 1100px wide
const PAGE = { w: 1440, h: 900 };
const OUT_W = 1100;
const SCALE = OUT_W / PAGE.w;
const TYPE_INTO_COMPOSER = (text) => `
      const box = document.getElementById('tmr-quick-input');
      box.focus(); box.value = ${JSON.stringify(text)};
      box.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('tmr-go').click();`;
const SUBSCRIPTION_STEP = (n) => `
      const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); };
      set(document.getElementById('id_description'), 'Netflix');
      set(document.getElementById('id_amount'), '649');
      ${n >= 2 ? "const freq = document.getElementById('id_frequency'); freq.value = 'MONTHLY'; freq.dispatchEvent(new Event('change', { bubbles: true }));" : ''}
      ${n >= 2 ? "document.getElementById('nextBtn').click();" : ''}
      ${n >= 3 ? "document.getElementById('nextBtn').click();" : ''}`;
const CLEAN = `(() => {
        try { localStorage.setItem('theme', 'light'); localStorage.setItem('cookieConsent', 'accepted'); } catch (e) {}
        document.documentElement.setAttribute('data-bs-theme', 'light');
        document.body.classList.remove('demo-mode');
        document.querySelectorAll('.sidebar *, nav *').forEach((e) => { if (!e.children.length && e.textContent.trim() === 'DE') e.textContent = 'RI'; });
        const style = document.createElement('style');
        style.textContent = '.demo-banner{display:none!important} #cookie-banner,.cookie-banner,#cookieConsent{display:none!important}';
        document.head.appendChild(style); return true;
      })()`;
const shots = [
  { file: '01-getting-started/img/dashboard-desktop.webp', url: '/dashboard/' },
  { file: '01-getting-started/img/salary-cycle-desktop.webp', url: '/settings/profile/' },
  { file: '01-getting-started/img/categories-desktop.webp', url: '/category/list/' },
  { file: '02-accounts/img/accounts-list-desktop.webp', url: '/accounts/list/?sort=attention' },
  { file: '02-accounts/img/add-account-desktop.webp', url: '/accounts/add/' },
  { file: '02-accounts/img/account-detail-desktop.webp', url: '/accounts/list/', before: `
      const a=[...document.querySelectorAll('.acx-name')].find(x=>x.textContent.includes('HDFC Bank')); location.href=a.href; ` , wait: 1800 },
  { file: '03-transactions-expenses/img/expenses-list-desktop.webp', url: '/expenses/' },
  { file: '03-transactions-expenses/img/add-menu-desktop.webp', url: '/dashboard/', before: `
      const toggle = [...document.querySelectorAll('.navbar .dropdown-toggle, nav .dropdown-toggle')].find(a => a.textContent.trim() === 'Add');
      const menu = toggle.parentElement.querySelector('.dropdown-menu');
      // The demo account hides Add Transfer; every real account has it, so show it as users see it
      if (![...menu.querySelectorAll('a')].some(a => a.textContent.includes('Transfer'))) {
        const item = menu.querySelector('li').cloneNode(true);
        const link = item.querySelector('a');
        link.href = '/transfers/add/';
        link.innerHTML = '<span class="add-menu-ico text-info"><i class="bi bi-arrow-left-right"></i></span> Add Transfer';
        menu.insertBefore(item, menu.querySelectorAll('li')[2]);
      }
      toggle.click();` },
  { file: '03-transactions-expenses/img/composer-parsed-desktop.webp', url: '/expenses/add/', before: `${TYPE_INTO_COMPOSER('zomato 450 yesterday upi')}`, wait: 1800 },
  { file: '03-transactions-expenses/img/large-amount-desktop.webp', url: '/expenses/add/', before: `${TYPE_INTO_COMPOSER('sofa 150000')}
      await new Promise(r => setTimeout(r, 1200));
      document.getElementById('tmr-add').click();`, wait: 1800 },
  { file: '07-budgets/img/set-limit-desktop.webp', url: '/budget/', before: `
      document.querySelector('[data-bdx-adjust]').click();`, wait: 1400 },
  { file: '05-transactions-recurring/img/subscription-step1-desktop.webp', url: '/recurring/create/', before: SUBSCRIPTION_STEP(1), clipTo: '#subscriptionForm', clipClosest: '.card-body' },
  { file: '05-transactions-recurring/img/subscription-step2-desktop.webp', url: '/recurring/create/', before: SUBSCRIPTION_STEP(2), clipTo: '#subscriptionForm', clipClosest: '.card-body' },
  { file: '05-transactions-recurring/img/subscription-step3-desktop.webp', url: '/recurring/create/', before: SUBSCRIPTION_STEP(3), clipTo: '#subscriptionForm', clipClosest: '.card-body' },
  { file: '14-filters-and-search/img/time-period-menu-desktop.webp', url: '/expenses/', before: `document.getElementById('tmr-time-dropdown-btn').click();` },
  { file: '14-filters-and-search/img/filter-menu-desktop.webp', url: '/expenses/', before: `document.getElementById('tmr-add-filter-btn').click();` },
  { file: '14-filters-and-search/img/page-finder-desktop.webp', url: '/dashboard/', before: `document.getElementById('sidebarSearchTrigger').click();`, wait: 1200 },
  { file: '09-capital-events/img/trend-markers-desktop.webp', url: '/dashboard/?time_period=last_3_months', wait: 2600, clipTo: '#trendChart', clipClosest: '.card' },
  { file: '12-goals/img/goal-detail-desktop.webp', url: '/goals/', before: `
      const link = [...document.querySelectorAll('a')].find(a => /\\/goals\\/[0-9a-f-]{8,}\\/?$/.test(a.getAttribute('href') || ''));
      location.href = link.href;`, wait: 2200 },
  { file: '08-loans/img/loan-detail-desktop.webp', url: '/loans/', before: `
      const link = [...document.querySelectorAll('a')].find(a => /\\/loans\\/[0-9a-f-]{8,}\\/?$/.test(a.getAttribute('href') || ''));
      location.href = link.href;`, wait: 2200 },
  { file: '04-transactions-income/img/add-income-desktop.webp', url: '/income/add/' },
  { file: '04-transactions-income/img/income-list-desktop.webp', url: '/income/list/' },
  { file: '05-transactions-recurring/img/subscriptions-list-desktop.webp', url: '/recurring/' },
  { file: '06-transfers/img/add-transfer-desktop.webp', url: '/transfers/add/' },
  { file: '06-transfers/img/transfers-list-desktop.webp', url: '/transfers/' },
  { file: '07-budgets/img/budgets-desktop.webp', url: '/budget/' },
  { file: '08-loans/img/loans-list-desktop.webp', url: '/loans/' },
  { file: '08-loans/img/add-loan-desktop.webp', url: '/loans/add/', fullPage: true },
  { file: '09-capital-events/img/capital-events-all-desktop.webp', url: '/capital-events/' },
  { file: '09-capital-events/img/add-capital-event-desktop.webp', url: '/capital-events/add/', fullPage: true },
  { file: '10-calendar/img/calendar-desktop.webp', url: '/calendar/' },
  { file: '11-analytics-and-health/img/analytics-desktop.webp', url: '/analytics/', wait: 2200 },
  { file: '11-analytics-and-health/img/cashflow-desktop.webp', url: '/analytics/cashflow/', wait: 2600 },
  { file: '11-analytics-and-health/img/net-worth-desktop.webp', url: '/analytics/net-worth/', wait: 2600 },
  { file: '12-goals/img/goals-list-desktop.webp', url: '/goals/' },
  { file: '12-goals/img/add-goal-desktop.webp', url: '/goals/add/' },
  { file: '13-tmr-flows/img/flows-landing-desktop.webp', url: '/flows/landing/' },
];


// ---------------------------------------------------------------------------------------------------
// TMR Flows: each flow is filled in with the guide's sample values, one step at a time. `steps` lists, per
// wizard step, the image to save and the field values to enter first (select options match by label prefix).
// The last image is the review screen.
// ---------------------------------------------------------------------------------------------------
const HDFC = 'HDFC Bank (Main)';
const flows = [
  { key: 'salary', steps: [
    { file: 'salary-step1', values: { amount: '85000', account: HDFC } },
    { file: 'salary-step2', values: { salary_date: '28', start_date: '2026-10-28' } },
  ], review: 'salary-review' },
  { key: 'rentbill', steps: [
    { file: 'rentbill-step1', values: { description: 'Flat rent', amount: '18000', category: 'Rent', account: HDFC } },
    { file: 'rentbill-step2', values: { frequency: 'Monthly', start_date: '2026-11-01' } },
  ], review: 'rentbill-review' },
  { key: 'insurance', steps: [
    { file: 'insurance-step1', values: { name: 'Term life cover', policy_number: 'TL-48291', sum_assured: '10000000' } },
    { file: 'insurance-step2', values: { premium_amount: '18500', premium_frequency: 'Annual', premium_payment_account: HDFC, start_date: '2026-11-15' } },
  ], review: 'insurance-review' },
  { key: 'sip', steps: [
    { file: 'sip-step1', values: { instrument_type: 'SIP', name: 'Nifty 50 Index Fund', amount: '5000', frequency: 'Monthly', deposit_start_date: '2026-11-05', from_account: HDFC } },
  ], review: 'sip-review' },
  { key: 'fd', steps: [
    { file: 'fd-step1', values: { name: 'HDFC 1-year FD', principal: '500000', annual_rate: '7.1', from_account: HDFC } },
    { file: 'fd-step2', values: { deposit_start_date: '2026-10-10', maturity_date: '2027-10-10', deposit_compounding: 'Quarterly' } },
    { file: 'fd-step3', values: {} },
  ], review: 'fd-review' },
  { key: 'ppfepfnps', steps: [
    { file: 'ppfepfnps-step1', values: { scheme_type: 'PPF', name: 'PPF', annual_amount: '150000', from_account: HDFC } },
    { file: 'ppfepfnps-step2', values: { deposit_principal: '150000', deposit_rate: '7.1', deposit_start_date: '2026-04-01', deposit_compounding: 'Quarterly' } },
  ], review: 'ppfepfnps-review' },
  { key: 'savingsgoal', steps: [
    { file: 'savingsgoal-step1', values: { name: 'Europe trip', target_amount: '300000', current_amount: '40000', target_months: '18' } },
    { file: 'savingsgoal-step2', values: {} },
  ], review: 'savingsgoal-review' },
  { key: 'car', steps: [
    { file: 'car-step1', values: { name: 'Hyundai Creta', purchase_price: '1450000', acquisition_date: '2026-10-05', from_account: HDFC } },
    { file: 'car-step2', values: {} },
  ], review: 'car-review' },
  { key: 'gold', steps: [
    { file: 'gold-step1', values: { route: 'Physical', name: 'Gold coins', amount: '200000', acquisition_date: '2026-10-05', from_account: HDFC } },
  ], review: 'gold-review' },
  { key: 'loan', steps: [
    { file: 'loan-step1-basics', values: { loan_type: 'Home Loan', repayment_type: 'EMI', name: 'Flat purchase', principal: '4000000', annual_rate: '8.5', tenure_months: '240', start_date: '2026-10-01' } },
    { file: 'loan-step2-repayment', values: { payment_account: HDFC } },
    { file: 'loan-step3-adjustments', values: {} },
  ], review: 'loan-step4-review' },
  { key: 'creditcard', steps: [
    { file: 'creditcard-step1', values: { name: 'Axis Magnus', balance: '15000' } },
    { file: 'creditcard-step2', values: { credit_limit: '150000', billing_day: '12' } },
  ], review: 'creditcard-review' },
];

const FILL = (values) => `(() => {
  const fire = (el) => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); };
  const missing = [];
  for (const [name, value] of Object.entries(${JSON.stringify(values)})) {
    const el = document.querySelector('form[id^="tmr-flow-form"] [name="' + name + '"]');
    if (!el) { missing.push(name); continue; }
    if (el.tagName === 'SELECT') {
      const want = String(value).toLowerCase();
      const opt = [...el.options].find((o) => o.text.trim().toLowerCase().startsWith(want));
      if (!opt) { missing.push(name + '=' + value); continue; }
      el.value = opt.value;
    } else if (el.type === 'checkbox') {
      el.checked = !!value;
    } else {
      el.value = value;
    }
    fire(el);
  }
  return missing;
})()`;
const NEXT = `(() => { const b = [...document.querySelectorAll('button')].find((x) => x.offsetParent && /Next/.test(x.textContent)); if (b) b.click(); return !!b; })()`;

// ---------------------------------------------------------------------------------------------------
async function launchChrome() {
  const profile = mkdtempSync(join(tmpdir(), 'tmr-shots-'));
  const proc = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    `--window-size=${PAGE.w},${PAGE.h}`, 'about:blank',
  ], { stdio: 'ignore' });
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (res.ok) return { proc, profile };
    } catch { /* not up yet */ }
    await sleep(200);
  }
  throw new Error('Chrome did not start');
}

async function connect() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' });
  const target = await res.json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { ok, bad } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? bad(new Error(msg.error.message)) : ok(msg.result);
    } else if (msg.method) {
      listeners.forEach((fn) => fn(msg));
    }
  };
  const send = (method, params = {}) => new Promise((ok, bad) => {
    const mid = ++id;
    pending.set(mid, { ok, bad });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  return { send, on: (fn) => listeners.push(fn), close: () => ws.close() };
}

async function main() {
  const { proc, profile } = await launchChrome();
  const page = await connect();
  const { send } = page;
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || 'evaluate failed');
    return r.result.value;
  };
  const goto = async (url) => {
    let loaded;
    const done = new Promise((r) => { loaded = r; });
    page.on((m) => { if (m.method === 'Page.loadEventFired') loaded(); });
    await send('Page.navigate', { url: url.startsWith('http') ? url : BASE + url });
    await Promise.race([done, sleep(15000)]);
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: PAGE.w, height: PAGE.h, deviceScaleFactor: 1, mobile: false });
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });

  // Enter the demo (this reseeds it when it is stale) and hide the banner so shots look like a real account
  await goto('/demo/');
  await sleep(1500);

  const todo = shots.filter((s) => s.file.includes(FILTER));
  let failed = 0;
  for (const shot of todo) {
    try {
      await goto(shot.url);
      await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
      await evaluate(CLEAN);
      await sleep(shot.wait || 1200);
      if (shot.before) { await evaluate(`(async () => { ${shot.before} })()`); await sleep(shot.wait || 900); await evaluate(CLEAN); await sleep(300); }
      const params = { format: 'webp', quality: 82, clip: { x: 0, y: 0, width: PAGE.w, height: PAGE.h, scale: SCALE } };
      if (shot.clipTo) {
        const box = await evaluate(`(() => { let el = document.querySelector(${JSON.stringify(shot.clipTo)});
          ${shot.clipClosest ? `el = el.closest(${JSON.stringify(shot.clipClosest)}) || el;` : ''}
          el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect();
          return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height }; })()`);
        params.captureBeyondViewport = true;
        params.clip = { x: box.x, y: box.y, width: box.w, height: box.h, scale: SCALE };
      }
      if (shot.fullPage) {
        const size = await evaluate('document.documentElement.scrollHeight');
        params.captureBeyondViewport = true;
        params.clip.height = Math.min(size, 1800);
      }
      const { data } = await send('Page.captureScreenshot', params);
      const out = join(ROOT, 'guide-src', shot.file);
      mkdirSync(dirname(out), { recursive: true });
      writeFileSync(out, Buffer.from(data, 'base64'));
      console.log('saved', shot.file);
    } catch (err) {
      failed++;
      console.error('FAILED', shot.file, err.message);
    }
  }
  const cardBox = async () => evaluate(`(() => { const el = document.querySelector('.card.shadow-lg .card-body'); el.scrollIntoView({ block: 'start' });
      const r = el.getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height }; })()`);
  const save = async (name) => {
    const box = await cardBox();
    const { data } = await send('Page.captureScreenshot', { format: 'webp', quality: 82, captureBeyondViewport: true,
      clip: { x: box.x, y: box.y, width: box.w, height: box.h, scale: SCALE } });
    const out = join(ROOT, 'guide-src', '13-tmr-flows', 'img', `${name}-desktop.webp`);
    writeFileSync(out, Buffer.from(data, 'base64'));
    console.log('saved', `13-tmr-flows/img/${name}-desktop.webp`);
  };
  for (const flow of flows.filter((f) => `13-tmr-flows/img/${f.review}`.includes(FILTER) || f.steps.some((st) => st.file.includes(FILTER)))) {
    try {
      await goto(`/flows/${flow.key}/`);
      await evaluate(CLEAN);
      await sleep(1200);
      for (const step of flow.steps) {
        const missing = await evaluate(FILL(step.values));
        if (missing.length) console.warn('  not found:', flow.key, missing.join(', '));
        await sleep(500);
        await save(step.file);
        await evaluate(NEXT);
        await sleep(700);
      }
      await sleep(2500);
      await save(flow.review);
    } catch (err) {
      failed++;
      console.error('FAILED flow', flow.key, err.message);
    }
  }
  page.close();
  proc.kill();
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
  if (failed) process.exitCode = 1;
}

main().catch((e) => { console.error(e); process.exit(1); });
