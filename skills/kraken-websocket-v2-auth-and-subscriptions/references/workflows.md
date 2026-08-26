# Workflows for Kraken WS v2 Integration

## 1. Mint a WebSocket token

```python
gen = KrakenNonceGenerator()            # one per API key, shared by all callers
engine = KrakenWsV2ManagerEngine(api_key=KEY, api_secret_b64=SECRET)

nonce = str(gen.next_nonce())
body = f"nonce={nonce}"                 # sign this exact string
sign = engine.generate_kraken_rest_hmac_signature(
    "/0/private/GetWebSocketsToken", nonce, body
)
# POST body to https://api.kraken.com/0/private/GetWebSocketsToken
# headers: {"API-Key": KEY, "API-Sign": sign,
#           "Content-Type": "application/x-www-form-urlencoded"}
```

The transport must send `body` unchanged. Re-encoding it — reordering keys,
reformatting a float, letting a library re-serialise the dict — changes the
bytes the signature covers and produces `EAPI:Invalid signature`.

Response: `{"error": [], "result": {"token": "...", "expires": 900}}`.
Record the wall-clock instant of the response as `created_timestamp_epoch`.

### Classifying the REST failure

| Error | Meaning | Action |
|---|---|---|
| `EAPI:Invalid key` | Wrong or revoked public key | Do not retry; check config |
| `EAPI:Invalid signature` | Wrong secret, wrong path, or body ≠ signed bytes | Do not retry; the signature is deterministic and will fail identically |
| `EAPI:Invalid nonce` | Nonce not strictly increasing for this key | Do not spin — repeated failures earn a temporary ban. Fix nonce ordering or widen the account's nonce window |
| `EGeneral:Permission denied` | Key lacks *Access WebSockets API* | Do not retry; fix the key's permissions |
| `EAPI:Rate limit exceeded` | Private REST counter exhausted | Back off; the counter decays at 0.33–1/sec by tier |
| `EService:Unavailable` / `EService:Busy` | Venue-side | Retry with backoff |

Only the last two are retryable. Retrying the others burns rate-limit budget and,
for nonce errors, makes the situation worse.

## 2. Build and route the subscribe frame

```python
token = KrakenWsTokenState(token=result["token"],
                           created_timestamp_epoch=response_time,
                           expires_in_seconds=result["expires"])

report = engine.build_v2_subscription_frame(
    KrakenWsV2SubscriptionSpec(channel="executions", snap_trades=True, req_id=1),
    token_state=token,
    current_time_epoch=time.time(),
)
if report.status == STATUS_FRAME_CREATED:
    ws.send(json.dumps(report.subscription_json_frame))   # send this
logger.info(report.audit_notes)                           # log this
```

`report.ws_url` names the endpoint the frame belongs on. Do not assume one
socket serves everything: public channels, `executions`/`balances`, and `level3`
are three different hosts, so a bot using all three holds three connections.

### Rejection statuses and what each means

| Status | Cause | Action |
|---|---|---|
| `INVALID_CHANNEL` | Not a v2 channel, or a request method (`add_order`) used as one | Fix the caller; send order entry as `{"method": ...}` |
| `MISSING_SYMBOL` | `book`/`ticker`/`trade`/`ohlc`/`level3` with no symbols | Supply `BASE/QUOTE` symbols |
| `INVALID_DEPTH` | Depth outside the channel's set | Use 10/25/100/500/1000 for `book`, 10/100/1000 for `level3` |
| `MISSING_WS_TOKEN` | Private channel with no token | Mint one; note `level3` counts as private |
| `TOKEN_INACTIVE` | `is_active=False` on the token state | Mint a replacement |
| `TOKEN_REFRESH_REQUIRED` | Age past the 720s margin, inside 900s | Routine: mint a fresh token and rebuild |
| `TOKEN_EXPIRED` | Age past the 900s use-by window | Mint a fresh token; investigate why the refresh path lagged |
| `TOKEN_CLOCK_SKEW` | Token dated in the future | Do not proceed — the age check is meaningless until the clock is fixed |

Warnings (`report.warnings`) never block a frame. They flag a REST altname used
as a v2 symbol, a depth on a channel that ignores it, and an unrecognised `ohlc`
interval.

## 3. Token lifecycle over a live session

The refresh rule follows the venue's actual semantics, not a timer:

- **Before every use of a token** — building a subscribe frame, or reconnecting
  — check its age. That is where `build_v2_subscription_frame` gates.
- **While a connection with a live private subscription is maintained** — do
  nothing. The token does not expire under it. There is no correct reason to
  resubscribe on a 15-minute cadence, and doing so drops the feed and consumes
  connection rate-limit budget.
- **On reconnect** — always mint a fresh token before resubscribing. The old
  one is almost certainly outside its use-by window, and even if not, a fresh
  one costs one REST call.

Keep the connection alive with `ping`; Kraken closes a socket after roughly a
minute of inactivity, and an authenticated socket needs at least one private
subscription to stay open.

## 4. Audit and credential hygiene

`report.subscription_json_frame` contains a live bearer token for private
channels. It is for the socket only. Anything that persists — log lines, audit
stores, tickets, screenshots — takes `report.audit_notes`, in which the token is
replaced by a stable `<ws_token:xxxxxxxx>` fingerprint. The fingerprint is
consistent for a given token, so it can still be used to correlate events across
a session without disclosing the credential.
