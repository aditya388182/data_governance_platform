# Day 4 drills

| Step | Command | Expected |
|---|---|---|
| Throwaway key lifecycle | `python scripts/key_admin.py demo` | exposure `[1]` before VACUUM, `[]` after; destroy idempotent; re-mint refused |
| Seeded violation | `python drills/day4/inject_bad_rows.py` | 7 rows appended (4 negative amounts, 3 currency `ZZ`). Re-running the drill later? Add `--seed 8` (9, …): the same rows are already in quarantine and would not be copied again |
| Checkpoint | `airflow dags test ge_checkpoint_daily <date>` or `python scripts/ge_checkpoint.py` | transactions FAIL, 7 quarantined, source untouched, alerts FIRING |
| Evidence | `python scripts/show_quarantine.py` | 7 rows, expectations, owner; 7/7 still in source |
| Owner remediation | `python drills/day4/remediate.py` then the checkpoint again | PASS, alerts resolve, quarantine keeps 7 |
