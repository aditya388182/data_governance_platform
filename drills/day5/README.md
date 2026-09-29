# Day 5 drills

| Step | Command | Expected |
|---|---|---|
| Lake lab (throwaway, local) | `python drills/day5/lake_lab.py` | DELETE leaves the row in time travel; RETAIN 0 refused; after the override, time travel RAISES; residue 1 |
| Freeze the backup | `python scripts/simulate_backup.py` | N objects + MANIFEST.json; a second run for the same label is REFUSED |
| Before | `python scripts/decrypt_view.py CUST-000011 --from-backup` | decrypts |
| Dry run | `python scripts/erasure_workflow.py --subject CUST-000011 --request-id GDPR-2026-0142 --dry-run` | 3 holding tables (quarantine via SWEEP ⚠), nothing changed |
| Erase | the same command with `--control CUST-000007`, without `--dry-run` | `RESULT: ERASED AND VERIFIED` |
| After | `decrypt_view.py CUST-000011 --from-backup` / `erasure_audit.py timetravel GDPR-2026-0142 --table customers` | exit 3 / traceback `… not found` |
| Rerun | the erase command again | 0 rows, key timestamp kept, `rerun_of=GDPR-2026-0142` |
