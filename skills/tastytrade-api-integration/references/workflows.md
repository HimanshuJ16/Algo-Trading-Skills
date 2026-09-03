# Deep Workflow Reference — tastytrade-api-integration

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Migration check (do this first)

The `POST /sessions` email-and-password flow that returned a `session-token` was
discontinued on 2025-12-01. Before extending any existing Tastytrade code, grep
it for `/sessions`, `session-token`, `remember-token`, and a bare
`Authorization:` header without the `Bearer` scheme. Any hit means the code
targets the retired flow and must be migrated rather than patched.

`TastytradeClient.login()` raises `TastytradeAuthDiscontinuedError` so that a
caller — human or agent — working from stale examples fails immediately and
legibly instead of debugging a 401.

## 1. OAuth2 session acquisition

```python
client = TastytradeClient(
    is_production=False,                      # certification by default
    http_fn=my_transport,                     # must raise on transport failure
    user_agent="my-strategy/1.4.0",           # <product>/<version> or 401
)
client.authenticate(TastytradeCredentials(client_secret, refresh_token))
```

- `POST /oauth/token` with `grant_type=refresh_token`, `client_secret` and
  `refresh_token`. No `Authorization` header on this request.
- The response carries `access_token` and `expires_in` (900 seconds).
- `ensure_access_token()` refreshes 60 seconds before expiry. Call it — or
  `auth_headers()`, which calls it — before every request, so that a token
  round trip never lands inside an order submission.
- An `expires_in` that is absent falls back to the documented 900s (a shorter
  assumption than reality is always safe). An `expires_in` that is present but
  zero, negative, non-numeric or larger than 86400 is fatal: acting on an
  inflated lifetime means using a dead token and 401-ing at an arbitrary moment.
- A 4xx on the token request means the grant was revoked or the secret is wrong.
  That needs a human re-grant, not a retry loop — and repeated failed attempts
  risk an 8-hour IP block.

## 2. OCC option symbol construction

```python
symbol = format_occ_symbol("AAPL", "240816", "C", "200")   # AAPL  240816C00200000
parsed = parse_occ_symbol(symbol)                          # verify what you got
```

Layout: 6-char space-padded root, 6-char `YYMMDD`, `C`/`P`, strike × 1000
zero-padded to 8 digits — exactly 21 characters.

Rejected, deliberately, rather than coerced:

| Input | Why rejecting matters |
|---|---|
| Root over 6 chars | `ljust(6)` does not truncate; the symbol silently becomes 22 characters. |
| `"CALL"` / `"PUT"` | Pasted straight in, the symbol becomes 24 characters. |
| `"2024-08-16"` | Not `YYMMDD`; the symbol becomes 25 characters. |
| `"240230"` | Well-formed length, impossible date — passes every length check. |
| Strike ≥ 100000 | Overflows the 8-digit field into 22 characters. |
| Strike ≤ 0 | `-1` yields `AAPL  240816C-0001000`: still 21 characters, still nonsense. |
| Strike `200.0001` | `round()` silently returns the $200 contract. |
| Strike `0.0005` | `round()` is banker's rounding, so this becomes strike `0`. |

Only **equity** options use this layout. Future options use Tastytrade's own
format (`./ESU4 EW4Q4 240823C5750`) and must be resolved from the future-option
chain; `OptionLeg` skips OCC validation for `Future Option` legs rather than
pretending to validate a format it does not model.

## 3. Pre-trade validation (dry run)

```python
preview = client.dry_run_option_order(
    account_number, legs, OrderType.LIMIT, "2.15", PriceEffect.DEBIT
)
if not preview.is_acceptable:
    abort(preview.errors)
```

- `POST /accounts/{account}/orders/dry-run` creates nothing and is safe to retry.
- It returns `buying-power-effect`, `fee-calculation`, `warnings` and `errors`.
- A 2xx dry run can still carry `errors`; check `is_acceptable`, not the status
  code alone.
- Fee projections here are the only pre-trade view of what a four-leg structure
  actually costs. A spread whose edge is smaller than its fees is a losing trade
  the backtest probably did not model.

## 4. Multi-leg order construction

