/**
 * 易盾指纹参数生成 (ydfp v1.1)
 * 参考: 网易易盾/滑块拼图/ydfp.js
 *
 * 用法:
 *   const fp = require('./fp');
 *   fp("www.example.com")  // => "encoded_data:timestamp"
 */

// ==================== 字节工具 ====================

function toInt8(v) {
    if (v < -128) return toInt8(256 + v);
    if (v > 127) return toInt8(v - 256);
    return v;
}

function xorBytes(a, b) {
    return toInt8(toInt8(a) ^ toInt8(b));
}

function xorArrays(a, b) {
    if (!a || !b) return a;
    const len = b.length;
    const result = [];
    for (let i = 0; i < a.length; i++) {
        result[i] = xorBytes(a[i], b[i % len]);
    }
    return result;
}

function addArrays(a, b) {
    if (!a || !b) return a;
    const len = b.length;
    const result = [];
    for (let i = 0; i < a.length; i++) {
        result[i] = toInt8(a[i] + b[i % len]);
    }
    return result;
}

function intToBytes(n) {
    return [
        toInt8((n >>> 24) & 255),
        toInt8((n >>> 16) & 255),
        toInt8((n >>> 8) & 255),
        toInt8(255 & n)
    ];
}

function copyBytes(src, srcOff, dst, dstOff, len) {
    for (let i = 0; i < len; i++) {
        if (srcOff + i < src.length) dst[dstOff + i] = src[srcOff + i];
    }
    return dst;
}

function padTo64(bytes) {
    if (!bytes || bytes.length === 0) return new Array(64).fill(0);
    if (bytes.length >= 64) return bytes.slice(0, 64);
    const result = [];
    for (let i = 0; i < 64; i++) result[i] = bytes[i % bytes.length];
    return result;
}

function zeroArray(len) {
    return new Array(len).fill(0);
}

// ==================== 字符串 / URI 编码 ====================

function stringToBytes(str) {
    if (str == null) return str;
    const encoded = encodeURIComponent(str);
    const result = [];
    for (let i = 0; i < encoded.length; i++) {
        if (encoded[i] === '%') {
            result.push(parseInt(encoded.substring(i + 1, i + 3), 16));
            i += 2;
        } else {
            result.push(encoded.charCodeAt(i));
        }
    }
    return result;
}

function hexToBytes(hex) {
    hex = "" + hex;
    const result = [];
    for (let i = 0; i < hex.length / 2; i++) {
        const hi = parseInt(hex.charAt(i * 2), 16) << 4;
        const lo = parseInt(hex.charAt(i * 2 + 1), 16);
        result[i] = toInt8(hi + lo);
    }
    return result;
}

// ==================== CRC32 ====================

