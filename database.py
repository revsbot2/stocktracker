import os
import json
from datetime import datetime
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get('DATABASE_URL')

# Fall back to SQLite for local development
if DATABASE_URL:
    def _conn():
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    PG = True
else:
    import sqlite3
    DB_PATH = 'stocks.db'
    PG = False
    def _conn():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def _q(sql):
    """Convert ? placeholders to %s for PostgreSQL."""
    if PG:
        return sql.replace('?', '%s')
    return sql


def init_db():
    conn = _conn()
    cur = conn.cursor()
    if PG:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS watchlist (
                user_id     INTEGER NOT NULL,
                symbol      TEXT NOT NULL,
                name        TEXT,
                asset_type  TEXT,
                themes      TEXT DEFAULT '[]',
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, symbol)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS asset_data (
                symbol      TEXT PRIMARY KEY,
                data        TEXT NOT NULL,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                identifier    TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login    TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS otp_codes (
                id          SERIAL PRIMARY KEY,
                identifier  TEXT NOT NULL,
                code        TEXT NOT NULL,
                expires_at  TIMESTAMP NOT NULL,
                used        INTEGER DEFAULT 0
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                identifier  TEXT NOT NULL,
                expires_at  TIMESTAMP NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tracked_politicians (
                user_id        INTEGER NOT NULL,
                politician_key TEXT NOT NULL,
                first_name     TEXT,
                last_name      TEXT,
                display_name   TEXT,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, politician_key)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS politician_trades (
                id               SERIAL PRIMARY KEY,
                politician_key   TEXT,
                politician_name  TEXT,
                chamber          TEXT,
                ticker           TEXT,
                type             TEXT,
                amount           TEXT,
                transaction_date TEXT,
                disclosure_date  TEXT,
                filing_url       TEXT,
                owner            TEXT,
                doc_id           TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_poltrades_ticker ON politician_trades (ticker)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_poltrades_key ON politician_trades (politician_key)')
    else:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS watchlist (
                user_id     INTEGER NOT NULL,
                symbol      TEXT NOT NULL,
                name        TEXT,
                asset_type  TEXT,
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, symbol)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS asset_data (
                symbol      TEXT PRIMARY KEY,
                data        TEXT NOT NULL,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier    TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login    TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS otp_codes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier  TEXT NOT NULL,
                code        TEXT NOT NULL,
                expires_at  TIMESTAMP NOT NULL,
                used        INTEGER DEFAULT 0
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                identifier  TEXT NOT NULL,
                expires_at  TIMESTAMP NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tracked_politicians (
                user_id        INTEGER NOT NULL,
                politician_key TEXT NOT NULL,
                first_name     TEXT,
                last_name      TEXT,
                display_name   TEXT,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, politician_key)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS politician_trades (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                politician_key   TEXT,
                politician_name  TEXT,
                chamber          TEXT,
                ticker           TEXT,
                type             TEXT,
                amount           TEXT,
                transaction_date TEXT,
                disclosure_date  TEXT,
                filing_url       TEXT,
                owner            TEXT,
                doc_id           TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_poltrades_ticker ON politician_trades (ticker)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_poltrades_key ON politician_trades (politician_key)')
        try:
            cur.execute('ALTER TABLE watchlist ADD COLUMN themes TEXT DEFAULT "[]"')
        except Exception:
            pass
        try:
            cur.execute('ALTER TABLE watchlist ADD COLUMN user_id INTEGER DEFAULT 0')
        except Exception:
            pass
    conn.commit()
    cur.close()
    conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetchone(cur):
    row = cur.fetchone()
    if row is None:
        return None
    if PG:
        return dict(row)
    return dict(row)


def _fetchall(cur):
    rows = cur.fetchall()
    if PG:
        return [dict(r) for r in rows]
    return [dict(r) for r in rows]


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_or_create_user(identifier):
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute(_q('SELECT * FROM users WHERE identifier=?'), (identifier,))
    row = _fetchone(cur)
    if not row:
        cur.execute(_q('INSERT INTO users (identifier) VALUES (?)'), (identifier,))
        conn.commit()
        cur.execute(_q('SELECT * FROM users WHERE identifier=?'), (identifier,))
        row = _fetchone(cur)
    else:
        cur.execute(_q('UPDATE users SET last_login=? WHERE identifier=?'),
                    (datetime.now().isoformat(), identifier))
        conn.commit()
    cur.close()
    conn.close()
    return row


def get_all_users():
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute(_q('SELECT id, identifier, created_at, last_login FROM users ORDER BY created_at DESC'))
    rows = _fetchall(cur)
    cur.close()
    conn.close()
    return rows


def get_user_by_identifier(identifier):
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute(_q('SELECT * FROM users WHERE identifier=?'), (identifier,))
    row = _fetchone(cur)
    cur.close()
    conn.close()
    return row


def set_user_password(identifier, pw_hash):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q('UPDATE users SET password_hash=? WHERE identifier=?'), (pw_hash, identifier))
    conn.commit()
    cur.close()
    conn.close()


def create_session(token, user_id, identifier, expires_at):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q('INSERT INTO sessions (token, user_id, identifier, expires_at) VALUES (?,?,?,?)'),
        (token, user_id, identifier, expires_at)
    )
    conn.commit()
    cur.close()
    conn.close()


def get_session_user(token):
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute(
        _q('SELECT * FROM sessions WHERE token=? AND expires_at > ?'),
        (token, datetime.now().isoformat())
    )
    row = _fetchone(cur)
    cur.close()
    conn.close()
    return row


def delete_session(token):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q('DELETE FROM sessions WHERE token=?'), (token,))
    conn.commit()
    cur.close()
    conn.close()


# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist(user_id):
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute(_q('SELECT * FROM watchlist WHERE user_id=? ORDER BY added_at'), (user_id,))
    rows = _fetchall(cur)
    cur.close()
    conn.close()
    for d in rows:
        d['themes'] = json.loads(d.get('themes') or '[]')
    return rows


def get_all_watched_symbols():
    """Returns all unique symbols across all users — used by the scheduler."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute('SELECT DISTINCT symbol FROM watchlist')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{'symbol': r[0]} for r in rows]


def update_themes(user_id, symbol, themes):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q('UPDATE watchlist SET themes = ? WHERE user_id = ? AND symbol = ?'),
        (json.dumps(themes), user_id, symbol.upper())
    )
    conn.commit()
    cur.close()
    conn.close()


def add_to_watchlist(user_id, symbol, name, asset_type):
    conn = _conn()
    cur = conn.cursor()
    try:
        if PG:
            cur.execute(
                'INSERT INTO watchlist (user_id, symbol, name, asset_type) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
                (user_id, symbol.upper(), name, asset_type)
            )
        else:
            cur.execute(
                'INSERT OR IGNORE INTO watchlist (user_id, symbol, name, asset_type) VALUES (?, ?, ?, ?)',
                (user_id, symbol.upper(), name, asset_type)
            )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        cur.close()
        conn.close()


def remove_from_watchlist(user_id, symbol):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q('DELETE FROM watchlist WHERE user_id = ? AND symbol = ?'), (user_id, symbol.upper()))
    conn.commit()
    cur.close()
    conn.close()


def save_asset_data(symbol, data):
    # Merge with existing record: keep old non-None values for any field
    # that came back None in the new fetch (e.g. when fast_info fallback was used)
    existing = get_asset_data(symbol)
    if existing:
        existing.pop('last_updated', None)
        merged = {**existing, **{k: v for k, v in data.items() if v is not None}}
    else:
        merged = data

    conn = _conn()
    cur = conn.cursor()
    if PG:
        cur.execute(
            '''INSERT INTO asset_data (symbol, data, updated_at) VALUES (%s, %s, %s)
               ON CONFLICT (symbol) DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at''',
            (symbol.upper(), json.dumps(merged), datetime.now().isoformat())
        )
    else:
        cur.execute(
            'INSERT OR REPLACE INTO asset_data (symbol, data, updated_at) VALUES (?, ?, ?)',
            (symbol.upper(), json.dumps(merged), datetime.now().isoformat())
        )
    conn.commit()
    cur.close()
    conn.close()


def get_asset_data(symbol):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q('SELECT data, updated_at FROM asset_data WHERE symbol = ?'),
        (symbol.upper(),)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        data = json.loads(row[0])
        data['last_updated'] = str(row[1])
        return data
    return None


def get_all_asset_data():
    conn = _conn()
    cur = conn.cursor()
    cur.execute('SELECT symbol, data, updated_at FROM asset_data')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for row in rows:
        data = json.loads(row[1])
        data['last_updated'] = str(row[2])
        result[row[0]] = data
    return result


# ── Tracked politicians (per user) ─────────────────────────────────────────────

def get_tracked_politicians(user_id):
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute(_q('SELECT first_name, last_name, display_name, politician_key '
                   'FROM tracked_politicians WHERE user_id=? ORDER BY display_name'), (user_id,))
    rows = _fetchall(cur)
    cur.close()
    conn.close()
    return rows


def add_tracked_politician(user_id, first_name, last_name, display_name, politician_key):
    """Returns True if a new row was inserted, False if already tracked."""
    conn = _conn()
    cur = conn.cursor()
    if PG:
        cur.execute(
            'INSERT INTO tracked_politicians (user_id, politician_key, first_name, last_name, display_name) '
            'VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
            (user_id, politician_key, first_name, last_name, display_name)
        )
    else:
        cur.execute(
            'INSERT OR IGNORE INTO tracked_politicians (user_id, politician_key, first_name, last_name, display_name) '
            'VALUES (?, ?, ?, ?, ?)',
            (user_id, politician_key, first_name, last_name, display_name)
        )
    inserted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return inserted


def remove_tracked_politician(user_id, politician_key):
    """Returns True if a row was removed."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q('DELETE FROM tracked_politicians WHERE user_id=? AND politician_key=?'),
                (user_id, politician_key))
    removed = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return removed


def get_all_tracked_distinct():
    """Distinct politicians tracked by any user — drives the global fetch union."""
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute('SELECT DISTINCT politician_key, first_name, last_name, display_name FROM tracked_politicians')
    rows = _fetchall(cur)
    cur.close()
    conn.close()
    return rows


# ── Politician trades (global, persisted) ──────────────────────────────────────

_TRADE_COLS = ('politician_key', 'politician_name', 'chamber', 'ticker', 'type',
               'amount', 'transaction_date', 'disclosure_date', 'filing_url', 'owner', 'doc_id')


def save_politician_trades(trades):
    """Replace the persisted trade set with the latest fetch."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM politician_trades')
    if trades:
        rows = [(
            t.get('politician_key', ''), t.get('politician', ''), t.get('chamber', ''),
            t.get('ticker', ''), t.get('type', ''), t.get('amount', ''),
            t.get('transaction_date', ''), t.get('disclosure_date', ''),
            t.get('filing_url', ''), t.get('owner', ''), str(t.get('doc_id', '')),
        ) for t in trades]
        ph = '(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)' if PG else '(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
        cur.executemany(
            'INSERT INTO politician_trades '
            '(politician_key, politician_name, chamber, ticker, type, amount, '
            'transaction_date, disclosure_date, filing_url, owner, doc_id) VALUES ' + ph,
            rows
        )
    conn.commit()
    cur.close()
    conn.close()


def get_all_politician_trades(limit=2000):
    """All persisted trades, newest first — used to warm the in-memory cache."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q('SELECT politician_name, chamber, ticker, type, amount, transaction_date, '
                   'disclosure_date, filing_url, owner, politician_key '
                   'FROM politician_trades ORDER BY transaction_date DESC LIMIT ?'), (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{
        'politician': r[0], 'chamber': r[1], 'ticker': r[2], 'type': r[3], 'amount': r[4],
        'transaction_date': r[5], 'disclosure_date': r[6], 'filing_url': r[7],
        'owner': r[8], 'politician_key': r[9],
    } for r in rows]


def get_politician_trades_for_user(user_id, symbols):
    """Trades on the given tickers made by politicians this user tracks."""
    if not symbols:
        return []
    conn = _conn()
    cur = conn.cursor()
    placeholders = ', '.join(['?'] * len(symbols))
    sql = _q(
        'SELECT t.ticker, t.politician_name, t.type, t.transaction_date, t.amount, '
        't.filing_url, t.chamber '
        'FROM politician_trades t '
        'JOIN tracked_politicians tp ON t.politician_key = tp.politician_key '
        'WHERE tp.user_id = ? AND t.ticker IN (' + placeholders + ') '
        'ORDER BY t.transaction_date DESC'
    )
    cur.execute(sql, (user_id, *[s.upper() for s in symbols]))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{
        'ticker': r[0], 'politician_name': r[1], 'type': r[2], 'transaction_date': r[3],
        'amount': r[4], 'filing_url': r[5], 'chamber': r[6],
    } for r in rows]
