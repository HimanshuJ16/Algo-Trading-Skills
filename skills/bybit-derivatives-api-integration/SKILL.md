---
name: bybit-derivatives-api-integration
description: Institutional-grade Bybit V5 API integration handling HMAC-SHA256 signature
  generation, NTP timestamp compliance, and REST rate limit tracking.
domain: Execution
subdomain: Venue Integration
tags:
- bybit
- crypto-derivatives
- v5-api
- hmac-sha256
- rate-limiting
brokers_frameworks:
- Bybit V5 REST
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating a quantitative trading system directly with the Bybit V5 API for trading crypto derivatives (Perpetuals, Futures). Direct integration is often preferred over heavy SDKs in low-latency environments to ensure strict control over thread-safety, request signing, and rate limit (`10006` errors) backoff logic.

## Prerequisites

- Bybit API Key and API Secret with Derivatives trading permissions.
- System clock synchronized via NTP (Bybit strictly enforces `recv_window` and timestamp freshness).
- Python 3.9+ with `requests` and `hmac` libraries.

## Workflow

1. **Authentication Configuration**: Initialize `BybitV5Client` with your API credentials and environment (Mainnet or Testnet).
2. **Signature Generation**: The engine automatically constructs the payload string: `timestamp + api_key + recv_window + jsonBody` and signs it using HMAC-SHA256.
3. **Execution Routing**: Send normalized JSON payloads to endpoints like `/v5/order/create`.
4. **Rate Limit Tracking**: Monitor the HTTP response headers (e.g., `X-Bapi-Limit-Status`) to dynamically back off before hitting `10006` blocks.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Timestamp Drift**: If your server's clock drifts by more than the `recv_window` (default 5000ms), all requests will be rejected with an authentication error.
- **Query String vs Body Sorting**: V5 requires GET requests to sort query parameters alphabetically before signing, while POST requests sign the raw JSON body string directly.
- **Ignoring Headers**: Polling blindly without tracking the `X-Bapi-Limit-Status` header will result in temporary API bans.

## Verification

- Simulate an authenticated request payload and verify the generated HMAC-SHA256 signature matches known test vectors.
- Run `python scripts/test_bybit_derivatives_api_integration.py` to verify the state machine.

## Related Skills

- `binance-futures-testnet-to-mainnet-promotion`
- `broker-side-order-throttle-detection`
