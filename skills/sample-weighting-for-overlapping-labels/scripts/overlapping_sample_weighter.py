"""
sample-weighting-for-overlapping-labels: label concurrency, average uniqueness,
return-attribution and time-decay sample weights for overlapping financial
labels (Triple Barrier Method holding periods, and any other multi-bar label).

Design notes
------------
* **Concurrency and average uniqueness** follow Lopez de Prado, *Advances in
  Financial Machine Learning* (Wiley, 2018), Snippets 4.1-4.2. A label spanning
  bars ``[t0, t1]`` is active on **both** endpoints (``count.loc[tIn:tOut] +=
  1``), and its average uniqueness is ``mean(1 / c_t)`` over that closed
  interval, where ``c_t`` is the number of labels active on bar ``t``.
  Uniqueness therefore always lies in ``(0, 1]``: 1.0 means the label shares no
  bar with any other label.

* **Return attribution** follows Snippet 4.10 (p. 69):
  ``w_i = |sum_{t in [t0, t1]} r_t / c_t|`` where ``r_t`` is the **log** return
  of bar ``t`` -- log returns specifically, because attribution sums returns
  across bars and only log returns are additive. That exact form is computed
  only when the caller supplies ``bar_log_returns``. Without it the engine falls
  back to ``u_i * |realized_return_i|``, which coincides with the snippet only
  when a span's per-bar returns are uniform. The fallback is flagged in the
  report (``return_attribution_is_exact``) and in the audit notes rather than
  being presented as the published formula.

* **Time decay** follows Snippet 4.11 (p. 70): piecewise-linear decay applied
  over **cumulative uniqueness** -- not over calendar time, and not over the
  caller's list position. The newest label gets factor 1.0, the oldest tends to
  ``time_decay_last_weight``, and negative settings zero out the oldest portion
  of cumulative uniqueness entirely. Spans are sorted chronologically inside the
  engine, so the factors describe chronology rather than the order the caller
  happened to build the list in.

* **Normalization** rescales weights to sum to the sample count ``N``
  (``w *= N / sum(w)``), matching the book's
  ``out['w'] *= out.shape[0] / out['w'].sum()``. Weights are returned unrounded;
  rounding them to 4 decimals -- as an earlier version of this module did --
  silently breaks the ``sum(w) == N`` invariant that ``references/standards.md``
  states.

* **Weighting is not leakage control.** Sample weights correct the IID violation
  *inside* a training set. They do nothing about train/validation contamination
  from the same overlap, which requires purging and embargoing (op. cit.
  Snippets 7.1-7.2, Ch. 7) -- see the repo skills
  ``hyperparameter-tuning-without-target-leakage`` and
  ``walk-forward-validation-setup``. Nor do they fix bootstrap redundancy in
  bagged learners: for that, op. cit. Sec. 4.4 recommends setting a bagging
  classifier's ``max_samples`` to the average uniqueness reported here.

* **Complexity** is O(sum of span lengths) in time and memory, because
  uniqueness is defined bar by bar. Spans expressed in tick or millisecond
  indices rather than bar indices allocate one dict entry per index covered.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


class SampleWeightingError(ValueError):
    """Raised on malformed label spans, returns, or weighting configuration."""


class WeightingMethod(str, Enum):
    UNIQUENESS_ONLY = "UNIQUENESS_ONLY"
    RETURN_ATTRIBUTED = "RETURN_ATTRIBUTED"
    TIME_DECAY = "TIME_DECAY"


@dataclass
class LabelSpan:
    """
    One label's holding period on the bar index.

    ``start_time_idx`` and ``end_time_idx`` are **bar indices, inclusive of both
    endpoints** -- the label is treated as active on its closing bar. Spans
    ``[1, 5]`` and ``[6, 10]`` do not overlap; ``[1, 5]`` and ``[5, 9]`` share
    bar 5.

    ``realized_return`` is the label's return over the whole span. It is used
    only by ``RETURN_ATTRIBUTED``, and only when per-bar returns are not
    supplied.
    """

    sample_id: str
    start_time_idx: int
    end_time_idx: int
    realized_return: float = 0.0


@dataclass
class SampleWeightResult:
    sample_id: str
    start_time_idx: int
    end_time_idx: int
    average_uniqueness: float
    raw_weight: float
    normalized_weight: float


@dataclass
class SampleWeightingReport:
    total_samples: int
    average_dataset_uniqueness: float
    weighting_method: WeightingMethod
    sample_results: List[SampleWeightResult]
    audit_notes: str
    #: True/False for RETURN_ATTRIBUTED (exact Snippet 4.10 vs the uniform-return
    #: approximation); None for the other methods, which have no such variant.
    return_attribution_is_exact: Optional[bool] = None
    #: True when every raw weight was zero and weights fell back to uniform 1.0.
    degenerate_uniform_fallback: bool = False


def _is_real_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value: float) -> bool:
    # NaN fails the self-comparison; +/-inf fails the magnitude comparison.
    return value == value and float("-inf") < value < float("inf")


class SampleWeightingForOverlappingLabelsEngine:
    """
    Concurrency, average uniqueness and sample weights for overlapping labels.

    Args:
        time_decay_last_weight: Lopez de Prado's ``clfLastW`` (Snippet 4.11).
            ``1.0`` disables decay; ``0 < c < 1`` decays linearly with every
            label keeping positive weight; ``0.0`` decays linearly to zero at the
            oldest label; ``-1 < c < 0`` zeroes out the oldest portion of
            cumulative uniqueness. Must lie in ``(-1.0, 1.0]``.

    Note:
        Before v2.0.0 this parameter was named ``decay_factor`` and applied an
        exponential decay keyed off the caller's *list position*. It was renamed
        rather than reinterpreted, so existing callers fail loudly instead of
        silently receiving different weights.
    """

    def __init__(self, time_decay_last_weight: float = 0.5) -> None:
        if not _is_real_number(time_decay_last_weight) or not _is_finite(float(time_decay_last_weight)):
            raise SampleWeightingError(
                f"time_decay_last_weight must be a finite number, got {time_decay_last_weight!r}."
            )
        value = float(time_decay_last_weight)
        if not -1.0 < value <= 1.0:
            raise SampleWeightingError(
                "time_decay_last_weight must lie in (-1.0, 1.0]: 1.0 is no decay, 0.0 decays to "
                f"zero at the oldest label, -1.0 is a division by zero. Got {value!r}."
            )
        self.time_decay_last_weight = value

    # ------------------------------------------------------------------ input

    def _validate_spans(self, spans: Sequence[LabelSpan]) -> None:
        if not isinstance(spans, Sequence) or isinstance(spans, (str, bytes)):
            raise SampleWeightingError(
                f"spans must be a sequence of LabelSpan, got {type(spans).__name__}."
            )
        if len(spans) == 0:
            raise SampleWeightingError("At least 1 LabelSpan is required for sample weighting.")

        seen_ids: Dict[str, int] = {}
        for i, s in enumerate(spans):
            if not isinstance(s, LabelSpan):
                raise SampleWeightingError(
                    f"spans[{i}] must be a LabelSpan, got {type(s).__name__}."
                )
            if not isinstance(s.sample_id, str) or not s.sample_id.strip():
                raise SampleWeightingError(
                    f"spans[{i}].sample_id must be a non-empty string, got {s.sample_id!r}."
                )
            if s.sample_id in seen_ids:
                raise SampleWeightingError(
                    f"Duplicate sample_id {s.sample_id!r} at spans[{i}] and spans[{seen_ids[s.sample_id]}]. "
                    "Weights are joined back onto the training matrix by id, so duplicates silently "
                    "mis-assign them."
                )
            seen_ids[s.sample_id] = i

            for field_name in ("start_time_idx", "end_time_idx"):
                idx = getattr(s, field_name)
                if not isinstance(idx, int) or isinstance(idx, bool):
                    raise SampleWeightingError(
                        f"spans[{i}].{field_name} must be an int bar index, got {idx!r}."
                    )
            if s.end_time_idx < s.start_time_idx:
                raise SampleWeightingError(
                    f"spans[{i}] ({s.sample_id!r}) ends before it starts: "
                    f"[{s.start_time_idx}, {s.end_time_idx}]. An inverted span covers no bars, so it "
                    "contributes nothing to concurrency and would then be scored as perfectly unique."
                )
            if not _is_real_number(s.realized_return) or not _is_finite(float(s.realized_return)):
                raise SampleWeightingError(
                    f"spans[{i}] ({s.sample_id!r}) needs a finite numeric realized_return, got "
                    f"{s.realized_return!r}. A single NaN propagates through normalization into every "
                    "returned weight."
                )

    # ------------------------------------------------------------ concurrency

    def compute_concurrency(self, spans: Sequence[LabelSpan]) -> Dict[int, int]:
        """
        Number of labels active on each bar, ``c_t`` (op. cit. Snippet 4.1).

        Both endpoints are inclusive. Bars covered by no label are absent from
        the mapping rather than present with a count of zero.
        """
        self._validate_spans(spans)
        concurrency: Dict[int, int] = {}
        for s in spans:
            for t in range(s.start_time_idx, s.end_time_idx + 1):
                concurrency[t] = concurrency.get(t, 0) + 1
        return concurrency

    def compute_sample_uniqueness(
        self,
        spans: Sequence[LabelSpan],
        concurrency: Mapping[int, int],
    ) -> List[float]:
        """
        Average uniqueness ``u_i = mean(1 / c_t)`` per span (op. cit. Snippet 4.2).

        ``concurrency`` must be the map returned by :meth:`compute_concurrency`
        over the *same* span set. A bar missing from it is an error, not a bar
        carrying one label: defaulting it to 1 would report a heavily overlapping
        label as perfectly unique.
        """
        self._validate_spans(spans)
        uniqueness_scores: List[float] = []
        for i, s in enumerate(spans):
            duration = (s.end_time_idx - s.start_time_idx) + 1
            inv_concurrency_sum = 0.0
            for t in range(s.start_time_idx, s.end_time_idx + 1):
                c_t = concurrency.get(t)
                if c_t is None or c_t < 1:
                    raise SampleWeightingError(
                        f"Concurrency map has no active-label count for bar {t}, which is covered by "
                        f"spans[{i}] ({s.sample_id!r}). Pass the map returned by compute_concurrency() "
                        "for this same span set."
                    )
                inv_concurrency_sum += 1.0 / c_t
            uniqueness_scores.append(inv_concurrency_sum / duration)
        return uniqueness_scores

    # ------------------------------------------------------------- time decay

    def compute_time_decay_factors(
        self,
        spans: Sequence[LabelSpan],
        uniqueness: Sequence[float],
    ) -> List[float]:
        """
        Piecewise-linear decay factors over cumulative uniqueness (Snippet 4.11).

        Returned in the order of ``spans``, but computed over spans sorted by
        ``(start_time_idx, end_time_idx)``, so the factors follow chronology
        rather than argument order. The newest span's factor is exactly 1.0; the
        oldest tends to ``time_decay_last_weight`` and is clipped at 0.0.
        """
        self._validate_spans(spans)
        if len(uniqueness) != len(spans):
            raise SampleWeightingError(
                f"uniqueness has {len(uniqueness)} entries for {len(spans)} spans."
            )

        chronological = sorted(
            range(len(spans)),
            key=lambda i: (spans[i].start_time_idx, spans[i].end_time_idx, i),
        )
        cumulative: List[float] = [0.0] * len(spans)
        running = 0.0
        for i in chronological:
            running += uniqueness[i]
            cumulative[i] = running

        total = running
        if total <= 0.0:  # pragma: no cover - uniqueness always lies in (0, 1]
            raise SampleWeightingError("Total cumulative uniqueness must be positive.")

        last_w = self.time_decay_last_weight
        if last_w >= 0.0:
            slope = (1.0 - last_w) / total
        else:
            slope = 1.0 / ((last_w + 1.0) * total)
        const = 1.0 - slope * total

        return [max(const + slope * c, 0.0) for c in cumulative]

    # ----------------------------------------------------- return attribution

    def _return_attribution_weights(
        self,
        spans: Sequence[LabelSpan],
        concurrency: Mapping[int, int],
        uniqueness: Sequence[float],
        bar_log_returns: Optional[Mapping[int, float]],
    ) -> Tuple[List[float], bool]:
        """Raw ``RETURN_ATTRIBUTED`` weights and whether they are the exact form."""
        if bar_log_returns is None:
            approximate = [
                uniqueness[i] * abs(s.realized_return) for i, s in enumerate(spans)
            ]
            return approximate, False

        if not isinstance(bar_log_returns, Mapping):
            raise SampleWeightingError(
                "bar_log_returns must be a mapping of bar index -> log return, got "
                f"{type(bar_log_returns).__name__}."
            )

        weights: List[float] = []
        for i, s in enumerate(spans):
            attributed = 0.0
            for t in range(s.start_time_idx, s.end_time_idx + 1):
                r_t = bar_log_returns.get(t)
                if r_t is None:
                    raise SampleWeightingError(
                        f"bar_log_returns is missing bar {t}, which is covered by spans[{i}] "
                        f"({s.sample_id!r}). Treating a missing bar as a zero return would understate "
                        "that label's attributed weight."
                    )
                if not _is_real_number(r_t) or not _is_finite(float(r_t)):
                    raise SampleWeightingError(f"bar_log_returns[{t}] is non-finite ({r_t!r}).")
                attributed += float(r_t) / concurrency[t]
            weights.append(abs(attributed))
        return weights, True

    # ---------------------------------------------------------------- weights

    def compute_sample_weights(
        self,
        spans: Sequence[LabelSpan],
        method: WeightingMethod = WeightingMethod.UNIQUENESS_ONLY,
        bar_log_returns: Optional[Mapping[int, float]] = None,
    ) -> SampleWeightingReport:
        """
        Compute per-sample weights normalized so that ``sum(weights) == N``.

        Args:
            spans: label holding periods on the bar index, both endpoints
                inclusive. Input order is preserved in the report.
            method: ``UNIQUENESS_ONLY`` (``w_i = u_i``), ``RETURN_ATTRIBUTED``
                (Snippet 4.10), or ``TIME_DECAY`` (``w_i = u_i * d_i`` with
                ``d_i`` from Snippet 4.11).
            bar_log_returns: optional mapping of ``bar index -> log return
                realized over that bar``. Supplying it switches
                ``RETURN_ATTRIBUTED`` from the uniform-return approximation to
                the exact snippet formula. Ignored by the other methods.

        Raises:
            SampleWeightingError: on malformed spans, an unknown method, or
                missing/non-finite returns.
        """
        self._validate_spans(spans)
        try:
            method = WeightingMethod(method)
        except ValueError as exc:
            raise SampleWeightingError(
                f"Unknown weighting method {method!r}. Expected one of "
                f"{[m.value for m in WeightingMethod]}."
            ) from exc

        if bar_log_returns is not None and method != WeightingMethod.RETURN_ATTRIBUTED:
            logger.warning(
                "bar_log_returns was supplied but method is %s, which does not use per-bar "
                "returns; they are being ignored. Pass RETURN_ATTRIBUTED for Snippet 4.10 "
                "attribution.",
                method.value,
            )

        n = len(spans)
        concurrency = self.compute_concurrency(spans)
        uniqueness = self.compute_sample_uniqueness(spans, concurrency)

        return_attribution_is_exact: Optional[bool] = None
        if method == WeightingMethod.UNIQUENESS_ONLY:
            raw_weights = list(uniqueness)
        elif method == WeightingMethod.RETURN_ATTRIBUTED:
            raw_weights, return_attribution_is_exact = self._return_attribution_weights(
                spans, concurrency, uniqueness, bar_log_returns
            )
        else:  # WeightingMethod.TIME_DECAY
            decay = self.compute_time_decay_factors(spans, uniqueness)
            raw_weights = [uniqueness[i] * decay[i] for i in range(n)]

        sum_raw = sum(raw_weights)
        degenerate = sum_raw <= 0.0
        if degenerate:
            logger.warning(
                "All raw sample weights are zero under %s; falling back to uniform weights of 1.0. "
                "Check the inputs -- for RETURN_ATTRIBUTED this means every label realized exactly "
                "zero return.",
                method.value,
            )
            norm_weights = [1.0] * n
        else:
            norm_weights = [(w / sum_raw) * n for w in raw_weights]

        results = [
            SampleWeightResult(
                sample_id=s.sample_id,
                start_time_idx=s.start_time_idx,
                end_time_idx=s.end_time_idx,
                average_uniqueness=uniqueness[i],
                raw_weight=raw_weights[i],
                normalized_weight=norm_weights[i],
            )
            for i, s in enumerate(spans)
        ]

        avg_dataset_u = sum(uniqueness) / n
        notes = (
            f"SAMPLE WEIGHTING [{method.value}]: N = {n}, "
            f"Avg Dataset Uniqueness = {avg_dataset_u:.4f} (1.0 = completely non-overlapping). "
            f"Bagging max_samples hint = {avg_dataset_u:.4f} (AFML Sec. 4.4)."
        )
        if return_attribution_is_exact is False:
            notes += (
                " Return attribution used the uniform-return APPROXIMATION u_i * |r_i|; pass "
                "bar_log_returns for the exact Snippet 4.10 attribution."
            )
        if degenerate:
            notes += " DEGENERATE: all raw weights were zero, uniform weights substituted."

        logger.info(notes)

        return SampleWeightingReport(
            total_samples=n,
            average_dataset_uniqueness=avg_dataset_u,
            weighting_method=method,
            sample_results=results,
            audit_notes=notes,
            return_attribution_is_exact=return_attribution_is_exact,
            degenerate_uniform_fallback=degenerate,
        )
