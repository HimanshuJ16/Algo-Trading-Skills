# Workflows for Post-Collapse Exchange Counterparty Risk

## 0. Scope the audit before scoring

One audit covers **one venue at one point in time**. Record, alongside the
verdict: the audit date, the venue's legal entity and jurisdiction (not its brand
name — the entity you contract with is the one that goes into insolvency), and
the provenance of every input.

Provenance is the part that decays silently. Tag each input as *verified by us*,
*attested by a third party*, or *asserted by the venue*. The verdict inherits the
weakest tag, and nothing downstream will remind you of that.

## 1. Establish the reserve figure and its date

1. Obtain the venue's published reserve and liability figures, and the snapshot
   timestamp they refer to.
2. Verify the PoR cryptographically before trusting the ratio — rehash an
   inclusion path, audit the branch for negative balances, and reconcile the
   root sum against the declared liability total. Use
   `exchange-proof-of-reserves-verification`; do not copy a dashboard figure.
3. Compute `proof_of_reserves_ratio` as verified on-chain reserves ÷ declared
   client liabilities, as a **ratio** (1.05 == 105%).
4. Compute `por_snapshot_age_days` from the snapshot timestamp to the audit
   date. If you cannot date it, pass `None` — do not guess. An undated ratio is
   scored as `POR_SNAPSHOT_UNDATED`, which is the honest outcome.

**Failure mode to avoid:** a reserve figure with no matching proof of
*liabilities*. Reserves are only meaningful against the total they must cover; a
wallet snapshot alone bounds nothing.

## 2. Measure native-token collateral concentration

1. Take the total collateral your desk has posted at the venue.
2. Take the portion held in the venue's own token, or in tokens whose value is
   materially a function of the venue's solvency (an affiliate token counts).
3. `native_token_collateral_pct` = (2) ÷ (1), as a **fraction**.

Mark-to-market this at a stressed price, not the screen price. The FTT position
that broke Alameda was not large at a stressed bid; it was large at a screen
price that only existed while the venue was solvent. Collateral that reprices to
near-zero in exactly the scenario you are hedging is not collateral.

## 3. Determine the settlement arrangement

Set `uses_off_exchange_settlement=True` only if **this desk's** collateral is
actually held off-venue under a live tri-party arrangement — not because the
venue advertises support for one, and not because another desk at the firm uses
it.

Then record separately, outside this engine:

- Which entity holds the collateral, and under what law.
- Whether the tri-party agreement has been reviewed for enforceability in the
  relevant insolvency forum.
- The settlement cadence, and therefore the size of the window in which
  obligations accrue unsettled.
- Whether unrealised PnL sits with the venue (it usually does).

The engine scores OES as a partial mitigation for exactly these reasons. See
`references/standards.md` for what the providers do and do not claim.

## 4. Determine attestation state — today

`has_independent_attestation=True` requires a *current* third-party attestation
covering reserves. Set it False if the report has been withdrawn, if the provider
has ceased crypto assurance work, or if the report predates a material corporate
event at the venue. Attestation state is a variable, not a property: check it at
each audit rather than carrying the last answer forward.

## 5. Compute NAV exposure

`nav_exposure_pct` = capital deployed at this venue ÷ total fund NAV, as a
fraction, measured at the audit date. Include collateral, free balance, and open
position margin. Aggregate exposure across venues belongs to
`counterparty-and-broker-concentration-risk`; this engine sees one venue at a
time and cannot detect that five venues at 19% each leave you 95% exposed to the
centralised-venue failure mode as a class.

## 6. Score and decide

Run `ExchangePostCollapseRiskEngine.audit_exchange_counterparty_risk`. Two
de-risking paths exist and they mean different things:

| Path | Trigger | Meaning | Recommendation |
|---|---|---|---|
| **Disqualification** | Coverage below the minimum, or native-token collateral above `hard_native_token_trigger` | Venue not usable at any size | 100% of venue capital |
| **Trimming** | Total score ≥ `derisk_score_threshold`, no disqualifier | Venue usable at reduced size | Trim to `derisk_residual_nav_pct` |

Read `recommended_capital_withdrawal_pct` as a percentage **of the capital at
that venue**. It is 0.0 both when nothing is deployed and when exposure is
already inside the residual target — in neither case does it mean the venue
passed. Gate admissibility on `is_derisking_triggered`, never on the percentage.

A soft-cap breach on its own does not de-risk (6% native-token collateral scores
25 against a threshold of 40). If your mandate says otherwise, set
`hard_native_token_trigger` down to the soft cap rather than assuming the default
behaves that way.

## 7. Execute de-risking

1. Confirm withdrawal destinations against the whitelist before initiating —
   `exchange-withdrawal-whitelist-enforcement`.
2. Send a test transaction first for any large or first-time destination —
   `test-transaction-verification-before-large-transfers`.
3. Expect the withdrawal path itself to degrade under stress. A venue in
   difficulty throttles, queues or halts withdrawals, so a recommendation
   produced at the moment the market notices is the least likely to execute. The
   value of this gate is in the audits that happen *before* the queue forms.
4. Unwind open positions before pulling collateral, not after; withdrawing
   margin against live positions triggers liquidation.
5. Log the report — score, findings, inputs and their provenance — as the
   decision record.

## 8. Re-audit on a cadence and diff

Persist every audit. Review the series, not the point:

- A coverage ratio that drifts down across publications.
- A snapshot age that keeps growing between publications.
- Native-token collateral rising as the venue offers fee rebates for posting it.
- An attestation provider that changes, or disappears.
- Reserves that appear only around publication dates.

Any of these is a stronger signal than a single passing verdict.
