import json
import time
import urllib.parse
import uuid
import requests
from pow_solver import solve_fold_fast
from kee_simulator import generate_kt, generate_tsh
from ocr_solver import solve_image_ocr

JS_SAFE = "-._~()'!"

def extract_domain(url_or_domain: str) -> str:
    """Extract domain from full URL or return domain directly."""
    if not url_or_domain:
        return "2captcha.com"
    if "://" in url_or_domain:
        parsed = urllib.parse.urlparse(url_or_domain)
        return parsed.netloc.split(":")[0]
    return url_or_domain.split("/")[0].split(":")[0]

class MTCaptchaSolver:
    def __init__(
        self,
        sitekey: str = "MTPublic-KzqLY1cKH",
        host_domain: str = "2captcha.com",
        pageurl: str = None,
        action: str = "$",
        lang: str = "en",
        proxy: str = None,
        session: requests.Session = None
    ):
        self.sitekey = sitekey
        raw_target = pageurl or host_domain or "2captcha.com"
        self.host_domain = extract_domain(raw_target)
        self.action = action if action else "$"
        self.lang = lang if lang else "en"
        self.proxy = proxy
        
        self.session = session or requests.Session()
        if self.proxy:
            self.session.proxies = {
                "http": self.proxy,
                "https": self.proxy
            }

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8,hy;q=0.7",
            "Referer": (
                f"https://service.mtcaptcha.com/mtcv1/client/iframe.html?v=2026-09-02.20.13.08&sitekey={self.sitekey}&iframeId=mtcaptcha-iframe-1&widgetSize=standard&custom=false&widgetInstance=mtcaptcha&challengeType=standard&theme=basic&lang={self.lang}&action={self.action if self.action != '$' else ''}&autoFadeOuterText=false&host=https%3A%2F%2F{self.host_domain}&hostname={self.host_domain}&serviceDomain=service.mtcaptcha.com&textLength=0&lowFrictionInvisible=&enableMouseFlow=false"
            ),
            "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "Cookie": (
                "mtv1ConfSum={v:01|wdsz:std|thm:basic|lan:en|chlg:std|clan:1|cstyl:1|afv:0|afot:0|};"
                " jsV=2026-09-02.20.13.08; mtv1Pulse=00016kwa04t1VIwceDt1_iwcXq"
            ),
        })

    def solve(self, custom_solution: str = None) -> dict:
        """
        دورة حل الكابتشا كاملة:
        1. جلب الـ Challenge
        2. جلب صورة الكابتشا (Base64)
        3. التعرف على الحروف بالـ OCR
        4. حل الـ PoW وتوليد الـ KT والـ TSH
        5. إرسال الحل والتحقق من النتيجة
        """
        # 1. التوليد الديناميكي لـ rt و ss
        rt = int(time.time() * 1000)
        ss = f"S1{uuid.uuid4()}"
        tsh = generate_tsh(self.sitekey)

        print(f"[+] 1. Fetching challenge (rt={rt}, ss={ss}, bd={self.host_domain}, sk={self.sitekey})...")
        getch_url = (
            f"https://service.mtcaptcha.com/mtcv1/api/getchallenge.json?"
            f"sk={self.sitekey}&bd={self.host_domain}&rt={rt}&tsh={urllib.parse.quote(tsh, safe=JS_SAFE)}&act={urllib.parse.quote(self.action, safe=JS_SAFE)}&ss={ss}&lf=1&tl=%24&lg={self.lang}&tp=s"
        )
        resp_ch = self.session.get(getch_url)
        ch_recv_ts = time.time()
        ch_json = resp_ch.json()

        if ch_json.get("code") != 1200:
            print("[-] Error in getchallenge:", ch_json)
            return {"success": False, "step": "getchallenge", "response": ch_json}

        ch_data = ch_json["result"]["challenge"]
        ct = ch_data["ct"]
        fold_info = ch_data.get("foldChlg", {})
        fseed = fold_info.get("fseed", "")
        fslots = fold_info.get("fslots", 30)
        fdepth = fold_info.get("fdepth", 100)

        print(f"[+] Challenge Token (ct): {ct}")

        # 2. جلب صورة الكابتشا مباشرة
        fa_for_img = "$"
        if ch_data.get("hasFoldChlg") and fold_info.get("preRes") is True:
            print("[!] Solving preRes Fold Challenge for image...")
            fa_for_img = solve_fold_fast(fseed, fslots, fdepth)

        enc_ct = urllib.parse.quote(ct, safe=JS_SAFE)
        enc_fa = urllib.parse.quote(fa_for_img, safe=JS_SAFE)
        getimg_url = (
            f"https://service.mtcaptcha.com/mtcv1/api/getimage.json?"
            f"sk={self.sitekey}&ct={enc_ct}&fa={enc_fa}&ss={ss}"
        )

        print("[+] 2. Fetching challenge image...")
        resp_img = self.session.get(getimg_url)
        img_json = resp_img.json()

        if img_json.get("code") != 1200:
            print("[-] Error in getimage:", img_json)
            return {"success": False, "step": "getimage", "response": img_json}

        image64 = img_json["result"]["img"]["image64"]
        print(f"[+] Image Base64 received (length={len(image64)})")

        # 3. حل النص بالـ OCR أو استخدام الحل المخصص
        if custom_solution:
            solution_text = custom_solution
            print(f"[+] Using custom solution text: {solution_text}")
        else:
            print("[+] 3. Running OCR on image...")
            solution_text = solve_image_ocr(image64)
            print(f"[+] OCR Detected Text: {solution_text}")

        # 4. حساب الـ PoW للحل النهائي
        print("[+] 4. Solving Proof-of-Work for submission...")
        fa_solution = solve_fold_fast(fseed, fslots, fdepth) if ch_data.get("hasFoldChlg") else "$"
        kt = generate_kt(fseed, solution_text)
        rt_solve = int(time.time() * 1000)

        # 5. مراعاة توقيت waitChlg كما في الودجت
        time_hex = ch_data.get("waitChlg", {}).get("time", "")
        if time_hex and len(time_hex) > 1:
            val = int(time_hex[1:], 16)
            wait_ms = ((val * 0x2F + 0x0B) % 0x100) * 100
        else:
            wait_ms = 0

        target_ts = ch_recv_ts + (wait_ms / 1000.0) + 0.3
        remaining_sec = target_ts - time.time()
        if remaining_sec > 0:
            print(f"[!] Waiting {remaining_sec:.2f}s for waitChlg constraint (wait_ms={wait_ms}ms)...")
            time.sleep(remaining_sec)

        # 6. إرسال طلب solvechallenge.json
        solve_params = {
            "ct": ct,
            "sk": self.sitekey,
            "st": solution_text,
            "lf": "1",
            "bd": self.host_domain,
            "rt": rt_solve,
            "tsh": tsh,
            "fa": fa_solution,
            "qh": "$",
            "act": self.action,
            "ss": ss,
            "tl": "$",
            "lg": self.lang,
            "tp": "s",
            "kt": kt,
            "fs": fseed if fseed else "0"
        }

        qs = "&".join(
            f"{urllib.parse.quote(str(k), safe=JS_SAFE)}={urllib.parse.quote(str(v), safe=JS_SAFE)}"
            for k, v in solve_params.items()
        )
        solve_url = f"https://service.mtcaptcha.com/mtcv1/api/solvechallenge.json?{qs}"

        print(f"[+] 5. Sending solvechallenge.json (st={solution_text})...")
        resp_solve = self.session.get(solve_url)
        solve_json = resp_solve.json()

        print("\n" + "="*50)
        print("[+] RESULT:")
        print(json.dumps(solve_json, indent=2))
        print("="*50)

        is_verified = solve_json.get("result", {}).get("verifyResult", {}).get("isVerified", False)
        verified_token = solve_json.get("result", {}).get("verifyResult", {}).get("verifiedToken", {}).get("vt", "")

        return {
            "success": is_verified,
            "isVerified": is_verified,
            "verifiedToken": verified_token,
            "solutionText": solution_text,
            "image64": image64,
            "response": solve_json
        }

    def solve_until_verified(self, max_retries: int = 5) -> dict:
        """
        إعادة المحاولة تلقائياً بجلب Challenge جديد في حال عدم دقة الـ OCR
        حتى الحصول على isVerified: True والـ verifiedToken
        """
        result = {"success": False, "isVerified": False}
        for attempt in range(1, max_retries + 1):
            print(f"\n>>> [Attempt {attempt}/{max_retries}] Starting solve cycle...")
            result = self.solve()
            if result.get("isVerified"):
                print(f"[SUCCESS] Captcha Solved Successfully on attempt {attempt}!")
                result["attempts"] = attempt
                return result
            print(f"[FAIL] Attempt {attempt} failed (isVerified=False). Retrying with new challenge...")
            time.sleep(1)

        print(f"[-] Reached maximum retries ({max_retries}).")
        result["attempts"] = max_retries
        return result
