---
name: multi-model-ensemble-weight-decay
description: Use when live capital is split across several forecasting models
  (XGBoost, LightGBM, LSTM, Ridge) whose relative skill drifts with the regime,
  to discount each model's loss and information coefficient exponentially,
  reweight them with a numerically stable softmax, and demote anti-predictive or
  floor-breaching models out of the book entirely rather than letting a static
  1/M allocation carry a decayed model indefinitely
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- ensemble
- weight-decay
- softmax-weighting
- exponential-decay
- model-demotion
- information-coefficient
brokers_frameworks:
- Python Standard Library
- EWMA (RiskMetrics convention)
- Hedge / Exponentially Weighted Average Forecaster
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when an ensemble of $M$ forecasting models feeds a live allocation and their relative skill is **non-stationary** — a model that carried the book last quarter is not the one carrying it now. A static $\mathbf{w} = [1/M, \dots, 1/M]$ has no mechanism to notice, so a decayed model keeps its full share until someone reads a report and intervenes by hand.

Two devices do the work, and they answer different questions:

- **Exponential recency decay** ($\lambda$) answers *how much of the past counts*. The EWMA recursion $\bar{L}_{m,t} = \lambda \bar{L}_{m,t-1} + (1-\lambda) L_{m,t}$ has an exact memory: its half-life is $\ln(0.5)/\ln(\lambda)$ periods — 13.51 at $\lambda = 0.95$, 11.20 at $0.94$, 68.97 at $0.99$. Pick $\lambda$ from the half-life you want, not by feel.
- **Softmax reweighting** ($\beta$) answers *how sharply skill converts into capital*. $w_m \propto \exp(-\beta \bar{L}_m)$ is the Hedge / exponentially weighted average forecaster, and $\beta$ is its learning rate.

**Demotion is a separate mechanism from weighting, and this is the point of the skill.** Softmax always returns a strictly positive weight for every model, no matter how bad — $\exp(-\beta \bar{L})$ is never zero. A model that has stopped working therefore keeps a small live allocation forever unless something removes it. The weight floor and the negative-IC circuit breaker are that something.

## When NOT to Use

- **As the first-pass ensemble weighting.** This is a *time-varying* overlay, and it will happily concentrate on whichever model most recently got lucky. If the question is how to combine signals without overfitting the historical noise in the first place — shrinkage toward $1/N$, non-negativity, per-model caps — start with `ensemble-signal-combination-without-overfitting` and add decay afterwards.

- **When the intent is to select one model per regime.** Blending and switching are different things. A softmax with a large $\beta$ approximates a switch but keeps a ragged tail of small allocations and reacts with a lag set by $\lambda$. Use `regime-detection-for-strategy-switching` for an actual switch.

- **When the models are near-duplicates.** The weights are computed per model with no notion of correlation, so five variants of the same gradient-boosted tree collectively receive five times the weight of one genuinely independent model. Deduplicate the roster first; correlation-aware allocation is `risk-parity-allocation-across-strategies`.

- **As a model health monitor.** This reads loss and IC that a caller computes elsewhere. It does not detect feature drift, a stale training set, or a broken data feed — a model fed a frozen feature vector can post an excellent loss. See `model-staleness-detection` and `feature-importance-drift-monitoring`.

- **On a single reweighting call.** With no `previous_decayed_*` state the recursion is seeded from the current reading, so $\bar{L} = \lambda L + (1-\lambda) L = L$ and $\lambda$ has no effect at all. The decay is a property of the *sequence* of calls; one-shot use is a plain softmax over current losses.

## Prerequisites

- Per-model telemetry for the current period: `model_id` (unique — duplicates are rejected, not merged), `recent_loss` (non-negative, finite), `recent_ic`, `days_active`.
- **The carried decay state**, `previous_decayed_loss` and `previous_decayed_ic`, persisted by the caller between periods. This engine is stateless; if the state is not fed back, no decay happens and the skill silently degrades to a per-period softmax.
- Loss and IC measured on the **same** forward window that the weights will govern, both computed out-of-sample. Weighting on in-sample loss allocates capital to the best overfitter.
- A `min_weight_floor` strictly below $1/M$. At or above the equal-weight share every model is demotable even when all perform identically, which is rejected as a configuration error.

## Workflow

1. **Choose $\lambda$ from a half-life, not a feeling.** $\lambda = 0.95$ forgets half its weight in 13.51 periods. RiskMetrics fits $0.94$ for daily and $0.97$ for monthly financial series (Technical Document, 4th ed., 1996, App. C) — a reasonable anchor, though it was fitted for volatility, not model loss.
   - **Decision point — match the half-life to how fast the alpha actually decays, not to how fast you want to react.** A half-life shorter than the horizon on which model skill genuinely changes converts estimation noise into turnover: the weights chase last period's luck and the ensemble pays the spread on every rotation.

