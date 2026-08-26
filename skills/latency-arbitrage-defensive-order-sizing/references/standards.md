# Standards & Sources for Latency-Arbitrage Defensive Order Sizing

## The model this skill approximates

| Claim | Source |
|---|---|
| The fundamental value evolves as a **compound Poisson jump process** with arrival rate $\lambda_{\text{jump}}$ and jump distribution $F_{\text{jump}}$ (finite bounded support, symmetric, mean zero). $J$ is the absolute jump size. | Budish, Cramton & Shim, *The High-Frequency Trading Arms Race: Frequent Batch Auctions as a Market Design Response*, QJE 130(4):1547–1621 (2015), sec. 6.1. [SSRN 2388265](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2388265) |
| A resting quote is snipe-able **only when the jump exceeds half the bid-ask spread**: "Suppose that at time $t$ the signal $y$ jumps from $y_{t^-}$ to $y_t$, and the jump size $\|y_t - y_{t^-}\|$ exceeds $\frac{s}{2}$." If the jump is small, "the sniper takes no action." | Same, sec. 6.2.2–6.2.3 |
| The liquidity provider's per-unit-time cost of being sniped is $\lambda_{\text{jump}}\Pr(J > \tfrac{s}{2})\,E(J - \tfrac{s}{2} \mid J > \tfrac{s}{2})\cdot\frac{N-1}{N}$ (eq. 6.1); the equilibrium spread solves $\lambda_{\text{invest}}\tfrac{s}{2} = \lambda_{\text{jump}}\Pr(J > \tfrac{s}{2})E(J - \tfrac{s}{2} \mid J > \tfrac{s}{2})$ (eq. 6.3). The provider is sniped with probability $\frac{N-1}{N}$. | Same, sec. 6.2.3, Proposition 1 |
| **Sniping makes it especially costly to provide a deep book**: at the $k$-th level the sniping cost term is *identical* to the first level's, while liquidity-provision revenue is scaled by the probability an investor needs at least $k$ units (eq. 6.4). Deeper size therefore requires a wider spread. | Same, sec. 6.2.4 |

The last row is the theoretical warrant for this skill existing at all: size at depth is
disproportionately exposed to sniping, so a defensive response should reduce *size*, not
only widen the spread.

## Where this implementation departs from that model

**These are approximations, and they are not endorsed by the source.**

| Departure | What the model says | What this module does | Consequence |
|---|---|---|---|
| Intensity | $\lambda_{\text{jump}}\Pr(J > \tfrac{s}{2})$ — a **jump** arrival rate | $h = \lambda_{\text{scaling}} \cdot \sigma_{\text{annualized}}$ | A monotone proxy only. Units are "events per ms per unit of annualized volatility"; $\sigma$ is **not** time-scaled to the window. $\lambda_{\text{scaling}}$ must be calibrated from realized markouts, never read off $\sigma$. |
| Spread dependence | Sniping intensity **falls** as the spread widens, via $\Pr(J > s/2)$ | $P_{\text{snipe}}$ does not read the spread at all | The widened spread this module emits is a directive to the quoter; its risk reduction is **not** reflected back into $P_{\text{snipe}}$, which is therefore over-stated once the spread widens. |
| The race | Provider loses with probability $\frac{N-1}{N}$ | Conditions on losing ($p = 1$) | Deliberately conservative. Model the race in `cross-venue-latency-arbitrage-defensive-design`. |
| Volatility as a driver | Jumps, not diffusion | $\sigma$ scales the hazard | At $\sigma = 0.20$ a 1 ms window carries $\approx 0.026$ bps of diffusive standard deviation, putting a 1 bp half-spread $\approx 38\sigma$ away. The shipped 9.5% is not obtainable from a diffusion. |

## EU — MiFID II market making obligations (mandatory, jurisdiction-specific)

Commission Delegated Regulation (EU) 2017/578 (RTS 8), [ec.europa.eu/finance/securities/docs/isd/mifid/rts/160613-rts-8_en.pdf](https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160613-rts-8_en.pdf).
Applies to an investment firm pursuing a market making strategy that has entered (or must
enter) a market making agreement. It does **not** apply to a firm quoting passively outside
such an agreement.

| Provision | Text | Bearing on this skill |
|---|---|---|
| Art. 1(1) | Firms must enter a market making agreement where, during half of the trading days over a one-month period, they "(a) post firm, simultaneous two-way quotes of comparable size and competitive prices (b) deal on their own account in at least one financial instrument on one trading venue for at least 50% of the daily trading hours of continuous trading at the respective trading venue, excluding opening and closing auctions." | The 50% presence floor bounds how much of the day this engine may spend at `HIGH_SNIPING_RISK_CANCEL`. |
| Art. 1(2)(c) | "two quotes shall be deemed of comparable size when their sizes do not diverge by more than 50% from each other" | **Directly constrains this engine's output.** Applying $Q_0(1 - P_{\text{snipe}})$ to one side alone breaches comparable size once the divergence passes 50%. `COMPARABLE_SIZE_MAX_DIVERGENCE` encodes this; the Article does not name a denominator, so the module measures against the **smaller** quote (the conservative reading). |
| Art. 2(1)(b) | The agreement must specify "the minimum obligations to be met by the investment firm in terms of presence, size and spread". | Your agreement may floor the size and cap the spread. A defensive directive that violates the agreed floor is not deployable — reconcile before going live. |
| Art. 2(1)(f) | Obligation "to flag firm quotes submitted to the trading venue under the market making agreement in order to distinguish those quotes from other order flows". | Defensively resized quotes still carry the flag. |
| Art. 3 | The liquidity obligation does not apply in these exceptional circumstances only: (a) extreme volatility triggering volatility mechanisms for the majority of instruments on the segment; (b) war, industrial action, civil unrest or cyber sabotage; (c) disorderly trading conditions (venue system delays/interruptions, multiple erroneous orders, insufficient venue capacity); (d) inability to maintain prudent risk management due to (i) technological issues "including problems with a data feed or other system that is essential to carry out a market making strategy", (ii) regulatory capital / margining / clearing-access issues, (iii) inability to hedge due to a short selling ban; (e) for non-equity instruments, an Art. 9(4) MiFIR suspension. | **Elevated sniping risk is not on this list.** A routine defensive pull is not an Art. 3 event. `INVALID_INPUT_CANCEL` on a genuinely broken latency feed may fall under Art. 3(d)(i); ordinary elevated latency does not. |
| Art. 4(1) | The **trading venue**, not the firm, makes public the occurrence of Art. 3(b), (c) and (e) circumstances. | A firm cannot self-declare its way out of the obligation. |

