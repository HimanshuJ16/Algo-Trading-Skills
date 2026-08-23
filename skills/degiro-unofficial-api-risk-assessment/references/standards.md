# Broker Integration Standards — degiro-unofficial-api-risk-assessment

## Contractual position (read first)

DEGIRO publishes no official trading API. DEGIRO's own helpdesk states that it
"does not support the use of external solutions, such as API wrappers or custom
scripts, that can interface with your DEGIRO account", and that using
third-party automation tools violates its terms of service. Every endpoint
below is undocumented and reverse-engineered; using them is a contractual
breach, not merely a technical risk.

Entity and supervision: DEGIRO is the trading name of the Dutch branch of
flatexDEGIRO Bank AG, which is supervised by BaFin; the Dutch branch is
registered with DNB and supervised by AFM and DNB.

## Endpoints

Paths follow the community `degiro-connector` library, the de facto record of
these undocumented endpoints. They may change without notice.

| Parameter | Specification | Description |
|---|---|---|
| Base Web URL | `https://trader.degiro.nl` | DEGIRO Web App host |
| Login | `/login/secure/login` | Standard username/password login |
| Login (TOTP) | `/login/secure/login/totp` | 2FA login; carries a `oneTimePassword` field |
| Login (in-app) | `/login/secure/login/in-app` | In-app confirmation variant |
| Client details | `/pa/secure/client` | Source of `intAccount` and client id |
| Product lookup | `/product_search/secure/v5/products/lookup` | Resolves internal integer product IDs |
| Dry-run | `/trading/secure/v5/checkOrder` | Order validation; returns `confirmationId` |
| Confirm | `/trading/secure/v5/order/{confirmationId}` | Order submission (second leg) |
| Session in URL | `;jsessionid=<sid>` plus `?intAccount=<acct>&sessionId=<sid>` | Both path-suffix and query parameters are sent |

## `checkOrder` response fields

Per the `CheckingResponse` model in `degiro-connector`, **only `confirmationId`
is required**. Every cost field is optional and has been observed absent in
practice (see issue #56, where the call returned just `confirmation_id` and
`response_datetime` on both NASDAQ and Euronext Amsterdam).

| Field | Type | Notes |
|---|---|---|
| `confirmationId` | str | Required; single-use token for the confirm leg |
| `transactionFee` | float or absent | Scalar commission component only |
| `transactionFees` | list or absent | Additional fee components |
| `transactionTaxes` | list or absent | e.g. transaction taxes |
| `transactionOppositeFees` | list or absent | Opposite-leg fees |
| `transactionAutoFxSurcharges` | list or absent | Auto-FX surcharges |
| `transactionAutoFxOppositeSurcharges` | list or absent | Opposite-leg FX surcharges |
| `autoFxConversionRate` | float or absent | FX rate applied |
| `freeSpaceNew` | float or absent | Remaining free space after the order |
| `showExAnteReportLink` | bool or absent | Ex-ante cost report availability |
| `responseDatetime` | datetime or absent | Set client-side by the connector |

There is **no `total` field** and no `freeCategory` field in this response.
Total consideration must be computed as gross notional plus the summed cost
components, and is unknown whenever no cost field is returned.

## Undocumented behaviour

DEGIRO publishes no rate limits, lockout thresholds, or session TTL. The
login-burst window, burst threshold, and 4-hour session staleness value in
`scripts/degiro_client.py` are this skill's operational heuristics, are
explicitly not sourced, and are constructor-overridable for calibration.

## Sources

- DEGIRO helpdesk, *Can I automate trades or use trading bots with DEGIRO?* — https://www.degiro.com/uk/helpdesk/trading-platform/can-i-automate-trades-or-use-trading-bots-degiro (also https://www.degiro.ie/helpdesk/trading-platform/can-i-automate-trades-or-use-trading-bots-degiro)
- DEGIRO helpdesk, *Who regulates DEGIRO?* — https://www.degiro.com/uk/helpdesk/about-degiro/safeguarded/who-regulates-degiro
- `degiro-connector`, endpoint constants — https://github.com/Chavithra/degiro-connector/blob/main/src/degiro_connector/core/constants/urls.py
- `degiro-connector`, `CheckingResponse` model — https://github.com/Chavithra/degiro-connector/blob/main/src/degiro_connector/trading/models/order.py
- `degiro-connector`, check/confirm order actions — https://github.com/Chavithra/degiro-connector/blob/main/src/degiro_connector/trading/actions/action_confirm_order.py
- `degiro-connector` issue #56, `check_order()` returning only `confirmation_id` and `response_datetime` — https://github.com/Chavithra/degiro-connector/issues/56

## Category

`broker-integration` — see top-level `mappings/` directory.
