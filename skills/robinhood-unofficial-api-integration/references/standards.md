# Broker Integration Standards — robinhood-unofficial-api-integration

Every row below is traced to the source in the **Source** column. Robinhood
publishes no specification for the endpoints in the "Unofficial endpoints"
section — those rows are traced to the community reference implementations that
exercise them daily, and are marked accordingly. Rows marked **Inferred** are
this skill's conservative judgement, not published Robinhood behaviour.

## Contractual position (read this before the technical rows)

| Item | Finding | Source |
|---|---|---|
| Consent required for API access | "You may not use the API Package or develop Licensee Products without Robinhood's express written consent (and Robinhood may decline any such request for use or development in its sole discretion)." | RHF-RHS Customer Agreement §29.1 "API & MCP", eff. 2026-07-02 |
| Third-party order entry | "You agree not to allow any person access to your Account, your Account username or password, or permit any other person to give orders or instructions on your Account to Robinhood, without the prior consent of Robinhood." | RHF-RHS Customer Agreement §4.7 "Orders and instructions" |
| Risk allocation for API orders | "Orders created and submitted through any API Products are not vetted until they are received by Robinhood. It is possible that Robinhood may reject an order placed through any API Products." | RHF-RHS Customer Agreement §29.5 "Risks; No Liability" |
| Official time of an order | "The time an order or a request is actually received by Robinhood (including for execution) will be the official time" | RHF-RHS Customer Agreement §29.5 |
| Automated-access clause in the general T&C | The Robinhood Terms & Conditions (updated 2025-08-22) contains a prohibited-conduct list scoped to the **Forums**; it does **not** contain a general anti-scraping/anti-automation clause. The operative restrictions are in the Customer Agreement above, not the T&C. | Robinhood Terms and Conditions PDF, 2025-08-22 — verified by full-text search |

The last row matters because the widely repeated claim that "Robinhood's Terms of
Service explicitly prohibit automated access" is not supported by the current
Terms & Conditions document. The correct citation is Customer Agreement §29.1
and §4.7. Cite the agreement that actually says it.

## Sanctioned alternatives

| Product | Scope | Endpoint / entry point | Availability | Source |
|---|---|---|---|---|
| Agentic Trading (MCP) | Stocks and options; crypto | `https://agent.robinhood.com/mcp/trading` | Opened to third-party agents 2026-05-27 (equities/options), extended to crypto 2026-07-20; free to eligible US customers | Robinhood — "Agentic Trading overview"; Robinhood Newsroom |
| Crypto Trading API | Crypto only — "lets you view crypto market data, access your account information, and place crypto orders programmatically". Equities and options are not supported | `https://docs.robinhood.com/` | US Robinhood Crypto customers; v1 (no fee tiers) and v2 (fee tiers) | Robinhood — "Robinhood Crypto Trading API" support article; docs.robinhood.com |
| Crypto API authentication | API key plus an Ed25519 request signature carried in `x-api-key`, `x-timestamp` and `x-signature` headers. Keys issued after 2024-08-13 are formatted `rh-api-[uuid]` | — | — | docs.robinhood.com |

Agentic Trading constraints worth knowing before choosing it: trades execute in a
**dedicated agentic account**, not the primary investing account; the agent has
read access to portfolio data; crypto is unavailable in some states including New
York; and the agent cannot transfer, stake or lend crypto.

## Unofficial endpoints

Robinhood publishes no reference for these. The column below cites the community
implementation that exercises them.

| Parameter | Value | Source |
|---|---|---|
| Host | `https://api.robinhood.com` | robin_stocks; sanko/Robinhood |
| Token endpoint | `POST /oauth2/token/` | robin_stocks `authentication.py` |
| OAuth client id | `c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS` — harvested from Robinhood's own web client, **not** a credential issued to you; can be rotated or revoked at any time | robin_stocks `authentication.py` |
| Token request fields | `client_id`, `grant_type=password`, `username`, `password`, `device_token`, `scope`, `expires_in`; current robin_stocks additionally sends `try_passkeys`, `token_request_path` and `create_read_only_secondary_token` | robin_stocks `authentication.py` |
| MFA field | `mfa_code`, added to the token payload when supplied | robin_stocks `authentication.py` |
| Challenge response endpoint | `POST /challenge/{challenge_id}/respond/` | robin_stocks `authentication.py` |
| Device-approval poll | `GET /push/{challenge_id}/get_prompts_status/`, awaiting `challenge_status == "validated"`, then a `workflow_status_approved` confirmation | robin_stocks `authentication.py` |
| Rate limits | **None published.** No documented per-second or per-hour budget, no `Retry-After` contract, no rate-limit headers documented | Absence of any Robinhood publication |
| Local polling default | ≥ 2 s between polls — **Inferred**, this skill's conservative default, not a broker figure | This skill |

### Login MFA reality

