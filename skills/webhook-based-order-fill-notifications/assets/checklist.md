# Pre-Flight / Sign-off Checklist — webhook-based-order-fill-notifications

Use this before considering the skill's implementation complete. Each item maps
to a check in `SKILL.md` § Verification.

## Does this integration need a webhook at all?

- [ ] **Publisher confirmed:** the broker's own documentation shows it POSTs
      fills to a URL you host. (IBKR, Alpaca, TradeStation and Coinbase Advanced
      Trade do not — they stream. Building a receiver for those is the wrong
      design.)
- [ ] **Signature scope recorded:** you have written down *which fields* the
      publisher's signature actually covers. Kite's checksum covers
      `order_id + order_timestamp` only; DhanHQ documents no signature.
- [ ] **Fields outside the digest are treated as untrusted** for the rest of the
      flow.

## Signature verification

- [ ] **Raw body:** the digest is computed over the bytes received, captured
      before the framework's JSON parser runs — never over a re-serialised dict.
- [ ] **Constant time:** comparison uses `hmac.compare_digest()`, never `==`.
- [ ] **Prefix handling:** the algorithm label (`sha256=`, `v1,`) is stripped
      from the front of the token only, not with a bare `replace()`.
- [ ] **Encoding:** both hex and base64 digests verify (Standard Webhooks emits
      base64).
- [ ] **Rotation:** a list of secrets and a space-delimited list of signature
      tokens both verify, so a dual-secret rotation window drops no deliveries.
- [ ] **Failure path:** an invalid signature returns 401 and the body is never
      parsed.
- [ ] **CSRF exemption** is scoped to the webhook route only, and HMAC
      verification is confirmed in place before the exemption was added.

## Replay defence

- [ ] **Window:** events outside ±300 s of server time are rejected.
- [ ] **No default:** a payload with a missing or null timestamp is **rejected**,
      not treated as fresh. Confirm by posting a payload with no `timestamp`.
- [ ] **Boundary:** the inclusive/exclusive behaviour at exactly 300 s is pinned
      by a test with a frozen clock.
- [ ] **Ordering:** the freshness check runs *before* the idempotency claim, so a
      stale replay cannot consume the key its legitimate twin needs.
- [ ] **Clock discipline:** the host's clock drift is monitored and is well
      inside the replay window.

## Payload handling

- [ ] **No 500s:** malformed quantity (`"abc"`, `null`, negative), malformed
      sequence, non-object payload and invalid UTF-8 each return a 400-class
      rejection with no exception escaping the handler.
- [ ] **Null identifiers:** `{"order_id": null}` is rejected, not accepted as the
      string `"None"`.
- [ ] **JSON constants:** the raw literals `NaN`, `Infinity` and `-Infinity` are
      rejected before any quantity reaches the ledger.

## Deduplication

- [ ] **Composite key:** `order_id:exec_id`, both halves required.
- [ ] **Atomic claim:** check-and-set is one operation, not `if not seen: mark()`.
- [ ] **Multi-process safe:** if more than one worker or host serves the
      endpoint, the claim is backed by a `UNIQUE (order_id, exec_id)` constraint
      or Redis `SET NX` — not an in-process set.
- [ ] **Unmistakable result:** a duplicate returns a distinct status *and* a
      zeroed quantity, so a caller that branches on status alone still cannot
      double-count. Confirm by delivering the same event five times and summing
      the quantity over results whose status reads as success.
- [ ] **Acknowledgement:** a duplicate returns HTTP 200, so the publisher stops
      retrying.
- [ ] **Retention:** the remembered-key window comfortably exceeds the
      publisher's retry horizon.
- [ ] **Bounded:** the store has a hard ceiling and logs loudly on eviction.
- [ ] **Concurrency:** 16 concurrent deliveries of one event yield exactly one
      applicable result.

## Corrections and sequencing

- [ ] **Content mismatch:** a redelivery of the same key with a different body is
      flagged for reconciliation, not silently dropped.
- [ ] **Corrections understood:** the team knows an IBKR correction reuses the
      `execId` except for the digits after the final period, so it does not
      present as a duplicate.
- [ ] **Out-of-order surfaced:** a sequence below the high-water mark is reported
      to the caller, not only logged.
- [ ] **Zero is a sequence:** the tracker does not skip `sequence_num == 0`.
- [ ] **Gaps detected:** missing sequences below the high-water mark are
      reported, and the team treats a gap as "reconcile", not "complete".
- [ ] **Documentation honest:** nothing claims events are queued or reordered
      unless a durable queue actually exists.

## Ledger integration

- [ ] **One boolean:** the ledger writer branches on `apply_to_ledger` and
      nothing else.
- [ ] **Reconcile before booking:** the position is written from the broker's
      authenticated order/trades record, with the webhook as trigger only.
- [ ] **Off the request path:** acknowledgement is returned once the event is
      durably recorded; reconciliation and booking run from a queue.
- [ ] **Periodic sweep:** an authenticated reconciliation runs on a timer, so a
      lost webhook is eventually caught. Absence of a postback is not treated as
      evidence that no fill occurred.
- [ ] **Audit trail:** the raw signed body and the verification outcome are
      retained, not just the parsed result. No secret appears in any log line.

## Automated testing

- [ ] **Run** `python -m unittest discover -s scripts` from the skill directory
      and confirm every test passes.
- [ ] **Regression coverage:** the suite contains a test that fails against the
      pre-fix behaviour for each defect fixed, not only a test that passes now.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Publisher and signature scope confirmed against vendor docs on: ____________
