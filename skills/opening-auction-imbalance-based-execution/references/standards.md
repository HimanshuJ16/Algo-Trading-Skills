# Standards for Opening Auction Imbalance-Based Execution

All times are US/Eastern. The cross is at 09:30:00; the "seconds to open" column
is the same deadline expressed the way `AuctionImbalanceData.seconds_to_open`
measures it.

## Venue-mandated deadlines

These are exchange rules. Violating them gets the order rejected or leaves it
un-cancellable; they are not tunable strategy parameters.

### Nasdaq Opening Cross

| Item | Rule | Seconds to open |
|---|---|---|
| Imbalance dissemination begins | 09:25, every 10 seconds | 300 |
| Near/Far Indicative Clearing Price first published | 09:28, then every second | 120 |
| MOO entry cutoff (must be *received* before) | 09:28 | 120 |
| LOO entry cutoff | 09:29:30 | 30 |
| Late-LOO re-pricing window | 09:28 – 09:29:30 | 120 – 30 |
| OIO entry cutoff | until the cross executes | 0 |
| Cancel/modify of on-open orders frozen | 09:25 | 300 |

Between 09:25 and 09:28 the disseminated message carries only the Current
Reference Price, Paired Shares, Imbalance Shares and Imbalance Direction. The
Near and Far Indicative Clearing Prices are added from 09:28 — SEC Release
34-91527 approving SR-NASDAQ-2021-004 states they are excluded from the earlier
dissemination deliberately, to limit large indicative price movements. Read the
ITCH price fields for those two prices as *absent*, not as zero, before 09:28.

An LOO entered between 09:28 and 09:29:30 is accepted at its limit price unless
that limit is more aggressive than the 09:28 Current Reference Price or the
prior day's Nasdaq Official Closing Price, in which case the venue re-prices it
to the more aggressive of the two and converts a DAY order to IOC. An LOO with
a TIF other than IOC received after 09:29:30 is treated as an Imbalance-Only
order.

Sources:
- Nasdaq Equity 4, Rules 4702(b)(8)–(10) (MOO, LOO, OIO) and 4752 (Opening
  Cross / Order Imbalance Indicator).
- SEC Release No. 34-91527, *Order Approving Proposed Rule Change, as Modified
  by Amendment No. 2, To Disseminate Abbreviated Order Imbalance Information for
  the Nasdaq Opening Cross…* (SR-NASDAQ-2021-004), 86 FR 18349, 8 April 2021;
  implemented 26 April 2021.
  https://www.federalregister.gov/documents/2021/04/08/2021-07197/
- *The Nasdaq Opening and Closing Crosses* FAQ, 2025 — Q7, Q8, Q17, Q25.
  https://nasdaqtrader.com/content/productsservices/trading/crosses/openclose_faqs.pdf
- Nasdaq TotalView-ITCH 5.0 specification, section 1.6 (NOII message).
  https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf

### NYSE Core Open Auction

| Item | Rule | Seconds to open |
|---|---|---|
| Imbalance publication begins | 08:00, every second when changed | 5400 |
| MOO / LOO entry | until the DMM opens the security | 0 |
| Cancel / cancel-and-replace of MOO and LOO rejected | 09:29 | 60 |
| Core Open Auction Imbalance Freeze | 09:29:55 – 09:30 | 5 |
| Opening Imbalance Only order type | not offered | — |

Sources:
- NYSE Rule 7.31(c)(1) (Auction-Only Orders: LOO, MOO, Opening D Orders) and the
  Rule 7.35 series (Rule 7.35A, DMM-facilitated Core Open Auction).
- NYSE trading information / opening auction timetable, 2026.
  https://www.nyse.com/markets/nyse/trading-info

## ITCH 5.0 NOII field semantics

| Field | Codes / meaning |
|---|---|
| Imbalance Direction | `B` buy imbalance, `S` sell imbalance, `N` no imbalance, `O` insufficient orders to calculate, `P` paused (added 28 April 2023) |
| Cross Type | `O` Opening Cross, `C` Closing Cross, `H` IPO / halted-paused cross, `A` Extended Trading Close |
| Far Price | hypothetical auction-clearing price for cross orders only |
| Near Price | hypothetical auction-clearing price for cross plus continuous orders |
| Current Reference Price | the price at which the NOII shares are calculated |

Only `B` and `S` are tradable sides. Only Cross Type `O` is in scope for this
skill.

Source: Nasdaq TotalView-ITCH 5.0 specification, section 1.6 and Appendix A.

## Strategy parameters (not venue requirements)

The defaults below are this skill's engineering choices. Calibrate them against
your own execution data; no exchange mandates any of them.

| Parameter | Default | Rationale |
|---|---|---|
| `imbalance_ratio_threshold` | 0.20 | Screens noise imbalances; not a venue rule. |
| `min_imbalance_qty` | 10,000 | Floor below which the imbalance is not worth an auction order. |
| `participation_pct` | 0.10 | Fraction of the published imbalance to absorb. |
| `max_pct_of_auction_volume` | 0.05 | Caps footprint against total auction interest. |
| `entry_safety_buffer_seconds` | 5.0 | Must be measured, not assumed — it is your own strategy-to-exchange latency. |
| `max_feed_age_seconds` | 15.0 | Sized above the 10-second pre-09:28 Nasdaq cadence. |
| `allow_unpriced_moo` | `False` | An MOO has no price protection at the cross. |

## Currency note

Exchange auction rules change. The Nasdaq cutoffs above reflect
SR-NASDAQ-2021-004 as implemented on 26 April 2021; secondary renderings of the
Nasdaq rulebook still circulate showing the pre-2021 09:28 cancel/modify freeze.
Re-confirm the deadlines in `VENUE_RULES` against the current rulebook before
going live, and treat the values in code as configuration, not as constants.
