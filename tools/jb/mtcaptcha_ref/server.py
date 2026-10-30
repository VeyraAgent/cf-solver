import argparse
import time
from flask import Flask, request, jsonify
from mtcaptcha_client import MTCaptchaSolver, extract_domain

app = Flask(__name__)

# Add CORS headers to every response
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Access-Control-Allow-Private-Network'] = 'true'
    response.headers['X-Powered-By'] = '@Real_zezomod'
    return response

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'online',
        'solver': 'MTCaptcha Automated Solver',
        'author': '@Real_zezomod',
        'endpoints': {
            'POST /solve': 'Solve MTCaptcha challenge',
            'GET /solve': 'Solve via query params',
            'GET /health': 'Check solver server status'
        }
    }), 200

@app.route('/solve', methods=['GET', 'POST', 'OPTIONS'])
def solve_endpoint():
    if request.method == 'OPTIONS':
        return '', 200

    # Extract parameters from JSON body or GET query params
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form.to_dict() or {}
    else:
        data = request.args.to_dict()

    sitekey = data.get('sitekey') or data.get('sk')
    url = data.get('url') or data.get('pageurl') or data.get('host') or data.get('bd') or data.get('domain')
    action = data.get('action') or data.get('act') or '$'
    lang = data.get('lang') or data.get('lg') or 'en'
    proxy = data.get('proxy')
    max_retries = int(data.get('max_retries', data.get('retries', 10)))
    custom_solution = data.get('solution') or data.get('custom_solution')

    if not sitekey:
        return jsonify({
            'ok': False,
            'status': 'error',
            'error': 'Missing required parameter: sitekey (or sk)',
            'author': '@Real_zezomod'
        }), 400

    if not url:
        return jsonify({
            'ok': False,
            'status': 'error',
            'error': 'Missing required parameter: url (or pageurl / host / domain)',
            'author': '@Real_zezomod'
        }), 400

    start_time = time.time()
    try:
        solver = MTCaptchaSolver(
            sitekey=sitekey,
            pageurl=url,
            action=action,
            lang=lang,
            proxy=proxy
        )
        
        if custom_solution:
            result = solver.solve(custom_solution=custom_solution)
            attempts = 1
        else:
            result = solver.solve_until_verified(max_retries=max_retries)
            attempts = result.get('attempts', 1)

        elapsed = round(time.time() - start_time, 2)

        if result.get('isVerified'):
            return jsonify({
                'ok': True,
                'status': 'success',
                'verified_token': result.get('verifiedToken'),
                'token': result.get('verifiedToken'),
                'solution_text': result.get('solutionText'),
                'attempts': attempts,
                'solve_time': elapsed,
                'sitekey': sitekey,
                'domain': solver.host_domain,
                'author': '@Real_zezomod'
            }), 200
        else:
            return jsonify({
                'ok': False,
                'status': 'fail',
                'error': 'Failed to verify captcha within maximum retries',
                'attempts': attempts,
                'solve_time': elapsed,
                'author': '@Real_zezomod',
                'details': result.get('response')
            }), 502

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return jsonify({
            'ok': False,
            'status': 'error',
            'error': str(e),
            'solve_time': elapsed,
            'author': '@Real_zezomod'
        }), 500

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MTCaptcha Solver HTTP API Server')
    parser.add_argument('--port', type=int, default=3050, help='Port to listen on (default: 3050)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind (default: 0.0.0.0)')
    args = parser.parse_args()

    print('=' * 60)
    print('       MTCaptcha Automated Solver API Server')
    print('                  @Real_zezomod')
    print('=' * 60)
    print(f'[*] Server running on: http://localhost:{args.port}')
    print(f'[*] Endpoints:')
    print(f'    - POST http://localhost:{args.port}/solve')
    print(f'    - GET  http://localhost:{args.port}/solve?sitekey=...&url=...')
    print(f'    - GET  http://localhost:{args.port}/health')
    print('=' * 60)

    app.run(host=args.host, port=args.port, threaded=True)
