"""Crypto foundation (Project 4, Day 4): per-subject keys, tokenization, AES-GCM."""
import os as _os

_os.environ.setdefault("RUST_LOG", "error")   # silence delta-rs/DataFusion WARN noise (must precede any deltalake import)
