# Pre-Flight / Sign-off Checklist — alpaca-paper-live-key-separation

Use this before considering the skill's implementation complete.

- [ ] **Credential Validation:** Confirm `key_id` and `secret_key` are non-empty and non-whitespace.
- [ ] **Environment Normalisation:** Confirm an unrecognised environment value raises instead of validating. A mode matching neither `PAPER` nor `LIVE` must never reach a success return.
- [ ] **Credential Prefix Verification:** Confirm a key bearing the *opposite* environment's prefix is rejected (`AK...` in paper mode, `PK...` in live mode), and that an unrecognised prefix is **not** rejected — the `PK`/`AK` convention is unofficial.
- [ ] **Base URL Matching:** Confirm paper configuration uses `https://paper-api.alpaca.markets` and live uses `https://api.alpaca.markets`, matched exactly against an allow-list. Confirm a look-alike host (`https://api.alpaca.markets.attacker.example`) is rejected.
- [ ] **Live Execution Gate:** Confirm live trading is blocked unless `ALLOW_LIVE_TRADING=true` is set, that surrounding whitespace is tolerated, and that authorisation emits a WARNING.
- [ ] **Account Probe — Tradability:** Confirm `probe_account()` requires `status` to be `ACTIVE` (or `PAPER_ONLY` in paper mode only).
- [ ] **Account Probe — Missing Status:** Confirm a response with no `status` field is vetoed, **not** defaulted to `ACTIVE`.
- [ ] **Account Probe — Blocked Flags:** Confirm `trading_blocked`, `account_blocked`, and `trade_suspended_by_user` each veto the order.
- [ ] **Account Probe — String Booleans:** Confirm `is_paper: "false"` and `trading_blocked: "true"` (string-typed) are honoured rather than discarded, and that a present-but-uninterpretable `is_paper` vetoes.
- [ ] **Account Probe — Malformed Response:** Confirm a non-mapping response raises `EnvironmentMismatchError`, not `AttributeError`.
- [ ] **Environment Resolution:** Confirm the probe resolves the environment only from `is_paper` (if an adapter injects it), `status == PAPER_ONLY`, or a `PA`-prefixed `account_number` — and that a realistic paper payload carrying **none** of these is accepted rather than vetoed. Alpaca's `/v2/account` returns no `is_paper` field.
- [ ] **Order Parameter Validation:** Confirm `guard_order()` rejects `qty <= 0`, `NaN`, `inf`, non-numeric `qty`, an empty `symbol`, and a `side` outside `buy`/`sell`.
- [ ] **Config Immutability:** Confirm `AlpacaConfig` is frozen and cannot be mutated after validation.
- [ ] **Credential Hygiene:** Confirm no log line or exception message contains the `key_id` or `secret_key` value.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/alpaca-paper-live-key-separation/scripts` and confirm a 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
