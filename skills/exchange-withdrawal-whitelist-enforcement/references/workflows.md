# Workflows for Exchange Withdrawal Whitelist Enforcement

## 0. Enable the venue control first

This engine gates *your* code. It does not gate an attacker who holds your API key
and calls the venue directly. Before wiring it in:

1. Enable the venue's withdrawal whitelist (opt-in on Binance and OKX).
2. Enable the venue's new-address lock and record the duration you chose.
3. Restrict every withdrawal-capable API key by IP. Keys used for trading should
   carry no withdrawal scope at all.
4. On Kraken, decide whether the Global Settings Lock is on — while it is, an
   address cannot be added at all, and your provisioning runbook must account for
   the unlock delay.

## 1. Load per-network policy from the venue

Fetch `GET /sapi/v1/capital/config/getall` (or the venue equivalent) and register a
`NetworkWithdrawalPolicy` per `(asset, network)` from the venue's own
`addressRegex`, `memoRegex` and `withdrawTag`:

```python
engine.register_network_policy(NetworkWithdrawalPolicy(
    asset_symbol="USDT", network="ETH",
    address_regex=r"^(0x)[0-9a-fA-F]{40}$", requires_destination_tag=False))
```

Skipping this is allowed but not silent: every report then carries a
`No NetworkWithdrawalPolicy registered…` warning, so an operator reading the audit
trail can see that address-shape and mandatory-memo checks did not run.

## 2. Register allowlist entries from the venue's list, not a parallel one

```python
engine.register_whitelisted_address(
    WhitelistedAddressRecord(
        address_id="ADDR_COLD_01", asset_symbol="BTC", network="BTC",
        destination_address="bc1q…", label="Cold Storage Vault",
        added_timestamp_seconds=venue_added_at,
        cooloff_duration_seconds=COOLOFF_48H_SECONDS),
    observed_at_seconds=trusted_now)
```

- `network` is mandatory and part of the entry's identity.
- Set `destination_tag` wherever the chain uses memos. It is compared exactly.
- `cooloff_duration_seconds` should mirror what the venue is actually enforcing.
  The engine independently applies `minimum_cooloff_seconds` as a firm floor, so
  the effective lock is the longer of the two.
- Pass `observed_at_seconds` explicitly. It is the anchor used when an entry is
  re-added after revocation.

**Registration raises** (rather than storing a bad entry) on a blank address or
`address_id`, a non-finite or negative cool-off, a NaN timestamp, an address that
fails the registered `addressRegex`, a memo that fails `memoRegex`, or a missing
memo on a `withdrawTag` network.

## 3. Keep the unlock time monotonic

Re-registering an existing key takes `max` of the submitted anchor, the existing
anchor, and any recorded revocation time, and `max` of the cool-offs. Consequences
to design around:

- A refresh carrying an older `added_timestamp_seconds` cannot shorten a live lock.
  The suppression is logged at WARNING as `COOLOFF REGRESSION SUPPRESSED` — **alert
  on that log line**, because a legitimate sync should not produce it.
- An address that was revoked and re-added serves a full fresh cool-off, matching
  how venues treat a deleted-and-re-added entry.
- A legitimately later anchor (the venue really did re-add the address) does extend
  the lock. That is the intended direction.

## 4. Revoke rather than delete

```python
engine.revoke_whitelisted_address("BTC", "BTC", address, observed_at_seconds=now)
```

Returns `True` if an active entry was deactivated, `False` if there was nothing to
revoke. The entry is retained so a subsequent attempt returns
`ADDRESS_REVOKED_REJECTION` instead of the generic unknown-address status —
an attempt against a *recently revoked* address is a much more specific signal
than an attempt against an address that was never known.

## 5. Audit each request against a trusted clock

```python
report = engine.audit_withdrawal_request(req, evaluation_timestamp_seconds=trusted_now)
if not report.is_withdrawal_approved:
    raise WithdrawalBlocked(report.status, report.audit_notes)
```

Evaluation order, short-circuiting on the first failure:

| # | Check | Status on failure |
|---|---|---|
| 1 | API key withdrawal scope | `API_KEY_WITHDRAWAL_DISABLED` |
| 2 | API key IP restriction | `API_KEY_NOT_IP_RESTRICTED_REJECTION` |
| 3 | Allowlist membership on `(asset, network, address)` | `UNAUTHORIZED_ADDRESS_REJECTION` |
| 4 | Revocation | `ADDRESS_REVOKED_REJECTION` |
| 5 | Destination tag binding | `DESTINATION_TAG_MISMATCH_REJECTION` |
| 6 | Cool-off lock | `COOLOFF_PERIOD_ACTIVE_REJECTION` |
| — | all passed | `WITHDRAWAL_APPROVED` |

Because it short-circuits, the status names the *first* reason the request was
stopped, not a merged verdict. Fields for checks that never ran are `None`, and
`checks_evaluated` lists what actually executed — so a `None` in
`is_address_whitelisted` means "not evaluated", never "not whitelisted".

Structurally invalid requests **raise `WithdrawalWhitelistError`** rather than
returning a report. An exception cannot be mistaken for approval; a report can, if
the caller reads the wrong field.

## 6. Submit, and let the venue have the last word

On `WITHDRAWAL_APPROVED`, submit with the network **explicit**. `network` is
optional on Binance's withdraw endpoint and silently falls back to the coin's
default chain, which discards the very binding this gate just enforced. Then treat
the venue's own rejection as authoritative: if it says the address is not
whitelisted or is still in its hold, your local state is stale — re-sync the
allowlist rather than retrying.

## 7. Operational monitoring

Alert on, at minimum:

- `COOLOFF REGRESSION SUPPRESSED` — a back-dated re-registration attempt.
- `API_KEY_NOT_IP_RESTRICTED_REJECTION` — a withdrawal-capable key reachable from
  anywhere.
- `UNAUTHORIZED_ADDRESS_REJECTION` — the signature of a key-compromise attempt.
- `CLOCK SKEW` warnings — either a broken clock or a manipulated request.
- Any registration of a new allowlist entry, routed to a human, out of band from
  whatever system performed the registration.
