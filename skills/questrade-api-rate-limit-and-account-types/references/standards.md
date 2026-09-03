# Broker Integration Standards — questrade-api-rate-limit-and-account-types

Every figure below was verified against the primary source listed in the
**Source** column. Where Questrade's own documentation is internally
inconsistent, both variants are recorded rather than one being presented as
canonical. Rows marked **Inferred** are this skill's conservative judgement,
not published Questrade behaviour.

## Hosts

| Parameter | Value | Source |
|---|---|---|
| Live OAuth2 token endpoint | `https://login.questrade.com/oauth2/token` | Questrade API — Getting started; Security |
| Practice OAuth2 token endpoint | `https://practicelogin.questrade.com/oauth2/token` | Questrade API — Getting started |
| Revoke endpoint | `POST https://login.questrade.com/oauth2/revoke` (form field `token`) | Questrade API — Security |
| API server | Dynamic, returned per session as `api_server` | Questrade API — Security ("The URL of the API servers will be provided to your application as a response to every access token request you make") |
| Transport | HTTPS only; plaintext connections refused | Questrade API — Security |

### `api_server` shapes observed in Questrade's own documentation

| Shape | Where it appears |
|---|---|
| `https://api01.iq.questrade.com` | Getting started (sample response); GET accounts (sample request host) |
| `https://api01.iq.questrade.com/` | Authorization (implicit-grant redirect fragment) |
| `https://api01.iq.questrade.com/v1` | Security (sample JSON response) |

Consequence: `f"{api_server}v1/accounts"` yields `…questrade.comv1/accounts` for
the first and `…/v1v1/accounts` for the third. Normalise before joining.

## OAuth2

| Parameter | Value | Source |
|---|---|---|
| Grant types supported | Authorization Code, Implicit. Resource Owner Password Credentials and Client Credentials are **not** supported | Questrade API — Authorization |
| Refresh grant parameters | `grant_type=refresh_token`, `refresh_token` | Questrade API — Security |
| Response properties | `access_token`, `token_type` (always `Bearer`), `expires_in`, `refresh_token`, `api_server` | Questrade API — Security |
| Request method | Getting started shows a bare URL (GET); Security shows `POST /oauth2/token`. Both are documented | Questrade API — Getting started; Security |
| `expires_in` values in samples | **300** (Authorization; Getting started) and **1800** (Implicit redirect; Security) | Questrade API — Authorization, Getting started, Security |
| Refresh-token rotation | Every redemption returns a new `refresh_token`; the submitted one is spent | Questrade API — Security ("You may want to store the refresh token, so that you can request new access token for next session") |
| Manual-authorization token validity | **7 days** from generation. Not stored decrypted by Questrade — if not copied, it must be regenerated | Questrade API — Getting started |
| Authorization header | `Authorization: Bearer {access_token}` | Questrade API — Security |

## OAuth scopes

| Scope | Identifier | Calls | Source |
|---|---|---|---|
| Read account information | `read_acc` | `GET time`, `GET accounts`, `accounts/:id/{positions,balances,executions,orders}`, `GET symbols/:id`, `GET symbols/:id/options`, `GET markets` | Questrade API — Security |
| Read market data | `read_md` | `GET markets/quotes/:id`, `GET markets/candles/:id` | Questrade API — Security |
| Trade | `trade` | `POST accounts/:id/orders[/:orderId]`, `POST accounts/:id/orders[/:orderId]/impact`, `DELETE accounts/:id/orders/:orderId` — **"partner developers only"** | Questrade API — Security |

Order placement is therefore unavailable to personal API applications, and no
order-placement endpoint appears in the public REST operations reference.
Getting started states the same: "If you are a Questrade **partner** developer,
you can also use the API to place trades."

Note that scope membership and rate-limit category are **different
partitions**: `GET markets`, `GET symbols/:id` and `GET symbols/:id/options`
are `read_acc` scope but Market Data rate limits.

## Rate limits

| Category | API calls | Max req/sec | Max req/hour | Source |
|---|---|---|---|---|
| Account calls | `GET time`, `GET accounts`, `accounts/:id/positions`, `accounts/:id/balances`, `accounts/:id/executions`, `accounts/:id/orders` | **30** | **30,000** | Questrade API — Rate limiting |
| Market Data calls | `GET markets`, `markets/quotes/:id`, `markets/candles/:id`, `symbols/:id`, `symbols/:id/options` | **20** | **15,000** | Questrade API — Rate limiting |

