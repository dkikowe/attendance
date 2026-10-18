import hashlib
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('password_helpers', Path(__file__).resolve().parents[1] / 'passwords.py')
passwords = importlib.util.module_from_spec(spec)
spec.loader.exec_module(passwords)


def test_new_password_hash_is_salted_and_accepts_only_the_correct_password():
    first = passwords.hash_password('Test-password-123')
    second = passwords.hash_password('Test-password-123')
    assert first != second
    assert first.startswith('scrypt:')
    assert passwords.verify_password(first, 'Test-password-123')
    assert not passwords.verify_password(first, 'wrong-password')


def test_existing_md5_accounts_can_still_sign_in():
    stored = hashlib.md5(b'legacy-password').hexdigest()
    assert passwords.verify_password(stored, 'legacy-password')
    assert not passwords.verify_password(stored, 'wrong-password')
