"""
Champion-Challenger A/B test evaluator for model serving infrastructure.

Two responsibilities, deliberately kept separate:

  * ``route_request`` - deterministic, stateless traffic allocation.
  * ``evaluate_ab_test_results`` - Welch's two-sample t-test over realised
    per-trade returns, and an **advisory** promotion recommendation.

The recommendation is advisory. Nothing in this module promotes a model,
cancels orders or moves capital; ``recommended_action`` is an input to a
governed change-control process, not an instruction to execute. See
``references/standards.md`` for why that separation is load-bearing.
"""

import hashlib
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence

from scipy.stats import t as student_t

logger = logging.getLogger(__name__)

# Sample variance needs at least two observations; below this the
# Welch-Satterthwaite denominator divides by (n - 1) = 0.
_MIN_OBSERVATIONS_FOR_VARIANCE = 2


class TestMode(str, Enum):
    """
    Traffic allocation regime.

    Subclasses ``str`` so callers comparing against the v1 literals
    (``config.test_mode == "SHADOW"``) keep working unchanged.
    """

    # Not a pytest test class, despite the leading "Test". Dunder names are
    # excluded from Enum membership, so this does not become a member.
    __test__ = False

    LIVE_SPLIT = "LIVE_SPLIT"
    SHADOW = "SHADOW"


class RecommendedAction(str, Enum):
    """Advisory outcome of one evaluation. Also ``str`` for v1 compatibility."""

    PROMOTE_CHALLENGER_TO_CHAMPION = "PROMOTE_CHALLENGER_TO_CHAMPION"
    REJECT_CHALLENGER = "REJECT_CHALLENGER"
    CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES = "CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES"
    CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE = "CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE"
    # The test was not run: the sample is corrupt, mislabelled or degenerate.
    # Distinct from "no difference found" precisely so a broken feed can never
    # be read off a dashboard as a clean inconclusive result.
    ABORT_EXPERIMENT_INVALID_DATA = "ABORT_EXPERIMENT_INVALID_DATA"


class ExperimentStatus(str, Enum):
    """Whether the statistical test actually ran."""

    AB_TEST_COMPLETED = "AB_TEST_COMPLETED"
    AB_TEST_INSUFFICIENT_SAMPLES = "AB_TEST_INSUFFICIENT_SAMPLES"
    AB_TEST_INVALID_DATA = "AB_TEST_INVALID_DATA"


@dataclass(frozen=True)
class ABTestConfig:
    """
    One pre-registered experiment.

    ``min_sample_size`` and ``significance_level_alpha`` MUST be fixed before
    data collection starts. Raising the sample floor or loosening alpha after
    looking at an interim result invalidates the p-value - see the peeking
    figures in ``references/standards.md``.

    Frozen for two reasons. Statistically, a pre-registered experiment whose
    parameters can be edited mid-flight is not pre-registered. Operationally,
    validation runs once in ``__post_init__``, so a mutable config could be
    walked past every check it just passed - assigning the plain string
    ``'SHADOW'`` after construction would leave ``test_mode`` no longer
    identical to :attr:`TestMode.SHADOW`, and route live orders to the
    challenger.
    """

    experiment_id: str
    champion_model_id: str
    challenger_model_id: str
    traffic_split_ratio: float = 0.80   # 0.80 = 80% Champion, 20% Challenger
    test_mode: TestMode = TestMode.LIVE_SPLIT
    min_sample_size: int = 30
    significance_level_alpha: float = 0.05

    def __post_init__(self) -> None:
        """
        Reject configurations that would silently mis-route live capital or
        produce an uninterpretable test.

        Fails loudly at construction rather than defaulting, because the
        defaulting branches here have live-money failure modes: an unrecognised
        ``test_mode`` string that quietly falls through to ``LIVE_SPLIT`` sends
        real orders to an unvalidated model.
        """
        for field_name in ("experiment_id", "champion_model_id", "challenger_model_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")

        if self.champion_model_id == self.challenger_model_id:
            raise ValueError(
                f"champion_model_id and challenger_model_id are both "
                f"{self.champion_model_id!r}; there is nothing to compare"
            )

        try:
            # object.__setattr__ because the dataclass is frozen; this is the
            # one sanctioned normalisation, before the config is handed out.
            object.__setattr__(self, "test_mode", TestMode(self.test_mode))
        except ValueError:
            raise ValueError(
                f"test_mode must be one of {[m.value for m in TestMode]}, got "
                f"{self.test_mode!r}. This comparison is case-sensitive: 'shadow' "
                f"is not 'SHADOW' and must not be silently treated as live."
            ) from None

        if not math.isfinite(self.traffic_split_ratio) or not 0.0 <= self.traffic_split_ratio <= 1.0:
            raise ValueError(
                f"traffic_split_ratio must lie in [0.0, 1.0] (the fraction routed "
                f"to the champion), got {self.traffic_split_ratio!r}"
            )

        if isinstance(self.min_sample_size, bool) or not isinstance(self.min_sample_size, int):
            raise ValueError(f"min_sample_size must be an int, got {self.min_sample_size!r}")
        if self.min_sample_size < _MIN_OBSERVATIONS_FOR_VARIANCE:
            raise ValueError(
                f"min_sample_size must be >= {_MIN_OBSERVATIONS_FOR_VARIANCE} "
                f"(sample variance is undefined below that), got {self.min_sample_size}"
            )

        if (not math.isfinite(self.significance_level_alpha)
                or not 0.0 < self.significance_level_alpha < 1.0):
            raise ValueError(
                f"significance_level_alpha must lie in (0.0, 1.0), got "
                f"{self.significance_level_alpha!r}"
            )