2. **Advance both EWMAs — loss and IC — every period, regardless of `weighting_method`.** The IC circuit breaker needs a discounted IC even when weighting runs on loss.

3. **Compute the softmax with the max-shift.** Score by $-\beta \bar{L}_m$ (loss) or $+\beta \overline{IC}_m$ (IC), subtract $\max_j s_j$, then exponentiate.
   - **Decision point — the shift is not an optimisation, it is the difference between a number and a crash.** $\exp$ of an unshifted score raises `OverflowError` on a large positive score and underflows *every* term to `0.0` on large negative ones, making the normalising denominator zero. Shifting is an exact identity — $\text{softmax}(s - c) = \text{softmax}(s)$ — that pins the largest term at $\exp(0) = 1$, so the denominator is always $\ge 1$.

4. **Run the circuit breakers, most-specific first: warm-up, then IC, then floor.**
   - **Decision point — the IC breaker must read the *discounted* IC, never the single-period reading.** A cross-sectional IC over $N$ names has standard error $\approx 1/\sqrt{N-1}$ under the null. At $N = 100$ that is $\approx 0.10$, so for any model with a realistically small true IC the sign of one period's reading is close to a coin flip. Demoting on it discards skilled models at roughly the rate it discards broken ones, and makes the decay memory decorative.
   - **Decision point — apply the IC breaker under `EXPONENTIAL_LOSS` too.** Low loss and negative IC coexist: a model that predicts small moves in the wrong direction scores a competitive MSE. Loss-based weighting cannot see the sign error, and will hand that model the majority of the book.

5. **Renormalise the survivors.** One pass suffices — dividing by a sum $\le 1$ can only *increase* each surviving weight, so nothing that cleared the floor can fall below it afterwards. Iterating is unnecessary, not merely expensive.

6. **If nothing survives, allocate nothing.**
   - **Decision point — do not fall back to equal weights.** The all-demoted state means every model failed a breaker. Spreading capital evenly across them re-admits exactly what was just rejected, including anti-predictive models, and reports success while doing it. The engine returns `ENSEMBLE_HALTED_ALL_DEMOTED` with every weight at $0.0$ and logs at ERROR; the caller must decide between flattening, falling back to a non-ensemble source, or halting.

7. **Persist the returned `decayed_metric` and `decayed_ic` as next period's `previous_decayed_*`.**

> Full procedure: see `references/workflows.md`.
> Standards, citations, and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming softmax demotes.** It never does. $\exp(-\beta \bar{L}) > 0$ for every finite loss, so with no floor and no IC breaker the worst model in the roster still holds a live allocation. Weighting and demotion are separate mechanisms.
- **Letting a negative-IC model keep weight under loss weighting.** The single most expensive failure here: a model with a *good* MSE and an inverted sign is being paid to lose money, and the loss metric cannot see it.
- **Firing the circuit breaker on a single period's IC.** With $\mathrm{SE}(IC) \approx 1/\sqrt{N-1}$, that is a coin flip dressed as a risk control. Use the discounted IC.
- **Equal-weight fallback when everything is demoted.** It converts "no model is usable" into a full allocation across unusable models, under a SUCCESS status. If every breaker fired, the answer is no position, not an evenly spread one.
- **Unshifted `math.exp`.** A loss of 400 with $\beta = 2$ underflows every exponential to zero and the normalisation raises `ZeroDivisionError` in production, on the exact bar where a model blew up.
- **NaN telemetry.** A NaN loss propagates through `exp` and the normalisation into the weight vector, and every comparison against the floor is `False`, so the model stays `ACTIVE` holding a NaN weight. Non-finite readings must be rejected at the boundary, not weighted.
- **Duplicate `model_id`.** Weights are keyed by id. A duplicate silently overwrites the first entry's metrics — the good model inherits the bad one's loss and gets demoted in its place.
- **A `min_weight_floor` at or above $1/M$.** With 30 models a $0.05$ floor exceeds the $0.0333$ equal share, so every model is demotable no matter how well it performs. Scale the floor with the roster size.
- **Negative or zero $\beta$.** It inverts the softmax and assigns the largest weight to the *worst* model, silently, with no error and a plausible-looking weight vector.
- **Expecting $\lambda$ to do anything on the first call.** Seeded from the current reading, $\bar{L} = L$ exactly. Not a bug; not decay either.
- **Reading the reported weights as bit-exact.** They are residual-corrected to sum to $1.0$ at 6 decimal places, exactly under `math.fsum`. A naive `sum()` over many weights may land an ULP or two away.

## Verification

