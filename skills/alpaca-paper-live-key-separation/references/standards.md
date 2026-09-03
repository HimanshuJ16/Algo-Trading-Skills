# Broker & Framework Coverage — alpaca-paper-live-key-separation

| Broker / Environment | Base URL Endpoint | Key Prefix | Safety Rules |
|---|---|---|---|
| Alpaca Paper Trading | `https://paper-api.alpaca.markets` | `PK...` | Paper sandbox execution |
| Alpaca Live Trading | `https://api.alpaca.markets` | `AK...` | Requires `ALLOW_LIVE_TRADING=true` |
| Interactive Brokers (IBKR) | Port 7497 (Paper) vs Port 7496 (Live) | Account ID `DU...` vs `U...` | Port & account prefix separation |

## Evidence & Confidence

| Claim | Status | Source |
|---|---|---|
| Paper base URL is `https://paper-api.alpaca.markets` | **Confirmed** | [Alpaca Paper Trading docs](https://docs.alpaca.markets/us/docs/paper-trading) — instructs setting `APCA_API_BASE_URL = https://paper-api.alpaca.markets` |
| Paper and live accounts use different API keys | **Confirmed** | [Alpaca Paper Trading docs](https://docs.alpaca.markets/us/docs/paper-trading) — "Your paper trading account will have a different API key from your live account" |
| GET `/v2/account` returns **no** `is_paper` field | **Confirmed** | [Get Account API reference](https://docs.alpaca.markets/us/reference/getaccount-1) schema, and the official [`alpaca-py` `TradeAccount` model](https://github.com/alpacahq/alpaca-py/blob/master/alpaca/trading/models.py) — neither contains `is_paper` |
| `trading_blocked` / `trade_suspended_by_user` mean orders are refused | **Confirmed** | [`alpaca-py` models](https://github.com/alpacahq/alpaca-py/blob/master/alpaca/trading/models.py) field docstring: "If true, the account is not allowed to place orders." |
| `account_blocked` prohibits account activity | **Confirmed** | [`alpaca-py` models](https://github.com/alpacahq/alpaca-py/blob/master/alpaca/trading/models.py): "If true, the account activity by user is prohibited." |
| `PAPER_ONLY` is a valid `AccountStatus` | **Confirmed** | [`alpaca-py` enums](https://github.com/alpacahq/alpaca-py/blob/master/alpaca/trading/enums.py) — `AccountStatus.PAPER_ONLY` |
| Key IDs are `PK…` for paper and `AK…` for live | **Observed convention, not officially documented** | Not present in Alpaca's own authentication or paper-trading documentation; corroborated only by third-party integration docs and community reports |
| Paper `account_number` values begin `PA` | **Observed convention, not officially documented** | Seen in Alpaca community/forum examples (e.g. `PA555C2E18OC`); no official statement found |

The two "observed convention" rows are the reason the reference implementation uses the key
prefix only to **reject** a credential bearing the opposite environment's prefix, and uses the
`PA` account-number prefix only as a **positive paper** signal. Neither is treated as proof that
an account is live, because neither is guaranteed by Alpaca and both could change without notice.

## The Authoritative Control

Environment separation rests on the **base URL**, not on any response field: Alpaca serves paper
accounts from the paper host and live accounts from the live host, so a live account is not
reachable through `paper-api.alpaca.markets`. Because the account payload carries no environment
discriminator, the probe can only ever *corroborate* the endpoint pin. Code that relies on the
probe alone — or that infers an environment from a field the API does not return — has no real
protection at all.

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

This skill implements an operational safety control, not a regulatory one; no rule cited here is
a compliance mandate. It does, however, support the pre-trade control expectations of SEC Rule
15c3-5 (US) and MiFID II RTS 6 (EU) by ensuring order flow cannot reach a production venue from a
configuration intended for testing. See `sec-rule-15c3-5-risk-controls-us` and
`mifid-ii-algo-trading-compliance-eu` for the actual regulatory requirements.
