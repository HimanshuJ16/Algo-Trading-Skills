# Standards — liquidity-adjusted-position-sizing

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards and **not** regulatory
limits. No general rule obliges a proprietary trader or fund to trade at $10\%$ of ADV
or to hold Days-to-Liquidate below one session. The right values depend on the
mandate, the redemption or margin-call horizon the book must survive, and the firm's
risk appetite. Calibrate each and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| Max participation $\alpha$ | $10.0\%$ of ADV | Share of a session's volume the policy will consume. Sets the daily liquidation capacity. |
| Max Days-to-Liquidate $DTL_{\text{max}}$ | $1.0$ session | Sessions allowed to reach flat at $\alpha$. Together with $\alpha$ this is the real control: $\text{MaxShares} = (\alpha/100) \times \text{ADV} \times DTL_{\text{max}}$, a cap on size relative to ADV. |
| Book depth multiple $m$ | $1.0\times$ | Optional second ceiling, applied only when a depth snapshot is supplied. No external basis for the value — calibrate from your own execution data. |
| ADV window | $20$ trading days | Matches the averaging convention of the EU/UK buy-back safe harbour below. |

Raising $DTL_{\text{max}}$ relaxes the size cap **linearly**. It does not make a larger
position cheaper to trade (see the impact section), only slower to exit.

## What is actually regulator-set

### Participation rate — EU/UK, buy-back programmes only

Commission Delegated Regulation (EU) 2016/1052, **Article 3(3)** (RTS under the Market
Abuse Regulation, Regulation (EU) No 596/2014, Art. 5): an issuer executing under a
buy-back programme must not purchase on any trading day more than **25% of the average
daily volume** of the share on the venue where the purchase is carried out, with the
ADV calculated over the **20 trading days preceding the date of the purchase**. Where
liquidity is extremely low the limit may reach **50%**, subject to the conditions and
notification in that Article.

- **Applicability:** EEA/UK issuers buying back their own shares, as a condition of the
  market-abuse safe harbour. It does **not** apply to ordinary strategy execution, and
  it is a ceiling for a programme explicitly *not* trying to move the price — not a
  target for one that is.
- **Currentness:** the $25\%$ / 20-day figures are those of the regulation as in force.
  ESMA published a report proposing amendments to Delegated Regulation 2016/1052 on
  **27 February 2026**, submitted to the European Commission for adoption; re-check
  Article 3 before relying on the figures for a live buy-back mandate.
- Source: [Delegated Regulation (EU) 2016/1052](https://eur-lex.europa.eu/eli/reg_del/2016/1052/oj/eng);
  restated in [Euronext, *Guidelines for buy-back programmes and price stabilisation*](https://www.euronext.com/sites/default/files/2021-03/Notice%20-%20Guidelines%20for%20buy-back%20programmes%20and%20price%20stabilisation_0.pdf);
  [ESMA74-268544963-1569 (27 Feb 2026)](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-268544963-1569_Report_on_the_amendments_to_Commission_Delegated_Regulation_20161052_on_buy-back_programmes_and_stabilisation_measures.pdf).

### Days-to-liquidate buckets — US registered open-end funds only

SEC **Rule 22e-4** (17 CFR 270.22e-4), the Liquidity Rule under the Investment Company
Act of 1940, defines liquidity by conversion horizon at a size the fund would actually
trade:

| Term | Rule text |
|---|---|
| Highly liquid investment | convertible to cash "in current market conditions in three business days or less without the conversion to cash significantly changing the market value of the investment" |
| Moderately liquid | "more than three calendar days but in seven calendar days or less", same value condition |
| Less liquid | sellable "in seven calendar days or less" without significantly changing market value, but settling in more than seven calendar days |
| Illiquid investment | "cannot be sold or disposed of in current market conditions in seven calendar days or less without the sale or disposition significantly changing the market value" |

Classification must account for size: the fund "must determine whether trading varying
portions of a position in a particular portfolio investment or asset class, in sizes
that the fund would reasonably anticipate trading, is reasonably expected to
significantly affect its liquidity". Classifications are reviewed **at least monthly**
in connection with Form N-PORT reporting, and no fund or In-Kind ETF "may acquire any
illiquid investment if, immediately after the acquisition, the fund or In-Kind ETF
would have invested more than **15% of its net assets** in illiquid investments".

- **Applicability:** US registered open-end management investment companies and In-Kind
  ETFs. Money market funds and ETFs meeting the rule's conditions are treated
  separately. It does **not** bind hedge funds, proprietary traders, or individuals —
  it is cited here as the reference definition of a days-to-liquidate bucket and as
  authority for the principle that a liquidity classification is a function of size.
- Source: [17 CFR § 270.22e-4](https://www.law.cornell.edu/cfr/text/17/270.22e-4).

### The cap must be a hard pre-trade block — EU/UK algorithmic trading

Commission Delegated Regulation (EU) 2017/589 (**RTS 6**), Article 15, requires
investment firms engaged in algorithmic trading to operate pre-trade controls including
price collars, **maximum order values** and **maximum order volumes**, and message
limits, blocking non-compliant orders rather than merely alerting on them. A sizer that
returns a position derived from a NaN or a misconfigured limit has not blocked
anything, which is why this implementation raises instead of sizing when its inputs or
its configuration are invalid. See also `sec-rule-15c3-5-risk-controls-us` and
`mifid-ii-algo-trading-compliance-eu`.

## Why the size cap, and not the participation rate, is the impact control

The price impact of a metaorder empirically follows a **square-root law** in total size
relative to daily volume, $I \sim \sigma\sqrt{Q/V}$, and to a first approximation it is
insensitive to the number of child orders and to the total time taken to execute
(Tóth, Lempérière, Deremble, de Lataillade, Kockelkoren and Bouchaud, *Anomalous price
impact and the critical nature of liquidity in financial markets*, Phys. Rev. X **1**,
021006, 2011, [arXiv:1105.1694](https://arxiv.org/abs/1105.1694)). Later work fits weak
separate dependences on participation rate and duration — exponents of roughly $0.52$
and $0.54$ — and proposes logarithmic alternatives (Zarinelli et al. 2015,
[arXiv:1412.2152](https://arxiv.org/pdf/1412.2152)), so the independence is an
approximation rather than a law; the direction of the conclusion is unchanged.

Two consequences for sizing:

1. **Slowing down is not a discount.** Halving $\alpha$ and doubling the horizon leaves
   $Q/V$ unchanged, so first-order impact is unchanged while timing risk grows. Widen
   $DTL_{\text{max}}$ only when a longer liquidation horizon is genuinely acceptable.
2. **Cost is superlinear in size.** Under the square-root law the *total* cost of a
   metaorder scales roughly as $Q^{3/2}/\sqrt{V}$. Doubling a position more than
   doubles the cost of getting out of it, which is the case for a hard cap on $Q$
   rather than a soft preference for smaller orders.

## Known limitations

- **ADV is supplied, not validated.** A 20-day mean spanning a holiday stretch, an
  expiry, or one index-rebalance print overstates continuously available volume. The
  sizer cannot detect an optimistic ADV; feed a stressed one when the exit being sized
  is a stressed exit. See `portfolio-stress-test-including-liquidity-crunch-scenarios`.
- **Displayed depth is a snapshot.** It can be pulled between the snapshot and the
  order, and it excludes hidden and dark liquidity. It tightens the ADV cap; it does
  not replace it.
- **Per-instrument.** Correlation and crowding are out of scope: several independently
  capped positions in the same factor still exit through one door.
- **Not a cost model.** The engine returns a size, never a predicted impact or cost.

## Category

`risk-management`
