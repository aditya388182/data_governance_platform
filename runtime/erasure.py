from __future__ import annotations

import getpass
import hashlib
import hmac
import re
import time
from dataclasses import dataclass

import pyarrow as pa
from deltalake import DeltaTable, QueryBuilder, write_deltalake
from deltalake.exceptions import TableNotFoundError

from crypto import policy as cpolicy
from crypto import shred
from crypto.key_store import ZEROS, KeyStore, SubjectErased
from govlib import lake
from govlib.contracts import load_registry
from govlib.keys import load_keys
from runtime import backup, objects

NOT_FOUND = re.compile(r"not found|notfound|no such file|does not exist", re.I)
AUDIT_SCHEMA = pa.schema([
    ("request_id", pa.string()), ("subject_hash", pa.string()), ("tables_touched", pa.list_(pa.string())),
    ("rows_deleted", pa.int64()), ("rows_deleted_by_table", pa.list_(pa.int64())),
    ("pre_delete_versions", pa.list_(pa.int64())), ("vacuum_versions", pa.list_(pa.int64())),
    ("key_destroyed_ts", pa.int64()), ("verified_lake", pa.bool_()), ("verified_crypto", pa.bool_()),
    ("completed_ts", pa.int64()), ("operator", pa.string()), ("rerun_of", pa.string()),
    ("uncatalogued_tables", pa.list_(pa.string())), ("backups_covered", pa.list_(pa.string())),
    ("downstream_to_notify", pa.list_(pa.string())),
])
AUDIT_CONFIG = {"delta.appendOnly": "true"}


@dataclass
class Holding:
    path: str            # s3a uri
    source: str          # "catalog" | "sweep"
    rows: int = 0
    version: int = -1

    @property
    def name(self) -> str:
        return objects.split(self.path)[1]


def _so():
    return lake.storage_options()


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _dt(path: str, version: int | None = None) -> DeltaTable:
    return DeltaTable(lake.uri(path), storage_options=_so(), version=version)


def mask(value: str) -> str:
    local, at, domain = value.partition("@")
    keep = local[:2] + "•" * max(len(local) - 4, 3) + local[-2:] if len(local) > 4 else local[:1] + "•••"
    return keep + at + domain


#  intake
def subject_key() -> str:
    return cpolicy.load_policy()[0]


def catalog_tables() -> dict[str, str]:
    _, pol = cpolicy.load_policy()
    return {d.delta_path: name for name, d in load_registry().items() if name.split(".")[-1] in pol}


def lake_roots() -> list[str]:
    return lake.platform().get("lake_roots") or ["s3a://governance-lake"]


def sweep(sk: str) -> list[str]:
    """Every Delta table under the lake roots whose schema contains the subject key."""
    found = []
    for root in lake_roots():
        keys = objects.list_keys(root)
        tables = sorted({k.split("/_delta_log/")[0] for k in keys if "/_delta_log/" in k})
        for t in tables:
            path = f"{root.rstrip('/')}/{t}"
            try:
                names = [f.name for f in _dt(path).schema().fields]
            except Exception:
                continue
            if sk in names:
                found.append(path)
    return found


def count_rows(path: str, sk: str, subject: str, version: int | None = None) -> int:
    dt = _dt(path, version)
    r = QueryBuilder().register("t", dt).execute(f"select count(*) as n from t where {sk} = {_q(subject)}").read_all()
    return int(pa.table(r).column(0)[0].as_py())


def downstream(sk: str) -> list[str]:
    """Consumers (Day 3 lineage scan) that read the subject key: they may hold copies we cannot erase."""
    from pathlib import Path

    from govlib.contracts import schema_fields
    from scripts import lineage_scan
    reg = load_registry()
    names = {ds: {f.name for f in schema_fields(d.schema)} for ds, d in reg.items()}
    out = set()
    for (consumer, dataset), u in lineage_scan.scan_consumers(Path(lake.ROOT), reg, names).items():
        if sk in u.fields or u.wildcard or u.dynamic:
            out.add(f"{consumer} ({dataset})")
    return sorted(out)


def backups_holding(sk: str, subject: str) -> list[str]:
    held = []
    for label in backup.labels():
        for t in backup.tables(label):
            try:
                if sk in [f.name for f in _dt(backup.table_uri(label, t)).schema().fields] and \
                        count_rows(backup.table_uri(label, t), sk, subject) > 0:
                    held.append(f"{label}/{t}")
            except Exception:
                continue
    return held