@dataclass
class ModelExecutionResult:
    """One realised trade attributed to one model."""

    model_id: str
    timestamp_epoch: float
    symbol: str
    signal_return_bps: float            # Realised trade return in basis points
    latency_ms: float


@dataclass
class ABTestReport:
    """
    One evaluation.

    Every statistic is ``Optional`` and is ``None`` when it was not actually
    computed, because the evaluation short-circuited. A field is never
    populated with a placeholder that reads like a measurement: a report
    carrying ``p_value = 1.0`` for a test that never ran is indistinguishable
    on a dashboard from a test that genuinely found nothing.
    """

    experiment_id: str
    champion_model_id: str
    challenger_model_id: str
    champion_sample_count: int
    challenger_sample_count: int
    recommended_action: RecommendedAction
    status: ExperimentStatus
    audit_notes: str
    champion_mean_return_bps: Optional[float] = None
    challenger_mean_return_bps: Optional[float] = None
    mean_difference_bps: Optional[float] = None
    welch_t_statistic: Optional[float] = None
    degrees_of_freedom: Optional[float] = None
    p_value: Optional[float] = None
    is_statistically_significant: bool = False


class ModelABTesterEngine:
    """
    Champion-Challenger A/B testing engine: deterministic traffic routing,
    shadow execution, and a Welch two-sample t-test over realised returns.

    Welch's test is used rather than Student's pooled-variance test because a
    challenger model has no reason to share the champion's return variance.
    The p-value is drawn from the **t distribution** with Welch-Satterthwaite
    degrees of freedom, per the NIST/SEMATECH handbook section 1.3.5.3 - never
    from the normal distribution, which is anti-conservative at every finite
    degrees of freedom and worst at exactly the sample sizes this module gates on.
    """

    def __init__(self) -> None:
        pass

    def route_request(self, config: ABTestConfig, request_key: str) -> str:
        """
        Deterministically map a request key to the model that will **execute**.

        In ``SHADOW`` mode this is always the champion: the challenger never
        touches live capital. Use :meth:`shadow_challenger_id` to obtain the
        model that should additionally be scored, without executing.

        The hash is salted with ``experiment_id``. Without the salt every
        experiment buckets every key identically, so a symbol routed to the
        challenger in one experiment is routed to the challenger in every
        concurrent experiment - the allocations become perfectly correlated
        instead of independent, and a re-run cannot re-randomise.

        :param config: validated experiment configuration.
        :param request_key: stable allocation unit, e.g. ``'AAPL'`` or
            ``'ACC_9921'``. It must be stable for the life of the experiment:
            hashing a per-order UUID re-randomises on every order, so a single
            symbol's fills are split across both models and the two samples are
            no longer independent draws from two strategies.
        :returns: the model id that should execute this request.
        :raises ValueError: if ``request_key`` is not a non-empty string.
        """
        if not isinstance(request_key, str) or not request_key:
            raise ValueError(f"request_key must be a non-empty string, got {request_key!r}")

        if config.test_mode is TestMode.SHADOW:
            return config.champion_model_id

        # blake2b rather than md5: stdlib, no FIPS-mode availability caveat,
        # and used here purely as a uniform mapping, not for security.
        salted = f"{config.experiment_id}\x00{request_key}".encode("utf-8")
        digest = hashlib.blake2b(salted, digest_size=8).digest()
        # Divide by 2**32, not 0xFFFFFFFF: dividing by the maximum attainable
        # value makes a score of exactly 1.0 possible and biases the top bucket.
        score = int.from_bytes(digest[:4], "big") / 2 ** 32

        if score < config.traffic_split_ratio:
            return config.champion_model_id
        return config.challenger_model_id

    def shadow_challenger_id(self, config: ABTestConfig) -> Optional[str]:
        """
        The model to score without executing, or ``None`` outside shadow mode.

        Shadow returns are counterfactual: they carry no market impact, no
        queue position and no realised fills, so they are systematically
        optimistic relative to the same model trading live. Shadow mode
        establishes that a challenger is not broken. It cannot establish that
        it is better, and its returns should not be fed to
        :meth:`evaluate_ab_test_results` against live champion returns.
        """
        return config.challenger_model_id if config.test_mode is TestMode.SHADOW else None

    def evaluate_ab_test_results(
        self,
        config: ABTestConfig,
        champion_results: Sequence[ModelExecutionResult],
        challenger_results: Sequence[ModelExecutionResult],
    ) -> ABTestReport:
        """
        Run Welch's two-sample t-test and return an advisory recommendation.

        Evaluation order is fixed and each step short-circuits:

          1. sample provenance and finiteness -> ``ABORT_EXPERIMENT_INVALID_DATA``
          2. pre-registered minimum sample size -> ``CONTINUE_..._INSUFFICIENT_SAMPLES``
          3. non-degenerate variance -> ``ABORT_EXPERIMENT_INVALID_DATA``
          4. Welch's t-test -> promote / reject / continue

        Provenance is checked before sample size because a corrupt or
        mislabelled sample must never be reported as "keep collecting", which
        reads as a healthy experiment still in progress.

        Promotion additionally requires a positive mean difference, so the
        effective one-sided false-promotion rate is ``alpha / 2``, not ``alpha``.

        :returns: an :class:`ABTestReport` whose statistics are ``None``
            wherever the evaluation short-circuited before computing them.
        """
        n_champion = len(champion_results)
        n_challenger = len(challenger_results)

        invalid_reason = self._find_sample_defect(config, champion_results, challenger_results)
        if invalid_reason is not None:
            return self._short_circuit_report(
                config,
                n_champion,
                n_challenger,
                RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA,
                ExperimentStatus.AB_TEST_INVALID_DATA,
                f"AB TEST INVALID DATA [{config.experiment_id}]: {invalid_reason} No statistic was "
                f"computed; repair the sample before reading anything into this experiment.",
                log_as_warning=True,
            )

        if n_champion < config.min_sample_size or n_challenger < config.min_sample_size:
            return self._short_circuit_report(
                config,
                n_champion,
                n_challenger,
                RecommendedAction.CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES,
                ExperimentStatus.AB_TEST_INSUFFICIENT_SAMPLES,
                f"AB TEST INSUFFICIENT SAMPLES [{config.experiment_id}]: Champion N={n_champion}, "
                f"Challenger N={n_challenger} (min required = {config.min_sample_size}). "
                f"Continue experiment.",
            )

        returns_champion = [r.signal_return_bps for r in champion_results]
        returns_challenger = [r.signal_return_bps for r in challenger_results]

        mean_champion = sum(returns_champion) / n_champion
        mean_challenger = sum(returns_challenger) / n_challenger
        diff_mean = mean_challenger - mean_champion

        # A sample can be finite element-wise and still overflow its own
        # variance: squaring a deviation of ~1e200 raises OverflowError. Returns
        # that large are implausible as basis points and entirely plausible as a
        # sentinel value from a mis-parsed feed, so this is a data fault, not a
        # crash.
        try:
            var_champion = self._sample_variance(returns_champion, mean_champion)
            var_challenger = self._sample_variance(returns_challenger, mean_challenger)
        except OverflowError:
            return self._short_circuit_report(
                config,
                n_champion,
                n_challenger,
                RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA,
                ExperimentStatus.AB_TEST_INVALID_DATA,
                f"AB TEST INVALID DATA [{config.experiment_id}]: sample variance overflowed. The "
                f"returns are finite individually but not on a scale any basis-point series "
                f"reaches (champion mean {mean_champion!r}, challenger mean {mean_challenger!r}); "
                f"check the feed for sentinel values.",
                log_as_warning=True,
            )

        # Both variances zero => Welch's statistic is 0/0. v1 floored the
        # variances at 1e-6 here, which turned two constant samples into a
        # t-statistic of ~35,000 and a p-value of 0.0 - fabricated certainty
        # from data carrying no information about sampling variability at all.
        if var_champion == 0.0 and var_challenger == 0.0:
            return self._short_circuit_report(
                config,
                n_champion,
                n_challenger,
                RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA,
                ExperimentStatus.AB_TEST_INVALID_DATA,
                f"AB TEST INVALID DATA [{config.experiment_id}]: both samples have zero variance "
                f"(champion constant at {mean_champion:.4f} bps, challenger constant at "
                f"{mean_challenger:.4f} bps). Welch's statistic is undefined here; this is a stubbed "
                f"or replayed feed, not a result.",
                log_as_warning=True,
                mean_champion=mean_champion,
                mean_challenger=mean_challenger,
                diff_mean=diff_mean,
            )

        se_champion_sq = var_champion / n_champion
        se_challenger_sq = var_challenger / n_challenger
        total_se_sq = se_champion_sq + se_challenger_sq
        standard_error = math.sqrt(total_se_sq)
        t_statistic = diff_mean / standard_error

        # Welch-Satterthwaite effective degrees of freedom (NIST 1.3.5.3),
        # written scale-invariantly. Dividing through by (sA^2/nA + sB^2/nB)^2
        # first gives the algebraically identical
        #     nu = 1 / (u^2/(nA-1) + v^2/(nB-1)),  u + v = 1
        # which avoids squaring the variance sum. The direct form overflows to
        # OverflowError on returns around 1e150 - implausible as basis points,
        # entirely plausible as a sentinel value from a mis-parsed feed.
        weight_champion = se_champion_sq / total_se_sq
        weight_challenger = se_challenger_sq / total_se_sq
        df = 1.0 / (
            (weight_champion ** 2) / (n_champion - 1)
            + (weight_challenger ** 2) / (n_challenger - 1)
        )

        # Saturating to inf does not raise, so guard the computed statistics as
        # well: an infinite variance yields a NaN t and a NaN df, which quietly
        # fail every comparison and report as "no significant difference".
        if not all(math.isfinite(v) for v in (t_statistic, df, total_se_sq)):
            return self._short_circuit_report(
                config,
                n_champion,
                n_challenger,
                RecommendedAction.ABORT_EXPERIMENT_INVALID_DATA,
                ExperimentStatus.AB_TEST_INVALID_DATA,
                f"AB TEST INVALID DATA [{config.experiment_id}]: the test statistic is not finite "
                f"(champion var = {var_champion!r}, challenger var = {var_challenger!r}). Check "
                f"the feed for sentinel values.",
                log_as_warning=True,
                mean_champion=mean_champion,
                mean_challenger=mean_challenger,
                diff_mean=diff_mean,
            )

        # Two-tailed p-value from the t distribution with nu = df. Using the
        # normal CDF instead - as v1 did - understates p at every finite df and
        # therefore over-promotes. Measured under the null at N=30 per arm:
        # 2.79% false promotions against a 2.50% nominal rate, rising to 4.43%
        # at N=5. See references/standards.md.
        p_value = float(2.0 * student_t.sf(abs(t_statistic), df))

        # Compared unrounded: rounding to 4dp first makes p = 0.049996 report
        # as 0.0500 and fail a `p < 0.05` gate that it should pass.
        is_significant = p_value < config.significance_level_alpha

        if is_significant and diff_mean > 0.0:
            action = RecommendedAction.PROMOTE_CHALLENGER_TO_CHAMPION
            notes = (
                f"AB TEST PROMOTION CANDIDATE [{config.experiment_id}]: Challenger "
                f"'{config.challenger_model_id}' outperforms Champion '{config.champion_model_id}'. "
                f"Mean Diff = +{diff_mean:.2f} bps, t = {t_statistic:.3f}, df = {df:.2f}, "
                f"p = {p_value:.4g} < {config.significance_level_alpha}. Advisory only: promotion is "
                f"a material change requiring documented authorisation before deployment."
            )
            logger.info(notes)
        elif is_significant and diff_mean < 0.0:
            action = RecommendedAction.REJECT_CHALLENGER
            notes = (
                f"AB TEST DEMOTION [{config.experiment_id}]: Challenger "
                f"'{config.challenger_model_id}' significantly underperforms Champion. "
                f"Mean Diff = {diff_mean:.2f} bps, t = {t_statistic:.3f}, df = {df:.2f}, "
                f"p = {p_value:.4g}."
            )
            logger.warning(notes)
        else:
            action = RecommendedAction.CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE
            notes = (
                f"AB TEST INCONCLUSIVE [{config.experiment_id}]: no statistically significant "
                f"difference (p = {p_value:.4g} >= {config.significance_level_alpha}, "
                f"t = {t_statistic:.3f}, df = {df:.2f}). Absence of evidence is not evidence of "
                f"equivalence; continue only to the pre-registered horizon."
            )
            logger.info(notes)

        return ABTestReport(
            experiment_id=config.experiment_id,
            champion_model_id=config.champion_model_id,
            challenger_model_id=config.challenger_model_id,
            champion_sample_count=n_champion,
            challenger_sample_count=n_challenger,
            recommended_action=action,
            status=ExperimentStatus.AB_TEST_COMPLETED,
            audit_notes=notes,
            champion_mean_return_bps=mean_champion,
            challenger_mean_return_bps=mean_challenger,
            mean_difference_bps=diff_mean,
            welch_t_statistic=t_statistic,
            degrees_of_freedom=df,
            p_value=p_value,
            is_statistically_significant=is_significant,
        )

    @staticmethod
    def _sample_variance(values: List[float], mean: float) -> float:
        """Unbiased (n - 1) sample variance. The caller guarantees len >= 2."""
        return sum((x - mean) ** 2 for x in values) / (len(values) - 1)

    @staticmethod
    def _find_sample_defect(
        config: ABTestConfig,
        champion_results: Sequence[ModelExecutionResult],
        challenger_results: Sequence[ModelExecutionResult],
    ) -> Optional[str]:
        """
        Return a human-readable defect description, or ``None`` if both samples
        are usable.

        Model-id provenance is checked because swapping the two argument lists
        inverts every recommendation - the engine would confidently advise
        promoting the worse model - and nothing downstream would catch it.
        Finiteness is checked because every comparison against ``NaN`` is
        ``False``: a NaN-poisoned sample propagates to a NaN t-statistic that
        fails the significance test and reports as a clean inconclusive result.
        """
        for label, results, expected_id in (
            ("champion", champion_results, config.champion_model_id),
            ("challenger", challenger_results, config.challenger_model_id),
        ):
            for index, result in enumerate(results):
                if result.model_id != expected_id:
                    return (
                        f"{label} sample[{index}] is attributed to model "
                        f"{result.model_id!r}, but this experiment's {label} is "
                        f"{expected_id!r}. Are the two result lists swapped?"
                    )
                if not math.isfinite(result.signal_return_bps):
                    return (
                        f"{label} sample[{index}] has a non-finite return "
                        f"({result.signal_return_bps!r})."
                    )
        return None

    @staticmethod
    def _short_circuit_report(
        config: ABTestConfig,
        n_champion: int,
        n_challenger: int,
        action: RecommendedAction,
        status: ExperimentStatus,
        notes: str,
        log_as_warning: bool = False,
        mean_champion: Optional[float] = None,
        mean_challenger: Optional[float] = None,
        diff_mean: Optional[float] = None,
    ) -> ABTestReport:
        """Build a report whose uncomputed statistics stay ``None``."""
        if log_as_warning:
            logger.warning(notes)
        else:
            logger.info(notes)
        return ABTestReport(
            experiment_id=config.experiment_id,
            champion_model_id=config.champion_model_id,
            challenger_model_id=config.challenger_model_id,
            champion_sample_count=n_champion,
            challenger_sample_count=n_challenger,
            recommended_action=action,
            status=status,
            audit_notes=notes,
            champion_mean_return_bps=mean_champion,
            challenger_mean_return_bps=mean_challenger,
            mean_difference_bps=diff_mean,
            is_statistically_significant=False,
        )
