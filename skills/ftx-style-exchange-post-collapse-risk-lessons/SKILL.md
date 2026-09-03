---
name: ftx-style-exchange-post-collapse-risk-lessons
description: >-
  Use when deciding whether a centralised crypto venue may hold fund capital and how
  much, against the failure modes the 2022 FTX collapse demonstrated: unbacked
  native-token collateral, undated proof of reserves and commingled custody.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: ftx-collapse-lessons, counterparty-risk, proof-of-reserves, off-exchange-settlement, crypto-custody, exchange-solvency, venue-de-risking
  brokers_frameworks: "Copper ClearLoop; Fireblocks Off Exchange; MiCA Regulation (EU) 2023/1114; PCAOB Investor Advisory 2023-03-08; Merkle Tree Proof of Reserves"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when deciding whether a centralised crypto venue may hold fund
capital, and how much. It turns a venue's reserve claim, its native-token
collateral share, its settlement arrangement and your own NAV allocation into an
auditable de-risking decision rather than a judgement call.

The six dimensions it scores are the ones the FTX record implicates, and it
reports them separately because they fail separately:

1. **Proof of Reserves coverage** — verified reserves over declared client
   liabilities.
2. **Age of that PoR figure** — a coverage ratio with no as-of date is not
   evidence about today.
3. **Native-token collateral share** — the FTT exposure. CoinDesk's 2022-11-02
   report on Alameda Research's balance sheet showed roughly USD 5.8bn of USD
   14.6bn in assets tied to FTX's own token; the run that followed is the whole
   lesson.
4. **Single-venue NAV concentration** — how much of the fund one bankruptcy can
   reach.
5. **Off-exchange settlement adoption** — whether trading collateral sits in
   venue custody at all.
6. **Independent attestation** — whether anyone outside the venue has looked.

## When NOT to Use

- **As a solvency measurement.** The engine verifies nothing. Every input is a
  figure you supply and most of them originate with the venue. A venue that
  misstates its reserve ratio produces a clean report here. Verify a PoR claim
  cryptographically with `exchange-proof-of-reserves-verification` before feeding
  a ratio in.
- **As a compliance check.** The default thresholds (100% coverage, 5%
  native-token collateral, 20% single-venue NAV) are internal risk policy. No
  regulator reviewed for this skill sets them, and none requires a centralised
  venue to publish a PoR at all. See `references/standards.md` for what MiCA
  Articles 70 and 75 do and do not require, and of whom.
- **As a one-time gate.** An attestation is not a durable property of a venue.
  Mazars published a PoR statement for Binance on 2022-12-07 and within days
  paused proof-of-reserves work for all crypto clients and removed the reports
  from its site. Re-run on a cadence and diff the inputs.
- **For non-custodial venue risk.** A DEX or an on-chain protocol fails through
  contract bugs, oracle manipulation and bridge compromise, not through
  commingled customer omnibus accounts. None of the six dimensions here applies.
- **As the sole capital control.** This gate decides venue admissibility. It does
  not size positions, monitor withdrawals, or halt trading — see
  `kill-switch-and-drawdown-circuit-breakers` and
  `withdrawal-velocity-limits-and-anomaly-detection`.

## Prerequisites

- A verified `proof_of_reserves_ratio` (a **ratio**: 1.05 == 105%) and the **age
  in days** of the snapshot it came from.
- `native_token_collateral_pct` and `nav_exposure_pct` as **fractions** (0.05 ==
  5%) despite the `_pct` suffix. Passing `5.0` for 5% raises rather than
  scoring.
- `uses_off_exchange_settlement` — True only if *this desk's* collateral is
  actually held off-venue under a tri-party arrangement, not merely that the
  venue supports one.
- `has_independent_attestation` — True only for a *current* attestation. A
  withdrawn one is False.
- Risk policy: coverage minimum, native-token soft cap and hard trigger,
  single-venue NAV cap, score threshold, residual NAV target, maximum snapshot
  age. All are constructor arguments; none is hard-coded.

## Workflow

1. **Establish the inputs before scoring, and record where each came from.**
   Anything the venue told you is a claim, not a measurement. The engine's
   verdict inherits the weakest input's provenance, and nothing downstream will
   remind you of that.
2. **Date the reserve figure.** An undated ratio is scored as a gap
   (`POR_SNAPSHOT_UNDATED`), never as a pass. The PCAOB Investor Advisory of
   2023-03-08 makes the point that these reports verify an asset type "at a
   particular moment in time"; treating a snapshot as a standing property is the
   error that makes PoR worse than useless.
3. **Score the six dimensions.** Weights total exactly 100, so the index cannot
   saturate — a venue failing coverage alone (35) is distinguishable from one
   failing everything (100). Reweight for your mandate if you disagree with the
   defaults; the engine rejects a weighting that does not total 100.
4. **Apply the two de-risking paths, which mean different things.**
   - *Disqualification*: a coverage shortfall, or native-token collateral above
     the hard trigger (default 10%). The venue is not usable at any size and the
     recommendation is 100% withdrawal.
   - *Trimming*: the total score reaching the threshold (default 40) without a
     disqualifier. The venue stays usable at a reduced size, trimmed to
     `derisk_residual_nav_pct`.
   A soft-cap breach alone does **not** de-risk: 6% native-token collateral
   scores 25, below the threshold of 40. If you want any breach to force a
   withdrawal, set `hard_native_token_trigger` to the soft cap — do not assume
   it.
