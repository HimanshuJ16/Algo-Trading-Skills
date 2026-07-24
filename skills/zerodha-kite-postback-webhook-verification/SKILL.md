---
name: zerodha-kite-postback-webhook-verification
description: >-
  Use when consuming Zerodha Kite Connect order postback webhooks to verify SHA-256 signature authenticity, prevent request spoofing, enforce timestamp replay protection, and guarantee idempotent order state updates
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "zerodha-kite-connect", "postback-webhooks", "webhook-security"]
brokers_frameworks: ["Zerodha Kite Connect v3 API"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a trading bot receives order fill or execution updates via Zerodha Kite Connect postback webhooks over HTTP. Webhooks received over public internet endpoints must never be trusted blindly — an attacker or malicious actor could spoof fake order fill notifications (e.g. fake "COMPLETE" fills) to trick the strategy into submitting invalid hedge orders or corrupting position ledgers. Validating SHA-256 postback checksums, verifying timestamp freshness, and deduplicating incoming postbacks before updating order state is mandatory.

## Prerequisites

- Active Zerodha Kite Connect API secret key stored securely in environment variables or vault.
- HTTPS webhook endpoint receiver (e.g., FastAPI, Flask, or async web server).
- In-memory or database tracking of processed postbacks for idempotency.

## Workflow

1. **Extract Postback Signature & Payload**:
   - Parse incoming HTTP POST request payload and header fields (`order_id`, `timestamp`, `checksum`, `status`, `filled_quantity`).

2. **Replay Attack & Timestamp Verification**:
   - Verify postback timestamp is within acceptable drift window ($\le 300$ seconds from current server time). Reject stale payloads to prevent replay attacks.

3. **Constant-Time SHA-256 Signature Verification**:
   - Compute `expected_checksum = sha256(order_id + timestamp + api_secret)`.
   - Perform constant-time string comparison using `hmac.compare_digest()` to prevent timing side-channel attacks.

4. **Idempotent Order Ledger Update**:
   - Deduplicate incoming postbacks using `order_id` + `status` composite keys.
   - Update internal order ledger (`order-placement-idempotency`) only after cryptographic verification succeeds.

5. **Alerting & Security Audit Logging**:
   - Log failed validation attempts with client IP, timestamp, and payload metadata to trigger security alerts on unauthorized postback injection attempts.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Blind Webhook Trust**: Processing unverified HTTP POST requests without validating `checksum` signature.
- **Timing Attack Vulnerability**: Comparing checksum strings using standard equality (`==`) instead of constant-time `hmac.compare_digest()`.
- **Replay Attacks**: Accepting historical postbacks without checking timestamp drift.
- **Duplicate Processing**: Re-applying identical postbacks repeatedly on network retries without idempotency keys.

## Verification

- Submit valid signed postback payload and confirm `verify_postback()` returns `True`.
- Submit tampered checksum payload and confirm `verify_postback()` raises `PostbackVerificationError` or returns `False`.
- Submit stale timestamp postback ($> 300\text{s}$) and confirm replay attack rejection.
- Run unit test suite `python scripts/test_postback_verifier.py` and confirm 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