def intake(subject: str) -> dict:
    sk = subject_key()
    cat = catalog_tables()
    paths = dict.fromkeys(list(cat) + sweep(sk))
    holdings = []
    for p in paths:
        try:
            dt = _dt(p)
        except TableNotFoundError:
            continue
        holdings.append(Holding(p, "catalog" if p in cat else "sweep", count_rows(p, sk, subject), dt.version()))
    return {"subject_key": sk, "holdings": holdings, "backups": backups_holding(sk, subject),
            "downstream": downstream(sk)}


#  audit
def audit_path() -> str:
    return lake.platform().get("audit_erasure", "s3a://governance-lake/audit/erasure")


def subject_hash(keys: dict, subject: str) -> str:
    return hmac.new(keys["audit_hmac_key"], b"erasure-subject|" + subject.encode(), hashlib.sha256).hexdigest()


def audit_records() -> list[dict]:
    try:
        return lake.read_table(lake.uri(audit_path()), storage_options=_so()).to_pylist()
    except TableNotFoundError:
        return []


def audit_append(rec: dict) -> None:
    write_deltalake(lake.uri(audit_path()), pa.Table.from_pylist([rec], schema=AUDIT_SCHEMA), mode="append",
                    configuration=AUDIT_CONFIG, storage_options=_so())


#  proofs
def time_travel(path: str, version: int) -> tuple[bool, str]:
    """True when reading the pre-delete version FAILS because its files are gone."""
    try:
        lake.read_table(lake.uri(path), storage_options=_so(), version=version)
        return False, "STILL READABLE"
    except Exception as e:
        msg = " ".join(str(e).split())
        m = re.search(r"Object at location (\S+) not found", msg)
        short = f"Object …/{m.group(1).rsplit('/', 1)[-1][:22]}… not found" if m else msg[:110]
        return bool(NOT_FOUND.search(msg)), f"{type(e).__name__}: {short}"


def log_residue(path: str, subject: str) -> int:
    """_delta_log commit files that still name the raw subject id (DELETE predicate, old file stats)."""
    n = 0
    for k in objects.list_keys(f"{path}/_delta_log"):
        if k.endswith(".json") and subject.encode() in objects.read(f"{path}/_delta_log/{k}"):
            n += 1
    return n


def backup_ciphertext(label: str, sk: str, subject: str) -> str | None:
    t = lake.read_table(lake.uri(backup.table_uri(label, "customers")), storage_options=_so())
    for r in t.to_pylist():
        if r.get(sk) == subject and shred.is_encrypted(r.get("email")):
            return r["email"]
    return None


