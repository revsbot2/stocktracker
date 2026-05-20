import sqlite3
import json
from datetime import datetime

DB_PATH = 'stocks.db'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol      TEXT PRIMARY KEY,
            name        TEXT,
            asset_type  TEXT,
            added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS asset_data (
            symbol      TEXT PRIMARY KEY,
            data        TEXT NOT NULL,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Auth tables
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier    TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login    TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS otp_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier  TEXT NOT NULL,
            code        TEXT NOT NULL,
            expires_at  TIMESTAMP NOT NULL,
            used        INTEGER DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            identifier  TEXT NOT NULL,
            expires_at  TIMESTAMP NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Migrate: add themes column if it doesn't exist yet
    try:
        conn.execute('ALTER TABLE watchlist ADD COLUMN themes TEXT DEFAULT "[]"')
    except Exception:
        pass
    conn.commit()
    conn.close()


# ── Auth ──────────────────────────────────────────────────────────────────────

def save_otp(identifier, code, expires_at):
    conn = sqlite3.connect(DB_PATH)
    # Invalidate any previous unused codes for this identifier
    conn.execute('UPDATE otp_codes SET used=1 WHERE identifier=? AND used=0', (identifier,))
    conn.execute('INSERT INTO otp_codes (identifier, code, expires_at) VALUES (?,?,?)',
                 (identifier, code, expires_at))
    conn.commit()
    conn.close()


def verify_otp(identifier, code):
    """Returns True and marks code used if valid; False otherwise."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        '''SELECT id FROM otp_codes
           WHERE identifier=? AND code=? AND used=0
             AND expires_at > ?''',
        (identifier, code, datetime.now().isoformat())
    ).fetchone()
    if row:
        conn.execute('UPDATE otp_codes SET used=1 WHERE id=?', (row[0],))
        conn.commit()
    conn.close()
    return row is not None


def get_or_create_user(identifier):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM users WHERE identifier=?', (identifier,)).fetchone()
    if not row:
        conn.execute('INSERT INTO users (identifier) VALUES (?)', (identifier,))
        conn.commit()
        row = conn.execute('SELECT * FROM users WHERE identifier=?', (identifier,)).fetchone()
    else:
        conn.execute('UPDATE users SET last_login=? WHERE identifier=?',
                     (datetime.now().isoformat(), identifier))
        conn.commit()
    result = dict(row)
    conn.close()
    return result


def get_user_by_identifier(identifier):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM users WHERE identifier=?', (identifier,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_password(identifier, pw_hash):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET password_hash=? WHERE identifier=?', (pw_hash, identifier))
    conn.commit()
    conn.close()


def create_session(token, user_id, identifier, expires_at):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'INSERT INTO sessions (token, user_id, identifier, expires_at) VALUES (?,?,?,?)',
        (token, user_id, identifier, expires_at)
    )
    conn.commit()
    conn.close()


def get_session_user(token):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT * FROM sessions WHERE token=? AND expires_at > ?',
        (token, datetime.now().isoformat())
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(token):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM sessions WHERE token=?', (token,))
    conn.commit()
    conn.close()


def get_watchlist():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM watchlist ORDER BY added_at').fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d['themes'] = json.loads(d.get('themes') or '[]')
        result.append(d)
    return result


def update_themes(symbol, themes):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'UPDATE watchlist SET themes = ? WHERE symbol = ?',
        (json.dumps(themes), symbol.upper())
    )
    conn.commit()
    conn.close()


def add_to_watchlist(symbol, name, asset_type):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            'INSERT OR IGNORE INTO watchlist (symbol, name, asset_type) VALUES (?, ?, ?)',
            (symbol.upper(), name, asset_type)
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def remove_from_watchlist(symbol):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM watchlist WHERE symbol = ?', (symbol.upper(),))
    conn.execute('DELETE FROM asset_data WHERE symbol = ?', (symbol.upper(),))
    conn.commit()
    conn.close()


def save_asset_data(symbol, data):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'INSERT OR REPLACE INTO asset_data (symbol, data, updated_at) VALUES (?, ?, ?)',
        (symbol.upper(), json.dumps(data), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_asset_data(symbol):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        'SELECT data, updated_at FROM asset_data WHERE symbol = ?',
        (symbol.upper(),)
    ).fetchone()
    conn.close()
    if row:
        data = json.loads(row[0])
        data['last_updated'] = row[1]
        return data
    return None


def get_all_asset_data():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('SELECT symbol, data, updated_at FROM asset_data').fetchall()
    conn.close()
    result = {}
    for row in rows:
        data = json.loads(row[1])
        data['last_updated'] = row[2]
        result[row[0]] = data
    return result
