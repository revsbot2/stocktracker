from werkzeug.security import generate_password_hash, check_password_hash
import secrets


def hash_password(password: str) -> str:
    return generate_password_hash(password, method='pbkdf2:sha256')


def verify_password(password: str, pw_hash: str) -> bool:
    return check_password_hash(pw_hash, password)


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)
