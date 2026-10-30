import hashlib
import random
import time
from pow_solver import MAP, URLSAFE_INT2CHAR


def generate_tsh(sitekey: str) -> str:
    """توليد توقيع tsh عبر MD5 مطابق لـ transactionSignature في الـ JS"""
    sig = hashlib.md5(f"mtcap@mtcaptcha.com{sitekey}".encode()).hexdigest()
    return f"TH[{sig}]"


def generate_kt(fseed: str, solution_text: str, delay_before_start: float = 1.2) -> str:
    """محاكاة كتابة الحل وتوليد بارامتر kt المطابق للـ JS الأصلي"""
    fseed_64 = fseed[:64]
    tail_keeint = 63  # MAP[ord('_')]
    init_ts = time.time() * 1000

    cider = [0] * 64
    last_char = MAP[ord(fseed_64[-1])]
    for i in range(64):
        c = MAP[ord(fseed_64[i])]
        cider[i] = c ^ last_char
        last_char = c

    keehist = list(cider)

    # 1. محاكاة وقت الاستجابة والتفكير قبل الضغطة الأولى
    now = init_ts + delay_before_start * 1000
    first_diff = min(round((now - init_ts) / 500), 0xF3C)
    keehist[0] = cider[0] ^ (first_diff // 64)
    keehist[1] = cider[1] ^ round(first_diff % 64)
    keehist[2] = cider[2] ^ tail_keeint

    pos = 2
    last_kee_ts = now

    # 2. محاكاة ضغطات المفاتيح بتوقيت إنساني طبيعي
    for char in solution_text:
        char_int = MAP[ord(char)] if ord(char) < 256 else -1
        if char_int < 0 or pos > 0x34:
            continue

        now += random.uniform(0.12, 0.26) * 1000
        delta = min(round((now - last_kee_ts) / 30), 63)

        keehist[pos] = cider[pos] ^ char_int
        keehist[pos + 1] = cider[pos + 1] ^ delta
        keehist[pos + 2] = cider[pos + 2] ^ tail_keeint

        pos += 2
        last_kee_ts = now

    return "".join(URLSAFE_INT2CHAR[val] for val in keehist)
