require("./webpack");

// ==================== 常量 ====================

var ENCRYPT_KEY_HEX = "fd6a43ae25f74398b61c03c83be37449";

var SBOX_HEX =
    "a7be3f3933fa8c5fcf86c4b6908b569ba1e26c1a6d7cfbf6" +
    "0ae4b00e074a194dac4b73e7f898541159a39d08183b76eedee3ed341e6685d2" +
    "357440158394b1ff03a9004cbbb5ca7dcb7f41489a16e03dcc9c71eb3c979668" +
    "5b1d01b4d56193a6e1f1a2470445c191ae49c5d82765dc82c350f263387a24a5" +
    "02fcbf442e2dddaad0e936d9ea22b89275307b42518fbc3a626ba806d4ecd6d7" +
    "25f50cc8c72fefa4551ccd6fc9b2b7ab954f815c7264c6e51f4eaf99885a7989" +
    "2b1b60a0b3526e57ba5d178d370958847eb9fd28f9ce0bc023f4148a2adfe632" +
    "126769057043d3bd8eda0df7872629f3809ef05310e83113216afe202c460fc2" +
    "3e789f77d1addb5e";

var OP_SEQUENCE = "037606da0296055c";

var CUSTOM_BASE64_ALPHABET = "MB.CfHUzEeJpsuGkgNwhqiSaI4Fd9L6jYKZAxn1/Vml0c5rbXRP+8tD3QTO2vWyo";
var CUSTOM_BASE64_PADDING = "7";

// CRC32查找表
var CRC32_TABLE = [];
for (var _i = 0; _i < 256; _i++) {
    var _c = _i;
    for (var _j = 0; _j < 8; _j++) {
        if (_c & 1) {
            _c = (0xEDB88320 ^ (_c >>> 1)) >>> 0;
        } else {
            _c = (_c >>> 1) >>> 0;
        }
    }
    CRC32_TABLE.push(_c);
}

// ==================== 基础工具函数 ====================

function clamp_byte(n) {
    return n & 0xFF;
}

function safe_xor(a, b) {
    return clamp_byte(a) ^ clamp_byte(b);
}

function int_to_4bytes_be(n) {
    return [(n >>> 24) & 0xFF, (n >>> 16) & 0xFF, (n >>> 8) & 0xFF, n & 0xFF];
}

function int_to_2bytes_be(n) {
    return [(n >>> 8) & 0xFF, n & 0xFF];
}

function hex_to_bytes(hex_str) {
    var result = [];
    for (var i = 0; i < hex_str.length; i += 2) {
        result.push(parseInt(hex_str.substr(i, 2), 16));
    }
    return result;
}

function generate_uuid() {
    var chars = 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx';
    var result = [];
    for (var i = 0; i < chars.length; i++) {
        var c = chars[i];
        if (c === 'x') {
            result.push(Math.floor(Math.random() * 16).toString(16));
        } else if (c === 'y') {
            result.push(((Math.floor(Math.random() * 16) & 0x3) | 0x8).toString(16));
        } else {
            result.push(c);
        }
    }
    return result.join('');
}

function _random_hex(length) {
    var result = [];
    for (var i = 0; i < length; i++) {
        var b = Math.floor(Math.random() * 256);
        result.push(b < 16 ? '0' + b.toString(16) : b.toString(16));
    }
    return result.join('');
}

// ==================== CRC32 ====================

function crc32_hex(data) {
    var crc = 0xFFFFFFFF;
    for (var i = 0; i < data.length; i++) {
        crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ data[i]) & 0xFF];
    }
    crc = 0xFFFFFFFF ^ crc;
    var bytes = int_to_4bytes_be(crc);
    var hex = '';
    for (var j = 0; j < bytes.length; j++) {
        hex += bytes[j] < 16 ? '0' + bytes[j].toString(16) : bytes[j].toString(16);
    }
    return hex;
}

// ==================== S盒替换 ====================

var _sbox_cache = null;

function get_sbox() {
    if (_sbox_cache === null) {
        _sbox_cache = hex_to_bytes(SBOX_HEX);
    }
    return _sbox_cache;
}

function sbox_substitute(data) {
    var sbox = get_sbox();
    var result = [];
    for (var i = 0; i < data.length; i++) {
        var high = (data[i] >>> 4) & 0x0F;
        var low = data[i] & 0x0F;
        result.push(sbox[16 * high + low]);
    }
    return result;
}

