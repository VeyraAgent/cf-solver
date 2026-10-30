#!/usr/bin/env node
/**
 * Friendly Captcha v2 Proof-of-Work Engine (High-Performance Multi-Core Work Pool)
 * Utilizes 100% of all logical CPU cores via dynamic work-stealing.
 * Clean worker lifecycle management.
 */

const fs = require('fs');
const os = require('os');
const { Worker, isMainThread, parentPort } = require('worker_threads');

const f = new Uint32Array([
  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
  2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8,
  3, 4, 10, 12, 13, 2, 7, 14, 6, 5, 9, 0, 11, 15, 8, 1,
  10, 7, 12, 9, 14, 3, 13, 15, 4, 0, 11, 2, 5, 8, 1, 6,
  12, 13, 9, 11, 15, 10, 14, 8, 7, 2, 5, 3, 0, 1, 6, 4,
  9, 14, 11, 5, 8, 12, 15, 1, 13, 3, 0, 10, 2, 6, 4, 7,
  11, 15, 5, 0, 1, 9, 8, 6, 14, 10, 2, 12, 3, 4, 7, 13
]);

function hashRound(A, n) {
  (function(A, n) {
    let r = 1779033703, t = 3144134277, e = 1013904242, o = 2773480762,
        i = 1359893119, g = 2600822924, a = 528734635, B = 1541459225,
        I = 1779033703, C = 3144134277, c = 1013904242, Q = 2773480762,
        u = 0, s = 0, h = 64, E = 11;
    for (let l = 0; l < 112; l += 16) {
      i = (((i = ((i ^ (I = I + (u = ((u ^ (r = (r + i >>> 0) + n[f[l + 0]] >>> 0)) >>> 16 | (u ^ r) << 16) >>> 0) >>> 0)) >>> 12 | (i ^ I) << 20) >>> 0) ^ (I = I + (u = ((u ^ (r = (r + i >>> 0) + n[f[l + 1]] >>> 0)) >>> 8 | (u ^ r) << 24) >>> 0) >>> 0)) >>> 7 | (i ^ I) << 25) >>> 0;
      g = (((g = ((g ^ (C = C + (s = ((s ^ (t = (t + g >>> 0) + n[f[l + 2]] >>> 0)) >>> 16 | (s ^ t) << 16) >>> 0) >>> 0)) >>> 12 | (g ^ C) << 20) >>> 0) ^ (C = C + (s = ((s ^ (t = (t + g >>> 0) + n[f[l + 3]] >>> 0)) >>> 8 | (s ^ t) << 24) >>> 0) >>> 0)) >>> 7 | (g ^ C) << 25) >>> 0;
      a = (((a = ((a ^ (c = c + (h = ((h ^ (e = (e + a >>> 0) + n[f[l + 4]] >>> 0)) >>> 16 | (h ^ e) << 16) >>> 0) >>> 0)) >>> 12 | (a ^ c) << 20) >>> 0) ^ (c = c + (h = ((h ^ (e = (e + a >>> 0) + n[f[l + 5]] >>> 0)) >>> 8 | (h ^ e) << 24) >>> 0) >>> 0)) >>> 7 | (a ^ c) << 25) >>> 0;
      B = (((B = ((B ^ (Q = Q + (E = ((E ^ (o = (o + B >>> 0) + n[f[l + 6]] >>> 0)) >>> 16 | (E ^ o) << 16) >>> 0) >>> 0)) >>> 12 | (B ^ Q) << 20) >>> 0) ^ (Q = Q + (E = ((E ^ (o = (o + B >>> 0) + n[f[l + 7]] >>> 0)) >>> 8 | (E ^ o) << 24) >>> 0) >>> 0)) >>> 7 | (B ^ Q) << 25) >>> 0;
      g = (((g = ((g ^ (c = c + (E = ((E ^ (r = (r + g >>> 0) + n[f[l + 8]] >>> 0)) >>> 16 | (E ^ r) << 16) >>> 0) >>> 0)) >>> 12 | (g ^ c) << 20) >>> 0) ^ (c = c + (E = ((E ^ (r = (r + g >>> 0) + n[f[l + 9]] >>> 0)) >>> 8 | (E ^ r) << 24) >>> 0) >>> 0)) >>> 7 | (g ^ c) << 25) >>> 0;
      a = (((a = ((a ^ (Q = Q + (u = ((u ^ (t = (t + a >>> 0) + n[f[l + 10]] >>> 0)) >>> 16 | (u ^ t) << 16) >>> 0) >>> 0)) >>> 12 | (a ^ Q) << 20) >>> 0) ^ (Q = Q + (u = ((u ^ (t = (t + a >>> 0) + n[f[l + 11]] >>> 0)) >>> 8 | (u ^ t) << 24) >>> 0) >>> 0)) >>> 7 | (a ^ Q) << 25) >>> 0;
      B = (((B = ((B ^ (I = I + (s = ((s ^ (e = (e + B >>> 0) + n[f[l + 12]] >>> 0)) >>> 16 | (s ^ e) << 16) >>> 0) >>> 0)) >>> 12 | (B ^ I) << 20) >>> 0) ^ (I = I + (s = ((s ^ (e = (e + B >>> 0) + n[f[l + 13]] >>> 0)) >>> 8 | (s ^ e) << 24) >>> 0) >>> 0)) >>> 7 | (B ^ I) << 25) >>> 0;
      i = (((i = ((i ^ (C = C + (h = ((h ^ (o = (o + i >>> 0) + n[f[l + 14]] >>> 0)) >>> 16 | (h ^ o) << 16) >>> 0) >>> 0)) >>> 12 | (i ^ C) << 20) >>> 0) ^ (C = C + (h = ((h ^ (o = (o + i >>> 0) + n[f[l + 15]] >>> 0)) >>> 8 | (h ^ o) << 24) >>> 0) >>> 0)) >>> 7 | (i ^ C) << 25) >>> 0;
    }
    A[0] = r; A[1] = t; A[2] = e; A[3] = o; A[4] = i; A[5] = g; A[6] = a; A[7] = B;
    A[8] = I; A[9] = C; A[10] = c; A[11] = Q; A[12] = u; A[13] = s; A[14] = h; A[15] = E;
  })(A, n);
  A[0] ^= A[8];
}

