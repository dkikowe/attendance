"""Modern hashes for new accounts; accept existing local MD5 accounts on login."""
import hashlib
import hmac
from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored, password):
    if len(stored) == 32 and all(char in '0123456789abcdef' for char in stored):
        return hmac.compare_digest(stored, hashlib.md5(password.encode('utf-8')).hexdigest())
    return check_password_hash(stored, password)