// ==================== 操作序列函数 ====================

function op_xor_increment(data, start) {
    var result = [];
    var t = clamp_byte(start);
    for (var i = 0; i < data.length; i++) {
        result.push(safe_xor(data[i], t));
        t = (t + 1) & 0xFF;
    }
    return result;
}

function op_add_decrement(data, start) {
    var result = [];
    var t = clamp_byte(start);
    for (var i = 0; i < data.length; i++) {
        result.push((data[i] + t) & 0xFF);
        t = (t - 1) & 0xFF;
    }
    return result;
}

function op_add_constant(data, constant) {
    var t = clamp_byte(constant);
    var result = [];
    for (var i = 0; i < data.length; i++) {
        result.push((data[i] + t) & 0xFF);
    }
    return result;
}

function op_xor_decrement(data, start) {
    var result = [];
    var t = clamp_byte(start);
    for (var i = 0; i < data.length; i++) {
        result.push(safe_xor(data[i], t));
        t = (t - 1) & 0xFF;
    }
    return result;
}

var OP_FUNCS = {
    3: op_xor_increment,
    6: op_add_decrement,
    2: op_add_constant,
    5: op_xor_decrement
};

function apply_operations(data) {
    var result = data.slice();
    for (var i = 0; i < OP_SEQUENCE.length; i += 4) {
        var func_idx = parseInt(OP_SEQUENCE.substr(i, 2), 16);
        var operand = parseInt(OP_SEQUENCE.substr(i + 2, 2), 16);
        var func = OP_FUNCS[func_idx];
        if (func) {
            result = func(result, operand);
        }
    }
    return result;
}

// ==================== XOR与加法组合 ====================

function xor_arrays(a, b) {
    if (!a || a.length === 0) return [];
    if (!b || b.length === 0) return a.slice();
    var result = [];
    for (var i = 0; i < a.length; i++) {
        result.push(safe_xor(a[i], b[i % b.length]));
    }
    return result;
}

function add_mod256_arrays(a, b) {
    if (!a || a.length === 0) return [];
    if (!b || b.length === 0) return a.slice();
    var result = [];
    for (var i = 0; i < a.length; i++) {
        result.push((a[i] + b[i % b.length]) & 0xFF);
    }
    return result;
}

// ==================== 密钥扩展 ====================

function expand_to_64(key) {
    if (!key || key.length === 0) {
        var zeros = [];
        for (var i = 0; i < 64; i++) zeros.push(0);
        return zeros;
    }
    if (key.length >= 64) return key.slice(0, 64);
    var result = [];
    for (var j = 0; j < 64; j++) {
        result.push(key[j % key.length]);
    }
    return result;
}

// ==================== 自定义Base64编码 ====================

function custom_base64_encode(data) {
    if (!data || data.length === 0) return "";
    var alphabet = CUSTOM_BASE64_ALPHABET;
    var padding = CUSTOM_BASE64_PADDING;
    var result = [];
    var i = 0;
    while (i < data.length) {
        var a = data[i];
        var b = (i + 1 < data.length) ? data[i + 1] : 0;
        var c = (i + 2 < data.length) ? data[i + 2] : 0;
        var chunk_len = Math.min(3, data.length - i);
        if (chunk_len === 3) {
            result.push(alphabet[(a >>> 2) & 0x3F]);
            result.push(alphabet[((a << 4) & 0x30) | ((b >>> 4) & 0x0F)]);
            result.push(alphabet[((b << 2) & 0x3C) | ((c >>> 6) & 0x03)]);
            result.push(alphabet[c & 0x3F]);
        } else if (chunk_len === 2) {
            result.push(alphabet[(a >>> 2) & 0x3F]);
            result.push(alphabet[((a << 4) & 0x30) | ((b >>> 4) & 0x0F)]);
            result.push(alphabet[(b << 2) & 0x3C]);
            result.push(padding);
        } else if (chunk_len === 1) {
            result.push(alphabet[(a >>> 2) & 0x3F]);
            result.push(alphabet[(a << 4) & 0x30]);
            result.push(padding);
            result.push(padding);
        }
        i += 3;
    }
    return result.join('');
}

// ==================== SHA-256风格填充 ====================

