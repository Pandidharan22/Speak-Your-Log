import secrets
import uuid

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app import repo
from app.crypto import (
    DecryptionError,
    EncryptedToken,
    InvalidTokenFormat,
    TokenVault,
    UnknownKeyError,
    normalize_token,
    token_last4,
)

KEY1 = bytes(range(32))
KEY2 = bytes(range(32, 64))
ALICE = uuid.UUID("12345678-1234-5678-1234-567812345678")
BOB = uuid.UUID("87654321-4321-8765-4321-876543218765")
GENERIC = "could not decrypt token"


@pytest.fixture
def vault() -> TokenVault:
    return TokenVault({"v1": KEY1}, "v1")


def sealed(vault: TokenVault, token="tok-ABCDEFGH-1234", user=ALICE) -> EncryptedToken:
    return vault.encrypt(SecretStr(token), user)


def open_(vault: TokenVault, enc: EncryptedToken, user=ALICE, **overrides):
    args = {"ciphertext": enc.ciphertext, "nonce": enc.nonce, "key_id": enc.key_id} | overrides
    return vault.decrypt(user, **args)


# ---- format stability: the most important test in this file ---------------------------------


def test_golden_vector_still_decrypts():
    """Produced once with a direct AESGCM call. If this fails, stored tokens would be orphaned."""
    vault = TokenVault({"v1": KEY1}, "v1")
    ciphertext = bytes.fromhex(
        "2f74b2021c877bea51093a86f72428be73f0356eaea93d7ed17d627f3f93455eb175ab"
    )
    nonce = bytes(range(100, 112))
    got = vault.decrypt(ALICE, ciphertext=ciphertext, nonce=nonce, key_id="v1")
    assert got.get_secret_value() == "golden-token-ABC123"


def test_stored_format_matches_the_documented_spec_independently(vault):
    enc = sealed(vault, "spec-check-token-99")
    aad = b"speakyourlog/proof-token/v1" + ALICE.bytes
    assert AESGCM(KEY1).decrypt(enc.nonce, enc.ciphertext, aad) == b"spec-check-token-99"


# ---- round trips and randomness -------------------------------------------------------------


def test_roundtrip_many_random_and_unicode_tokens(vault):
    tokens = [secrets.token_urlsafe(secrets.randbelow(200) + 8) for _ in range(150)]
    tokens += ["tökén-ünïcode-日本語-9", "x" * 512]
    for t in tokens:
        assert open_(vault, sealed(vault, t)).get_secret_value() == t


def test_ciphertext_is_plaintext_plus_16_byte_tag_and_nonce_is_12_bytes(vault):
    enc = sealed(vault, "exactly-20-chars-ok!")
    assert len(enc.ciphertext) == len("exactly-20-chars-ok!") + 16
    assert len(enc.nonce) == 12 and enc.key_id == "v1"


def test_every_encryption_uses_a_fresh_nonce_and_ciphertext(vault):
    encs = [sealed(vault, "same-token-value-1") for _ in range(2000)]
    assert len({e.nonce for e in encs}) == 2000
    assert len({e.ciphertext for e in encs}) == 2000  # same input never encrypts the same twice


def test_plaintext_does_not_appear_in_the_ciphertext(vault):
    enc = sealed(vault, "visible-marker-STRING")
    assert b"visible-marker-STRING" not in enc.ciphertext


# ---- tampering: authenticated encryption must reject ANY change -----------------------------


def test_flipping_any_single_bit_of_the_ciphertext_is_detected(vault):
    enc = sealed(vault, "short-token-1")
    for byte_i in range(len(enc.ciphertext)):
        for bit in range(8):
            mutated = bytearray(enc.ciphertext)
            mutated[byte_i] ^= 1 << bit
            with pytest.raises(DecryptionError):
                open_(vault, enc, ciphertext=bytes(mutated))


def test_flipping_any_single_bit_of_the_nonce_is_detected(vault):
    enc = sealed(vault)
    for byte_i in range(len(enc.nonce)):
        for bit in range(8):
            mutated = bytearray(enc.nonce)
            mutated[byte_i] ^= 1 << bit
            with pytest.raises(DecryptionError):
                open_(vault, enc, nonce=bytes(mutated))


