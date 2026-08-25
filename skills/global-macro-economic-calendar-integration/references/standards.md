# Standards — global-macro-economic-calendar-integration

## Configuration defaults (calibrate before use)

**No regulator, exchange, or standards body identified in the research for this
skill mandates a trading halt around a macroeconomic release.** The buffer values
below are this library's defaults and carry no external authority. They exist so
the engine has a definable window; the correct width depends on your instrument's
liquidity profile, your latency, and how long your venue's book actually takes to
rebuild after the print. Calibrate them and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `pre_event_buffer_sec` | `900.0` (15 min) | How long before a blocking release the gate closes. Applies to `HIGH_IMPACT` and, unless overridden, to `MEDIUM_IMPACT`. |
| `post_event_buffer_sec` | `900.0` (15 min) | How long after the release the gate stays closed. **Too short for events with a follow-on** — see the FOMC row below. |
| `medium_pre_event_buffer_sec` / `medium_post_event_buffer_sec` | `None` (inherit the high-impact values) | Set these to give `MEDIUM_IMPACT` events a narrower window than `HIGH_IMPACT` ones. |
| `BLOCKING_SEVERITIES` | `(HIGH_IMPACT, MEDIUM_IMPACT)` | Which severities raise a blackout. `LOW_IMPACT` events sit in the calendar and are reported, but do not block. |
| `require_non_empty_calendar` | `True` | An empty calendar returns `MACRO_CALENDAR_UNAVAILABLE` and blocks. Setting this `False` makes "the feed never loaded" indistinguishable from "nothing is scheduled". |
| `max_calendar_age_sec` | `None` (no staleness check) | Set it whenever the feed can go silent: past this age relative to `calendar_as_of_utc`, the status becomes `MACRO_CALENDAR_STALE` and trading is blocked. |
| `surprise_lookback_sec` | `86_400.0` (24 h) | How far back the permitted branch looks for a released event to report a surprise for. A window, not a signal horizon — it does not imply a 24-hour signal decays. |
| Per-event `pre_event_buffer_override_sec` / `post_event_buffer_override_sec` | `None` | Per-event widening. This is the mechanism for the FOMC press conference and for any release whose market-moving component is not the headline print. |

## Sourced facts

