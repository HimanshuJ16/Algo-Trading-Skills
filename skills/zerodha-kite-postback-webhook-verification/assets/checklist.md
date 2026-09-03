# Pre-Flight / Sign-off Checklist — zerodha-kite-postback-webhook-verification

Use this before considering the skill's implementation complete.

## Signature

- [ ] **Formula matches the broker.** Checksum is `SHA-256(order_id + order_timestamp +
      api_secret)` over the **raw** `order_timestamp` string, never a reparsed or
      reformatted one.
- [ ] **Fails closed.** A postback with `checksum` absent, empty, or null is *rejected*.
      Confirm no `if received_checksum:` guard wraps the comparison.
- [ ] **Shape-checked before compare.** Received checksum must be 64 hex characters;
      anything else is rejected without reaching `hmac.compare_digest`.
- [ ] **Constant-time on bytes.** Both sides go through `bytes.fromhex()`. A non-ASCII
      checksum returns a rejection, not a `TypeError`.
- [ ] **Empty `api_secret` raises.** The handler refuses to start or verify rather than
      accepting a publicly derivable digest.
- [ ] **Verified before the freshness check**, so "forged" and "stale" stay separable.

## Trust boundary

- [ ] **Body fields treated as unauthenticated.** No position or ledger mutation reads
      `status`, `filled_quantity` or `average_price` straight from the postback.
- [ ] **Reconciliation wired.** An accepted postback triggers `GET /orders/:order_id`
      and *that* response is what updates state.
- [ ] **Periodic sweep exists.** A scheduled `GET /orders` reconciliation runs
      independently, so a lost postback surfaces as a difference rather than silence.

## Timestamp

- [ ] **Timezone explicit.** `order_timestamp` is parsed as IST (UTC+05:30), not as
      server-local time. Verified by running the handler on a UTC host.
- [ ] **Documented format parses.** `"2022-03-03 09:24:25"` is accepted; a genuine
      captured postback verifies end to end.
- [ ] **Past and future bounded separately.** Stale and future-dated produce distinct
      outcomes, not one `abs(drift)` bucket.
- [ ] **Window limit understood.** Documented that a replay *inside* the window still
      passes the signature, and that dedup — not freshness — is the replay defense.

## Idempotency

- [ ] **Fingerprint is the full state change** (`order_id`, `order_timestamp`, `status`,
      `filled_quantity`, `average_price`) — not `order_id` + `status`.
- [ ] **Partial fills survive.** Three successive `OPEN` updates with rising
      `filled_quantity` are all applied.
- [ ] **Claim is atomic.** Test-and-insert under one lock or a DB unique constraint;
      concurrent redeliveries yield exactly one application.
- [ ] **A duplicate does not report "safe to apply."** `if result.valid: apply()` cannot
      double-apply a redelivery.
- [ ] **Store is bounded**, and durable if idempotency must survive a restart/deploy.

## Operations

- [ ] **Endpoint is HTTPS on port 443.** Zerodha reaches only ports 80/443; any other
      port fails silently.
- [ ] **Handler never raises.** Malformed bodies, bad JSON, non-UTF-8 bytes and bad
      quantities all return a classified rejection and a 2xx response.
- [ ] **Secrets never logged.** No `api_secret` and no full received checksum in logs;
      truncated fragments only.
- [ ] **Alerting split by outcome class.** Checksum mismatch and missing checksum page
      as security events; staleness and duplicates do not.

## Testing

- [ ] **Test vector is externally derived.** The expected digest in the suite is not
      produced by re-running the implementation's own formula.
- [ ] **Real payload exercised.** The documented Kite example payload is verified as a
      raw JSON body, not a hand-simplified dict with an epoch timestamp.
- [ ] Run `python -m unittest discover -s skills/zerodha-kite-postback-webhook-verification/scripts` and confirm a
      100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
