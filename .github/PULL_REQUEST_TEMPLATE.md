## Change

Describe the bounded behavior changed and the evidence supporting it.

## Gates

- [ ] `make verify` (ruff format, ruff lint, mypy strict, pytest with the 90% coverage gate)
- [ ] `make demo` (the gates run against the toy and a report renders)
- [ ] New or changed gate has a paired self-test proving it can fail (self-test doctrine)
- [ ] English and Spanish cases changed as peers, not one bolted onto the other
- [ ] No em dashes in prose; counts are emitted by the harness, not asserted
- [ ] Any SIMM/SAM/GC identifier added to docs was read against the source, or omitted and said so
- [ ] No claim of California approval, endorsement, or compliance
