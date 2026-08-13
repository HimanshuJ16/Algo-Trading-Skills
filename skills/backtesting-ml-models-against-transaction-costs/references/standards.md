# ML Transaction Cost Standards

These are modelling conventions and empirical findings, **not** regulatory requirements.
No regulator known to this skill prescribes a backtest transaction-cost model.

The nearest regulatory touchpoint, for EU MiFID II investment firms only, is
Article 5 of RTS 6: before deploying or substantially updating a trading algorithm, a
firm must establish clearly delineated development and testing methodologies, adapt
those methodologies to the venues and markets where the algorithm will be deployed,
and have the deployment authorised by a person designated by senior management. The
requirement is about having and documenting a testing methodology — it says nothing
about cost assumptions. Treat a documented, calibrated cost model as evidence that
your methodology was fit for the venue, not as a compliance checkbox in itself.
*Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 5,
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. Applies to EU investment
firms engaged in algorithmic trading; the testing articles apply to algorithms that
lead to order execution. Verify current applicability for your jurisdiction — UK,
US, and APAC regimes differ.*

## Cost conventions

| Concept | Rule |
|---|---|
| Cost unit | `bps_cost_half_turn` is charged per **unit of turnover**, where 1 unit = a change of 1.0 in target position. |
| Half-turn | Flat → Long (or Long → Flat) = 1 unit = 1 × `bps_cost_half_turn`. |
| Round trip | Enter then exit = 2 units = 2 × `bps_cost_half_turn`. |
| Flip | Long (+1) → Short (−1) = 2 units (close the long, open the short). |
| Terminal position | A position still open on the last bar owes its exit half-turn. `liquidate_at_end=True` charges it; disabling it understates cost. |
| Hurdle rate | Entry threshold should clear the round trip: `signal_threshold >= 2 * bps_cost_half_turn / 10_000`, in decimal-return units. The default config satisfies this exactly (5 bps → 0.001). **Note the `/ 10_000`** — `bps_cost_half_turn` is in basis points while `signal_threshold` is a decimal return; comparing them directly is a units error of four orders of magnitude. |

## Empirical grounding

**Turnover is the dominant driver of net-of-cost survival.** Novy-Marx & Velikov
studied a large cross-section of documented anomalies after transaction costs and
found that "most of the anomalies that we consider with one-sided monthly turnover
lower than 50% continue to generate statistically significant net spreads" — and that
"in all cases transaction costs reduce the strategies' profitability and its
associated statistical significance." Treat monthly turnover as a first-class
viability metric alongside gross Sharpe, not as a footnote.
*Robert Novy-Marx and Mihail Velikov, "A Taxonomy of Anomalies and Their Trading
Costs," Review of Financial Studies 29(1), 104–147 (2016). NBER Working Paper 20721,
<https://www.nber.org/papers/w20721>.*

**A buy/hold spread is the most effective simple mitigation.** The same paper finds
that "introducing a buy/hold spread, which allows investors to continue to hold
stocks that they would not actively trade into, is the single most effective simple
cost mitigation strategy." This is what `exit_threshold` implements: enter only on
strong conviction, exit only when conviction genuinely decays, and stop paying the
spread to re-establish a position you already held.
*Same source as above.*

**Calibrate the bps figure to your own fills, not to published averages.** Frazzini,
Israel & Moskowitz measured live institutional execution across $1.7 trillion of
trades in 21 developed equity markets over 19 years and found actual trading costs
to be "an order of magnitude smaller than previous studies suggest." Cost estimates
therefore vary enormously with who is trading, at what size, and through which
venue — a single borrowed constant is an assumption, not a measurement.
*Andrea Frazzini, Ronen Israel and Tobias J. Moskowitz, "Trading Costs" (23 August
2018), <https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs>.*

## Model limitations

- Cost is **linear in turnover and independent of order size**. There is no market
  impact term, so results are only valid at sizes small relative to available
  liquidity. See `transaction-cost-analysis-tca-integration` for a square-root
  impact model.
- Costs are assumed **constant over time**. Real spreads widen at the open, at the
  close, and during volatility spikes; recalibrate per
  `execution-cost-model-recalibration-cadence`.
- Positions are discrete `{-1, 0, +1}`; there is no partial sizing, leverage, or
  financing cost.
- Fills are assumed **certain and immediate** at the modelled cost. Rejections,
  partial fills, and queue position are out of scope.
