# Frozen test universe for tests/impact

Copies of the Day 3 contracts (payments schema v3 with receipt_email, the Day 2 registry),
the lineage catalog, CODEOWNERS and the two vendored consumers as they are at the start of
Day 3 (fraud-service selects e.status), plus the marketing-etl drill consumer. Self-tests
build throwaway git repos from THESE files, never from the live contracts/, consumers/ or
contracts/lineage/ — those change during Day 3's own drills (drop status, ACK, register
marketing-etl, fraud-service migration). Do not edit these files to make a test pass.
