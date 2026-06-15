"""
Password hashing helpers.

We use the `bcrypt` library directly. bcrypt is adaptive (tunable cost) and
stores the salt inside the hash itself, so no separate salt column is needed.

Note on the 72-byte limit: bcrypt only considers the first 72 bytes of the
password and raises on longer input. We truncate to 72 bytes before hashing and
verifying so both paths behave consistently and never raise at runtime. For an
internal tool this is acceptable; if we ever need to lift the limit we can
pre-hash with SHA-256 before bcrypt.
"""
import bcrypt

# Cost factor: higher is slower (more secure). 12 is a sensible default in 2025.
_BCRYPT_ROUNDS = 12

_MAX_BCRYPT_BYTES = 72

# A pre-computed hash of a throwaway password. We verify against this when the
# username does not exist, so that a failed login takes roughly the same time
# whether or not the username is real. This defeats timing attacks that could
# otherwise reveal which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))


def _encode(password: str) -> bytes:
    """Encode the password to UTF-8 and truncate to bcrypt's 72-byte limit."""
    return password.encode("utf-8")[:_MAX_BCRYPT_BYTES]


def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash (as a string) for the given plaintext password."""
    hashed = bcrypt.hashpw(_encode(plain_password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Return True if the plaintext password matches the stored hash."""
    return bcrypt.checkpw(_encode(plain_password), password_hash.encode("utf-8"))


def dummy_verify() -> None:
    """
    Perform a throwaway hash verification to equalize timing on the
    'username not found' path. The result is intentionally ignored.
    """
    bcrypt.checkpw(b"wrong-password", _DUMMY_HASH)
