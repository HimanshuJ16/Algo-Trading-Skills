---
name: broker-api-deprecation-notice-monitoring
description: >-
  Use when building production broker adapters to monitor HTTP deprecation headers (RFC 8594 / Sunset header) and developer changelog RSS feeds, alerting ops teams before API endpoint retirements break live trading bots.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "deprecation-monitoring", "sunset-headers", "rfc-8594", "changelog-parser", "api-maintenance"]
brokers_frameworks: ["Deprecation Monitor", "Python Requests", "RFC 8594"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating long-running algorithmic trading systems connected to REST/WebSocket broker APIs. Brokerages frequently retire legacy endpoints or update payload schemas, issuing RFC 8594 `Sunset` headers or developer changelog announcements weeks before shutdown. This skill automatically scans HTTP response headers and changelog feeds to alert engineering teams to impending API sunset dates before live production bots crash.

## Prerequisites

- Broker API adapter with access to HTTP response headers.
- Target broker developer changelog RSS/JSON feed URL.

## Workflow

1. **Inspect Live Response Headers (RFC 8594)**:
   - Check HTTP response headers on every API call for `Deprecation: true`, `Sunset: {date_string}`, or `X-API-Deprecation-Warning`.

2. **Ingest Developer Changelog Feeds**:
   - Periodically poll broker developer changelogs / RSS feeds for keywords (`deprecated`, `sunset`, `breaking change`, `end of life`).

3. **Calculate Days to Sunset**:
   - Parse sunset date and compute remaining operational days $D = \text{SunsetDate} - \text{CurrentDate}$.

4. **Classify Alert Urgency**:
   - $D > 30$ days: Log `NOTICE`.
   - $7 < D \le 30$ days: Raise `WARNING_30_DAYS`.
   - $D \le 7$ days: Raise `CRITICAL_SUNSET_IMMINENT` and trigger migration alert.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unparsed HTTP Sunset Headers**: Ignoring standard RFC 8594 `Sunset` response headers returned by modern API gateways.
- **Ambiguous Date Formats**: Failing to parse HTTP-date formats (RFC 1123, e.g. `Wed, 11 Nov 2026 00:00:00 GMT`) correctly.
- **Unmonitored WebSocket Feed Deprecations**: Relying solely on REST header checks while ignoring WebSocket control frame deprecation warnings.

## Verification

- Submit mock HTTP response with `Sunset: Wed, 11 Nov 2026 00:00:00 GMT` header and verify extraction and countdown calculation.
- Simulate changelog feed item with breaking change keywords and verify alert classification.
- Run `python scripts/test_deprecation_monitor.py` and confirm 100% pass rate.

## Related Skills

- `broker-api-versioning-migration-playbook`
- `broker-status-page-monitoring-integration`
- `structured-logging-for-post-incident-forensics`
---