## US — Reg NMS lot and quotation semantics (mandatory, jurisdiction-specific)

17 CFR 242.600(b), [law.cornell.edu/cfr/text/17/242.600](https://www.law.cornell.edu/cfr/text/17/242.600). NMS stocks only.

| Provision | Text | Bearing on this skill |
|---|---|---|
| 600(b)(93) "Round lot" | By average closing price on the primary listing exchange during the prior Evaluation Period: "(A) \$250.00 or less per share, an order ... of 100 shares; (B) \$250.01 to \$1,000.00 per share ... 40 shares; (C) \$1,000.01 to \$10,000.00 per share ... 10 shares; (D) \$10,000.01 or more per share ... 1 share". | `min_lot_size` must track the instrument's **actual** round lot. The shipped default of 100 is correct only at or below \$250.00; above it the default over-cancels valid quotes. Implemented as `round_lot_for_nms_price()`. |
| 600(b)(16) "Bid or offer" | The price "at which it is willing to buy or sell **one or more round lots** of an NMS security". | A sub-round-lot defensive quote is not a bid or offer in the NBBO sense. |
| 600(b)(69) "Odd-lot information" | Odd-lot transaction data; odd lots at prices between the NBB and NBO aggregated per price level per venue; and the best odd-lot order to buy and to sell. | A defensively shrunk odd lot is disseminated as odd-lot information rather than standing as a protected quotation — which is the substantive reason to pull it, beyond fee overhead. |
| 600(b)(81) "Protected bid or protected offer" | A quotation that is displayed by an automated trading center, disseminated under an effective NMS plan, and is the best bid or offer of an exchange or association. | Read with 600(b)(16): protection is a round-lot concept. |

## This skill's engineering rules

Everything below is a choice made by **this skill**. None of it is a venue, regulatory, or
published standard, and the numeric defaults are starting points, not thresholds anyone
endorses.

| Rule | Requirement | Why |
|---|---|---|
| Fail closed | A non-finite `latency_gap_ms` / `volatility_annualized`, or a negative volatility, MUST return $P_{\text{snipe}} = 1.0$ and pull the quote. | `nan <= 0.0` is `False` and `max(0.0, nan)` is `0.0`, so a naive implementation scores 0% risk and posts full size on a dropped probe. |
| Negative gap is valid | A negative `latency_gap_ms` MUST be treated as zero exposure, not as an error. | The cancel beating the sweep is a real, benign state. |
| Structural vs. measurement inputs | Structural fields MUST raise `ValueError` on construction; measurement fields MUST fail closed into a report. | A bad `base_quote_qty` is a bug; a stale latency sample is an expected runtime event and must not throw inside the quoting path. |
| Floor, never round up | Sizes MUST be floored to `lot_increment`. | Rounding up shows more risk than the model authorised. |
| Inclusive threshold | The cancel MUST fire at $P_{\text{snipe}} \ge$ threshold. | At the point of indifference the safe answer is not being on the book. |
| Cancel precedence | `INVALID_INPUT` > `HIGH_SNIPING_RISK` > `MIN_LOT` > sized. | Most-certain and most-severe conditions first; a corrupt input must never reach the sizing arithmetic. |
| Auditability | A full report MUST be returned on every path, cancels included. | A silent cancel cannot be reviewed after the fact. |
| Score semantics | $P_{\text{snipe}}$ MUST NOT be presented as a calibrated probability. | It rests on a proxy hazard with an uncalibrated coefficient. |
| Divergence reporting | The reduction's divergence MUST be reported, measured against the smaller quote, and `inf` for a pulled quote. | RTS 8 Art. 1(2)(c) constrains the caller, and the engine sees only one side. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `lambda_scaling` | 0.50 | **Placeholder.** Unpublished and uncalibrated. At this value the engine fully cancels above 6.93 ms at $\sigma = 0.20$ and above 1.73 ms at $\sigma = 0.80$. |
| `max_sniping_prob_threshold` | 0.50 | Engineering choice. Must be in $(0, 1]$. |
| `SPREAD_WIDENING_SENSITIVITY` | 2.0 | Engineering choice; not derived from BCS. |
| `min_lot_size` | 100 | Correct for NMS stocks at or below \$250.00 only — see 600(b)(93). |
| `lot_increment` | 1 | Leaves sizes unrounded, preserving pre-2.0.0 behaviour. Set to the venue's increment. |

## Scope boundary

Defensive sizing and quote withdrawal are ordinary risk management. Nothing in this skill
is a surveillance or enforcement artifact, and nothing here authorises withdrawing from an
obligation a firm has contracted into. This module returns directives only; it never sends,
amends, or cancels an order.