function solveSub(puzzleBuf, solNum, solType, threshold) {
  const t = new Uint32Array(16);
  new Uint8Array(t.buffer).set(puzzleBuf);
  const dv = new DataView(t.buffer);
  dv.setUint8(56, solNum);
  dv.setUint8(57, solType); // 2 = WASM
  dv.setUint8(59, 0);

  const out = new Uint32Array(16);
  for (let g = 0; g < 4294967295; g++) {
    t[15] = g;
    hashRound(out, t);
    if (out[0] < threshold) {
      return Buffer.from(t.buffer, 56, 8);
    }
  }
  throw new Error("Nonce exhausted");
}

function toUrlSafeBase64(buf) {
  return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_');
}

if (!isMainThread) {
  parentPort.on('message', (msg) => {
    if (msg.type === 'task') {
      const { puzzleBuf, solNum, solType, threshold } = msg;
      const sol = solveSub(Buffer.from(puzzleBuf), solNum, solType, threshold);
      parentPort.postMessage({ type: 'result', solNum, sol });
    } else if (msg.type === 'stop') {
      process.exit(0);
    }
  });
} else {
  async function main() {
    const inputJson = fs.readFileSync(0, 'utf8');
    const params = JSON.parse(inputJson);

    const cost = params.cost;
    const numSub = 36;
    const targetPerSub = Math.max(Math.ceil(cost / numSub), 100000);
    const actualCount = Math.ceil(cost / targetPerSub);
    const threshold = Math.floor(4294967295 / targetPerSub);

    const t0 = Date.now();
    const puzzleBuf = Buffer.alloc(24);
    puzzleBuf.writeUInt32LE(Math.floor(params.solveCtxTs / 1000), 0);
    puzzleBuf.writeUInt16LE(params.solveCtxTs % 1000, 4);
    puzzleBuf.writeUInt8(1, 6);
    puzzleBuf.writeUInt8(params.solveMode !== undefined ? params.solveMode : 2, 7);
    puzzleBuf.writeUInt16LE(1, 8);
    puzzleBuf.writeUInt16LE(1, 10);
    puzzleBuf.writeUInt32LE(threshold, 12);
    puzzleBuf.writeUInt32LE(params.nonce, 16);
    const subNonce = (Math.random() * 4294967295) >>> 0;
    puzzleBuf.writeUInt32LE(subNonce, 20);

    const totalCores = os.cpus().length || 4;
    const numWorkers = Math.min(totalCores, actualCount);

    const queue = [];
    for (let i = 0; i < actualCount; i++) {
      queue.push(i);
    }

    const solutionsMap = new Map();
    let completedCount = 0;

    await new Promise((resolve, reject) => {
      const workers = [];

      function assignTask(worker) {
        if (queue.length > 0) {
          const nextSolNum = queue.shift();
          worker.postMessage({
            type: 'task',
            puzzleBuf,
            solNum: nextSolNum,
            solType: 2,
            threshold
          });
        }
      }

      for (let i = 0; i < numWorkers; i++) {
        const worker = new Worker(__filename);
        workers.push(worker);

        worker.on('message', (msg) => {
          if (msg.type === 'result') {
            solutionsMap.set(msg.solNum, Buffer.from(msg.sol));
            completedCount++;
            if (completedCount === actualCount) {
              workers.forEach(w => {
                try {
                  w.postMessage({ type: 'stop' });
                } catch (e) {}
              });
              resolve();
            } else {
              assignTask(worker);
            }
          }
        });

        worker.on('error', (err) => {
          workers.forEach(w => {
            try { w.terminate(); } catch (e) {}
          });
          reject(err);
        });

        // Start worker on first task
        assignTask(worker);
      }
    });

    const solutions = [];
    for (let i = 0; i < actualCount; i++) {
      solutions.push(solutionsMap.get(i));
    }

    const solveDuration = Date.now() - t0;
    const pB64 = toUrlSafeBase64(puzzleBuf);
    const solB64 = toUrlSafeBase64(Buffer.concat(solutions));

    let totalEvaluatedNonces = 0;
    for (const sol of solutions) {
      totalEvaluatedNonces += sol.readUInt32LE(4);
    }
    const measuredRate = Math.max(100000.0, (totalEvaluatedNonces / (solveDuration + 0.5)) * 1000);

    const diagB64 = toUrlSafeBase64(Buffer.from(JSON.stringify({
      hash_rate_wall: measuredRate,
      hash_rate_accumulated: measuredRate
    })));

    const submissionString = `${pB64}.${solB64}.${diagB64}`;

    process.stdout.write(JSON.stringify({
      submissionString,
      solveDuration
    }));
  }

  main().catch(err => {
    console.error(err);
    process.exit(1);
  });
}