5. **Read the withdrawal figure as a percentage of venue capital, not of NAV.**
   It is 0.0 when nothing is deployed at the venue and 0.0 when exposure is
   already inside the residual target. **Neither means the venue is safe.** Read
   `status` and `is_derisking_triggered` to decide admissibility;
   `recommended_capital_withdrawal_pct` only sizes the action.
6. **Act on the findings list, then re-audit.** `findings` carries one
   machine-readable code per failed dimension (`POR_SHORTFALL`,
   `NATIVE_TOKEN_CONCENTRATION`, `NAV_CONCENTRATION`,
   `NO_OFF_EXCHANGE_SETTLEMENT`, `NO_INDEPENDENT_ATTESTATION`,
   `POR_SNAPSHOT_STALE` / `POR_SNAPSHOT_UNDATED`). Persist the score and the
   findings per audit; the trend across publications is the signal, not any
   single verdict.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading a withdrawal recommendation of 0.0% as a pass.** A venue disqualified
  on native-token concentration with nothing currently deployed returns
  `EXCHANGE_DERISK_TRIGGERED` and 0.0% — there is simply nothing to withdraw. An
  automation that gates on the percentage rather than on
  `is_derisking_triggered` will happily fund that venue tomorrow.
- **Confusing Proof of Reserves with an audit.** The PCAOB advisory of
  2023-03-08 states PoR engagements "are not audits and, consequently, the
  related reports do not provide any meaningful assurance". A wallet snapshot
  with no proof of *liabilities* bounds nothing: reserves are only meaningful
  against the total they are supposed to cover.
- **Treating off-exchange settlement as zero counterparty risk.** OES moves the
  collateral principal out of venue custody; it does not move unrealised PnL,
  the settlement window, or the settlement operator's own operational risk.
  Copper describes ClearLoop as reducing counterparty exposure, not removing it.
  Scoring OES as a full exemption re-creates the exposure it was adopted to
  avoid.
- **Accepting native-token collateral as ordinary collateral.** Collateral whose
  value is a function of the venue's solvency is not collateral against that
  venue failing — it is a levered bet on it. The 5% soft cap is deliberately far
  below the FTT concentration that actually broke Alameda.
- **Passing percentages where fractions are expected.**
  `native_token_collateral_pct=5` means 500%, and `proof_of_reserves_ratio=105`
  would read as a 105x-reserved venue that clears every check. The engine
  rejects both, but an agent editing these values without reading the units will
  hit them.
- **Recording an attestation as current because it once existed.** The Mazars
  withdrawal in December 2022 removed the reports from the web while the
  underlying venues kept operating. `has_independent_attestation` describes
  today, not history.
- **Assuming MiCA segregation covers your venue.** Articles 70 and 75 bind CASPs
  authorised in the EU. An offshore venue serving you through a non-EU entity is
  outside that perimeter, and the engine does not model jurisdiction at all.

## Verification

- Audit a clean venue (PoR 1.05, native token 0.02, OES enabled, NAV 0.15,
  current attestation, snapshot 30 days old) and confirm
  `VENUE_RISK_ACCEPTABLE`, `risk_score_0_to_100 == 0.0`, an empty `findings`
  list, and a 0.0% withdrawal recommendation.
- Audit an FTX-shaped venue (PoR 0.85, native token 0.35, no OES, NAV 0.40, no
  attestation, undated snapshot) and confirm `EXCHANGE_DERISK_TRIGGERED`, a
  score of exactly `100.0` (35 + 5 + 25 + 15 + 12 + 8), six findings, and a
  100.0% withdrawal recommendation.
- Disqualify a venue on native-token concentration while `nav_exposure_pct` is
  `0.0` and confirm the report returns 0.0% rather than raising
  `ZeroDivisionError`.
- De-risk a venue by score at `nav_exposure_pct=0.02` against a 5% residual
  target and confirm the recommendation is `0.0`, not a negative percentage.
- Confirm the score threshold fires on `>=`: coverage clean, undated snapshot,
  NAV 0.50, no OES, no attestation scores exactly 40.0 and recommends
  withdrawing 90.0% ((0.50 − 0.05) / 0.50).
- Submit `float("nan")` native-token collateral, `5.0` where a fraction was
  meant, `105.0` as a coverage ratio, `"no"` as an attestation flag, or a blank
  `venue_id`, and confirm `ExchangeCounterpartyRiskError` rather than a verdict.
- Construct the engine with `hard_native_token_trigger` below
  `max_native_token_ratio`, or `derisk_residual_nav_pct` above
  `max_single_venue_nav_pct`, and confirm it refuses to build.
- Run `python -m unittest discover -s skills/ftx-style-exchange-post-collapse-risk-lessons/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `exchange-proof-of-reserves-verification`
- `counterparty-and-broker-concentration-risk`
- `custody-solution-vendor-due-diligence-checklist`
- `third-party-custody-audit-report-review-cadence`
- `hot-cold-wallet-split-for-trading-bots`
- `withdrawal-velocity-limits-and-anomaly-detection`
