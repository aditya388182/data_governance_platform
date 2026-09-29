import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from govlib.keys import KEY_NAMES, OPTIONAL_KEY_NAMES, fingerprint, load_keys  # noqa: E402

ALL = KEY_NAMES + OPTIONAL_KEY_NAMES
path = Path(sys.argv[1] if len(sys.argv) > 1 else "conf/keys.yml")
if path.exists():
    present = load_keys(path)
    missing = [n for n in ALL if n not in present]
    if missing:
        with open(path, "a") as fh:
            for name in missing:
                fh.write(f'{name}: "{secrets.token_hex(32)}"\n')
    k = load_keys(path)
    what = f"added {', '.join(missing)}" if missing else "left untouched"
    print(f"{path} already exists — {what}. Fingerprints: " + ", ".join(f"{n}={fingerprint(k[n])}" for n in ALL))
    sys.exit(0)
path.parent.mkdir(parents=True, exist_ok=True)
lines = ["# conf/keys.yml — GITIGNORED. Never commit. Created by scripts/init_keys.py"]
for name in ALL:
    lines.append(f'{name}: "{secrets.token_hex(32)}"')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write("\n".join(lines) + "\n")
k = load_keys(path)
print(f"created {path} (mode 600). Fingerprints (safe to log): " + ", ".join(f"{n}={fingerprint(k[n])}" for n in ALL))
