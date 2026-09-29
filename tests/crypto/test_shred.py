import os
import sys
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from crypto import shred  # noqa: E402

K1, K2 = os.urandom(32), os.urandom(32)


def test_tokenize_is_deterministic_keyed_and_one_way():
    t = shred.tokenize(K1, "123-45-6789")
    assert t == shred.tokenize(K1, "123-45-6789") and len(t) == 64 and int(t, 16) >= 0
    assert t != shred.tokenize(K2, "123-45-6789")          # per-subject key -> not linkable across subjects
    assert "123" not in t


def test_encrypt_round_trip_and_randomized():
    a = shred.encrypt(K1, "CUST-000001", "customers.email", "ann@example.com")
    b = shred.encrypt(K1, "CUST-000001", "customers.email", "ann@example.com")
    assert a.startswith("enc:v1:") and a != b                # random nonce: equal plaintexts do not show
    assert shred.decrypt(K1, "CUST-000001", "customers.email", a) == "ann@example.com"


@pytest.mark.parametrize("key,subject,field", [(K2, "CUST-000001", "customers.email"),      # wrong key (post-erasure)
                                               (K1, "CUST-000002", "customers.email"),      # moved to another subject
                                               (K1, "CUST-000001", "customers.full_name")]) # moved to another column
def test_decrypt_fails_closed(key, subject, field):
    blob = shred.encrypt(K1, "CUST-000001", "customers.email", "ann@example.com")
    with pytest.raises(InvalidTag):
        shred.decrypt(key, subject, field, blob)


def test_nonce_guard_refuses_reuse_under_same_key_only():
    g = shred.NonceGuard()
    n = os.urandom(12)
    shred.encrypt(K1, "s", "t.email", "x", guard=g, nonce=n)
    shred.encrypt(K2, "s", "t.email", "x", guard=g, nonce=n)          # other key: fine
    with pytest.raises(shred.NonceReuse):
        shred.encrypt(K1, "s", "t.full_name", "y", guard=g, nonce=n)  # the F2 collision: other field, same key


def test_many_encryptions_one_key_unique_nonces():
    g = shred.NonceGuard()
    blobs = {shred.encrypt(K1, "s", f"t.f{i % 3}", "v", guard=g) for i in range(20_000)}
    assert len({b.split(":")[2] for b in blobs}) == 20_000
