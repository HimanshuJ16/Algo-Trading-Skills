# Standards — portfolio-stress-test-including-liquidity-crunch-scenarios

## Configuration defaults (calibrate before use)

None of these is a standard, an industry convention, or a regulatory limit. They are
this library's defaults, chosen so the engine runs out of the box. A default that has
never been questioned is a scenario nobody chose.

| Parameter | Default | What it actually does |
|---|---|---|
| `daily_participation_rate` $\alpha$ | $0.10$ | Share of a stressed session's volume the liquidation is assumed to consume. Sets daily capacity, and therefore $DTL$. |
| `max_allowed_dtl_days` | $5.0$ | Sessions to flat above which a position is flagged a bottleneck. Strictly greater than; exactly at the limit passes. |
| `impact_coefficient_y` $Y$ | $1.0$ | The constant in the square-root impact law, which Tóth et al. describe as "of order unity". |
| `liquidity_drop_pct` | $0.50$ | Scenario haircut to executable **capacity**. Not a forecast of tape volume. |
| `spread_expansion_factor` | $5.0$ | Scenario multiplier on the normal-conditions bid-ask spread. |
| `IMPACT_CALIBRATION_MAX_PHI` | $0.10$ | Above this volume fraction the impact figure is reported as an extrapolation. This library's reading of "a few %"; not a value stated in the source paper. |

Calibrate `max_allowed_dtl_days` against the horizon the book must actually survive —
the margin-call cycle, the redemption terms, the funding line — not against this number.

## What is actually regulator-set, and for whom

### EU — liquidity stress testing for fund managers

**ESMA Guidelines on liquidity stress testing in UCITS and AIFs**, ESMA34-39-897 (final
report ESMA34-39-882, 2 September 2019). Para. 8: "These Guidelines apply from
30 September 2020." Para. 5: they apply to UCITS and AIFs, including ETFs operating as
either and leveraged closed-ended AIFs.

The two guidelines this engine implements directly:

- **Para. 43** — "Liquidation cost and time to liquidity are the two principal
  approaches typically employed by managers to simulate asset liquidity under normal and
  stressed conditions." The engine reports both, per position and in aggregate.
- **Para. 44** — "Liquidation cost depends on asset type, liquidation horizon and the
  size of the trade/order. Managers should consider these three factors when assessing
  liquidation cost of their assets under normal and stressed conditions." Size enters
  through $\phi = |Q| / \text{StressedADV}$; horizon through $DTL$; asset type through
  the per-position spread and volatility inputs.
- **Para. 45** — stressed conditions are "typically characterised by higher volatility,
  lower liquidity (e.g. higher bid-ask spread) and longer time to liquidate (depending on
  asset class)", and "managers should not only refer to historical observations of
  stressed markets." This is why the scenario is an input, not something the engine
  derives from history.
- **Para. 46** — a manager "should also be aware of the method's limitations and make
  conservative adjustments". The module's documented limitations exist to be read, not
  to be assumed away.

- **Applicability:** EU UCITS management companies and AIFMs. These guidelines do **not**
  bind proprietary trading firms, hedge funds outside AIFMD scope, or individuals. They
  are cited here as the reference framework for what a liquidity stress test must
  measure, not as a constraint on every user of this skill.
