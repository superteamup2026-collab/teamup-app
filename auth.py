"""
auth.py
Password hashing and input checking for sign-up / log-in.
Uses only Python's standard library, so nothing extra to install.

We never store the real password. We store a "hash": a one-way scrambled
version. At login we scramble what the user typed the same way and compare.
"""

import hashlib
import hmac
import re
import secrets

# How many times the scrambling is repeated. Higher = slower for attackers.
ITERATIONS = 600_000


def hash_password(password):
    """
    Turns a password into a storable string like:
    pbkdf2_sha256$600000$<random salt>$<hash>
    The random salt means two people with the same password get different hashes.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    """Checks a typed password against a stored hash. Returns True/False."""
    try:
        _algo, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        # compare_digest avoids leaking information through timing differences
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def is_valid_email(email):
    """Very basic shape check: something@something.something"""
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None


def clean_phone(phone):
    """
    Strips spaces, dashes and brackets, then checks it looks like a phone number
    (10-15 digits, optional leading +). Returns the cleaned number, or None if invalid.
    """
    stripped = re.sub(r"[\s\-()]", "", phone)
    if re.fullmatch(r"\+?\d{10,15}", stripped):
        return stripped
    return None