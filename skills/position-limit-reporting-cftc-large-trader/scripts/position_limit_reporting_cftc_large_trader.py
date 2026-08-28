"""
position-limit-reporting-cftc-large-trader: CFTC large-trader reporting-level
screening and Part 150 federal speculative position-limit auditing for an
entity's aggregated futures and options positions.

Purpose
-------
Answer two *different* regulatory questions that are routinely conflated:

1. **Is a position reportable?** 17 CFR 15.00(p)(1) defines a "reportable
   position" as any open contract position that, at the close of the market on
   any business day, "equals or exceeds the quantity specified in Sec. 15.03"
   in either (i) "any one future of any commodity on any one reporting market"
   or (ii) "long or short put or call options that exercise into the same
   futures contract". An account holding a reportable position is a "special
   account" (Sec. 15.00(r)), which the *carrying firm* identifies to the
   Commission on Form 102A.

2. **Is a position over the limit?** 17 CFR 150.2 prohibits holding or
   controlling positions "net long or net short, in excess of" the levels the
   Commission specifies, tested separately for the spot month, a single month,
   and all months combined.

These use different arithmetic, different aggregation universes and different
comparison operators. Getting them backwards is the single most common defect
in home-grown position-limit code, so this engine keeps them structurally
separate and reports them as separate flags.

Who files what (this engine does NOT file anything)
---------------------------------------------------
Form 102A is **not** filed by the position-holding entity. 17 CFR 17.01(a):
"When a special account is reported for the first time, the futures commission
merchant, clearing member, or foreign broker shall identify the special account
to the Commission on Form 102". The trading entity's own form is Form 40,
filed "after a special call upon such trader by the Commission or its designee"
(17 CFR 18.04(a)) - not on a routine daily schedule.

So this engine is a **buy-side self-surveillance tool**: it tells you that your
carrying broker is about to identify you as a special account, and that you are
approaching or over a federal limit. It generates no filing payload and
transmits nothing.

Reporting arithmetic (the part most implementations get wrong)
--------------------------------------------------------------
Sec. 15.00(p)(1) tests **one side at a time, in one bucket at a time**:

  * bucket = (one future of one commodity on one reporting market), and,
    separately, (options exercising into that same future);
  * a bucket is reportable if gross long >= level **or** gross short >= level.

It is therefore wrong to:

  * sum long + short and compare the total to the level - 200 long against
    200 short in CL is 400 by that arithmetic and would be flagged at a level
    of 350, though neither side reaches 350; and
  * net long against short and compare |net| to the level - 400 long against
    300 short nets to 100 and would *not* be flagged, though the long side of
    400 is squarely reportable. That direction is the dangerous one: it is a
    missed identification.

Both errors are why this module takes gross long and gross short as inputs and
derives net, rather than accepting a caller-supplied net that can disagree with
its own components.

Limit arithmetic
----------------
Sec. 150.2 is a **net** test, on futures-equivalent positions, and the operator
is strict: "in excess of" means equal to the limit is not a breach, whereas
"equals or exceeds" in Sec. 15.00(p) means equal to the reporting level *is*
reportable. The two boundaries differ by one contract and this module encodes
that difference deliberately.

Federal limits are not one number per commodity. Under the 2020 final rule
(effective 15 March 2021) all 25 core referenced futures contracts carry a
federal **spot-month** limit, but only the nine legacy agricultural contracts
carry federal single-month and all-months-combined limits; outside the spot
month everything else is governed by exchange-set limits or accountability
levels. Some spot-month limits also step down inside the spot month (the
CFTC-published Live Cattle limit steps 600 -> 300 -> 200). ``CFTCLimitSpec``
therefore holds three independently optional limits, and a limit left as
``None`` is *not tested* rather than silently treated as zero or infinite.

Limitations (deliberate, and load-bearing - read before relying on this)
------------------------------------------------------------------------
- **No futures-equivalent conversion.** Sec. 150.2 combines futures with
  futures-equivalent options and economically equivalent swaps. This engine
  does no delta conversion: option positions must already be supplied on a
  futures-equivalent basis for the limit tests to mean anything. Reporting
  buckets, by contrast, are counted in raw option contracts, which is what
  Sec. 15.00(p)(1)(ii) tests.
- **No exemption adjudication.** ``is_bona_fide_hedge`` excludes a position
  from the *limit* tests only, because Sec. 150.3 exempts bona fide hedges from
  limits. It does not exclude it from the *reporting* test: Sec. 15.00(p)
  contains no hedging carve-out, so a hedger's position is still reportable.
  Whether a position actually qualifies as a bona fide hedge is a legal
  determination this engine does not make and cannot make.
- **No Sec. 150.4 aggregation adjudication.** The caller decides which accounts
  belong to the entity. Sec. 150.4(a)(1) requires aggregating all positions in
  accounts where a person "directly or indirectly controls trading or holds a
  10 percent or greater ownership or equity interest", subject to eight
  categories of exemption in Sec. 150.4(b) (independent account controllers,
  independently operated owned entities, and so on). Feed this engine the
  post-aggregation set; it verifies internal consistency, not entitlement.
- **No swaps.** Part 20 large swaps trader reporting is out of scope, and its
  routine position reports were sunset by the Commission effective 21 July 2026
  (Release 9269-26). Economically equivalent swaps still count toward Part 150
  limits and must be converted and supplied by the caller if relevant.
- **End-of-day semantics for reporting, continuous for limits.** Sec. 15.00(p)
  is a close-of-market test. Sec. 150.2 prohibits *holding or controlling* an
  excess position, which is not limited to the close. Run limit checks
  intraday; the reporting flag is only meaningful on an end-of-day snapshot.

Levels change. ``CFTCLimitSpec`` is caller-supplied on purpose: nothing in this
module hard-codes a reporting level or a limit. See ``references/standards.md``
for where the authoritative tables live.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

FUTURE = "FUTURE"
OPTION = "OPTION"
_INSTRUMENT_CLASSES = (FUTURE, OPTION)

SIDE_NONE = "NONE"
SIDE_LONG = "LONG"
SIDE_SHORT = "SHORT"
SIDE_BOTH = "BOTH"

LIMIT_SPOT_MONTH = "SPOT_MONTH"
LIMIT_SINGLE_MONTH = "SINGLE_MONTH"
LIMIT_ALL_MONTHS_COMBINED = "ALL_MONTHS_COMBINED"

STATUS_DISABLED = "ENGINE_DISABLED"
STATUS_BELOW = "BELOW_REPORTING_LEVEL"
STATUS_REPORTABLE = "FORM_102A_REPORTABLE"
STATUS_BREACHED = "SPECULATIVE_LIMIT_BREACHED"


@dataclass
class Config:
    """Engine configuration.

    ``enabled=False`` short-circuits evaluation and returns a report with
    ``status='ENGINE_DISABLED'`` and both flags ``False``. That report asserts
    nothing about the positions; never treat a disabled result as evidence of
    compliance.
    """

    enabled: bool = True


@dataclass(frozen=True)
class TraderAccountPosition:
    """One account's open position in one contract month of one commodity.

    Gross long and gross short are supplied separately and net is *derived*.
    A caller-supplied net is not accepted, because a net that disagrees with
    its own components is exactly the input that produces a missed reportable
    position (see the module docstring).

    ``contract_month`` is the bucket key for the Sec. 15.00(p)(1)(i) test - one
    future of one commodity. Any stable per-expiry label works ('2026-12',
    'DEC26'); it is compared as an opaque string, so it must be spelled
    consistently across accounts or positions will not aggregate.

    ``is_bona_fide_hedge`` excludes the position from Part 150 limit tests
    only. It never excludes it from the reporting-level test.
    """

    account_id: str
    entity_name: str
    commodity_code: str
    contract_month: str
    long_position: float = 0.0
    short_position: float = 0.0
    instrument_class: str = FUTURE
    is_bona_fide_hedge: bool = False

    @property
    def net_position(self) -> float:
        """Signed net contracts: positive is net long, negative is net short."""
        return self.long_position - self.short_position


@dataclass(frozen=True)
class CFTCLimitSpec:
    """Reporting level and federal limits for one commodity.

    All three limits are independently optional and a ``None`` limit is **not
    tested**. This matters: for a non-legacy contract such as NYMEX crude oil
    there is no federal single-month or all-months-combined limit at all, and
    inventing one produces false breaches. Conversely, configuring a limit as
    ``0.0`` means "no position permitted", which is a real constraint and is
    tested.

    Levels are not hard-coded anywhere in this module. Reporting levels come
    from the Sec. 15.03(b) table; federal limit levels come from Appendix E to
    Part 150. Both are amended from time to time - resolve them at the
    evaluation date and record what you used.
    """

    commodity_code: str
    reporting_threshold_contracts: float
    spot_month_limit: Optional[float] = None
    single_month_limit: Optional[float] = None
    all_months_combined_limit: Optional[float] = None


@dataclass(frozen=True)
class ContractMonthPosition:
    """Aggregated exposure in one reporting bucket, and its reporting verdict.

    A bucket is (contract_month, instrument_class): Sec. 15.00(p)(1) tests
    futures and the options exercising into them as separate buckets.
    """

    contract_month: str
    instrument_class: str
    gross_long: float
    gross_short: float
    net_position: float
    is_reportable: bool
    reportable_side: str


@dataclass(frozen=True)
class LimitBreach:
    """One Part 150 limit test that came back over the line."""

    limit_type: str
    contract_month: Optional[str]
    net_position: float
    limit: float
    excess: float


@dataclass(frozen=True)
class CFTCLargeTraderReport:
    """Outcome of one entity/commodity evaluation.

    ``is_reportable`` and ``is_limit_breached`` are independent. ``status`` is
    a single label with breach taking precedence, so a caller that reads only
    ``status`` on a breached position cannot see that the position is also
    reportable. Read the flags, not the label, when both matter.

    **The aggregate figures and the breach figures have different populations.**
    ``aggregated_net_position``, ``aggregated_gross_long`` and
    ``aggregated_gross_short`` describe the whole book, hedges included, because
    that is the population the reporting test runs on. Each ``LimitBreach``
    carries a net computed *excluding* bona fide hedges, because that is the
    population Sec. 150.3 leaves subject to limits. The two will not tie out
    whenever ``hedge_exempt_contracts_excluded`` is non-zero, and that is
    correct rather than an inconsistency.
    """

    entity_name: str
    commodity_code: str
    as_of: str
    aggregated_net_position: float
    aggregated_gross_long: float
    aggregated_gross_short: float
    month_detail: Tuple[ContractMonthPosition, ...]
    reportable_buckets: Tuple[str, ...]
    reporting_threshold: float
    is_reportable: bool
    breaches: Tuple[LimitBreach, ...]
    is_limit_breached: bool
    hedge_exempt_contracts_excluded: float
    limits_not_tested: Tuple[str, ...]
    status: str
    audit_notes: str


def _require_finite_non_negative(value: float, label: str) -> float:
    """Coerce to float, rejecting booleans, non-numerics, NaN/Inf and negatives.

    Gross legs are magnitudes. A negative short leg silently flips the sign of
    the net position and turns a breach into a clean report, so it is rejected
    rather than normalised.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite, got {numeric!r}")
    if numeric < 0.0:
        raise ValueError(
            f"{label} must be non-negative - long and short legs are gross "
            f"magnitudes, not signed quantities, got {numeric!r}"
        )
    return numeric


