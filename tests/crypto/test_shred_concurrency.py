import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from crypto import shred  # noqa: E402

KEY = os.urandom(32)


def test_parallel_encryptions_under_one_key_have_unique_nonces_and_round_trip():
    guard, out, errs = shred.NonceGuard(), [], []

    def work(t):
        try:
            for i in range(2_000):
                v = f"t{t}-{i}@example.com"
                out.append((v, shred.encrypt(KEY, f"S{t}", "customers.email", v, guard=guard)))
        except Exception as e:  # pragma: no cover
            errs.append(e)
    threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errs and len(out) == 16_000
    assert len({b.split(":")[2] for _, b in out}) == 16_000
    for v, b in out[::97]:                       # the subject is S<thread>, recoverable from the value "t<thread>-<i>@…"
        assert shred.decrypt(KEY, "S" + v[1:v.index("-")], "customers.email", b) == v


def test_forced_duplicate_across_threads_is_refused_exactly_once():
    guard, nonce, results = shred.NonceGuard(), os.urandom(12), []
    barrier = threading.Barrier(2)

    def work():
        barrier.wait()
        try:
            shred.encrypt(KEY, "S", "customers.email", "x", guard=guard, nonce=nonce)
            results.append("ok")
        except shred.NonceReuse:
            results.append("refused")
    ts = [threading.Thread(target=work) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["ok", "refused"]


def test_counter_style_nonce_collision_across_fields_is_the_f2_case():
    guard = shred.NonceGuard()
    counter0 = (0).to_bytes(12, "big")
    shred.encrypt(KEY, "S", "customers.email", "a@x", guard=guard, nonce=counter0)
    with pytest.raises(shred.NonceReuse):
        shred.encrypt(KEY, "S", "customers.full_name", "Ann", guard=guard, nonce=counter0)
