"""Supply-chain read-through -> a standardized revenue-surprise score for one target company.

What this does
--------------
Takes point-in-time observations of (a) the reported revenue growth of the
target's *upstream suppliers* and (b) the inventory growth of the target's
*downstream customers*, weights each by its disclosed concentration, blends them
into an implied revenue growth rate for the target, and standardizes the gap
against the sell-side consensus:

    weighted_supplier_growth = sum(w_i * g_i) / sum(w_i)   over usable suppliers
    weighted_customer_invty  = sum(v_j * h_j) / sum(v_j)   over usable customers
    implied_revenue_growth   = Ws * weighted_supplier_growth
                               - Wc * weighted_customer_invty
    gap                      = implied_revenue_growth - consensus_revenue_growth
    Z                        = gap / consensus_dispersion

Why the two terms have the signs they do
----------------------------------------
- **Suppliers, positive.** A supplier's revenue is the target's purchase-order
  stream, observed one step earlier in the chain. Cohen & Frazzini (2008),
  *Journal of Finance* 63(4), 1977-2011, show that news about an economically
  linked firm is not promptly incorporated by the market; Menzly & Ozbas (2010),
  *Journal of Finance* 65(4), 1555-1580, show that supplier and customer
  industries cross-predict each other's returns in both directions.
- **Customer inventory, negative.** Inventory accumulated at the target's
  customers is demand already shipped and not yet sold; it gets worked off by
  cutting the next order. Thomas & Zhang (2002), *Review of Accounting Studies*
  7, 163-187, find the accrual anomaly of Sloan (1996) is driven mainly by
  inventory changes, with inventory build predicting lower subsequent returns.

Bullwhip: why the blend weights are below one
---------------------------------------------
Lee, Padmanabhan & Whang (1997), "Information Distortion in a Supply Chain: The
Bullwhip Effect", *Management Science* 43(4), 546-558, show that order variance
exceeds sales variance and that the distortion *grows as one moves upstream*,
driven by demand signal processing, the rationing game, order batching and price
variation. A supplier's revenue swing is therefore an *amplified* image of the
target's end demand, so reading it straight back down the chain overstates the
move. `supplier_blend_weight < 1.0` damps it. The size of the damping is a house
calibration, not a published constant -- see `references/standards.md`.

Units contract (read before wiring this up)
-------------------------------------------
`consensus_revenue_growth_pct` MUST be a **revenue** growth consensus, in the
same period-over-period convention (normally year-over-year for the same fiscal
quarter) and the same percentage units as the supplier and customer inputs.

It MUST NOT be an EPS growth consensus. This module estimates a top-line growth
rate; differencing it against an EPS expectation subtracts two different
quantities and yields a number with no interpretation. Translating revenue into
EPS requires incremental-margin and share-count assumptions this engine does not
make and cannot infer. Version 1.0.0 of this skill made exactly that
subtraction; it is the defect this version exists to fix.

`consensus_dispersion_pct` is the standard deviation used to standardize the gap
and is **required per call** -- there is no default. Two defensible choices: the
cross-analyst dispersion of the revenue estimates for this company-quarter (the
Mendenhall-style SUE denominator), or the historical standard deviation of *this
model's own* realized gap for this name. They are not interchangeable, and
whichever is used must be the one `surprise_z_threshold` was calibrated against.
The engine cannot tell which it was handed.

Point-in-time discipline
------------------------
Every observation carries `available_from_iso`: the instant the figure became
public -- the supplier's earnings release or the SEC filing acceptance timestamp,
never the fiscal period end. The two are far apart by construction: Form 10-Q is
due 40 days after quarter end for large accelerated and accelerated filers and 45
days for everyone else, and Form 10-K is due 60 / 75 / 90 days after year end for
large accelerated / accelerated / non-accelerated filers (Exchange Act Rule 12b-2
filer categories; General Instructions to Forms 10-K and 10-Q). Observations
stamped after `as_of` are excluded and counted, and `as_of` is required on every
call, so there is no path that silently reads the future.

What the engine cannot check
----------------------------
- **Fiscal-period alignment.** Whether supplier quarter t-tau was correctly
  aligned to target quarter t is decided upstream. `lead_time_months` is recorded
  in the output for reproducibility; it is not a computation input.
- **Whether the growth rates are like-for-like.** Organic vs. reported, constant
  vs. current currency, and 52/53-week retail calendars all change the number.
- **Graph completeness.** See below.

Graph completeness is structurally limited
------------------------------------------
The public-filing supply-chain graph is truncated by design. ASC 280-10-50-42
requires a public entity to disclose the *fact* and *amount* of revenue from any
single external customer at or above 10% of its revenues, but states explicitly
that "the public entity need not disclose the identity of a major customer."
Relationships below 10% need not be disclosed at all. Separately, the SEC's 2020
modernization of Regulation S-K (Release 33-10825, effective 9 November 2020)
replaced the prescriptive requirement to name 10%+ customers in Item 101(c) with
a principles-based description of any dependence on major customers, so named
counterparties became scarcer, not more common, after that date. A vendor graph
built on these filings is therefore incomplete, name-ambiguous, and its coverage
is not stable through time. `supplier_coverage_pct` in the output reports how
much of the target's declared input spend was actually observed; it is not a
formality.

Limitations (documented, deliberate)
------------------------------------
- **The normalized weighted mean extrapolates.** Dividing by the observed weight
  total assumes the unobserved suppliers grew like the observed ones. That is the
  only assumption available, and it is why `supplier_coverage_pct` is reported
  and why `min_supplier_coverage_pct` gates the signal off below a floor.
- **A supplier's total revenue growth is not its revenue growth from the target.**
  Where the target is a small share of the supplier's book, that growth is mostly
  other customers. `min_read_through_share_pct` screens those out.
- **Consensus is a moving target.** The engine compares against the consensus it
  is handed; a backtest must use the consensus as it stood at `as_of`, not the
  final pre-announcement print.
- **No industry or macro control.** A supplier growing 20% in a sector growing
  25% is a negative read-through that this engine scores positive.
- **One target, one period.** Cross-sectional ranking, panel construction and
  sector neutralization live upstream.
- **This is a research signal, not an order instruction.** `BUY_EARNINGS_SURPRISE`
  is a directional bias on a fundamental estimate. Sizing, stops and exposure
  limits are owned elsewhere -- see the Related Skills in `SKILL.md`.
- **Alternative supply-chain data can carry material non-public information.** The
  controls for that are in `insider-trading-controls-for-alternative-data-usage`,
  not here.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

SIGNAL_BUY = "BUY_EARNINGS_SURPRISE"
SIGNAL_SELL = "SELL_EARNINGS_DISAPPOINTMENT"
SIGNAL_NEUTRAL = "NEUTRAL"
SIGNAL_INSUFFICIENT = "INSUFFICIENT_DATA"

# House defaults. None of these is a published constant; see references/standards.md.
DEFAULT_SUPPLIER_BLEND_WEIGHT = 0.70
DEFAULT_INVENTORY_BLEND_WEIGHT = 0.30
DEFAULT_SURPRISE_Z_THRESHOLD = 1.0
DEFAULT_LEAD_TIME_MONTHS = 3

# Both coverage floors are anchored to the 10% materiality line that
# ASC 280-10-50-42 itself uses for customer concentration. The anchoring is an
# analogy, not a requirement: no standard sets a floor for this calculation.
DEFAULT_MIN_SUPPLIER_COVERAGE_PCT = 10.0
DEFAULT_MIN_READ_THROUGH_SHARE_PCT = 10.0


def _require_finite(name: str, value: float) -> float:
    """Coerce to float and reject NaN/Inf.

    A NaN growth rate propagates to a NaN Z-score, and every comparison against a
    NaN is False, so an unguarded banding chain falls through to a confident
    `NEUTRAL` built on missing data. That is the most dangerous silent failure in
    this module, so nothing numeric enters the arithmetic unchecked.
    """
    # `bool` is an `int` subclass and `str` converts cleanly, so both are refused
    # explicitly; NumPy scalars and `Decimal` convert and are accepted, because a
    # pandas-fed caller should not have to unwrap every value by hand.
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a real number, got {value!r}") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite (not NaN or infinity), got {value!r}")
    return numeric


def _require_pct_share(name: str, value: float) -> float:
    """A share of a whole, expressed in percentage points, in [0, 100]."""
    numeric = _require_finite(name, value)
    if not 0.0 <= numeric <= 100.0:
        raise ValueError(f"{name} must be a percentage share in [0, 100], got {numeric}")
    return numeric


def _require_ticker(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _parse_iso_utc(name: str, value: str) -> datetime:
    """Parse a timezone-aware ISO-8601 timestamp into UTC; reject naive values.

    A naive availability stamp carries no time base, so comparing it against
    `as_of` silently mixes clocks. An earnings release lands at a specific instant
    and the whole purpose of the comparison is deciding which side of that instant
    a backtest is standing on, so a bare date is refused rather than assumed UTC.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty ISO-8601 string, got {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware (e.g. '2026-08-05T12:00:00Z'); a naive "
            f"timestamp cannot be placed on either side of a point-in-time cutoff, "
            f"got {value!r}"
        )
    return parsed.astimezone(timezone.utc)


