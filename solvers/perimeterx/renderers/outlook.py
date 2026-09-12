"""Render trigger for the PerimeterX/HUMAN gate on Outlook signup.

The @outlook.com new-mailbox flow (go.microsoft.com/fwlink linkid=2125440) only
surfaces the HUMAN 'Press & Hold' gate AFTER the whole form is walked: username ->
password -> birthdate -> name. Empirically the gate first appears at the name step
(gate_trigger_probe.py: 0..3 = no, 4_after_name = YES). There is NO standalone URL
that renders the gate (iframe.hsprotect.net returns an empty body; captcha.hsprotect
.net returns 403), so this navigation is unavoidable to reach a harvestable gate.

CRITICAL: every value typed here is a THROWAWAY trigger — its only purpose is to make
the challenge render so the solver can harvest _px3. This is NOT account creation:
the solver never submits the final CreateAccount. The real auto-register script owns
the actual account values and reuses the harvested _px3 (+ same proxy/UA) later.
"""
import asyncio
import random
import string

ENTRY_URL = "https://signup.live.com/signup?lic=1"
# legacy fwlink often breaks through residential proxies (ERR_SSL / tunnel)
# ENTRY_URL_FWLINK = ("https://go.microsoft.com/fwlink/p/?linkid=2125440"
#              "&clcid=0x409&culture=en-us&country=us")

_FIRST = ["Nathan", "Cherly", "Galuh", "Rendy", "Sinta", "Bagas", "Dinda", "Fajar"]
_LAST = ["Kusumo", "Halimah", "Pratama", "Wijaya", "Santoso", "Nugroho", "Saputra"]


async def _fill(page, sels, val):
    for s in sels:
        try:
            await page.fill(s, val, timeout=3000)
            return True
        except Exception:
            continue
    return False


async def _next(page):
    for s in ['#iSignupAction', 'button:has-text("Next")', 'button[type=submit]',
              '#nextButton']:
        try:
            await page.click(s, timeout=2500)
            return True
        except Exception:
            continue
    return False


async def _pick_fluent(page, trigger_sels, option_index):
    """Fluent UI Dropdown (role=combobox -> listbox, not native <select>)."""
    for ts in trigger_sels:
        try:
            await page.click(ts, timeout=3000)
        except Exception:
            continue
        opts = page.locator('[role="option"]')
        try:
            await opts.first.wait_for(state="visible", timeout=3000)
        except Exception:
            await asyncio.sleep(0.5)
        try:
            n = await opts.count()
            if n:
                await opts.nth(min(option_index, n - 1)).click(timeout=2500)
                await asyncio.sleep(0.4)
                return True
        except Exception:
            continue
    return False


async def render_outlook_gate(page) -> None:
    """Walk the throwaway Outlook signup form far enough that the PerimeterX gate
    renders. Navigates to ENTRY_URL itself. Types disposable values only. Stops
    right after the name step — does NOT submit CreateAccount.
    """
    tag = "".join(random.choices(string.ascii_lowercase, k=6)) + str(random.randint(100, 999))
    pw = "Zx" + "".join(random.choices(string.ascii_letters + string.digits, k=12)) + "!7"

    # Prefer direct signup.live.com; fwlink often dies on residential proxies.
    last_err = None
    for entry in (
        "https://signup.live.com/signup?lic=1",
        "https://signup.live.com/signup",
        "https://go.microsoft.com/fwlink/p/?linkid=2125440&clcid=0x409&culture=en-us&country=us",
    ):
        try:
            await page.goto(entry, wait_until="domcontentloaded", timeout=45000)
            last_err = None
            break
        except Exception as e:
            last_err = e
            continue
    if last_err is not None:
        raise last_err

    await asyncio.sleep(6)  # signup.live.com OAuth redirect settles
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    # bare username -> username@outlook.com
    await _fill(page, ['input[name="MemberName"]', 'input[type="email"]'], tag)
    await _next(page)
    await asyncio.sleep(3)
    # password
    await _fill(page, ['input[name="Password"]', 'input[type="password"]'], pw)
    await _next(page)
    await asyncio.sleep(3)
    # "Add some details": birthdate (Fluent month/day + year input)
    await _pick_fluent(page, ['#BirthMonthDropdown', '[aria-label="Birth month"]'],
                       random.randint(0, 11))
    await _pick_fluent(page, ['#BirthDayDropdown', '[aria-label="Birth day"]'],
                       random.randint(0, 27))
    await _fill(page, ['input[name="BirthYear"]', '[aria-label="Birth year"]', '#BirthYear'],
                str(random.randint(1988, 2002)))
    await _next(page)
    await asyncio.sleep(3)
    # name step — the gate renders after this
    await _fill(page, ['#firstNameInput', 'input[name="FirstName"]'], random.choice(_FIRST))
    await _fill(page, ['#lastNameInput', 'input[name="LastName"]'], random.choice(_LAST))
    await _next(page)
    await asyncio.sleep(6)