def _require_optional_limit(value: Optional[float], label: str) -> Optional[float]:
    """``None`` means the limit does not exist and must not be tested."""
    if value is None:
        return None
    return _require_finite_non_negative(value, label)


class PositionLimitReportingCFTCLargeTraderEngine:
    """Screens an entity's aggregated positions against CFTC reporting levels
    and Part 150 federal speculative position limits.

    The engine validates rather than assumes: positions that do not belong to
    the named entity, do not match the limit spec's commodity, or duplicate an
    already-supplied bucket raise ``ValueError``. Silent tolerance of those
    inputs is how an aggregation bug becomes a missed filing.
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()

    def evaluate_entity_cftc_compliance(
        self,
        entity_name: str,
        commodity_positions: Sequence[TraderAccountPosition],
        limit_spec: CFTCLimitSpec,
        spot_month: Optional[str] = None,
        as_of: str = "",
    ) -> CFTCLargeTraderReport:
        """Aggregate ``commodity_positions`` for ``entity_name`` and evaluate
        reporting-level and federal limit status.

        Args:
            entity_name: Legal entity the positions are aggregated under. Every
                position must carry this exact ``entity_name``.
            commodity_positions: Post-aggregation account positions, all in the
                commodity named by ``limit_spec``. May be empty, which yields a
                zero report explicitly annotated as such.
            limit_spec: Reporting level and whichever federal limits apply.
            spot_month: The ``contract_month`` label currently in its spot
                month. Required whenever ``spot_month_limit`` is set - without
                it the spot-month test cannot run, and silently skipping a
                configured limit test is worse than refusing to run.
            as_of: Free-form snapshot label recorded in the report. The
                reporting-level test is a close-of-market test; record which
                close this is.

        Returns:
            A ``CFTCLargeTraderReport``.

        Raises:
            ValueError: on any inconsistent, duplicated, non-finite, negative
                or misattributed input, and on a configured spot-month limit
                with no ``spot_month`` supplied.
        """
        if not isinstance(entity_name, str) or not entity_name.strip():
            raise ValueError("entity_name must be a non-empty string")

        reporting_threshold = _require_finite_non_negative(
            limit_spec.reporting_threshold_contracts,
            "limit_spec.reporting_threshold_contracts",
        )
        spot_limit = _require_optional_limit(
            limit_spec.spot_month_limit, "limit_spec.spot_month_limit"
        )
        single_limit = _require_optional_limit(
            limit_spec.single_month_limit, "limit_spec.single_month_limit"
        )
        all_months_limit = _require_optional_limit(
            limit_spec.all_months_combined_limit,
            "limit_spec.all_months_combined_limit",
        )

        if not self.config.enabled:
            return CFTCLargeTraderReport(
                entity_name=entity_name,
                commodity_code=limit_spec.commodity_code,
                as_of=as_of,
                aggregated_net_position=0.0,
                aggregated_gross_long=0.0,
                aggregated_gross_short=0.0,
                month_detail=(),
                reportable_buckets=(),
                reporting_threshold=reporting_threshold,
                is_reportable=False,
                breaches=(),
                is_limit_breached=False,
                hedge_exempt_contracts_excluded=0.0,
                limits_not_tested=(
                    LIMIT_SPOT_MONTH,
                    LIMIT_SINGLE_MONTH,
                    LIMIT_ALL_MONTHS_COMBINED,
                ),
                status=STATUS_DISABLED,
                audit_notes=(
                    "Engine disabled - no evaluation performed. This report "
                    "asserts nothing about the entity's positions and is not "
                    "evidence of compliance."
                ),
            )

        if spot_limit is not None and spot_month is None:
            raise ValueError(
                "limit_spec.spot_month_limit is configured but spot_month was "
                "not supplied - the spot-month test would be silently skipped"
            )

        buckets, hedge_excluded = self._aggregate(
            entity_name, commodity_positions, limit_spec.commodity_code
        )

        month_detail = self._build_month_detail(buckets, reporting_threshold)
        reportable_buckets = tuple(
            "{0}/{1}".format(m.contract_month, m.instrument_class)
            for m in month_detail
            if m.is_reportable
        )
        is_reportable = bool(reportable_buckets)

        gross_long = sum(m.gross_long for m in month_detail)
        gross_short = sum(m.gross_short for m in month_detail)
        total_net = gross_long - gross_short

        breaches, limits_not_tested = self._audit_limits(
            positions=commodity_positions,
            spot_month=spot_month,
            spot_limit=spot_limit,
            single_limit=single_limit,
            all_months_limit=all_months_limit,
        )
        is_limit_breached = bool(breaches)

        if is_limit_breached:
            status = STATUS_BREACHED
        elif is_reportable:
            status = STATUS_REPORTABLE
        else:
            status = STATUS_BELOW

        notes = self._build_notes(
            entity_name=entity_name,
            limit_spec=limit_spec,
            status=status,
            position_count=len(commodity_positions),
            gross_long=gross_long,
            gross_short=gross_short,
            total_net=total_net,
            reporting_threshold=reporting_threshold,
            reportable_buckets=reportable_buckets,
            breaches=breaches,
            limits_not_tested=limits_not_tested,
            hedge_excluded=hedge_excluded,
        )

        if is_limit_breached:
            logger.critical("CFTC POSITION LIMIT BREACH: %s", notes)
        elif is_reportable:
            logger.warning("CFTC REPORTING LEVEL REACHED: %s", notes)
        else:
            logger.info("%s", notes)

        return CFTCLargeTraderReport(
            entity_name=entity_name,
            commodity_code=limit_spec.commodity_code,
            as_of=as_of,
            aggregated_net_position=total_net,
            aggregated_gross_long=gross_long,
            aggregated_gross_short=gross_short,
            month_detail=month_detail,
            reportable_buckets=reportable_buckets,
            reporting_threshold=reporting_threshold,
            is_reportable=is_reportable,
            breaches=breaches,
            is_limit_breached=is_limit_breached,
            hedge_exempt_contracts_excluded=hedge_excluded,
            limits_not_tested=limits_not_tested,
            status=status,
            audit_notes=notes,
        )

    def _aggregate(
        self,
        entity_name: str,
        positions: Sequence[TraderAccountPosition],
        commodity_code: str,
    ) -> Tuple[Dict[Tuple[str, str], List[float]], float]:
        """Validate every position and fold it into its reporting bucket.

        Returns the bucket map keyed by (contract_month, instrument_class) and
        the gross contract count excluded from the limit tests as bona fide
        hedges.
        """
        buckets: Dict[Tuple[str, str], List[float]] = {}
        seen: Set[Tuple[str, str, str, bool]] = set()
        hedge_excluded = 0.0

        for index, position in enumerate(positions):
            where = "commodity_positions[{0}]".format(index)
            if not isinstance(position, TraderAccountPosition):
                raise ValueError(
                    "{0} must be a TraderAccountPosition, got {1}".format(
                        where, type(position).__name__
                    )
                )
            if position.entity_name != entity_name:
                raise ValueError(
                    "{0} belongs to entity {1!r}, not {2!r} - aggregating "
                    "another entity's position into this evaluation would "
                    "corrupt both the reporting and the limit test".format(
                        where, position.entity_name, entity_name
                    )
                )
            if position.commodity_code != commodity_code:
                raise ValueError(
                    "{0} is commodity {1!r} but the limit spec is for {2!r} - "
                    "reporting levels and limits are per commodity and must "
                    "not be pooled".format(
                        where, position.commodity_code, commodity_code
                    )
                )
            if position.instrument_class not in _INSTRUMENT_CLASSES:
                raise ValueError(
                    "{0}.instrument_class must be one of {1}, got {2!r}".format(
                        where, _INSTRUMENT_CLASSES, position.instrument_class
                    )
                )
            if (
                not isinstance(position.contract_month, str)
                or not position.contract_month.strip()
            ):
                raise ValueError(
                    "{0}.contract_month must be a non-empty string".format(where)
                )
            if (
                not isinstance(position.account_id, str)
                or not position.account_id.strip()
            ):
                raise ValueError(
                    "{0}.account_id must be a non-empty string".format(where)
                )

            long_leg = _require_finite_non_negative(
                position.long_position, "{0}.long_position".format(where)
            )
            short_leg = _require_finite_non_negative(
                position.short_position, "{0}.short_position".format(where)
            )

            key = (
                position.account_id,
                position.contract_month,
                position.instrument_class,
                position.is_bona_fide_hedge,
            )
            if key in seen:
                raise ValueError(
                    "{0} duplicates account {1!r} in {2!r}/{3} - the same "
                    "account position supplied twice is double counted and "
                    "inflates both tests; consolidate the account's legs into "
                    "one record before evaluating".format(
                        where,
                        position.account_id,
                        position.contract_month,
                        position.instrument_class,
                    )
                )
            seen.add(key)

            bucket = buckets.setdefault(
                (position.contract_month, position.instrument_class), [0.0, 0.0]
            )
            bucket[0] += long_leg
            bucket[1] += short_leg

            if position.is_bona_fide_hedge:
                hedge_excluded += long_leg + short_leg

        return buckets, hedge_excluded

    @staticmethod
    def _build_month_detail(
        buckets: Dict[Tuple[str, str], List[float]],
        reporting_threshold: float,
    ) -> Tuple[ContractMonthPosition, ...]:
        """Apply the Sec. 15.00(p)(1) side-by-side gross test to each bucket.

        The comparison is ``>=``: Sec. 15.00(p) says "equals or exceeds", so a
        position exactly at the reporting level is reportable. Each side is
        tested on its own; the sides are never summed and never netted.
        """
        detail: List[ContractMonthPosition] = []
        for (month, instrument), (gross_long, gross_short) in sorted(buckets.items()):
            long_hit = gross_long >= reporting_threshold
            short_hit = gross_short >= reporting_threshold
            if long_hit and short_hit:
                side = SIDE_BOTH
            elif long_hit:
                side = SIDE_LONG
            elif short_hit:
                side = SIDE_SHORT
            else:
                side = SIDE_NONE
            detail.append(
                ContractMonthPosition(
                    contract_month=month,
                    instrument_class=instrument,
                    gross_long=gross_long,
                    gross_short=gross_short,
                    net_position=gross_long - gross_short,
                    is_reportable=side != SIDE_NONE,
                    reportable_side=side,
                )
            )
        return tuple(detail)

    @staticmethod
    def _audit_limits(
        positions: Sequence[TraderAccountPosition],
        spot_month: Optional[str],
        spot_limit: Optional[float],
        single_limit: Optional[float],
        all_months_limit: Optional[float],
    ) -> Tuple[Tuple[LimitBreach, ...], Tuple[str, ...]]:
        """Apply the Sec. 150.2 net tests to non-hedge positions.

        The comparison is strict ``>``: Sec. 150.2 prohibits positions "in
        excess of" the level, so a position exactly at the limit is not a
        breach. This is deliberately one contract different from the
        reporting-level boundary above.

        Options are netted with futures on the assumption that the caller has
        already converted them to a futures-equivalent basis; this engine
        performs no delta conversion.
        """
        net_by_month: Dict[str, float] = {}
        for position in positions:
            if position.is_bona_fide_hedge:
                continue
            net_by_month[position.contract_month] = (
                net_by_month.get(position.contract_month, 0.0)
                + float(position.long_position)
                - float(position.short_position)
            )

        breaches: List[LimitBreach] = []
        not_tested: List[str] = []

        if spot_limit is None:
            not_tested.append(LIMIT_SPOT_MONTH)
        else:
            spot_net = net_by_month.get(spot_month, 0.0)
            if abs(spot_net) > spot_limit:
                breaches.append(
                    LimitBreach(
                        limit_type=LIMIT_SPOT_MONTH,
                        contract_month=spot_month,
                        net_position=spot_net,
                        limit=spot_limit,
                        excess=abs(spot_net) - spot_limit,
                    )
                )

        if single_limit is None:
            not_tested.append(LIMIT_SINGLE_MONTH)
        else:
            for month in sorted(net_by_month):
                month_net = net_by_month[month]
                if abs(month_net) > single_limit:
                    breaches.append(
                        LimitBreach(
                            limit_type=LIMIT_SINGLE_MONTH,
                            contract_month=month,
                            net_position=month_net,
                            limit=single_limit,
                            excess=abs(month_net) - single_limit,
                        )
                    )

        if all_months_limit is None:
            not_tested.append(LIMIT_ALL_MONTHS_COMBINED)
        else:
            combined_net = sum(net_by_month.values())
            if abs(combined_net) > all_months_limit:
                breaches.append(
                    LimitBreach(
                        limit_type=LIMIT_ALL_MONTHS_COMBINED,
                        contract_month=None,
                        net_position=combined_net,
                        limit=all_months_limit,
                        excess=abs(combined_net) - all_months_limit,
                    )
                )

        return tuple(breaches), tuple(not_tested)

    @staticmethod
    def _build_notes(
        entity_name: str,
        limit_spec: CFTCLimitSpec,
        status: str,
        position_count: int,
        gross_long: float,
        gross_short: float,
        total_net: float,
        reporting_threshold: float,
        reportable_buckets: Tuple[str, ...],
        breaches: Tuple[LimitBreach, ...],
        limits_not_tested: Tuple[str, ...],
        hedge_excluded: float,
    ) -> str:
        """Build a single audit string that states what was tested and, just as
        importantly, what was not."""
        parts = [
            "CFTC LTR AUDIT [{0} '{1}' - {2}]:".format(
                entity_name, limit_spec.commodity_code, status
            ),
            "gross long = {0:,.2f}, gross short = {1:,.2f}, net (all months) = "
            "{2:,.2f} contracts.".format(gross_long, gross_short, total_net),
            "Reporting level = {0:,.2f} (>= test, per future and per option "
            "bucket, each side separately);".format(reporting_threshold),
        ]
        if reportable_buckets:
            parts.append(
                "reportable buckets = " + ", ".join(reportable_buckets) + "."
            )
        else:
            parts.append("no bucket reaches the reporting level.")

        if breaches:
            parts.append(
                "Part 150 breaches: "
                + "; ".join(
                    "{0}{1} net {2:,.2f} vs limit {3:,.2f} (excess {4:,.2f})".format(
                        b.limit_type,
                        " [{0}]".format(b.contract_month) if b.contract_month else "",
                        b.net_position,
                        b.limit,
                        b.excess,
                    )
                    for b in breaches
                )
                + "."
            )
        else:
            parts.append("no Part 150 limit exceeded among the limits tested.")

        if limits_not_tested:
            parts.append(
                "Limits NOT tested (no level configured): "
                + ", ".join(limits_not_tested)
                + " - absence of a breach here is not evidence of compliance."
            )
        if hedge_excluded:
            parts.append(
                "{0:,.2f} gross contracts flagged bona fide hedge were excluded "
                "from the limit tests but remain in the reporting test.".format(
                    hedge_excluded
                )
            )
        if position_count == 0:
            parts.append(
                "WARNING: no positions supplied - this is a zero report, not a "
                "verified-flat report."
            )
        return " ".join(parts)
