# Standards for API Key Least-Privilege Audit

## Status of these requirements

The role/scope matrix below is an **engineering standard defined by this skill**, not a
regulatory one. No securities or market regulator surveyed for this skill publishes a
prescribed API-key scope matrix for trading bots, mandates a specific set of forbidden
scopes, or requires a particular audit cadence. General access-control and
least-privilege obligations do exist in financial-sector cyber-hygiene regimes, but the
specific scope names, role names and policy rows here are this repository's engineering
choices. Do not present them to an auditor as a regulatory obligation.

The one externally-sourced control on this page is the Binance IP-restriction
requirement, quoted and cited below.

## Role policy matrix — as implemented

This table mirrors `ROLE_POLICIES` in `scripts/key_auditor.py` exactly. The audit is
**deny-by-default**: any granted scope absent from the *Allowed* column is a violation,
whether or not it appears in the *Forbidden* column.

`CRITICAL` below expands to the `CRITICAL_FORBIDDEN_PERMISSIONS` frozenset:
`withdraw`, `withdraw_funds`, `transfer`, `crypto_transfer`, `account_admin`,
`sub_account_create`, `api_key_manage`.

| Role | Required | Allowed (superset of Required) | Forbidden | Purpose |
|---|---|---|---|---|
| `MARKET_DATA_ONLY` | `read_market_data` | `read_market_data`, `read_account_info` | `CRITICAL` + `place_orders`, `cancel_orders` | Market data feed reader |
| `EXECUTION_BOT` | `read_market_data`, `place_orders`, `cancel_orders` | `read_market_data`, `read_account_info`, `read_positions`, `place_orders`, `cancel_orders` | `CRITICAL` | Live order execution process |
| `PORTFOLIO_MONITOR` | `read_account_info`, `read_positions` | `read_market_data`, `read_account_info`, `read_positions`, `read_orders` | `CRITICAL` + `place_orders`, `cancel_orders` | Risk and accounting reader |
| `ADMIN_SUPERVISOR` | `read_account_info`, `read_positions` | the `PORTFOLIO_MONITOR` set + `place_orders`, `cancel_orders`, `account_admin`, `api_key_manage`, `sub_account_create` | `withdraw`, `withdraw_funds`, `transfer`, `crypto_transfer` | Human-supervised oversight key |

`ADMIN_SUPERVISOR` is the only role permitted administrative scopes, and it still forbids
every money-movement scope: administrative access is for oversight, not for moving
capital. The wildcard `*` is a violation for every role including this one.

## Where a key's granted scopes actually come from

The auditor trusts the scope set it is handed. Sourcing that set correctly is the part
of this workflow that most often goes wrong, because **not every venue lets a key
introspect its own permissions**.

| Venue | Key-permission introspection | What it returns | Money-movement scope |
|---|---|---|---|
| Binance (Spot) | **Yes** — `GET /sapi/v1/account/apiRestrictions` | `ipRestrict`, `createTime`, `enableReading`, `enableWithdrawals`, `enableInternalTransfer`, `enableMargin`, `enableFutures`, `permitsUniversalTransfer`, `enableVanillaOptions`, `enableFixApiTrade`, `enableFixReadOnly`, `enableSpotAndMarginTrading`, `enablePortfolioMarginTrading` | `enableWithdrawals`, `enableInternalTransfer`, `permitsUniversalTransfer` |
| Coinbase Advanced Trade | **Yes** — `GET /api/v3/brokerage/key_permissions` | `can_view`, `can_trade`, `can_transfer`, `portfolio_uuid`, `portfolio_type` | `can_transfer` — documented as "whether the API key has deposit/withdrawal permissions" |
| Kraken (Spot REST) | **No** documented endpoint returns the calling key's own permissions | — | `Withdraw Funds` permission, set in the key's UI configuration |
| Zerodha Kite Connect | **No** — `GET /user/profile` returns `user_id`, `user_name`, `user_shortname`, `email`, `user_type`, `broker`, `exchanges`, `products`, `order_types`, `avatar_url`, `meta.demat_consent`. None of these are API-key scopes. | — | Kite Connect exposes no fund-withdrawal API |
| Alpaca Broker API | Not documented as a REST introspection endpoint; scopes are configured in Access Controls | Per-scope `Read & Write` / `Read only` / `No Access` over: Accounts, Funding, Admin, Crypto, Rebalancing, Trading, Journaling, Data, Reporting, SSE events | `Funding` |

