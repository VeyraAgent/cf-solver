import json
import time
import urllib.parse
import uuid
import requests

# ----------------------------------------------------
# خوارزمية حل الـ Fold Challenge (Proof-of-Work) لـ MTCaptcha
# ----------------------------------------------------
URLSAFE_INT2CHAR = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    "-", "_",
]

MAP = [-1] * 256
MAP[ord('-')] = 62
MAP[ord('_')] = 63
for i in range(10): MAP[ord('0') + i] = i
for i in range(26): MAP[ord('A') + i] = 10 + i
for i in range(26): MAP[ord('a') + i] = 36 + i


def fold_base64_int_array(a1, fold_count):
    a2 = list(reversed(a1))
    a3 = list(a1)
    offset = 0
    y = 0
    z = 0
    for _ in range(fold_count):
        offset += 1
        for x in range(len(a1)):
            a3[x] = (
                int((a3[x] + a2[(x + offset) % len(a2)]) * 73 / 8) + y + z
            ) % 64
            z = int(y / 2)
            y = int(a3[x] / 2)
    return a3


def hash_int_ary(arr):
    h = 0
    for val in arr:
        h = ((h << 5) - h + val) & 0xFFFFFFFF
        if h & 0x80000000:
            h = -((~h + 1) & 0xFFFFFFFF)
    if h < 0:
        h = -h
    return h


import subprocess

def solve_fold_python(fseed, fslots, fdepth):
    if not fseed or fslots < 1:
        return "0"
    int_arr = [MAP[ord(c)] for c in fseed]
    res = []
    for _ in range(fslots):
        int_arr = fold_base64_int_array(int_arr, 31)
        h_val = hash_int_ary(fold_base64_int_array(int_arr, fdepth))
        val = h_val % 4096
        c1 = URLSAFE_INT2CHAR[(val >> 6) % 64]
        c2 = URLSAFE_INT2CHAR[val & 0x3F]
        res.append(c1 + c2)
    return "".join(res)


