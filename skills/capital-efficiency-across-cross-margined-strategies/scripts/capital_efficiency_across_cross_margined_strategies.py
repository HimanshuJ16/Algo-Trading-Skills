"""
capital-efficiency-across-cross-margined-strategies:
Estimate how much collateral a portfolio-margined account frees up relative to
margining every position standalone, and how much of that estimate you are
allowed to believe.

What this module is
-------------------
A **pairwise spread-credit estimator**, structurally modelled on the
inter-commodity spread credit in CME SPAN: a credit rate is applied to the
*smaller* of two opposing legs' margin requirements, spreads are formed in a
deterministic priority order, and each leg is consumed once it has been spread.
That shape is deliberate -- it is how a real margin engine grants offsets.

What this module is NOT
-----------------------
**It is not a margin calculator, and its number is not the broker's number.**
Production portfolio-margin engines are *scenario* engines, not correlation
formulas:

  - OCC's Customer Portfolio Margin (the basis of FINRA Rule 4210(g) portfolio
    margin, applied nightly as TIMS by brokers such as IBKR) revalues the
    portfolio across a grid of underlying price points -- +/-15% for individual
    equities and sector indexes, +/-10% for non-high-cap broad-based indexes,
    -8%/+6% for high-cap broad-based indexes -- and takes the worst loss.
  - CME SPAN sums scan risk, intra-commodity spread charges and delivery risk,
    then subtracts inter-commodity credits, and floors the result at the short
    option minimum.
  - Bybit's Unified Trading Account portfolio margin derives offsets from
    stress-test results, not from a published correlation matrix.

Three consequences the caller must respect
------------------------------------------
  1. **Offset credits are published parameters, not statistics you compute.**
     Exchanges and clearing houses publish them: SPAN inter-commodity credit
     rates are a table of recognised spread formations (Corn/Soybeans at 65%,
     for example), and OCC groups classes into product groups with fixed offset
     percentages, up to 90% between broad-based index class groups. Where you
     have the published rate, pass it via ``credit_rate_overrides`` and the
     correlation matrix is not consulted for that pair. Correlation is only a
     fallback proxy, and ``correlation_haircut`` is a caller-chosen
     conservatism knob with no regulatory standing.
  2. **Real requirements are floored; this estimate is not.** TIMS applies a
     minimum of $0.375 x contract multiplier per contract carried, SPAN applies
     a short option minimum, and non-index single-stock class groups receive no
     offset at all under OCC CPM. Set ``min_cross_margin_fraction`` to model
     that floor rather than assuming an offset can run to zero.
  3. **Offsets exist only inside one margining pool.** A long at one venue and
     a short at another do not net; they are separate accounts at separate
     clearing organisations. Netting across clearing houses happens only under
     a formal cross-margin programme -- see the sibling skill
     ``cross-margining-across-asset-classes``.

Deliberate conservatism
-----------------------
  - **The efficiency ratio is bounded above by 2.0.** Each spread consumes its
    credited amount from both legs, so the total credit can never exceed half
    the isolated requirement. A perfectly hedged pair at a 100% credit rate
    reports 2.0x, never infinity. Scenario engines can net further than this;
    treat the ceiling as a property of the estimator, not of the market.
  - A long and a short that are *negatively* correlated are risk-additive, not
    offsetting, and receive no credit here.
  - Two same-side positions that are negatively correlated genuinely hedge each
    other, and this module does **not** credit that. Its estimate is low on
    such books by construction.
  - Only the *sign* of ``delta_usd`` is used, to classify a leg as long or
    short. The credit is applied to margin, not to delta. Zero-delta legs (a
    delta-neutral options structure, say) contribute their margin to the
    isolated total and form no spreads.

See ``references/standards.md`` for the sourced methodology parameters.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "MarginInputError",
    "Position",
    "OffsetCredit",
    "MarginReport",
    "CrossMarginOptimizer",
    "net_positions_by_symbol",
]


class MarginInputError(ValueError):
    """
    Raised when portfolio, correlation or parameter input cannot be margined
    safely.

    This module fails closed on purpose. An under-reported margin requirement is
    not a cosmetic defect: capital sized against it is capital that is not there
    when the clearing house calls for it.
    """


def _check_finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarginInputError(f"{name} must be a number, got {value!r}")
    value = float(value)
    if math.isnan(value):
        raise MarginInputError(f"{name} must not be NaN")
    if math.isinf(value):
        raise MarginInputError(f"{name} must be finite, got {value!r}")
    return value


def _check_unit_fraction(value: float, name: str) -> float:
    value = _check_finite(value, name)
    if not 0.0 <= value <= 1.0:
        raise MarginInputError(f"{name} must be within [0.0, 1.0], got {value!r}")
    return value


@dataclass(frozen=True)
class Position:
    """
    One margined leg.

    symbol:          instrument identifier; the key used against the correlation
                     and credit-rate matrices.
    delta_usd:       signed directional exposure. Only the sign is used for
                     spread formation (> 0 long, < 0 short, == 0 forms no
                     spread); the magnitude is used only by
                     ``net_positions_by_symbol``.
    base_margin_usd: the requirement this leg would carry standalone. Must be
                     non-negative -- a negative margin requirement is not a
                     thing, and accepting one would understate the isolated
                     total before any offset is applied.
    """

    symbol: str
    delta_usd: float
    base_margin_usd: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise MarginInputError(f"symbol must be a non-empty string, got {self.symbol!r}")
        object.__setattr__(self, "delta_usd", _check_finite(self.delta_usd, "delta_usd"))
        margin = _check_finite(self.base_margin_usd, "base_margin_usd")
        if margin < 0.0:
            raise MarginInputError(f"base_margin_usd must be >= 0, got {margin!r}")
        object.__setattr__(self, "base_margin_usd", margin)


@dataclass(frozen=True)
class OffsetCredit:
    """One granted spread credit, kept so the total can be audited leg by leg."""

    long_symbol: str
    short_symbol: str
    credit_rate: float
    spread_margin_usd: float  # the smaller leg's remaining margin, consumed from both
    offset_usd: float
    source: str  # "published" (credit_rate_overrides) or "correlation" (proxy)


@dataclass(frozen=True)
class MarginReport:
    """
    The estimate, plus enough detail to audit it.

    ``offsets`` lists every credit granted, in the order the spreads formed.
    Note that when ``floor_applied`` is True the reported ``total_offset_usd``
    is the *floored* credit and will be smaller than the sum of ``offsets`` --
    the floor, not the spread logic, produced the final number.
    """

    isolated_margin_usd: float
    cross_margin_usd: float
    capital_efficiency_ratio: float
    total_offset_usd: float
    offsets: Tuple[OffsetCredit, ...] = field(default_factory=tuple)
    floor_applied: bool = False


class CrossMarginOptimizer:
    """
    Estimates a portfolio margin requirement by granting spread credits between
    opposing legs.

    Credit rate for a (long, short) pair, in precedence order:
      1. ``credit_rate_overrides[a][b]`` -- the broker's or exchange's published
         offset percentage. Used as given; the haircut is *not* applied, because
         a published credit rate is already the post-haircut number.
      2. ``max(correlation, 0) * correlation_haircut`` -- a proxy, to be used
         only while the published rate is unknown.

    Spreads are formed highest-credit-first, mirroring the exchange-defined
    spread priority SPAN uses, so the result does not depend on the order
    positions happen to arrive in. Each spread consumes the smaller leg's
    remaining margin from *both* legs, so no leg is credited twice.
    """

    def __init__(
        self,
        correlation_matrix: Mapping[str, Mapping[str, float]],
        correlation_haircut: float = 0.80,
        credit_rate_overrides: Optional[Mapping[str, Mapping[str, float]]] = None,
        min_cross_margin_fraction: float = 0.0,
    ) -> None:
        """
        correlation_matrix:        pairwise correlations, e.g. {'BTC': {'ETH': 0.90}}.
                                   Read symmetrically; missing pairs are 0.0, which
                                   grants no credit.
        correlation_haircut:       multiplier applied to a *correlation-derived*
                                   credit rate. Caller-chosen conservatism, not a
                                   published standard. 1.0 disables it.
        credit_rate_overrides:     published offset percentages per pair, taking
                                   precedence over correlation. Also read
                                   symmetrically.
        min_cross_margin_fraction: floor on the result as a fraction of isolated
                                   margin, modelling the per-contract and
                                   no-offset minimums real engines impose.
                                   Default 0.0 applies no floor.
        """
        self.correlation_matrix = self._validate_matrix(
            correlation_matrix, "correlation_matrix", -1.0, 1.0
        )
        self.credit_rate_overrides = self._validate_matrix(
            credit_rate_overrides or {}, "credit_rate_overrides", 0.0, 1.0
        )
        self.correlation_haircut = _check_unit_fraction(correlation_haircut, "correlation_haircut")
        self.min_cross_margin_fraction = _check_unit_fraction(
            min_cross_margin_fraction, "min_cross_margin_fraction"
        )

    @staticmethod
    def _validate_matrix(
        matrix: Mapping[str, Mapping[str, float]], name: str, low: float, high: float
    ) -> Dict[str, Dict[str, float]]:
        """
        Reject out-of-range entries up front.

        A correlation of 1.4, or a credit rate of 2.0, slipping in from a bad
        data feed would grant more credit than the legs carry margin, driving the
        estimate below zero and the efficiency ratio to infinity -- the exact
        fail-open direction this module must not have.
        """
        if not isinstance(matrix, Mapping):
            raise MarginInputError(f"{name} must be a mapping, got {type(matrix).__name__}")
        validated: Dict[str, Dict[str, float]] = {}
        for row_key, row in matrix.items():
            if not isinstance(row, Mapping):
                raise MarginInputError(f"{name}[{row_key!r}] must be a mapping")
            validated[row_key] = {}
            for col_key, raw in row.items():
                value = _check_finite(raw, f"{name}[{row_key!r}][{col_key!r}]")
                if not low <= value <= high:
                    raise MarginInputError(
                        f"{name}[{row_key!r}][{col_key!r}] must be within "
                        f"[{low}, {high}], got {value!r}"
                    )
                validated[row_key][col_key] = value
        return validated

    @staticmethod
    def _lookup(
        matrix: Mapping[str, Mapping[str, float]], sym1: str, sym2: str
    ) -> Optional[float]:
        row = matrix.get(sym1)
        if row is not None and sym2 in row:
            return row[sym2]
        row = matrix.get(sym2)
        if row is not None and sym1 in row:
            return row[sym1]
        return None

    def _get_correlation(self, sym1: str, sym2: str) -> float:
        """Symmetric lookup; 1.0 for a symbol against itself, 0.0 when the pair is absent."""
        if sym1 == sym2:
            return 1.0
        value = self._lookup(self.correlation_matrix, sym1, sym2)
        return 0.0 if value is None else value

    def _credit_rate(self, sym1: str, sym2: str) -> Tuple[float, str]:
        """
        Return ``(credit_rate, source)`` for a long/short pair.

        A published rate wins over a correlation proxy. Negative correlation
        between a long and a short means the two legs move *together* in risk
        terms; crediting an offset there would understate the requirement, so the
        proxy rate floors at zero.
        """
        published = self._lookup(self.credit_rate_overrides, sym1, sym2)
        if published is not None:
            return published, "published"
        correlation = self._get_correlation(sym1, sym2)
        return max(correlation, 0.0) * self.correlation_haircut, "correlation"

    def calculate_margin(self, positions: Sequence[Position]) -> MarginReport:
        """
        Estimate the portfolio margin requirement for ``positions``.

        Every symbol must appear at most once: the requirement is a property of
        the *net* position in an instrument, and two rows for one symbol -- the
        normal case when several strategies trade it -- would otherwise be
        margined as two independent legs and granted a spread credit against each
        other. Net them first with ``net_positions_by_symbol``.
        """
        if isinstance(positions, Position):
            raise MarginInputError(
                "positions must be a sequence of Position, not a single Position"
            )
        positions = list(positions)
        for index, position in enumerate(positions):
            if not isinstance(position, Position):
                raise MarginInputError(
                    f"positions[{index}] must be a Position, got {type(position).__name__}"
                )
        symbols = [p.symbol for p in positions]
        duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
        if duplicates:
            raise MarginInputError(
                f"duplicate symbols in portfolio: {duplicates}. Net them per symbol first "
                f"(see net_positions_by_symbol) -- margin applies to the net position."
            )

        isolated_margin = math.fsum(p.base_margin_usd for p in positions)
        remaining = {p.symbol: p.base_margin_usd for p in positions}

        longs = [p for p in positions if p.delta_usd > 0.0]
        shorts = [p for p in positions if p.delta_usd < 0.0]

        # Rank candidate spreads before consuming anything, so the result is a
        # function of the portfolio rather than of list order. Ties break on the
        # larger spreadable amount, then on symbol, for full determinism.
        candidates: List[Tuple[float, float, str, str, float, str]] = []
        for long_pos in longs:
            for short_pos in shorts:
                rate, source = self._credit_rate(long_pos.symbol, short_pos.symbol)
                if rate <= 0.0:
                    continue
                candidates.append(
                    (
                        -rate,
                        -min(long_pos.base_margin_usd, short_pos.base_margin_usd),
                        long_pos.symbol,
                        short_pos.symbol,
                        rate,
                        source,
                    )
                )
        candidates.sort()

        credits: List[OffsetCredit] = []
        total_offset = 0.0
        for _, _, long_symbol, short_symbol, rate, source in candidates:
            spread_margin = min(remaining[long_symbol], remaining[short_symbol])
            if spread_margin <= 0.0:
                continue
            offset = spread_margin * rate
            total_offset += offset
            remaining[long_symbol] -= spread_margin
            remaining[short_symbol] -= spread_margin
            credits.append(
                OffsetCredit(
                    long_symbol=long_symbol,
                    short_symbol=short_symbol,
                    credit_rate=rate,
                    spread_margin_usd=spread_margin,
                    offset_usd=offset,
                    source=source,
                )
            )

        cross_margin = isolated_margin - total_offset
        floor = isolated_margin * self.min_cross_margin_fraction
        floor_applied = cross_margin < floor
        if floor_applied:
            cross_margin = floor
            total_offset = isolated_margin - cross_margin

        # Each spread consumes its amount from *both* legs and credit rates are
        # capped at 1.0, so the total credit can never exceed half the isolated
        # requirement: cross margin is bounded below by isolated / 2 and the
        # efficiency ratio is bounded above by 2.0. Anything claiming a 5x or 10x
        # capital saving is not this model. Guard the invariant anyway -- a
        # silently negative requirement is the worst possible output here.
        if cross_margin < 0.0:  # pragma: no cover - unreachable given validation
            raise MarginInputError(
                f"internal invariant violated: cross margin {cross_margin!r} is negative"
            )

        # An empty book is perfectly efficient by definition, not infinitely so.
        cer = 1.0 if isolated_margin == 0.0 else isolated_margin / cross_margin

        logger.info(
            "Margin estimate: isolated=%.2f cross=%.2f offset=%.2f CER=%.4fx "
            "spreads=%d floor_applied=%s",
            isolated_margin,
            cross_margin,
            total_offset,
            cer,
            len(credits),
            floor_applied,
        )

        return MarginReport(
            isolated_margin_usd=isolated_margin,
            cross_margin_usd=cross_margin,
            capital_efficiency_ratio=cer,
            total_offset_usd=total_offset,
            offsets=tuple(credits),
            floor_applied=floor_applied,
        )


def net_positions_by_symbol(positions: Sequence[Position]) -> List[Position]:
    """
    Collapse multiple rows per symbol into one net position per symbol.

    The multi-strategy case this exists for: two sleeves each hold BTC, one long
    and one short. The account holds the *net* of the two and is margined on it;
    passing both rows to ``calculate_margin`` would instead margin them as two
    legs and grant only a partial spread credit between them, overstating the
    requirement on a book that is partly flat.

    Netting rule, deliberately conservative:
      - Net delta is the signed sum of the group's deltas.
      - The margin rate per unit of exposure is taken as the **highest**
        ``base_margin_usd / abs(delta_usd)`` in the group and applied to the net
        delta.
      - The result is capped at the group's summed standalone margin, so netting
        can never *raise* the requirement.
      - Zero-delta rows carry no derivable rate; their margin is added on top
        rather than netted away.

    Where the broker publishes the netted requirement, prefer that number over
    this approximation.
    """
    positions = list(positions)
    for index, position in enumerate(positions):
        if not isinstance(position, Position):
            raise MarginInputError(
                f"positions[{index}] must be a Position, got {type(position).__name__}"
            )

    grouped: Dict[str, List[Position]] = {}
    for position in positions:
        grouped.setdefault(position.symbol, []).append(position)

    netted: List[Position] = []
    for symbol, group in grouped.items():
        if len(group) == 1:
            netted.append(group[0])
            continue

        directional = [p for p in group if p.delta_usd != 0.0]
        flat_margin = math.fsum(p.base_margin_usd for p in group if p.delta_usd == 0.0)
        total_margin = math.fsum(p.base_margin_usd for p in group)

        if not directional:
            netted.append(Position(symbol, 0.0, total_margin))
            continue

        net_delta = math.fsum(p.delta_usd for p in directional)
        max_rate = max(p.base_margin_usd / abs(p.delta_usd) for p in directional)
        net_margin = min(max_rate * abs(net_delta) + flat_margin, total_margin)
        netted.append(Position(symbol, net_delta, net_margin))

    return netted
