import atexit
import io as _io
import json
import os
import re as _re
import sys as _sys
import threading as _threading
import time
import traceback
import types as _types
import requests as _req
import yfinance as _yf
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, make_response, g
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
        g.session_user = session
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
    watchlist = database.get_all_watched_symbols()
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
    user_id  = g.session_user['user_id']
    watchlist = database.get_watchlist(user_id)
    all_data  = database.get_all_asset_data()
    symbols   = [item['symbol'] for item in watchlist]

    # Join in trades by this user's favorited politicians, grouped by ticker
    pol_map = {}
    try:
        for tr in database.get_politician_trades_for_user(user_id, symbols):
            pol_map.setdefault(tr['ticker'], []).append({
                'name':    tr['politician_name'],
                'type':    tr['type'],
                'date':    tr['transaction_date'],
                'amount':  tr['amount'],
                'url':     tr['filing_url'],
                'chamber': tr['chamber'],
            })
    except Exception as e:
        print(f'[politicians] watchlist join failed: {e}')

    result = []
    for item in watchlist:
        sym        = item['symbol']
        asset_data = all_data.get(sym, {})
        result.append({**item, **asset_data, 'politicians': pol_map.get(sym, [])})
    return jsonify(result)


@app.route('/api/watchlist', methods=['POST'])
@require_auth
def add_to_watchlist():
    user_id = g.session_user['user_id']
    body    = request.json or {}
    symbol  = (body.get('symbol') or '').upper().strip()
    if not symbol:
        return jsonify({'error': 'symbol is required'}), 400

    existing = [w['symbol'] for w in database.get_watchlist(user_id)]
    if symbol in existing:
        return jsonify({'error': f'{symbol} is already in your watchlist'}), 400

    data = data_fetcher.get_asset_data(symbol)
    if not data:
        return jsonify({'error': f'Could not find data for "{symbol}". Check the ticker symbol.'}), 404

    database.add_to_watchlist(user_id, symbol, data.get('name', symbol), data.get('asset_type', 'EQUITY'))
    database.save_asset_data(symbol, data)
    return jsonify({'success': True, 'data': data})


@app.route('/api/watchlist/<symbol>', methods=['DELETE'])
@require_auth
def remove_from_watchlist(symbol):
    user_id = g.session_user['user_id']
    database.remove_from_watchlist(user_id, symbol.upper())
    return jsonify({'success': True})


@app.route('/api/watchlist/<symbol>', methods=['PATCH'])
@require_auth
def update_watchlist_item(symbol):
    user_id = g.session_user['user_id']
    body = request.json or {}
    if 'themes' in body:
        themes = [t.strip() for t in body['themes'] if isinstance(t, str) and t.strip()]
        database.update_themes(user_id, symbol.upper(), themes)
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


@app.route('/api/debug/fetch/<symbol>', methods=['GET'])
def debug_fetch(symbol):
    secret = os.environ.get('ADMIN_SECRET')
    if not secret or request.args.get('key') != secret:
        return jsonify({'error': 'Unauthorized'}), 401
    results = {}
    sym = symbol.upper()

    # Test 1: ticker.info
    try:
        t = _yf.Ticker(sym)
        info = t.info or {}
        results['ticker_info'] = {
            'keys': len(info),
            'quoteType': info.get('quoteType'),
            'currentPrice': info.get('currentPrice'),
            'shortName': info.get('shortName'),
        }
    except Exception as e:
        results['ticker_info'] = {'error': str(e)}

    # Test 2: crumb
    try:
        session, crumb = data_fetcher._get_yahoo_session()
        results['crumb'] = crumb[:10] + '...' if crumb else None
        results['session_ok'] = session is not None
    except Exception as e:
        results['crumb'] = {'error': str(e)}

    # Test 3: quoteSummary
    try:
        qs = data_fetcher._quotesummary_fallback(sym)
        results['quotesummary'] = {
            'got_data': qs is not None,
            'currentPrice': qs.get('currentPrice') if qs else None,
            'trailingPE': qs.get('trailingPE') if qs else None,
        }
    except Exception as e:
        results['quotesummary'] = {'error': str(e), 'trace': traceback.format_exc()}

    # Test 4: fast_info
    try:
        t2 = yf.Ticker(sym)
        fi = t2.fast_info
        results['fast_info'] = {
            'last_price': getattr(fi, 'last_price', None),
            'market_cap': getattr(fi, 'market_cap', None),
        }
    except Exception as e:
        results['fast_info'] = {'error': str(e)}

    return jsonify(results)