```python
legs = [
    OptionLeg(format_occ_symbol("SPY", "241220", "P", 480), LegAction.BUY_TO_OPEN, 1),
    OptionLeg(format_occ_symbol("SPY", "241220", "P", 490), LegAction.SELL_TO_OPEN, 1),
    OptionLeg(format_occ_symbol("SPY", "241220", "C", 510), LegAction.SELL_TO_OPEN, 1),
    OptionLeg(format_occ_symbol("SPY", "241220", "C", 520), LegAction.BUY_TO_OPEN, 1),
]
order = client.place_complex_option_order(
    account_number, legs, OrderType.LIMIT, "1.35", PriceEffect.CREDIT,
    external_identifier="condor-2026-09-02-001",
)
```

Payload rules enforced locally, before any network call:

- **Price direction lives in `price-effect`.** The API does not accept negative
  numbers. If the strategy produces a signed net price, use
  `price_effect_for_signed_price()` for the effect and pass `abs(price)`.
- **`Market` orders carry no price fields.** Passing `net_price` or
  `price_effect` with one raises rather than sending a payload the API has no
  field for.
- **Duplicate `(symbol, action)` legs raise.** Two identical legs double the
  intended size at the same price; combine the quantities instead. The same
  symbol with opposite actions is legitimate and allowed.
- **Quantities are positive whole contracts** for option and future legs.
- **Account numbers are validated** before being interpolated into a URL path.

"Complex" here means multi-leg in a single order. Tastytrade's separate
`/complex-orders` endpoint builds OCO/OTOCO groups from several orders and is not
covered by this client.

## 5. Outcome classification

`place_complex_option_order` sorts every submission into three buckets:

| Outcome | Raised / returned | Meaning |
|---|---|---|
| Rejected | `TastytradeOrderRejectedError` (4xx except 408/425/429) | No order exists. The payload must change; retrying it unchanged cannot succeed. `error_codes` carries the parsed codes. |
| Accepted | `TastytradeOrder` | A real `order.id` and `status` came back. Read `order.warnings`. |
| Ambiguous | `TastytradeAmbiguousOrderError` | Transport exception, 408/425/429/5xx, or a 2xx with no order id or no status. |

The client never fabricates an order id or a status. A synthetic id cannot cancel
anything, and a synthetic `"Received"` conceals a rejection — a 2xx with no id
most likely means the order *is* live and merely unnamed, which is exactly the
case where the caller most needs to know it does not have a handle.

## 6. Reconciliation after an ambiguous submission

```python
try:
    order = client.place_complex_option_order(..., external_identifier=tag)
except TastytradeAmbiguousOrderError as exc:
    existing = client.find_orders_by_external_identifier(exc.account_number, tag)
    if existing:
        adopt(existing[0])        # it landed
    else:
        resubmit_with_a_fresh_tag()
```

Tastytrade publishes no client-supplied idempotency key for order placement, so a
resubmission is a **new order**, not a deduplicated one. A timed-out four-leg
condor that is blindly retried becomes an eight-leg position at twice the
intended risk.

`external-identifier` is echoed back on the order, which is what makes the lookup
possible. It is a reconciliation tag, not an idempotency guarantee — Tastytrade
documents no server-side de-duplication on it, so never treat a matching tag as
proof that a duplicate *cannot* exist.

The dangerous direction is the other one. "I found nothing" and "I could not
tell" both look like an empty list, and only the first justifies resubmitting.
So on this path — unlike the reporting reads — the client raises instead of
returning `[]`:

- when the `/orders/live` response does not carry the `data.items` envelope, and
- when the live-order list echoes no `external-identifier` on *any* order, which
  means the tag is not being round-tripped and an empty match proves nothing.

Both leave the decision with a human or a supervising strategy rather than
letting a silent empty list authorise a duplicate order.

## 7. Position and order tracking

- `GET /accounts/{account}/positions` — open exposure.
- `GET /accounts/{account}/orders/live` — working and recently terminal orders.

Both unwrap the `{"data": {"items": [...]}}` envelope — leniently for positions,
where an empty list is a benign reporting answer, and strictly for
`/orders/live`, where it is not. A multi-leg order can
partially fill: a defined-risk spread that fills on only its short leg is a naked
short option. Monitor leg-level fills, not just order status.

## Production Implementation Reference

- Reference code: `scripts/tastytrade_client.py` (`TastytradeClient`,
  `TastytradeCredentials`, `OptionLeg`, `TastytradeOrder`, `OrderPreview`,
  `format_occ_symbol`, `parse_occ_symbol`, `price_effect_for_signed_price`).
- Automated unit tests: `scripts/test_tastytrade_client.py`.
- Sourced endpoint, header and lifetime table: `references/standards.md`.
