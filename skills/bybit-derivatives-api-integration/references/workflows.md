# Bybit V5 Integration Procedure

## 1. Key provisioning

1. Generate the key in the console of the environment you will actually call. Mainnet,
   testnet, mainnet-demo and testnet-demo are separate key domains; using a key against
   the wrong host returns `retCode 10003`, which reads like an invalid secret.
2. Restrict it to the permissions the strategy needs and whitelist the egress IP. A
   request from an unlisted IP fails with `retCode 10010`.
3. Choose the account type deliberately (Unified Trading vs. Contract): it changes which
   `category` values and endpoints are valid, and a mismatch surfaces as HTTP 404 rather
   than a permission error.
4. Store the secret in a secrets manager, never in the repository or a config file that
   gets logged. See `centralized-secrets-management-vault-integration`.

## 2. Clock

1. Run `chronyd` or `ntpd` and alert on drift. This is Bybit's documented recommendation.
2. On startup, call `GET /v5/market/time` and check
   `BybitV5Authenticator.is_within_acceptance_window(local_ms, server_ms, recv_window)`.
   Fail startup rather than discovering the problem on the first order.
3. Only if NTP is genuinely unavailable, call `sync_with_server_time(server_time_ms)` and
   re-sync at least every few minutes — the module refuses to sign with an offset older
   than `max_offset_age_s`, because a stale correction hides ongoing drift instead of
   fixing it. A single-sample offset also absorbs part of the round-trip latency of the
   call that measured it, so it is strictly worse than a synchronised clock.

## 3. Signing and dispatch

1. Build the parameter dict with **strings** for `qty`, `price`, `triggerPrice`,
   `takeProfit` and `stopLoss`.
2. For an order, mint `orderLinkId` with `new_order_link_id(prefix)` and persist it with
   the order intent **before** dispatch.
3. Call `sign_request(method, endpoint, params)`.
4. Send exactly what it returns:
   - GET → send `url` (query already appended), no body.
   - POST → send `url`, and `body` as the raw request body.
   Do not let the HTTP client rebuild the query from a dict or re-serialise the JSON.

   ```python
   req = auth.sign_request("POST", "/v5/order/create", params)
   resp = session.post(req["url"], headers=req["headers"], data=req["body"], timeout=5)
   ```

   `data=req["body"]` — not `json=params`, which re-serialises and breaks the signature.

## 4. Response handling

1. HTTP status first, then `retCode`. A 200 with a non-zero `retCode` is a failure.
2. Update pacing: `RateLimitSnapshot.from_headers(resp.headers)`; back off while
   `should_throttle()` is true. Treat a `None` snapshot as unknown, not as healthy.
3. Classify the failure before deciding to retry:

   | Outcome | Action |
   |---|---|
   | `retCode 0` | Success |
   | `10002`, `10003`, `10004`, `10005`, `10010` | Deterministic. Fix the cause; retrying burns rate limit |
   | `10006`, HTTP 429 | Back off, then retry |
   | HTTP 403 / `10018` | IP ban. Terminate **all** HTTP sessions and wait ≥10 minutes |
   | `110072` | Duplicate `orderLinkId` — the order already exists. **Not** a failure to retry; reconcile |
   | Network timeout on POST | Ambiguous. Do not resubmit blindly — see below |

## 5. Ambiguous submissions

A timeout on `POST /v5/order/create` means the order may already be live. The bounded,
safe sequence is:

1. Query `GET /v5/order/realtime` (and `/v5/order/history`) by the `orderLinkId` you
   persisted before dispatch.
2. If it is present, adopt it. Reconciliation, not resubmission.
3. If it is absent, resubmit **with the same `orderLinkId`**. If Bybit had in fact
   accepted the first attempt, the retry is rejected with `retCode 110072` rather than
   opening a second position.
4. Cap the attempts. An unbounded retry loop against an ambiguous order state is exactly
   what pre-trade risk controls exist to prevent.

## 6. Promotion to mainnet

1. Exercise every code path against testnet, including the failure paths above.
2. Re-verify the base URL and that the key belongs to the mainnet domain.
3. Set a process-wide request budget below the 600-per-5-second IP ceiling, shared across
   every strategy behind the same egress IP.
4. Start with minimum-size orders and confirm fills, fees and position deltas reconcile
   before scaling. See `paper-to-live-promotion-checklist`.
