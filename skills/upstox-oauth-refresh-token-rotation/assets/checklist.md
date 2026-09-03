# Pre-Flight / Sign-off Checklist — upstox-oauth-refresh-token-rotation

Use this before considering the skill's implementation complete.

## Premise

- [ ] **No refresh grant anywhere in the codebase:** Confirm nothing calls
      `grant_type=refresh_token` or `https://api.upstox.com/v2/login/auth/token` against
      Upstox. Neither exists. (`grep -r "refresh_token"` over the integration should
      return nothing but comments explaining its absence.)
- [ ] **Acquisition path chosen deliberately:** authorization-code OAuth,
      v3 Access Token Request approval flow, or read-only Analytics Token — and the
      choice is compatible with whether the bot places orders.

## Expiry

- [ ] **03:30 IST boundary, not a duration:** Confirm expiry is derived as the next
      03:30 IST instant, and that a 02:30 IST issuance expires the **same** morning.
- [ ] **Timezone conversion:** Confirm a UTC-expressed instant is converted to IST before
      the boundary is applied, not read from raw wall-clock fields.
- [ ] **No `expires_in` assumption:** Confirm no code path defaults to 86400 seconds, and
      that a persisted record lacking `expires_at` is rejected rather than defaulted.
- [ ] **Millisecond parsing:** Confirm the notifier webhook's string-typed
      `expires_at` is parsed as epoch milliseconds, and a seconds-valued timestamp is
      rejected rather than silently divided.
- [ ] **Pre-flight buffer:** Confirm a token dying within ~900s is treated as expiring, so
      no strategy cycle starts on a token that will die mid-flight.

## Concurrency & persistence

- [ ] **Single-flight re-auth:** Confirm concurrent workers produce exactly **one**
      re-authentication call — including the re-check *inside* the lock, not only the
      fast-path check outside it.
- [ ] **Atomic persistence:** Confirm tokens are written via temp file + `os.replace`,
      before in-memory state is published and before the lock is released.
- [ ] **File permissions:** Confirm the token file is opened at `0600` from the start
      (not `chmod`-ed after), and no `.tmp` file is left behind.
- [ ] **Persistence failures propagate:** Confirm a write failure raises rather than being
      logged and swallowed, and that in-memory state is not updated on failure.
- [ ] **Restart survival:** Confirm a fresh process reloads the persisted token and
      considers it valid without re-authenticating.

## Capability & error handling

- [ ] **Write-capability gate:** Confirm order-placement call sites require a
      non-read-only token and fail locally rather than at 403 `UDAPI100067`.
- [ ] **`extended_token` not bound as the trading credential:** Confirm the Get Token
      response's `access_token`, not its `extended_token`, is what the bot carries.
- [ ] **Error codes, not messages:** Confirm `UDAPI100050` triggers re-auth while
      `UDAPI100016` / `UDAPI100073` are treated as configuration faults and not retried.
- [ ] **HTTP 200 is not success:** Confirm the response body's error envelope is inspected
      on every token call.

## Environment

- [ ] **Static IP registered:** Confirm the deployment's egress IP matches the address
      whitelisted with the broker and is stable across restarts (NSE/INVG/67858 A.1, I.e;
      changeable at most once a calendar week per A.6).
- [ ] **Daily logout is the design:** Confirm nothing in the system assumes a session
      persists across trading days (NSE/INVG/67858 A.8).

## Testing

- [ ] **Automated testing:** Run
      `python -m unittest discover -s skills/upstox-oauth-refresh-token-rotation/scripts`
      and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (sandbox/live): ___________________________