- **Softmax against a closed form.** Losses $0.1 / 0.5 / 2.5$, $\beta = 2.0$, $\lambda = 0.90$, floor $0.05$, no carried state: raw weights are $0.686079 / 0.308275 / 0.005646$; the third is demoted `DEMOTED_BELOW_FLOOR`; the survivors renormalise to $0.689974 / 0.310026$, which must equal the two-model logistic $e^{\beta(L_B - L_A)}/(1 + e^{\beta(L_B - L_A)}) = e^{0.8}/(1 + e^{0.8})$ to 6 places.
- **A near-miss is not a demotion.** Losses $0.1 / 0.5 / 1.2$ with the defaults ($\beta = 2.0$, floor $0.05$) give $0.640971 / 0.288007 / 0.071022$ and demote **nothing** — $0.071 > 0.05$. Confirm all three stay `ACTIVE`. (The pre-2.0 SKILL.md claimed ~80% for the leader and a demotion for the laggard on these inputs; neither reproduces.)
- **Half-life.** $\lambda = 0.5 \implies 1.0$ period exactly; $\lambda = 0.25 \implies 0.5$; $\lambda = 0.95 \implies 13.513407$; $\lambda = 0.94 \implies 11.202306$. Confirm $\lambda \in \{0, 1\}$ raises rather than returning $\infty$.
- **EWMA recursion.** `previous_decayed_loss=0.5`, `recent_loss=0.1`, $\lambda = 0.9 \implies 0.46$ exactly. Confirm the first call with no carried state returns `decayed_metric == recent_loss` for both $\lambda = 0.99$ and $\lambda = 0.10$.
- **IC breaker reads the discounted IC (regression, both directions).** `previous_decayed_ic=0.06`, `recent_ic=-0.02`, $\lambda=0.9 \implies +0.052$: stays `ACTIVE`. `previous_decayed_ic=-0.04`, `recent_ic=+0.01 \implies -0.035$: `DEMOTED_NEGATIVE_IC`. The pre-2.0 engine got both backwards. Confirm exactly $IC = 0$ demotes (the trigger is $\le 0$).
- **IC breaker under loss weighting (regression).** Losses $0.20$ / $0.19$ with ICs $+0.06$ / $-0.09$ under `EXPONENTIAL_LOSS`: the anti-predictive model must hold $0.0$ and the sound one $1.0$. The pre-2.0 engine gave the anti-predictive model $0.505$.
- **All-demoted halt (regression).** Two models, both negative IC: status `ENSEMBLE_HALTED_ALL_DEMOTED`, `active_model_count == 0`, `demoted_model_count == 2`, every weight $0.0$, logged at ERROR with a per-model reason. The pre-2.0 engine returned `ENSEMBLE_REWEIGHTED_SUCCESS`, `demoted == 0`, and $0.5 / 0.5$.
- **Numerical stability (regression).** Losses $400 / 500$ must return finite weights — the pre-2.0 engine raised `ZeroDivisionError`. IC $400$ under `IC_SOFTMAX` must return finite weights — it raised `OverflowError`.
- **Weight invariants.** Over rosters of 2, 3, 5, 7, 11, 23, 41 and 200 models, `math.fsum` of the reported weights is exactly $1.0$ and a naive `sum()` is within $10^{-12}$; every active weight clears the floor after renormalisation; every non-active weight is exactly $0.0$; and `active_model_count + demoted_model_count + pending_warmup_model_count` partitions the roster.
- **Warm-up is not demotion.** With `min_days_active=30` and a roster of one fresh model (`days_active=3`), one inverted-IC model and one seasoned model, confirm the counts split 1 active / 1 demoted / 1 pending. An ensemble where *every* model is still warming up halts with `demoted_model_count == 0` — a new ensemble must not read as a roster of broken models.
- **Negative checks.** Empty roster, blank `ensemble_id`, duplicate `model_id`, NaN/±Inf in any of the four numeric telemetry fields, a negative `recent_loss`, a non-numeric loss, negative `days_active`, $\lambda \notin [0,1]$, $\beta \le 0$ or non-finite, `min_weight_floor` outside $[0,1)$ or at/above $1/M$, `min_days_active < 1`, and an unrecognised `weighting_method` (including the correct spelling in lower case) must each raise `EnsembleWeightError` — which subclasses `ValueError` for callers written against the old contract.
- Run `python -m unittest discover -s skills/multi-model-ensemble-weight-decay/scripts` and confirm a 100% pass rate.

## Related Skills

- `ensemble-signal-combination-without-overfitting`
- `model-staleness-detection`
- `model-serving-infrastructure-ab-testing`
- `regime-detection-for-strategy-switching`
- `capital-reallocation-based-on-live-performance`
- `strategy-performance-decay-detection-vs-market-wide-decay`
- `feature-importance-drift-monitoring`
- `meta-strategy-signal-arbitration`
