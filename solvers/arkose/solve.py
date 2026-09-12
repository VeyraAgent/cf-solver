"""Arkose FunCaptcha solver — browser-driven challenge + ONNX prediction.

Flow: navigate to page_url → trigger Arkose → intercept gfct (context-level) →
download challenge image → predict via ONNX → encrypt answer (CryptoJS AES-CBC)
→ submit to /fc/ca/ → return token.

Answer submission format:
  POST /fc/ca/ with form-urlencoded:
    session_token, game_token, sid, guess, render_type, analytics_tier,
    bio, is_compatibility_mode, ecdata

  guess = CryptoJS.AES.encrypt(JSON.stringify([{index: N}]), session_token)
  bio = base64 encoded motion data (mouse movements)
  ecdata = base64('{"height": 450, "width": 400}')
"""
import asyncio
import base64
import json
import logging
import os
import random
import tempfile
import time
import urllib.parse
from pathlib import Path

from cloakbrowser import launch_async

from solvers.arkose.predict import predict as onnx_predict, cryptojs_encrypt

log = logging.getLogger("arkose")

_ECDATA = base64.b64encode(b'{"height": 450, "width": 400}').decode()


def _generate_bio() -> str:
    """Generate minimal mouse motion bio data (base64 encoded)."""
    motion_parts = []
    ts = random.randint(5, 100)
    # Initial pause
    ts += random.randint(120, 420)
    # Generate a few mouse move points
    x, y = random.randint(100, 300), random.randint(100, 350)
    motion_parts.append(f"{ts},0,{x},{y};")
    for _ in range(random.randint(8, 15)):
        ts += random.randint(6, 55)
        x += random.randint(-20, 20)
        y += random.randint(-20, 20)
        x = max(20, min(380, x))
        y = max(36, min(414, y))
        motion_parts.append(f"{ts},0,{x},{y};")
    # Final pause + click
    ts += random.randint(30, 110)
    motion_parts.append(f"{ts},0,{x},{y};")
    ts += random.randint(60, 180)
    motion_parts.append(f"{ts},1,{x},{y};")
    ts += random.randint(60, 180)
    motion_parts.append(f"{ts},2,{x},{y};")

    motion_str = "".join(motion_parts)
    bio_json = json.dumps({"mbio": motion_str, "tbio": "", "kbio": ""})
    return base64.b64encode(bio_json.encode()).decode()


def _extract_challenge(gfct: dict) -> dict:
    game_data = gfct.get("game_data", {})
    custom_gui = game_data.get("customGUI", {})
    return {
        "instruction": game_data.get("instruction_string", ""),
        "challenge_imgs": custom_gui.get("_challenge_imgs", []),
        "session_token": gfct.get("session_token", ""),
        "challenge_id": gfct.get("challengeID", ""),
        "sec": gfct.get("sec", ""),
        "sid": gfct.get("sid", ""),
        "waves": game_data.get("waves", 1),
        "game_type": game_data.get("gameType", 4),
    }