function sha256_pad(data) {
    var length = data.length;
    var padded = data.slice();
    var pad_count;
    if (length % 64 <= 60) {
        pad_count = 64 - length % 64 - 4;
    } else {
        pad_count = 128 - length % 64 - 4;
    }
    for (var i = 0; i < pad_count; i++) padded.push(0);
    var len_bytes = int_to_4bytes_be(length);
    padded.push(len_bytes[0], len_bytes[1], len_bytes[2], len_bytes[3]);
    return padded;
}

function split_blocks(data, block_size) {
    block_size = block_size || 64;
    var blocks = [];
    for (var i = 0; i < data.length; i += block_size) {
        blocks.push(data.slice(i, i + block_size));
    }
    return blocks;
}

// ==================== 核心加密函数 ====================

function encrypt_d(input_bytes, random_bytes) {
    var key_bytes = [];
    for (var k = 0; k < ENCRYPT_KEY_HEX.length; k++) {
        key_bytes.push(ENCRYPT_KEY_HEX.charCodeAt(k));
    }
    if (!random_bytes) {
        random_bytes = [];
        for (var r = 0; r < 4; r++) {
            random_bytes.push(Math.floor(Math.random() * 256));
        }
    }

    var expanded_key = expand_to_64(key_bytes);
    var expanded_random = expand_to_64(random_bytes);
    var derived_key = xor_arrays(expanded_key, expanded_random);
    derived_key = expand_to_64(derived_key);

    var crc_hex = crc32_hex(input_bytes);
    var crc_bytes = [];
    for (var ci = 0; ci < crc_hex.length; ci++) {
        crc_bytes.push(crc_hex.charCodeAt(ci));
    }

    var data_with_crc = input_bytes.slice().concat(crc_bytes);
    var padded = sha256_pad(data_with_crc);
    var blocks = split_blocks(padded, 64);

    var output = random_bytes.slice();
    var state = derived_key.slice();

    for (var bi = 0; bi < blocks.length; bi++) {
        var transformed = apply_operations(blocks[bi]);
        var xored = xor_arrays(transformed, derived_key);
        var added = add_mod256_arrays(xored, state);
        var combined = xor_arrays(added, state);
        state = sbox_substitute(sbox_substitute(combined));
        for (var si = 0; si < state.length; si++) {
            output.push(state[si]);
        }
    }

    return custom_base64_encode(output);
}

// ==================== TLV 编码 ====================

function _right_align(value_bytes, max_len) {
    return value_bytes.slice(-max_len);
}

function tlv_encode_string(tag, value, max_len) {
    var value_bytes = [];
    for (var i = 0; i < value.length; i++) {
        value_bytes.push(value.charCodeAt(i));
    }
    value_bytes = _right_align(value_bytes, max_len);
    return int_to_2bytes_be(tag).concat(int_to_2bytes_be(value_bytes.length)).concat(value_bytes);
}

function tlv_encode_int(tag, value, max_len) {
    var value_bytes = _right_align(int_to_4bytes_be(value), max_len);
    return int_to_2bytes_be(tag).concat(int_to_2bytes_be(value_bytes.length)).concat(value_bytes);
}

function tlv_encode_bool(tag, value, max_len) {
    max_len = max_len || 1;
    var val = value ? 1 : 2;
    var value_bytes = _right_align(int_to_4bytes_be(val), max_len);
    return int_to_2bytes_be(tag).concat(int_to_2bytes_be(value_bytes.length)).concat(value_bytes);
}

function tlv_encode_hex(tag, hex_str, max_len) {
    var value_bytes = hex_to_bytes(hex_str);
    value_bytes = _right_align(value_bytes, max_len);
    return int_to_2bytes_be(tag).concat(int_to_2bytes_be(value_bytes.length)).concat(value_bytes);
}

function tlv_encode_array(tag, values, lengths) {
    var result = [];
    for (var i = 0; i < values.length; i++) {
        var aligned = _right_align(int_to_4bytes_be(values[i]), lengths[i]);
        for (var j = 0; j < aligned.length; j++) result.push(aligned[j]);
    }
    return int_to_2bytes_be(tag).concat(int_to_2bytes_be(result.length)).concat(result);
}