def _format_utc(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


@dataclass
class Config:
    """DEPRECATED container retained for import compatibility.

    Carries no engine settings and is read by nothing. Configure
    `SupplyChainDataForEarningsPredictionEngine` through its constructor.
    """
    name: str = "supply-chain-data-for-earnings-prediction"


class Engine:
    """DEPRECATED no-op shim retained for import compatibility.

    `run()` returns True unconditionally and computes nothing. Use
    `SupplyChainDataForEarningsPredictionEngine`.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run(self) -> bool:
        return True


@dataclass(frozen=True)
class SupplierCustomerLink:
    """DEPRECATED graph-edge descriptor retained for import compatibility.

    Superseded by `SupplierObservation`. This type carries neither a point-in-time
    availability stamp nor the supplier's share of the target's input spend, so it
    cannot be scored -- and version 1.0.0 never read it anywhere, which is why that
    version's documented "customer concentration weighting" did not in fact exist
    in the code.
    """
    supplier_ticker: str
    customer_ticker: str
    revenue_dependency_pct: float       # % of the supplier's revenue coming from the customer
    lead_time_months: int = DEFAULT_LEAD_TIME_MONTHS


@dataclass(frozen=True)
class SupplierObservation:
    """One upstream supplier's reported growth, with the weights needed to use it.

    Attributes:
        supplier_ticker: Identifier of the supplier. Must be unique within a batch;
            the same link counted twice double-weights it.
        revenue_growth_pct: The supplier's reported revenue growth for the period
            aligned to the target's period, in percentage points (25.0 for +25%).
            Must use the same convention as the consensus it is compared against.
        supplier_share_of_target_cogs_pct: This supplier's share of the target's
            input spend, in [0, 100]. This is the weight actually applied.
        target_share_of_supplier_revenue_pct: The target's share of *this
            supplier's* revenue, in [0, 100] -- the ASC 280-style concentration
            figure. Governs how much of the supplier's total growth is about the
            target at all; screened by `min_read_through_share_pct`.
        available_from_iso: Timezone-aware ISO-8601 instant at which this figure
            became public (earnings release or SEC filing acceptance), never the
            fiscal period end.
    """
    supplier_ticker: str
    revenue_growth_pct: float
    supplier_share_of_target_cogs_pct: float
    target_share_of_supplier_revenue_pct: float
    available_from_iso: str


@dataclass(frozen=True)
class CustomerObservation:
    """One downstream customer's inventory growth, with the weight needed to use it.

    Attributes:
        customer_ticker: Identifier of the customer. Must be unique within a batch.
        inventory_growth_pct: The customer's reported inventory growth for the
            aligned period, in percentage points. Positive means inventory built.
        customer_share_of_target_revenue_pct: This customer's share of the target's
            revenue, in [0, 100]. This is the weight actually applied.
        available_from_iso: Timezone-aware ISO-8601 instant at which this figure
            became public.
    """
    customer_ticker: str
    inventory_growth_pct: float
    customer_share_of_target_revenue_pct: float
    available_from_iso: str


@dataclass
class SupplyChainEarningsSignal:
    """Full audit record of one supply-chain read-through evaluation.

    `surprise_z_score`, `implied_revenue_growth_pct` and `consensus_revenue_gap_pct`
    are `None` exactly when `is_signal_measurable` is False. `None` means "not
    measurable" and must never be rendered as 0.0 -- a zero gap is a measurement
    that the chain and the consensus agreed.
    """
    as_of_iso: str
    asset_id: str
    directional_signal: str
    is_signal_measurable: bool
    surprise_z_score: Optional[float]
    implied_revenue_growth_pct: Optional[float]
    consensus_revenue_gap_pct: Optional[float]
    consensus_revenue_growth_pct: float
    consensus_dispersion_pct: float
    weighted_supplier_revenue_growth_pct: Optional[float]
    weighted_customer_inventory_growth_pct: float
    supplier_coverage_pct: float
    customer_coverage_pct: float
    suppliers_submitted: int
    suppliers_used: int
    customers_submitted: int
    customers_used: int
    future_observations_excluded_count: int
    stale_observations_excluded_count: int
    low_read_through_excluded_count: int
    declared_lead_time_months: int
    audit_notes: List[str] = field(default_factory=list)


class SupplyChainDataForEarningsPredictionEngine:
    """Concentration-weighted supply-chain read-through into a revenue-surprise Z-score.

    Every threshold is a constructor parameter and every one of them is read by
    `evaluate_supply_chain_lead_signal`. Persist the configuration alongside the
    output: the Z-score is not reproducible without it.
    """

    def __init__(
        self,
        supplier_blend_weight: float = DEFAULT_SUPPLIER_BLEND_WEIGHT,
        inventory_blend_weight: float = DEFAULT_INVENTORY_BLEND_WEIGHT,
        surprise_z_threshold: float = DEFAULT_SURPRISE_Z_THRESHOLD,
        min_supplier_coverage_pct: float = DEFAULT_MIN_SUPPLIER_COVERAGE_PCT,
        min_read_through_share_pct: float = DEFAULT_MIN_READ_THROUGH_SHARE_PCT,
        max_observation_age_days: Optional[float] = None,
        lead_time_months: int = DEFAULT_LEAD_TIME_MONTHS,
    ):
        """
        Args:
            supplier_blend_weight: Damping applied to the weighted supplier growth.
                Below 1.0 because bullwhip amplification makes the upstream swing
                larger than the target's end-demand swing. House default 0.70.
            inventory_blend_weight: Weight on the customer inventory drag term.
                House default 0.30.
            surprise_z_threshold: |Z| at or beyond which a directional signal is
                emitted; inclusive at the boundary. Default 1.0.
            min_supplier_coverage_pct: Minimum share of the target's input spend
                the surviving supplier observations must cover before a Z-score is
                produced at all. Below it the result is `INSUFFICIENT_DATA`. Set
                0.0 to disable.
            min_read_through_share_pct: Suppliers for whom the target is a smaller
                share of revenue than this are dropped, because their total growth
                is mostly about other customers. Set 0.0 to disable.
            max_observation_age_days: Optional staleness bound. Observations
                published more than this many days before `as_of` are excluded.
                `None` applies no bound, which makes staleness the caller's problem
                and is why the stale counter can read zero.
            lead_time_months: Declared supplier-to-target alignment lag, recorded in
                the output for reproducibility. The engine cannot verify that the
                caller actually applied it.

        Raises:
            ValueError: on a non-finite or negative blend weight, both blend weights
                zero, a non-positive `surprise_z_threshold`, a coverage or
                read-through floor outside [0, 100], a non-positive
                `max_observation_age_days`, or a negative `lead_time_months`.
        """
        self.supplier_blend_weight = _require_finite(
            "supplier_blend_weight", supplier_blend_weight)
        self.inventory_blend_weight = _require_finite(
            "inventory_blend_weight", inventory_blend_weight)
        if self.supplier_blend_weight < 0.0 or self.inventory_blend_weight < 0.0:
            raise ValueError(
                "blend weights must be >= 0; the sign of each term is fixed by the "
                "economics (supplier growth positive, inventory build negative) and "
                "flipping it via a negative weight would invert the model silently"
            )
        if self.supplier_blend_weight == 0.0 and self.inventory_blend_weight == 0.0:
            raise ValueError(
                "supplier_blend_weight and inventory_blend_weight cannot both be 0; "
                "the implied growth would be identically 0 for every input"
            )

        self.surprise_z_threshold = _require_finite(
            "surprise_z_threshold", surprise_z_threshold)
        if self.surprise_z_threshold <= 0.0:
            raise ValueError(
                f"surprise_z_threshold must be > 0, got {self.surprise_z_threshold}; "
                "a threshold of 0 emits a directional signal on every non-zero gap"
            )

        self.min_supplier_coverage_pct = _require_pct_share(
            "min_supplier_coverage_pct", min_supplier_coverage_pct)
        self.min_read_through_share_pct = _require_pct_share(
            "min_read_through_share_pct", min_read_through_share_pct)

        if max_observation_age_days is None:
            self.max_observation_age_days: Optional[float] = None
        else:
            age = _require_finite("max_observation_age_days", max_observation_age_days)
            if age <= 0.0:
                raise ValueError(
                    f"max_observation_age_days must be > 0 or None, got {age}")
            self.max_observation_age_days = age

        if isinstance(lead_time_months, bool) or not isinstance(lead_time_months, int):
            raise ValueError(f"lead_time_months must be an int, got {lead_time_months!r}")
        if lead_time_months < 0:
            raise ValueError(
                f"lead_time_months must be >= 0, got {lead_time_months}; a negative "
                "lag would align the target's period to a supplier period that had "
                "not happened yet"
            )
        self.lead_time_months = lead_time_months

    def evaluate_supply_chain_lead_signal(
        self,
        *,
        target_asset: str,
        as_of_iso: str,
        supplier_observations: Sequence[SupplierObservation],
        consensus_revenue_growth_pct: float,
        consensus_dispersion_pct: float,
        customer_observations: Sequence[CustomerObservation] = (),
    ) -> SupplyChainEarningsSignal:
        """Score one target company's supply chain against consensus, as of an instant.

        Keyword-only by design: version 1.0.0 took four positional floats and
        differenced a revenue estimate against an *EPS* consensus. Refusing
        positional binding makes every v1 call site fail loudly rather than
        silently rebinding its arguments to different quantities.

        Args:
            target_asset: Ticker of the company whose results are being predicted.
            as_of_iso: Timezone-aware ISO-8601 evaluation instant. Required.
                Observations published after it are excluded, and this value -- not
                the wall clock -- stamps the output, so the call is deterministic
                and replayable.
            supplier_observations: Upstream supplier readings. May be empty, in
                which case the result is `INSUFFICIENT_DATA`.
            consensus_revenue_growth_pct: Sell-side consensus **revenue** growth for
                the target period, in the same convention and units as the inputs.
                Not an EPS consensus -- see the module docstring.
            consensus_dispersion_pct: Strictly positive standard deviation used to
                standardize the gap. No default: it varies by name and by quarter,
                and a hard-coded one produces a Z-score that only looks calibrated.
            customer_observations: Downstream customer inventory readings. May be
                empty; the drag term is then 0.0 and an audit note records that the
                signal is supplier-only.

        Returns:
            A `SupplyChainEarningsSignal`. When the surviving supplier set covers
            less than `min_supplier_coverage_pct` of the target's input spend,
            `directional_signal` is `INSUFFICIENT_DATA` and the three derived
            figures are `None`.

        Raises:
            ValueError: on a naive or malformed timestamp, a non-finite input, a
                weight outside [0, 100], a non-positive `consensus_dispersion_pct`,
                duplicate tickers within a batch, a wrong observation type, or a
                weight total above 100%.
        """
        # Materialize once: a generator would otherwise be consumed by the loop and
        # then report len() == 0 in the audit record, or fail obscurely.
        supplier_observations = tuple(supplier_observations)
        customer_observations = tuple(customer_observations)

        target = _require_ticker("target_asset", target_asset)
        as_of = _parse_iso_utc("as_of_iso", as_of_iso)
        consensus = _require_finite(
            "consensus_revenue_growth_pct", consensus_revenue_growth_pct)
        dispersion = _require_finite(
            "consensus_dispersion_pct", consensus_dispersion_pct)
        if dispersion <= 0.0:
            raise ValueError(
                f"consensus_dispersion_pct must be > 0, got {dispersion}; a zero or "
                "negative dispersion has no Z-score interpretation and must be "
                "treated as a broken configuration, not absorbed into NEUTRAL"
            )

        notes: List[str] = []
        cutoff = (
            as_of - timedelta(days=self.max_observation_age_days)
            if self.max_observation_age_days is not None
            else None
        )
        future_excluded = 0
        stale_excluded = 0
        low_read_through_excluded = 0

        # --- Upstream suppliers ------------------------------------------------
        seen_suppliers: Set[str] = set()
        supplier_weight_total = 0.0
        supplier_weighted_sum = 0.0
        suppliers_used = 0
        declared_supplier_weight_total = 0.0

        for position, obs in enumerate(supplier_observations):
            if not isinstance(obs, SupplierObservation):
                raise ValueError(
                    f"supplier_observations[{position}] must be a SupplierObservation, "
                    f"got {type(obs).__name__}"
                )
            label = f"supplier_observations[{position}]"
            ticker = _require_ticker(f"{label}.supplier_ticker", obs.supplier_ticker)
            if ticker in seen_suppliers:
                raise ValueError(
                    f"{label}: duplicate supplier_ticker {ticker!r}; the same link "
                    "appearing twice double-weights it in the concentration mean"
                )
            seen_suppliers.add(ticker)

            growth = _require_finite(
                f"{label}.revenue_growth_pct", obs.revenue_growth_pct)
            weight = _require_pct_share(
                f"{label}.supplier_share_of_target_cogs_pct",
                obs.supplier_share_of_target_cogs_pct)
            read_through = _require_pct_share(
                f"{label}.target_share_of_supplier_revenue_pct",
                obs.target_share_of_supplier_revenue_pct)
            available = _parse_iso_utc(
                f"{label}.available_from_iso", obs.available_from_iso)
            declared_supplier_weight_total += weight

            if available > as_of:
                future_excluded += 1
                continue
            if cutoff is not None and available < cutoff:
                stale_excluded += 1
                continue
            if read_through < self.min_read_through_share_pct:
                low_read_through_excluded += 1
                continue
            if weight <= 0.0:
                # A zero input-spend share contributes nothing and would only
                # dilute the coverage arithmetic. Not an error, just uninformative.
                continue

            supplier_weight_total += weight
            supplier_weighted_sum += weight * growth
            suppliers_used += 1

        if declared_supplier_weight_total > 100.0 + 1e-9:
            raise ValueError(
                "supplier_share_of_target_cogs_pct sums to "
                f"{declared_supplier_weight_total:.4f}% across the batch; a supplier "
                "set cannot exceed 100% of the target's input spend"
            )

        # --- Downstream customers ----------------------------------------------
        seen_customers: Set[str] = set()
        customer_weight_total = 0.0
        customer_weighted_sum = 0.0
        customers_used = 0
        declared_customer_weight_total = 0.0

        for position, obs in enumerate(customer_observations):
            if not isinstance(obs, CustomerObservation):
                raise ValueError(
                    f"customer_observations[{position}] must be a CustomerObservation, "
                    f"got {type(obs).__name__}"
                )
            label = f"customer_observations[{position}]"
            ticker = _require_ticker(f"{label}.customer_ticker", obs.customer_ticker)
            if ticker in seen_customers:
                raise ValueError(
                    f"{label}: duplicate customer_ticker {ticker!r}; the same link "
                    "appearing twice double-weights it in the concentration mean"
                )
            seen_customers.add(ticker)

            inventory = _require_finite(
                f"{label}.inventory_growth_pct", obs.inventory_growth_pct)
            weight = _require_pct_share(
                f"{label}.customer_share_of_target_revenue_pct",
                obs.customer_share_of_target_revenue_pct)
            available = _parse_iso_utc(
                f"{label}.available_from_iso", obs.available_from_iso)
            declared_customer_weight_total += weight

            if available > as_of:
                future_excluded += 1
                continue
            if cutoff is not None and available < cutoff:
                stale_excluded += 1
                continue
            if weight <= 0.0:
                continue

            customer_weight_total += weight
            customer_weighted_sum += weight * inventory
            customers_used += 1

        if declared_customer_weight_total > 100.0 + 1e-9:
            raise ValueError(
                "customer_share_of_target_revenue_pct sums to "
                f"{declared_customer_weight_total:.4f}% across the batch; a customer "
                "set cannot exceed 100% of the target's revenue"
            )

        # Normalizing by the observed weight total assumes the unobserved part of
        # the chain grew like the observed part. `supplier_coverage_pct` is how the
        # caller sees how large that assumption is.
        weighted_customer_inventory = (
            customer_weighted_sum / customer_weight_total
            if customer_weight_total > 0.0
            else 0.0
        )

        if customers_used == 0:
            notes.append(
                "No usable downstream customer observations: the inventory drag term "
                "is 0.0 and the signal is supplier-only. That is not a measured "
                "absence of channel inventory build."
            )
        if future_excluded:
            notes.append(
                f"{future_excluded} observation(s) excluded as published after as_of.")
        if stale_excluded:
            notes.append(
                f"{stale_excluded} observation(s) excluded as older than "
                f"{self.max_observation_age_days} days before as_of.")
        if low_read_through_excluded:
            notes.append(
                f"{low_read_through_excluded} supplier(s) excluded: the target is "
                f"below {self.min_read_through_share_pct}% of their revenue, so their "
                "reported growth is mostly other customers.")
        if self.max_observation_age_days is None:
            notes.append(
                "No staleness bound configured (max_observation_age_days is None); "
                "stale_observations_excluded_count reading 0 is not evidence that the "
                "observations are fresh.")

        supplier_coverage = round(supplier_weight_total, 6)
        customer_coverage = round(customer_weight_total, 6)

        if suppliers_used == 0 or supplier_weight_total < self.min_supplier_coverage_pct:
            notes.append(
                f"Supplier coverage {supplier_coverage}% of target input spend is "
                f"below the {self.min_supplier_coverage_pct}% floor; no Z-score was "
                "computed. INSUFFICIENT_DATA is not NEUTRAL."
            )
            logger.warning(
                "supply-chain signal suppressed for %s at %s: %d supplier(s) used, "
                "coverage %.4f%% < floor %.4f%%",
                target, _format_utc(as_of), suppliers_used,
                supplier_weight_total, self.min_supplier_coverage_pct,
            )
            return SupplyChainEarningsSignal(
                as_of_iso=_format_utc(as_of),
                asset_id=target,
                directional_signal=SIGNAL_INSUFFICIENT,
                is_signal_measurable=False,
                surprise_z_score=None,
                implied_revenue_growth_pct=None,
                consensus_revenue_gap_pct=None,
                consensus_revenue_growth_pct=consensus,
                consensus_dispersion_pct=dispersion,
                weighted_supplier_revenue_growth_pct=None,
                weighted_customer_inventory_growth_pct=round(
                    weighted_customer_inventory, 6),
                supplier_coverage_pct=supplier_coverage,
                customer_coverage_pct=customer_coverage,
                suppliers_submitted=len(supplier_observations),
                suppliers_used=suppliers_used,
                customers_submitted=len(customer_observations),
                customers_used=customers_used,
                future_observations_excluded_count=future_excluded,
                stale_observations_excluded_count=stale_excluded,
                low_read_through_excluded_count=low_read_through_excluded,
                declared_lead_time_months=self.lead_time_months,
                audit_notes=notes,
            )

        weighted_supplier_growth = supplier_weighted_sum / supplier_weight_total
        implied_growth = (
            self.supplier_blend_weight * weighted_supplier_growth
            - self.inventory_blend_weight * weighted_customer_inventory
        )
        gap = implied_growth - consensus
        z_score = gap / dispersion

        # Band on the unrounded Z. Rounding first promotes 0.99996 to 1.0 and hands
        # back a directional signal the data does not support.
        if z_score >= self.surprise_z_threshold:
            directional = SIGNAL_BUY
        elif z_score <= -self.surprise_z_threshold:
            directional = SIGNAL_SELL
        else:
            directional = SIGNAL_NEUTRAL

        return SupplyChainEarningsSignal(
            as_of_iso=_format_utc(as_of),
            asset_id=target,
            directional_signal=directional,
            is_signal_measurable=True,
            surprise_z_score=round(z_score, 6),
            implied_revenue_growth_pct=round(implied_growth, 6),
            consensus_revenue_gap_pct=round(gap, 6),
            consensus_revenue_growth_pct=consensus,
            consensus_dispersion_pct=dispersion,
            weighted_supplier_revenue_growth_pct=round(weighted_supplier_growth, 6),
            weighted_customer_inventory_growth_pct=round(weighted_customer_inventory, 6),
            supplier_coverage_pct=supplier_coverage,
            customer_coverage_pct=customer_coverage,
            suppliers_submitted=len(supplier_observations),
            suppliers_used=suppliers_used,
            customers_submitted=len(customer_observations),
            customers_used=customers_used,
            future_observations_excluded_count=future_excluded,
            stale_observations_excluded_count=stale_excluded,
            low_read_through_excluded_count=low_read_through_excluded,
            declared_lead_time_months=self.lead_time_months,
            audit_notes=notes,
        )