@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    secret = os.environ.get('ADMIN_SECRET')
    if not secret:
        return jsonify({'error': 'Admin not configured'}), 403
    if request.args.get('key') != secret:
        return jsonify({'error': 'Unauthorized'}), 401
    users = database.get_all_users()
    watchlist_counts = {}
    for u in users:
        wl = database.get_watchlist(u['id'])
        watchlist_counts[u['id']] = len(wl)
    return jsonify([{
        'id':         u['id'],
        'email':      u['identifier'],
        'joined':     str(u['created_at']),
        'last_login': str(u['last_login']) if u['last_login'] else None,
        'watchlist':  watchlist_counts[u['id']],
    } for u in users])


# ---------------------------------------------------------------------------
# Politicians / congressional trades  (via House Clerk official disclosures)
# ---------------------------------------------------------------------------

# pdfminer.six is installed but needs cryptography; we fake it since PTR PDFs are unencrypted
def _ensure_fake_crypto():
    if 'cryptography' not in _sys.modules:
        class _FO:
            def __init__(self, *a, **kw): pass
            def __call__(self, *a, **kw): return _FO()
            def __getattr__(self, n): return _FO()
        for _m in [
            'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.backends',
            'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.ciphers',
            'cryptography.hazmat.primitives.ciphers.algorithms',
            'cryptography.hazmat.primitives.ciphers.modes',
            'cryptography.hazmat.primitives.padding',
            'cryptography.hazmat.primitives.hashes',
        ]:
            _sys.modules[_m] = _types.ModuleType(_m)
        _sys.modules['cryptography.hazmat.backends'].default_backend = _FO
        _sys.modules['cryptography.hazmat.primitives.ciphers'].Cipher = _FO
        _sys.modules['cryptography.hazmat.primitives.ciphers.algorithms'].AES = _FO
        _sys.modules['cryptography.hazmat.primitives.ciphers.algorithms'].ARC4 = _FO
        _sys.modules['cryptography.hazmat.primitives.ciphers.modes'].CBC = _FO
        _sys.modules['cryptography.hazmat.primitives.padding'].PKCS7 = _FO
        _sys.modules['cryptography.hazmat.primitives.hashes'].MD5 = _FO

# Install the shim and import pdfminer at module load time so there is nothing
# left to lazily import when requests come in or background threads run.
_ensure_fake_crypto()
from pdfminer.high_level import extract_text as _pdf_extract_text

_HOUSE_BASE = 'https://disclosures-clerk.house.gov'
_HOUSE_SEARCH = _HOUSE_BASE + '/FinancialDisclosure/ViewMemberSearchResult'
_HOUSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Content-Type': 'application/x-www-form-urlencoded',
}

_pol_cache    = {'data': None, 'ts': 0}
_pol_lock     = _threading.Lock()
_pol_fetching = False
_POL_TTL      = 43200  # 12 hours

# Tracked politicians are persisted per-user in the database (see database.py).
# The legacy JSON file is migrated into the DB once at startup, then retired.
_TRACKED_FILE = os.path.join(os.path.dirname(__file__), 'tracked_politicians.json')


