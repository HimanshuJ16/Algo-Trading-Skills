# Standards for Multi-Leg Strategy Margin Optimization

## Scope and attribution

These are **strategy-based** margin requirements for **listed** options. Regulation T
(12 CFR 220.12(f)) does not itself set them: for options issued by a registered
clearing corporation and listed on a registered national securities exchange, Reg T
defers to "the amount specified by the rules of the registered national securities
exchange … provided that all such rules have been approved or amended by the SEC."
The operative text is therefore FINRA Rule 4210(f)(2) and Cboe Rule 10.3, not Reg T.
"Reg T margin" is the industry shorthand for this regime and is used that way here.

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Naked short call | $\max(0.20 S - \text{OTM} + P,\; 0.10 S + P) \times M \times Q$. The floor is 10% of the **underlying** value. |
| Naked short put | $\max(0.20 S - \text{OTM} + P,\; 0.10 K + P) \times M \times Q$. The floor is 10% of the **aggregate exercise price**, not the underlying. |
| Broad-based index options | Substitute 15% for 20%; the 10% floors are unchanged. `underlying_pct` MUST be set from the option class actually being margined. |
| Long options | Paid for in full. (Cboe permits 75% of cost for listed **equity** options with more than 9 months to expiration; this module always charges 100%, which errs high.) |
| Spread short-leg margin | The **lesser of** the naked requirement above and the maximum potential loss. A module that always charges the maximum potential loss overstates a wide credit spread; one that always charges the width mis-prices every debit structure. |
| Maximum potential loss | Netted intrinsic values at price points corresponding to every exercise price in the combination, greatest loss taken. Spot 0 is evaluated in addition to bound the downside tail. |
| Unbounded risk | A combination whose net call position above the highest strike is short has no maximum potential loss. It MUST receive no offset and MUST NOT be classified from leg shape. |
| Expiration | All legs MUST share one expiration to receive the width-based offset. A short leg expiring after its long leg is not a spread at all and MUST be flagged. |
| Quantity | Contract counts are positive; direction is carried by `action`. Per-leg quantities MUST feed the payoff computation — never taken from a single leg. |
| Input validation | Unrecognised option type or action, non-positive/fractional quantity, negative or non-finite premium, non-positive strike or underlying, and unparseable expirations MUST raise. Every silent coercion available here understates the requirement. |
| Reporting basis | Un-offset and combination requirements MUST be reported on the same gross basis so the saving is apples-to-apples, with the net-of-credit deposit reported separately. |
| Reconciliation | The figure is an SRO minimum. It MUST be reconciled against the broker's own (often higher, house-adjusted) requirement before freed capital is redeployed. |

## Not covered by this module

Portfolio margin accounts are margined under **FINRA Rule 4210(g)** using the OCC's
Theoretical Intermarket Margining System (TIMS), which revalues the portfolio across a
scenario set rather than applying strategy templates. TIMS requirements are typically
materially lower than the strategy-based figures here and are **not** derivable from
them. Nothing in this module computes, approximates or bounds a portfolio-margin
number.

Stock legs (covered calls, collars, protective puts against a long equity position)
and non-option instruments are out of scope.

## Source provenance

| Claim | Source | Notes |
|---|---|---|
| Reg T defers listed-option margin to SEC-approved exchange rules | 12 CFR 220.12(f) — https://www.law.cornell.edu/cfr/text/12/220.12 | Distinguishes listed options (exchange rules) from all other puts and calls (the creditor's examining authority's maintenance rules). |
| Naked short call/put percentages and 10% floors; long options paid in full; 75% for equity options over 9 months | Cboe, *Strategy-based Margin* — https://www.cboe.com/us/options/strategy_based_margin | "100% of option proceeds plus 20% of underlying security/index value less out-of-the-money amount, if any, to a minimum of option proceeds plus 10% of underlying security/index value" (calls); "…plus 10% of the put exercise price" (puts). |
| Debit vs credit spread treatment | Cboe, *Strategy-based Margin* (same page) | "For debit (or long) spreads is to pay for the net debit in full. For credit (or short) spreads, cash or cash equivalents equal to the maximum risk (less the net credit received for selling the spread) must be deposited." |
| Spread short-leg margin is the lesser of the (E) requirement and the maximum potential loss | FINRA Rule 4210(f)(2)(H) — https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210 | Also: "'Long' options must be paid for in full", and all legs must be either all listed or all OTC. |
| How maximum potential loss is computed, and that it applies to spreads of more than two legs | FINRA Regulatory Notice 12-44 — https://www.finra.org/rules-guidance/notices/12-44 | "the intrinsic value … of the options at price points for the underlying security or instrument that are set to correspond to every exercise price present in the spread"; "The intrinsic values are netted at each price point, and the maximum potential loss is the greatest loss, if any." The notice's own worked example is a four-leg spread. Effective 26 October 2012. |
| Short must expire on or before the long | FINRA Rule 4210(f)(2)(H) (same) / Regulatory Notice 12-44 | "the 'short' option(s) must expire on or before the expiration date of the 'long' option(s)." |
| 15% for broad-based index options | Cboe/SEC rule filing SR-CBOE-2019-069 Exhibit 5A — https://www.sec.gov/files/rules/sro/cboe/2019/34-87189-ex5.pdf | "100% of the current market value of the contract plus 15% of the underlying index value, less the out-of-the-money dollar amount", minimum 10% of index value (calls) / 10% of aggregate exercise price (puts). |
| Portfolio margin uses OCC TIMS under Rule 4210(g) | FINRA, *Margin Regulation* key topic — https://www.finra.org/rules-guidance/key-topics/margin-accounts ; OCC margin methodology — https://www.theocc.com/risk-management/margin-methodology | Cited only to mark the boundary of this module; no TIMS parameters are used or reproduced here. The former Portfolio Margin FAQ was folded into the Interpretations of Rule 4210 on 9 August 2024. |
