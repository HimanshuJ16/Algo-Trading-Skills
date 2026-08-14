# Standards for Blue-Green Deployments of Live Trading Strategies

## 0. How to read this document

Sections 1-2 are **regulatory touchpoints**: obligations that exist in law for firms in
scope, with the jurisdiction stated. Sections 3-7 are **engineering standards**: they are
this repository's recommended practice, not legal requirements, and are labelled as such
so an agent does not present them to an operator as compliance mandates.

Nothing here is a substitute for your own compliance function's determination of which
regime applies to you.

## 1. EU / MiFID II — Commission Delegated Regulation (EU) 2017/589 ("RTS 6")

**Applicability:** investment firms engaged in algorithmic trading that are authorized
under MiFID II (Directive 2014/65/EU). It does **not** apply to a US-only broker-dealer,
to an unregulated proprietary trader outside the EU, or to a retail individual trading
their own capital. The UK operates a materially equivalent onshored version of RTS 6,
supervised by the FCA. Do not apply these obligations to a firm outside their scope.

| RTS 6 Article | Title | Relevance to this skill |
|---|---|---|
| Art. 5(2) | General methodology | A person designated by the firm's senior management must authorize the deployment or substantial update of an algorithmic trading system, trading algorithm or algorithmic trading strategy. This is what `authorised_by` on `deploy_to_inactive()` and `cutover()` exists to capture — an unattributed deployment does not satisfy it. |
| Art. 5(7) | General methodology | The firm must keep records of any material change made to the software used for algorithmic trading, allowing it to determine: when the change was made, the person that made it, the person that approved it, and the nature of the change. `deployment_history` is structured to answer exactly those four questions — hence `timestamp`, `authorised_by`, `action`/`detail`, and the `forced` flag that distinguishes an overridden rollback from a clean one. |
| Art. 8 | Controlled deployment of algorithms | Introducing a new or materially modified algorithm into production must be controlled, including cautious limits (instruments traded, price, order value and count, positions, markets involved) and more intensive monitoring of the algorithm's activity. A blue-green cutover is a controlled deployment mechanism, but the *pointer swap alone does not satisfy this article* — the limits and heightened monitoring during the drain window are a separate obligation. |
| Art. 11 | Management of material changes | Post-deployment management of material changes to trading systems and algorithms. A strategy version bump routed to live capital is the material change this article governs. |
| Art. 12 | Kill functionality | The firm must be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders at any or all venues it is connected to, and must be able to attribute each order to the responsible algorithm, trader, desk or client. This is the mandated fallback when `rollback()` refuses — halting is a regulated capability; a forced rollback onto an unreconciled book is not. |

Primary text: Commission Delegated Regulation (EU) 2017/589, EUR-Lex ELI
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. The UK onshored text is at
<https://www.legislation.gov.uk/eur/2017/589> and in the FCA Handbook technical standards.

## 2. US — SEC Rule 15c3-5 (Market Access Rule)

**Applicability:** broker-dealers with market access, including those providing market
access to customers. It binds the *broker-dealer*, not the strategy author — but it
constrains any deployment that touches the order path.

- Financial and regulatory risk-management controls must be applied on an **automated,
  pre-trade** basis, and must be under the **direct and exclusive control** of the
  broker-dealer with market access.
- Consequence for this skill: a cutover must never move order flow onto a path where
  those controls are absent, weaker, or owned by the strategy instance rather than the
  broker-dealer. "The new version has its own risk checks" does not satisfy the rule.
  Treat the pre-trade control layer as *outside* the blue-green swap, not as part of the
  strategy artifact being swapped.
- The rule also requires at least annual review of the effectiveness of these controls,
  with certification by the CEO or equivalent officer.

Primary text: 17 CFR 240.15c3-5; adopting release SEC Rel. No. 34-63241
<https://www.sec.gov/files/rules/final/2010/34-63241.pdf>. Staff FAQs:
<https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0>.

## 3. Engineering standard — market data continuity

*Recommended practice, not a regulatory requirement.*

- The incoming instance should be consuming and processing live market data, and be
  caught up, **before** it is eligible for cutover — not started at the moment routing
  moves. Where the venue publishes redundant A/B multicast feeds, both instances should
  arbitrate across them independently.
- Gap detection belongs in the health check: an instance whose sequence numbers show an
  unrecovered gap is not fit to trade regardless of process liveness.

## 4. Engineering standard — order safety across the handover

*Recommended practice, not a regulatory requirement.*

- Client order IDs must be idempotent across the cutover so that an order in flight when
  routing moves cannot be submitted twice under two different identities. See
  `order-placement-idempotency`.
- Orders BLUE placed remain live at the venue after the swap. Ownership of those working
  orders must transfer explicitly as part of state synchronization; anything the incoming
  instance does not know about becomes an orphan that nobody cancels or manages.
- Fills for BLUE-originated orders can arrive *after* the swap. The execution/drop-copy
  consumer must route them into the now-live instance's position manager, keyed by order
  ID, rather than to whichever instance submitted them.

## 5. Engineering standard — reversibility

*Recommended practice, not a regulatory requirement.*

- **Rollback is not a pointer swap.** The moment GREEN sends its first order, BLUE's book
  is stale. Restoring routing to BLUE without reconciling the delta gives control to a
  strategy holding a wrong position, which it will then trade against. `rollback()`
  therefore reconciles state backwards from the live slot to the target and refuses the
  rollback when that reconciliation fails.
- **A hot standby is not automatically a current standby.** Keeping BLUE subscribed to
  market data while suppressing its signal output keeps its *market* view fresh; it does
  nothing for its *position* view. Fills are not in the market data feed. If you want a
  standby whose book stays current, it must consume the execution report / drop-copy
  stream as well — and that is a design decision with its own reconciliation failure
  modes, not a free property of running two instances.
- **Do not publish a rollback latency target that the reconciliation step cannot meet.**
  Rollback time is dominated by state reconciliation and by the venue round trip for any
  corrective orders, not by the pointer write. Measure it in your own environment before
  committing to a number in a runbook or an SLA.
- **When rollback is refused, halt — do not force.** The regulated, rehearsed primitive
  for "stop trading now" is the kill switch (EU firms: RTS 6 Art. 12). Forcing routing
  onto a slot with an unreconciled book is strictly worse than being flat.

## 6. Engineering standard — resource isolation

*Recommended practice, not a regulatory requirement.*

- The incoming instance's initialization (JIT compilation, cache warming, risk-model
  construction, historical replay) is CPU- and memory-intensive and must not degrade the
  live instance. On latency-sensitive systems this usually means dedicated pinned cores,
  ideally on a separate NUMA node, with only the intended shared-memory regions mapped.
- Quantify the isolation before relying on it. "Separate process" is not isolation if the
  two instances contend for the same cores, the same NIC queues, or the same feed handler.

## 7. Engineering standard — auditability

*Recommended practice, which for in-scope EU firms also carries the Art. 5(7) obligation
in Section 1.*

- Record refused operations, not only successful ones. A rollback that was blocked is
  precisely the event a post-incident review needs to find.
- Record overrides distinguishably. A forced rollback that reads like a clean one in the
  log defeats the purpose of having the guard.
- Record ordering: the deployment history's list order is authoritative. Wall-clock
  timestamps are for human reading and can move backwards across an NTP correction.
- Retention is set by your applicable regime (EU firms should consult their MiFID II
  record-keeping retention requirements with their compliance function); this skill does
  not assert a retention period.
