# Standards — risk-control-unit-testing-framework

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry or regulatory standards. No regulator
publishes a maximum order size, position cap, price-collar width, daily loss limit or
risk-check latency budget. RTS 6 Art. 15 names the *categories* of pre-trade control an
investment firm must operate; it prescribes no magnitudes. 17 CFR 240.15c3-5 requires
"appropriate pre-set credit or capital thresholds" and "appropriate price or size
parameters" — "appropriate" is the whole of the specification. Calibrate against your own
instruments, venues and mandate, and record the rationale alongside the change.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_order_size` | $1000$ | Rejects an order whose quantity **exceeds** it. Exactly $1000$ is allowed. Must be finite and $> 0$ — a zero or absent limit raises rather than meaning "unlimited" (FIA §1.1). |
| `max_position_size` | $5000$ | Rejects when the worst-case projected long or short position exceeds it. The projection includes resting orders on the same side (FIA §1.2), and never nets buys against sells. |
| `max_price_collar_pct` | $0.05$ | Fractional deviation of the order's limit price from the reference mid. Compared as `abs(price - mid) > collar * mid`, never by dividing — see *Numerical note* below. |
| `max_daily_loss_usd` | $10{,}000$ | A positive loss magnitude. Breach when signed `accumulated_daily_pnl_usd < -limit`; exactly $-10{,}000$ is allowed. |
| `required_rule_coverage` | all six rule ids | A suite that never actually fires one of these is rejected regardless of how many cases passed. This taxonomy is a convention of this library. |
| `latency_budget_microseconds` | `None` | Optional. Reported as p50/p99 over the suite's own evaluations. `enforce_latency_budget` defaults to `False`. |

**Threshold convention.** Every rule uses strict `>` (or `<` for the signed loss limit), so
the configured limit value is itself permitted. This is a choice, not a regulatory
requirement; it is applied uniformly so a single sentence describes all four rules, and
the standard suite tests it explicitly at every boundary.

**Numerical note.** `abs(price - mid) / mid > collar` and `abs(price - mid) > collar * mid`
are not equivalent in IEEE-754. For an order priced at exactly the collar, the division
form spuriously rejects for roughly $1\%$ of reference prices — e.g. mid $402.69$, price
$422.8245$, where the quotient is $0.05000000000000001$. The multiplication form is used
and a regression test pins that exact pair.

**Latency.** The report exposes p50/p99 of the engine's own evaluation, but enforcement is
off by default: an interpreted reference engine timed on a shared CI runner measures the
runner. Across repeated fresh-process runs of the shipped standard suite the p99 sits
around $2.5\times$ the p50 at single-digit microseconds, with occasional evaluations beyond
$100\,\mu\text{s}$ from cold code paths alone. Latency budgeting against a real engine on representative hardware
belongs in `risk-control-latency-budget`, which likewise records that "illustrative values
are not production policy".

## Regulatory touchpoints (verified against primary sources)

### MiFID II RTS 6 — Commission Delegated Regulation (EU) 2017/589
Organisational requirements of investment firms engaged in algorithmic trading.
([EUR-Lex](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng))

| Article | Title | Relevance to this skill |
|---|---|---|
| Art. 5 | General methodology | 5(1): clearly delineated development **and testing** methodologies prior to deployment or substantial update. 5(4)(a),(b): testing must ensure the system does not behave in an unintended manner and complies with the firm's obligations under the Regulation — which include the Art. 15 pre-trade controls. 5(2): deployment authorised by a person designated by senior management. |
| Art. 7 | Testing environments | 7(1): the Art. 5(4)(a),(b),(d) testing is undertaken in an environment separated from production. A unit-test suite satisfies the separation trivially; it does not satisfy 5(4)(d). |
| Art. 11 | Management of material changes | 11(1): a material change to the production environment is preceded by review by a person designated by senior management. 11(2): functionality changes are communicated to the traders, the compliance function and the risk management function. The gate report is the artefact that review consumes. |
| Art. 12 | Kill functionality | Immediate cancellation of any or all unexecuted orders. **Not tested here** — see `kill-switch-and-drawdown-circuit-breakers`. |
| Art. 15 | Pre-trade controls on order entry | The rules this framework tests. (a) price collars that automatically block or cancel orders outside set price parameters; (b) maximum order values; (c) maximum order volumes; (d) maximum messages limits. **(d) is out of scope** for a stateless per-order engine. |
| Art. 16 / 17 | Real-time monitoring / post-trade controls | Out of scope: this is a pre-trade gate only. |

*Currency caveat:* RTS 6 remains in force in the EU; the UK version is assimilated law
maintained in the FCA Handbook technical standards. Confirm the instrument and article
numbering that apply in your jurisdiction before citing either in a filing.

### United States — SEC Rule 15c3-5 (17 CFR 240.15c3-5)
([Cornell LII](https://www.law.cornell.edu/cfr/text/17/240.15c3-5))

- **§(b)** — a broker-dealer with market access shall "establish, document, and maintain a
  system of risk management controls and supervisory procedures reasonably designed to
  manage the financial, regulatory, and other risks of this business activity."
- **§(c)(1)(i)** — prevent orders exceeding "appropriate pre-set credit or capital
  thresholds", in the aggregate per customer and for the broker-dealer.
- **§(c)(1)(ii)** — prevent erroneous orders "by rejecting orders that exceed appropriate
  price or size parameters, **on an order-by-order basis or over a short period of time**,
  or that indicate duplicative orders." The emphasised limb is a *cumulative* control this
  framework cannot exercise against a stateless engine.
- **§(e)(1)/(e)(2)** — review the effectiveness of the controls no less frequently than
  annually, with an annual CEO certification.
- **What the rule does not say:** it contains **no** algorithm- or control-testing
  mandate, and **no** numeric threshold or latency requirement anywhere. Do not present a
  passing gate as 15c3-5 evidence beyond its contribution to the §(e)(1) review.

### United States — FINRA Regulatory Notice 15-09 (26 March 2015)
*Guidance on Effective Supervision and Control Practices for Firms Engaging in
Algorithmic Trading Strategies.* **Guidance, not rule text** — it describes practices firms
"should consider". Relevant items: conducting testing prior to production "to confirm that
core code components operate as intended and do not produce unintended consequences"; a
development and change-management process that tracks changes and includes review of test
results and approval protocols; quality assurance performed independently of code
development; and "implementing and periodically evaluating test controls to confirm their
adequacy and reliability".
([finra.org/rules-guidance/notices/15-09](https://www.finra.org/rules-guidance/notices/15-09))

### Industry — FIA, *Guide to the Development and Operation of Automated Trading Systems* (March 2015)
The source for the specific control semantics this framework models, and the closest thing
to a citable basis for the "100% pass rate blocks the release" gate.
([FIA guide, March 2015](https://www.fia.org/fia/articles/fia-issues-guide-development-and-operation-automated-trading-systems))

| Section | Provision | How this skill applies it |
|---|---|---|
| §1.1 Maximum Order Size | The "fat-finger" limit. "Systems should be designed to prevent orders from being placed in cases where no order size limits have been set for an instrument." | A zero, absent, infinite or NaN limit raises in `RiskRuleConfig.__post_init__` instead of being read as "unlimited". |
| §1.2 Maximum Intraday Position | "both current positions and working orders should be evaluated to determine whether a breach of the limit could occur. It is important to include working orders such that limits would not be breached if that order is filled". | `ProposedOrder` carries `working_buy_quantity`/`working_sell_quantity`, and the projection takes the worst case on each side without netting them. |
| §1.3 Market Data Reasonability | Where market data may be stale or aberrant, "an alert should be provided … and any orders should be blocked while the deviation is investigated." | An unusable `current_mid_price` raises `REFERENCE_PRICE_UNAVAILABLE` and **blocks**, rather than skipping the collar. |
| §1.4 Price Tolerance | The *participant-side* control: "the maximum amount an individual order's limit price may deviate from a reference price". | This is what `FAT_FINGER_PRICE_COLLAR` implements. FIA reserves "price collar" (§1.6) for the *exchange-side* band on trade prices; RTS 6 Art. 15(a) uses "price collars" for the participant-side control, so both names appear in the literature for this check. |
| §5.2 Testing | "Failing to successfully complete a test deemed necessary for verifying that a system is working properly should be a sufficient condition for preventing the release of that system." Also: previously designed tests "should be reused for future software changes", and obsolete tests should be removed. | The gate is fail-closed on any case failure, and `required_rule_coverage` keeps retired coverage visible rather than silently absent. |
| §5.2.1 / §5.2.2 | Unit testing tests discrete units in isolation; regression testing "confirms that no bugs are introduced into an existing system as a result of changes being made". | Each boundary and fail-closed test in `scripts/test_risk_unit_test_harness.py` is written to fail against the specific prior behaviour it replaced. |

## Known limitations

- **Per-order only.** No cumulative, rate-based or time-windowed control is exercised, so
  neither 15c3-5(c)(1)(ii)'s "over a short period of time" limb nor RTS 6 Art. 15(d)
  message limits are covered.
- **No credit, capital, duplicate-order, restricted-list or self-match control.** The
  shipped fixture models four rules; 15c3-5(c)(1)(i) and (c)(2)(i)–(iii) are not among them.
- **Stateless and single-threaded.** No concurrency, no ordering, no position drift between
  the check and the fill. A real gate's races are not testable with this fixture.
- **Says nothing about disorderly trading.** RTS 6 Art. 5(4)(d) requires testing that the
  algorithm does not contribute to disorderly trading conditions and works effectively
  under stress; a unit suite cannot produce that evidence.
- **Only as good as the cases written.** Coverage enforcement checks that a rule *fired*,
  not that it fired on the input that would have caught the bug.
- **Latency figures are indicative.** See *Latency* above.
