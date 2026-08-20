# Bybit V5 Pre-Flight Checklist

## Credentials
- [ ] Does the API key belong to the **same domain** as the base URL (mainnet / testnet /
      mainnet-demo / testnet-demo)? A mismatch returns `retCode 10003`, not a clear error.
- [ ] Is the key IP-whitelisted to the egress address the process actually uses (`10010`)?
- [ ] Is the secret loaded from a secrets manager, and absent from every log record,
      exception message and serialised config?

## Clock
- [ ] Is an NTP daemon running, with drift alerting?
- [ ] Does startup verify the local clock against `GET /v5/market/time` and fail closed?
- [ ] Has anyone tried to "fix" drift by enlarging `recv_window`? It cannot help a fast
      clock — the forward tolerance is a fixed 1000 ms — and it widens the replay window.

## Signing
- [ ] Is the transmitted query string byte-identical to the signed one (no re-encoding by
      the HTTP client)?
- [ ] Is the transmitted body the signed string (`data=req["body"]`, never `json=params`)?
- [ ] Are `qty`, `price`, `triggerPrice`, `takeProfit` and `stopLoss` decimal **strings**?
- [ ] Are optional parameters omitted rather than sent as `"None"`?
- [ ] Do GET requests carry **no** body? Bybit returns HTTP 403 for a GET with an empty
      JSON body.

## Rate limits
- [ ] Is backoff driven by `X-Bapi-Limit-Status` **relative to** `X-Bapi-Limit`? An
      absolute "below 10" rule fires constantly on 10/s order endpoints and never protects
      a 50/s one.
- [ ] Is an unknown budget (headers missing or unparseable) treated as unknown rather than
      as full?
- [ ] Is there a process-wide cap under the per-IP ceiling of 600 requests / 5 seconds,
      shared by every strategy on this egress IP?
- [ ] Does the HTTP 403 / `10018` path terminate all sessions and wait ≥10 minutes?

## Order safety
- [ ] Is an `orderLinkId` (≤36 chars, unique) minted and **persisted before dispatch** for
      every order?
- [ ] Does a timeout trigger a lookup by `orderLinkId` before any resubmission?
- [ ] Does a resubmission reuse the original `orderLinkId`, so `110072` blocks a duplicate?
- [ ] Is the retry count bounded, with escalation instead of an endless loop?
- [ ] Are deterministic errors (`10002`, `10004`, `10005`, `10010`) excluded from retry?

## Before mainnet
- [ ] Have all failure paths, not just the happy path, been exercised on testnet?
- [ ] Has a signed request been sent end-to-end through the real transport and returned
      `retCode 0`? Unit tests cannot see a transport that mutates the request.