| Item | Finding | Source |
|---|---|---|
| Primary login MFA today | In-app **device approvals**: "When you update your account information or log in with a new device, we'll notify your mobile devices to verify it's really you taking the action." | Robinhood — "Device approvals" support article |
| Where approvals are delivered | Device approvals "only go to a trusted Robinhood device" | Robinhood — "Device approvals" |
| Consequence for headless hosts | A server is never a trusted Robinhood device, so there is no unattended completion path for a device-approval challenge — **Inferred** from the above | This skill |
| Authenticator-app OTP | Reported phased out in favour of device approvals; community wrappers break on the transition repeatedly (robin_stocks issues #530, #535, #1597, #1621) | robin_stocks issue tracker |
| Response marker | The token response carries `verification_workflow` with an `id`, distinct from `mfa_required`/`mfa_type` | robin_stocks `authentication.py` |

### `POST /orders/`

| Field | Meaning | Source |
|---|---|---|
| `account` | "Account to make this order with" — a URL from the account profile | sanko/Robinhood `Order.md` |
| `instrument` | "Instrument URL of the security you're attempting to buy or sell" | sanko/Robinhood `Order.md` |
| `symbol` | "The ticker symbol of the security" | sanko/Robinhood `Order.md` |
| `type` | `market` or `limit` | sanko/Robinhood `Order.md` |
| `time_in_force` | `gfd`, `gtc`, `ioc` or `opg` | sanko/Robinhood `Order.md` |
| `trigger` | `immediate` or `stop` | sanko/Robinhood `Order.md` |
| `price` / `stop_price` | Limit price / stop conversion price | sanko/Robinhood `Order.md` |
| `quantity`, `side` | Shares; `buy` or `sell` | sanko/Robinhood `Order.md` |
| `extended_hours` | "Would/Should order execute when exchanges are closed" | sanko/Robinhood `Order.md` |
| `ref_id` | Client-generated UUID sent on every order by robin_stocks (`'ref_id': str(uuid4())`) | robin_stocks `orders.py` |
| `override_day_trade_checks`, `override_dtbp_checks` | Documented, undescribed. Not sent by this skill's client — overriding a broker's own day-trade check is not a default any automation should take | sanko/Robinhood `Order.md` |

**`ref_id` idempotency is not a documented contract.** Community implementations
rely on it as a duplicate guard, but Robinhood publishes no statement of the
behaviour. Treat it as a reconciliation handle first: always send a stable value,
and after an ambiguous outcome look the order up by `ref_id` rather than assuming
a resubmission will be de-duplicated.

**Market orders are collared limit orders.** robin_stocks converts a market buy
during regular hours into `'type': 'limit'` with `"preset_percent_limit": "0.05"`
(a 5% collar), and converts orders to limit type for extended/all-day hours. A
"market" order can therefore fail to fill in a fast market.

### Documented order states

`queued`, `unconfirmed`, `confirmed`, `partially_filled`, `filled`, `rejected`,
`canceled`, `failed` (sanko/Robinhood `Order.md`). Anything outside this set means
the schema moved; treat the order as live and reconcile.

### `GET /positions/`

| Item | Finding | Source |
|---|---|---|
| Pagination | Paginated; robin_stocks reads it with `request_get(url, 'pagination')` and follows the cursor | robin_stocks `account.py` |
| Server-side filter | `{'nonzero': 'true'}` returns only currently-held positions; without it the endpoint returns "every position ever traded" | robin_stocks `account.py` |
| Ticker symbol | **Not present.** Documented keys include `url`, `instrument`, `account`, `account_number`, `average_buy_price`; the symbol must be resolved from the `instrument` URL (`build_holdings` calls `get_instrument_by_url(item['instrument'])`) | robin_stocks `account.py` |
| Other fields used here | `quantity`, `average_buy_price`, `shares_held_for_sells` | robin_stocks `account.py` |
| Max pages followed | 20 — **Inferred**, a local bound so a malformed cursor cannot spin | This skill |

## Sources

- RHF-RHS Customer Agreement (eff. 2026-07-02) — https://cdn.robinhood.com/assets/robinhood/legal/Robinhood-Customer-Agreement.pdf
- Robinhood Terms and Conditions (updated 2025-08-22) — https://cdn.robinhood.com/assets/robinhood/legal/Robinhood%20Terms%20and%20Conditions.pdf
- Robinhood — Agentic Trading overview — https://robinhood.com/us/en/support/articles/agentic-trading-overview/
- Robinhood — Robinhood is Now Open to Agents (Newsroom) — https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/
- Robinhood — Crypto Trading API — https://robinhood.com/us/en/support/articles/crypto-api/ and https://docs.robinhood.com/
- Robinhood — Device approvals — https://robinhood.com/us/en/support/articles/device-approvals/
- robin_stocks (community reference) — https://github.com/jmfernandes/robin_stocks
- sanko/Robinhood unofficial documentation — https://github.com/sanko/Robinhood/blob/master/Order.md
