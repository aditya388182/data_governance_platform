# Frozen test universe for the schema-gate self-tests

These files are copies of the Day 1 contracts (schema **v1**, the Day 1
registry) and the Day 1 drill payloads. The self-tests build throwaway git repos
from THESE files, never from the live `contracts/` directory.

Why: the live contracts change as PRs merge (PR A turned v1 into v2). Tests that
read live contracts as test data start failing — or silently pass — depending on
what is on main. The live contracts are judged by the gate itself; the
self-tests judge the gate's logic against a universe that never moves.
Do not edit these files to make a test pass.
