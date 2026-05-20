import atexit
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, make_response
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

import database
import data_fetcher
import auth as auth_module

app = Flask(__name__)
CORS(app, supports_credentials=True)

database.init_db()

SESSION_COOKIE = 'st_session'
SESSION_DAYS   = 30


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return jsonify({'error': 'Not authenticated'}), 401
        session = database.get_session_user(token)
        if not session:
            return jsonify({'error': 'Session expired'}), 401
        return f(*args, **kwargs)
    return decorated


def _set_session_cookie(response, token):
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,
        samesite='Lax',
        max_age=SESSION_DAYS * 24 * 3600,
    )
    return response


# ---------------------------------------------------------------------------
# Auth routes  (public)
# ---------------------------------------------------------------------------

@app.route('/api/auth/register', methods=['POST'])
def register():
    body       = request.json or {}
    identifier = (body.get('email') or '').strip().lower()
    password   = body.get('password') or ''

    if not identifier or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    existing = database.get_user_by_identifier(identifier)
    if existing and existing.get('password_hash'):
        return jsonify({'error': 'An account with this email already exists'}), 400

    pw_hash = auth_module.hash_password(password)
    if existing:
        database.set_user_password(identifier, pw_hash)
    else:
        database.get_or_create_user(identifier)
        database.set_user_password(identifier, pw_hash)

    user  = database.get_user_by_identifier(identifier)
    token = auth_module.generate_session_token()
    exp   = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat()
    database.create_session(token, user['id'], identifier, exp)

    resp = make_response(jsonify({'success': True, 'user': {'email': identifier}}))
    return _set_session_cookie(resp, token)


@app.route('/api/auth/login', methods=['POST'])
def login():
    body       = request.json or {}
    identifier = (body.get('email') or '').strip().lower()
    password   = body.get('password') or ''

    if not identifier or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    user = database.get_user_by_identifier(identifier)
    if not user or not user.get('password_hash'):
        return jsonify({'error': 'No account found. Please register first.'}), 401
    if not auth_module.verify_password(password, user['password_hash']):
        return jsonify({'error': 'Incorrect password'}), 401

    database.get_or_create_user(identifier)
    token = auth_module.generate_session_token()
    exp   = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat()
    database.create_session(token, user['id'], identifier, exp)

    resp = make_response(jsonify({'success': True, 'user': {'email': identifier}}))
    return _set_session_cookie(resp, token)


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return jsonify({'error': 'Not authenticated'}), 401
    session = database.get_session_user(token)
    if not session:
        return jsonify({'error': 'Session expired'}), 401
    return jsonify({'email': session['identifier']})


@app.route('/api/auth/change-password', methods=['POST'])
@require_auth
def change_password():
    body     = request.json or {}
    password = body.get('password') or ''
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    session = database.get_session_user(request.cookies.get(SESSION_COOKIE))
    database.set_user_password(session['identifier'], auth_module.hash_password(password))
    return jsonify({'success': True})


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        database.delete_session(token)
    resp = make_response(jsonify({'success': True}))
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Background refresh
# ---------------------------------------------------------------------------

def refresh_all_data():
    watchlist = database.get_watchlist()
    print(f"[scheduler] Refreshing {len(watchlist)} symbol(s)...")
    for item in watchlist:
        symbol = item['symbol']
        data = data_fetcher.get_asset_data(symbol)
        if data:
            database.save_asset_data(symbol, data)
            print(f"[scheduler]   OK  {symbol}")
        else:
            print(f"[scheduler]   ERR {symbol}")


scheduler = BackgroundScheduler()
scheduler.add_job(func=refresh_all_data, trigger='interval', hours=1, id='hourly_refresh')
scheduler.start()
atexit.register(lambda: scheduler.shutdown())


# ---------------------------------------------------------------------------
# Protected API routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')


@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)


@app.route('/test')
def test_page():
    return send_from_directory('templates', 'test.html')


@app.route('/api/watchlist', methods=['GET'])
@require_auth
def get_watchlist():
    watchlist = database.get_watchlist()
    all_data  = database.get_all_asset_data()
    result = []
    for item in watchlist:
        sym        = item['symbol']
        asset_data = all_data.get(sym, {})
        result.append({**item, **asset_data})
    return jsonify(result)


@app.route('/api/watchlist', methods=['POST'])
@require_auth
def add_to_watchlist():
    body   = request.json or {}
    symbol = (body.get('symbol') or '').upper().strip()
    if not symbol:
        return jsonify({'error': 'symbol is required'}), 400

    existing = [w['symbol'] for w in database.get_watchlist()]
    if symbol in existing:
        return jsonify({'error': f'{symbol} is already in your watchlist'}), 400

    data = data_fetcher.get_asset_data(symbol)
    if not data:
        return jsonify({'error': f'Could not find data for "{symbol}". Check the ticker symbol.'}), 404

    database.add_to_watchlist(symbol, data.get('name', symbol), data.get('asset_type', 'EQUITY'))
    database.save_asset_data(symbol, data)
    return jsonify({'success': True, 'data': data})


@app.route('/api/watchlist/<symbol>', methods=['DELETE'])
@require_auth
def remove_from_watchlist(symbol):
    database.remove_from_watchlist(symbol.upper())
    return jsonify({'success': True})


@app.route('/api/watchlist/<symbol>', methods=['PATCH'])
@require_auth
def update_watchlist_item(symbol):
    body = request.json or {}
    if 'themes' in body:
        themes = [t.strip() for t in body['themes'] if isinstance(t, str) and t.strip()]
        database.update_themes(symbol.upper(), themes)
    return jsonify({'success': True})


@app.route('/api/data/<symbol>', methods=['GET'])
@require_auth
def get_data(symbol):
    sym  = symbol.upper()
    data = database.get_asset_data(sym)
    if not data:
        data = data_fetcher.get_asset_data(sym)
        if data:
            database.save_asset_data(sym, data)
    if not data:
        return jsonify({'error': 'Symbol not found'}), 404
    return jsonify(data)


@app.route('/api/refresh', methods=['POST'])
@require_auth
def refresh_all():
    refresh_all_data()
    return jsonify({'success': True})


@app.route('/api/refresh/<symbol>', methods=['POST'])
@require_auth
def refresh_symbol(symbol):
    sym  = symbol.upper()
    data = data_fetcher.get_asset_data(sym)
    if data:
        database.save_asset_data(sym, data)
        return jsonify({'success': True, 'data': data})
    return jsonify({'error': 'Failed to refresh data'}), 500


@app.route('/api/search', methods=['GET'])
@require_auth
def search_tickers():
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    results = data_fetcher.search_tickers(q)
    return jsonify(results)


@app.route('/api/competitors/<symbol>', methods=['GET'])
@require_auth
def get_competitors(symbol):
    competitors = data_fetcher.get_competitors(symbol.upper())
    return jsonify(competitors)


if __name__ == '__main__':
    app.run(debug=True, port=3001, use_reloader=False)
