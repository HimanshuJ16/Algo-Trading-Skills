# Risk Management Standards — margin-utilization-circuit-breaker

## Engineering defaults

Every value below is a **house-policy default shipped by this module**, not a regulatory
prescription. No rule surveyed here fixes a margin utilization number for a trading firm.
The "Anchored to" column states what the number was chosen against; it does not make the
number mandatory.

| Parameter | Default | Anchored to |
|---|---|---|
| `basis` | `MarginBasis.MAINTENANCE` | Liquidation is driven by the maintenance requirement, so that is the basis a breaker must default to |
| `warning_threshold` | $0.60$ | House policy. Early enough to stop adding size while an unwind is still voluntary |
| `hard_stop_threshold` | $0.80$ | House policy. Leaves a $20\%$ cushion against the maintenance basis, where $1.0$ is the broker's liquidation point |
| `re_arm_threshold` | `= warning_threshold` ($0.60$) | Must be strictly below `hard_stop_threshold`: re-arming at the trip level re-trips on the next poll, so the re-arm must clear back to the pre-warning band |
| `max_data_age_seconds` | `None` (check disabled) | Fail-open default retained for backward compatibility only. **Set it.** The module logs a warning at construction when it is `None` |
| `latching` | `True` | The condition that tripped the breaker is usually still present; auto-recovery delays the same breach |

Threshold validation is deliberately strict: any threshold outside $(0, 1]$ is rejected at
construction, and on the `MAINTENANCE` basis `hard_stop_threshold` must be **strictly**
below $1.0$. A breaker that trips at maintenance utilization $1.0$ has not prevented
anything — see the IBKR note below.

## What the numerator means, and why it is a factor of two

