# Standards Reference — multi-strategy-capital-allocation-limits

## Engineering defaults

These are the module's defaults, not regulatory prescriptions. Map them to your own mandate.

| Parameter | Default | Description |
|---|---|---|
| Cash Reserve (`cash_reserve_pct`) | 10% of NAV | Minimum uninvested buffer; also the account-level pre-trade ceiling |
| Total Investable | 90% of NAV | `1 - cash_reserve_pct`; enforced on the sum of allocations *and* on committed exposure |
| Max Per-Strategy Allocation | Configurable (e.g. 40%) | Hard cap per strategy, as a fraction of NAV |
| Amount tolerance | $0.01 absolute | Float-noise slack on cap comparisons; deliberately not NAV-proportional |
| Exposure basis | Gross notional, marked to market | Longs and shorts are summed in absolute value, never netted |

## Category

`risk-management` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Jurisdiction-specific. Nothing below is a universal requirement; confirm applicability to your
entity and venues before relying on it.

**United States — SEC Rule 17 CFR § 240.15c3-5 (Market Access Rule).** Mandatory for
broker-dealers with market access; it does not by itself bind an unregistered proprietary
trader, though the control pattern is the industry norm. Paragraph (c)(1)(i) requires controls
reasonably designed to "[p]revent the entry of orders that exceed appropriate pre-set credit or
capital thresholds in the aggregate for each customer and the broker or dealer and, where
appropriate, more finely-tuned by sector, security, or otherwise by rejecting orders if such
orders would exceed the applicable credit or capital thresholds." SEC staff FAQ guidance adds
that where an aggregate limit is implemented as sub-limits, "such individual limits do not
exceed the aggregate credit limit" — the direct analogue of this skill's rule that per-strategy
caps must sum within the account-level investable ceiling. The same guidance permits raising a
threshold intraday, provided it follows supervisory procedures and "[t]he reasons for such
modifications should be documented and retained."
<https://www.law.cornell.edu/cfr/text/17/240.15c3-5>

**EU/EEA — MiFID II RTS 6, Commission Delegated Regulation (EU) 2017/589.** Applies to
investment firms engaged in algorithmic trading (assimilated into UK law post-Brexit).
Article 15 ("Pre-trade controls on order entry") mandates price collars, maximum order values,
maximum order volumes and maximum message limits. Two paragraphs bear directly on this skill:

- Art. 15(2): "An investment firm shall immediately include all orders sent to a trading venue
  into the calculation of the pre-trade limits referred to in paragraph 1." — in-flight orders
  must consume limit capacity, which is why capital is reserved at submission rather than at fill.
- Art. 15(5): "Controls shall be applied, where appropriate, on exposures to individual clients,
  financial instruments, traders, trading desks or the investment firm as a whole." — per-desk
  (here, per-strategy) *and* firm-wide limits, not one or the other.
- Art. 15(4) requires market and credit risk limits "based on its capital base, its clearing
  arrangements, its trading strategy, its risk tolerance", adjusted for changing market impact.
- Art. 15(6) allows submitting an order blocked by pre-trade controls only "in relation to a
  specific trade on a temporary basis and in exceptional circumstances", subject to verification
  by the risk management function and authorisation by a designated individual.
- Art. 17(3) requires the firm to "have the capability to calculate in real time its outstanding
  exposure and that of its traders and clients"; Art. 17(4) adds maximum long, short and overall
  strategy position controls for derivatives.

<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>

**Not covered here.** Reg T / portfolio margin arithmetic, SPAN or cross-margin offsets, and
any venue-specific position limit regime. This module caps notional capital allocation only.
