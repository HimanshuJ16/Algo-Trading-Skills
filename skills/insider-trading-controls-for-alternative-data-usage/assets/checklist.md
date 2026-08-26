# Pre-Flight Checklist — Alt-Data Trading Eligibility

Run before a dataset drives trading, and again on every re-diligence trigger.

## Inputs

- [ ] A current `DiligenceRecord` exists from `alternative-data-vendor-due-diligence-checklist` (this gate carries that outcome; it does not re-derive it).
- [ ] Every boolean in the spec is a real `bool` mapped from evidence the firm holds — no `'yes'`/`'no'` strings, no vendor assertion standing alone.
- [ ] Unknown answers were set conservatively (`has_mnpi_risk=True`, positive controls `False`) and escalated, not guessed permissively.

## MNPI provenance (Rule 10b-5 / Section 204A)

- [ ] Origin traced: no hacked, leaked, or unauthorised-access material in the chain.
- [ ] No upstream contributor owed a duty of confidentiality to an issuer, or supplied the data under use-restricting terms.
- [ ] Dataset reveals a consumer/industry trend, not a specific imminent non-public corporate outcome.
- [ ] Where provenance is unresolved, `has_mnpi_risk=True` and legal escalated.

## Vendor diligence and terms of service

- [ ] Diligence record is **current** — not superseded by a change in the vendor's collection methodology.
- [ ] Vendor's representations independently verified (right-to-audit exercised, sample-data inspection, or third-party attestation). App Annie's written aggregation/anonymisation promises were false; a representation alone is not a control.
- [ ] For scraped sources: terms re-read recently, `robots.txt` respected, no simulated accounts, no CAPTCHA bypass, nothing scraped behind a login without authorisation.
- [ ] Contract exposure considered separately from CFAA exposure — lawful scraping can still breach a contract.

## PII scrubbing and panel aggregation

- [ ] Identifier removal **verified**, and singling-out, linkability and inference risk assessed (GDPR Recital 26; CCPA/CPRA § 1798.140(b), (m)).
- [ ] `panel_aggregation_count` meets the firm's `min_panel_aggregation_count`.
- [ ] The threshold is recorded as **firm policy** — no regulator prescribes a number, and the file does not claim one does.

## Earnings blackout

- [ ] `hours_to_earnings_release` is the **signed** distance to the nearest release (+ before, − after), or `None` if none is scheduled.
- [ ] The window length is recorded as firm policy, with its calibration rationale. It has no regulatory basis; the Rule 10b5-1(c) cooling-off periods govern insider trading plans, not research data.
- [ ] `BLACKOUT_WINDOW_RESTRICTED` routes to a scheduled re-check, not to the legal escalation path used for rejections.

## Record

- [ ] The full `AltDataComplianceReport` is persisted — including `failed_controls`, not just `risk_classification`.
- [ ] Reviewer, timestamp, and the threshold policy in force are captured alongside it.
- [ ] Every `is_*_cleared` flag on the persisted record reflects a control that was actually evaluated.
- [ ] The re-diligence trigger (`next_review_date`, methodology change, red flag, earnings cycle, policy change) is on a calendar.