def _pol_key(*parts):
    """Normalize a politician's name into a stable join key, order-independent.

    Works for both ('Nancy', 'Pelosi') and a full display name like
    'Hon. Pelosi, Nancy' — both collapse to 'nancy_pelosi'.
    """
    text = ' '.join(p for p in parts if p)
    text = _re.sub(r'\b(hon|mr|mrs|ms|dr)\b\.?', ' ', text, flags=_re.I)
    tokens = sorted(t for t in _re.sub(r'[^a-z\s]', ' ', text.lower()).split() if len(t) > 1)
    return '_'.join(tokens)

# Pre-compiled regex to parse House Clerk PTR filing rows from HTML
_ROW_PAT = _re.compile(
    r'<a href="(public_disc/ptr-pdfs/\d+/(\d+)\.pdf)"[^>]*>([^<]+)</a>'
    r'.*?<td[^>]*>([^<]+)</td>'
    r'.*?<td[^>]*>([^<]+)</td>'
    r'.*?<td[^>]*>([^<]+)</td>',
    _re.DOTALL,
)


def _search_house_clerk(last_name='', first_name=''):
    """Return list of (doc_id, display_name, link) for PTR filings matching the given name."""
    filings = []
    seen = set()
    for year in [str(datetime.now().year), str(datetime.now().year - 1)]:
        try:
            r = _req.post(
                _HOUSE_SEARCH,
                data=f'LastName={last_name}&FirstName={first_name}&FilingYear={year}&State=&District=&FDType=P',
                headers=_HOUSE_HEADERS, timeout=30,
            )
            if not r.ok:
                continue
            for m in _ROW_PAT.finditer(r.text):
                link, doc_id, raw_name = m.group(1), m.group(2), m.group(3).strip()
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                clean  = _re.sub(r'Hon\.\s*\.?\s*', '', raw_name).strip()
                parts  = [p.strip() for p in clean.split(',', 1)]
                display = f'{parts[1]} {parts[0]}' if len(parts) == 2 else clean
                filings.append((doc_id, display, link))
        except Exception as e:
            print(f'[politicians] Search error ({last_name}): {e}')
    return filings


def _extract_pdf_text(pdf_bytes):
    """Extract plain text from a PDF using pdfminer.six with a fake crypto shim."""
    try:
        return _pdf_extract_text(_io.BytesIO(pdf_bytes))
    except Exception as e:
        print(f'[politicians] PDF extract error: {e}')
        return ''


def _parse_ptr_text(text, politician_name, doc_id, politician_key=None):
    """Parse pdfminer text from a House PTR into a list of trade dicts."""
    trades = []
    pkey = politician_key or _pol_key(politician_name)
    # Strip null bytes that pdfminer leaves from wide-char fonts
    text = text.replace('\x00', '')
    text = _re.sub(r'[ \t]+', ' ', text)

    # Each transaction block contains:
    #   (<TICKER>) [OP/ST/...] P/S/E  MM/DD/YYYY  MM/DD/YYYY  $X - $Y
    # Works for both compact (one line) and multi-line PDF layout formats.
    pat = _re.compile(
        r'\(([A-Z][A-Z0-9\.\-]{0,9})\)'          # (TICKER)
        r'\s*\[[A-Z]+\]'                            # [OP] / [ST] / etc
        r'\s*(P|S\s*\(partial\)|S\s*\(full\)|S|E)' # transaction type
        r'\s*(\d{2}/\d{2}/\d{4})'                  # transaction date
        r'\s*\d{2}/\d{2}/\d{4}'                    # notification date (ignore)
        r'\s*(\$[\d,]+\s*-\s*\$[\d,]+|Over \$[\d,]+|[\$\d,]+ or less)',  # amount
        _re.DOTALL
    )
    for m in pat.finditer(text):
        ticker    = m.group(1)
        raw_type  = m.group(2).strip()
        raw_date  = m.group(3)
        amount    = m.group(4).strip()

        if raw_type == 'P':
            trade_type = 'purchase'
        elif 'partial' in raw_type:
            trade_type = 'sale_partial'
        elif 'full' in raw_type:
            trade_type = 'sale_full'
        elif raw_type == 'E':
            trade_type = 'exchange'
        else:
            trade_type = 'sale'

        try:
            d = datetime.strptime(raw_date, '%m/%d/%Y')
            iso_date = d.strftime('%Y-%m-%d')
        except Exception:
            iso_date = raw_date

        trades.append({
            'chamber':          'House',
            'politician':       politician_name,
            'politician_key':   pkey,
            'ticker':           ticker,
            'asset_description': '',
            'type':             trade_type,
            'amount':           amount.replace('\n', '').strip(),
            'transaction_date': iso_date,
            'disclosure_date':  iso_date,
            'owner':            'self',
            'filing_url':       f'{_HOUSE_BASE}/public_disc/ptr-pdfs/{iso_date[:4]}/{doc_id}.pdf',
        })
    return trades