| Parameter | Value | Source |
|---|---|---|
| Headers on every limited response | `X-RateLimit-Remaining` (requests allowed against the current limit), `X-RateLimit-Reset` (Unix timestamp when the current limit expires) | Questrade API — Rate limiting |
| Which window `X-RateLimit-Remaining` describes | Not stated. Questrade sends one value for two windows | Questrade API — Rate limiting (silence) |
| Treatment of that ambiguity | Apply to the shortest-period window only; ignore a value above its capacity — **Inferred**, not documented | This skill |
| Exceeding a limit | HTTP **429** with the same rate-limit headers | Questrade API — Rate limiting |
| Uncategorised endpoints | `accounts/:id/activities`, `symbols/search`, `markets/quotes/options`, `markets/quotes/strategies` appear in the REST reference but in **neither** rate-limit category | Questrade API — Rate limiting (absence); Rest operations (existence) |
| Treatment of uncategorised endpoints | Apply the tighter Market Data budget — **Inferred**, not documented | This skill |
| Hourly cap vs per-second cap | 30 req/sec sustained is 108,000 req/hour, so the hourly cap binds after ~1,000 seconds of maximum-rate polling | Arithmetic on the two documented figures |

## Account types

Sixteen documented values (Questrade API — Enumerations, "Account Type"):

| Type | Description | Registered plan | May short |
|---|---|---|---|
| `Cash` | Cash account | No | No — no margin facility |
| `Margin` | Margin account | No | **Yes** |
| `TFSA` | Tax-Free Savings Account | Yes | No |
| `RRSP` | Registered Retirement Savings Plan | Yes | No |
| `FHSA` | First Home Savings Account | Yes | No |
| `SRRSP` | Spousal RRSP | Yes | No |
| `LRRSP` | Locked-In RRSP | Yes | No |
| `LIRA` | Locked-In Retirement Account | Yes | No |
| `LIF` | Life Income Fund | Yes | No |
| `RIF` | Retirement Income Fund | Yes | No |
| `SRIF` | Spousal RIF | Yes | No |
| `LRIF` | Locked-In RIF | Yes | No |
| `RRIF` | Registered RIF | Yes | No |
| `PRIF` | Prescribed RIF | Yes | No |
| `RESP` | Individual Registered Education Savings Plan | Yes | No |
| `FRESP` | Family RESP | Yes | No |

The "May short" column is not a Questrade API field. It follows from the
borrowing prohibition on registered plans (see **Regulatory basis**) plus
Questrade's requirement that short selling be conducted in a margin account.

`clientAccountType` is a **separate** enumeration: `Individual`, `Joint`,
`Informal Trust`, `Corporation`, `Formal Trust`, `Partnership`,
`Sole Proprietorship`, `Family`, `Joint and Informal Trust`, `Institution`.
`Individual` is not an account type.

## Account status

Documented values (Questrade API — Enumerations, "Account Status"), listed
without descriptions in the source:

`Active`, `Suspended (Closed)`, `Suspended (View Only)`, `Liquidate Only`,
`Closed`.

| Status | Treatment in this skill | Basis |
|---|---|---|
| `Active` | All sides permitted subject to account type | Documented value |
| `Liquidate Only` | Position-reducing sides only (`Sell`, `Cov`, `STC`, `BTC`) | **Inferred** from the status name |
| `Suspended (Closed)`, `Suspended (View Only)`, `Closed` | No sides permitted | **Inferred** from the status names |
| Unrecognised value | No sides permitted | Fail-closed default |

## Order sides

| Enumeration | Values | Source |
|---|---|---|
| Order Action | `Buy`, `Sell` | Questrade API — Enumerations |
| Order Side | `Buy`, `Sell`, `Short` (sell short), `Cov` (cover the short), `BTO`, `STC`, `STO`, `BTC` | Questrade API — Enumerations |

`SellShort` is **not** a Questrade value.

## Error handling

| Parameter | Value | Source |
|---|---|---|
| General error body | `code` (String), `message` (String) | Questrade API — Error handling |
| Order processing error body | `code`, `message`, **`orderId`**, `orders[]` — returned when a trade call "results in the creation of an order with a unique internal identifier" | Questrade API — Error handling |
| Order error under HTTP 200 | Documented explicitly: `HTTP/1.1 200 OK` with `code: 3054`, `"Order was rejected by the exchange"`, `orderId: 134353223` | Questrade API — Error handling ("Sample order error response (order created)") |

