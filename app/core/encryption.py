"""
Encryption utilities for storing sensitive data (API keys, secrets).

Uses envelope encryption:
1. Each record gets a unique Fernet key (DEK - Data Encryption Key)
2. The DEK is encrypted with a master key (KEK - Key Encryption Key)
3. Master key comes from environment (MVP) or KMS (production)

To upgrade to KMS:
- Replace get_master_key() to fetch from AWS KMS / GCP KMS
- Everything else stays the same
"""

import os
import base64
from typing import Optional, Tuple
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import settings


class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""
    pass


def get_master_key() -> bytes:
    """
    Get the master key for envelope encryption.

    MVP: From environment variable
    Production: Replace with KMS call

    The key should be a 32-byte (256-bit) key, base64 encoded.
    Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    master_key = getattr(settings, 'ENCRYPTION_MASTER_KEY', None) or os.getenv('ENCRYPTION_MASTER_KEY')

    if not master_key:
        if settings.DEBUG:
            # Use a deterministic key for development only
            # WARNING: Never use this in production
            master_key = base64.urlsafe_b64encode(b'dev-key-32-bytes-do-not-use!!')
            if isinstance(master_key, bytes):
                master_key = master_key.decode()
        else:
            raise EncryptionError(
                "ENCRYPTION_MASTER_KEY not set. Generate with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

    return master_key.encode() if isinstance(master_key, str) else master_key


def generate_data_key() -> bytes:
    """Generate a new Fernet key for encrypting data."""
    return Fernet.generate_key()


def encrypt_data_key(data_key: bytes, master_key: Optional[bytes] = None) -> bytes:
    """Encrypt a data key with the master key."""
    if master_key is None:
        master_key = get_master_key()

    fernet = Fernet(master_key)
    return fernet.encrypt(data_key)


def decrypt_data_key(encrypted_data_key: bytes, master_key: Optional[bytes] = None) -> bytes:
    """Decrypt a data key with the master key."""
    if master_key is None:
        master_key = get_master_key()

    try:
        fernet = Fernet(master_key)
        return fernet.decrypt(encrypted_data_key)
    except InvalidToken:
        raise EncryptionError("Failed to decrypt data key - invalid master key or corrupted data")


def encrypt_value(value: str) -> Tuple[str, str]:
    """
    Encrypt a sensitive value using envelope encryption.

    Returns:
        Tuple of (encrypted_value, encrypted_data_key) - both base64 encoded strings
    """
    if not value:
        return "", ""

    # Generate a unique key for this value
    data_key = generate_data_key()

    # Encrypt the value with the data key
    fernet = Fernet(data_key)
    encrypted_value = fernet.encrypt(value.encode())

    # Encrypt the data key with the master key
    encrypted_data_key = encrypt_data_key(data_key)

    # Return both as base64 strings for storage
    return (
        base64.urlsafe_b64encode(encrypted_value).decode(),
        base64.urlsafe_b64encode(encrypted_data_key).decode(),
    )


def decrypt_value(encrypted_value: str, encrypted_data_key: str) -> str:
    """
    Decrypt a value that was encrypted with envelope encryption.

    Args:
        encrypted_value: Base64 encoded encrypted value
        encrypted_data_key: Base64 encoded encrypted data key

    Returns:
        The original plaintext value
    """
    if not encrypted_value or not encrypted_data_key:
        return ""

    try:
        # Decode from base64
        encrypted_value_bytes = base64.urlsafe_b64decode(encrypted_value.encode())
        encrypted_data_key_bytes = base64.urlsafe_b64decode(encrypted_data_key.encode())

        # Decrypt the data key
        data_key = decrypt_data_key(encrypted_data_key_bytes)

        # Decrypt the value
        fernet = Fernet(data_key)
        decrypted_value = fernet.decrypt(encrypted_value_bytes)

        return decrypted_value.decode()
    except Exception as e:
        raise EncryptionError(f"Failed to decrypt value: {e}")


def mask_key(key: str, visible_chars: int = 4) -> str:
    """
    Mask an API key for display, showing only last N characters.

    Example: "sk-1234567890abcdef" -> "sk-************cdef"
    """
    if not key or len(key) <= visible_chars:
        return "*" * 8

    prefix = ""
    if "-" in key[:10]:
        # Preserve prefix like "sk-" or "pk-"
        prefix = key[:key.index("-") + 1]
        key = key[len(prefix):]

    masked_length = len(key) - visible_chars
    return prefix + "*" * masked_length + key[-visible_chars:]
