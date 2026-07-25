# Pre-Flight Checklist — structured-logging-for-post-incident-forensics

- [ ] All log events emitted as structured JSON with required fields.
- [ ] Correlation IDs assigned and linking related events.
- [ ] Sequence numbers are monotonically increasing.
- [ ] Timeline reconstruction by correlation_id works correctly.
- [ ] Event type taxonomy covers all critical trading events.
- [ ] Run `python scripts/test_structured_logger.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
