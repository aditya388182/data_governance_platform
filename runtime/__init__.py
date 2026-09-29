"""Runtime enforcement (Project 4, Day 4): checkpoints over live Delta, quarantine, metrics."""
import os as _os

_os.environ.setdefault("RUST_LOG", "error")   # silence delta-rs/DataFusion WARN noise (must precede any deltalake import)
