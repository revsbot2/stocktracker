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
                symbol      TEXT PRIMARY KEY,
                name        TEXT,
                asset_type  TEXT,
                themes      TEXT DEFAULT '[]',
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    else:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol      TEXT PRIMARY KEY,
                name        TEXT,
                asset_type  TEXT,
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        try:
            cur.execute('ALTER TABLE watchlist ADD COLUMN themes TEXT DEFAULT "[]"')
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

def get_watchlist():
    conn = _conn()
    if PG:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cur = conn.cursor()
    cur.execute('SELECT * FROM watchlist ORDER BY added_at')
    rows = _fetchall(cur)
    cur.close()
    conn.close()
    for d in rows:
        d['themes'] = json.loads(d.get('themes') or '[]')
    return rows


def update_themes(symbol, themes):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q('UPDATE watchlist SET themes = ? WHERE symbol = ?'),
        (json.dumps(themes), symbol.upper())
    )
    conn.commit()
    cur.close()
    conn.close()


def add_to_watchlist(symbol, name, asset_type):
    conn = _conn()
    cur = conn.cursor()
    try:
        if PG:
            cur.execute(
                'INSERT INTO watchlist (symbol, name, asset_type) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                (symbol.upper(), name, asset_type)
            )
        else:
            cur.execute(
                'INSERT OR IGNORE INTO watchlist (symbol, name, asset_type) VALUES (?, ?, ?)',
                (symbol.upper(), name, asset_type)
            )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        cur.close()
        conn.close()


def remove_from_watchlist(symbol):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q('DELETE FROM watchlist WHERE symbol = ?'), (symbol.upper(),))
    cur.execute(_q('DELETE FROM asset_data WHERE symbol = ?'), (symbol.upper(),))
    conn.commit()
    cur.close()
    conn.close()


def save_asset_data(symbol, data):
    conn = _conn()
    cur = conn.cursor()
    if PG:
        cur.execute(
            '''INSERT INTO asset_data (symbol, data, updated_at) VALUES (%s, %s, %s)
               ON CONFLICT (symbol) DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at''',
            (symbol.upper(), json.dumps(data), datetime.now().isoformat())
        )
    else:
        cur.execute(
            'INSERT OR REPLACE INTO asset_data (symbol, data, updated_at) VALUES (?, ?, ?)',
            (symbol.upper(), json.dumps(data), datetime.now().isoformat())
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
