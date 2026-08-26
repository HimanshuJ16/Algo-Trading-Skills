# Deep Workflow Reference — monte-carlo-strategy-robustness-testing

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Ingest and Validate Historical Trade Returns:**
   - Extract the sequence of trade returns $R = [r_1, r_2, \dots, r_N]$ as
     **fractional returns on account equity** ($0.02 = +2\%$).
   - If the trade log holds absolute currency P&L, divide by the account equity at
     the time of each trade *before* passing it in. There is no way for the engine
     to distinguish a $-1500.0$ dollar loss from a $-150{,}000\%$ return other than
     by rejecting anything $\le -1.0$, which it does.
   - Reject NaN and Inf at the boundary. A NaN silently defeats every `>` comparison
     in a peak/drawdown loop, so an unvalidated engine reports a shallow drawdown
     and a passing verdict on a corrupted log.
   - Confirm $N \ge 5$ (hard floor) and prefer $N \ge 30$; resampling cannot add
     information the trade log does not contain.
   - Record `seed`, `num_simulations`, `initial_capital`, `max_drawdown_limit`, and
     `max_risk_of_ruin_pct` alongside the result. These five values plus the trade
     log fully determine the output.

2. **Execute Trade Sequence Permutations (Shuffling):**
   - Perform $M \ge 500$ permutations of $R$ without replacement, rebuilding the
     compounded equity curve $E_k = E_{k-1}(1 + r_k)$ for each and recording the
     maximum peak-to-trough drawdown $\max_k (P_k - E_k)/P_k$, where $P_k$ is the
     running peak.
   - Measure drawdown against the **running peak**, not against initial capital. An
     equity curve that rises to $121{,}000$ and falls to $108{,}900$ never dips
     below its $100{,}000$ start but has suffered a $10\%$ drawdown.
   - Do **not** interpret terminal equity from this stage. $\prod_i (1 + r_i)$ is
     order-invariant, so every permutation ends at the same equity. The reference
     implementation sets `final_equity_is_path_invariant=True` on this result.

3. **Execute Bootstrap Resampling:**
   - Sample $N$ trades with replacement, $M$ times (Efron 1979).
   - This stage produces genuine dispersion in both drawdown and terminal wealth.
   - Sanity check: bootstrap $DD_{99}$ should be at least as deep as shuffling
     $DD_{99}$, because bootstrap can draw the worst trade repeatedly while
     shuffling holds the multiset fixed. A violation indicates a bug or too few paths.

4. **Inject Execution Noise:**
   - Perturb each return by $\epsilon \sim \mathcal{N}(\mu_{\text{cost}}, \sigma_{\text{noise}})$,
     preserving the original trade order so that execution sensitivity is isolated
     from ordering effects.
   - Calibrate $\sigma_{\text{noise}}$ from the desk's own realized fill dispersion
     (`transaction-cost-analysis-tca-integration`). The reference implementation
     requires this argument rather than defaulting it, because no default is defensible.
   - Run at least one pass with $\mu_{\text{cost}} < 0$ (e.g. $-0.0005$ for a 5 bps
     per-trade cost). Symmetric zero-mean noise is a sensitivity test only; it is as
     likely to improve a fill as to worsen it, so the median edge survives it almost
     unchanged and the test proves little.
   - A perturbed return can fall to or below $-100\%$. Treat that path as hitting
     the absorbing barrier (equity $0$, drawdown $100\%$) rather than letting the
     recursion compound into negative equity.

5. **Calculate Quantiles & Breach Probability:**
   - Sort the $M$ path drawdowns and take the nearest-rank quantiles
     $x_{(\lceil 0.95M \rceil)}$ and $x_{(\lceil 0.99M \rceil)}$.
   - Compute the drawdown-breach probability $P(DD_{\max} \ge \text{Limit})$ as the
     share of paths breaching. This is reported as `risk_of_ruin_pct`; it is **not**
     the classical probability of capital depletion (see `references/standards.md`).

6. **Strategy Deployment Sign-off:**
   - Require $DD_{95} \le \text{Limit}$ **and** breach probability $\le$ the
     configured ceiling (default $1.0\%$) in **all three** modes.
   - Record which mode was the binding constraint. Failing only the noise stage is
     an execution-cost problem; failing only bootstrap points at dependence on a
     small number of outsized winners.
   - Re-run with an identical seed and confirm the result reproduces before the
     sign-off is recorded (`backtest-determinism-and-reproducibility`).

## Failure Modes Observed in Production

- **Optimistic bias from broken loss clustering.** Shuffling and IID bootstrap
  assume exchangeability. When a strategy's losses cluster by regime, resampling
  separates them and understates the realized drawdown. This is the single most
  important limitation of the method and it errs in the unsafe direction.
- **Silent NaN pass-through.** An unvalidated peak/drawdown loop treats a NaN trade
  as a no-op and returns a passing robustness verdict on a broken trade log.
- **Units confusion.** Absolute P&L fed in as fractional returns drives equity
  negative, after which reported "drawdown" exceeds $100\%$ and sign flips make
  subsequent compounding meaningless.
- **Non-reproducible sign-offs.** Seeding the process-global RNG inside the engine
  makes the second run on the same object draw from a different state, and silently
  reseeds the caller's own random stream.
- **Insufficient sample size.** Below $\sim 500$ paths, $DD_{95}$ moves materially
  between runs; see the order-statistic error bound in `references/standards.md`.
- **Monte Carlo as a substitute for out-of-sample validation.** Resampling an
  overfitted trade log yields tight, confident quantiles around a non-existent edge.

## Production Implementation Reference

- Reference code: `scripts/monte_carlo_engine.py`
  (`MonteCarloRobustnessEngine`, `MonteCarloResult`, `MonteCarloError`).
- Simulation entry points: `run_sequence_shuffling`, `run_bootstrap_resampling`,
  `run_noise_injection`.
- Automated unit tests: `scripts/test_monte_carlo_engine.py`
  (`python -m unittest discover -s skills/monte-carlo-strategy-robustness-testing/scripts`).
