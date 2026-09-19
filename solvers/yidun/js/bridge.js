// bridge.js — one-shot op dispatcher for the vendored Yidun crypto JS.
//
// Usage: node bridge.js <op> '<json-args>'
//   ops:
//     init            {hostname}              -> {cb, fp}
//     cb              {}                      -> {cb}
//     fp              {hostname}              -> {fp}
//     data            {trace, token, slide}   -> {data}          (get_data)
//     encryptvalidate {result, fp}            -> {validate}      (get_encryptvalidate)
//     encrypt_d       {input, random_bytes}   -> {d}             (deterministic vector / cross-check)
//     request_body    {app_id}                -> {body}          (build_request_body)
//
// The heavy lifting (cb / data / encryptValidate) lives in the obfuscated
// vendor bundle (webpack.js) — porting it to Python is out of scope; we run it
// verbatim and parse the sentinel-prefixed JSON result out. Vendored from
// CodeEmpower/yidun-silder.
const fs = require('fs');
const path = require('path');

if (typeof window === 'undefined') {
    globalThis.window = globalThis;
}

// Vendor bundle + fp.js spray console.log noise (getElementById debug, an fp
// string printed at load, …) — silence it and emit the result behind a
// sentinel so the Python side can always find it regardless of interleaving.
const _realLog = console.log;
console.log = function () {};

// Top-level function/var declarations attach to this module's scope.
const _srcEncrypt = fs.readFileSync(path.join(__dirname, 'encrypt.js'), 'utf8');
const _srcFp = fs.readFileSync(path.join(__dirname, 'fp.js'), 'utf8');
const _splitAt = "// ==================== 基础工具函数 ====================";
eval(_srcEncrypt.slice(0, _srcEncrypt.indexOf(_splitAt))); // require("./webpack") first
eval(_srcEncrypt.slice(_srcEncrypt.indexOf(_splitAt)));
eval(_srcFp);

function die(msg) {
    _realLog("__YIDUN_BRIDGE__" + JSON.stringify({ error: String(msg) }));
    process.exit(1);
}

const op = process.argv[2];
let args = {};
try {
    args = JSON.parse(process.argv[3] || '{}');
} catch (e) {
    die('bad args json: ' + e.message);
}

try {
    let out;
    switch (op) {
        case 'init':
            out = { cb: get_cb(), fp: fp(args.hostname || 'dun.163.com') };
            break;
        case 'cb':
            out = { cb: get_cb() };
            break;
        case 'fp':
            out = { fp: fp(args.hostname || 'dun.163.com') };
            break;
        case 'data':
            out = { data: get_data(args.trace, args.token, args.slide) };
            break;
        case 'encryptvalidate':
            out = { validate: get_encryptvalidate(args.result, args.fp) };
            break;
        case 'encrypt_d':
            out = { d: encrypt_d(args.input, args.random_bytes) };
            break;
        case 'request_body':
            out = { body: build_request_body(args.app_id) };
            break;
        default:
            die('unknown op: ' + op);
    }
    _realLog("__YIDUN_BRIDGE__" + JSON.stringify(out));
} catch (e) {
    die(op + ' failed: ' + (e && e.stack ? e.stack : e));
}
