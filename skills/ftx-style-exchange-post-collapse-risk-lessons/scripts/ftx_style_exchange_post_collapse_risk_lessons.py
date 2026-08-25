"""
ftx-style-exchange-post-collapse-risk-lessons: a venue counterparty-risk gate
built from the specific failure modes documented in the November 2022 collapse
of FTX.

What this module does
---------------------
It scores one centralised venue on six separable dimensions -- Proof of
Reserves (PoR) coverage, the age of the PoR snapshot, native-token collateral
concentration, single-venue NAV concentration, off-exchange settlement (OES)
adoption, and whether an independent attestation exists -- and turns the result
into two operational outputs: a de-risking decision and the fraction of the
capital sitting at that venue that should be pulled back to custody you
control.

The dimensions are the ones the FTX record actually implicates:

* **Native-token collateral.** CoinDesk's 2022-11-02 report on Alameda
  Research's balance sheet showed roughly USD 5.8bn of USD 14.6bn in assets
  tied to FTX's own FTT token. The run that followed is why native-token
  collateral is capped here at a low single-digit percentage rather than
  treated as ordinary collateral.
* **Commingling and the absence of segregation.** The SEC's 2022-12-13
  complaint against Samuel Bankman-Fried alleges FTX customer assets were
  routinely accepted and held by Alameda and commingled with Alameda's own
  funds. Off-exchange settlement exists to make that structurally impossible
  for your capital.
* **The absence of trustworthy books.** John J. Ray III's first-day
  declaration in the FTX Chapter 11 case (D. Del., 2022-11-17) states that in
  40 years he had never seen "such a complete failure of corporate controls and
  such a complete absence of trustworthy financial information". An attestation
  is evidence; its absence is not neutral.

What this module is NOT
-----------------------
It is **not** a solvency measurement and it verifies nothing. Every input is a
figure you supply, and most of them originate with the venue. A venue that lies
about its PoR ratio, or an operator who records
``has_independent_attestation=True`` for an attestation that has since been
withdrawn, gets a clean report out of this engine. Mazars published a PoR
statement for Binance on 2022-12-07 and within days paused proof-of-reserves
work for all crypto clients and removed the reports from its site: the state of
an attestation is not a durable property of a venue. Re-run this audit on a
cadence and diff the inputs, and use ``exchange-proof-of-reserves-verification``
to check a PoR claim cryptographically before feeding a ratio in here.

Thresholds are risk policy, not regulation
------------------------------------------
The defaults (100% PoR coverage, 5% native-token collateral, 20% single-venue
NAV) are *internal risk-policy limits*. No regulator reviewed for this skill
sets them, and no regulator requires a centralised venue to publish a PoR at
all. MiCA (Regulation (EU) 2023/1114) does impose segregation duties -- Article
70 on safekeeping of clients' crypto-assets and funds, and Article 75(7) on
legal and operational segregation of custodied crypto-assets from the CASP's
estate -- but those bind CASPs authorised in the EU and say nothing about an
offshore venue. Whether a venue falls in scope of any such regime is a
jurisdictional fact to establish separately; this engine does not model it. See
``references/standards.md``.

Units
-----
``proof_of_reserves_ratio`` is a **ratio** (1.05 means 105% coverage).
``native_token_collateral_pct`` and ``nav_exposure_pct`` are **fractions**
despite the ``_pct`` suffix (0.05 means 5%). Passing 5.0 where 0.05 was meant
raises ``ExchangeCounterpartyRiskError`` rather than silently producing a
verdict.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

STATUS_ACCEPTABLE = "VENUE_RISK_ACCEPTABLE"
STATUS_DERISK = "EXCHANGE_DERISK_TRIGGERED"

#: Risk-score weights. Every weight is a policy choice, not a measured
#: quantity, so they are named and asserted to total ``MAX_RISK_SCORE``. Weights
#: that sum past the maximum make the top of the scale degenerate: a venue
#: failing PoR alone and a venue failing every dimension would both report 100.
WEIGHT_POR_SHORTFALL = 35.0
WEIGHT_NATIVE_TOKEN_CONCENTRATION = 25.0
WEIGHT_NAV_CONCENTRATION = 15.0
WEIGHT_NO_OFF_EXCHANGE_SETTLEMENT = 12.0
WEIGHT_NO_INDEPENDENT_ATTESTATION = 8.0
WEIGHT_STALE_OR_UNDATED_POR = 5.0

MAX_RISK_SCORE = 100.0

_ALL_WEIGHTS = (
    WEIGHT_POR_SHORTFALL,
    WEIGHT_NATIVE_TOKEN_CONCENTRATION,
    WEIGHT_NAV_CONCENTRATION,
    WEIGHT_NO_OFF_EXCHANGE_SETTLEMENT,
    WEIGHT_NO_INDEPENDENT_ATTESTATION,
    WEIGHT_STALE_OR_UNDATED_POR,
)
if not math.isclose(sum(_ALL_WEIGHTS), MAX_RISK_SCORE):
    raise RuntimeError(
        "risk weights must total MAX_RISK_SCORE so the index cannot saturate"
    )


class ExchangeCounterpartyRiskError(ValueError):
    """Raised when an input or engine configuration is unusable.

    A counterparty audit must fail loudly rather than degrade. NaN defeats
    every comparison it appears in -- ``float('nan') <= 0.05`` and
    ``float('nan') > 0.10`` are both False -- so a NaN collateral figure would
    otherwise be scored as a concentration breach *and* skip the hard
    de-risking trigger. A verdict computed from an unusable input is worse than
    no verdict: it carries the authority of a check that never happened.
    """


def _require_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExchangeCounterpartyRiskError(
            f"{name} must be a real number, got {type(value).__name__}"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ExchangeCounterpartyRiskError(f"{name} must be finite, got {value!r}")
    return numeric


def _require_fraction(value: object, name: str) -> float:
    """Validate a 0-1 fraction, rejecting the percent/fraction confusion.

    ``native_token_collateral_pct=5`` (meaning 5%) is a plausible mistake for a
    caller or an agent reading the ``_pct`` suffix. Left unchecked it reads as
    500% collateral: it happens to trip the concentration breach, but the
    reported figure and everything derived from it are wrong by two orders of
    magnitude.
    """
    numeric = _require_finite(value, name)
    if not 0.0 <= numeric <= 1.0:
        raise ExchangeCounterpartyRiskError(
            f"{name} must be a fraction in [0.0, 1.0] (0.05 == 5%), got {numeric!r}"
        )
    return numeric


def _require_bool(value: object, name: str) -> bool:
    """Reject truthy non-booleans.

    ``has_independent_attestation="no"`` is truthy. Scoring it as an attestation
    that exists is precisely the direction of error this engine must not make.
    """
    if not isinstance(value, bool):
        raise ExchangeCounterpartyRiskError(
            f"{name} must be a bool, got {type(value).__name__}"
        )
    return value


def _require_optional_days(value: object, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExchangeCounterpartyRiskError(f"{name} must be an int or None")
    if value < 0:
        raise ExchangeCounterpartyRiskError(f"{name} must be >= 0, got {value!r}")
    return value


@dataclass
class ExchangeSolvencyMetrics:
    """Operator-supplied counterparty inputs for one centralised venue.

    Every field is an assertion *you* are making about the venue, most of them
    sourced from the venue itself. Nothing here is verified by this module.

    Attributes:
        venue_id: Stable internal identifier for the venue.
        exchange_name: Human-readable venue name, used in audit notes.
        proof_of_reserves_ratio: Verified on-chain reserves divided by declared
            client liabilities, as a **ratio** (1.05 == 105%). Verify the
            underlying PoR with ``exchange-proof-of-reserves-verification``
            before supplying it; a number copied off a dashboard is a claim.
        native_token_collateral_pct: Fraction of collateral posted at this venue
            that is held in the venue's own token (0.05 == 5%). This is the FTT
            exposure.
        uses_off_exchange_settlement: True only if this desk's trading collateral
            is actually held off-venue under a tri-party arrangement (e.g.
            Copper ClearLoop, Fireblocks Off Exchange) -- not merely that the
            venue supports one.
        nav_exposure_pct: Fraction of total fund NAV deployed at this venue
            (0.20 == 20%).
        has_independent_attestation: True only if a current third-party
            attestation covering reserves exists. An attestation that has been
            withdrawn is False.
        por_snapshot_age_days: Age in days of the PoR figure above. ``None``
            means unknown and is scored as a gap, not as a pass: a PoR ratio
            with no as-of date is not evidence about today.
    """

    venue_id: str
    exchange_name: str
    proof_of_reserves_ratio: float
    native_token_collateral_pct: float
    uses_off_exchange_settlement: bool
    nav_exposure_pct: float
    has_independent_attestation: bool
    por_snapshot_age_days: Optional[int] = None


@dataclass
class ExchangePostCollapseRiskReport:
    """Outcome of one venue audit.

    ``recommended_capital_withdrawal_pct`` is a percentage **of the capital
    currently held at this venue**, not of fund NAV. It is 0.0 when
    ``nav_exposure_pct`` is 0.0 because there is nothing to withdraw -- never
    read it as a safety signal. Read ``status`` and ``is_derisking_triggered``
    to decide whether the venue may be used.
    """

    venue_id: str
    exchange_name: str
    risk_score_0_to_100: float
    is_por_valid: bool
    is_por_snapshot_current: bool
    is_native_token_safe: bool
    is_nav_exposure_safe: bool
    is_derisking_triggered: bool
    recommended_capital_withdrawal_pct: float
    status: str
    findings: List[str] = field(default_factory=list)
    audit_notes: str = ""


class ExchangePostCollapseRiskEngine:
    """Scores a centralised venue against post-FTX counterparty risk policy.

    All thresholds are constructor arguments because all of them are policy.
    They are not regulator-set limits and this engine does not claim they are.

    Args:
        min_por_coverage_ratio: Minimum reserves/liabilities ratio for the PoR
            dimension to pass. 1.00 == full reserves.
        max_native_token_ratio: Soft cap on native-token collateral, as a
            fraction. Breaching it adds ``WEIGHT_NATIVE_TOKEN_CONCENTRATION``.
        max_single_venue_nav_pct: Soft cap on NAV deployed at one venue, as a
            fraction.
        hard_native_token_trigger: Native-token fraction above which de-risking
            fires regardless of the total score. Must be >=
            ``max_native_token_ratio``.
        derisk_score_threshold: Total score at or above which de-risking fires.
        derisk_residual_nav_pct: Target residual NAV at a venue that is being
            trimmed but not disqualified outright, as a fraction. Must not
            exceed ``max_single_venue_nav_pct``.
        max_por_snapshot_age_days: Age beyond which a PoR figure is scored as
            stale.

    Raises:
        ExchangeCounterpartyRiskError: If any threshold is unusable or the
            thresholds are mutually inconsistent.
    """

    def __init__(
        self,
        min_por_coverage_ratio: float = 1.00,
        max_native_token_ratio: float = 0.05,
        max_single_venue_nav_pct: float = 0.20,
        hard_native_token_trigger: float = 0.10,
        derisk_score_threshold: float = 40.0,
        derisk_residual_nav_pct: float = 0.05,
        max_por_snapshot_age_days: int = 90,
    ) -> None:
        self.min_por_coverage_ratio = _require_finite(
            min_por_coverage_ratio, "min_por_coverage_ratio"
        )
        if self.min_por_coverage_ratio <= 0.0:
            raise ExchangeCounterpartyRiskError("min_por_coverage_ratio must be > 0")

        self.max_native_token_ratio = _require_fraction(
            max_native_token_ratio, "max_native_token_ratio"
        )
        self.max_single_venue_nav_pct = _require_fraction(
            max_single_venue_nav_pct, "max_single_venue_nav_pct"
        )
        if self.max_single_venue_nav_pct <= 0.0:
            raise ExchangeCounterpartyRiskError("max_single_venue_nav_pct must be > 0")

        self.hard_native_token_trigger = _require_fraction(
            hard_native_token_trigger, "hard_native_token_trigger"
        )
        if self.hard_native_token_trigger < self.max_native_token_ratio:
            raise ExchangeCounterpartyRiskError(
                "hard_native_token_trigger must be >= max_native_token_ratio, "
                "otherwise the hard trigger fires before the soft cap and the "
                "soft cap can never be observed"
            )

        self.derisk_score_threshold = _require_finite(
            derisk_score_threshold, "derisk_score_threshold"
        )
        if not 0.0 < self.derisk_score_threshold <= MAX_RISK_SCORE:
            raise ExchangeCounterpartyRiskError(
                f"derisk_score_threshold must be in (0, {MAX_RISK_SCORE}]"
            )

        self.derisk_residual_nav_pct = _require_fraction(
            derisk_residual_nav_pct, "derisk_residual_nav_pct"
        )
        if self.derisk_residual_nav_pct > self.max_single_venue_nav_pct:
            raise ExchangeCounterpartyRiskError(
                "derisk_residual_nav_pct must not exceed max_single_venue_nav_pct"
            )

        days = _require_optional_days(
            max_por_snapshot_age_days, "max_por_snapshot_age_days"
        )
        if days is None:
            raise ExchangeCounterpartyRiskError(
                "max_por_snapshot_age_days must be an int"
            )
        self.max_por_snapshot_age_days = days

    def _validate(self, metrics: ExchangeSolvencyMetrics) -> None:
        if not isinstance(metrics, ExchangeSolvencyMetrics):
            raise ExchangeCounterpartyRiskError(
                "metrics must be an ExchangeSolvencyMetrics instance"
            )
        for name in ("venue_id", "exchange_name"):
            value = getattr(metrics, name)
            if not isinstance(value, str) or not value.strip():
                raise ExchangeCounterpartyRiskError(
                    f"{name} must be a non-empty string"
                )

        por = _require_finite(
            metrics.proof_of_reserves_ratio, "proof_of_reserves_ratio"
        )
        if por < 0.0:
            raise ExchangeCounterpartyRiskError("proof_of_reserves_ratio must be >= 0")
        if por > 10.0:
            # A coverage ratio above 10x is not a real book; it is almost always
            # 105 supplied where 1.05 was meant, which would report a
            # catastrophically under-reserved venue as fully reserved.
            raise ExchangeCounterpartyRiskError(
                f"proof_of_reserves_ratio={por!r} is a ratio, not a percentage "
                "(1.05 == 105%); values above 10 are rejected as a unit error"
            )

        _require_fraction(
            metrics.native_token_collateral_pct, "native_token_collateral_pct"
        )
        _require_fraction(metrics.nav_exposure_pct, "nav_exposure_pct")
        _require_bool(
            metrics.uses_off_exchange_settlement, "uses_off_exchange_settlement"
        )
        _require_bool(
            metrics.has_independent_attestation, "has_independent_attestation"
        )
        _require_optional_days(metrics.por_snapshot_age_days, "por_snapshot_age_days")

    def _recommended_withdrawal_pct(
        self, nav_exposure_pct: float, venue_disqualified: bool
    ) -> float:
        """Fraction of the capital at this venue to withdraw, as a percentage.

        Three cases, in this order:

        1. No capital deployed -> 0.0. There is nothing to withdraw. Dividing
           by ``nav_exposure_pct`` here raises ``ZeroDivisionError`` on a venue
           with zero exposure that is nonetheless disqualified on native-token
           concentration.
        2. Venue disqualified (PoR shortfall or hard-trigger concentration)
           -> 100.0. Target residual is zero; the venue is not usable at any
           size.
        3. Otherwise trim down to ``derisk_residual_nav_pct``. Clamped at 0.0
           so a venue already below the residual target never returns a
           negative "withdrawal" -- an unclamped formula returns -150.0 for a
           venue at 2% NAV against a 5% target.
        """
        if nav_exposure_pct <= 0.0:
            return 0.0
        if venue_disqualified:
            return 100.0
        excess = nav_exposure_pct - self.derisk_residual_nav_pct
        if excess <= 0.0:
            return 0.0
        return round(min(100.0, (excess / nav_exposure_pct) * 100.0), 1)

    def audit_exchange_counterparty_risk(
        self, metrics: ExchangeSolvencyMetrics
    ) -> ExchangePostCollapseRiskReport:
        """Audit one venue and return a de-risking decision.

        Args:
            metrics: The venue's counterparty inputs.

        Returns:
            An ``ExchangePostCollapseRiskReport``. Each dimension is reported
            separately because the dimensions fail separately.

        Raises:
            ExchangeCounterpartyRiskError: If any input is unusable. The engine
                never returns a verdict derived from a non-finite, negative or
                out-of-range figure.
        """
        self._validate(metrics)

        risk_score = 0.0
        findings: List[str] = []

        # 1. Proof of Reserves coverage. A shortfall disqualifies the venue
        #    outright: reserves below liabilities means the claim on your
        #    balance is already impaired.
        is_por_valid = metrics.proof_of_reserves_ratio >= self.min_por_coverage_ratio
        if not is_por_valid:
            risk_score += WEIGHT_POR_SHORTFALL
            findings.append(
                "POR_SHORTFALL: coverage "
                f"{metrics.proof_of_reserves_ratio * 100:.1f}% < required "
                f"{self.min_por_coverage_ratio * 100:.1f}%"
            )

        # 2. Age of the PoR figure. PoR is a point-in-time snapshot; the PCAOB
        #    Investor Advisory of 2023-03-08 makes the point that these reports
        #    verify an asset type "at a particular moment in time". An undated
        #    ratio is scored as a gap, never as a pass.
        if metrics.por_snapshot_age_days is None:
            is_por_snapshot_current = False
            risk_score += WEIGHT_STALE_OR_UNDATED_POR
            findings.append(
                "POR_SNAPSHOT_UNDATED: no as-of date supplied for the coverage ratio"
            )
        elif metrics.por_snapshot_age_days > self.max_por_snapshot_age_days:
            is_por_snapshot_current = False
            risk_score += WEIGHT_STALE_OR_UNDATED_POR
            findings.append(
                f"POR_SNAPSHOT_STALE: {metrics.por_snapshot_age_days}d old > max "
                f"{self.max_por_snapshot_age_days}d"
            )
        else:
            is_por_snapshot_current = True

        # 3. Native-token collateral. The FTT lesson: collateral whose value is
        #    a function of the venue's own solvency is not collateral against
        #    that venue failing.
        is_native_token_safe = (
            metrics.native_token_collateral_pct <= self.max_native_token_ratio
        )
        if not is_native_token_safe:
            risk_score += WEIGHT_NATIVE_TOKEN_CONCENTRATION
            findings.append(
                "NATIVE_TOKEN_CONCENTRATION: "
                f"{metrics.native_token_collateral_pct * 100:.1f}% > max "
                f"{self.max_native_token_ratio * 100:.1f}%"
            )

        # 4. Single-venue NAV concentration.
        is_nav_safe = metrics.nav_exposure_pct <= self.max_single_venue_nav_pct
        if not is_nav_safe:
            risk_score += WEIGHT_NAV_CONCENTRATION
            findings.append(
                f"NAV_CONCENTRATION: {metrics.nav_exposure_pct * 100:.1f}% of NAV > max "
                f"{self.max_single_venue_nav_pct * 100:.1f}%"
            )

        # 5. Off-exchange settlement. Reduces, but does not remove, exposure:
        #    unrealised PnL and the settlement window still sit with the venue.
        if not metrics.uses_off_exchange_settlement:
            risk_score += WEIGHT_NO_OFF_EXCHANGE_SETTLEMENT
            findings.append(
                "NO_OFF_EXCHANGE_SETTLEMENT: full collateral balance sits in "
                "venue custody"
            )

        # 6. Independent attestation.
        if not metrics.has_independent_attestation:
            risk_score += WEIGHT_NO_INDEPENDENT_ATTESTATION
            findings.append(
                "NO_INDEPENDENT_ATTESTATION: no current third-party reserve "
                "attestation"
            )

        risk_score = round(risk_score, 1)

        venue_disqualified = (not is_por_valid) or (
            metrics.native_token_collateral_pct > self.hard_native_token_trigger
        )
        is_derisk_triggered = venue_disqualified or (
            risk_score >= self.derisk_score_threshold
        )
        withdrawal_pct = (
            self._recommended_withdrawal_pct(
                metrics.nav_exposure_pct, venue_disqualified
            )
            if is_derisk_triggered
            else 0.0
        )

        if is_derisk_triggered:
            status = STATUS_DERISK
            notes = (
                f"POST-FTX COUNTERPARTY RISK ALERT [{metrics.exchange_name}]: "
                f"risk score {risk_score:.1f}/{MAX_RISK_SCORE:.0f}. "
                f"Findings: {'; '.join(findings) if findings else 'none'}. "
                f"DE-RISKING TRIGGERED. Withdraw {withdrawal_pct:.1f}% of the "
                "capital held at this venue to custody you control."
            )
            logger.critical(notes)
        else:
            status = STATUS_ACCEPTABLE
            notes = (
                f"VENUE RISK ACCEPTABLE [{metrics.exchange_name}]: "
                f"risk score {risk_score:.1f}/{MAX_RISK_SCORE:.0f}. "
                f"PoR coverage {metrics.proof_of_reserves_ratio * 100:.1f}%, "
                "native-token collateral "
                f"{metrics.native_token_collateral_pct * 100:.1f}%, "
                f"NAV exposure {metrics.nav_exposure_pct * 100:.1f}%. "
                "Cleared at current limits; re-audit on the next PoR publication."
            )
            logger.info(notes)

        return ExchangePostCollapseRiskReport(
            venue_id=metrics.venue_id,
            exchange_name=metrics.exchange_name,
            risk_score_0_to_100=risk_score,
            is_por_valid=is_por_valid,
            is_por_snapshot_current=is_por_snapshot_current,
            is_native_token_safe=is_native_token_safe,
            is_nav_exposure_safe=is_nav_safe,
            is_derisking_triggered=is_derisk_triggered,
            recommended_capital_withdrawal_pct=withdrawal_pct,
            status=status,
            findings=findings,
            audit_notes=notes,
        )