| Fact | Source |
|---|---|
| The FOMC **statement is released at 2:00 p.m. ET** on the second day of a policy meeting. For the 28 January 2026 meeting the Board's calendar states the statement is released "at 2 p.m. EST" and **"the Chair's news conference will start at 2:30 p.m. EST"**. A 15-minute post-release buffer therefore reopens trading 15 minutes *before* the press conference begins. | [Federal Reserve — Calendar: January 2026](https://www.federalreserve.gov/newsevents/2026-january.htm); [Federal Reserve — FOMC Meeting Calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm); statement pages carry "Released … at 2:00 p.m.", e.g. [17 September 2025](https://www.federalreserve.gov/monetarypolicy/fomcpresconf20250917.htm) |
| **BLS principal releases are issued at 8:30 a.m. Eastern Time** — a fixed *wall-clock* time, therefore a moving UTC instant across DST. The CPI schedule lists e.g. August 2026 CPI for 11 September 2026 at 8:30 a.m. ET; the Employment Situation schedule lists 4 September 2026 at 8:30 a.m. ET. | [BLS — Schedule of Releases for the CPI](https://www.bls.gov/schedule/news_release/cpi.htm); [BLS — Schedule of Releases for the Employment Situation](https://www.bls.gov/schedule/news_release/empsit.htm) |
| **A scheduled release can simply not happen.** During the lapse in appropriations from 1 October to 12 November 2025, BLS did not publish an October 2025 Employment Situation news release, and published no all-items or core CPI estimate for October 2025; CPS collection was suspended for the period. A calendar that goes empty or stale is a real operating state, not only a bug. | [BLS — Revised news release dates following the 2025 and 2026 lapses in appropriations](https://www.bls.gov/bls/2025-lapse-revised-release-dates.htm); [BLS — 2025 federal government shutdown impact on the CPS](https://www.bls.gov/cps/methods/2025-federal-government-shutdown-impact-cps.htm); [BLS — 2025 shutdown impact on CE and CPI](https://www.bls.gov/cpi/additional-resources/2025-federal-government-shutdown-impact-cpi.htm) |
| **US DST**: standard time is advanced one hour "commencing at 2 o'clock antemeridian on the second Sunday of March … and ending at 2 o'clock antemeridian on the first Sunday of November", as amended by the Energy Policy Act of 2005 (Pub. L. 109–58). | [15 U.S.C. § 260a](https://uscode.house.gov/view.xhtml?req=%28title%3A15+section%3A260a+edition%3Aprelim%29) |
| **EU DST**: from 2002 the summer-time period begins at **1.00 a.m. Greenwich Mean Time on the last Sunday in March** and ends at **1.00 a.m. GMT on the last Sunday in October**. The US and EU therefore transition on different dates, and the US–Europe offset differs from its usual value for several weeks each spring and autumn. | [Directive 2000/84/EC (CELEX 32000L0084)](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32000L0084) |
| **Trading Economics calendar schema**: `Importance` is documented as `(1-Low, 2-Medium, 3-High)`, and `Date` is serialised as `yyyy-MM-ddTHH:mm:ss` **with no timezone designator** — e.g. `"Date": "2023-03-30T00:00:00"`. Two consequences: numeric severity codes must be normalised rather than string-compared, and a midnight value is a date placeholder, not an 00:00 UTC release. | [Trading Economics — Economic Calendar API](https://docs.tradingeconomics.com/economic_calendar/snapshot/) |
| **FRED release dates are dates, not instants.** `fred/release/dates` returns a `date` field formatted `YYYY-MM-DD`, and the documentation notes release dates "are published by data sources and do not necessarily represent when data will be available on the FRED or ALFRED websites." FRED can tell you *which day* a release falls on; it cannot supply a blackout-grade timestamp, and it publishes no consensus forecast. | [FRED API — fred/release/dates](https://fred.stlouisfed.org/docs/api/fred/release_dates.html); [FRED API — fred/releases/dates](https://fred.stlouisfed.org/docs/api/fred/releases_dates.html) |
| **Standardising a surprise by the standard deviation of past surprises for that indicator** is the construction used by Scotti's real-time surprise index (and the same normalisation underlying Citi-style economic surprise indices): surprises are divided by their standard deviation precisely because units of measurement differ across macroeconomic variables. A "surprise" left in native units is not comparable across indicators and cannot be thresholded. | [Chiara Scotti, *Surprise and Uncertainty Indexes: Real-Time Aggregation of Real-Activity Macro Surprises*, FRB IFDP No. 1093r (2016)](https://www.federalreserve.gov/econresdata/ifdp/2013/files/ifdp1093r.pdf); published in the *Journal of Monetary Economics* 82 (2016) |
| **RTS 6 Article 12 ("Kill functionality")** requires an investment firm engaged in algorithmic trading to be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders at any or all trading venues it is connected to. **Article 15 ("Pre-trade controls on order entry")** requires pre-trade controls on order entry. These are the *general* obligations that a macro blackout gate helps satisfy; **neither article, and no provision identified in this research, requires a halt around a macroeconomic release.** | [Commission Delegated Regulation (EU) 2017/589 (RTS 6)](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0589) |
| **SEC Rule 15c3-5** ("Market Access Rule") requires brokers or dealers with market access to have risk-management controls reasonably designed to prevent the entry of erroneous orders and orders exceeding pre-set credit or capital thresholds. As with RTS 6, **no macro-release-specific halt requirement was identified**; the blackout is a control you choose, justified by execution risk, not by rule text. | [17 CFR § 240.15c3-5](https://www.ecfr.gov/current/title-17/chapter-II/part-240/section-240.15c3-5) |

**Consequence encoded in the engine.** Because a vendor severity code you do not
recognise, an empty calendar, and a stale calendar are all indistinguishable from
"nothing is scheduled", each of them blocks rather than permits:
`normalize_impact_severity` raises on unknown codes, and the audit returns
`MACRO_CALENDAR_UNAVAILABLE` / `MACRO_CALENDAR_STALE` with
`is_trading_permitted=False`.

**Consequence for the surprise index.** Because standardisation requires the
standard deviation of that indicator's past surprises, `calculate_surprise_index`
returns `None` when `forecast_std_dev` is absent rather than substituting `1.0`.
The unstandardised difference is still available as `macro_surprise_raw`, labelled
as being in the release's own units.

## Known limitations

- **Scheduled events only.** Unscheduled market-moving events have no calendar row
  and will never raise a blackout. This is a scheduling gate, not a news filter.
- **The engine trusts the calendar's timestamps.** It validates that they are
  finite numbers and enforces that they are epoch seconds UTC, but it cannot know
  that a vendor placed a release at midnight because it only had a date.
- **`should_cancel_open_limit_orders` is level-triggered**, not edge-triggered: it
  is `True` on every tick of the blackout. Debouncing and cancel idempotency are
  the caller's responsibility.
- **Buffer widths are uncalibrated defaults.** No claim is made — and none was
  found in the sources — about the width of the illiquid window following any
  particular release.
- **No vintage awareness.** The engine enforces no look-ahead *within* a run
  (`actual_release` is unreadable before `release_timestamp_utc`), but it cannot
  detect that the calendar you loaded contains revised consensus figures or a
  schedule that differs from the one that existed at the historical decision time.
- **Release schedules and DST law change.** The 2005 US amendment moved the US
  transition dates, and the EU has an open legislative proposal to discontinue
  seasonal clock changes. Resolve wall-clock times through `zoneinfo` against a
  current tzdata rather than caching offsets.

## Category

`risk-management` — see the top-level `mappings/` directory for how this category rolls up
