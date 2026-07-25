# Pre-Flight / Sign-off Checklist — broker-api-deprecation-notice-monitoring

Use this before considering the skill's implementation complete.

- [ ] **RFC 8594 Sunset Header Inspection:** Confirm HTTP responses are scanned for `Sunset` and `Deprecation` headers.
- [ ] **Changelog Feed Parsing:** Confirm RSS/JSON developer feeds are parsed for deprecation keywords.
- [ ] **Sunset Date Parsing:** Confirm RFC 1123 and ISO date strings are parsed into UTC date objects.
- [ ] **Urgency Classification:** Confirm $D \le 7$ days triggers `CRITICAL_SUNSET_IMMINENT` alerts.
- [ ] **Automated Testing:** Run `python scripts/test_deprecation_monitor.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