@pytest.mark.parametrize("cut", ["empty", "tag_only", "truncated", "extended"])
def test_truncated_or_extended_ciphertext_is_rejected(vault, cut):
    enc = sealed(vault)
    ct = {
        "empty": b"",
        "tag_only": enc.ciphertext[-16:],
        "truncated": enc.ciphertext[:-1],
        "extended": enc.ciphertext + b"\x00",
    }[cut]
    with pytest.raises(DecryptionError):
        open_(vault, enc, ciphertext=ct)


@pytest.mark.parametrize("nonce", [b"", b"\x00" * 11, b"\x00" * 13])
def test_wrong_length_nonce_is_rejected(vault, nonce):
    with pytest.raises(DecryptionError):
        open_(vault, sealed(vault), nonce=nonce)


# ---- ownership binding (AAD) ----------------------------------------------------------------


def test_a_row_moved_to_another_user_cannot_be_decrypted(vault):
    enc = sealed(vault, user=ALICE)
    assert open_(vault, enc, user=ALICE)
    with pytest.raises(DecryptionError):
        open_(vault, enc, user=BOB)


# ---- keys -----------------------------------------------------------------------------------


def test_wrong_key_cannot_decrypt():
    enc = sealed(TokenVault({"v1": KEY1}, "v1"))
    other = TokenVault({"v1": KEY2}, "v1")  # same label, different key bytes
    with pytest.raises(DecryptionError):
        open_(other, enc)


def test_unknown_key_id_is_a_distinct_operational_error(vault):
    enc = sealed(vault)
    with pytest.raises(UnknownKeyError):
        open_(vault, enc, key_id="v9")


def test_relabelling_the_key_id_does_not_help_an_attacker():
    both = TokenVault({"v1": KEY1, "v2": KEY2}, "v1")
    enc = sealed(both)  # encrypted under v1
    with pytest.raises(DecryptionError):
        open_(both, enc, key_id="v2")  # claim it was v2


@pytest.mark.parametrize(
    "keys,active",
    [
        ({"v1": b"short"}, "v1"),
        ({"v1": KEY1}, "v2"),
        ({"V1": KEY1}, "V1"),
        ({"../x": KEY1}, "../x"),
        ({"v1": KEY1 + b"x"}, "v1"),
    ],
)
def test_vault_refuses_to_start_with_bad_configuration(keys, active):
    with pytest.raises(ValueError):
        TokenVault(keys, active)


def test_vault_builds_from_settings(make_settings):
    vault = TokenVault.from_settings(make_settings())
    assert vault.active_key_id == "v1"
    assert open_(vault, sealed(vault)).get_secret_value() == "tok-ABCDEFGH-1234"


# ---- rotation -------------------------------------------------------------------------------


def test_rotation_moves_tokens_to_the_new_key():
    old = TokenVault({"v1": KEY1}, "v1")
    enc_old = sealed(old, "rotate-me-please-1")

    new = TokenVault({"v1": KEY1, "v2": KEY2}, "v2")  # both keys present during the migration
    assert new.needs_rotation(enc_old.key_id) and not new.needs_rotation("v2")
    assert open_(new, enc_old).get_secret_value() == "rotate-me-please-1"

    enc_new = new.reencrypt(
        ALICE, ciphertext=enc_old.ciphertext, nonce=enc_old.nonce, key_id=enc_old.key_id
    )
    assert enc_new.key_id == "v2" and enc_new.nonce != enc_old.nonce

    only_v2 = TokenVault({"v2": KEY2}, "v2")  # after retiring v1
    assert open_(only_v2, enc_new).get_secret_value() == "rotate-me-please-1"
    with pytest.raises(UnknownKeyError):
        open_(only_v2, enc_old)


def test_reencrypt_keeps_ownership_binding():
    new = TokenVault({"v1": KEY1, "v2": KEY2}, "v2")
    enc = new.reencrypt(ALICE, **_parts(sealed(TokenVault({"v1": KEY1}, "v1"))))
    with pytest.raises(DecryptionError):
        open_(new, enc, user=BOB)