def _fetch_pol_background():
    """Download and parse PTR PDFs; runs in a background thread."""
    global _pol_fetching
    now = time.time()

    with _pol_lock:
        if _pol_fetching:
            return
        if _pol_cache['data'] is not None and now - _pol_cache['ts'] < _POL_TTL:
            return
        _pol_fetching = True

    try:
        print('[politicians] Starting fresh fetch from House Clerk…')

        # Targeted fetch for the union of every user's tracked politicians
        try:
            tracked = database.get_all_tracked_distinct()
        except Exception as e:
            print(f'[politicians] Could not load tracked list: {e}')
            tracked = []

        tracked_filings = []
        seen_docs = set()
        for pol in tracked:
            for filing in _search_house_clerk(pol['last_name'], pol.get('first_name', '')):
                if filing[0] not in seen_docs:
                    seen_docs.add(filing[0])
                    tracked_filings.append(filing)

        # General fetch (all PTRs), fill up to 25 after deduping against tracked
        general_filings = _search_house_clerk()
        general_extra = [f for f in general_filings if f[0] not in seen_docs][:25]
        for f in general_extra:
            seen_docs.add(f[0])

        all_to_download = tracked_filings + general_extra
        print(f'[politicians] Downloading {len(tracked_filings)} tracked + {len(general_extra)} general filings…')

        trades = []
        for doc_id, name, link in all_to_download:
            try:
                r = _req.get(f'{_HOUSE_BASE}/{link}',
                             headers={'User-Agent': _HOUSE_HEADERS['User-Agent']},
                             timeout=20)
                if r.ok:
                    text = _extract_pdf_text(r.content)
                    if text:
                        # Key is derived from the actual filer's name so the watchlist
                        # join only matches genuine filings for a tracked politician
                        # (a last-name search also returns unrelated same-surname filers).
                        parsed = _parse_ptr_text(text, name, doc_id)
                        trades.extend(parsed)
                        print(f'[politicians]  {name}: {len(parsed)} trades')
                time.sleep(0.15)
            except Exception as e:
                print(f'[politicians]  skip {doc_id}: {e}')

        trades.sort(key=lambda x: x['transaction_date'], reverse=True)
        print(f'[politicians] Done – {len(trades)} total trades cached.')
        try:
            database.save_politician_trades(trades)
        except Exception as e:
            print(f'[politicians] Failed to persist trades: {e}')
        with _pol_lock:
            _pol_cache['data'] = trades
            _pol_cache['ts']   = time.time()
    finally:
        with _pol_lock:
            _pol_fetching = False


def _fetch_pol():
    """Return cached data immediately; kick off background refresh if needed."""
    now = time.time()
    with _pol_lock:
        fresh = (_pol_cache['data'] is not None and now - _pol_cache['ts'] < _POL_TTL)
        fetching = _pol_fetching

    if not fresh and not fetching:
        t = _threading.Thread(target=_fetch_pol_background, daemon=True)
        t.start()

    with _pol_lock:
        return _pol_cache['data'] or []


