# Standards for Daylight Saving Time Transition Handling

## Statutory transition rules

| Jurisdiction | Instrument | Start of DST / summer time | End | Reference time |
|---|---|---|---|---|
| United States | 15 U.S.C. § 260a (as amended by Pub. L. 109-58, Energy Policy Act of 2005) | 02:00 on the **2nd Sunday of March** | 02:00 on the **1st Sunday of November** | **Local** time in each zone — US zones therefore transition an hour apart from one another |
| European Union | Directive 2000/84/EC, Arts. 2–3 | 01:00 on the **last Sunday of March** | 01:00 on the **last Sunday of October** | **GMT** — every Member State transitions at the same instant |
| United Kingdom | Summer Time Order 2002 (SI 2002/262) | last Sunday of March | last Sunday of October | 01:00 GMT — unchanged post-Brexit, so `Europe/London` stays aligned with the EU dates |

Both instruments are in force as of this writing. Neither is stable long-term (see *Rule volatility* below), which is why the rules above are documentation only — the implementation resolves every offset from the IANA tz database and hard-codes no dates.

## Desynchronization window lengths

Derived arithmetically from the two rules above; verified against the 2020–2035 calendar.

| Window | Span | Length |
|---|---|---|
| Spring (US on DST, EU not) | 2nd Sunday of March → last Sunday of March | **14 days**, or **21 days** when 1 March falls on a Sunday (2020, 2024, 2025, 2026, 2030, 2031, …) |
| Autumn (US still on DST, EU not) | last Sunday of October → 1st Sunday of November | **always exactly 7 days** |

A fixed "two-week window" constant is incorrect for every autumn window and for roughly one spring window in three. Detect the window from the two zones' live DST states.

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| UTC invariant | ALL internal market data timestamps and execution logs MUST be stored as UTC nanosecond epochs. Local wall time is a presentation format, never a storage or join key. |
| IANA database | Offsets MUST be resolved dynamically from the IANA tz database at the instant required. Fixed offsets and cached per-day offsets are both prohibited — a cached offset is wrong for any session containing a transition. |
| Boundary-level resolution | Session open and close MUST each resolve their own offset. A session's elapsed length MUST be derived from the UTC epochs, not from the nominal local-clock span. |
| Skipped / repeated wall times | A local session boundary that is non-existent (spring forward) or ambiguous (fall back) MUST be detected and either rejected or explicitly resolved. Silent `fold=0` resolution is not acceptable for an unattended scheduler. |
| Cross-border desync detection | Algorithms spanning US and EU markets MUST audit the March and October/November desynchronization windows, and MUST verify that the audit resolved both legs rather than trusting a bare `False`. |
| tz database currency | The `tzdata` version MUST be pinned for reproducible backtests and refreshed on a defined cadence for live trading. Windows hosts and slim containers have no system tz database and require the `tzdata` package explicitly. |

## Rule volatility

DST rules are political and change with little lead time; both major jurisdictions have live proposals:

- **EU** — the Commission's 2018 proposal to discontinue seasonal clock changes was endorsed by Parliament in 2019 but has been stalled in the Council since, with no agreement on which permanent time to adopt. Directive 2000/84/EC remains in force.
- **US** — the Sunshine Protection Act (H.R. 139, 119th Congress) passed the House on 14 July 2026 by 308–117 but had not been passed by the Senate or enacted as of August 2026, so 15 U.S.C. § 260a still governs. *Verify the current status before relying on this.*

Neither status is encoded in the implementation. Both are reasons to treat the tz database — not any constant in this repository — as the source of truth.

## Sources

- 15 U.S.C. § 260a — <https://www.law.cornell.edu/uscode/text/15/260a>
- Directive 2000/84/EC on summer-time arrangements — <https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32000L0084>
- EU legislative status, discontinuing seasonal changes of time — <https://www.europarl.europa.eu/legislative-train/theme-union-of-democratic-change/file-discontinuing-seasonal-changes-of-time>
- H.R. 139, Sunshine Protection Act of 2025 — <https://www.congress.gov/bill/119th-congress/house-bill/139>
- PEP 495, Local Time Disambiguation (`fold`) — <https://peps.python.org/pep-0495/>
- IANA Time Zone Database — <https://www.iana.org/time-zones>
