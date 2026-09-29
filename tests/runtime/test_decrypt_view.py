"""Authorized read path round-trips; an erased subject is reported, never decrypted."""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_decrypt_view_round_trip_then_erased(tmp_path):
    env = dict(os.environ, LAKE_LOCAL_ROOT=str(tmp_path), PYTHONWARNINGS="ignore",
               **{n: os.urandom(32).hex() for n in ("GOV_KEK", "GOV_FIXTURES_KEY", "GOV_AUDIT_HMAC_KEY", "GOV_TOKEN_KEY")})
    py = lambda *a: subprocess.run([sys.executable, *a], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)  # noqa: E731
    assert py("scripts/seed_prod_tables.py", "--subjects", "5", "--transactions", "50").returncode == 0
    ok = py("scripts/decrypt_view.py", "CUST-000003")
    assert ok.returncode == 0 and "@example.com" in ok.stdout and "one-way" in ok.stdout
    code = ("import sys; sys.path.insert(0,'.'); from crypto.key_store import KeyStore; from govlib import lake; "
            "from govlib.keys import load_keys; KeyStore(lake.uri(lake.key_store_path()), load_keys()['kek']).destroy_key('CUST-000003')")
    assert py("-c", code).returncode == 0
    gone = py("scripts/decrypt_view.py", "CUST-000003")
    assert gone.returncode == 3 and "SUBJECT ERASED" in gone.stdout and "@example.com" not in gone.stdout
    assert py("scripts/decrypt_view.py", "CUST-000004").returncode == 0            # other subjects unaffected
