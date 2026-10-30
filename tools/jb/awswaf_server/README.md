# AWS WAF Challenge Solver API & Web Interface

High-performance, automated AWS WAF Challenge & Token solver built on Node.js. Automatically discovers challenges, emulates browser signals and hardware telemetry, solves Proof-of-Work (PoW) and NetworkBandwidth challenges, binds dynamic `gokuProps`, and generates valid `aws-waf-token` cookies.

**Author:** [@Real_zezomod](https://t.me/Real_zezomod)  
**Channel:** [t.me/arabconfigs](https://t.me/arabconfigs)

---

## Features

- **Automated Challenge Discovery**: Automatically inspects HTML, 202 Challenge pages, and linked JavaScript bundles (supports SPAs like Angular, React, Vue).
- **Dynamic `gokuProps` Binding**: Automatically extracts and signs the dynamic cryptographic context (`key`, `iv`, `context`) required by modern AWS WAF distributions.
- **Full Algorithm Support**:
  - SHA256 / Hashcash PoW
  - NetworkBandwidth challenges via Multipart form data (`mp_verify`)
  - Modern obfuscated 65-character challenge hashes (`ha9faaff...`, `h72f957...`, `h7b0c47...`)
- **Realistic Telemetry & Evasion**:
  - Canvas, WebGL, GPU, and Audio metrics
  - AES-256-GCM + CRC32 signal encryption using the `Zoey` identifier
  - Real-time latency emulation (`SignalAcquisitionTime`, `InputsApiLatency`, `ChallengeExpiredRetryBucket`)
- **Dual Interface**:
  - Clean Web GUI for manual testing in your browser
  - Lightweight REST API for automated tooling (e.g., Python, C#, OpenBullet, SilverBullet, curl)
- **Proxy Support**: Direct proxy routing via query parameter or environment variables (HTTP/HTTPS, auth supported).

---

## Prerequisites

- **Node.js**: v18.0.0 or higher (tested on Node.js v18, v20, v22, v24).
- **npm**: Installed with Node.js.

---

## Installation

1. Open your terminal / command prompt in the project directory:
   ```bash
   cd "AWS WAF SOLVER"
   ```

2. Install the required dependencies:
   ```bash
   npm install
   ```

### Dependencies
Defined in `package.json`:
- `https-proxy-agent` (`^9.1.0`): Handles HTTPS outbound proxy tunneling.
- `http-proxy-agent` (`^9.1.0`): Handles HTTP outbound proxy tunneling.
- Standard built-in Node.js libraries: `http`, `https`, `crypto`, `url`, `querystring`, `zlib`.

---

## Running the Solver

Start the server using:
```bash
node waf_solver.js
```

By default, the server listens on port **`3090`**.

### Custom Port
You can specify a custom port using the `PORT` environment variable:

- **Linux / macOS:**
  ```bash
  PORT=8080 node waf_solver.js
  ```
- **Windows (PowerShell):**
  ```powershell
  $env:PORT="8080"; node waf_solver.js
  ```
- **Windows (CMD):**
  ```cmd
  set PORT=8080 && node waf_solver.js
  ```

---

## Usage

### 1. Web GUI (Browser Interface)
Once the server is running, navigate to:
```
http://localhost:3090
```
Paste your target URL into the input field and click **Solve WAF**. The solved `aws-waf-token` will appear directly on screen.

---

### 2. REST API Endpoint

#### Endpoint:
```http
GET /solve?url=<TARGET_URL>
```

#### Query Parameters:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | **Yes** | The full URL of the protected page (e.g. `https://example.com/login`). |
| `proxy` | string | Optional | Proxy in `ip:port` or `ip:port:user:pass` format. |
| `chal` | string | Optional | Manual Challenge Base URL fallback (if auto-discovery is bypassed). |

#### Example 1: Basic Request
```bash
curl "http://localhost:3090/solve?url=https://www.turnitin.com/login_page.asp?lang=en_us"
```

#### Example 2: Request with Proxy
```bash
curl "http://localhost:3090/solve?url=https://www.turnitin.com/login_page.asp&proxy=127.0.0.1:8080:username:password"
```

#### Example Response (`200 OK`):
```json
{
  "author": "@Real_zezomod",
  "success": true,
  "token": "897c2def-321a-4261-81f3-8a548fba8e2e:CQoAtYIx0jNjHAAA:ofEdrtF0DkiJhVLPoTCiAiYx1xQKC3K8KMxVNLKUvz5k54PYs6lFjaxtTEKKJLoqSmn...",
  "elapsed": "0.68s"
}
```

---

## How to Use the Generated Token

Pass the returned token in the `Cookie` header of your subsequent requests to the target website:

```http
GET /protected-page HTTP/1.1
Host: www.target-site.com
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36
Cookie: aws-waf-token=897c2def-321a-4261-81f3-8a548fba8e2e:CQoAtYIx0jNjHAAA:...
```

---

## Manual Fallback: Extracting the Challenge URL via Network Tab

In 99% of cases, the solver automatically extracts the challenge URL and dynamic tokens. If you are dealing with a heavily customized or obfuscated single-page application and want to supply the challenge URL manually via the `&chal=` parameter, follow these steps:

1. **Open Developer Tools**:
   - Open your browser (Google Chrome, Microsoft Edge, or Firefox).
   - Press **F12** (or `Ctrl + Shift + I` / `Cmd + Option + I` on macOS).

2. **Configure the Network Tab**:
   - Click on the **Network** tab at the top.
   - Check the **Preserve log** checkbox.
   - In the filter search bar, type:
     ```text
     awswaf
     ```
     *(or filter by `token.awswaf.com`)*

3. **Trigger the Challenge**:
   - Navigate to the protected page (or refresh with `Ctrl + F5`).
   - Look through the filtered requests in the list.

4. **Locate the Challenge URL**:
   - Look for a request URL matching one of these patterns:
     - `https://<hash>.<region>.token.awswaf.com/<id1>/<id2>/challenge.js`
     - `https://<hash>.<region>.token.awswaf.com/<id1>/<id2>/<id3>/challenge.js`
     - `https://<hash>.<region>.sdk.awswaf.com/<id1>/<id2>/challenge.js`

5. **Copy the Base URL**:
   - Right-click the request -> **Copy** -> **Copy URL**.
   - Strip `/challenge.js` from the end (the solver handles both with or without `/challenge.js` automatically).
   - Example base URL:
     ```text
     https://d6c86bf421eb.7cb16d03.eu-central-1.token.awswaf.com/d6c86bf421eb/19f56c6ee507/baddf11a6a77
     ```

6. **Pass to the Solver**:
   Append it to your API call using the `chal` parameter:
   ```text
   http://localhost:3090/solve?url=https://example.com/login&chal=https://d6c86bf421eb.7cb16d03.eu-central-1.token.awswaf.com/d6c86bf421eb/19f56c6ee507/baddf11a6a77
   ```

---

## Credits & Rights

- **Developer:** [@Real_zezomod](https://t.me/Real_zezomod)
- **Official Telegram Channel:** [t.me/arabconfigs](https://t.me/arabconfigs)
