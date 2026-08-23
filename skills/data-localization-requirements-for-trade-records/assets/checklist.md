# Pre-Flight Checklist

- [ ] Is every cloud region mapped to the jurisdiction it *physically* sits in, rather than trusted by name prefix (`eu-west-2` = UK, `eu-central-2` = CH)?
- [ ] Does an unknown origin jurisdiction or an unmapped region resolve to `REVIEW_REQUIRED` rather than passing the gate?
- [ ] Is personal-data egress from China blocked absent a recorded PIPL Art. 38 mechanism, and is non-personal tick egress separately assessed for DSL "important data"?
- [ ] Are Indian **payment system** ledgers kept in India (RBI circular), and are Indian trade records treated as unresolved rather than falsely mandated (SEBI CSCRF PR.DS.S2 in abeyance since 31 Dec 2024)?
- [ ] Are EU third-country transfers gated on a recorded GDPR Chapter V mechanism instead of being blocked as a nonexistent localization violation?
- [ ] Is retention checked per 17a-4 paragraph — 6 years for (a) with the first two easily accessible, 3 years for (b) — rather than one flat number?
- [ ] Does the recordkeeping system meet **either** WORM **or** the 17a-4(f) audit-trail alternative (2022 amendments, effective 3 Jan 2023)?
- [ ] For US/UK records stored offshore, is a retrievable copy held outside any export-controlled jurisdiction so prompt production under 17a-4(j) remains possible?
- [ ] Is the decision audit trail persisted and protected from post-hoc edits?