function fisher_yates_shuffle(lst) {
    var result = lst.slice();
    var i = result.length;
    while (i > 1) {
        var r = Math.floor(Math.random() * i);
        i--;
        var tmp = result[i];
        result[i] = result[r];
        result[r] = tmp;
    }
    return result;
}

// ==================== 构建指纹数据 ====================

function build_fingerprint_data(app_id, sdk_version, version_key, session_id, access_info,
                                online_times, enc_device_id, enc_device_status,
                                collect_duration, visit_duration,
                                intranet_ip, storage_usage_kb, behavior_counts) {
    sdk_version = sdk_version || "2.0.13_yanzhengma";
    version_key = version_key || "d44593ca";
    access_info = access_info || "init:1-gts:1";
    online_times = online_times || 1;
    enc_device_status = enc_device_status || 200;
    behavior_counts = behavior_counts || {};

    if (!session_id) session_id = generate_uuid();
    if (!enc_device_id) enc_device_id = generate_uuid();
    if (!collect_duration) collect_duration = Math.floor(Math.random() * 2900) + 100;
    if (!visit_duration) visit_duration = Math.floor(Math.random() * 29000) + 1000;
    if (!intranet_ip) intranet_ip = "192.168." + (Math.floor(Math.random() * 254) + 1) + "." + (Math.floor(Math.random() * 254) + 1);
    if (!storage_usage_kb) storage_usage_kb = Math.floor(Math.random() * 49900) + 100;

    var ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36";
    var lang = "zh-CN";
    var platform_str = "Win32";
    var timestamp_ms = "" + Date.now();
    var canvas_hash = _random_hex(16);
    var webgl_hash = _random_hex(16);
    var webgl_debug = "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)";
    var font_list = "Arial,Calibri,Cambria,Consolas,Courier New,Georgia,Impact,Tahoma,Times New Roman,Trebuchet MS,Verdana";
    var audio_fp = "124.04347527516074,-100";
    var webgl_vendor = "Google Inc. (NVIDIA)";
    var webgl_renderer = "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER)";
    var timezone = "Asia/Shanghai";
    var conn_info = "4g";
    var permissions_data = "geolocation=granted,notifications=granted";
    var screen_data = "1920,1080,1920,1040,24";
    var murmur_hash = _random_hex(16);

    var entries = [];

    // --- Navigator/浏览器属性 ---
    entries.push(tlv_encode_string(200, ua, 400));
    entries.push(tlv_encode_string(201, lang, 20));
    entries.push(tlv_encode_int(202, 5, 1));
    entries.push(tlv_encode_int(203, 8, 1));
    entries.push(tlv_encode_int(206, 0, 1));
    entries.push(tlv_encode_bool(207, true));
    entries.push(tlv_encode_bool(208, true));
    entries.push(tlv_encode_bool(209, true));
    entries.push(tlv_encode_bool(210, true));
    entries.push(tlv_encode_bool(211, true));

    // --- 平台/硬件 ---
    entries.push(tlv_encode_string(213, platform_str, 10));
    entries.push(tlv_encode_string(214, "8", 15));

    // --- Canvas/WebGL 指纹哈希 ---
    entries.push(tlv_encode_hex(216, canvas_hash, 16));
    entries.push(tlv_encode_hex(217, webgl_hash, 16));

    // --- 能力检测 ---
    entries.push(tlv_encode_bool(218, true));
    entries.push(tlv_encode_bool(223, true));
    entries.push(tlv_encode_int(225, 0, 1));
    entries.push(tlv_encode_bool(228, true));
    entries.push(tlv_encode_bool(229, true));

    // --- 字体/连接/Canvas数据 ---
    entries.push(tlv_encode_string(233, font_list, 400));
    entries.push(tlv_encode_string(234, conn_info, 64));
    entries.push(tlv_encode_string(238, "", 40));
    entries.push(tlv_encode_string(239, timezone, 20));

    // --- 屏幕信息 ---
    entries.push(tlv_encode_array(242, [1920, 1080, 1920, 1040], [2, 2, 2, 2]));
    entries.push(tlv_encode_int(243, 24, 1));

    // --- 其他浏览器属性 ---
    entries.push(tlv_encode_bool(250, false));
    entries.push(tlv_encode_int(251, 0, 1));
    entries.push(tlv_encode_string(252, "Google Inc.", 100));
    entries.push(tlv_encode_string(253, "5.0 (Windows)", 30));
    entries.push(tlv_encode_int(254, 1, 1));
    entries.push(tlv_encode_string(255, webgl_vendor, 20));
    entries.push(tlv_encode_string(257, webgl_renderer, 20));
    entries.push(tlv_encode_int(258, 20030107, 1));
    entries.push(tlv_encode_int(260, 0, 4));
    entries.push(tlv_encode_int(261, 0, 1));
    entries.push(tlv_encode_string(262, "Google Inc.", 30));
    entries.push(tlv_encode_array(263, [1, 1, 1, 1, 1, 1, 1, 1], [1, 1, 1, 1, 1, 1, 1, 1]));
    entries.push(tlv_encode_int(264, 0, 1));

    // --- Audio 指纹 ---
    entries.push(tlv_encode_string(265, audio_fp, 400));
    entries.push(tlv_encode_int(267, 0, 1));

    // --- Hash字段 ---
    entries.push(tlv_encode_hex(273, _random_hex(16), 16));
    entries.push(tlv_encode_string(279, "0", 5));
    entries.push(tlv_encode_int(280, 0, 1));

    // --- WebGL debug ---
    entries.push(tlv_encode_string(283, webgl_debug, 500));
    entries.push(tlv_encode_string(284, audio_fp, 400));

    // --- Window/Screen ---
    entries.push(tlv_encode_string(500, screen_data, 100));
    entries.push(tlv_encode_int(501, 0, 1));
    entries.push(tlv_encode_array(502, [1, 1, 1920, 1080], [1, 1, 4, 4]));
    entries.push(tlv_encode_string(503, "", 32));
    entries.push(tlv_encode_string(505, "1", 3));
    entries.push(tlv_encode_bool(506, true));
    entries.push(tlv_encode_array(508, [0, 0], [4, 4]));
    entries.push(tlv_encode_string(509, "5.0 (Windows)", 30));
    entries.push(tlv_encode_string(510, "Win32", 15));
    entries.push(tlv_encode_string(511, "", 32));
    entries.push(tlv_encode_bool(512, true));
    entries.push(tlv_encode_string(513, "", 100));

    // --- Permissions ---
    entries.push(tlv_encode_string(700, permissions_data, 200));
    entries.push(tlv_encode_array(713, [0, 0], [4, 4]));

    // --- Window dimension features ---
    entries.push(tlv_encode_string(800, "1920", 8));
    entries.push(tlv_encode_string(801, "1080", 8));
    entries.push(tlv_encode_string(802, "1920", 8));
    entries.push(tlv_encode_string(803, "1040", 8));
    entries.push(tlv_encode_string(804, "2", 8));

    // --- Hash/特殊字段 ---
    entries.push(tlv_encode_hex(902, _random_hex(16), 16));
    entries.push(tlv_encode_hex(904, _random_hex(16), 16));
    entries.push(tlv_encode_hex(900, murmur_hash, 16));
    entries.push(tlv_encode_string(901, "", 200));
    entries.push(tlv_encode_bool(911, true));
    entries.push(tlv_encode_int(912, 0, 4));
    entries.push(tlv_encode_int(913, 0, 4));
    entries.push(tlv_encode_string(914, "", 100));
    entries.push(tlv_encode_string(922, "", 100));
    entries.push(tlv_encode_string(963, "", 400));
    entries.push(tlv_encode_int(964, 0, 1));

    // ==================== Vr Collection (14 entries) ====================
    entries.push(tlv_encode_string(2, app_id, 32));
    entries.push(tlv_encode_string(3, "", 32));
    entries.push(tlv_encode_string(4, sdk_version, 20));
    entries.push(tlv_encode_string(5, session_id, 32));
    entries.push(tlv_encode_string(6, timestamp_ms, 16));
    entries.push(tlv_encode_int(515, collect_duration, 4));
    entries.push(tlv_encode_int(516, visit_duration, 4));
    entries.push(tlv_encode_string(121, access_info, 32));
    entries.push(tlv_encode_string(910, intranet_ip, 400));
    entries.push(tlv_encode_int(278, storage_usage_kb, 4));
    entries.push(tlv_encode_string(3006, enc_device_id, 400));
    entries.push(tlv_encode_string(3007, session_id, 400));
    entries.push(tlv_encode_int(971, enc_device_status, 4));
    entries.push(tlv_encode_int(972, online_times, 4));

    // ==================== Wr Collection (13 entries) ====================
    entries.push(tlv_encode_int(110, behavior_counts.click || 0, 2));
    entries.push(tlv_encode_int(111, behavior_counts.keydown || 0, 2));
    entries.push(tlv_encode_int(112, behavior_counts.touchstart || 0, 2));
    entries.push(tlv_encode_int(113, behavior_counts.touchmove || 0, 2));
    entries.push(tlv_encode_int(114, behavior_counts.touchend || 0, 2));
    entries.push(tlv_encode_int(115, behavior_counts.mousedown || 0, 2));
    entries.push(tlv_encode_int(116, behavior_counts.mouseup || 0, 2));
    entries.push(tlv_encode_int(117, behavior_counts.wheel || 0, 2));
    entries.push(tlv_encode_int(118, behavior_counts.scroll || 0, 2));
    entries.push(tlv_encode_int(119, behavior_counts.pointerdown || 0, 2));
    entries.push(tlv_encode_int(120, behavior_counts.pointerup || 0, 2));
    entries.push(tlv_encode_int(967, behavior_counts.trusted_keydown || 0, 2));
    entries.push(tlv_encode_int(968, behavior_counts.untrusted_keydown || 0, 2));

    // Fisher-Yates 洗牌
    var shuffled = fisher_yates_shuffle(entries);

    // 合并所有条目
    var result = [];
    for (var si = 0; si < shuffled.length; si++) {
        for (var sj = 0; sj < shuffled[si].length; sj++) {
            result.push(shuffled[si][sj]);
        }
    }
    return result;
}