- Source: [ESMA34-39-897](https://www.esma.europa.eu/sites/default/files/library/esma34-39-897_guidelines_on_liquidity_stress_testing_in_ucits_and_aifs_en.pdf);
  [final report ESMA34-39-882](https://www.esma.europa.eu/sites/default/files/library/esma34-39-882_final_report_guidelines_on_lst_in_ucits_and_aifs.pdf).

### US — days-to-liquidate buckets for registered open-end funds

**SEC Rule 22e-4** (17 CFR 270.22e-4), the Liquidity Rule under the Investment Company
Act of 1940, classifies each portfolio investment by how long it takes to convert to cash
at a size the fund would actually trade: **highly liquid** is three business days or
less; **illiquid** is an investment the fund "reasonably expects cannot be sold or
disposed of in current market conditions in seven calendar days or less without the sale
or disposition significantly changing the market value of the investment". A fund may not
acquire further illiquid investments once they exceed **15% of net assets**.
Classifications are reviewed at least monthly.

- **Applicability:** US registered open-end management investment companies and In-Kind
  ETFs; money market funds are excluded. It does **not** bind proprietary traders or
  individuals.
- **Why it matters here:** it is the closest thing to an authoritative definition of a
  days-to-liquidate bucket, and it establishes the principle this engine is built on —
  that a liquidity classification is a function of *size*, not of the instrument alone.
  Note its illiquid boundary is seven calendar days, while this library's default flag is
  a tighter five sessions.
- Source: [17 CFR § 270.22e-4](https://www.law.cornell.edu/cfr/text/17/270.22e-4);
  [SEC compliance guide](https://www.sec.gov/resources-small-businesses/small-business-compliance-guides/investment-company-liquidity-risk-management-program-rules).

## The cost model and its provenance

### Exogenous cost — half the spread, once

Bangia, Diebold, Schuermann and Stroughair, *Modeling Liquidity Risk with Implications
for Traditional Market Risk Measurement and Management* (Wharton Financial Institutions
Center working paper 99-06, 1999) add a cost of liquidity to VaR:

$$\text{COL} = \tfrac{1}{2} P \left( \mu_S + z_\alpha \sigma_S \right)$$

where $\mu_S$ and $\sigma_S$ are the mean and standard deviation of the *proportional*
bid-ask spread. The factor of $\tfrac{1}{2}$ is the point: a liquidation crosses from the
mid to the bid once. This engine uses the deterministic part,
$\tfrac{1}{2} P \cdot \text{spread}$, with the stress carried by
`spread_expansion_factor`; it does not model the spread's own distribution, so the
$z_\alpha \sigma_S$ term is omitted rather than approximated.

**It is charged once per share, not once per share per session.** Slicing a position
across $DTL$ days does not make each share pay the spread $DTL$ times. Version 1.0.0 of
this skill charged the full spread on the full position value for each of up to ten days,
overstating this component by up to $20\times$ — and by more precisely on the illiquid
positions the report exists to flag.

- Source: [SSRN 1298788](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1298788).

### Endogenous cost — the square-root law of market impact

Tóth, Lempérière, Deremble, de Lataillade, Kockelkoren and Bouchaud, *Anomalous price
impact and the critical nature of liquidity in financial markets*, Phys. Rev. X **1**,
021006 (2011), Eq. (1):

$$\Delta(Q) = Y \sigma \sqrt{\frac{Q}{V}}$$

where $\sigma$ is the asset's daily volatility and $V$ its daily traded volume, "both
quantities measured contemporaneously to the trade. The numerical constant $Y$ is of
order unity." In Fig. 1 of that paper $\Delta$ is measured as the *average execution
shortfall* of the metaorder, which is why it can be used directly as a cost fraction
rather than as a peak-impact figure needing a further adjustment.

**Fitted range.** The paper's own data — nearly 500,000 futures trades, June 2007 to
December 2010 — covers "$Q/V$ ranging from a few $10^{-4}$ to a few %", with fitted
exponents $\delta \approx 0.5$ for small-tick and $\approx 0.6$ for large-tick
contracts. Related studies put $\delta$ in the range $0.4$ to $0.7$. A stressed portfolio
routinely implies $\phi \ge 1$, one to two orders of magnitude beyond that range, which
is why positions above `IMPACT_CALIBRATION_MAX_PHI` are reported separately in
`positions_outside_impact_calibration`. Their impact figure is a flag that the position
is untradeable on the assumed horizon, not a cost estimate.

- Source: [Phys. Rev. X 1, 021006](https://link.aps.org/doi/10.1103/PhysRevX.1.021006);
  [arXiv:1105.1694](https://arxiv.org/abs/1105.1694).

## Why the crunch is a capacity haircut, not a volume forecast

`liquidity_drop_pct` haircuts ADV, but a crash is not generally an episode of falling
volume. The FSB's *Holistic Review of the March Market Turmoil* (17 November 2020)
records "the surge in trading activity" and notes that venues "were able to handle record
trading volumes". What failed was depth: on 16 March 2020, "market depth in several asset
classes (including US equities and Treasuries) declined to levels seen during the worst
period of the 2008 financial crisis. This was accompanied by a large increase in
transaction costs in many inter-dealer markets" (p. 8). Its footnote 2 cites the IMF's
Global Markets Monitor: 10-year US Treasury market depth "declined 93% from the February
average to its lowest level in history and 30-year market depth dropped 76% from its
February average; also the lowest in history."

So the haircut represents the collapse in size absorbable *at a tolerable price*.
Calibrating it from an observed decline in tape volume understates the crunch, because in
the episodes this skill exists to model the tape got busier while the book got thinner.

- Source: [FSB, *Holistic Review of the March Market Turmoil*](https://www.fsb.org/uploads/P171120-2.pdf).

## Known limitations

- **Single-period, single-scenario.** One shock vector applied at one instant. No path,
  no multi-day drawdown accumulation, no re-shocking of the residual book as it is worked
  off.
- **No cross-asset correlation or crowding feedback.** Shocks are independent per symbol.
  Margin spirals, forced-seller cascades and the shared exit door are out of scope — see
  `tail-correlation-between-strategies-under-stress`.
- **Netting is assumed economically real.** `price_shock_loss` nets longs against shorts;
  that is only meaningful if the legs were shocked consistently.
- **Impact requires a volatility input.** Without `daily_volatility` the impact term is
  zero and the position is listed in `positions_missing_volatility`; the haircut is then
  an explicit lower bound.
- **No funding, margin, or settlement dimension.** The engine says nothing about whether
  the cash arrives in time.
