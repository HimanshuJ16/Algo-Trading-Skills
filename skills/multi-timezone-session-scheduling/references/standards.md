# Broker & Framework Coverage — multi-timezone-session-scheduling

| Library / Standard | Relevance to this skill |
|---|---|
| IANA Time Zone Database (tzdb) | Source of truth for zone keys (`America/New_York`, `Europe/London`, `Asia/Kolkata`) and their historical + current transition rules. |
| Python `zoneinfo` (3.9+) | Standard-library tz support. On hosts with no system tz database (notably Windows) the `tzdata` package supplies it; without either, every `ZoneInfo` lookup raises `ZoneInfoNotFoundError`. |
| PEP 495 (`fold`) | Defines how a repeated local wall time is disambiguated. `fold=0` is the default and selects the FIRST occurrence; a skipped wall time is normalised by the pre-transition offset. Neither case raises, which is why they must be detected explicitly. |
| ISO 10383 (MIC) | Market Identifier Codes used as exchange keys (`XNYS`, `XLON`, `XNSE`, `XTKS`, `XASX`). |
| ISO 8601 / RFC 3339 | Timestamp representation with an explicit UTC offset. |

## Category

`data-management-global` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Shipped Default Schedules

`DEFAULT_EXCHANGE_SCHEDULES` in `scripts/session_scheduler.py` models **continuous trading only**;
opening and closing auction windows are excluded, since an auction is not a period in which a
resting limit order trades continuously. Verify against the exchange's own published calendar
before relying on these in production — exchange hours change.

| MIC | Exchange | Local continuous session | Notes |
|---|---|---|---|
| `XNYS` | New York Stock Exchange | 09:30–16:00 ET | Early Trading 07:00 ET, Late Trading to 20:00 ET. The familiar 04:00 ET pre-market start is **NYSE Arca / Nasdaq**, not NYSE — override `pre_market_time` for those venues. |
| `XLON` | London Stock Exchange | 08:00–16:30 (Europe/London) | Closing auction runs 16:30–16:35 after the modelled close. |
| `XNSE` | NSE India | 09:15–15:30 IST | Pre-open 09:00–09:15. Asia/Kolkata (UTC+5:30) observes no DST. |
| `XTKS` | Tokyo Stock Exchange | 09:00–11:30 and 12:30–15:30 JST | Close extended from 15:00 to 15:30 effective **2024-11-05**, with a closing auction 15:25–15:30. The 11:30–12:30 lunch break is modelled as a `breaks` entry. |
| `XASX` | ASX | 10:00–16:00 (Australia/Sydney) | Closing single-price auction follows ~16:10. Southern Hemisphere: UTC+11 in January, UTC+10 in July. |

Sources: NYSE *Holidays & Trading Hours* (nyse.com/trade/hours-calendars); JPX *Final Decision for
Extension of Trading Hours and Introduction of Closing Auction*, 2023-09-20, effective 2024-11-05;
NSE India *Market Timings* (nseindia.com/static/market-data/market-timings); London Stock Exchange
market close information document; ASX trading-day documentation.

## DST Rule Sources

- **US** — DST runs 02:00 *local* on the second Sunday of March to 02:00 local on the first Sunday
  of November (15 U.S.C. § 260a, as amended by the Energy Policy Act of 2005).
- **EU** — Summer time runs 01:00 *GMT* on the last Sunday of March to 01:00 GMT on the last Sunday
  of October (Directive 2000/84/EC, Arts. 2–3). The 2018 Commission proposal to discontinue seasonal
  clock changes (COM(2018) 639) has **not** been adopted, so the Directive remains in force.
- The two rules do not coincide, producing a desynchronisation window each spring and autumn during
  which the US/EU offset differential moves by one hour.

Treat these as background for *why* offsets move. Do not reimplement them: the tz database already
encodes them, including amendments, and hand-rolled rules go stale.

## Regulatory & Operational Notes

Session scheduling intersects with exchange official-hours rules, cross-border settlement schedules,
and trade-timestamping obligations. On timestamping specifically, **MiFID II RTS 25**
(Commission Delegated Regulation (EU) 2017/574) sets a *tiered* requirement rather than a blanket
microsecond mandate:

- Members/participants using **high-frequency algorithmic trading techniques**: maximum divergence
  from UTC of **100 microseconds**, granularity of 1 microsecond or better.
- Other members/participants and trading venues whose gateway-to-gateway latency exceeds
  1 millisecond: maximum divergence of **1 millisecond**, granularity of 1 millisecond or better.

RTS 25 governs the *accuracy of recorded timestamps*, not session scheduling. It is cited here
because a scheduler that resolves session boundaries to the wrong UTC instant will also stamp
records against the wrong session, which is where the two concerns meet. Confirm applicability for
your own jurisdiction, entity type, and trading activity before treating either tier as binding.
