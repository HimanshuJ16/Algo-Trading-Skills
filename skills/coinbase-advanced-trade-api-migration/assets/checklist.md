# Pre-Flight Checklist

## Credentials
- [ ] Is the key a **CDP ECDSA** key, with a fresh **ES256 JWT minted per request** (not a
      reused token, and not legacy `CB-ACCESS-KEY`/`SIGN`/`PASSPHRASE` HMAC headers)?

## Semantics that change silently if mistranslated
- [ ] Does `stop_direction` come from the legacy `stop` field (`loss` → `STOP_DOWN`,
      `entry` → `STOP_UP`) rather than from `side`? Check a sell stop-**entry** and a buy
      stop-**loss** specifically — those are the cases a side-based rule inverts.
- [ ] Is `time_in_force` reflected in the configuration key (`limit_limit_gtc` /
      `_gtd` / `_fok`), so no IOC or FOK order becomes a resting GTC order?
- [ ] Do GTD configurations carry an RFC3339 `end_time`?
- [ ] For a market **buy**, does a legacy `funds` amount land in `quote_size` — never in
      `base_size`?
- [ ] Is `post_only` present only on `limit_limit_gtc` / `limit_limit_gtd`?

## Payload shape
- [ ] Are the legacy fields nested under `order_configuration.<key>` rather than sent flat?
- [ ] Is `side` uppercase (`BUY` / `SELL`)?
- [ ] Are `base_size`, `quote_size`, `limit_price` and `stop_price` decimal **strings**
      with no exponent (`0.00000001`, not `1e-08`)?
- [ ] Do those values already respect the product's `base_increment`, `quote_increment`
      and minimum size? The adapter does not round them.

## Idempotency and response handling
- [ ] Is `client_order_id` stable and caller-supplied, so a retry is the *same* order?
- [ ] Does the client check `success` in the response **body**, not just the HTTP status?
- [ ] Are `new_order_failure_reason` and `error_details` retained so a rejection can be
      classified as terminal or retryable?
- [ ] Is `status="ACCEPTED"` treated as acceptance only, with live state read from
      `GET /api/v3/brokerage/orders/historical/{order_id}`?
- [ ] After a timeout or ambiguous response, does the client reconcile by
      `client_order_id` before re-submitting?
