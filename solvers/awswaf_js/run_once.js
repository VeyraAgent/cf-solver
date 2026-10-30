#!/usr/bin/env node
/**
 * run_once.js — one-shot CLI bridge for the AWS WAF solver core.
 *
 *   node run_once.js '<targetUrl>' ['<proxy url>'] ['<challenge base url>'] ['<user-agent>']
 *
 * Prints exactly one JSON object on stdout:
 *   {"ok": true,  "token": "...", "elapsed": "0.68"}
 *   {"ok": false, "error": "..."}
 * The python side (solvers/awswaf_js/solve.py) parses that line — nothing else
 * is written to stdout, so any stray log from the core cannot corrupt it.
 */
const { WafSolver } = require('./waf_core.js');

const [, , targetUrl, proxyArg, chalArg, uaArg] = process.argv;

function out(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

(async () => {
  if (!targetUrl || !/^https?:\/\//i.test(targetUrl)) {
    return out({ ok: false, error: 'targetUrl must start with http:// or https://' });
  }
  const proxy = proxyArg && proxyArg.trim() ? proxyArg.trim() : null;
  const chal = chalArg && chalArg.trim() ? chalArg.trim() : null;
  const ua = uaArg && uaArg.trim() ? uaArg.trim() : WafSolver.UA;
  try {
    const raw = await WafSolver.solve(targetUrl, ua, () => {}, proxy, chal);
    const idx = raw.indexOf(':');
    out({ ok: true, elapsed: raw.slice(0, idx), token: raw.slice(idx + 1) });
  } catch (e) {
    out({ ok: false, error: (e && e.message) || String(e) });
  }
  process.exit(0);
})();