A non-success outcome therefore does **not** imply no order exists. Reconcile by
`orderId` rather than retrying — see `order-placement-idempotency`.

## Streaming

| Parameter | Value | Source |
|---|---|---|
| Capabilities | Order status changes, executions, L1 market data | Questrade API — Streaming |
| Transports | WebSocket or RawSocket; **only one at a time** — a second connection disconnects the first | Questrade API — Streaming |
| Port stability | Ports are stable for the same URL within the same day; they differ across URLs | Questrade API — Streaming |
| Auth on socket | Send the access token as the first message; **do not** prefix it with `Bearer` | Questrade API — Streaming |
| Session keepalive | "Keeping a socket open does not extend your session. You need to send requests at least every 30 minutes otherwise your socket will get disconnected when the session expires." | Questrade API — Streaming |
| Side effect | Using L1 streaming in the API freezes market data in any other IQ platform used simultaneously | Questrade API — Streaming |

## Regulatory basis for the registered-account borrow prohibition

Jurisdiction: **Canada** (federal Income Tax Act). These are tax-law
consequences on the plan, not securities-regulator trading rules, and they are
mandatory rather than advisory.

| Provision | Effect | Source |
|---|---|---|
| ITA 146(4)(a) | An RRSP trust is liable for tax "if the trust has borrowed money (other than money used in carrying on a business) in the year or has, after June 18, 1971, borrowed money … that it has not repaid before the commencement of the year" | Income Tax Act (RSC 1985, c. 1 (5th Supp.)), s. 146(4)(a) |
| ITA 146.2(2)(f) | A TFSA arrangement in trust must prohibit "the trust from borrowing money or other property for the purposes of the arrangement" | Income Tax Act, s. 146.2(2)(f) |
| ITA 146.2(4) | Permits the *holder* to pledge their interest in a TFSA as security for arm's-length indebtedness — i.e. the holder may borrow against it externally; the plan itself still may not borrow | Income Tax Act, s. 146.2(4) |
| Questrade product requirement | Short selling is offered through a Questrade **Margin** account | Questrade — Margin account product page |

Because a short sale requires borrowing the security, no registered plan can
hold a short position. This skill enforces the borrow prohibition; it does not
attempt to enumerate which *option* strategies Questrade approves inside each
registered plan, because that is an account-approval matter the API does not
report. See `regulatory-custody-requirements-by-jurisdiction` and
`capital-gains-vs-business-income-classification` for adjacent Canadian topics.

## Documentation currency caveat

Questrade's published API release notes list only C++/C#/ActiveX client library
versions, the most recent dated **December 18, 2015**. The REST reference has not
been versioned since. Treat the enumerations in particular as a floor rather
than a complete list — `FHSA` is documented, but the same table has not been
revised for other product launches — and fail closed on unrecognised values.

## Sources

- Questrade API — Rate limiting: <https://www.questrade.com/api/documentation/rate-limiting>
- Questrade API — Security (scopes, refresh grant, revoke): <https://www.questrade.com/api/documentation/security>
- Questrade API — Authorization: <https://www.questrade.com/api/documentation/authorization>
- Questrade API — Getting started (practice host, 7-day token): <https://www.questrade.com/api/documentation/getting-started>
- Questrade API — Error handling: <https://www.questrade.com/api/documentation/error-handling>
- Questrade API — Streaming: <https://www.questrade.com/api/documentation/streaming>
- Questrade API — Enumerations: <https://www.questrade.com/api/documentation/rest-operations/enumerations/enumerations>
- Questrade API — GET accounts: <https://www.questrade.com/api/documentation/rest-operations/account-calls/accounts>
- Questrade API — Release notes: <https://www.questrade.com/api/documentation/release-notes>
- Income Tax Act, s. 146 (RRSP): <https://laws-lois.justice.gc.ca/eng/acts/I-3.3/section-146.html>
- Income Tax Act, s. 146.2 (TFSA): <https://laws-lois.justice.gc.ca/eng/acts/I-3.3/section-146.2.html>
- Questrade — Margin accounts: <https://www.questrade.com/margin>
