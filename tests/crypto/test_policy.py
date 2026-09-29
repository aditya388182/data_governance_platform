import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from crypto import policy, shred  # noqa: E402
from crypto.key_store import KeyStore  # noqa: E402

KEYS = {"token_key": os.urandom(32)}


@pytest.fixture()
def ks(tmp_path):
    return KeyStore(str(tmp_path / "ks"), os.urandom(32))


def txns():
    return pd.DataFrame({"customer_id": ["C1", "C2", "C1"], "merchant_id": ["M-1", "M-1", "M-2"],
                         "receipt_email": ["a@x.com", None, "a@x.com"], "amount_minor": [1, 2, 3]})


def test_merchant_tokens_joinable_across_customers_f4(ks):
    out = policy.protect("transactions", txns(), ks, KEYS)
    assert out.merchant_id[0] == out.merchant_id[1] != out.merchant_id[2]          # same merchant, two customers
    assert out.merchant_id[0] == shred.tokenize(KEYS["token_key"], "M-1")


def test_receipt_email_encrypted_per_subject_f9(ks):
    out = policy.protect("transactions", txns(), ks, KEYS)
    assert shred.is_encrypted(out.receipt_email[0]) and out.receipt_email[1] is None
    assert out.amount_minor.tolist() == [1, 2, 3]                                   # non-PII untouched
    back = policy.reveal("transactions", out, ks)
    assert back.receipt_email[0] == "a@x.com" and back.merchant_id[0] == out.merchant_id[0]


def test_customers_ssn_token_is_per_subject(ks):
    df = pd.DataFrame({"customer_id": ["C1", "C2"], "email": ["a@x", "b@x"], "ssn": ["111-11-1111"] * 2,
                       "full_name": ["A", "B"], "country": ["US", "US"]})
    out = policy.protect("customers", df, ks, KEYS)
    assert out.ssn[0] != out.ssn[1]                    # same SSN, different subjects -> unlinkable tokens
    assert all(shred.is_encrypted(v) for v in out.email) and out.country.tolist() == ["US", "US"]


@pytest.mark.parametrize("entry", ["{class: DIRECT_IDENTIFIER, treatment: TOKENIZE_HMAC, key_scope: PLATFORM}",
                                   "{class: QUASI_IDENTIFIER, treatment: TOKENIZE_HMAC, key_scope: GLOBAL}"])
def test_invalid_key_scope_is_rejected(tmp_path, entry):
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts/pii_catalog.yml").write_text(f"subject_key: customer_id\ncolumns:\n  customers.ssn: {entry}\n")
    with pytest.raises(policy.PolicyError):
        policy.load_policy(tmp_path)


def test_platform_scope_needs_token_key(ks):
    with pytest.raises(Exception, match="token_key"):
        policy.protect("transactions", txns(), ks, {})
