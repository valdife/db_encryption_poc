"""
Cryptographic utilities for SSN encryption and hashing.

This module provides secure encryption (Fernet/AES) and hashing (SHA-256)
for Social Security Numbers. All functions expect SSNs to be normalized
before encryption/hashing for consistency.

Security Design:
- Encryption: Fernet (AES-128-CBC with HMAC-SHA256 for authentication)
- Hashing: SHA-256 with application-level salt for equality queries
- Normalization: Strip all non-digit characters for consistent processing

Environment Variables Required:
- SSN_ENCRYPTION_KEY: Fernet-compatible key (32 url-safe base64-encoded bytes)
- SSN_HASH_SALT: Random salt string for hashing (minimum 32 characters recommended)
"""
import hashlib
import re

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SSNCryptoError(Exception):
    """Base exception for SSN cryptography errors."""


class SSNEncryptionError(SSNCryptoError):
    """Raised when SSN encryption fails."""


class SSNDecryptionError(SSNCryptoError):
    """Raised when SSN decryption fails."""


class SSNConfigurationError(SSNCryptoError):
    """Raised when encryption is not properly configured."""


SSN_PATTERN = re.compile(r"[^0-9]")


def _get_fernet() -> Fernet:
    """Get configured Fernet instance for encryption/decryption."""
    key = settings.SSN_ENCRYPTION_KEY
    if not key:
        raise SSNConfigurationError(
            "SSN_ENCRYPTION_KEY is not configured. "
            "Set the SSN_ENCRYPTION_KEY environment variable."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        raise SSNConfigurationError(f"Invalid SSN_ENCRYPTION_KEY: {e}") from e


def _get_hash_salt() -> str:
    """Get configured salt for hashing."""
    salt = settings.SSN_HASH_SALT
    if not salt:
        raise SSNConfigurationError(
            "SSN_HASH_SALT is not configured. "
            "Set the SSN_HASH_SALT environment variable."
        )
    return salt


def normalize_ssn(ssn: str) -> str:
    """
    Normalize SSN by removing all non-digit characters.

    Examples:
        "123-45-6789" -> "123456789"
        "123 45 6789" -> "123456789"
        "123.45.6789" -> "123456789"

    Args:
        ssn: Raw SSN string in any format

    Returns:
        Normalized SSN containing only digits

    Raises:
        ValueError: If normalized SSN is not exactly 9 digits
    """
    normalized = SSN_PATTERN.sub("", ssn)
    if len(normalized) != 9:
        raise ValueError(
            f"Invalid SSN: must contain exactly 9 digits, got {len(normalized)}"
        )
    return normalized


def encrypt_ssn(ssn: str) -> str:
    """
    Encrypt a plaintext SSN using Fernet (AES-128-CBC + HMAC-SHA256).

    The SSN is normalized before encryption to ensure consistent ciphertext
    for equivalent inputs.

    Args:
        ssn: Plaintext SSN (will be normalized)

    Returns:
        Base64-encoded ciphertext string

    Raises:
        SSNEncryptionError: If encryption fails
        SSNConfigurationError: If encryption key is not configured
    """
    try:
        normalized = normalize_ssn(ssn)
        fernet = _get_fernet()
        ciphertext = fernet.encrypt(normalized.encode("utf-8"))
        return ciphertext.decode("utf-8")
    except SSNCryptoError:
        raise
    except ValueError as e:
        raise SSNEncryptionError(f"Invalid SSN format: {e}") from e
    except Exception as e:
        raise SSNEncryptionError(f"Encryption failed: {e}") from e


def decrypt_ssn(ciphertext: str) -> str:
    """
    Decrypt a ciphertext back to plaintext SSN.

    Args:
        ciphertext: Base64-encoded ciphertext from encrypt_ssn()

    Returns:
        Plaintext SSN (normalized, digits only)

    Raises:
        SSNDecryptionError: If decryption fails (invalid ciphertext, wrong key, etc.)
        SSNConfigurationError: If encryption key is not configured
    """
    try:
        fernet = _get_fernet()
        plaintext = fernet.decrypt(ciphertext.encode("utf-8"))
        return plaintext.decode("utf-8")
    except SSNCryptoError:
        raise
    except InvalidToken as e:
        raise SSNDecryptionError(
            "Decryption failed: invalid token. "
            "This may indicate corrupted data or wrong encryption key."
        ) from e
    except Exception as e:
        raise SSNDecryptionError(f"Decryption failed: {e}") from e


def hash_ssn(ssn: str) -> str:
    """
    Compute a salted SHA-256 hash of the SSN for equality queries.

    The SSN is normalized before hashing to ensure consistent hash values
    for equivalent inputs (e.g., "123-45-6789" and "123456789" produce
    the same hash).

    Security Note:
        The hash is salted with SSN_HASH_SALT to prevent rainbow table attacks.
        However, SSN space is small (10^9 values), so this is not a substitute
        for proper access controls. The hash enables efficient equality queries
        without exposing plaintext SSNs in the database.

    Args:
        ssn: Plaintext SSN (will be normalized)

    Returns:
        64-character hexadecimal SHA-256 hash

    Raises:
        ValueError: If SSN format is invalid
        SSNConfigurationError: If hash salt is not configured
    """
    normalized = normalize_ssn(ssn)
    salt = _get_hash_salt()

    # Concatenate salt + normalized SSN and hash
    salted_value = f"{salt}{normalized}"
    hash_digest = hashlib.sha256(salted_value.encode("utf-8")).hexdigest()

    return hash_digest


def mask_ssn(ssn: str) -> str:
    """
    Return a masked version of SSN for display purposes.

    Only the last 4 digits are shown, the rest are masked with asterisks.

    Examples:
        "123-45-6789" -> "***-**-6789"
        "123456789"   -> "***-**-6789"

    Args:
        ssn: Plaintext SSN in any format

    Returns:
        Masked SSN in format "***-**-XXXX"
    """
    try:
        normalized = normalize_ssn(ssn)
        return f"***-**-{normalized[-4:]}"
    except ValueError:
        return "***-**-****"


def format_ssn(ssn: str) -> str:
    """
    Format a normalized SSN with dashes for display.

    Args:
        ssn: Normalized SSN (9 digits)

    Returns:
        Formatted SSN as "XXX-XX-XXXX"
    """
    normalized = normalize_ssn(ssn)
    return f"{normalized[:3]}-{normalized[3:5]}-{normalized[5:]}"