const CRC32_TABLE = [
    0, 1996959894, 3993919788, 2567524794, 124634137, 1886057615, 3915621685, 2657392035,
    249268274, 2044508324, 3772115230, 2547177864, 162941995, 2125561021, 3887607047, 2428444049,
    498536548, 1789927666, 4089016648, 2227061214, 450548861, 1843258603, 4107580753, 2211677639,
    325883990, 1684777152, 4251122042, 2321926636, 335633487, 1661365465, 4195302755, 2366115317,
    997073096, 1281953886, 3579855332, 2724688242, 1006888145, 1258607687, 3524101629, 2768942443,
    901097722, 1119000684, 3686517206, 2898065728, 853044451, 1172266101, 3705015759, 2882616665,
    651767980, 1373503546, 3369554304, 3218104598, 565507253, 1454621731, 3485111705, 3099436303,
    671266974, 1594198024, 3322730930, 2970347812, 795835527, 1483230225, 3244367275, 3060149565,
    1994146192, 31158534, 2563907772, 4023717930, 1907459465, 112637215, 2680153253, 3904427059,
    2013776290, 251722036, 2517215374, 3775830040, 2137656763, 141376813, 2439277719, 3865271297,
    1802195444, 476864866, 2238001368, 4066508878, 1812370925, 453092731, 2181625025, 4111451223,
    1706088902, 314042704, 2344532202, 4240017532, 1658658271, 366619977, 2362670323, 4224994405,
    1303535960, 984961486, 2747007092, 3569037538, 1256170817, 1037604311, 2765210733, 3554079995,
    1131014506, 879679996, 2909243462, 3663771856, 1141124467, 855842277, 2852801631, 3708648649,
    1342533948, 654459306, 3188396048, 3373015174, 1466479909, 544179635, 3110523913, 3462522015,
    1591671054, 702138776, 2966460450, 3352799412, 1504918807, 783551873, 3082640443, 3233442989,
    3988292384, 2596254646, 62317068, 1957810842, 3939845945, 2647816111, 81470997, 1943803523,
    3814918930, 2489596804, 225274430, 2053790376, 3826175755, 2466906013, 167816743, 2097651377,
    4027552580, 2265490386, 503444072, 1762050814, 4150417245, 2154129355, 426522225, 1852507879,
    4275313526, 2312317920, 282753626, 1742555852, 4189708143, 2394877945, 397917763, 1622183637,
    3604390888, 2714866558, 953729732, 1340076626, 3518719985, 2797360999, 1068828381, 1219638859,
    3624741850, 2936675148, 906185462, 1090812512, 3747672003, 2825379669, 829329135, 1181335161,
    3412177804, 3160834842, 628085408, 1382605366, 3423369109, 3138078467, 570562233, 1426400815,
    3317316542, 2998733608, 733239954, 1555261956, 3268935591, 3050360625, 752459403, 1541320221,
    2607071920, 3965973030, 1969922972, 40735498, 2617837225, 3943577151, 1913087877, 83908371,
    2512341634, 3803740692, 2075208622, 213261112, 2463272603, 3855990285, 2094854071, 198958881,
    2262029012, 4057260610, 1759359992, 534414190, 2176718541, 4139329115, 1873836001, 414664567,
    2282248934, 4279200368, 1711684554, 285281116, 2405801727, 4167216745, 1634467795, 376229701,
    2685067896, 3608007406, 1308918612, 956543938, 2808555105, 3495958263, 1231636301, 1047427035,
    2932959818, 3654703836, 1088359270, 936918000, 2847714899, 3736837829, 1202900863, 817233897,
    3183342108, 3401237130, 1404277552, 615818150, 3134207493, 3453421203, 1423857449, 601450431,
    3009837614, 3294710456, 1567103746, 711928724, 3020668471, 3272380065, 1510334235, 755167117
];

function crc32(bytes) {
    let crc = 4294967295;
    if (bytes != null) {
        for (let i = 0; i < bytes.length; i++) {
            crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ bytes[i]) & 255];
        }
    }
    const raw = intToBytes(crc ^ 4294967295);
    const hex = "0123456789abcdef";
    let out = "";
    for (let i = 0; i < raw.length; i++) {
        const b = raw[i] & 0xff;
        out += hex[(b >>> 4) & 15] + hex[b & 15];
    }
    return out;
}

// ==================== MurmurHash3 x86-32 ====================

function murmurhash3(key, seed) {
    seed = seed || 31;
    const len = key.length;
    const remainder = len & 3;
    const bytes = len - remainder;
    let h1 = seed;
    const c1 = 0xcc9e2d51;
    const c2 = 0x1b873593;
    let i = 0;

    while (i < bytes) {
        let k1 =
            (key.charCodeAt(i) & 0xff) |
            ((key.charCodeAt(++i) & 0xff) << 8) |
            ((key.charCodeAt(++i) & 0xff) << 16) |
            ((key.charCodeAt(++i) & 0xff) << 24);
        ++i;

        k1 = ((k1 & 0xffff) * c1 + ((((k1 >>> 16) * c1) & 0xffff) << 16)) & 0xffffffff;
        k1 = (k1 << 15) | (k1 >>> 17);
        k1 = ((k1 & 0xffff) * c2 + ((((k1 >>> 16) * c2) & 0xffff) << 16)) & 0xffffffff;

        h1 ^= k1;
        h1 = (h1 << 13) | (h1 >>> 19);
        h1 = ((h1 & 0xffff) * 5 + ((((h1 >>> 16) * 5) & 0xffff) << 16)) & 0xffffffff;
        h1 = ((h1 & 0xffff) + 27492 + (((((h1 >>> 16) + 58964) & 0xffff)) << 16));
    }

    let k1 = 0;
    switch (remainder) {
        case 3: k1 ^= (key.charCodeAt(i + 2) & 0xff) << 16;
        case 2: k1 ^= (key.charCodeAt(i + 1) & 0xff) << 8;
        case 1: k1 ^= key.charCodeAt(i) & 0xff;
            k1 = ((k1 & 0xffff) * c1 + ((((k1 >>> 16) * c1) & 0xffff) << 16)) & 0xffffffff;
            k1 = (k1 << 15) | (k1 >>> 17);
            k1 = ((k1 & 0xffff) * c2 + ((((k1 >>> 16) * c2) & 0xffff) << 16)) & 0xffffffff;
            h1 ^= k1;
    }

    h1 ^= len;
    h1 ^= h1 >>> 16;
    h1 = ((h1 & 0xffff) * 0x85ebca6b + ((((h1 >>> 16) * 0x85ebca6b) & 0xffff) << 16)) & 0xffffffff;
    h1 ^= h1 >>> 13;
    h1 = ((h1 & 0xffff) * 0xc2b2ae35 + ((((h1 >>> 16) * 0xc2b2ae35) & 0xffff) << 16)) & 0xffffffff;
    h1 ^= h1 >>> 16;

    return h1 >>> 0;
}

