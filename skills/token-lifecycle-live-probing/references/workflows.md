# Deep Workflow Reference — token-lifecycle-live-probing

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Designate the probe — endpoint *and* method.**
   - A cheap read call: Kite `GET /user/profile` or `GET /user/margins`, Fyers
     `GET /profile`, Breeze `GET /customerdetails`.
   - Never an order-related endpoint. And confirm the method: Breeze's `funds` path
     reads on `GET` and writes a funds allocation on `POST`.

2. **Probe first, timestamp never.**
   - On bot startup, issue the probe with the cached token. Do not gate it behind a
     TTL comparison — for Fyers there is no published TTL to compare against.

3. **3-outcome classification.**
   - **VALID** — 2xx *and*, where the broker wraps errors in a 2xx envelope, a body
     that does not itself report an error.
   - **INVALID** — a status the broker documents as an auth failure: Kite `403`
     `TokenException`, Fyers `401` with `"code": -16`. Trigger headless
     re-authentication (`headless-broker-auth-patterns`).
   - **AMBIGUOUS** — timeout, network drop, 5xx, `429`, `408`, or any status not in
     your classification table. Retry with capped, jittered backoff.
   - Configure `invalid_codes` and `retryable_codes` per broker rather than accepting
     the defaults blind; see `references/standards.md` for what each broker documents.

4. **Exhausted retries escalate; they do not log in.**
   - `verify_and_refresh_token` raises `AmbiguousProbeError` and does **not** call
     `reauth_fn`. The cached token rides along on the exception so the caller can keep
     it cached and retry on the next cycle.
   - Rationale: an ambiguous probe means the broker did not answer. Logging in at that
     moment aims a request at the login endpoint — the one most likely to be
     rate-limited — while the broker is already degraded.

5. **Re-authentication & post-auth verification.**
   - On `INVALID`, invoke `reauth_fn()`, then re-probe the freshly issued token
     (with the same bounded backoff) before marking the bot ready to trade.
   - If that verification probe does not return VALID, `TokenVerificationError` is
     raised **carrying the new token**. Persist it before retrying: the login
     succeeded, so discarding the token spends a second login for nothing.

6. **Empirical token lifespan tracking.**
   - Record each observed lifespan with `record_lifespan()` (issue timestamp →
     invalidation-detected timestamp).
   - A negative lifespan raises `ValueError` rather than being clamped to zero — a
     clock-skew artefact entering the baseline as a 0-second lifetime would make every
     subsequent token look overdue for refresh.
   - `should_proactively_refresh()` compares elapsed time against the **minimum**
     observed lifespan minus a safety margin, and returns `False` until at least
     `min_samples` (default 3) observations exist. The minimum, because a mean sits
     above a lifespan already observed to end sooner; the sample floor, because one
     hand-revoked token during testing would otherwise permanently drag the refresh
     forward.
   - This tunes *when* to refresh. It never replaces the probe.

## Failure Modes Observed in Production

- **Timestamp-only expiry checks:** relying on documented TTL timestamps without live
  probing, attempting trades with invalidated tokens during market open.
- **Two-outcome classification:** a `probe_fn` returning `bool` cannot distinguish
  "revoked" from "no answer", and every caller then treats the second as the first.
- **Rate-limit read as revocation:** classifying `429` as INVALID, so a throttled
  probe triggers a login against the endpoint about to throttle harder.
- **Unrecognised-status fallthrough:** treating every unmatched status as INVALID, so
  a `404` from a wrong base URL or a `400` from a malformed request burns logins on a
  defect re-authentication cannot fix.
- **Status-only classification on envelope brokers:** Breeze returns
  `{"Success":…, "Status":…, "Error":…}` under HTTP 200, so a dead session reads as
  VALID and the bot trades on it.
- **Side-effect-heavy probing:** using order placement/modification calls for probing,
  or hitting a read path with a write method.
- **Aggressive outage re-logins:** treating single network timeouts as invalidations,
  triggering login rate limits during broker API outages.
- **Undecorrelated fleet retries:** bare exponential backoff means every bot retries
  at the same instants and arrives as one burst when the broker recovers.
- **Discarding a verified-pending token:** raising on an ambiguous post-auth probe
  without returning the token, so the next start re-authenticates unnecessarily.
- **Alert channel masking the verdict:** an `alert_fn` that raises replaces
  `AmbiguousProbeError` with its own exception, so the caller's "do not spend a login"
  branch never runs. `_alert()` contains the alert failure and logs it.
- **Raw HTTP call passed as `probe_fn`:** `requests.get` raises rather than returning
  a status, bypassing the retry loop entirely.
- **Concurrent token owners:** nothing here is synchronised. Two workers can both see
  INVALID and both log in, spending two logins and leaving one holding a superseded
  token. Elect one owner per broker app; a lock would not span processes anyway.

## Production Implementation Reference

- Reference code: `scripts/token_probe.py` — `LiveTokenProbeManager`, `ProbeOutcome`,
  `classify_probe_response`, `probe_with_backoff`, and the
  `TokenProbeError` / `AmbiguousProbeError` / `TokenVerificationError` hierarchy.
- Automated unit tests: `scripts/test_token_probe.py`.
- `sleep_fn` and `rng` are injectable throughout so retry paths are deterministic and
  cost no wall-clock time under test.