def _migrate_tracked_json():
    """One-time: copy the legacy global JSON list into every user's tracked set."""
    if not os.path.exists(_TRACKED_FILE):
        return
    # Skip if anyone is already tracking — prevents re-adding removed politicians
    # on redeploys where the ephemeral filesystem loses the .migrated rename.
    try:
        if database.get_all_tracked_distinct():
            return
    except Exception:
        return
    try:
        with open(_TRACKED_FILE) as f:
            entries = json.load(f)
    except Exception as e:
        print(f'[politicians] Could not read legacy tracked file: {e}')
        return

    users = database.get_all_users()
    if not users:
        return  # no users yet — leave the file so migration runs once accounts exist

    for u in users:
        for e in entries:
            last  = (e.get('last_name') or '').strip()
            first = (e.get('first_name') or '').strip()
            if not last:
                continue
            display = e.get('display_name') or (f'{first} {last}'.strip())
            database.add_tracked_politician(u['id'], first, last, display, _pol_key(first, last))

    try:
        os.rename(_TRACKED_FILE, _TRACKED_FILE + '.migrated')
    except Exception:
        pass
    print(f'[politicians] Migrated {len(entries)} tracked politician(s) to {len(users)} user(s).')


def _preload_politicians():
    """Warm the in-memory cache from the DB, then refresh from source in the background."""
    try:
        cached = database.get_all_politician_trades(2000)
        if cached:
            with _pol_lock:
                _pol_cache['data'] = cached
                _pol_cache['ts']   = 0   # serve immediately, but force a fresh fetch
            print(f'[politicians] Warmed cache with {len(cached)} persisted trades.')
    except Exception as e:
        print(f'[politicians] Cache warm failed: {e}')
    _threading.Thread(target=_fetch_pol_background, daemon=True).start()


# Migrate legacy data, preload at startup in the background, and refresh every 12 h
_migrate_tracked_json()
_preload_politicians()
scheduler.add_job(func=_fetch_pol, trigger='interval', hours=12, id='pol_refresh')


@app.route('/api/politicians', methods=['GET'])
@require_auth
def get_politicians():
    try:
        data = _fetch_pol()
        with _pol_lock:
            loading = _pol_fetching
        return jsonify({'trades': data[:2000], 'loading': loading})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/politicians/tracked', methods=['GET'])
@require_auth
def get_tracked_politicians():
    user_id = g.session_user['user_id']
    return jsonify({'tracked': database.get_tracked_politicians(user_id)})


@app.route('/api/politicians/tracked', methods=['POST'])
@require_auth
def add_tracked_politician():
    user_id = g.session_user['user_id']
    body  = request.get_json(force=True, silent=True) or {}
    first = (body.get('first_name') or '').strip()
    last  = (body.get('last_name')  or '').strip()
    if not last:
        return jsonify({'error': 'last_name is required'}), 400
    display = f'{first} {last}'.strip() if first else last
    key     = _pol_key(first, last)
    entry   = {'first_name': first, 'last_name': last, 'display_name': display, 'politician_key': key}

    if not database.add_tracked_politician(user_id, first, last, display, key):
        return jsonify({'error': 'Already tracking this politician'}), 409

    # Invalidate cache so the next fetch picks up their filings
    with _pol_lock:
        _pol_cache['ts'] = 0
    _threading.Thread(target=_fetch_pol_background, daemon=True).start()

    return jsonify({'tracked': entry}), 201


@app.route('/api/politicians/tracked', methods=['DELETE'])
@require_auth
def remove_tracked_politician():
    user_id = g.session_user['user_id']
    body  = request.get_json(force=True, silent=True) or {}
    first = (body.get('first_name') or '').strip()
    last  = (body.get('last_name')  or '').strip()
    if not last:
        return jsonify({'error': 'last_name is required'}), 400

    if not database.remove_tracked_politician(user_id, _pol_key(first, last)):
        return jsonify({'error': 'Not found'}), 404

    return jsonify({'ok': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3001))
    app.run(debug=False, port=port, use_reloader=False)