def solve_fold_fast(fseed, fslots, fdepth):
    """حل الـ Fold Challenge بسرعة فائقة باستخدام Node.js إذا كان متوفراً"""
    js_code = f"""
    const URLSAFE_INT2CHAR = ['0','1','2','3','4','5','6','7','8','9','A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T','U','V','W','X','Y','Z','a','b','c','d','e','f','g','h','i','j','k','l','m','n','o','p','q','r','s','t','u','v','w','x','y','z','-','_'];
    const MAP = new Array(256).fill(-1);
    MAP['-'.charCodeAt(0)] = 62; MAP['_'.charCodeAt(0)] = 63;
    for(let i=0;i<10;i++) MAP['0'.charCodeAt(0)+i] = i;
    for(let i=0;i<26;i++) MAP['A'.charCodeAt(0)+i] = 10+i;
    for(let i=0;i<26;i++) MAP['a'.charCodeAt(0)+i] = 36+i;

    function fold(a1, count) {{
        const a2 = a1.slice().reverse(), a3 = a1.slice();
        let offset = 0, y = 0, z = 0;
        for (let i = 0; i < count; i++) {{
            offset++;
            for (let x = 0; x < a1.length; x++) {{
                a3[x] = (Math.floor((a3[x] + a2[(x + offset) % a2.length]) * 73 / 8) + y + z) % 64;
                z = Math.floor(y / 2);
                y = Math.floor(a3[x] / 2);
            }}
        }}
        return a3;
    }}

    function hash(arr) {{
        let h = 0;
        for (let i = 0; i < arr.length; i++) h = ((h << 5) - h + arr[i]) | 0;
        return Math.abs(h);
    }}

    const int_arr = '{fseed}'.split('').map(c => MAP[c.charCodeAt(0)]);
    let cur = int_arr;
    const res = [];
    for (let i = 0; i < {fslots}; i++) {{
        cur = fold(cur, 31);
        const h_val = hash(fold(cur, {fdepth}));
        const val = h_val % 4096;
        res.push(URLSAFE_INT2CHAR[(val >> 6) % 64] + URLSAFE_INT2CHAR[val & 0x3F]);
    }}
    process.stdout.write(res.join(''));
    """
    try:
        proc = subprocess.run(
            ["node", "-e", js_code],
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
    except Exception:
        pass
    return solve_fold_python(fseed, fslots, fdepth)


# ----------------------------------------------------
# الدالة الرئيسية لجلب التحدي وصورة الـ Captcha
# ----------------------------------------------------
def fetch_mtcaptcha_challenge():
    # 1. التوليد الديناميكي لـ rt و ss
    rt = int(time.time() * 1000)  # Timestamp الحالي بالمللي ثانية
    ss = f"S1{uuid.uuid4()}"  # توليد Session ID بصيغة S1 + UUID v4

    # الـ Parameters الثابتة الخاصة بالموقع
    sitekey = "MTPublic-KzqLY1cKH"
    host_domain = "2captcha.com"
    tsh = "TH[da41f1ece73265d224dcfea32cece1b2]"

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8,hy;q=0.7",
        "Referer": (
            f"https://service.mtcaptcha.com/mtcv1/client/iframe.html?v=2026-09-02.20.13.08&sitekey={sitekey}&iframeId=mtcaptcha-iframe-1&widgetSize=standard&custom=false&widgetInstance=mtcaptcha&challengeType=standard&theme=basic&lang=en&action=&autoFadeOuterText=false&host=https%3A%2F%2F{host_domain}&hostname={host_domain}&serviceDomain=service.mtcaptcha.com&textLength=0&lowFrictionInvisible=&enableMouseFlow=false"
        ),
        "sec-ch-ua": (
            '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"'
        ),
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

    # Step 1: طلب الـ Challenge
    getchallenge_url = (
        f"https://service.mtcaptcha.com/mtcv1/api/getchallenge.json?"
        f"sk={sitekey}&bd={host_domain}&rt={rt}&tsh={urllib.parse.quote(tsh)}&act=%24&ss={ss}&lf=1&tl=%24&lg=en&tp=s"
    )

    print(f"[+] 1. Sending getchallenge.json (rt={rt}, ss={ss})...")
    resp_ch = session.get(getchallenge_url)
    ch_json = resp_ch.json()

    if ch_json.get("code") != 1200:
        print("[-] Error getting challenge:", ch_json)
        return None

    print("[+] Challenge JSON response:")
    print(json.dumps(ch_json, indent=2))

    ch_data = ch_json["result"]["challenge"]
    ct = ch_data["ct"]

    # Step 2: حساب الـ Proof-of-Work (fa) إذا تطلب الأمر (عندما يكون preRes = true)
    fa = "$"
    fold_info = ch_data.get("foldChlg", {})
    if ch_data.get("hasFoldChlg") and fold_info.get("preRes") is True:
        print("[!] Solving Proof-of-Work (foldChlg)...")
        fa = solve_fold_fast(
            fold_info["fseed"], fold_info["fslots"], fold_info["fdepth"]
        )
        print(f"[+] Computed fa: {fa}")

    # Step 3: طلب صورة الكابتشا مباشرة عبر GET (مطابق لسلوك الـ Widget)
    JS_SAFE = "-._~()'!"
    encoded_ct = urllib.parse.quote(ct, safe=JS_SAFE)
    encoded_fa = urllib.parse.quote(fa, safe=JS_SAFE)
    getimage_url = (
        f"https://service.mtcaptcha.com/mtcv1/api/getimage.json?"
        f"sk={sitekey}&ct={encoded_ct}&fa={encoded_fa}&ss={ss}"
    )

    print(f"\n[+] 2. Sending getimage.json...")
    resp_img = session.get(getimage_url)
    img_json = resp_img.json()

    if img_json.get("code") == 1200:
        base64_image = img_json["result"]["img"]["image64"]
        print("\n[+] SUCCESS! Image Base64:")
        print(base64_image)
        return base64_image
    else:
        print("[-] Failed to get image. Response:", img_json)
        return None


if __name__ == "__main__":
    fetch_mtcaptcha_challenge()