async def solve_arkose(public_key: str, page_url: str | None = None,
                       game_type: str = "4", proxy: str | None = None,
                       timeout_s: int = 120, max_attempts: int = 10,
                       pre_actions: list | None = None) -> dict:
    if not public_key:
        return {"solved": False, "error": "public_key is required"}
    if not page_url:
        return {"solved": False, "error": "page_url is required"}

    # Models are NOT in this git repo (~1.4GB optional HF download).
    # Fail fast — do not burn browser time if models/ is empty.
    models_dir = Path(__file__).parent / "models"
    if not models_dir.is_dir() or not any(models_dir.glob("*.onnx")):
        return {
            "solved": False,
            "error": (
                "arkose models missing: place ONNX files in arkose/models/ "
                "(gitignored ~1.4GB; see arkose/README.md). Not shipped by the repo."
            ),
            "elapsed": 0.0,
        }

    t_start = time.monotonic()

    # Use shared browser kwargs so Modal Xvfb/BROWSER_HEADLESS applies
    from solvers.common.browser import browser_kwargs
    kw = browser_kwargs("TURNSTILE", proxy=proxy)

    browser = await launch_async(**kw)
    try:
        ctx = await browser.new_context()
        page = await ctx.new_page()

        gfct_data: dict = {}
        ca_result: dict = {}
        arkose_domain: str = ""
        arkose_token_js: str = ""
        net_hits: list[str] = []

        async def _capture_gfct(url: str, txt: str):
            nonlocal arkose_domain
            try:
                data = json.loads(txt)
            except Exception:
                return
            if not isinstance(data, dict):
                return
            # gfct payloads carry session_token + game_data
            if not (data.get("session_token") or data.get("challengeID") or data.get("game_data")):
                return
            from urllib.parse import urlparse
            arkose_domain = urlparse(url).netloc or arkose_domain
            gfct_data.clear()
            gfct_data.update(data)
            log.info("arkose: captured gfct domain=%s keys=%s", arkose_domain, list(data.keys())[:8])

        async def on_response(resp):
            nonlocal arkose_domain
            url = resp.url
            try:
                low = url.lower()
                if any(x in low for x in ("/fc/gfct", "gfct", "/fc/gt2", "arkoselabs", "funcaptcha")):
                    if len(net_hits) < 40:
                        net_hits.append(f"{resp.status} {url[:160]}")
                if "/fc/gfct" in low or low.rstrip("/").endswith("gfct") or "/fc/gt2/" in low:
                    try:
                        txt = await resp.text()
                    except Exception:
                        return
                    await _capture_gfct(url, txt)
                elif "/fc/ca/" in low:
                    try:
                        txt = await resp.text()
                        ca_result.clear()
                        ca_result.update(json.loads(txt))
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", on_response)
        ctx.on("response", on_response)

        log.info("arkose: navigating to %s", page_url)
        # Prefer REAL page first — domain-locked keys reject stub origins / incomplete site data.
        # Stub fallback only if real nav dies.
        # Note: external api.js from Arkose CDN — no SRI (third-party, versioned by key).
        stub = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>arkose-local</title>
<script>
window.__arkose_token='';
window.__arkose_err='';
window.__arkose_ready=false;
window.__arkose_shown=false;
window.setupEnforcement=function(myEnforcement){{
  try{{
    myEnforcement.setConfig({{
      publicKey: '{public_key}',
      language: 'en',
      selector: '#arkose-host',
      onCompleted: function(r){{
        try{{ window.__arkose_token=(r&&r.token)||''; }}catch(e){{}}
      }},
      onSuppress: function(r){{
        try{{ window.__arkose_token=(r&&r.token)||window.__arkose_token||''; }}catch(e){{}}
      }},
      onReady: function(){{
        window.__arkose_ready=true;
        try{{ myEnforcement.run(); }}catch(e){{ window.__arkose_err=String(e); }}
      }},
      onShown: function(){{ window.__arkose_shown=true; }},
      onError: function(r){{
        try{{ window.__arkose_err=JSON.stringify(r||{{}}); }}catch(e){{ window.__arkose_err=String(r); }}
      }},
      onFailed: function(r){{
        try{{ window.__arkose_err='failed:'+JSON.stringify(r||{{}}); }}catch(e){{}}
      }}
    }});
  }}catch(e){{ window.__arkose_err=String(e); }}
}};
</script>
<script src="https://client-api.arkoselabs.com/v2/{public_key}/api.js"
  data-callback="setupEnforcement" async defer
  onerror="window.__arkose_err='api.js load failed'"></script>
</head><body style="margin:0;background:#f5f5f5">
<button id="arkose-host" type="button" style="margin:40px;padding:16px 28px;font-size:18px">
  Start Challenge