// ==================== 指纹哈希处理 ====================

function formatNumberToFixedDigits(num, targetLen) {
    if (num < 0 || num >= 10) throw Error("Number out of range");
    const s = String(num);
    const digits = new Array(targetLen).fill('0');
    let idx = 0;
    for (let i = 0; i < s.length && idx < targetLen; i++) {
        if (s.charAt(i) !== '.') digits[idx++] = s.charAt(i);
    }
    return parseInt(digits.join(""));
}

function processFingerprint(str) {
    const hash = murmurhash3(str, 31);
    const hashStr = String(hash);

    // 计算哈希各位数字之和
    let sum = 0;
    let count = 0;
    for (let i = 0; i < hashStr.length; i++) {
        const d = parseInt(hashStr.charAt(i));
        sum += isNaN(d) ? 1 : d;
        count++;
    }
    if (count === 0) count = 1;

    // 派生值1: 数字均值的固定2位整数
    const avg = sum / count;
    const dv1 = formatNumberToFixedDigits(avg, 2);

    // 派生值2: 按阈值分组后两组均值之差
    const threshold = Math.floor(dv1 / 10);
    let sumLo = 0, cntLo = 0, sumHi = 0, cntHi = 0;
    for (let i = 0; i < hashStr.length; i++) {
        let d = parseInt(hashStr.charAt(i));
        if (isNaN(d)) {
            sumHi += threshold;
            cntHi++;
        } else if (d < threshold) {
            sumLo += d;
            cntLo++;
        } else {
            sumHi += d;
            cntHi++;
        }
    }
    if (cntHi === 0) cntHi = 1;
    if (cntLo === 0) cntLo = 1;
    const dv2 = formatNumberToFixedDigits(sumHi / cntHi - sumLo / cntLo, 2);

    return [
        hash,
        String(dv1).padStart(2, '0'),
        String(dv2).padStart(2, '0')
    ].join("");
}

// ==================== 自定义 Base64 编码 ====================

const B64_ALPHABET = ["2","4","0","a","Y","H","i","Q","x","L","\\","Z","u","f","V","l","g","8","s","P","M","R","6","d","G","k","X","v","O","/","C","b","w","9","W","D","j","1","E","T","y","I","S","c","m","e","o","J","z","3","7","q","t","h","B","r","U","+","K","N","A","5","p","n"];
const B64_PADDING = "F";

function b64Encode3(bytes, off, len) {
    const out = [];
    if (len === 1) {
        const a = bytes[off];
        out.push(B64_ALPHABET[a >>> 2 & 63]);
        out.push(B64_ALPHABET[(a << 4 & 48)]);
        out.push(B64_PADDING, B64_PADDING);
    } else if (len === 2) {
        const a = bytes[off], b = bytes[off + 1];
        out.push(B64_ALPHABET[a >>> 2 & 63]);
        out.push(B64_ALPHABET[(a << 4 & 48) + (b >>> 4 & 15)]);
        out.push(B64_ALPHABET[(b << 2 & 60)]);
        out.push(B64_PADDING);
    } else {
        const a = bytes[off], b = bytes[off + 1], c = bytes[off + 2];
        out.push(B64_ALPHABET[a >>> 2 & 63]);
        out.push(B64_ALPHABET[(a << 4 & 48) + (b >>> 4 & 15)]);
        out.push(B64_ALPHABET[(b << 2 & 60) + (c >>> 6 & 3)]);
        out.push(B64_ALPHABET[c & 63]);
    }
    return out.join("");
}

function b64Encode(bytes) {
    if (!bytes || bytes.length === 0) return "";
    const chunks = [];
    for (let i = 0; i < bytes.length; ) {
        const left = bytes.length - i;
        if (left >= 3) {
            chunks.push(b64Encode3(bytes, i, 3));
            i += 3;
        } else {
            chunks.push(b64Encode3(bytes, i, left));
            break;
        }
    }
    return chunks.join("");
}

