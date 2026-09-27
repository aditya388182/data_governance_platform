# Frozen test universe for tests/pii

Copies of the Day 2 contracts (payments schema v2, the initial PII catalog, the Day 2
registry) plus drill payloads. Self-tests build throwaway repos from THESE files, never
from the live contracts/ directory, so merging a PR (e.g. receipt_email) cannot break or
silently weaken them. The live contracts are judged by the gate itself.
Do not edit these files to make a test pass.