**United States — Reg T initial vs FINRA maintenance.**
[12 CFR 220.12(a)](https://www.govinfo.gov/content/pkg/CFR-2023-title12-vol3/xml/CFR-2023-title12-vol3-sec220-12.xml)
requires, for a margin equity security, "50 percent of the current market value of the
security or the percentage set by the regulatory authority where the trade occurs,
whichever is greater." [FINRA Rule 4210(c)](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210)
sets maintenance margin at "25 percent of the current market value of all margin
securities … long in the account", with short-position minima of the greater of \$5.00 per
share or 30% of market value (\$2.50 per share or 100% below \$5.00).

Consequence for this module: the *same book* is roughly twice as "utilized" on the initial
basis as on the maintenance basis. A 0.80 threshold is a materially tighter budget when
`used_margin` carries the Reg T initial requirement than when it carries the maintenance
requirement. `basis=` exists so that the number in a log line is unambiguous; it does not
convert between the two, and this module does not compute either requirement.

## Broker behaviour verified for this skill

**Interactive Brokers — no margin call, real-time liquidation.**
[Current Excess Liquidity](https://www.interactivebrokers.com/campus/glossary-terms/current-excess-liquidity/)
is the margin cushion before liquidation: `Equity with Loan Value − Maintenance Margin` for
the securities segment, `Net Liquidation Value − Maintenance Margin` for commodities. When
it is negative the account no longer meets maintenance requirements and IBKR may begin
liquidating. IBKR's own *margin cushion* percentage is
`(Equity with Loan Value − Maintenance Margin) / Net Liquidation Value` — the complement of
maintenance utilization, and note the numerator and denominator use *different* equity
measures.

IBKR [states](https://www.ibkrguides.com/traderworkstation/margin-monitoring.htm) that it
does not make margin calls, that accounts generally will not have time to deposit funds to
meet a deficiency, that it may liquidate without prior notice and without letting the
client choose the positions or the order, and that an account moving rapidly from a
greater-than-10% cushion into violation may be liquidated **without ever displaying a
warning**. A client-side breaker therefore has to trip on a cushion wide enough to absorb
the gap your instruments actually produce — 20% is a starting point, not a safe constant.

**Alpaca — both bases are available.** The account object exposes `initial_margin`
("Reg T initial margin requirement, continuously updated"), `maintenance_margin`
("maintenance margin requirement, continuously updated") and `equity`
(`cash + long_market_value + short_market_value`) as separate fields
([account plans](https://docs.alpaca.markets/docs/account-plans)). Choosing the basis here
is a free choice; make it explicitly.

**Zerodha Kite Connect — `utilised.debits` is not a pure requirement.** Kite's
[user margins](https://kite.trade/docs/connect/v3/user/) response defines `debits` as the
"Sum of all utilised margins (unrealised M2M + realised M2M + SPAN + Exposure + Premium +
Holding sales)" — realised and unrealised P&L are bundled in with the margin requirement.
`net` is documented as a "Net cash balance available for trading", not account equity, and
no maintenance-margin field is exposed. Map these deliberately or the ratio measures
something other than what its name says.

**MetaTrader 4/5 — the reciprocal convention.** MetaTrader reports *margin level* as
`Equity / Margin × 100` ([margin calculation](https://www.metatrader5.com/en/terminal/help/trading_advanced/margin_forex)),
where **higher is safer**: a margin call level of 100% and a stop-out level of, say, 50% or
20% are low numbers signalling distress. This module's utilization is the inverse. Feeding
`ACCOUNT_MARGIN_LEVEL` in as `used_margin` inverts the control silently.

**CME Clearing — the requirement moves without you trading.** CME Clearing publishes
performance bond (margin) changes as numbered clearing advisory notices with a stated
effective date — e.g. "Performance Bond Requirements: Energy, FX, Interest Rate and Metal
Margins – Effective August 07, 2026" — as part of its
[performance bond](https://www.cmegroup.com/solutions/risk-management/performance-bonds-margins.html)
regime, in which requirements are raised in more volatile periods and lowered in calmer
ones. An unchanged position can therefore consume materially more margin at the next
session, and brokers may layer house margin on top. This is the mechanism behind the
"weekend/holiday margin change" pitfall.

## Regulatory reference points

These establish that a pre-trade limit of this kind is expected. **None of them prescribes
a threshold value**, and each binds a specific class of firm.

**EU/UK — MiFID II RTS 6 requires the firm to set its own limits and enforce them
automatically.** Commission Delegated Regulation (EU)
[2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng) Art. 15(4): an investment
firm "shall set market and credit risk limits that are based on its capital base, its
clearing arrangements, its trading strategy, its risk tolerance, experience" and shall
adjust them as market impact changes. Art. 15(5) requires the firm to "automatically block
or cancel orders where those orders risk compromising the investment firm's own risk
thresholds," applied where appropriate at client, instrument, trader, desk or firm level.
Art. 15(1) separately mandates price collars, maximum order values, maximum order volumes
and maximum message limits — those are order-shape controls, not margin controls, and are
out of scope here.

Art. 15(6) governs overriding a block: procedures for submitting an order the firm's own
pre-trade controls stopped "shall be applied in relation to a specific trade on a temporary
basis and in exceptional circumstances" and "shall be subject to verification by the risk
management function and authorisation by a designated individual." That is the requirement
`re_arm()` implements — a checked boolean, a named operator, a stated reason, and a
persisted refusal record.

Applies to investment firms engaged in algorithmic trading under MiFID II Art. 17(1). The
UK retains the same text as assimilated law.

**United States — SEC Rule 15c3-5 binds the broker-dealer, not its customer.**
[17 CFR 240.15c3-5](https://www.govinfo.gov/content/pkg/CFR-2023-title17-vol4/xml/CFR-2023-title17-vol4-sec240-15c3-5.xml)(b)
applies to "a broker or dealer with market access, or that provides a customer or any other
person with access to an exchange or alternative trading system through use of its market
participant identifier or otherwise." Paragraph (c)(1)(i) requires controls reasonably
designed to "[p]revent the entry of orders that exceed appropriate pre-set credit or
capital thresholds in the aggregate for each customer and the broker or dealer … by
rejecting orders if such orders would exceed the applicable credit or capital thresholds."
A proprietary firm trading through a third-party broker-dealer is covered indirectly, via
that broker-dealer's controls. Do not describe this module's thresholds as satisfying
15c3-5 on your own behalf unless you are the broker-dealer with market access.

**India — SEBI's upfront-margin framework is a settlement-side obligation, not a
utilization cap.** SEBI circular SEBI/HO/MRD2/DCAP/CIR/P/2020/127 (20 July 2020),
["Framework to Enable Verification of Upfront Collection of Margins from Clients in Cash
and Derivatives segments"](https://www.sebi.gov.in/legal/circulars/jul-2020/framework-to-enable-verification-of-upfront-collection-of-margins-from-clients-in-cash-and-derivatives-segments_47101.html),
and its [May 2022 amendment](https://www.sebi.gov.in/legal/circulars/may-2022/changes-to-the-framework-to-enable-verification-of-upfront-collection-of-margins-from-clients-in-cash-and-derivatives-segments_58843.html)
govern verification of upfront margin collection and the penalty regime for short
collection. The penalty schedule is levied on the *broker* by the exchange and is a
separate concern from this breaker; the specific penalty percentages were not verified
against a primary source for this skill and are deliberately not quoted here. Consult the
circulars and your exchange's current penalty notice directly.

## Explicitly out of scope

This module does not compute margin requirements (no SPAN, no portfolio margin, no
cross-margin offsets, no liquidation price), does not place or cancel orders, does not plan
liquidations, and does not reconcile multi-currency margin. Run it alongside
`broker-account-margin-call-handling` for broker-cushion grading and de-leveraging,
`options-margin-span-calculation-global` for requirement estimation, and
`leverage-limit-enforcement-across-instruments` for the separate exposure question.