**Consequence for the workflow.** For Binance and Coinbase Advanced Trade the scope set
can be *probed* from the live key, which is the strong form of this audit: it reflects
what the key can do right now. For Kraken, Zerodha and Alpaca the scope set must be
*declared* from an operator-maintained record of how the key was configured, which is
only as accurate as that record. Record which of the two you used; a declared audit does
not prove the live key matches.

### Kraken permission vocabulary

Kraken's documented key permissions, for mapping onto the canonical scope names above:
Query Funds; Query Open Orders & Trades; Query Closed Orders & Trades; Create & Modify
Orders; Cancel/Close Orders; Query Ledger Entries; Export Data; Access WebSocket API;
Deposit Funds; **Withdraw Funds**; Earn.

## The endpoint mistake this skill exists to prevent

Binance's `GET /api/v3/account` returns `canTrade`, `canWithdraw`, `canDeposit` and a
`permissions` array. These describe the **account**, not the API key's granted scopes,
and Binance does not document them as key-permission flags. An audit that reads
`canWithdraw` from `/api/v3/account` is not auditing the key at all. The key's
permissions are at `GET /sapi/v1/account/apiRestrictions`.

Likewise, `GET /api/v3/brokerage/accounts` on Coinbase Advanced Trade lists brokerage
accounts; the key's permissions are at `GET /api/v3/brokerage/key_permissions`.

## IP access restriction — the paired control (externally sourced)

Scope reduction is necessary but not sufficient. Binance states in its API key
documentation:

> "Adding IP access (IPv4 format) restrictions is mandatory to enable withdrawal
> permission."

and

> "the system-generated API Key's permission can only be set to [Enable Reading] if your
> IP is unrestricted."

So on Binance a withdrawal-capable key is *necessarily* IP-restricted. That is a venue
product rule, not a general one — do not assume other venues couple the two. Treat
scope audit and IP binding as two independent controls and verify both.

This auditor checks scopes only. It does not read, verify or enforce IP restrictions.

## Limitations of this reference

- The venue table was verified against the cited vendor documentation during this audit.
  Venues change key-permission models and endpoint paths without changing API version —
  re-check before relying on a row.
- OKX, Bybit, Interactive Brokers and Upstox were **not** verified for this table and are
  deliberately omitted rather than guessed at.
- Alpaca's row covers **Broker API** credential Access Controls. Retail Trading API key
  scoping was not verified and is not claimed here.
- The auditor covers static API key scopes only. OAuth token scopes and session-based
  permissions have their own models and are out of scope — see
  `token-lifecycle-live-probing`.

## Sources

- Binance, *Get API Key Permission* (`GET /sapi/v1/account/apiRestrictions`) —
  <https://developers.binance.com/docs/wallet/account/api-key-permission>
- Binance, *Spot REST — Account Endpoints* (`GET /api/v3/account`) —
  <https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints>
- Binance, *How to Create API Keys on Binance* —
  <https://www.binance.com/en/support/faq/how-to-create-api-keys-on-binance-360002502072>
- Coinbase Developer Platform, *Get API Key Permissions*
  (`GET /api/v3/brokerage/key_permissions`) —
  <https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/data-api/get-api-key-permissions>
- Kraken, *API key permissions* —
  <https://docs.kraken.com/exchange/guides/rest/api-keys>
- Zerodha, *Kite Connect v3 — User* (`GET /user/profile`) —
  <https://kite.trade/docs/connect/v3/user/>
- Alpaca, *Credentials Management* (Access Controls) —
  <https://docs.alpaca.markets/docs/credential-management>

## Category

`broker-integration` — see top-level `mappings/` directory.
