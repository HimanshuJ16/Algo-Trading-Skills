# Pre-Flight / Sign-off Checklist — token-lifecycle-live-probing

Use this before considering the skill's implementation complete.

## Probe design

- [ ] **Read-Only Probe Endpoint:** Confirm the probe uses a side-effect-free call
      (Kite `/user/profile` or `/user/margins`, Fyers `/profile`, Breeze
      `/customerdetails`).
- [ ] **Read-Only Probe *Method*:** Confirm the HTTP method is a read. Breeze's
      `funds` path reads on `GET` and sets a funds allocation on `POST`.
- [ ] **No Timestamp Gate:** Confirm the probe is not skipped when a cached expiry
      timestamp still looks fresh. For Fyers there is no published expiry instant to
      compare against at all.

## Classification

- [ ] **3-Outcome Classification:** Confirm responses are classified into `VALID`,
      `INVALID`, and `AMBIGUOUS` — and that the probe's return type can express all
      three (a `bool` cannot).
- [ ] **Broker-Specific Code Table:** Confirm `invalid_codes` / `retryable_codes` were
      set from the broker's own documentation, not left at the defaults by accident.
- [ ] **Rate Limits Are Ambiguous:** Confirm `429` (and `408`) do **not** trigger
      re-authentication.
- [ ] **Unrecognised Statuses Are Ambiguous:** Confirm `400`/`404`/`405`/`410`/`3xx`
      do not trigger re-authentication — re-auth cannot fix any of them.
- [ ] **Envelope Errors Caught:** For Breeze or Fyers, confirm an error reported
      inside an HTTP 200 body is classified `INVALID`, not `VALID`.

## Behaviour under failure

- [ ] **Ambiguous Never Re-Authenticates:** Force a timeout, a `503` and a `429`, and
      confirm the login flow is **not** invoked, an alert fires, and the cached token
      is retained. This is the single most important line on this checklist.
- [ ] **Jittered, Capped Backoff:** Confirm retries use jitter (not bare exponential)
      and a delay cap, so a fleet does not retry in lockstep after an outage.
- [ ] **Re-Authentication Flow:** Confirm a documented auth failure triggers
      `reauth_fn()` and re-verifies the new token before trading is enabled.
- [ ] **New Token Preserved On Verification Failure:** Confirm a freshly issued token
      is persisted (from `TokenVerificationError.token`) rather than discarded when its
      post-auth probe is ambiguous.

## Observability & hygiene

- [ ] **No Token Material In Logs:** Confirm no log line, alert, or exception message
      emitted by the probe path contains a token value.
- [ ] **Alert Failure Is Contained:** Confirm a raising `alert_fn` does not replace
      `AmbiguousProbeError` with its own exception.
- [ ] **`probe_fn` Owns Transport Errors:** Confirm it returns `(None, True)` rather
      than letting the HTTP client raise through the retry loop.
- [ ] **Single Token Owner:** Confirm exactly one process re-authenticates per broker
      app; others read the token it publishes.
- [ ] **Lifespan Recording:** Confirm observed lifespans are persisted, and that a
      negative lifespan is rejected rather than clamped to zero.
- [ ] **Proactive Refresh Gated:** If `should_proactively_refresh` is used, confirm it
      is driven by the minimum observed lifespan and requires a sample floor.
- [ ] **Automated Testing:** Run
      `python -m unittest discover -s skills/token-lifecycle-live-probing/scripts`
      and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Broker(s) covered by this sign-off: ___________________________