// ==================== 构建请求体 ====================

function build_request_body(app_id, sdk_version, version_key, behavior_counts) {
    sdk_version = sdk_version || "2.0.13_yanzhengma";
    version_key = version_key || "d44593ca";
    var nonce = generate_uuid();
    var input_data = build_fingerprint_data(app_id, sdk_version, version_key, null, null,
                                            1, null, 200, null, null, null, null, behavior_counts || {});
    var d = encrypt_d(input_data);
    return {
        p: app_id,
        v: sdk_version,
        vk: version_key,
        n: nonce,
        d: d
    };
}

// ==================== 原有接口 ====================

function get_cb() {
    if (typeof window._0x62692 === 'function') {
        return window._0x62692();
    }
}

function getValidateFromJsonp(jsonpStr) {
    var start = jsonpStr.indexOf('(') + 1;
    var end = jsonpStr.lastIndexOf(')');
    var jsonStr = jsonpStr.substring(start, end);
    var jsonData = JSON.parse(jsonStr);
    return jsonData.data.validate;
}

function get_encryptvalidate(data, fp) {
    var validate = getValidateFromJsonp(data);
    var _0x1f29f5 = window._0xab267f(validate, fp, 'CN31'),
        _0x37b7d4 = {};
    _0x37b7d4['verifyStatus'] = window.a0_0x1e60(425);
    _0x37b7d4['validate'] = validate;
    var _0x55aff3 = {};
    _0x55aff3[window.a0_0x1e60(1411)] = _0x1f29f5;
    return _0x55aff3[window.a0_0x1e60(1411)];
}

function get_trace_data(trace, token) {
    var trace_list = [];
    for (var i = 0; i < trace.length; i++) {
        var new_trace_data = window._0x3855dc(token, trace[i] + '');
        trace_list.push(new_trace_data);
    }
    return trace_list;
}

function get_data(trace, token, slide) {
    var trace_data = get_trace_data(trace, token);
    var _0x4a4a62 = window._0x148293["sample"](trace_data, 50);
    var _0x15278a = window._0x3ebd00(window._0x3855dc(token, slide / 320 * 100 + ''));
    var _0x4d98f2 = window._0x6e07ce(window._0x148293["unique2DArray"](trace, 2));
    return JSON["stringify"]({
        'd': window._0x3ebd00(_0x4a4a62['join'](':')),
        'm': '',
        'p': _0x15278a,
        'f': window._0x3ebd00(window._0x3855dc(token, _0x4d98f2['join'](','))),
        'ext': window._0x3ebd00(window._0x3855dc(token, 1 + ',' + trace_data['length']))
    });
}
