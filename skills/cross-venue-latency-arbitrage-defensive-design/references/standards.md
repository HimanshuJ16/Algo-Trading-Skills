# Standards for Cross-Venue Latency Arbitrage Defensive Design

## Engineering Standards

| Metric | Engineering Standard |
|---|---|
| Toxicity Trigger | Defensive triggers MUST be calibrated against the firm's own realized adverse-selection data. No universal threshold exists, so the normalized threshold ships disabled (`None`) rather than hard-coded. |
| Bounded Toxicity Metric | Any trigger denominated in ticks MUST account for the bound $\tau_{\text{ticks}} \le \text{half-spread in ticks}$: the weighted mid is a convex combination of the touch prices and cannot leave $[P_{\text{bid}}, P_{\text{ask}}]$. A "$\tau \ge 2.0$ ticks" rule cannot fire in any market quoted tighter than 4 ticks. Engines MUST report `half_spread_ticks` so the ceiling is visible. |
| Scale-Free Scoring | A cross-instrument toxicity score MUST normalize by the half-spread, giving $\tau_{\text{norm}} = |V_{\text{bid}} - V_{\text{ask}}| / (V_{\text{bid}} + V_{\text{ask}}) \in [0,1]$, so thresholds transfer between tick regimes. |
| No Premature Rounding | Prices MUST NOT be rounded before the toxicity difference is taken. Rounding a weighted mid to 4 decimals zeroes the signal on instruments quoted to 5 (FX) or 8 (crypto) decimals. |
| Numerically Stable Skew | The imbalance skew MUST be derived from the depth ratio, not by differencing two nearly equal prices; float residue on a balanced book otherwise produces a phantom toxic side. |
| Directional Defense | The exposed side MUST be identified ($P_w >$ mid implies an exposed ASK) and widened asymmetrically. Symmetric widening discards the only directional information the signal carries. |
| Bounded Quote Adjustment | Spread widening MUST scale on the bounded normalized score, not on the spread-dependent tick score, which yields unbounded widening in wide books. |
| Dead-Heat Handling | A latency margin of exactly zero MUST be treated as a LOSS (venues process in arrival order), and a jitter buffer sized from measured latency percentiles SHOULD be applied on top. |
| One-Way Latency | The race MUST be evaluated on one-way cancel delivery time to the matching engine. Substituting a round trip is conservative but is not the raced quantity, and MUST be documented where used. |
| Clock Domain Integrity | Lead-event and cancel-sent timestamps MUST come from one synchronized clock domain; a cancel timestamped before the event it reacts to MUST raise rather than inflate the margin. |
| Consistent Directives | An engine that reports a preemptive cancel MUST NOT simultaneously recommend a positive quote size. Where the cancel cannot win the race, the recommended size MUST be 0. |
| Locked/Crossed Books | $P_{\text{bid}} \ge P_{\text{ask}}$ MUST short-circuit to maximum defensive posture. Deviation-based scores read zero there, reporting the most dangerous state as benign. |
| Input Validation | Non-positive tick size, negative depth, and non-finite prices/latencies MUST be rejected. Negative depth can drive the weighted mid outside the touch and invert the signal. |

## Market-Structure and Regulatory Anchors (verify currency before relying on them)

| Regime / Source | Provision | Effect on this design |
|---|---|---|
| US — SEC / IEX | Order approving D-Limit, Release No. 34-89686 (Aug 2020); *Citadel Securities LLC v. SEC* (D.C. Cir. 2022) upholding the approval | Establishes the regulated-venue precedent for a formulaic indicator that identifies moments of imminent adverse selection from latency arbitrage (IEX's Crumbling Quote Indicator). The court noted the indicator is on for roughly 0.007% of the trading day while ~24% of IEX displayed liquidity trades in that window — the concentration this skill's signal targets. Related litigation over IEX Options (SEC approval 18 Sep 2025) was pending on petition as of Aug 2026. |
| EU — MiFID II RTS 8 | Commission Delegated Regulation (EU) 2017/578, Arts. 1–2 | A firm pursuing a market-making strategy under a market-making agreement must post firm, simultaneous two-way quotes of comparable size and competitive prices for **at least 50% of daily trading hours** during continuous trading. Defensive quote-pulling must fit inside this obligation. |
| EU — MiFID II RTS 8 | Reg. (EU) 2017/578, Art. 3 | Exceptional circumstances releasing the quoting obligation are enumerated: extreme volatility triggering volatility mechanisms, war/industrial action/civil unrest/cyber sabotage, disorderly trading conditions, inability to maintain prudent risk management (including technological issues and short-selling bans), and non-equity suspension periods. **Detected adverse-selection risk is not among them.** |
| EU — MiFID II RTS 9 | Commission Delegated Regulation (EU) 2017/566 | Venues must limit the ratio of unexecuted orders to transactions (OTR), calculated and monitored per member and per instrument in both volume and number; venues set the maximum and may set a specific limit for members under market-making obligations. Cancel-heavy defense consumes this budget. |
| Academic | Stoikov, S., "The micro-price: a high-frequency estimator of future prices", *Quantitative Finance* 18(12), 1959-1966 (2018); SSRN 2970694 | Defines the micro-price as the long-run expectation of the mid conditional on available information, and reports it as empirically a better short-horizon predictor than the mid **or the weighted mid**. The quantity implemented here is the weighted mid; the naming distinction matters if the score is used as a price predictor rather than a risk flag. |

Sources consulted (Aug 2026): SEC order 34-89686 and D.C. Circuit opinion in *Citadel Securities v. SEC* (2022);
EUR-Lex CELEX:32017R0578 (RTS 8) Arts. 1-3; Commission Delegated Regulation (EU) 2017/566 (RTS 9) and
ESMA market-structures Q&A material; Stoikov (2018) via SSRN and Quantitative Finance. Numerical
behaviour stated above (metric bound, rounding loss, float residue) was reproduced directly in CPython 3.11.
