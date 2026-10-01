"""Proof-token vault cryptography (ADR-004).

AES-256-GCM via the `cryptography` library — no home-made primitives. Stored form of a token:

    ciphertext = AES-256-GCM(key[key_id], nonce, utf8(token), aad)      # includes the 16-byte tag
    nonce      = 12 fresh random bytes per encryption
    aad        = b"speakyourlog/proof-token/v1" + user_id.bytes

The AAD binds a ciphertext to its owner: moving a row to another user makes decryption fail.
`key_id` is stored beside the ciphertext so keys can be rotated. The format is pinned by a golden
test vector (tests/test_crypto.py): changing it silently would orphan every stored token.

Limits worth knowing: Python strings are immutable, so plaintext cannot be wiped from memory
after use; we keep its lifetime short (decrypt only at post time) and wrap it in SecretStr so an
accidental log line shows `**********`.
"""

import os
import re
from dataclasses import dataclass, field
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.config import Settings

KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
MAX_PLAINTEXT_BYTES = 1024
_AAD_PREFIX = b"speakyourlog/proof-token/v1"
_KEY_ID_RE = re.compile(r"^[a-z0-9_]{1,16}$")

# Token hygiene: what we accept from a user's paste box.
MIN_TOKEN_CHARS = 8
MAX_TOKEN_CHARS = 512
_TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")  # printable ASCII, no whitespace or control chars


class VaultError(Exception):
    """Base class. Messages never contain plaintext, ciphertext, or key material."""


class InvalidTokenFormat(VaultError):
    pass


class UnknownKeyError(VaultError):
    pass


class DecryptionError(VaultError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedToken:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    key_id: str


def normalize_token(raw: str) -> SecretStr:
    """Trim paste artefacts (spaces/newlines at the ends) and check the token looks sane."""
    token = raw.strip()
    if not (MIN_TOKEN_CHARS <= len(token) <= MAX_TOKEN_CHARS) or not _TOKEN_RE.fullmatch(token):
        raise InvalidTokenFormat("token has an unexpected format")
    return SecretStr(token)


def token_last4(token: SecretStr) -> str:
    """The only fragment of a token the UI may ever show."""
    return token.get_secret_value()[-4:]


def _aad(user_id: UUID) -> bytes:
    return _AAD_PREFIX + user_id.bytes


class TokenVault:
    def __init__(self, keys: dict[str, bytes], active_key_id: str) -> None:
        for key_id, key in keys.items():
            if not _KEY_ID_RE.fullmatch(key_id):
                raise ValueError(f"invalid key id {key_id!r}")
            if len(key) != KEY_BYTES:
                raise ValueError(f"key {key_id!r} must be {KEY_BYTES} bytes")
        if active_key_id not in keys:
            raise ValueError(f"active key id {active_key_id!r} has no key")
        # AESGCM objects hold the key; keep them (not the raw bytes) after construction.
        self._ciphers = {key_id: AESGCM(key) for key_id, key in keys.items()}
        self._active = active_key_id

    @classmethod
    def from_settings(cls, settings: Settings) -> "TokenVault":
        return cls(settings.token_enc_keys, settings.token_enc_key_id)

    @property
    def active_key_id(self) -> str:
        return self._active

    def needs_rotation(self, key_id: str) -> bool:
        return key_id != self._active

    def encrypt(self, token: SecretStr, user_id: UUID) -> EncryptedToken:
        plaintext = token.get_secret_value().encode("utf-8")
        if not plaintext or len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise InvalidTokenFormat("token has an unexpected length")
        nonce = os.urandom(NONCE_BYTES)  # random 96-bit nonce: safe far below 2^32 uses per key
        ciphertext = self._ciphers[self._active].encrypt(nonce, plaintext, _aad(user_id))
        return EncryptedToken(ciphertext=ciphertext, nonce=nonce, key_id=self._active)

    def decrypt(self, user_id: UUID, *, ciphertext: bytes, nonce: bytes, key_id: str) -> SecretStr:
        cipher = self._ciphers.get(key_id)
        if cipher is None:
            raise UnknownKeyError(f"no key configured for id {key_id!r}")
        if len(nonce) != NONCE_BYTES or len(ciphertext) <= TAG_BYTES:
            raise DecryptionError("could not decrypt token")
        try:
            plaintext = cipher.decrypt(nonce, ciphertext, _aad(user_id))
            return SecretStr(plaintext.decode("utf-8"))
        except (InvalidTag, UnicodeDecodeError):
            # Same message for every failure (wrong key/user, tampering, corruption): a caller
            # or attacker learns nothing about *why*. `from None` drops the chained traceback.
            raise DecryptionError("could not decrypt token") from None

    def reencrypt(
        self, user_id: UUID, *, ciphertext: bytes, nonce: bytes, key_id: str
    ) -> EncryptedToken:
        """Move a stored token to the active key (used by key rotation)."""
        token = self.decrypt(user_id, ciphertext=ciphertext, nonce=nonce, key_id=key_id)
        return self.encrypt(token, user_id)

    def __repr__(self) -> str:  # never include key material
        return f"TokenVault(active={self._active!r}, keys={sorted(self._ciphers)})"
