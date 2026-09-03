# Deep Workflow Reference — upstox-oauth-refresh-token-rotation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

**Before anything else:** Upstox issues no refresh token and supports no refresh grant.
If you arrived here looking for `grant_type=refresh_token`, stop — see
`references/standards.md` for what exists instead. The procedure below manages a token
that must be **re-acquired daily**, not refreshed.

## Full Procedure

### 1. Load persisted state

Read `access_token`, `expires_at` (epoch seconds), `issued_at`, `source`, and
`read_only`. Validate on load:

- A record with no `access_token`, or no `expires_at`, is invalid — reject it. Do **not**
  substitute a default expiry; that is precisely how a fixed-duration assumption
  re-enters the system.
- An unreadable or corrupt file means *no token*, which triggers re-authentication. It
  must never be mistaken for a usable token.

### 2. Derive expiry from the 03:30 IST boundary

The access token expires at 03:30 IST on the day following issuance — or on the *same*
day, if issued between midnight and 03:30. The token response carries no `expires_in`,
so this must be computed:

```
boundary = now_in_IST.replace(hour=3, minute=30, second=0, microsecond=0)
if boundary <= now_in_IST:
    boundary += 1 day
```

Convert to IST before applying the rule — reading a UTC datetime's raw hour field rolls
the boundary at the wrong instant. Use a fixed UTC+05:30 offset rather than
`ZoneInfo("Asia/Kolkata")`: India has observed no DST since 1945, so the offset is exact,
and `tzdata` is absent by default on Windows hosts.

Where the broker supplies an authoritative `expires_at` — the notifier webhook does —
prefer it, parsing it as **epoch milliseconds delivered as a string**.

### 3. Expiry check with a buffer

Treat the token as expiring if `now >= expires_at - buffer` (≈900s). This is not
"refresh early" — nothing can be refreshed. It prevents a strategy from *beginning* a
cycle on a token that will die mid-flight, between an order submission and its status
poll.

### 4. Single-flight re-authentication

```
if token is usable:            # fast path, no lock
    return it
with lock:
    if token is usable:        # MANDATORY re-check inside the lock
        return it
    state = reauth_fn()
    validate(state)
    persist(state)             # before publishing, before releasing
    self.state = state
    return state.access_token
```

The inner re-check is the whole point. Without it the lock merely *serialises* the
re-auth attempts rather than collapsing them, and ten workers waking at 03:30 IST still
generate ten approval prompts on the user's phone.

### 5. Choose an acquisition path

**Authorization code** (`POST https://api.upstox.com/v2/login/authorization/token`):

- `Accept: application/json`, `Content-Type: application/x-www-form-urlencoded`.
- Fields: `code`, `client_id`, `client_secret`, `redirect_uri`, `grant_type=authorization_code`.
- The body is **form-encoded, not JSON**. The v3 Access Token Request API is the
  opposite — it takes JSON. Do not generalise one to the other.
- `code` is single-use and short-lived. A failed exchange needs a *new* code from a new
  dialog round-trip; replaying the old one fails.
- The response also carries an `extended_token`. It is read-only. Binding it as the
  trading credential produces 403 `UDAPI100067` at the first order.

**Access Token Request** (`POST https://api.upstox.com/v3/login/auth/token/request/{client_id}`):

- `Content-Type: application/json`, body `{"client_secret": "..."}`.
- Returns `{"status": "success", "data": {"authorization_expiry": ..., "notifier_url": ...}}`.
  It does **not** return the token.
- On user approval (in-app + WhatsApp prompt), Upstox POSTs the token to the app's
  registered `notifier_url`. Individual accounts only; the app must be approved and the
  notifier URL configured at app-setup time.
- Treat the webhook as untrusted input: verify the delivery is genuinely from Upstox at
  the transport layer, and check `client_id`/`user_id` match the account you expect,
  before binding the token.

**Analytics Token**: generated from the Developer Apps console, 1-year validity, one
active per account (regenerating revokes the previous one). GET requests only — it
cannot place, modify, or cancel orders. Account-scoped APIs additionally require Static
IP to be enabled.

### 6. Validate the acquired token

- Reject an empty `access_token`.
- Reject a token whose `expires_at` is already in the past. This is nearly always a
  seconds/milliseconds mix-up, and accepting it yields a hot re-auth loop that spams the
  user with approval prompts.
- Reject a timestamp outside the plausible epoch-millisecond range rather than dividing
  by 1000 and hoping.

### 7. Persist atomically, at 0600

Open the temp file with `os.open(tmp, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` and `os.replace`
it into position. Opening at `0600` from the start matters: `chmod`-after leaves a window
in which a plaintext bearer token is world-readable. Remove the temp file on any failure.

**Let persistence failures propagate.** A token held only in RAM works until the process
restarts, at which point an unattended bot cannot start and the approval flow wakes the
user — a failure that surfaces hours later, typically at a 03:30-adjacent restart with
nobody watching.

### 8. Gate writes on token capability

At order-placement call sites, require a non-read-only token and fail locally rather than
learning from a rejected order that the bound credential was an Analytics or extended
token.

### 9. Classify failures by error code

| Code | Action |
|---|---|
| `UDAPI100050` | Token invalid/expired — re-authenticate |
| `UDAPI100016` | Bad credentials — configuration fault, do not retry |
| `UDAPI100067` | Read-only token used for a write — swap credential, retrying cannot help |
| `UDAPI100073` | `client_id` inactive — configuration fault, do not retry |

HTTP 200 is not proof of success: errors arrive in the body as
`{"status": "error", "errors": [{"error_code": ..., "message": ...}]}`. Branch on the
code, never on message substrings.

## Known Failure Modes

- **Implementing a refresh exchange that does not exist.** Code and generated snippets
  offering Upstox "refresh token rotation" target `grant_type=refresh_token` and a
  `/v2/login/auth/token` endpoint, neither of which Upstox publishes. The similarly
  spelled real endpoint, `/v3/login/auth/token/request/{client_id}`, is the user-approval
  flow — a different mechanism.
- **Fixed-duration expiry.** A token minted at 20:00 IST and dated `now + 86400` is
  believed valid until 20:00 the next day, ~16.5h past its real 03:30 IST death. The bot
  reports itself authenticated across the entire next trading session while every call
  returns 401 `UDAPI100050` from market open onward.
- **Millisecond timestamps read as seconds.** `"1731448800000"` treated as seconds dates
  the token to the year 56000 and disables every expiry check. It is also a string, so a
  naive comparison against `time.time()` raises rather than compares.
- **Lock without an inner re-check.** Every queued thread re-authenticates in turn; the
  user receives one approval prompt per worker.
- **Swallowed persistence errors.** The process keeps working; the next restart is
  unauthenticated.
- **Local-calendar cache keys.** The session boundary is 03:30 IST, so a UTC-hosted bot
  rolls its key at the wrong instant in both directions — reusing a flushed token, or
  forcing a re-auth that needlessly wakes a human.
- **Read-only credential bound for order flow.** The `extended_token` arrives in the same
  response as the real access token, which makes it easy to bind the wrong one.

## Production Implementation Reference

- Reference code: `scripts/upstox_auth.py` — `next_session_expiry`,
  `parse_upstox_epoch_millis`, `raise_for_upstox_error`, `build_authorization_code_form`,
  `state_from_token_response`, `state_from_notifier_payload`,
  `state_for_read_only_token`, `UpstoxTokenState`, `UpstoxTokenManager`.
- Automated unit tests: `scripts/test_upstox_auth.py`.
- `UpstoxTokenManager.rotate_refresh_token` is retained only to raise an explanatory
  error; it is not a working code path and never was.
