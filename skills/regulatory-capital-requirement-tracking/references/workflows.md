# Workflows for Regulatory Capital Requirement Tracking

Sources for every threshold named here are in `standards.md`.

## 1. Establish which requirement components apply — once, then on every change of permissions

1. Identify the regime and the firm's permissions. Under 15c3-1(a)(2) the dollar
   minimum is USD 250,000 for a firm carrying customer accounts, USD 100,000 for
   a dealer or a firm exempt from Rule 15c3-3, USD 50,000 for an introducing
   broker. Under MIFIDPRU 4.4.1R the PMR is GBP 750,000 for a firm dealing on own
   account, GBP 150,000 or GBP 75,000 for narrower permissions.
2. Compute the ratio or activity-based components yourself — aggregate
   indebtedness under 15c3-1(a)(1)(i), aggregate debit items under (a)(1)(ii),
   fixed overheads under MIFIDPRU 4.5.1R ("one quarter of the firm's relevant
   expenditure during the preceding year"), K-factors under MIFIDPRU 4.6. The
   engine does not compute these.
3. Name each component after the rule it comes from, e.g.
   `"MIN_DOLLAR_(a)(2)(i)"`, `"AI_RATIO_(a)(1)(i)"`, `"PMR"`, `"FOR"`, `"KFR"`.
   The name is what `report.binding_component` returns, so it is what an analyst
   reads when asking why the floor moved.
4. Construct `CapitalRequirementSpec` with `AGGREGATION_GREATER_OF` (the
   default). Use `AGGREGATION_SUM` only where the regime genuinely stacks — a
   minimum with a conservation buffer on top — and record why.
5. Re-run this step whenever permissions, the FOCUS-report basis, or the
   election between the basic and alternative standards changes. A stale spec is
   a wrong floor that looks authoritative.

## 2. Assemble the balance sheet — daily, at minimum

1. `total_assets` — total, unfiltered. The engine deducts non-allowable assets
   itself.
2. `total_liabilities` — total, **excluding** anything subordinated under a
   satisfactory subordination agreement (15c3-1(c)(2)(ii)).
3. `non_allowable_assets` — 15c3-1(c)(2)(iv), "fixed assets and assets which
   cannot be readily converted into cash".
4. `securities_haircuts` — 15c3-1(c)(2)(vi) percentage deductions on securities
   positions.
5. `qualifying_subordinated_debt` — only agreements that actually satisfy
   Appendix D. If the agreement is unexecuted or repayment falls inside the
   notice period, it is an ordinary liability and belongs in step 2.
6. Round every figure to your reporting precision before constructing. The
   engine does not round, and its display formatting is round-half-even.

## 3. Evaluate

```python
engine = RegulatoryCapitalTrackerEngine(spec)
report = engine.evaluate_capital_adequacy(components)
```

`CapitalInputError` from either constructor is a **failed** check, not a skipped
one. Do not catch it and continue with yesterday's report.

## 4. Act on the status

| Status | Meaning | Obligation (US broker-dealer) |
|---|---|---|
| `COMPLIANT` | Net capital at or above 120% of the requirement | Record and retain the computation |
| `WARNING_BUFFER_BREACHED` | At or above the floor but below 120% | 17a-11(b)(3): notice within 24 hours. Escalate to the FinOp; halt discretionary capital consumption |
| `CAPITAL_DEFICIT` | Below the floor | 17a-11(a)(1): notice the same day. Conducting a securities business while deficient is a continuing violation of 15c3-1(a) |

`report.regulatory_notice` carries the applicable text for mapped jurisdictions.
`None` means the jurisdiction is unmapped in this module — your own regime's
notification rules still apply in full.

## 5. Escalate on the binding component, not just the headline

`report.binding_component` names what is actually constraining the firm. A
requirement that moves from `PMR` to `FOR` means fixed overheads have grown past
the permanent minimum — a business fact, not a market one, and one that will not
reverse on its own. Trend it.

## 6. Retain

Persist `report.audit_notes` verbatim alongside the inputs that produced it. It
names the status, both amounts, the binding component, the aggregation mode, the
headroom, the ratio, the early-warning line, and the notification rule — which
is what reconstructs the computation for an examiner. Retention periods are in
`record-retention-periods-by-jurisdiction`.

## 7. Reconcile before you rely on it

Before trusting the engine on today's numbers, reproduce a previously filed net
capital figure from the same inputs. A discrepancy is either a modelling error
here or a classification disagreement in your books. Both are worth finding
before an examiner finds them.