// ==================== S-Box 替换 ====================

const SBOX = [-9,-84,-50,59,115,102,57,125,94,-15,15,2,-72,-98,-79,38,-56,-49,76,-26,-117,60,90,9,-107,-12,-71,-100,63,42,-18,28,-120,-11,33,45,79,92,37,97,4,58,98,84,-97,-88,95,-104,-13,-89,78,-90,119,-66,13,-5,29,-116,-4,-81,27,40,-59,-43,85,48,-74,109,-64,26,67,-33,-115,0,-37,-102,88,-48,127,-86,41,105,-2,122,-42,112,-94,81,-31,-65,-101,-14,65,49,-67,-114,-103,-87,-19,104,66,-73,-34,-78,-45,-27,-109,-108,47,61,86,43,-54,25,64,-35,-44,53,-112,36,73,89,-82,51,-32,39,-83,80,-85,-111,12,-58,103,-76,-46,-127,34,1,-99,14,-57,110,106,93,-52,11,113,20,-106,75,62,-69,-39,-55,-119,126,114,123,10,77,-121,-8,74,21,-93,17,-61,-21,-105,-126,18,124,-17,52,-10,-77,-24,-22,120,-95,-25,96,-110,22,-23,69,-125,-128,-47,-38,-1,3,-20,100,68,101,5,117,-122,44,-51,-36,-41,24,-80,30,82,-63,-40,-92,91,-6,-53,-124,-62,-28,111,19,50,108,70,-68,-29,-75,99,-91,-60,-70,71,-118,-3,83,87,-7,32,55,31,-123,121,107,-113,46,-30,118,54,23,116,-16,7,6,35,16,-96,56,72,8];

function sboxSubstitute(bytes) {
    if (bytes == null) return null;
    const result = [];
    for (let i = 0; i < bytes.length; i++) {
        const b = bytes[i];
        result[i] = SBOX[(b >>> 4 & 15) * 16 + (b & 15)];
    }
    return result;
}

// ==================== 随机字符串 ====================

const RANDOM_CHARS = "aZbY0cXdW1eVf2Ug3Th4SiR5jQk6PlO7mNn8MoL9pKqJrIsHtGuFvEwDxCyBzA";

function randomString(len) {
    let s = "";
    for (let i = 0; i < len; i++) {
        s += RANDOM_CHARS[Math.floor(Math.random() * RANDOM_CHARS.length)];
    }
    return s;
}

// ==================== 核心加密流程 ====================

const SEED_KEY_STR = "14731255234d414cF91356d684E4E8F5F56c8f1bc";

function encrypt(dataBytes, randomKey, expandedKey) {
    if (!dataBytes || dataBytes.length === 0) return zeroArray(64);

    const dataLen = dataBytes.length;
    const padSize = dataLen % 64 <= 60 ? 64 - dataLen % 64 - 4 : 128 - dataLen % 64 - 4;
    const padded = [];
    copyBytes(dataBytes, 0, padded, 0, dataLen);
    for (let i = 0; i < padSize; i++) padded[dataLen + i] = 0;
    copyBytes(intToBytes(dataLen), 0, padded, dataLen + padSize, 4);

    // 按64字节分块
    const numBlocks = padded.length / 64;
    const blocks = [];
    for (let b = 0; b < numBlocks; b++) {
        blocks[b] = padded.slice(b * 64, b * 64 + 64);
    }

    // 输出 = [4字节随机key] + [加密后的各块]
    const output = [];
    copyBytes(randomKey, 0, output, 0, 4);
    let prevBlock = expandedKey;

    for (let b = 0; b < blocks.length; b++) {
        const block = blocks[b];

        // Step 1: XOR with 37
        let step = [];
        for (let i = 0; i < block.length; i++) step[i] = xorBytes(block[i], 37);

        // Step 2: XOR with decrementing constant (35, 34, 33, ...)
        let c = 35;
        for (let i = 0; i < step.length; i++) step[i] = xorBytes(step[i], c--);

        // Step 3: ADD with incrementing constant (-44, -43, -42, ...)
        c = -44;
        for (let i = 0; i < step.length; i++) step[i] = toInt8(step[i] + c++);

        // Step 4: XOR with expanded key
        step = xorArrays(step, expandedKey);

        // Step 5: ADD with prevBlock
        if (prevBlock != null) {
            for (let i = 0; i < step.length; i++) {
                step[i] = toInt8(step[i] + prevBlock[i % prevBlock.length]);
            }
        }

        // Step 6: XOR with prevBlock
        step = xorArrays(step, prevBlock);

        // Step 7+8: S-Box substitution twice
        let result = sboxSubstitute(step);
        result = sboxSubstitute(result);

        copyBytes(result, 0, output, b * 64 + 4, 64);
        prevBlock = result;
    }

    return output;
}

