# Pre-Flight Checklist

- [ ] Are `JurisdictionRules` profiles configured for every entity and venue jurisdiction the firm routes to, and reviewed against current regulator publications?
- [ ] Is the short-selling severity ordering agreed as `NONE < REPORTING < PRICE_TEST < BAN`, and were any pre-v2.0.0 integer configs (old 1=UPTICK, 2=REPORTING) re-mapped?
- [ ] Does the pre-trade gate run before routing, on every order, rather than as an offline review?
- [ ] Do unregistered jurisdictions fail closed on all three dimensions, and is `unregistered_jurisdictions` alarmed as a configuration defect?
- [ ] Are LEIs validated with ISO 17442 structure **and** MOD 97-10 check digits, not by length?
- [ ] Is a GLEIF lookup (issuance + active entity status on trade date) performed downstream, since structural validation cannot confirm it?
- [ ] Are natural-person clients routed to national client identifier (RTS 22 Annex II / CONCAT) validation instead of LEI validation?
- [ ] Are `required_obligations` (price test, net short position reporting, LEI tagging) consumed by a downstream system rather than discarded on an approved order?
- [ ] Are audit decisions retained for every approved and rejected order, with the accessor returning copies so records cannot be rewritten?
- [ ] Is there a documented escalation path for mandate-vs-prohibition conflicts, which Strictest Rule Primacy cannot resolve?
