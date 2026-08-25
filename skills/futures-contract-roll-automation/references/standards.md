# Standards — futures-contract-roll-automation

## Configuration defaults (calibrate before use)

No regulator, exchange, or standards body mandates *when* a position must be rolled.
The values below are this library's defaults; the only hard deadlines are the
exchange-published ones in the next section. Calibrate each per product and record
the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `min_days_to_expiration` | 5 business days | Rolls when the front contract is this close to Last Trading Day. Chosen to sit inside the liquid part of the roll window, not to satisfy any rule. |
| `min_days_to_first_notice` | 2 business days | Rolls when a **physically delivered** contract is this close to First Notice Day. |
| `enable_volume_crossover` | `True` | Trigger on $V_{\text{next}} > V_{\text{front}}$ (strictly greater; equal volumes are not a crossover). |
| `enable_open_interest_crossover` | `True` | Trigger on $OI_{\text{next}} > OI_{\text{front}}$. |
| `spread_quoting_convention` | `NEARBY_MINUS_DEFERRED` | Which side of the combination instrument performs the roll, and the sign of the quoted price. Product-specific — see below. |
| `spread_price_decimals` | `None` (no rounding) | Round to the spread instrument's own tick precision if you round at all. A fixed 4-decimal round mis-quotes a 5-decimal FX futures spread by over a tick. |

## Exchange-published facts

| Fact | Source |
|---|---|
| The standard calendar spread carries CME Globex strategy code **`SP`**; it is "the simultaneous purchase (sale) of one product with a nearby expiration and a sale (purchase) of the same product at a deferred expiration." Listing convention is nearby first, deferred second, "creating a differential spread of nearby expiration minus the deferred expiration." Calendar spread *variations* exist per product, each with its own spread type code. | [CME Group — Spreads and Combinations Available on CME Globex](https://www.cmegroup.com/confluence/display/EPICSANDBOX/Spreads+and+Combinations+Available+on+CME+Globex) |
| **CME FX** futures calendar spreads are quoted as **far expiry minus near expiry**; buying the spread is a simultaneous purchase of the far expiry and sale of the near expiry. | [CME Group — FAQ: CME FX Futures Calendar Spreads](https://www.cmegroup.com/articles/faqs/frequently-asked-questions-cme-fx-futures-calendar-spreads.html) |
| **CBOT Treasury** roll: a participant long the expiring front month re-establishes the position in the deferred month by **selling** the calendar spread (selling the front, buying the deferred); a short does the reverse by buying it. | [CME Group — Treasury Futures Calendar Spreads](https://www.cmegroup.com/trading/interest-rates/files/treasury-futures-calendar-spreads.pdf) |
| **CME Equity Index** calendar spreads are priced as the index-point difference of the deferred contract over the nearby contract; in buying the roll the participant purchases the deferred and sells the nearby. | [CME Group — Rolling an Equity Position Using Spreads](https://www.cmegroup.com/education/courses/introduction-to-equity-index-products/rolling-an-equity-position-using-spreads) |
| **E-mini S&P 500 (ES)**: $50 × index, **cash settled** to the S&P 500 Special Opening Quotation on the third Friday of the contract month — there is no First Notice Day. | [CME Group — E-mini S&P 500 Contract Specs](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.contractSpecs.html) |
| **CBOT Corn (ZC)**: 5,000 bushels, physically delivered. Last trading day is the business day preceding the 15th calendar day of the delivery month. **First Notice Day is the last business day of the month preceding the delivery month** — e.g. December 2026 corn has FND 30 Nov 2026 against LTD 14 Dec 2026. | [CME Group — CBOT Rulebook Chapter 10, Corn Futures](https://www.cmegroup.com/rulebook/CBOT/I/10.pdf); [CME Group — Corn Contract Specs](https://www.cmegroup.com/markets/agriculture/grains/corn.contractSpecs.html) |
| On First Notice Day the short holder of a physically settled contract may tender a delivery notice; the clearing house matches shorts to longs, and the matched long receives an invoice. The long does not control the timing. | [CME Group — Understanding the Grain Delivery Process](https://www.cmegroup.com/education/courses/introduction-to-agriculture/grains-oilseeds/understanding-the-grain-delivery-process) |

**Consequence encoded in the engine**: because FND precedes LTD for many physically
delivered products, `days_to_expiration` cannot bound delivery risk on its own. The
engine therefore requires `days_to_first_notice` whenever `is_physically_delivered`
is set, and refuses to guess.

**Consequence for order construction**: since the quoting convention differs across
CME product lines, the side of the combination instrument that performs a roll is
configuration, not a constant. Getting it backwards doubles the position instead of
rolling it.

## Known limitations

- **Stateless and single-session.** The engine evaluates the snapshot it is handed.
  Multi-session confirmation of a volume/OI crossover — which materially reduces
  false triggers around the roll window — is the caller's responsibility.
- **No exchange calendar.** Business-day counts are inputs, not computations. The
  engine cannot detect that a caller supplied calendar days, nor apply holiday
  calendars; see `global-exchange-holiday-calendar-handling`.
- **`estimated_roll_cost` is a basis estimate only.** It excludes commissions,
  exchange and clearing fees, and the cost of crossing the spread's own bid/ask.
- **`spread_symbol` is a label.** Tradable combination instrument IDs come from the
  venue's security definition, not from concatenation.
- **Delivery specifications change.** Contract terms are amended by rule filing;
  re-verify FND and LTD definitions against the current rulebook chapter rather than
  caching them.

## Category

`execution-algorithms`
