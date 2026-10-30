#!/usr/bin/env node

const http = require('http');
const https = require('https');
const crypto = require('crypto');
const url = require('url');
const querystring = require('querystring');
const zlib = require('zlib');
const { HttpProxyAgent } = require('http-proxy-agent');
const { HttpsProxyAgent } = require('https-proxy-agent');


class WafCrypto {
  static KEY = Buffer.from('6f71a512b1e035eaab53d8be73120d3fb68a0ca346b9560aab3e5cdf753d5e98', 'hex');

  static manualCrc32(data) {
    let crc = 0xffffffff;
    for (let i = 0; i < data.length; i++) {
      crc ^= data[i];
      for (let j = 0; j < 8; j++) {
        crc = (crc & 1) ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
      }
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  static encode(obj) {
    const json = JSON.stringify(obj);
    const data = Buffer.from(json);
    const crc32 = this.manualCrc32(data);
    return `${crc32.toString(16).padStart(8, '0').toUpperCase()}#${json}`;
  }

  static encrypt(plaintext) {
    const iv = crypto.randomBytes(12);
    const data = Buffer.from(plaintext);
    const cipher = crypto.createCipheriv('aes-256-gcm', WafCrypto.KEY, iv);
    const encrypted = cipher.update(data);
    cipher.final();
    const authTag = cipher.getAuthTag();

    return `${iv.toString('base64')}::${authTag.toString('hex')}::${encrypted.toString('hex')}`;
  }
}


class WafMetrics {
  static COLLECTORS = [
    ['fp2', '100', 0.4, 3.2],
    ['browser', '101', 0.1, 1.1],
    ['capabilities', '102', 2.1, 8.1],
    ['gpu', '103', 3.1, 12.1],
    ['dnt', '104', 0.0, 1.0],
    ['math', '105', 0.0, 1.0],
    ['screen', '106', 0.0, 1.0],
    ['navigator', '107', 0.0, 1.0],
    ['auto', '108', 0.0, 1.0],
    ['stealth', 'undefined', 1.1, 4.1],
    ['subtle', '110', 0.0, 1.0],
    ['canvas', '111', 85.0, 205.0],
    ['formdetector', '112', 0.0, 3.1],
    ['be', 'undefined', 0.0, 1.0]
  ];

  static r(lo, hi) {
    return Math.round((lo + Math.random() * (hi - lo)) * 10) / 10;
  }

  static build(hasToken) {
    const list = this.COLLECTORS.map(([n, mid, lo, hi]) => ({
      n,
      mid,
      v: this.r(lo, hi)
    }));

    const fp = {};
    list.forEach(item => {
      fp[item.n] = Math.floor(item.v);
    });

    const num = this.r(0.5, 3.0);
    const num2 = this.r(2.0, 8.0);
    const num3 = Math.round(list.reduce((sum, c) => sum + c.v, 0) + num + num2 + this.r(2.0, 6.0) * 10) / 10;
    const num4 = this.r(2.0, 8.0);
    const num5 = this.r(0.1, 1.0);
    const num6 = Math.round((num3 + num4 + num5) * 10) / 10;

    const met = [
      { name: '2', value: num, unit: '2' }
    ];

    list.forEach(item => {
      met.push({ name: item.mid, value: item.v, unit: '2' });
    });

    met.push({ name: '3', value: num2, unit: '2' });
    met.push({ name: '7', value: hasToken ? 1 : 0, unit: '4' });
    met.push({ name: '1', value: num3, unit: '2' });
    met.push({ name: '4', value: num4, unit: '2' });
    met.push({ name: '5', value: num5, unit: '2' });
    met.push({ name: '6', value: num6, unit: '2' });
    met.push({ name: '8', value: 1, unit: '4' });

    return { m: met, fp };
  }
}


class WafSignal {
  static PLUGINS = [
    { name: 'PDF Viewer', str: 'PDF Viewer ' },
    { name: 'Chrome PDF Viewer', str: 'Chrome PDF Viewer ' },
    { name: 'Chromium PDF Viewer', str: 'Chromium PDF Viewer ' },
    { name: 'Microsoft Edge PDF Viewer', str: 'Microsoft Edge PDF Viewer ' },
    { name: 'WebKit built-in PDF', str: 'WebKit built-in PDF ' }
  ];

  static PLUGIN_STR = this.PLUGINS.map(p => p.str).join('');

  static MATH_C = {
    tan: '-1.4214488238747245',
    sin: '0.8178819121159085',
    cos: '-0.5753861119575491'
  };

  static BASE_BINS = [
    14469, 36, 41, 46, 47, 49, 28, 22, 44, 24, 38, 15, 39, 49, 32, 42, 31, 29, 22, 33,
    32, 27, 40, 28, 47, 12, 31, 32, 42, 20, 27, 35, 118, 22, 22, 31, 22, 13, 27, 26,
    27, 17, 27, 33, 15, 29, 29, 30, 33, 32, 27, 38, 31, 16, 35, 23, 22, 24, 19, 18,
    25, 23, 20, 22, 102, 15, 22, 13, 19, 19, 18, 24, 13, 26, 10, 15, 26, 16, 14, 19,
    16, 20, 18, 26, 18, 49, 15, 19, 24, 22, 19, 17, 15, 20, 21, 22, 103, 27, 50, 38,
    55, 31, 496, 25, 19, 15, 25, 24, 18, 53, 32, 13, 19, 19, 21, 20, 29, 18, 28, 30,
    19, 15, 14, 23, 28, 12, 33, 131, 41, 35, 33, 29, 8, 15, 13, 17, 28, 33, 41, 21,
    35, 23, 26, 33, 19, 20, 74, 34, 12, 24, 15, 20, 19, 71, 20, 9, 20, 18, 22, 84,
    20, 19, 27, 7, 31, 18, 21, 24, 13, 14, 40, 20, 39, 16, 27, 24, 29, 17, 18, 27,
    16, 14, 16, 26, 13, 17, 14, 22, 20, 15, 20, 99, 15, 9, 18, 16, 15, 20, 31, 13,
    28, 35, 27, 48, 52, 48, 33, 47, 32, 47, 42, 13, 28, 21, 25, 26, 30, 25, 15, 23,
    21, 27, 24, 115, 41, 30, 16, 20, 26, 17, 24, 36, 24, 32, 24, 60, 28, 33, 25, 37,
    48, 32, 31, 26, 19, 51, 34, 50, 31, 43, 43, 53, 76, 57, 50, 13659
  ];

  static GPU_POOL = [
    {
      vendor: 'Google Inc. (NVIDIA Corporation)',
      model: 'ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 5070/PCIe/SSE2, OpenGL ES 3.2)',
      extensions: [
        'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_clip_control', 'EXT_color_buffer_half_float',
        'EXT_depth_clamp', 'EXT_disjoint_timer_query', 'EXT_float_blend', 'EXT_frag_depth',
        'EXT_polygon_offset_clamp', 'EXT_shader_texture_lod', 'EXT_texture_compression_bptc',
        'EXT_texture_compression_rgtc', 'EXT_texture_filter_anisotropic', 'EXT_texture_mirror_clamp_to_edge',
        'EXT_sRGB', 'KHR_parallel_shader_compile', 'OES_element_index_uint', 'OES_fbo_render_mipmap',
        'OES_standard_derivatives', 'OES_texture_float', 'OES_texture_float_linear', 'OES_texture_half_float',
        'OES_texture_half_float_linear', 'OES_vertex_array_object', 'WEBGL_blend_func_extended',
        'WEBGL_color_buffer_float', 'WEBGL_compressed_texture_astc', 'WEBGL_compressed_texture_etc',
        'WEBGL_compressed_texture_etc1', 'WEBGL_compressed_texture_s3tc', 'WEBGL_compressed_texture_s3tc_srgb',
        'WEBGL_debug_renderer_info', 'WEBGL_debug_shaders', 'WEBGL_depth_texture', 'WEBGL_lose_context',
        'WEBGL_multi_draw', 'WEBGL_polygon_mode'
      ]
    },
    {
      vendor: 'Google Inc. (Microsoft)',
      model: 'ANGLE (Microsoft, Microsoft Basic Render Driver (0x0000008C) Direct3D11 vs_5_0 ps_5_0, D3D11)',
      extensions: [
        'ANGLE_instanced_arrays', 'EXT_blend_minmax', 'EXT_clip_control', 'EXT_color_buffer_half_float',
        'EXT_depth_clamp', 'EXT_float_blend', 'EXT_frag_depth', 'EXT_polygon_offset_clamp',
        'EXT_shader_texture_lod', 'EXT_texture_compression_bptc', 'EXT_texture_compression_rgtc',
        'EXT_texture_filter_anisotropic', 'EXT_texture_mirror_clamp_to_edge', 'EXT_sRGB',
        'KHR_parallel_shader_compile', 'OES_element_index_uint', 'OES_fbo_render_mipmap',
        'OES_standard_derivatives', 'OES_texture_float', 'OES_texture_float_linear', 'OES_texture_half_float',
        'OES_texture_half_float_linear', 'OES_vertex_array_object', 'WEBGL_blend_func_extended',
        'WEBGL_color_buffer_float', 'WEBGL_compressed_texture_s3tc', 'WEBGL_compressed_texture_s3tc_srgb',
        'WEBGL_debug_renderer_info', 'WEBGL_debug_shaders', 'WEBGL_depth_texture', 'WEBGL_draw_buffers',
        'WEBGL_lose_context', 'WEBGL_multi_draw', 'WEBGL_polygon_mode'
      ]
    }
  ];

  static randCanvas() {
    const list = this.BASE_BINS.map(num => {
      if (num > 500) return num + Math.floor(Math.random() * 401) - 200;
      if (num > 80) return num + Math.floor(Math.random() * 31) - 15;
      return Math.max(1, num + Math.floor(Math.random() * 7) - 3);
    });
    const hash = Math.floor(Math.random() * 900000000) + 100000000;
    return { hash, bins: list };
  }

  static build(site, fp, ua) {
    const now = Date.now();
    const gpu = this.GPU_POOL[Math.floor(Math.random() * this.GPU_POOL.length)];
    const { hash, bins } = this.randCanvas();

    return {
      metrics: fp,
      start: now,
      flashVersion: null,
      plugins: this.PLUGINS,
      dupedPlugins: this.PLUGIN_STR + '||1920-1080-1080-24-*-*-*',
      screenInfo: '1920-1080-1080-24-*-*-*',
      referrer: '',
      userAgent: ua,
      location: site,
      webDriver: false,
      capabilities: {
        css: {
          textShadow: 1,
          WebkitTextStroke: 1,
          boxShadow: 1,
          borderRadius: 1,
          borderImage: 1,
          opacity: 1,
          transform: 1,
          transition: 1
        },
        js: {
          audio: true,
          geolocation: true,
          localStorage: 'supported',
          touch: false,
          video: true,
          webWorker: true
        },
        elapsed: fp.capabilities || 4
      },
      gpu,
      dnt: null,
      math: this.MATH_C,
      automation: {
        wd: { properties: { document: [], window: [], navigator: [] } },
        phantom: { properties: { window: [] } }
      },
      stealth: { t1: 0, t2: 0, i: 1, mte: 0, mtd: false },
      crypto: {
        crypto: 1,
        subtle: 1,
        encrypt: true,
        decrypt: true,
        wrapKey: true,
        unwrapKey: true,
        sign: true,
        verify: true,
        digest: true,
        deriveBits: true,
        deriveKey: true,
        getRandomValues: true,
        randomUUID: true
      },
      canvas: {
        hash,
        emailHash: null,
        histogramBins: bins
      },
      formDetected: false,
      numForms: 0,
      numFormElements: 0,
      be: { si: false },
      end: now + 1,
      errors: [],
      version: '2.4.0',
      id: crypto.randomUUID()
    };
  }
}


class HttpClient {
  constructor(proxy = null) {
    this.cookies = {};
    this.proxy = proxy;
    this.proxyAgent = null;
    this.proxyAgentSecure = null;

    if (proxy) {
      this.proxyAgent = new HttpProxyAgent(proxy);
      this.proxyAgentSecure = new HttpsProxyAgent(proxy);
    }
  }

  setCookie(cookieHeader) {
    if (!cookieHeader) return;
    const parts = cookieHeader.split(';');
    const [key, value] = parts[0].split('=');
    if (key && value) {
      this.cookies[key.trim()] = value.trim();
    }
  }

  getCookieString() {
    return Object.entries(this.cookies)
      .map(([k, v]) => `${k}=${v}`)
      .join('; ');
  }

  request(uri, options = {}) {
    return new Promise((resolve, reject) => {
      const isHttps = uri.startsWith('https');
      const client = isHttps ? https : http;
      const parsed = new URL(uri);

      const opts = {
        hostname: parsed.hostname,
        port: parsed.port || (isHttps ? 443 : 80),
        path: parsed.pathname + parsed.search,
        method: options.method || 'GET',
        headers: options.headers || {},
        rejectUnauthorized: false,
        timeout: 30000
      };

      if (this.proxy) {
        opts.agent = isHttps ? this.proxyAgentSecure : this.proxyAgent;
      }

      const cookieStr = this.getCookieString();
      if (cookieStr) {
        opts.headers['Cookie'] = cookieStr;
      }

      if (options.body) {
        opts.headers['Content-Length'] = Buffer.byteLength(options.body);
      }

      const req = client.request(opts, (res) => {
        const chunks = [];
        res.on('data', chunk => chunks.push(chunk));
        res.on('end', () => {
          if (res.headers['set-cookie']) {
            const setCookies = Array.isArray(res.headers['set-cookie'])
              ? res.headers['set-cookie']
              : [res.headers['set-cookie']];
            setCookies.forEach(sc => this.setCookie(sc));
          }
          
          let buffer = Buffer.concat(chunks);
          const encoding = res.headers['content-encoding'];
          
          try {
            if (encoding === 'gzip') {
              buffer = zlib.gunzipSync(buffer);
            } else if (encoding === 'deflate') {
              buffer = zlib.inflateSync(buffer);
            } else if (encoding === 'br') {
              if (zlib.brotliDecompressSync) {
                buffer = zlib.brotliDecompressSync(buffer);
              }
            }
          } catch (e) {
            console.error(`[DEBUG] Decompression error: ${e.message}`);
          }

          const data = buffer.toString('utf8');
          resolve({ status: res.statusCode, headers: res.headers, body: data });
        });
      });

      req.on('error', (e) => {
        console.error(`[DEBUG] Request error: ${e.message}`);
        reject(e);
      });
      req.on('timeout', () => { req.destroy(); reject(new Error('Request timeout')); });

      if (options.body) {
        req.write(options.body);
      }
      req.end();
    });
  }
}

class WafSolver {
  static UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36';
  static ENDPOINTS = {
    'ha9faaffd31b4d5ede2a2e19d2d7fd525f66fee61911511960dcbb52d3c48ce25': 'mp_verify',
    'h72f957df656e80ba55f5d8ce2e8c7ccb59687dba3bfb273d54b08a261b2f3002': 'verify',
    'h7b0c470f0cfe3a80a9e26526ad185f484f6817d0832712a4a37a908786a6a67f': 'verify',
    'SHA256': 'verify',
    'HashcashScrypt': 'verify',
    'NetworkBandwidth': 'mp_verify'
  };
  static BW_SIZES = { 1: 1024, 2: 10240, 3: 102400, 4: 1048576, 5: 10485760 };

  static async discover(baseUrl, ua, client, log = console.log) {
    try {
      log(`[*] Discovering challenge for: ${baseUrl}`);
      const response = await client.request(baseUrl, {
        method: 'GET',
        headers: {
          'User-Agent': ua,
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/^*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9'
        }
      });

      const html = response.body;
      if (response.status !== 200 && response.status !== 202) {
        log(`[!] Page returned status ${response.status}, searching for challenge anyway...`);
      }

      let chalUrl = null;
      let isSameOrigin = false;
      let gokuProps = null;
      let cookieDomains = [];

      // Extract gokuProps if present (Crucial for 202 challenge pages)
      const gokuMatch = html.match(/window\.gokuProps\s*=\s*({[\s\S]*?});/);
      if (gokuMatch) {
        try {
          gokuProps = JSON.parse(gokuMatch[1]);
          log(`[+] Found gokuProps in HTML`);
        } catch (e) {}
      }

      // Extract awsWafCookieDomainList if present
      const domainListMatch = html.match(/window\.awsWafCookieDomainList\s*=\s*(\[[^\]]*\]);/);
      if (domainListMatch) {
        try {
          cookieDomains = JSON.parse(domainListMatch[1]);
        } catch (e) {}
      }

      // 1. Search in HTML
      const sameMatch = html.match(/(\/__challenge_[A-Za-z0-9]+\/[a-f0-9]+\/[a-f0-9]+)/);
      if (sameMatch) {
        chalUrl = baseUrl + sameMatch[1];
        isSameOrigin = true;
      } else {
        const extMatch = html.match(/(https:\/\/[a-z0-9-.]+\.token\.awswaf\.com\/[^\s"'>]+)/);
        if (extMatch) {
          chalUrl = extMatch[1].replace(/\/challenge\.js$/, '');
        } else {
          const sdkMatch = html.match(/(https:\/\/[a-z0-9-.]+\.(?:edge\.)?sdk\.awswaf\.com\/[a-z0-9]+\/[a-z0-9]+)\/challenge\.js/);
          if (sdkMatch) {
            chalUrl = sdkMatch[1];
          }
        }
      }

      // 2. If not found, search in linked JS files (Crucial for Turnitin/SPA)
      if (!chalUrl) {
        log(`[*] Not found in HTML, scanning JS resources...`);
        const jsFiles = html.match(/src="([^"]+\.js)"/g) || [];
        for (const jsTag of jsFiles) {
          const jsUrl = jsTag.match(/"([^"]+)"/)[1];
          const fullJsUrl = jsUrl.startsWith('http') ? jsUrl : baseUrl + (jsUrl.startsWith('/') ? '' : '/') + jsUrl;
          try {
            const jsRes = await client.request(fullJsUrl, { headers: { 'User-Agent': ua } });
            const jsContent = jsRes.body;
            const jsExtMatch = jsContent.match(/(https:\/\/[a-z0-9-.]+\.(?:token|sdk)\.awswaf\.com\/[^\s"']+\/[^\s"']+\/[^\s"']+)/);
            if (jsExtMatch) {
              chalUrl = jsExtMatch[1].replace(/\/challenge\.js$/, '');
              break;
            }
          } catch (e) { /* ignore JS fetch errors */ }
        }
      }

      if (!chalUrl) {
        throw new Error(`Challenge URL not found automatically. Site may be using a highly obfuscated WAF implementation.`);
      }

      log(`[+] Found Challenge URL: ${chalUrl}`);
      return { chalUrl, isSameOrigin, gokuProps, cookieDomains };
    } catch (err) {
      throw err;
    }
  }

  static checkZeros(hash, diff) {
    let zeros = 0;
    for (let byte of hash) {
      if (byte === 0) {
        zeros += 8;
      } else {
        for (let i = 7; i >= 0; i--) {
          if ((byte & (1 << i)) === 0) {
            zeros++;
          } else {
            return zeros >= diff;
          }
        }
        break;
      }
    }
    return zeros >= diff;
  }

  static solvePow(input, checksum, diff, ctype, mem) {
    if (ctype === 'SHA256' || this.ENDPOINTS[ctype] === 'verify' || !ctype) {
      const baseData = input + checksum;
      for (let i = 0; i < 100000000; i++) {
        const data = Buffer.concat([Buffer.from(baseData), Buffer.from(i.toString())]);
        const hash = crypto.createHash('sha256').update(data).digest();
        if (this.checkZeros(hash, diff)) {
          return i.toString();
        }
      }
    }
    return '0';
  }

  static solveBw(diff) {
    const size = this.BW_SIZES[diff] || 1024;
    return Buffer.alloc(size).toString('base64');
  }

  static buildMultipart(domain, challenge, solData, checksum, encrypted, metrics, goku) {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let boundary = '----WebKitFormBoundary';
    for (let i = 0; i < 16; i++) {
      boundary += chars[Math.floor(Math.random() * chars.length)];
    }

    const metadata = {
      challenge,
      solution: null,
      signals: [{ name: 'Zoey', value: { Present: encrypted } }],
      checksum,
      existing_token: null,
      client: 'Browser',
      domain,
      metrics
    };
    if (goku) {
      metadata.goku_props = goku;
    }

    const metadataJson = JSON.stringify(metadata);

    const parts = [];
    const crlf = '\r\n';
    const delim = `--${boundary}`;

    parts.push(delim + crlf);
    parts.push(`Content-Disposition: form-data; name="solution_data"${crlf}${crlf}`);
    parts.push(solData + crlf);

    parts.push(delim + crlf);
    parts.push(`Content-Disposition: form-data; name="solution_metadata"${crlf}${crlf}`);
    parts.push(metadataJson + crlf);

    parts.push(delim + '--' + crlf);

    const body = parts.join('');

    return {
      body,
      contentType: `multipart/form-data; boundary=${boundary}`
    };
  }

  static parseUa(ua) {
    const match = ua.match(/Chrome\/(\d+)/);
    const version = match ? match[1] : '144';
    const brands = [
      `"Not/A)Brand";v="8", "Chromium";v="${version}", "Google Chrome";v="${version}"`,
      `"Not A(Brand";v="24", "Chromium";v="${version}", "Google Chrome";v="${version}"`,
      `"Chromium";v="${version}", "Not(A:Brand";v="24", "Google Chrome";v="${version}"`,
      `"Not:A-Brand";v="8", "Chromium";v="${version}", "Google Chrome";v="${version}"`
    ];
    const platform = ua.includes('Windows') ? 'Windows' : 'Linux';
    return {
      brand: brands[parseInt(version) % 4],
      platform
    };
  }

  static getHeaders(site, ua, isSameOrigin) {
    const { brand, platform } = this.parseUa(ua);
    return {
      'accept': '*/*',
      'accept-language': 'en-US,en;q=0.9',
      'cache-control': 'no-cache',
      'ect': '4g',
      'origin': site,
      'pragma': 'no-cache',
      'priority': 'u=1, i',
      'referer': site + '/',
      'sec-ch-ua': brand,
      'sec-ch-ua-mobile': '?0',
      'sec-ch-ua-platform': `"${platform}"`,
      'sec-fetch-dest': 'empty',
      'sec-fetch-mode': 'cors',
      'sec-fetch-site': isSameOrigin ? 'same-origin' : 'cross-site',
      'user-agent': ua
    };
  }

  static async solve(targetUrl, ua = this.UA, log = console.log, proxy = null, directChalUrl = null) {
    const site = targetUrl.replace(/\/$/, '');
    const domain = new URL(site).hostname;
    const startTime = Date.now();


    let effectiveProxy = proxy;

    if (!effectiveProxy && process.env.PROXY) {
      effectiveProxy = process.env.PROXY;
      log(`[*] Using proxy from PROXY env: ${effectiveProxy.substring(0, 40)}...`);
    }

    if (!effectiveProxy && process.env.PROXY_IP && process.env.PROXY_PORT) {
      const ip = process.env.PROXY_IP;
      const port = process.env.PROXY_PORT;
      const user = process.env.PROXY_USER || '';
      const pass = process.env.PROXY_PASS || '';

      if (user && pass) {
        effectiveProxy = `http://${user}:${pass}@${ip}:${port}`;
      } else {
        effectiveProxy = `http://${ip}:${port}`;
      }
      log(`[*] Using proxy from PROXY_IP/PORT/USER/PASS env`);
    }

    if (effectiveProxy) {
      log(`[*] Proxy: ${effectiveProxy.substring(0, 50)}...`);
    }

    const client = new HttpClient(effectiveProxy);
    const silentLog = () => {}; // Silent logging for intermediate steps

    const delayMs = parseInt(process.env.SOLVER_DELAY || '100');
    if (delayMs > 0) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }

    try {
      let chalUrl, isSameOrigin, gokuProps = null, cookieDomains = [];
      if (directChalUrl) {
        chalUrl = directChalUrl.replace(/\/challenge\.js$/, '');
        isSameOrigin = chalUrl.includes(domain);
        log(`[*] Using direct challenge URL: ${chalUrl}`);
      } else {
        const discovery = await this.discover(targetUrl, ua, client, log);
        chalUrl = discovery.chalUrl;
        isSameOrigin = discovery.isSameOrigin;
        gokuProps = discovery.gokuProps;
        cookieDomains = discovery.cookieDomains;
      }
      const effectiveDomain = (cookieDomains && cookieDomains.length > 0) ? cookieDomains[0].replace(/^\./, '') : domain;
      const headers = this.getHeaders(site, ua, isSameOrigin);
      let token = null;

      for (let round = 0; round < 2; round++) {
        const hasToken = round > 0;
        const { m: metrics, fp } = WafMetrics.build(hasToken);
        metrics.push({ name: 'SignalAcquisitionTime', value: Math.random() * 100 + 50, unit: 'Milliseconds' });
        metrics.push({ name: 'InputsApiLatency', value: Math.random() * 200 + 100, unit: 'Milliseconds' });
        metrics.push({ name: 'ChallengeExpiredRetryBucket', value: 1, unit: '2' });
        const signal = WafSignal.build(site + '/', fp, ua);
        const encoded = WafCrypto.encode(signal);
        const checksum = encoded.split('#')[0];
        const encrypted = WafCrypto.encrypt(encoded);

        let inputEndpoint = chalUrl;
        if (!inputEndpoint.includes('/inputs')) {
          inputEndpoint = `${chalUrl}/inputs?client=browser`;
        }
        const inputResponse = await client.request(inputEndpoint, {
          method: 'GET',
          headers
        });

        if (inputResponse.status !== 200) {
          throw new Error(`Input fetch failed (${inputResponse.status}): ${inputResponse.body.substring(0, 200)}`);
        }

        let inputData;
        try {
          inputData = JSON.parse(inputResponse.body);
        } catch (e) {
          throw new Error(`Invalid JSON from inputs endpoint: ${e.message}`);
        }

        const challenge = inputData.challenge;
        if (!challenge || !challenge.input) {
          throw new Error(`Invalid challenge structure: ${JSON.stringify(inputData).substring(0, 200)}`);
        }

        // In the new AWS WAF version, challenge_type and difficulty are at the root level
        const chalType = inputData.challenge_type || '';
        const difficulty = inputData.difficulty || 1;
        const memory = inputData.memory || 128;
        // challenge.input is now an opaque token, no longer a base64 JSON

        let body, contentType;
        const isBw = chalType === 'NetworkBandwidth' || this.ENDPOINTS[chalType] === 'mp_verify';
        if (isBw) {
          const solution = this.solveBw(difficulty);
          const multipart = this.buildMultipart(effectiveDomain, challenge, solution, checksum, encrypted, metrics, gokuProps);
          body = multipart.body;
          contentType = multipart.contentType;
          log(`[DEBUG] Multipart body length: ${Buffer.byteLength(body)}, Content-Type: ${contentType}`);
        } else {
          const solution = this.solvePow(challenge.input, checksum, difficulty, chalType, memory);
          const payload = {
            challenge,
            solution,
            signals: [{ name: 'Zoey', value: { Present: encrypted } }],
            checksum,
            existing_token: token,
            client: 'Browser',
            domain: effectiveDomain,
            metrics
          };
          if (gokuProps) {
            payload.goku_props = gokuProps;
          }
          body = JSON.stringify(payload);
          contentType = 'text/plain;charset=UTF-8';
        }

        const endpoint = this.ENDPOINTS[chalType] || 'verify';
        let verifyEndpoint = `${chalUrl}/${endpoint}`;
        if (chalUrl.includes('/inputs')) {
          verifyEndpoint = chalUrl.replace(/\/inputs(\?.*)?$/, `/${endpoint}`);
        }
        const contentLength = Buffer.byteLength(body);

        const finalHeaders = {
          ...headers,
          'Content-Type': contentType,
          'Content-Length': contentLength
        };

        const verifyResponse = await client.request(verifyEndpoint, {
          method: 'POST',
          headers: finalHeaders,
          body
        });

        let retries = 0;
        const maxRetries = 3;

        while (retries <= maxRetries && (verifyResponse.status === 400 || verifyResponse.status === 429)) {
          if (retries > 0) {
            const waitTime = 1500 * Math.pow(2, retries - 1); // 1.5s, 3s, 6s
            log(`[*] Got ${verifyResponse.status}, retrying in ${waitTime}ms (attempt ${retries}/${maxRetries})`);
            await new Promise(resolve => setTimeout(resolve, waitTime));

            const { m: newMetrics, fp: newFp } = WafMetrics.build(hasToken);
            newMetrics.push({ name: 'SignalAcquisitionTime', value: Math.random() * 100 + 50, unit: 'Milliseconds' });
            newMetrics.push({ name: 'InputsApiLatency', value: Math.random() * 200 + 100, unit: 'Milliseconds' });
            newMetrics.push({ name: 'ChallengeExpiredRetryBucket', value: 1, unit: '2' });
            const newSignal = WafSignal.build(site + '/', newFp, ua);
            const newEncoded = WafCrypto.encode(newSignal);
            const newChecksum = newEncoded.split('#')[0];
            const newEncrypted = WafCrypto.encrypt(newEncoded);

            let retryBody, retryContentType;
            const isRetryBw = chalType === 'NetworkBandwidth' || this.ENDPOINTS[chalType] === 'mp_verify';
            if (isRetryBw) {
              const solution = this.solveBw(difficulty);
              const multipart = this.buildMultipart(effectiveDomain, challenge, solution, newChecksum, newEncrypted, newMetrics, gokuProps);
              retryBody = multipart.body;
              retryContentType = multipart.contentType;
            } else {
              const solution = this.solvePow(challenge.input, newChecksum, difficulty, chalType, memory);
              const payload = {
                challenge,
                solution,
                signals: [{ name: 'Zoey', value: { Present: newEncrypted } }],
                checksum: newChecksum,
                existing_token: token,
                client: 'Browser',
                domain: effectiveDomain,
                metrics: newMetrics
              };
              if (gokuProps) {
                payload.goku_props = gokuProps;
              }
              retryBody = JSON.stringify(payload);
              retryContentType = 'text/plain;charset=UTF-8';
            }

            const retryResponse = await client.request(`${chalUrl}/${endpoint}`, {
              method: 'POST',
              headers: {
                ...headers,
                'Content-Type': retryContentType,
                'Content-Length': Buffer.byteLength(retryBody)
              },
              body: retryBody
            });

            if (retryResponse.status !== 400 && retryResponse.status !== 429) {
              verifyResponse.status = retryResponse.status;
              verifyResponse.body = retryResponse.body;
              verifyResponse.headers = retryResponse.headers;
              break;
            }
          }
          retries++;
        }

        if (verifyResponse.status !== 200 && verifyResponse.status !== 201) {
          log(`[-] Verify failed with status ${verifyResponse.status}`);
          log(`[-] Response: ${verifyResponse.body.substring(0, 200)}`);
          throw new Error(`Verify failed with status ${verifyResponse.status}: ${verifyResponse.body.substring(0, 200)}`);
        }

        let verifyData;
        try {
          const responseText = verifyResponse.body.trim();
          if (!responseText) {
            throw new Error('Empty response');
          }
          verifyData = JSON.parse(responseText);
        } catch (e) {
          throw new Error(`Invalid JSON from verify endpoint: ${e.message}`);
        }

        token = verifyData.token || token;
      }

      const elapsed = ((Date.now() - startTime) / 1000).toFixed(2);

      if (!token) {
        throw new Error('No token received from WAF');
      }

      return `${elapsed}:${token}`;
    } catch (err) {
      throw err;
    }
  }
}


const server = http.createServer(async (req, res) => {
  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.pathname;
  const query = parsedUrl.query;

  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  res.setHeader('Access-Control-Allow-Private-Network', 'true');

  if (req.method === 'OPTIONS') {
    res.writeHead(200, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Private-Network': 'true'
    });
    res.end();
    return;
  }

  if (pathname === '/solve') {
    const targetUrl = query.url || query.target;
    let proxyUrl = null;


    if (query.proxy) {
      const proxyParts = query.proxy.split(':');
      if (proxyParts.length === 4) {
        const [ip, port, user, pass] = proxyParts;
        proxyUrl = `http://${user}:${pass}@${ip}:${port}`;
      } else if (proxyParts.length === 2) {
        const [ip, port] = proxyParts;
        proxyUrl = `http://${ip}:${port}`;
      } else {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid proxy format. Use: ip:port or ip:port:user:pass' }));
        return;
      }
    } else if (process.env.PROXY) {
      proxyUrl = process.env.PROXY;
    } else if (process.env.PROXY_IP && process.env.PROXY_PORT) {
      const ip = process.env.PROXY_IP;
      const port = process.env.PROXY_PORT;
      const user = process.env.PROXY_USER || '';
      const pass = process.env.PROXY_PASS || '';

      if (user && pass) {
        proxyUrl = `http://${user}:${pass}@${ip}:${port}`;
      } else {
        proxyUrl = `http://${ip}:${port}`;
      }
    }

    const directChalUrl = query.chal; // New parameter to pass challenge URL directly

    // Security: validate URL format
    if (targetUrl && !/^https?:\/\//i.test(targetUrl)) {
      res.writeHead(400, { 'Content-Type': 'application/json', 'X-Powered-By': '@Real_zezomod' });
      res.end(JSON.stringify({ author: '@Real_zezomod', success: false, error: 'URL must start with http:// or https://' }));
      return;
    }

    // Security: validate URL format
    if (targetUrl && !/^https?:\/\//i.test(targetUrl)) {
      res.writeHead(400, { 'Content-Type': 'application/json', 'X-Powered-By': '@Real_zezomod' });
      res.end(JSON.stringify({ author: '@Real_zezomod', success: false, error: 'URL must start with http:// or https://' }));
      return;
    }

    if (!targetUrl) {
      res.writeHead(400, { 'Content-Type': 'application/json', 'X-Powered-By': '@Real_zezomod' });
      res.end(JSON.stringify({ author: '@Real_zezomod', success: false, error: 'Missing "url" parameter' }));
      return;
    }

    try {
      console.log(`[*] Received request for: ${targetUrl}`);
      const result = await WafSolver.solve(targetUrl, WafSolver.UA, (msg) => {}, proxyUrl, directChalUrl);
      const colonIndex = result.indexOf(':');
      const elapsed = result.substring(0, colonIndex);
      const token = result.substring(colonIndex + 1);
      console.log(`    [+] Solved in ${elapsed}s`);
      res.writeHead(200, { 'Content-Type': 'application/json', 'X-Powered-By': '@Real_zezomod' });
      res.end(JSON.stringify({ author: '@Real_zezomod', success: true, token: token, elapsed: elapsed + 's' }));
    } catch (error) {
      res.writeHead(500, { 'Content-Type': 'application/json', 'X-Powered-By': '@Real_zezomod' });
      res.end(JSON.stringify({ author: '@Real_zezomod', success: false, error: error.message }));
    }
  } else if (pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>AWS WAF Solver</title>
        <style>
          body { font-family: Arial; margin: 40px; background: #f5f5f5; }
          .container { background: white; padding: 20px; border-radius: 8px; max-width: 600px; }
          input { width: 100%; padding: 10px; margin: 10px 0; font-size: 14px; }
          button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
          button:hover { background: #0056b3; }
          .result { margin-top: 20px; padding: 10px; background: #f8f9fa; border-radius: 4px; word-break: break-all; }
          .error { color: #dc3545; }
          .success { color: #28a745; }
          .loading { color: #ffc107; }
        </style>
      </head>
      <body>
        <div class="container">
          <h1>AWS WAF Solver</h1>
          <p>Enter a URL to solve the WAF challenge and get the token.</p>
          <input type="text" id="urlInput" placeholder="https://example.com/login" value="">
          <button onclick="solveWAF()">Solve WAF</button>
          <div id="result"></div>
        </div>
        <script>
          async function solveWAF() {
            const url = document.getElementById('urlInput').value;
            const resultDiv = document.getElementById('result');

            if (!url) {
              resultDiv.innerHTML = '<p class="error">Please enter a URL</p>';
              return;
            }

            resultDiv.innerHTML = '<p class="loading"> Solving... This may take a moment...</p>';

            try {
              const response = await fetch('/solve?url=' + encodeURIComponent(url));
              const token = await response.text();

              if (response.ok) {
                resultDiv.innerHTML = '<h3 class="success">✓ Token:</h3><div class="result">' + token + '</div>';
              } else {
                resultDiv.innerHTML = '<p class="error">Error: ' + token + '</p>';
              }
            } catch (error) {
              resultDiv.innerHTML = '<p class="error">Error: ' + error.message + '</p>';
            }
          }

          document.getElementById('urlInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') solveWAF();
          });
        </script>
      </body>
      </html>
    `);
  } else {
    res.writeHead(404, { 'Content-Type': 'application/json', 'X-Powered-By': '@Real_zezomod' });
    res.end(JSON.stringify({ author: '@Real_zezomod', error: 'Not found' }));
  }
});

const PORT = process.env.PORT || 3090;
server.listen(PORT, () => {
  console.log('\n===========================================================');
  console.log(' AWS WAF Solver Running on Port ' + PORT + ' - by @Real_zezomod');
  console.log('===========================================================\n');
  console.log('Access the solver:');
  console.log('   Browser:  http://localhost:' + PORT);
  console.log('   API:      http://localhost:' + PORT + '/solve?url=<URL>');
  console.log('Example:');
  console.log('   http://localhost:' + PORT + '/solve?url=https://example.com/login\n');
});

process.on('SIGINT', () => {
  console.log('\n\n[*] Server shutting down...');
  process.exit(0);
});