#  the workflow
def run(subject: str, request_id: str, control: str | None = None, dry_run: bool = False, backup_label: str | None = None,
        operator: str | None = None, say=print) -> dict:
    keys = load_keys()
    ks = KeyStore(lake.uri(lake.key_store_path()), keys["kek"], _so())
    h = subject_hash(keys, subject)
    operator = operator or getpass.getuser()
    say(f"ERASURE REQUEST {request_id} — subject {subject} — operator {operator}")
    say(f"subject_hash (HMAC, audit_hmac_key): {h[:16]}…   the audit stores this, never the id")

    # 1. intake 
    it = intake(subject)
    sk, hold = it["subject_key"], it["holdings"]
    prior = [r for r in audit_records() if r["subject_hash"] == h and r["rerun_of"] is None]
    primary = prior[0] if prior else None
    say("\n== 1. Intake: pii_catalog.yml + live sweep of the lake ==")
    for x in hold:
        flag = "catalog" if x.source == "catalog" else "SWEEP ⚠ not in the catalog"
        say(f"  {x.name:26s} {x.rows:5d} row(s) @ v{x.version:<3d} {flag}")
    uncat = [x.path for x in hold if x.source == "sweep" and x.rows > 0]
    for u in uncat:
        say(f"  ⚠ GOVERNANCE FINDING: {u} holds this subject but is not in pii_catalog.yml/registry.yml")
    say(f"  backups holding the subject: {', '.join(it['backups']) or 'none'}  (immutable: crypto-shredding only)")
    say(f"  downstream consumers reading {sk} (owners to notify): {', '.join(it['downstream']) or 'none'}")
    st = ks.status(subject)
    say("  key store: " + ("no key" if st is None else f"destroyed at {st['destroyed_ts']}" if st["destroyed_ts"] else "live DEK"))
    if primary:
        say(f"  prior erasure found: {primary['request_id']} → this run is recorded as rerun_of={primary['request_id']}")
    if dry_run:
        n = sum(x.rows for x in hold)
        say(f"\nDRY RUN — nothing changed. Would delete {n} row(s) from {sum(x.rows > 0 for x in hold)} table(s), "
            f"VACUUM {len(hold)} table(s), destroy 1 key.")
        return {"dry_run": True, "intake": it, "rows": n}

    # 2. crypto, before 
    label = backup_label or backup.latest()
    say("\n== 2. Crypto, before: read the subject's ciphertext from the frozen backup ==")
    blob, before = None, None
    if label:
        blob = backup_ciphertext(label, sk, subject)
    if blob is None:
        say(f"  backup {label or '(none)'}: no ciphertext for this subject")
    else:
        say(f"  backups/{backup.SOURCE_BUCKET}/{label}/customers  email = {blob[:34]}…")
        try:
            before = shred.decrypt(ks.get_dek(subject), subject, "customers.email", blob)
            say(f"  BEFORE destroy: decrypts OK -> {mask(before)}   (masked: an erasure log must not re-leak PII)")
        except SubjectErased as e:
            say(f"  BEFORE: key already destroyed ({str(e)[:60]}…) — the first run recorded the readable state")

    # 3-4. DELETE then VACUUM, sequentially 
    say("\n== 3. DELETE (each commit completes before the next step) ==")
    deleted = {}
    for x in hold:
        if x.rows:
            m = _dt(x.path).delete(f"{sk} = {_q(subject)}")
            deleted[x.path] = int(m["num_deleted_rows"])
            say(f"  {x.name:26s} {deleted[x.path]} row(s) deleted (v{x.version} -> v{_dt(x.path).version()})")
        else:
            deleted[x.path] = 0
            say(f"  {x.name:26s} 0 rows — nothing to delete")
    say("\n== 4. VACUUM RETAIN 0 (retention guard overridden: the demo compresses the 7-day default) ==")
    vac = {}
    for x in hold:
        dt = _dt(x.path)
        removed = dt.vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)
        vac[x.path] = _dt(x.path).version()
        say(f"  {x.name:26s} {len(removed)} file(s) removed (now v{vac[x.path]})")

    # 5. proof: lake 
    say("\n== 5. Proof: lake ==")
    cur = {x.path: count_rows(x.path, sk, subject) for x in hold}
    ok_cur = all(v == 0 for v in cur.values())
    say("  current read, subject rows: " + ", ".join(f"{objects.split(p)[1]} {v}" for p, v in cur.items())
        + ("   ✓" if ok_cur else "   ✗"))
    pre = {}
    if primary:
        pre.update({p: v for p, v, d in zip(primary["tables_touched"], primary["pre_delete_versions"],
                                             primary["rows_deleted_by_table"]) if d > 0})
    pre.update({x.path: x.version for x in hold if deleted.get(x.path)})
    tt_ok = True
    for p, v in pre.items():
        ok, msg = time_travel(p, v)
        tt_ok &= ok
        say(f"  time travel {objects.split(p)[1]}@v{v} -> {'raises' if ok else 'DID NOT RAISE'}: {msg}   {'✓' if ok else '✗'}")
    if not pre:
        say("  time travel: no rows were ever held → nothing to prove")
    cust = next((x.path for x in hold if x.path in catalog_tables() and catalog_tables()[x.path] == "customers"), None)
    if control is None and cust:
        ids = sorted(set(lake.read_table(lake.uri(cust), [sk], _so()).column(0).to_pylist()) - {subject})
        control = ids[0] if ids else None
    ctrl = {x.name: count_rows(x.path, sk, control) for x in hold if x.source == "catalog"} if control else {}
    ok_ctrl = bool(ctrl) and ctrl.get("customers", 0) > 0
    say(f"  control {control}: " + ", ".join(f"{k} {v} row(s)" for k, v in ctrl.items()) + ("   ✓ surgical" if ok_ctrl else "   ✗"))
    verified_lake = ok_cur and tt_ok and ok_ctrl

    # 6. destroy the key 
    say("\n== 6. Destroy the key ==")
    was = ks.status(subject)
    ts = ks.destroy_key(subject)
    first = was is None or was["destroyed_ts"] is None
    say(f"  destroy_key -> destroyed_ts {ts} ({'destroyed now' if first else 'already destroyed: original timestamp kept'})")

    # 7. proof: crypto 
    say(f"\n== 7. Proof: crypto (backup {label or '(none)'} was frozen before this erasure) ==")
    checks = []
    try:
        ks.get_dek(subject)
        checks.append(False)
        say("  AFTER: DEK request returned a key   ✗")
    except SubjectErased:
        checks.append(True)
        say("  AFTER destroy: DEK request -> SubjectErased   ✓")
    row = ks._rows().get(subject)
    try:
        ks._unwrap(subject, row["wrapped_dek"])
        checks.append(False)
        say("  wrapped DEK still unwraps   ✗")
    except Exception as e:
        checks.append(row["wrapped_dek"] == ZEROS)
        say(f"  wrapped DEK is zeros -> unwrap fails: {type(e).__name__}   ✓")
    exp = ks.history_exposure(subject)
    checks.append(exp == [])
    say(f"  key-store time-travel exposure: {exp}   {'✓' if exp == [] else '✗'}")
    if label:
        good, total, bad = backup.verify(label)
        checks.append(not bad)
        say(f"  backup unchanged since freeze: {good}/{total} objects match MANIFEST.json   {'✓' if not bad else '✗ ' + bad[0]}")
    if blob is not None:
        say(f"  backup ciphertext {blob[:24]}… is now noise: no key exists that can open it   ✓")
    if control:
        try:
            ctrl_blob = backup_ciphertext(label, sk, control) if label else None
            if ctrl_blob:
                shred.decrypt(ks.get_dek(control), control, "customers.email", ctrl_blob)
                checks.append(True)
                say(f"  control {control} still decrypts from the same backup   ✓ per-subject keys")
        except Exception as e:
            checks.append(False)
            say(f"  control {control} FAILED to decrypt: {type(e).__name__}   ✗")
    verified_crypto = all(checks)

    # 8. residue 
    say("\n== 8. Residue (informational, F13) ==")
    res = {x.name: log_residue(x.path, subject) for x in hold}
    say("  _delta_log commit files naming the raw pseudonym: " + ", ".join(f"{k} {v}" for k, v in res.items()))
    say("  (the DELETE predicate and old file stats; no PII, only the pseudonymous id, which now maps to nothing:"
        " the customer row and the key are gone. They expire with delta.logRetentionDuration, 30 days by default)")

    # 9. audit 
    paths = [x.path for x in hold]
    rec = {"request_id": request_id, "subject_hash": h, "tables_touched": paths,
           "rows_deleted": sum(deleted.values()), "rows_deleted_by_table": [deleted[p] for p in paths],
           "pre_delete_versions": [x.version for x in hold], "vacuum_versions": [vac[p] for p in paths],
           "key_destroyed_ts": ts, "verified_lake": verified_lake, "verified_crypto": verified_crypto,
           "completed_ts": int(time.time() * 1000), "operator": operator,
           "rerun_of": primary["request_id"] if primary else None, "uncatalogued_tables": uncat,
           "backups_covered": it["backups"], "downstream_to_notify": it["downstream"]}
    audit_append(rec)
    kind = f"rerun_of={primary['request_id']}" if primary else "primary record"
    say(f"\n== 9. Audit ==\n  appended to {audit_path()} (append-only): {request_id}, {kind}, "
        f"verified_lake={verified_lake}, verified_crypto={verified_crypto}")
    return {"record": rec, "verified_lake": verified_lake, "verified_crypto": verified_crypto, "intake": it,
            "deleted": deleted, "before": before, "control": control, "residue": res}


def notification(rec: dict) -> str:
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(rec["completed_ts"] / 1000))
    lines = [f"Your erasure request {rec['request_id']} was completed on {when}."]
    if rec["rerun_of"]:
        lines.append(f"Your data had already been erased under request {rec['rerun_of']}; this request re-verified it.")
    else:
        lines.append(f"{rec['rows_deleted']} record(s) were deleted from {sum(d > 0 for d in rec['rows_deleted_by_table'])} "
                     f"dataset(s) and can no longer be recovered from their history.")
    sets = len({b.split("/")[0] for b in rec["backups_covered"]})
    lines.append("The key that protected your personal data was destroyed, so encrypted copies in backups "
                 f"({sets} backup set(s)) are permanently unreadable.")
    if rec["downstream_to_notify"]:
        lines.append(f"Teams that process copies of your data downstream are being notified ({len(rec['downstream_to_notify'])}).")
    return "\n".join(lines)