// ==================== 主函数 ====================

/**
 * 生成易盾指纹参数
 * @param {string} hostname - 目标网站域名, 如 "www.zhihu.com"
 * @returns {string} 格式: "encoded_fp:timestamp"
 */
function fp(hostname) {
    hostname = hostname || "www.zhihu.com";
    const timestamp = Date.now() + 900000;

    // 浏览器指纹组1 (Mac Chrome 环境)
    const fp1 = [
        true,
        true,
        true,
        "undefined" + Math.random().toString(),
        "undefined",
        null,
        "MacIntel",
        "data:image/png;base64,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",
        "ActiveBorder:rgb(0, 0, 0):ActiveCaption:rgb(0, 0, 0):AppWorkspace:rgb(255, 255, 255):Background:rgb(255, 255, 255):ButtonFace:rgb(239, 239, 239):ButtonHighlight:rgb(239, 239, 239):ButtonShadow:rgb(239, 239, 239):ButtonText:rgb(0, 0, 0):CaptionText:rgb(0, 0, 0):GrayText:rgb(128, 128, 128):Highlight:rgba(128, 188, 254, 0.6):HighlightText:rgb(0, 0, 0):InactiveBorder:rgb(0, 0, 0):InactiveCaption:rgb(255, 255, 255):InactiveCaptionText:rgb(128, 128, 128):InfoBackground:rgb(255, 255, 255):InfoText:rgb(0, 0, 0):Menu:rgb(255, 255, 255):MenuText:rgb(0, 0, 0):Scrollbar:rgb(255, 255, 255):ThreeDDarkShadow:rgb(0, 0, 0):ThreeDFace:rgb(239, 239, 239):ThreeDHighlight:rgb(0, 0, 0):ThreeDLightShadow:rgb(0, 0, 0):ThreeDShadow:rgb(0, 0, 0):Window:rgb(255, 255, 255):WindowFrame:rgb(0, 0, 0):WindowText:rgb(0, 0, 0)"
    ];

    // 浏览器指纹组2 (Mac Chrome 环境)
    const fp2 = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "zh-CN",
        24 + Math.random(),
        "1120x1792",
        -480,
        null,
        "PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf$Chrome PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf$Chromium PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf$Microsoft Edge PDF Viewer::Portable Document Format::application/pdf~pdf,text/pdf~pdf$WebKit built-in PDF::Portable Document Format::application/pdf~pdf,text/pdf~pdf;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;"
    ];

    // 组装 fp 对象
    const fpObj = {
        "v": "v1.1",
        "fp": processFingerprint(fp1.join("###")) + "," + processFingerprint(fp2.join("###")),
        "u": randomString(3) + timestamp + randomString(3),
        "h": hostname
    };

    // JSON序列化 (key/value 用单引号包裹)
    let data = JSON.stringify(fpObj)
        .replace(/"([^"]+)":/g, "'$1':")
        .replace(/:"([^"]+)"/g, ":'$1'");

    // 追加CRC32校验后转字节
    const dataBytes = stringToBytes(data + crc32(stringToBytes(data)));

    // 生成4字节随机密钥
    const randomKey = [];
    for (let i = 0; i < 4; i++) {
        randomKey[i] = toInt8(Math.floor(Math.random() * 256));
    }

    // 种子密钥 XOR 随机密钥 -> 64字节扩展密钥
    let expandedKey = padTo64(stringToBytes(SEED_KEY_STR));
    expandedKey = xorArrays(expandedKey, padTo64(randomKey));
    expandedKey = padTo64(expandedKey);

    // 加密
    const output = encrypt(dataBytes, randomKey, expandedKey);

    // 自定义Base64编码
    return b64Encode(output) + ":" + timestamp;
}

// ==================== 导出 ====================

if (typeof module !== 'undefined' && module.exports) {
    module.exports = fp;
    module.exports.fp = fp;
    module.exports.murmurhash3 = murmurhash3;
    module.exports.processFingerprint = processFingerprint;
    module.exports.crc32 = crc32;

    if (require.main === module) {
        const hostname = process.argv[2] || "www.zhihu.com";
        console.log(fp(hostname));
    }
}
