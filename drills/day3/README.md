# Day 3 drills — reproduce every result

| PR | Branch | Change | Expected checks | Proves |
|---|---|---|---|---|
| drop status | `drop-status-column` | `contract_edit.py drop ... status --with-suite` + fixture regen | schema ✓ · ge ✓ · pii ✓ · **impact ✗** | compatible-but-breaking |
| ACK | `ack-drop-status` | `ack.py fraud-service --pr <drop PR> --fields status` | all ✓ (notice on impact-bot) | the consumer's sign-off is its own reviewed change |
| drop status (re-run) | `drop-status-column` | `gh pr update-branch` | all ✓ | ACK read from main |
| unregistered consumer | `add-marketing-etl` | copy `drills/day3/marketing-etl` into `consumers/` | all ✓, **UNREGISTERED CONSUMER warning** | warn, not block |
| … + registration | same | `lineage/marketing-etl.yml` + CODEOWNERS rule | all ✓, no unregistered warning | the closure mechanism |
| bonus: rename | `rename-currency-v3` | `contract_edit.py rename ... currency currency_code` | **schema ✗ · ge ✗ · impact ✗** · pii ✓ | the layers agree on a truly breaking change |
