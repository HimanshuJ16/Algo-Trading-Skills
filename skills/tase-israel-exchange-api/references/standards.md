# Tel Aviv Stock Exchange (TASE) Integration Standards

> **Sourcing note.** TASE's own trading-schedule and instrument pages are client-rendered
> and were not machine retrievable when this reference was written. Every claim below is
> tagged with its corroboration level. Anything marked **Unverified** must be confirmed
> against TASE Member Services documentation or the venue's session-definition feed
> before it drives production behaviour.

## 1. Trading week — changed 5 January 2026

**Corroborated.** TASE moved from a **Sunday-Thursday** to a **Monday-Friday** trading
week effective **5 January 2026**. The change was announced by the Israel Securities
Authority with the approval of the Ministry of Finance, and was reflected ahead of the
effective date in the operational notices of index providers whose benchmarks include
TASE-listed securities (MSCI, Solactive). Friday is a shortened session that closes
before the onset of Shabbat.

This is the single highest-impact fact in this skill. A system carrying the pre-2026
calendar fails in both directions — it treats Friday as closed and sits out a live
session, and treats Sunday as open and routes orders into a closed market.

| Regime | In force | Trading days (`date.weekday()`) | Short day |
| :--- | :--- | :--- | :--- |
| Monday-Friday | from 2026-01-05 | Mon-Fri (0,1,2,3,4) | Friday |
| Sunday-Thursday (legacy) | until 2026-01-04 | Sun-Thu (6,0,1,2,3) | Sunday |

The legacy regime is retained in code for backtests over pre-2026 history. Replaying
2025 data under the current calendar reintroduces the same error in reverse.

## 2. Session boundaries (Israel local time)

| Boundary | Mon-Thu | Friday | Corroboration |
| :--- | :--- | :--- | :--- |
| Pre-open opens | 09:25 | 09:25 | Vendor session tables; contemporaneous press coverage |
| Session open | 09:59 | 09:59 | **Corroborated** — MSCI announcement for the 2026 change |
| Continuous trading from | 10:00 | 10:00 | Derived from session open |
| Closing auction starts | 17:15 | 13:40 | **Unverified** — placeholder, replace before production |
| Session close | 17:25 | 13:50 | **Corroborated** — MSCI announcement for the 2026 change |

Secondary sources disagree on the minute-level detail (close reported variously as 17:25
and 17:35; Friday close as 13:50 and 14:00). Where sources conflict, the table follows
MSCI's operational notice, which is the source an index-tracking participant would be
held to. This is exactly why the schedule is a configurable dataclass rather than
hard-coded comparisons — the boundaries are expected to be overridden with venue-supplied
values.

## 3. Timezone — IST / IDT

**Corroborated.** Under Israel's Time Determination Law, clocks advance one hour from the
**Friday before the last Sunday in March** until the **last Sunday in October**:

- **IST** (winter) = UTC+2
- **IDT** (summer) = UTC+3

That is roughly seven months of the year on IDT. A hard-coded UTC+2 offset therefore
misclassifies the session phase by a full hour for most of the trading calendar. Resolve
local time through the IANA zone `Asia/Jerusalem` so transitions come from the tz
database. Treat a missing tz database (`tzdata` on Windows and slim containers) as a
start-up failure rather than a reason to substitute a constant offset.

## 4. Price denomination

The conversion between quoted price and cash value is the highest-consequence arithmetic
in the integration; an error here is 100x in either direction and passes every other
pre-trade check because those checks are computed from the same wrong number.

| Instrument class | Quoted in | Cash value per unit | Needs |
| :--- | :--- | :--- | :--- |
| Equities, ETFs, mutual funds | Agorot | `price / 100` ILS | — |
| Corporate & government bonds | % of par | `price / 100 x par_value_ils` | Par value |
| Makam (T-bills) | % of par | `price / 100 x par_value_ils` | Par value |
| Index options & futures | ILS | `price` | — |

> [!IMPORTANT]
> A percentage quote carries **no cash value on its own**. A bond at 102.5 is 102.5% of
> par, not 102.5 ILS. The engine refuses to register a percentage-quoted instrument
> without a positive `par_value_ils`, and refuses to value such an order if one is
> missing, rather than silently treating the percentage as shekels.

> [!IMPORTANT]
> Validate the order's declared denomination **against the security master**, not against
> a convention. An order claiming Agorot is claiming it because the caller set the field,
> which is precisely the bug you are trying to catch.

## 5. FIX field encoding

**Corroborated** against the FIX 5.0 dictionary for `OrdType` (tag 40):

| Intent | Tag 40 value | Note |
| :--- | :--- | :--- |
| Market | `1` | |
| Limit | `2` | |
| Stop / Stop Loss | `3` | **Not** stop-limit |
| Stop Limit | `4` | |
| Iceberg | *not an OrdType* | Limit order (`2`) + `DisplayQty` (tag 1138); `MaxFloor` (tag 111) in FIX 4.x |

Tag 40 = `L` is "Previous Fund Valuation Point", not iceberg. `Side` (tag 54) uses
`1` = Buy, `2` = Sell; `OrdStatus` (tag 39) uses `0` = New, `1` = Partially filled,
`2` = Filled, `4` = Canceled, `8` = Rejected. `TradSesStatus` (tag 340) has its own
enumeration (`2` = Open, `4` = Pre-Open) which does **not** match this module's symbolic
`MarketPhase` values — do not serialise one as the other.

## 6. Platform and protocol

**Partially verified.** TASE runs its trading on Nasdaq's **Genium INET** platform, having
migrated from the earlier TACT system. The specific FIX version, gateway topology, session
configuration and market-data protocols offered to members are documented in TASE Member
Services material that is not publicly retrievable, so this skill makes **no claim** about
which FIX version or market-data feed a given member is entitled to. Obtain the connectivity
specification from TASE Member Services and configure `protocol_version` accordingly.

Note that FIX 5.0 and later split the session layer into **FIXT.1.1**; describing a
deployment as "FIX 5.0 SP2 session management" conflates the application and transport
layers. Confirm both independently.

## 7. Regulatory and pre-trade controls

Israeli securities markets are supervised by the **Israel Securities Authority (ISA)**.
Concrete obligations for algorithmic trading members — order-to-trade ratio caps,
self-match prevention requirements, dynamic price collar bands and any member
certification regime — are set out in TASE bylaws, directives and ISA regulation. Those
instruments were not retrievable for this reference, so **no specific threshold, ratio or
band is asserted here**.

What the engine implements is the *enforcement structure*, with limits supplied by the
operator rather than assumed:

- `max_order_qty` — per-order quantity cap.
- `max_order_value_ils` — per-order notional cap, estimated from the reference price when
  the order carries no price, and refused outright when neither is available.
- `max_price_collar_pct` — deviation cap against the registered reference price.
- `require_registered_security` — refuses to route an instrument whose denomination and
  reference price cannot be verified.
- `enforce_session_calendar` — refuses order entry outside an order-accepting phase.

Populate these from TASE/ISA source documents and your own risk policy. Do not treat the
defaults as regulatory values — they are placeholders chosen to be restrictive.