def _parts(enc: EncryptedToken) -> dict:
    return {"ciphertext": enc.ciphertext, "nonce": enc.nonce, "key_id": enc.key_id}


# ---- nothing sensitive leaks through errors, reprs or logs ----------------------------------


def test_all_decryption_failures_look_identical_and_carry_no_context(vault):
    enc = sealed(vault, "super-secret-token-777")
    bad_cases = [
        {"user": BOB},
        {"ciphertext": enc.ciphertext[:-1]},
        {"nonce": b"\x01" * 12},
    ]
    for case in bad_cases:
        with pytest.raises(DecryptionError) as exc:
            open_(vault, enc, **case)
        text = f"{exc.value} {exc.value!r}"
        assert str(exc.value) == GENERIC  # no hint about *why* it failed
        assert "super-secret-token-777" not in text
        assert enc.ciphertext.hex() not in text and enc.nonce.hex() not in text
        assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_reprs_never_expose_keys_ciphertext_or_plaintext(vault):
    enc = sealed(vault, "super-secret-token-777")
    revealed = open_(vault, enc)
    shown = " ".join([repr(vault), repr(enc), repr(revealed), str(revealed), f"{revealed}"])
    assert KEY1.hex() not in shown and str(KEY1) not in shown
    # Python prints bytes as b'...', not hex, so check that form (a hex-only check cannot fail).
    assert repr(enc.ciphertext) not in shown and repr(enc.nonce) not in shown
    assert enc.ciphertext.hex() not in shown
    assert "super-secret-token-777" not in shown
    assert "**********" in shown  # SecretStr masks it if someone logs it by mistake


# ---- input hygiene --------------------------------------------------------------------------


def test_normalize_trims_paste_artifacts_and_wraps_in_secretstr():
    tok = normalize_token("  \n abcDEF123456xyz \r\n")
    assert isinstance(tok, SecretStr) and tok.get_secret_value() == "abcDEF123456xyz"
    assert token_last4(tok) == "6xyz"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "short",
        "has inner space 12345",
        "tab\tinside12345",
        "a" * 513,
        "日本語日本語日本語日本語",
        "ctrl\x00char12345",
        "new\nline-inside-123",
    ],
)
def test_normalize_rejects_unexpected_shapes(bad):
    with pytest.raises(InvalidTokenFormat):
        normalize_token(bad)


def test_exactly_at_the_length_limits_is_accepted():
    assert normalize_token("a" * 8) and normalize_token("a" * 512)


@pytest.mark.parametrize("bad", ["", "a" * 1025])
def test_encrypt_rejects_empty_or_oversized_plaintext(vault, bad):
    with pytest.raises(InvalidTokenFormat):
        vault.encrypt(SecretStr(bad), ALICE)


# ---- through the real database (bytea round trip) -------------------------------------------


@pytest.mark.integration
def test_encrypted_token_survives_the_database_and_stays_bound_to_its_owner(conn, vault):
    alice, bob = repo.create_user(conn), repo.create_user(conn)
    token = SecretStr("integration-token-" + secrets.token_urlsafe(24))
    enc = vault.encrypt(token, alice.id)
    repo.upsert_proof_credential(
        conn,
        alice.id,
        ciphertext=enc.ciphertext,
        nonce=enc.nonce,
        key_id=enc.key_id,
        last4=token_last4(token),
    )

    row = repo.get_proof_credential(conn, alice.id)
    got = vault.decrypt(alice.id, ciphertext=row.ciphertext, nonce=row.nonce, key_id=row.key_id)
    assert got.get_secret_value() == token.get_secret_value()
    assert row.last4 == token.get_secret_value()[-4:]

    with pytest.raises(DecryptionError):  # the same row presented as Bob's is useless
        vault.decrypt(bob.id, ciphertext=row.ciphertext, nonce=row.nonce, key_id=row.key_id)
    raw = conn.execute(
        "select ciphertext from speakyourlog.proof_credentials where user_id = %s", (alice.id,)
    ).fetchone()["ciphertext"]
    assert token.get_secret_value().encode() not in bytes(raw)  # the DB holds no plaintext
