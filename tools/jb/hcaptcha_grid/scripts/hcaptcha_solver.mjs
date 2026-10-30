#!/usr/bin/env node
// hCaptcha grid solver — Camoufox (anti-detect browser) + wreq-js (transport) + VLM (vision)
//
// Kredensial TIDAK di-hardcode. Semua lewat environment:
//   VL_URL      OpenAI-compatible chat/completions endpoint   (wajib)
//   VL_KEY      Bearer token untuk endpoint di atas           (opsional)
//   VL_MODEL    model id vision                              (wajib)
//   PROXY_LIST  file host:port:user:pass per baris           (opsional)
//   PROXY       satu proxy inline, override PROXY_LIST       (opsional)
//   MAX_ROUNDS  jumlah ronde maksimum, default 8
//
// Exit 0 = token P1_ didapat.  Exit 1 = gagal.

import { NewBrowser } from 'camoufox-js';
import { firefox } from 'playwright-core';
import { createSession } from 'wreq-js';
import fs from 'fs';

// ---------- CONFIG ----------
const TARGET_URL = process.env.TARGET_URL || 'https://nopecha.com/demo/hcaptcha';
const SITEKEY_FALLBACK = process.env.SITEKEY || '';
const MAX_ROUNDS = Number(process.env.MAX_ROUNDS || 8);

const VL_URL = process.env.VL_URL || '';
const VL_KEY = process.env.VL_KEY || '';
const VL_MODEL = process.env.VL_MODEL || '';
const PROXY_LIST = process.env.PROXY_LIST || '';
const TOKEN_OUT = process.env.TOKEN_OUT || './token.txt';
const TOKEN_LOG = process.env.TOKEN_LOG || './tokens.log';

if (!VL_URL || !VL_MODEL) {
  console.error('[fatal] VL_URL dan VL_MODEL wajib diisi.');
  process.exit(2);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const T0 = Date.now();
const log = (...a) => console.log(`[${((Date.now() - T0) / 1000).toFixed(1)}s]`, ...a);

// ---------- proxy pool ----------
function loadPool() {
  if (!PROXY_LIST || !fs.existsSync(PROXY_LIST)) return [];
  return fs.readFileSync(PROXY_LIST, 'utf8').split('\n').map((s) => s.trim()).filter(Boolean);
}
const pool = loadPool();
let pIdx = 0;
function nextProxy() {
  if (process.env.PROXY) {
    const [h, p, u, w] = process.env.PROXY.split(':');
    return { server: `http://${h}:${p}`, username: u, password: w };
  }
  if (!pool.length) return null;
  const [h, p, u, w] = pool[pIdx++ % pool.length].split(':');
  return { server: `http://${h}:${p}`, username: u, password: w };
}
const proxyUrl = (p) => (p ? `http://${p.username}:${p.password}@${p.server.replace('http://', '')}` : null);

// ---------- VLM: satu call per grid ----------
async function callVlm(messages, maxTokens = 40, attempt = 1) {
  try {
    const res = await fetch(VL_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(VL_KEY ? { authorization: `Bearer ${VL_KEY}` } : {}) },
      body: JSON.stringify({ model: VL_MODEL, messages, max_tokens: maxTokens, temperature: 0 }),
    });
    if (!res.ok) {
      if ((res.status >= 500 || res.status === 429) && attempt < 4) {
        await sleep(700 * attempt);
        return callVlm(messages, maxTokens, attempt + 1);
      }
      return { err: `HTTP ${res.status}` };
    }
    const text = await res.text();
    // endpoint bisa balas SSE atau JSON biasa — tangani keduanya
    let content = '';
    for (const line of text.split('\n')) {
      const s = line.trim();
      if (!s.startsWith('data:')) continue;
      const payload = s.slice(5).trim();
      if (payload === '[DONE]') continue;
      try { content += JSON.parse(payload).choices?.[0]?.message?.content || ''; } catch {}
    }
    if (!content) {
      try { content = JSON.parse(text).choices?.[0]?.message?.content || ''; } catch {}
    }
    return { content };
  } catch (e) {
    if (attempt < 4) { await sleep(700 * attempt); return callVlm(messages, maxTokens, attempt + 1); }
    return { err: e.message };
  }
}

