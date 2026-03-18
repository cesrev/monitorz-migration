"""
Token encryption at rest using Fernet symmetric encryption.

Encrypts/decrypts OAuth tokens before storing in SQLite.
Requires TOKEN_ENCRYPTION_KEY env var (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").
If the key is not set, encryption is skipped (dev mode) with a warning.
"""

import os
import logging

logger = logging.getLogger(__name__)

_fernet = None
_encryption_enabled = False

_TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")

if _TOKEN_ENCRYPTION_KEY:
    try:
        from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
        _fernet = Fernet(_TOKEN_ENCRYPTION_KEY.encode())
        _encryption_enabled = True
        logger.info("Token encryption enabled")
    except Exception as e:
        logger.error("TOKEN_ENCRYPTION_KEY is set but invalid: %s", e)
        raise
else:
    logger.warning(
        "TOKEN_ENCRYPTION_KEY not set — OAuth tokens will be stored in PLAINTEXT. "
        "Set this env var in production!"
    )


# Encrypted tokens are prefixed so we can detect them during migration
_ENCRYPTED_PREFIX = "enc::"


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string. Returns prefixed ciphertext, or plaintext if encryption disabled."""
    if not plaintext:
        return plaintext
    if not _encryption_enabled:
        return plaintext
    ciphertext = _fernet.encrypt(plaintext.encode()).decode()
    return f"{_ENCRYPTED_PREFIX}{ciphertext}"


def decrypt_token(stored: str) -> str:
    """Decrypt a token string. Handles both encrypted (prefixed) and plaintext tokens."""
    if not stored:
        return stored
    if not stored.startswith(_ENCRYPTED_PREFIX):
        # Plaintext token (pre-migration or encryption disabled)
        return stored
    if not _encryption_enabled:
        logger.error(
            "Found encrypted token but TOKEN_ENCRYPTION_KEY is not set — cannot decrypt"
        )
        raise RuntimeError("Cannot decrypt token: TOKEN_ENCRYPTION_KEY not configured")
    from cryptography.fernet import InvalidToken
    ciphertext = stored[len(_ENCRYPTED_PREFIX):]
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt token — wrong key or corrupted data")
        raise


def is_token_encrypted(stored: str) -> bool:
    """Check if a stored token value is already encrypted."""
    if not stored:
        return False
    return stored.startswith(_ENCRYPTED_PREFIX)
