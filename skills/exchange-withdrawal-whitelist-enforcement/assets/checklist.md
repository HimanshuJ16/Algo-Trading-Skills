# Pre-Flight Checklist — Exchange Withdrawal Whitelist Enforcement

## Venue-side controls (do these first; the local gate is the second layer)

- [ ] Withdrawal address whitelist is **enabled** on the account (opt-in on Binance and OKX).
- [ ] The venue's new-address lock is enabled, and the chosen duration is recorded
      (Binance 24/48/72h · Coinbase Exchange 48h · OKX 24h · Kraken 12h/24h, triggered by password change).
- [ ] Every withdrawal-capable API key carries an IP access restriction.
- [ ] Keys used for trading carry **no** withdrawal scope.
- [ ] Kraken only: Global Settings Lock status is known, and the runbook accounts for its unlock delay.

## Allowlist data

- [ ] Entries are synced from the venue, not maintained as a parallel list.
- [ ] Every entry names an explicit `network` — no "universal"/any-chain entries.
- [ ] Every memo-based chain entry (XRP, XLM, EOS, ATOM) has its `destination_tag` bound.
- [ ] A `NetworkWithdrawalPolicy` is registered per `(asset, network)` from the venue's
      `addressRegex`, `memoRegex` and `withdrawTag` — or reports are checked for the
      "No NetworkWithdrawalPolicy registered" warning.
- [ ] `cooloff_duration_seconds` mirrors what the venue is actually enforcing.
- [ ] `minimum_cooloff_seconds` is set from firm policy, not left at the default by accident.

## Request path

- [ ] `evaluation_timestamp_seconds` comes from a trusted server clock, not from the request.
- [ ] `is_withdrawal_enabled_on_key` and `is_key_ip_restricted` are asserted from real key
      metadata, never hard-coded to `True`.
- [ ] The submitted withdrawal call passes `network` **explicitly** — it is optional on
      Binance and falls back to the coin's default chain.
- [ ] A non-approved report blocks submission; the caller checks `is_withdrawal_approved`,
      not merely the absence of an exception.
- [ ] `WithdrawalWhitelistError` is allowed to propagate, not swallowed into a retry.

## Verification before go-live

- [ ] A future `request_timestamp_seconds` does **not** unlock an in-cool-off address.
- [ ] Re-registering an in-cool-off address with an older timestamp or a zero cool-off
      leaves the lock unchanged.
- [ ] A revoked-then-re-added address serves a full fresh cool-off.
- [ ] The same address is rejected under a different `network` and a different `asset`.
- [ ] A swapped or dropped destination tag is rejected.
- [ ] `amount=NaN`, `amount=0`, and a blank address each raise rather than produce a decision.
- [ ] A small test transaction has been sent and confirmed on-chain before the first large
      transfer to any new address — see `test-transaction-verification-before-large-transfers`.

## Monitoring

- [ ] Alerts fire on `COOLOFF REGRESSION SUPPRESSED`, `API_KEY_NOT_IP_RESTRICTED_REJECTION`,
      `UNAUTHORIZED_ADDRESS_REJECTION`, and clock-skew warnings.
- [ ] Every new allowlist registration notifies a human out of band from the system that
      performed it.
