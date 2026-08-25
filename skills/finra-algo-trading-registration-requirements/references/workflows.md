# Deep Workflow Reference — finra-algo-trading-registration-requirements

Full technical procedure behind `SKILL.md`. Reference implementation:
`scripts/finra_algo_trading_registration_requirements.py`; tests:
`scripts/test_finra_algo_trading_registration_requirements.py`.

Citations are carried in `references/standards.md`; this file assumes them.

## Full Procedure

### 0. Establish the registration snapshot before any commit is audited

- `DeveloperCredentials` is a copy of CRD / FINRA Gateway state. Refresh it on a
  cadence the firm can defend; a gate reading a snapshot from last quarter cannot
  see a registration terminated last week.
- `is_series_57_active` must mean *the registration is currently effective*, not
  *this person once passed Series 57*. A registration lapsed two or more years
  requires requalification (Rule 1210.08).
- Populate `is_ce_inactive` from the CE status field, not inferred. Rule 1240(a)
  makes CE inactivity a prohibition on functioning in a registered capacity.
- Populate `is_sie_grandfathered` for anyone registered as a Securities Trader
  before 1 October 2018. They have no SIE exam record and are deemed to have
  passed it; without this flag the gate blocks the firm's most senior traders.
- Populate `is_general_securities_principal` (Series 24) so a Securities Trader
  Principal is recorded as such under Rule 1220(a)(7).

### 1. Firm-level applicability

`FinraAlgoRegistrationEngine(is_finra_member=...)`. Rule 1220 binds associated
persons of FINRA members. A non-member returns `OUT_OF_SCOPE_NOT_FINRA_MEMBER`
for every change; do not repurpose the codes to mean something else.

### 2. Security-type classification

- Covered: `EQUITY`, `EQUITY_OPTION`, `PREFERRED`, `CONVERTIBLE_DEBT`.
- Enumerated non-covered: `CORPORATE_DEBT`, `MUNICIPAL`, `TREASURY`, `FUTURE`,
  `FUTURES_OPTION`, `FX`, `CRYPTO`.
- Anything else raises `ValueError`. This is deliberate. The failure mode of a
  permissive default is a covered equity algorithm slipping through under a
  house instrument code nobody mapped; the failure mode of raising is a red CI
  job and a five-minute mapping change.
- A strategy trading more than one asset class is classified by the covered leg.
  Audit the equity leg as a covered change rather than averaging the two.

### 3. System-behaviour classification

| Token | Meaning | In scope? |
|---|---|---|
| `GENERATES_OR_ROUTES_ORDERS` | Generates orders, routes with discretion, or emits order-related messages such as cancellations | Yes |
| `SOLELY_ROUTES_ENTIRE_ORDERS` | Standard router sending orders in their entirety to a market center | No |
| `IDEA_GENERATION_ONLY` | Produces signals or allocations; cannot emit orders or order-related messages | No |

The two exclusions are narrow and easy to lose. A router that decides price or
size, uses parent/child orders, or chooses displayed versus non-displayed
interest is *not* solely routing. An idea engine wired to an order gateway is
*not* idea-only. Re-classify the system when either changes — that
re-classification is itself the compliance event.

### 4. Activity classification

Registrable (`REGISTRABLE_ACTIVITIES`):

- `DESIGN`, `DEVELOPMENT`, `SIGNIFICANT_MODIFICATION` — the three disjunctive
  prongs of the rule. Treating only the third as the trigger is the single most
  consequential misreading, because it exempts new algorithms entirely.
- `THIRD_PARTY_DIRECTION` — directing a vendor's design, development or
  significant modification.
- `PERFORMANCE_MONITORING` — monitoring or reviewing the algorithm's
  performance, including for an unmodified off-the-shelf algorithm.
- `DAY_TO_DAY_SUPERVISION` — the second prong of the rule.

Not registrable (`NON_REGISTRABLE_ACTIVITIES`): `MINOR_MODIFICATION`,
`INFRASTRUCTURE_INTEGRATION`, `TESTING_LINKAGES`.

`author_primarily_responsible=False` takes a change out of scope regardless of
activity — the junior-developer case. Set it from the firm's documented
responsibility map, never from repository statistics such as lines changed.

### 5. Qualification evaluation

A person is a usable Securities Trader when **all** hold:

1. `is_series_57_active`
2. `is_sie_active or is_sie_grandfathered`
3. `not is_ce_inactive`

Defects are reported individually (`AUTHOR_NO_ACTIVE_SERIES_57`,
`AUTHOR_SIE_NOT_SATISFIED`, `AUTHOR_CE_INACTIVE`) and accumulate; an unknown
person yields `AUTHOR_NOT_IN_REGISTRY` alone, which is a different finding from
a known-but-unregistered person and should be routed differently — one is a data
gap, the other is a violation.

Supervisor basis is recorded as `SECURITIES_TRADER_PRINCIPAL`,
`SECURITIES_TRADER`, `NOT_QUALIFIED` or `NOT_IDENTIFIED`. Notice 16-21 accepts
either registered capacity for the person supervising the covered activities.

### 6. Decision and gating

| Status | Meaning | `blocks_deployment` |
|---|---|---|
| `COMPLIANCE_APPROVED` | In scope, everyone required is registered | `False` |
| `REGISTRATION_VIOLATION_BLOCKED` | In scope, at least one violation | `True` |
| `OUT_OF_SCOPE_RULE_1220B4` | This rule does not reach the change | `False` |

Gate CI on `report.blocks_deployment`. Comparing `cicd_gate_status` to
`COMPLIANCE_APPROVED` and blocking otherwise turns every out-of-scope change —
every futures commit, every data-feed tweak — into a failed build, and the desk
will disable the gate.

`requires_change_management_review` stays true for any change to an algorithmic
trading strategy, including one out of registration scope, because Notice 15-09
change management is not conditioned on the registration prong. Note the limit:
for an instrument outside FINRA's reach the flag is a firm-policy signal, not a
FINRA expectation.

### 7. Retention

Persist every report — approved, blocked and out of scope — to an append-only
sink meeting Rule 4511(c) / SEA Rule 17a-4. `audit_trail` is an in-memory
reference adapter that does not survive a restart.

## Integration notes

- **Concurrency.** The engine holds mutable state (`personnel_registry`,
  `_audit_trail`) with no lock. Use one engine per worker, or serialise access,
  or re-instantiate per request. Do not share one instance across threads while
  mutating the registry.
- **Identifier matching.** Personnel lookup and self-approval detection both
  case-fold and strip. `dev_a` and `DEV_A` are the same person, deliberately:
  otherwise one person's two accounts satisfy a two-person check.
- **Legacy callers.** `is_significant_modification` / `modifies_order_routing_logic`
  still work and derive the new fields, but they cannot express initial design or
  development and emit a warning. Migrate to `author_activity`.

## Known limitations

- No CRD or FINRA Gateway integration. Snapshot freshness is the caller's
  problem and this module cannot detect staleness.
- "Primarily responsible" and "significant modification" are inputs, not
  inferences. The engine cannot read a diff and decide either question; that
  determination is a documented human judgement.
- Registration status is modelled as booleans, not as dated registration
  periods, so the engine cannot answer "was this person registered on the date
  of commit X?" — only "are they registered now?". A firm reconstructing a
  historical exam response needs dated CRD records alongside the retained
  reports.
