# consumers/ — vendored stand-ins for downstream repositories

These directories are **stand-ins** for the consumer teams' own repos. In
production, `scripts/lineage_scan.py` would clone each repo listed in
`contracts/lineage/*.yml` (`repo:` would be a Git URL) and scan the checkout.
Here the repos are vendored so the whole platform runs from one repository;
the scanning logic is identical, only the fetch step is stubbed (the same
one-repo-runnable trade-off as Project 2's CSV stand-in for Project 1's CDF).

| Consumer | Team | Reads | How |
|---|---|---|---|
| `fraud-service` | fraud-team | `payments.transactions` | Kafka subject via `from_avro` (streaming) |
| `finance-recon` | finance-data-team | `payments.transactions` | Delta path (batch) |

The code is real PySpark (not planted markers): the scanner must understand
genuine patterns — `from_avro(...).alias("e")`, `select("e.currency")`,
`F.sum("amount_minor")`, `selectExpr(...)`, Delta `load(path)`.
