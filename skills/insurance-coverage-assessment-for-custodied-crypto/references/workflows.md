# Workflows for Custody Insurance Assessment

## 1. Establish what each policy actually attaches to

Before any arithmetic, resolve the tier mapping from the binder — never from the policy's
name or a marketing page.

- Obtain the insurance binder and the executed custody agreement.
- For each policy, record: form (specie / crime / other), limit, retention, occurrence vs
  aggregate basis, wallet tiers named in the wording, and the named insured.
- Resolve the tier mapping explicitly. A crime form covering cold storage and a specie form
  covering hot wallets are both real market positions.
- Where a tier is uninsured, record `0.0` rather than omitting it. Several major custodians
  insure cold storage only.
- Record whether the firm is a named insured or loss payee. Absent an endorsement, the firm's
  claim runs against the custodian, not the insurer.

## 2. Audit the hot tier

- Recovery on an isolated loss is `max(0, min(hot AUM, hot limit) − hot retention)`.
- Obtain `total_custodian_hot_aum_usd` if the custodian will disclose it. Hot balances sit in
  omnibus wallets and dilute exactly as cold balances do; omitting the pool models the tier
  as undiluted, which is optimistic, and the engine flags it.
- Compare the resulting ratio against the firm's hot-tier threshold.

## 3. Audit the cold tier and its pro-rata dilution

- Net proceeds available to the pool are `max(0, cold limit − cold retention)`.
- Dilution factor is `min(1, net proceeds ÷ total custodian cold AUM)`.
- Pooled recovery for the firm is `firm cold AUM × dilution factor`. The retention is
  absorbed once at the tower level and is deliberately **not** deducted a second time from
  the firm's share.
- Set `cold_limit_is_dedicated_to_firm=True` only against a written endorsement reserving a
  limit to the firm (e.g. excess specie naming it Dedicated Customer Loss Payee).
- Treat `total_custodian_cold_aum_usd` as an estimate unless the custodian has disclosed it
  in writing; most decline on security grounds. Re-verify at each review.

## 4. Bracket the recovery and size the shortfall

- Isolated loss (upper bound): only this firm is hit, so the per-occurrence limit is not
  shared.
- Pooled loss (lower bound): the whole book is hit and the limit splits pro-rata.
- Plan against the pooled figure; quote both.
- Where the retention exceeds the firm's own balance the ordering inverts — isolated recovery
  falls to zero while pooled recovery remains positive. Treat that as a signal that the firm
  is too small for the custodian's retention, and negotiate a dedicated limit or reduce the
  balance held.
- Net uninsured capital is total firm AUM less modelled recovery, reported in USD and
  percentage under each scenario.

## 5. Escalate and record

- `CRITICAL_HOT_WALLET_UNINSURED`: hot-tier coverage below threshold. The hot tier is the
  actively exploitable one; escalate before adding balance.
- `PARTIALLY_INSURED_SHORTFALL`: net pooled coverage below threshold.
- `FULLY_INSURED`: both thresholds met, subject to the recorded assumptions.
- Reproduce the report's `assumptions` list wherever a figure from it is quoted. Each one can
  move the number materially.

## 6. Re-run on cadence

Custody insurance programmes renew annually and limits, retentions, pool size and tier scope
all move between renewals. Re-run this assessment at renewal, on any material change in the
firm's balances, and whenever the custodian announces a programme change. Pair it with
`third-party-custody-audit-report-review-cadence` and
`custody-solution-vendor-due-diligence-checklist`, which cover the control and
bankruptcy-remoteness questions insurance does not answer.
