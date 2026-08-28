# Standards — reinforcement-learning-safety-constraints-for-execution

## Configuration defaults (calibrate before use)

These are the guard's defaults, **not** industry standards and **not** regulatory minimums.
No regulator, exchange or standards body publishes a mandatory maximum slice size, a
mandatory liquidation horizon, or a required reward penalty. RTS 6 is explicit that firms
"can set the parameters underpinning hard blocks at their discretion", taking into account
the firm's risk appetite, the type of trading activity, the instruments traded, the venues
used, and the instruments' price, liquidity and volatility conditions (ESMA supervisory
briefing ¶72). Calibrate each value per instrument and record it with the deployment.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_order_size` | $100.0$ | Per-action absolute quantity clip, sign-preserving. Binds only when `|qty| > max_order_size`; exactly at the limit passes. Must be finite and positive. |
| `penalty_lambda` | $10.0$ | Deducted once per intercepted step. **Not a risk limit** — a hyperparameter of the reward scale. Never applied to data-integrity vetoes. $0.0$ shields without punishing. |
| `terminal_horizon_sec` | $60.0$ | Remaining window at or below which open inventory is force-liquidated. Boundary is inclusive. |
| `terminal_clearance_overrides_spread_veto` | `True` | Whether a forced liquidation crosses a wide spread. `True` prefers a known cost over unbounded carry risk; `False` prefers holding, and leaves the residual inventory to you. |
| `max_cumulative_qty` | `None` | Per-episode traded-quantity ceiling. `None` means **unconstrained**: the per-order clip does not bound accumulation across steps. |
| `max_inventory` (state) | — | Symmetric absolute position cap. The admissible band is widened to the current inventory so an over-cap position stays reducible. |
| `max_spread` (state) | — | Widest quoted spread, in price units, at which the policy may trade. Compared unrounded; exactly at the limit is not a breach. |

## Regulatory position (verified — read this before citing anything)

**This guard is a strategy-side guardrail. It is not a broker's market-access control and
it is not a complete pre-trade control set.**

| Claim | Source | Status |
|---|---|---|
| Market-access risk controls must "[p]revent the entry of erroneous orders, by rejecting orders that exceed appropriate price or size parameters, on an order-by-order basis or over a short period of time" | SEC Rule 15c3-5(c)(1)(ii), [17 CFR 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5) | Mandatory (US broker-dealers with market access). Note "**or over a short period of time**" — an order-by-order clip alone is not the whole obligation. |
| Controls must "[p]revent the entry of orders that exceed appropriate pre-set credit or capital thresholds" | SEC Rule 15c3-5(c)(1)(i) | Mandatory. |
| Those controls "shall be under the direct and exclusive control of the broker or dealer" | SEC Rule 15c3-5(d)(1) | Mandatory. **A limit inside your own RL agent cannot satisfy this** — it is not under the broker's exclusive control. This shield supplements broker-side controls; it never replaces them. |
| Firms must carry out pre-trade controls comprising (a) price collars, (b) maximum order values, (c) maximum order volumes, (d) maximum messages limits | RTS 6 Art. 15(1), [Commission Delegated Regulation (EU) 2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng) | Mandatory (EU/EEA investment firms engaged in algorithmic trading). This guard implements **(c) only**. |
| Firms "shall have in place repeated automated execution throttles which control the number of times an algorithmic trading strategy has been applied" | RTS 6 Art. 15(3) | Mandatory. **Not implemented here** — see Known limitations. |
| Firms "shall automatically block or cancel orders where those orders risk compromising the investment firm's own risk thresholds" | RTS 6 Art. 15(5) | Mandatory. The position cap is an implementation of this at the strategy layer. |
| Procedures are required for orders "blocked by the investment firm's pre-trade controls but which the investment firm nevertheless wishes to submit"; applied "on a temporary basis and in exceptional circumstances" and "subject to verification by the risk management" function | RTS 6 Art. 15(6) | Mandatory. An override path may exist, but it is exceptional and independently verified — never a developer's unilateral flag flip. |
| A hard block "should be set as a default limit which traders should not be able to overcome either directly (e.g. by modifying an order block's parameters) or indirectly (e.g. **by slicing blocked orders to circumvent the set parameters**)" | ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 Feb 2026, ¶74 — [ESMA](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf) | Supervisory guidance. **Directly relevant:** a per-order clip that the policy re-proposes every step is circumvention by slicing. This is why `max_cumulative_qty` exists. |
| "Traders should not be allowed to independently override hard blocks... Any revision or modification of parameters should involve the risk management and compliance functions." | ibid. ¶73 | Guidance. Applies to changing `max_order_size`, `max_inventory`, `max_spread`, `terminal_horizon_sec`. |
| Firms using "reinforcement learning, deep learning, neural-networks, and GenAI" to generate trading signals "should consider risks posed by trading signals generated by more advanced AI-related technology when designing PTCs" | ibid. ¶85 | Guidance. The clearest supervisory statement that an RL execution policy is expected to sit behind pre-trade controls. |
| Retesting is expected for changes including "Risk Controls — Changing thresholds, kill switch logic, or alert triggers" and "Adaptive Capabilities — Retraining or modifying machine learning components" | ibid. ¶31 table | Guidance. Both a policy retrain and a limit change are material changes. |
| Firms "should manage the risk that a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change in the model output without it being tested" | ibid. ¶30, ¶47 | Guidance. An online-learning policy changes continuously by construction. |
| Real-time monitoring "should also include the alerts from triggering PTCs" | ibid. ¶94; RTS 6 Art. 16 | Mandatory (Art. 16) plus guidance. Interceptions are monitoring input, not just a log line. |
| Firms must be able to cancel immediately any or all unexecuted orders ("kill functionality") | RTS 6 Art. 12 | Mandatory. **Out of scope here** — see `execution-algorithm-kill-switch-integration`. |
| Exchanges and associations must maintain rules requiring members "reasonably to avoid... [d]isplaying quotations that lock or cross any protected quotation in an NMS stock" | 17 CFR 242.610(e), [Reg NMS Rule 610](https://www.law.cornell.edu/cfr/text/17/242.610) | Mandatory on SROs (US NMS stocks). Supporting rationale for treating a crossed top-of-book as bad data rather than a tradeable spread. Not itself an obligation on this guard. |

Jurisdiction note: the SEC rules bind US broker-dealers; RTS 6 and the ESMA briefing bind
EU/EEA investment firms. Neither set travels automatically to other jurisdictions. Nothing
above makes a *spread veto* or a *terminal liquidation horizon* a regulatory requirement —
both are execution-quality and mandate-completion controls, and they are house policy.

## Method (verified against primary sources)

| Fact | Source | Applied here |
|---|---|---|
| A **post-posed shield** "is introduced after the learning agent. The shield monitors the actions from the learner and corrects them only if the chosen action causes a violation." The alternative, a **preemptive** shield, "provides a list of safe actions" before the agent decides. | Alshiekh, M., Bloem, R., Ehlers, R., Könighofer, B., Niekum, S., Topcu, U. (2018), "Safe Reinforcement Learning via Shielding", *AAAI-18*, pp. 2669–2678 — [arXiv:1708.08611](https://arxiv.org/abs/1708.08611) | `intercept_action` is a post-posed shield. A preemptive shield over a continuous quantity would mean projecting the whole admissible interval into the policy — a different and generally preferable design where the policy can consume it. |
| Two options on correction: "The agent assigns a punishment $r'_{t+1} < 0$ to the unsafe action $a^1_t$ and learns that selecting $a^1_t$ at state $s_t$ is unsafe", or "The agent updates the unsafe action $a^1_t$ with the reward $r_{t+1}$. Therefore, picking unsafe actions can likely be part of an optimal policy by the agent." | ibid. | `penalty_lambda > 0` is the first option; `penalty_lambda = 0.0` is the second. Note the punishment attaches to $a^1_t$, **the action the agent proposed** — hence `SafeAction.proposed_qty`. |
| "The learning agent does not even need to know that it is shielded." | ibid. | The guard requires no cooperation from the policy and can be added to an already-trained one. |
| "Since $\mathcal{M}'$ is a standard MDP, all learning algorithms that converge on standard MDPs can be shown to converge in the presence of a shield under this construction." | ibid. | Convergence is preserved by the *shielded-MDP* construction. It is **not** a guarantee about this implementation: the penalty variant changes the reward function, and correcting a continuous action is not the discrete-action setting the proof addresses. Treat convergence as unproven here. |

## Known limitations

- **Quantity only.** Limit price, order type, venue and aggressiveness are untouched. There
  is no price collar (RTS 6 Art. 15(1)(a)) and no order-value limit (Art. 15(1)(b)); a
  quantity limit is not a notional limit when the price moves.
- **No message-rate or repeated-execution throttle** (RTS 6 Art. 15(1)(d), Art. 15(3)).
  `max_cumulative_qty` bounds traded quantity, not message count — a policy vetoed to zero
  every step consumes no budget while still generating one decision per step.
- **The terminal horizon does not guarantee a flat book.** Liquidation is clipped to
  `max_order_size` per step like any other order, and the guard never checks that the
  horizon leaves enough steps to finish. 800 units at 100 per slice needs 8 slices; a 60s
  horizon polled every 30s offers 4, and 400 units survive the deadline silently. Data
  integrity vetoes inside the terminal window consume slices too. Size
  `terminal_horizon_sec` against `max_order_size`, the decision frequency and the expected
  worst-case inventory, and monitor the residual.
- **Stateless between steps except for the budget.** The guard holds no order state: it
  cannot see partial fills, rejects, cancels, or in-flight orders. `current_inventory` must
  be reconciled upstream, and an unfilled shielded order is invisible to it.
- **Single instrument, single account.** No portfolio-level or cross-instrument exposure
  aggregation, and no notion of correlated risk across simultaneously shielded agents.
- **Symmetric position cap only.** Asymmetric long/short limits and short-sale locate
  requirements are out of scope.
- **The terminal-clearance override is a policy choice, not a derivation.** Crossing a wide
  spread to meet a deadline can be the wrong trade in a genuinely dislocated market; the
  flag exists because neither answer is universally correct.
- **Convergence and optimality under penalty shaping are not established** for this
  continuous-action, reward-modifying variant. The shield bounds behaviour; it does not
  promise the policy still learns well.
- **A shield is a bound, not a validation.** It says nothing about whether the policy is
  profitable, well-calibrated, or free of look-ahead bias in its features.

## Category

`execution-algorithms` / `machine-learning` — see top-level `mappings/` directory.