async function waitTilesReady(frame, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await frame.evaluate(() => {
      const els = [...document.querySelectorAll('.task[role="button"]')];
      let ok = 0;
      for (const el of els) {
        const img = el.querySelector('img');
        const hasImg = img && img.complete && img.naturalWidth > 20;
        let hasBg = false;
        for (const n of el.querySelectorAll('div, span')) {
          const bg = getComputedStyle(n).backgroundImage;
          if (bg && bg !== 'none' && /url\(["']?https?:/.test(bg)) { hasBg = true; break; }
        }
        const canvas = el.querySelector('canvas');
        const hasCanvas = canvas && canvas.width > 20;
        const li = el.querySelector('.loading-indicator');
        const loadingVisible = li && getComputedStyle(li).opacity !== '0';
        if ((hasImg || hasBg || hasCanvas) && !loadingVisible) ok++;
      }
      return { ok, total: els.length };
    }).catch(() => ({ ok: 0, total: 0 }));
    if (r.total > 0 && r.ok >= r.total) { await sleep(700); return true; }
    await sleep(400);
  }
  log('  [warn] tile belum semua ready, lanjut screenshot');
  return false;
}

async function solveGrid(frame, prompt) {
  const tiles = await frame.$$('.task[role="button"]');
  const n = tiles.length;
  if (!n) return [];

  await waitTilesReady(frame, n);

  let shotPath = '/tmp/hc_grid.png';
  const container = await frame.$('.challenge-container, .task-grid, #task-grid');
  if (container) await container.screenshot({ path: shotPath }).catch(() => {});
  else await frame.page().screenshot({ path: shotPath }).catch(() => {});
  if (!fs.existsSync(shotPath)) return [];

  const cols = Math.round(Math.sqrt(n)) || 3;
  const b64 = fs.readFileSync(shotPath).toString('base64');
  const instruction =
    `CAPTCHA grid, ${n} tiles in a ${cols}-column layout, numbered LEFT-TO-RIGHT then TOP-TO-BOTTOM from index 0.\n` +
    `Task: "${prompt}"\n` +
    `If a reference/sample image is present, it is NOT a selectable tile - match tiles to its category.\n` +
    `Return ONLY a JSON array of matching tile indexes, e.g. [0,3,7]. ` +
    `Output must start with "[" and end with "]". No words, no explanation.`;

  const { content, err } = await callVlm([{
    role: 'user',
    content: [
      { type: 'text', text: instruction },
      { type: 'image_url', image_url: { url: `data:image/png;base64,${b64}` } },
    ],
  }]);
  if (err) { log('    [vlm] ERR', err); return []; }

  let picks = [];
  const m = content.match(/\[[\s\d,\s]*\]/);
  if (m) { try { picks = JSON.parse(m[0]); } catch { picks = []; } }
  else picks = (content.match(/\b\d{1,2}\b/g) || []).map(Number);

  picks = [...new Set(picks)].filter((i) => Number.isInteger(i) && i >= 0 && i < n);
  // model memilih semua tile = salah paham
  if (picks.length >= n) {
    log(`    [vlm] pilih semua ${n} tile → ditolak`);
    picks = picks.slice(0, 3);
  }
  log(`    [vlm] "${content.trim().slice(0, 45)}" → [${picks.join(',')}]`);
  return picks;
}

// ---------- main ----------
let PROXY = nextProxy();
const session = await createSession({
  browser: 'chrome_149', os: 'windows',
  ...(PROXY ? { proxy: { url: proxyUrl(PROXY), type: 'http' } } : {}),
});
log('[proxy]', PROXY?.server || '(none)');

// recon: ambil sitekey kalau tidak diberikan
let sitekey = SITEKEY_FALLBACK;
if (!sitekey) {
  try {
    const html = await (await session.fetch(TARGET_URL)).text();
    const m = html.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
    if (m) sitekey = m[1];
  } catch {}
  if (!sitekey) {
    const js = (await (session.fetch(TARGET_URL.replace(/\/[^/]*$/, '/js/demo/hcaptcha.js')).text().catch(() => '')) || '');
    const m = js.match(/'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'/i);
    if (m) sitekey = m[1];
  }
}
log('[recon] sitekey =', sitekey || '(tidak ditemukan)');

const browser = await NewBrowser(firefox, true, {}, false, false, {
  headless: true,
  os: 'windows',
  locale: 'en-US',
  humanize: true,
  screen: { width: 1920, height: 1080 },
  firefox_user_prefs: {
    'dom.webdriver.enabled': false,
    'privacy.resistFingerprinting': false,
    // WAJIB: hCaptcha render tile ke canvas. Tanpa ini tile kosong & klik hang.
    'webgl.force-enabled': true,
    'webgl.disabled': false,
    'webgl.enable-webgl2': true,
    'gfx.webrender.software': true,
    'layers.acceleration.disabled': true,
    'gfx.canvas.accelerated': false,
  },
  args: ['-allow-software-gl', '--use-gl=swiftshader'],
});

let token = null;

for (let round = 1; round <= MAX_ROUNDS && !token; round++) {
  PROXY = nextProxy();
  log(`[round ${round}] proxy=${PROXY?.server || '(none)'}`);

  let ctx;
  try {
    ctx = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      locale: 'en-US',
      ...(PROXY ? { proxy: PROXY } : {}),
    });
  } catch (e) {
    log('  browser mati, stop:', String(e).slice(0, 80));
    break;
  }

  const page = await ctx.newPage();
  page.setDefaultTimeout(20000);

  const tokenNow = () => page.evaluate(() => {
    const el = document.querySelector('textarea[name="h-captcha-response"], textarea[id^="h-captcha-response"]');
    return el?.value && el.value.length > 20 ? el.value : null;
  }).catch(() => null);

  try {
    await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
    const ok = await page.waitForSelector('iframe[src*="hcaptcha"]', { timeout: 18000 }).then(() => true).catch(() => false);
    if (!ok) { log('  widget tidak render'); await ctx.close().catch(() => {}); continue; }
    await sleep(2000);

    // klik checkbox "I am human"
    const cbF = page.frames().find((f) => /frame=checkbox/.test(f.url()));
    const cb = cbF && await cbF.$('#checkbox').catch(() => null);
    if (cb) { await cb.click({ delay: 60 }).catch(() => {}); await sleep(2800); }

    const chF = page.frames().find((f) => /frame=challenge/.test(f.url()));
    if (!chF) { token = await tokenNow(); await ctx.close().catch(() => {}); continue; }

    // NB: .display-error selalu ada di DOM dengan opacity:0 → cek style, bukan innerText
    const st = await chF.evaluate(() => {
      const p = document.querySelector('.prompt-text, .challenge-text, .prompt');
      const box = document.querySelector('.display-error');
      const vis = box && getComputedStyle(box).opacity !== '0' && getComputedStyle(box).display !== 'none';
      return {
        prompt: (p?.textContent || '').trim(),
        err: vis ? (box.querySelector('.error-text')?.textContent || '').trim() : '',
        tiles: document.querySelectorAll('.task[role="button"]').length,
      };
    }).catch(() => null);
    if (!st || !st.tiles) { token = await tokenNow(); await ctx.close().catch(() => {}); continue; }
    log(`  "${st.prompt}" tiles=${st.tiles} err="${st.err}"`);

    const picks = await solveGrid(chF, st.prompt);
    // NB: boundingBox() di iframe relatif frame → klik lewat handle, bukan page.mouse
    const handles = await chF.$$('.task[role="button"]');
    for (const i of picks) {
      const h = handles[i];
      if (!h) continue;
      await h.hover().catch(() => {});
      await sleep(45);
      await h.click({ delay: 35 }).catch(() => {});
    }
    await sleep(200);
    const chk = await chF.evaluate(() => document.querySelectorAll('.task[aria-pressed="true"]').length).catch(() => 0);
    log(`  pick=[${picks.join(',')}] terpilih=${chk}`);

    const verify = await chF.$('#verify, .button-submit').catch(() => null);
    if (verify) { await verify.click({ delay: 50 }).catch(() => {}); log('  → verify'); }

    // challenge bisa minta 2+ page dalam frame yang sama
    for (let pg = 0; pg < 4 && !token; pg++) {
      await sleep(3000);
      token = await tokenNow();
      if (token) break;
      const sub = await chF.evaluate(() => {
        const p = document.querySelector('.prompt-text, .challenge-text, .prompt');
        return {
          prompt: (p?.textContent || '').trim(),
          tiles: document.querySelectorAll('.task[role="button"]').length,
          checked: document.querySelectorAll('.task[aria-pressed="true"]').length,
        };
      }).catch(() => null);
      if (!sub || !sub.tiles || sub.checked > 0) continue;
      log(`    [page ${pg + 2}] "${sub.prompt}"`);
      const p2 = await solveGrid(chF, sub.prompt);
      const hs = await chF.$$('.task[role="button"]');
      for (const i of p2) await hs[i]?.click({ delay: 35 }).catch(() => {});
      await sleep(200);
      const v2 = await chF.$('#verify, .button-submit').catch(() => null);
      if (v2) { await v2.click({ delay: 50 }).catch(() => {}); log(`    → verify page ${pg + 2}`); }
    }
  } catch (e) {
    log('  [error]', String(e).slice(0, 120));
  } finally {
    if (!token) await ctx.close().catch(() => {});
  }
}

// ---------- hasil ----------
if (token) {
  fs.writeFileSync(TOKEN_OUT, token);
  fs.appendFileSync(TOKEN_LOG, `${new Date().toISOString()} ${token}\n`);
  log('TOKEN:', token.slice(0, 40) + '...');
} else {
  log('TOKEN: (tidak dapat)');
}

await browser.close().catch(() => {});
process.exit(token ? 0 : 1);