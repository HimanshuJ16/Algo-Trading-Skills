# Standards — real-time-liquidity-risk-monitoring

## Scope note: market liquidity, not funding liquidity

This skill measures **market (asset) liquidity** — the cost and horizon of unwinding a
position. Basel III's Liquidity Coverage Ratio and Net Stable Funding Ratio govern
**funding liquidity** for banks (high-quality liquid assets against net cash outflows)
and are *not* implemented, referenced, or satisfied by anything here. Version 1.0.0 of
this skill listed "Basel III/IV Liquidity Risk Standards" as its framework; that was a
mis-attribution and has been removed.

## Configuration defaults (calibrate before use)

No regulator, exchange, or standards body publishes a mandatory Days-to-Liquidate cap,
spread-spike ratio, depth-drop threshold, or market-impact coefficient for a trading
book. Every value below is a **library default**, chosen to be legible, not authoritative.
Calibrate each against the desk's mandate, instrument liquidity tier, and realized
transaction costs, and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_dtl_threshold_days` | $2.0$ days | Alert when $\text{DTL} \ge$ this. Inclusive: exactly at the limit is a breach. |
| `max_participation_pct` | $0.10$ | Fraction of ADV assumed tradable per session. Sets the DTL denominator; must be in $(0, 1]$. |
| `spread_spike_threshold_ratio` | $2.0\times$ | Alert when $\text{Spread}/\text{NormalSpread} \ge$ this. |
| `depth_drop_threshold_pct` | $0.50$ | Alert when $1 - \text{Depth}/\text{NormalDepth} \ge$ this. |
| `market_impact_coeff_per_day` | $0.10$ | $k$ in the COL impact term. **Uncalibrated placeholder** — see below. |

## Verified formula anchors

### Liquidity-Adjusted VaR — Bangia, Diebold, Schuermann & Stroughair (1999)

Source: *Modeling Liquidity Risk, With Implications for Traditional Market Risk
Measurement and Management*, Wharton Financial Institutions Center Working Paper 99-06
([SSRN abstract](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1298788)).

| Fact | Location |
|---|---|
| Exogenous cost of liquidity $\text{COL} = \tfrac{1}{2} P_t (\bar{S} + a\tilde{\sigma})$ | Eq. 4 |
| Relative spread defined as $(\text{Ask} - \text{Bid}) / \text{Mid}$; $\tilde{\sigma}$ is the volatility of the relative spread | Text following Eq. 4 |
| Empirical tail scaler $a$ ranges from $2.0$ to $4.5$ depending on instrument and market; spread distributions are far from normal, so any such scaling is an approximation | Sec. III.B |
| Liquidity-adjusted VaR is additive: $\text{L-VaR} = P_t(1 - e^{-2.33\sigma}) + \tfrac{1}{2}P_t(\bar{S} + a\tilde{\sigma})$ | Eq. 5b |
| The additive form assumes extreme return moves and extreme spread moves occur concurrently | Sec. III.B, preceding Eq. 5 |
| The paper models **exogenous** liquidity only; endogenous (position-size) illiquidity is explicitly out of its scope | Sec. I–II |

**What this implementation does and does not take from BDSS.** The half-spread term is
Eq. 4 with $a = 0$, evaluated on the *current snapshot* spread rather than on
$\bar{S} + a\tilde{\sigma}$. It is therefore a mean-condition cost, not the 99%-coverage
cost the paper targets, and understates tail exogenous liquidity risk. Supplying the
tail term would require a spread-volatility time series per symbol, which this engine
does not ingest.

### Market impact — the $k \cdot \text{DTL}$ term is not a published result

The endogenous term $\tfrac{1}{2} \cdot \text{Notional} \cdot k \cdot \text{DTL}$ has no
regulatory or academic provenance. It is a linear-in-horizon proxy. The relevant
empirical finding is that impact is *concave* in trade rate: Almgren, Thum, Hauptmann &
Li (2005), *Direct Estimation of Equity Market Impact*
([paper](https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf)) fit a $3/5$ power law
to Citigroup US equity desk data and explicitly reject the square-root exponent for
temporary impact. A single per-day coefficient cannot reproduce that across order sizes.
Consequences to hold in mind:

- At $k = 0.10$/day, a $5$-day DTL charges $25\%$ of notional. That is implausible for a
  liquid large-cap and will dominate the half-spread term by two orders of magnitude.
- Setting $k = 0$ is a defensible configuration: it reduces COL to the BDSS exogenous
  term, which at least has a published basis.
- `spread_cost_usd` and `impact_cost_usd` are reported separately precisely so a reviewer
  can see how much of an L-VaR figure rests on the uncalibrated half.

## Regulatory context (informative, not implemented)

These are the closest supervisory analogues to a days-to-liquidate metric. None of them
is satisfied by running this engine, and none prescribes its thresholds.

| Regime | What it actually says |
|---|---|
| **US SEC Rule 22e-4** (17 CFR 270.22e-4), open-end funds | Requires classification of portfolio investments by the number of days to convert to cash in current market conditions without significantly changing market value: *highly liquid* = 3 business days or less; *moderately liquid* = more than 3 but 7 calendar days or less; *illiquid* = cannot be sold or disposed of in 7 calendar days or less without significantly changing market value. Paragraph (b)(1)(iv) caps illiquid investments at 15% of net assets. Applies to registered open-end funds — **not** to proprietary trading books. ([17 CFR 270.22e-4](https://www.law.cornell.edu/cfr/text/17/270.22e-4)) |
| **Basel FRTB internal models approach** (BCBS d457, Jan 2019, MAR33.12, Table 2) | Assigns each risk factor a *liquidity horizon* $n$ from the set $\{10, 20, 40, 60, 120\}$ calendar days — e.g. large-cap equity price $10$, small-cap equity price $20$, credit-spread volatility $120$. Horizons are a floor that may be raised desk-by-desk with documented rationale and supervisory approval, capped at instrument maturity. Applies to bank trading-book capital under IMA. ([BCBS d457](https://www.bis.org/bcbs/publ/d457.pdf)) |
| **Basel III LCR / NSFR** | Funding liquidity for banks. Out of scope here; listed only to mark what this skill is *not*. |

## Known limitations

- **Snapshot-only.** One observation per symbol; no timestamp, no staleness detection,
  no smoothing. Data recency is the caller's responsibility.
- **ADV is an input.** The engine cannot distinguish a stressed ADV from a stale one,
  and DTL scales inversely with it.
- **Constant participation.** DTL assumes the cap is achievable every session — no
  partial fills, halts, limit states, or auction-only sessions are modelled.
- **No cross-symbol correlation.** Crowding, where every holder unwinds the same names
  at once, is out of scope.
- **Monitor, not control.** The engine reports; it never blocks, resizes, or cancels.