</button>
</body></html>"""

        from urllib.parse import urlsplit
        parts = urlsplit(page_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        mode = "real"
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=35000)
            # If site already embeds Arkose, just wait. Else inject enforcement on real origin.
            has_ark = await page.evaluate(
                """() => !!(window.arkoseLabsClientApi || document.querySelector(
                  'script[src*="arkoselabs"],script[src*="funcaptcha"]'))"""
            )
            if not has_ark:
                await page.evaluate(
                    """(pk) => {
                      if (window.__arkose_injected) return;
                      window.__arkose_injected = true;
                      window.__arkose_token = '';
                      window.__arkose_err = '';
                      window.__arkose_ready = false;
                      window.setupEnforcement = function (myEnforcement) {
                        try {
                          myEnforcement.setConfig({
                            publicKey: pk,
                            language: 'en',
                            selector: '#arkose-host-inject',
                            onCompleted: function (r) {
                              window.__arkose_token = (r && r.token) || '';
                            },
                            onSuppress: function (r) {
                              window.__arkose_token = (r && r.token) || window.__arkose_token || '';
                            },
                            onReady: function () {
                              window.__arkose_ready = true;
                              try { myEnforcement.run(); } catch (e) { window.__arkose_err = String(e); }
                            },
                            onError: function (r) {
                              try { window.__arkose_err = JSON.stringify(r || {}); } catch (e) {}
                            }
                          });
                        } catch (e) { window.__arkose_err = String(e); }
                      };
                      var host = document.getElementById('arkose-host-inject');
                      if (!host) {
                        host = document.createElement('button');
                        host.id = 'arkose-host-inject';
                        host.textContent = 'Start Challenge';
                        host.style.cssText = 'position:fixed;top:12px;left:12px;z-index:2147483646;padding:12px';
                        document.body.appendChild(host);
                      }
                      var s = document.createElement('script');
                      s.src = 'https://client-api.arkoselabs.com/v2/' + pk + '/api.js';
                      s.setAttribute('data-callback', 'setupEnforcement');
                      s.async = true;
                      document.head.appendChild(s);
                    }""",
                    public_key,
                )
                mode = "real+inject"
        except Exception as e:
            log.warning("arkose real nav failed (%s); stub", e)
            mode = "stub"
            try:
                async def _origin_doc(route):
                    try:
                        if route.request.resource_type == "document":
                            await route.fulfill(
                                body=stub, status=200,
                                content_type="text/html; charset=utf-8")
                        else:
                            await route.continue_()
                    except Exception:
                        try:
                            await route.continue_()
                        except Exception:
                            pass

                await page.route(f"{origin}/**", _origin_doc)
                await page.goto(origin + "/", wait_until="domcontentloaded", timeout=30000)
            except Exception as e2:
                return {"solved": False, "error": f"navigation failed: {e2}",
                        "elapsed": round(time.monotonic() - t_start, 1)}

        log.info("arkose: mode=%s", mode)

        if pre_actions:
            from solvers.common.browser import run_pre_actions
            try:
                await run_pre_actions(page, pre_actions)
            except Exception as e:
                log.warning("arkose: pre_actions error: %s", e)

        log.info("arkose: waiting for gfct / onCompleted...")
        wait_n = max(15, min(int(timeout_s or 90) - 35, 50))
        last_err = ""
        last_ready = False
        for i in range(wait_n):
            if gfct_data or arkose_token_js:
                break
            if time.monotonic() - t_start > max(20, int(timeout_s or 90) - 30):
                break
            try:
                st = await asyncio.wait_for(
                    page.evaluate(
                        """() => ({
                          token: window.__arkose_token || '',
                          err: window.__arkose_err || '',
                          ready: !!window.__arkose_ready,
                          shown: !!window.__arkose_shown,
                        })"""
                    ),
                    timeout=3.0,
                )
                last_ready = bool(st.get("ready"))
                if st.get("token") and len(st["token"]) > 40:
                    arkose_token_js = st["token"]
                    break
                if st.get("err"):
                    last_err = str(st["err"])[:300]
                    # gt2 400 / API_REQUEST_ERROR usually won't recover without site data/proxy
                    if "API_REQUEST_ERROR" in last_err or '"status":400' in last_err:
                        if i > 10:
                            break
            except Exception:
                pass
            if i in (1, 4, 8, 15):
                for sel in ("#arkose-host", "#arkose-host-inject", "button",
                            "[data-callback]", "iframe"):
                    try:
                        await page.click(sel, timeout=800)
                        break
                    except Exception:
                        pass
            await asyncio.sleep(1)

        if arkose_token_js and not gfct_data:
            return {
                "solved": True,
                "token": arkose_token_js,
                "method": "enforcement-onCompleted",
                "elapsed": round(time.monotonic() - t_start, 1),
            }

        if not gfct_data:
            try:
                frames = [f.url[:120] for f in page.frames][:12]
            except Exception:
                frames = []
            log.info("arkose: fail diag err=%r ready=%s hits=%d mode=%s",
                     last_err, last_ready, len(net_hits), mode)
            err = "no gfct response"
            if last_err and "API_REQUEST_ERROR" in last_err:
                err = ("arkose gt2 rejected public_key (HTTP 400) — key is domain/site-data "
                       "locked or Modal/datacenter IP blocked; need real site embed + residential proxy")
            return {
                "solved": False,
                "error": err,
                "arkose_err": last_err or None,
                "ready": last_ready,
                "mode": mode,
                "net_hits": net_hits[:15],
                "frames": frames,
                "elapsed": round(time.monotonic() - t_start, 1),
            }

        guesses = []  # Accumulated guesses per wave

        for wave in range(max_attempts):
            if time.monotonic() - t_start > timeout_s:
                break

            info = _extract_challenge(gfct_data)
            instruction = info["instruction"]
            challenge_imgs = info["challenge_imgs"]
            session_token = info["session_token"]
            challenge_id = info["challenge_id"]
            sid = info["sid"]

            log.info("arkose wave %d: %s imgs=%d sid=%s",
                     wave, instruction, len(challenge_imgs), sid)

            if not challenge_imgs:
                break

            img_path = None
            try:
                # Download challenge image
                img_bytes = await page.evaluate(
                    """async (url) => {
                        const r = await fetch(url);
                        return Array.from(new Uint8Array(await r.arrayBuffer()));
                    }""", challenge_imgs[0])

                if len(img_bytes) < 100:
                    continue

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp.write(bytes(img_bytes))
                    img_path = tmp.name

                from PIL import Image
                img = Image.open(img_path)
                answer = onnx_predict(img, instruction)
                if answer is None:
                    log.warning("arkose wave %d: prediction failed", wave)
                    continue

                log.info("arkose wave %d: answer=%d", wave, answer)

                # Find an Arkose-origin frame for same-origin fetch
                game_frame = None
                for frame in page.frames:
                    if "game-core" in (frame.url or ""):
                        game_frame = frame
                        break
                if not game_frame:
                    for frame in page.frames:
                        if "arkoselabs" in (frame.url or ""):
                            game_frame = frame
                            break
                if not game_frame:
                    log.warning("arkose wave %d: no Arkose frame", wave)
                    continue

                # Send required action updates to /fc/a/ before first answer
                # (Arkose SDK sends these to register session state)
                a_url = f"https://{arkose_domain}/fc/a/"
                game_type_str = str(info.get("game_type", 4))
                if game_type_str == "0":
                    game_type_str = "4"

                if wave == 0:
                    enforcement_url = gfct_data.get("challengeURL", "")
                    base_payload = urllib.parse.urlencode({
                        "sid": sid, "session_token": session_token,
                        "analytics_tier": "15", "disableCookies": "false",
                        "render_type": "canvas", "is_compatibility_mode": "false",
                    })
                    a1 = base_payload + "&category=Site+URL&action=" + urllib.parse.quote(enforcement_url)
                    a2 = base_payload + f"&game_token={urllib.parse.quote(challenge_id)}&game_type={game_type_str}&category=loaded&action=game+loaded"
                    a3 = base_payload + f"&game_token={urllib.parse.quote(challenge_id)}&game_type={game_type_str}&category=begin+app&action=user+clicked+verify"
                    for label, body_str in [("Site URL", a1), ("game loaded", a2), ("user clicked verify", a3)]:
                        await game_frame.evaluate(
                            """async ({url, body}) => {
                                await fetch(url, {method:'POST',
                                    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
                                    body:body, credentials:'include'});
                            }""", {"url": a_url, "body": body_str})
                    log.info("arkose: sent3 action updates to /fc/a/")

                # Build guess array (accumulating)
                guesses.append({"index": answer})
                guess_json = json.dumps(guesses, separators=(",", ":"))

                bio = _generate_bio()
                analytics_tier = "15"

                ca_url = f"https://{arkose_domain}/fc/ca/"

                # Encrypt guess in Python (CryptoJS-compatible AES-CBC)
                encrypted_guess = cryptojs_encrypt(guess_json, session_token)

                # Submit pre-encrypted guess from Arkose-origin frame
                result = await game_frame.evaluate(
                    """async ({guess, sessionToken, gameToken, sid,
                              gameType, analyticsTier, bio, ecdata, caUrl}) => {
                        const params = new URLSearchParams();
                        params.set('session_token', sessionToken);
                        params.set('game_token', gameToken);
                        params.set('sid', sid);
                        params.set('guess', guess);
                        params.set('render_type', 'canvas');
                        params.set('analytics_tier', analyticsTier);
                        params.set('bio', bio);
                        params.set('is_compatibility_mode', 'false');
                        params.set('ecdata', ecdata);

                        const r = await fetch(caUrl, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                'X-Requested-With': 'XMLHttpRequest'
                            },
                            body: params.toString(),
                            credentials: 'include'
                        });
                        return {status: r.status, response: await r.text()};
                    }""", {
                        "guess": encrypted_guess,
                        "sessionToken": session_token,
                        "gameToken": challenge_id,
                        "sid": sid,
                        "gameType": game_type_str,
                        "analyticsTier": analytics_tier,
                        "bio": bio,
                        "ecdata": _ECDATA,
                        "caUrl": ca_url,
                    })

                log.info("arkose wave %d: ca result: %s", wave, str(result)[:300])

                if result.get("error"):
                    log.warning("arkose wave %d: error: %s", wave, result["error"])
                    guesses.pop()
                    continue

                try:
                    resp_data = json.loads(result.get("response", "{}"))
                except (json.JSONDecodeError, TypeError):
                    resp_data = {}

                # Check for solved token
                solved = resp_data.get("solved", False)
                solved_token = resp_data.get("token", "")
                response_str = resp_data.get("response", "")

                if solved and solved_token:
                    elapsed = round(time.monotonic() - t_start, 1)
                    log.info("arkose: SOLVED in %.1fs (wave %d)", elapsed, wave + 1)
                    return {
                        "solved": True,
                        "token": solved_token,
                        "method": "onnx-predict",
                        "waves": wave + 1,
                        "elapsed": elapsed,
                    }

                if response_str == "answered":
                    # Answered but may need more waves or got token
                    if solved_token:
                        elapsed = round(time.monotonic() - t_start, 1)
                        return {
                            "solved": True,
                            "token": solved_token,
                            "method": "onnx-predict",
                            "waves": wave + 1,
                            "elapsed": elapsed,
                        }

                if response_str == "not answered":
                    # Correct answer, next wave
                    # Next challenge image is in the ca response, NOT a new gfct
                    log.info("arkose wave %d: correct, next wave", wave)
                    next_imgs = resp_data.get("_challenge_imgs", [])
                    next_instruction = resp_data.get("instruction_string", instruction)
                    if next_imgs:
                        # Update gfct_data with next challenge info
                        gfct_data.clear()
                        gfct_data.update({
                            "session_token": session_token,
                            "challengeID": challenge_id,
                            "sid": sid,
                            "game_data": {
                                "customGUI": {"_challenge_imgs": next_imgs},
                                "instruction_string": next_instruction,
                                "gameType": info.get("game_type", 4),
                            }
                        })
                        log.info("arkose wave %d: next challenge imgs=%d instruction=%s",
                                 wave, len(next_imgs), next_instruction)
                        continue
                    # Fallback: wait for new gfct
                    gfct_data.clear()
                    ca_result.clear()
                    for _ in range(30):
                        if gfct_data:
                            break
                        await asyncio.sleep(1)
                    if gfct_data:
                        continue
                    log.warning("arkose: no next challenge after correct answer")
                    break

                log.info("arkose wave %d: unexpected response: %s",
                         wave, str(resp_data)[:200])
                guesses.pop()  # Remove wrong guess

            finally:
                if img_path:
                    try:
                        os.unlink(img_path)
                    except OSError:
                        pass

            # Wrong answer — try to get new challenge
            gfct_data.clear()
            ca_result.clear()
            try:
                await page.click("[data-theme='try-again']", timeout=3000)
            except Exception:
                pass
            await asyncio.sleep(2)
            for _ in range(15):
                if gfct_data:
                    break
                await asyncio.sleep(1)
            if not gfct_data:
                break

        elapsed = round(time.monotonic() - t_start, 1)
        return {"solved": False, "error": f"failed after {max_attempts} waves",
                "elapsed": elapsed}
    finally:
        try:
            await asyncio.wait_for(browser.close(), timeout=5.0)
        except Exception:
            try:
                browser.close()
            except Exception:
                pass
