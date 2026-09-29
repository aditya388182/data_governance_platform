#!/usr/bin/env python3
import os
import sys
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crypto import shred  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
key, value = os.urandom(32), "firstname.lastname1234@example.com"
blob = shred.encrypt(key, "CUST-000001", "customers.email", value)
g = shred.NonceGuard()
cases = [("HMAC-SHA256 tokenize", lambda: shred.tokenize(key, value)),
         ("AES-GCM encrypt (random nonce + AAD)", lambda: shred.encrypt(key, "CUST-000001", "customers.email", value, guard=g)),
         ("AES-GCM decrypt", lambda: shred.decrypt(key, "CUST-000001", "customers.email", blob))]
print(f"| Operation ({N:,} fields, {len(value)}-char value) | µs / field | fields / s |\n|---|---:|---:|")
for name, fn in cases:
    best = min(timeit.repeat(fn, number=N, repeat=3)) / N
    print(f"| {name} | {best * 1e6:.2f} | {1 / best:,.0f} |")
