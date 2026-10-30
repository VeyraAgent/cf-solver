import subprocess

URLSAFE_INT2CHAR = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    "-", "_",
]

MAP = [-1] * 256
MAP[ord("-")] = 62
MAP[ord("_")] = 63
for i in range(10): MAP[ord("0") + i] = i
for i in range(26): MAP[ord("A") + i] = 10 + i
for i in range(26): MAP[ord("a") + i] = 36 + i


def fold_base64_int_array(a1, fold_count):
    a2 = a1[::-1]
    a3 = list(a1)
    L = len(a1)
    offset = 0
    y = 0
    z = 0
    for _ in range(fold_count):
        offset += 1
        for x in range(L):
            a3[x] = (int((a3[x] + a2[(x + offset) % L]) * 73 / 8) + y + z) % 64
            z = y >> 1
            y = a3[x] >> 1
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


def solve_fold_python(fseed: str, fslots: int, fdepth: int) -> str:
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


def solve_fold_fast(fseed: str, fslots: int, fdepth: int) -> str:
    """حل الـ Proof of Work بسرعة فائقة عبر Node.js إذا كان متوفراً مع Fallback لبايثون"""
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
            timeout=8
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
    except Exception:
        pass
    return solve_fold_python(fseed, fslots, fdepth)
