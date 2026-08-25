"""
exchange-fee-tier-and-rebate-structure-analysis: venue fee-tier classification and
net execution-cost analysis for maker-taker and inverted (taker-maker) schedules.

Sign convention (applies to every rate and every USD amount in this module):

    rate < 0  -> rebate: the venue CREDITS the member
    rate > 0  -> fee:    the venue CHARGES the member

A signed cost is therefore always ``shares * rate``. A positive net cost is money
leaving the desk; a negative net cost is net rebate capture.

Tier qualification basis
------------------------
This is the parameter that decides whether the answer is right, and there is no safe
default, so it is required.

``PRIOR_PERIOD``
    The active tier is fixed by volume achieved in a *completed prior period*.
    Volume traded now cannot change the rate applied now; it can only change the
    next period's tier. This is mandatory for US national securities exchanges.
    Reg NMS Rule 610(d) provides that an exchange "shall not impose, nor permit to
    be imposed, any fee or fees, or provide, or permit to be provided, any rebate
    or other remuneration, for the execution of an order in an NMS stock that
    cannot be determined at the time of execution" (adopting release 34-101070,
    Sept. 18 2024; compliance date Feb. 2 2026 per Rel. 34-104541, Jan. 5 2026).
    Exchanges implemented it by deriving tier volumes from the prior month -- e.g.
    the Cboe US equities fee schedules state that "unless otherwise indicated, all
    volume figures will be derived from quoting or trading activity in the prior
    month."

``ROLLING_CURRENT``
    The active tier is set by a rolling window that includes the volume being
    priced, so crossing a threshold reprices the whole window retroactively. This
    is the model used by most crypto venues (rolling 30-day volume) and by some
    non-US venues. It is NOT lawful for US NMS stocks on or after Feb. 2 2026.

Limitations (documented, deliberate)
------------------------------------
- **Thresholds are absolute share counts.** Many real US equity tiers qualify on a
  percentage of Total Consolidated Volume or of consolidated ADV (Nasdaq's price
  list, for example, carries criteria such as "Add 0.65% or greater of TCV or 70M
  shares ADV"). Converting a percentage criterion into a share threshold requires a
  consolidated-volume forecast that this module does not make; supply the converted
  threshold yourself and treat the result as conditional on that forecast.
- **One qualifying volume, one tier.** Real schedules frequently require *several*
  simultaneous criteria (add % AND remove %, or a cross-asset condition). A single
  scalar threshold cannot express those; model only schedules that reduce to one.
- **No fill-probability, queue-position, or adverse-selection modelling.** Net fee
  cost is one term of execution cost, and on inverted venues it is usually the
  smaller one. Do not route on this number alone.
- **No per-share price awareness.** Rates are per share, so sub-$1.00 securities
  (capped as a percentage of quotation price on US venues) are out of scope.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Reg NMS Rule 610(c) access fee cap currently in force for NMS stocks priced at or
#: above $1.00 per share. The 2024 amendments reduce this to $0.0010 (and the
#: sub-$1.00 cap from 0.3% to 0.1% of the quotation price), but the SEC extended
#: temporary exemptive relief from the amended cap to the first business day of
#: November 2027 (order of June 11 2026), so $0.0030 remains the operative cap.
REG_NMS_610C_ACCESS_FEE_CAP_USD = 0.0030

#: The amended Rule 610(c) cap, effective for US NMS stocks priced at or above
#: $1.00 from the first business day of November 2027 unless further extended.
REG_NMS_610C_AMENDED_CAP_USD = 0.0010

#: Rounding boundary for reported amounts. Arithmetic is carried in full precision
#: and rounded only here, so rounding never compounds into the net.
_USD_DP = 2
_PER_SHARE_DP = 6


class PricingModel(str, Enum):
    """Which side of a fill the venue pays."""

    #: Venue credits makers and charges takers (Nasdaq, Cboe BZX/EDGX/EDGA, ...).
    MAKER_TAKER = "MAKER_TAKER"
    #: Inverted venue: charges makers and credits takers (Cboe BYX, Nasdaq BX, ...).
    TAKER_MAKER = "TAKER_MAKER"


class TierQualificationBasis(str, Enum):
    """Which period's volume fixes the tier applied to a fill. See module docstring."""

    #: Tier fixed by a completed prior period. Required for US NMS stocks (Rule 610(d)).
    PRIOR_PERIOD = "PRIOR_PERIOD"
    #: Tier fixed by a rolling window that includes the volume being priced.
    ROLLING_CURRENT = "ROLLING_CURRENT"


class FeeScheduleError(ValueError):
    """Raised when a fee schedule is internally inconsistent or unusable."""


def _normalize(value: object) -> str:
    """
    Normalizes an enum member or a string to its bare uppercase value.

    ``str()`` on a ``(str, Enum)`` member returns ``'ClassName.MEMBER'`` on Python
    3.11, so the engine's own enum members must be unwrapped before comparison
    rather than stringified.
    """
    if isinstance(value, Enum):
        return str(value.value).strip().upper()
    return str(value).strip().upper()


def _fmt_usd(amount: float) -> str:
    """Formats a signed amount as -$1,234.56 rather than $-1,234.56."""
    return f"-${abs(amount):,.2f}" if amount < 0 else f"${amount:,.2f}"


def _fmt_per_share(rate: float) -> str:
    """
    Formats a signed per-share rate at 5 decimal places.

    Per-share fees live in the fourth and fifth decimal, so the 2dp money format
    renders every realistic rate as -$0.00 and hides the number entirely.
    """
    return f"-${abs(rate):.5f}" if rate < 0 else f"${rate:.5f}"


@dataclass
class FeeTierDefinition:
    """
    One row of a venue fee schedule.

    ``maker_rate_per_share`` / ``taker_rate_per_share`` follow the module sign
    convention: negative is a rebate credited to the member, positive is a fee
    charged to the member.

    ``min_monthly_volume_shares`` is the qualifying-volume threshold, interpreted
    against whichever period the engine's ``TierQualificationBasis`` selects.
    """

    tier_name: str
    min_monthly_volume_shares: int
    maker_rate_per_share: float
    taker_rate_per_share: float

    def __post_init__(self) -> None:
        if not str(self.tier_name).strip():
            raise FeeScheduleError("tier_name must be a non-empty string.")
        if not isinstance(self.min_monthly_volume_shares, int) or isinstance(
            self.min_monthly_volume_shares, bool
        ):
            raise FeeScheduleError(
                f"{self.tier_name}: min_monthly_volume_shares must be an int, "
                f"got {type(self.min_monthly_volume_shares).__name__}."
            )
        if self.min_monthly_volume_shares < 0:
            raise FeeScheduleError(
                f"{self.tier_name}: min_monthly_volume_shares must be >= 0, "
                f"got {self.min_monthly_volume_shares}."
            )
        for label, rate in (
            ("maker_rate_per_share", self.maker_rate_per_share),
            ("taker_rate_per_share", self.taker_rate_per_share),
        ):
            if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                raise FeeScheduleError(
                    f"{self.tier_name}: {label} must be a number, "
                    f"got {type(rate).__name__}."
                )
            # NaN fails every comparison, so a NaN rate would silently produce a NaN
            # net cost that reads as a valid float downstream.
            if rate != rate or rate in (float("inf"), float("-inf")):
                raise FeeScheduleError(
                    f"{self.tier_name}: {label} must be finite, got {rate!r}."
                )


@dataclass
class VenueVolumeSummary:
    """
    Volume submitted for pricing, plus the qualifying volume that fixes the tier.

    ``rolling_30d_maker_volume_shares`` / ``rolling_30d_taker_volume_shares`` are the
    shares actually being *priced*.

    ``qualifying_volume_shares`` is the volume compared against tier thresholds.
    Under ``PRIOR_PERIOD`` it is a different, completed period's volume and must be
    supplied explicitly -- the priced volume cannot stand in for it, because under
    Rule 610(d) the volume being priced is exactly the volume that may *not*
    influence its own rate. Under ``ROLLING_CURRENT`` it defaults to the priced
    volume, which is what a rolling-window venue actually qualifies on.
    """

    venue_id: str
    pricing_model: str
    rolling_30d_maker_volume_shares: int
    rolling_30d_taker_volume_shares: int
    qualifying_volume_shares: Optional[int] = None

    def __post_init__(self) -> None:
        for label, vol in (
            ("rolling_30d_maker_volume_shares", self.rolling_30d_maker_volume_shares),
            ("rolling_30d_taker_volume_shares", self.rolling_30d_taker_volume_shares),
        ):
            if isinstance(vol, bool) or not isinstance(vol, int):
                raise FeeScheduleError(
                    f"{label} must be an int share count, got {type(vol).__name__}."
                )
            if vol < 0:
                raise FeeScheduleError(f"{label} must be >= 0, got {vol}.")
        if self.qualifying_volume_shares is not None:
            if isinstance(self.qualifying_volume_shares, bool) or not isinstance(
                self.qualifying_volume_shares, int
            ):
                raise FeeScheduleError(
                    "qualifying_volume_shares must be an int share count, got "
                    f"{type(self.qualifying_volume_shares).__name__}."
                )
            if self.qualifying_volume_shares < 0:
                raise FeeScheduleError(
                    "qualifying_volume_shares must be >= 0, got "
                    f"{self.qualifying_volume_shares}."
                )

    @property
    def priced_volume_shares(self) -> int:
        return (
            self.rolling_30d_maker_volume_shares + self.rolling_30d_taker_volume_shares
        )


@dataclass
class FeeTierAnalysisReport:
    """
    Structured result. All USD amounts are signed per the module convention: positive
    is a charge, negative is a credit.
    """

    venue_id: str
    active_tier_name: str
    pricing_model: str
    total_volume_shares: int
    gross_taker_fees_usd: float
    gross_maker_rebates_usd: float
    net_transaction_cost_usd: float
    effective_cost_per_share: float
    next_tier_name: Optional[str]
    volume_needed_for_next_tier_shares: int
    estimated_monthly_savings_at_next_tier_usd: float
    audit_notes: str
    # --- tier qualification, made explicit ---
    tier_qualification_basis: str = TierQualificationBasis.ROLLING_CURRENT.value
    qualifying_volume_shares: int = 0
    # --- signed per-side economics, correct on inverted venues ---
    maker_side_cost_usd: float = 0.0
    taker_side_cost_usd: float = 0.0
    # --- tier jump economics ---
    #: Which period the tier-jump benefit lands in. Under PRIOR_PERIOD the extra
    #: volume cannot change the current period's rate, so the benefit is NEXT_PERIOD.
    tier_jump_benefit_period: Optional[str] = None
    #: Cost of trading the incremental shares needed to reach the next tier, priced
    #: at whichever tier actually bills them.
    incremental_volume_cost_usd: float = 0.0
    #: Gross savings minus the cost of the incremental volume. This is the number to
    #: act on; the gross figure alone always overstates the case for chasing a tier.
    net_tier_jump_benefit_usd: float = 0.0
    #: Non-fatal schedule or model inconsistencies detected during analysis.
    warnings: List[str] = field(default_factory=list)


class ExchangeFeeTierAnalyzerEngine:
    """
    Classifies a venue fee tier, computes signed net execution cost, and evaluates
    whether reaching the next tier is worth the volume required to reach it.

    ``qualification_basis`` is required: see the module docstring. Passing
    ``ROLLING_CURRENT`` for a US NMS-stock venue models a fee structure that Rule
    610(d) has prohibited since Feb. 2 2026.
    """

    def __init__(
        self,
        venue_id: str,
        pricing_model: str,
        tiers: Sequence[FeeTierDefinition],
        qualification_basis: str,
    ) -> None:
        if not str(venue_id).strip():
            raise FeeScheduleError("venue_id must be a non-empty string.")
        self.venue_id = venue_id

        try:
            self.pricing_model = PricingModel(_normalize(pricing_model))
        except ValueError:
            raise FeeScheduleError(
                f"pricing_model must be one of {[m.value for m in PricingModel]}, "
                f"got {pricing_model!r}."
            ) from None

        try:
            self.qualification_basis = TierQualificationBasis(
                _normalize(qualification_basis)
            )
        except ValueError:
            raise FeeScheduleError(
                f"qualification_basis must be one of "
                f"{[b.value for b in TierQualificationBasis]}, got "
                f"{qualification_basis!r}. There is no default: the basis determines "
                f"whether the tier assignment is correct at all."
            ) from None

        tier_list = list(tiers)
        if not tier_list:
            raise FeeScheduleError(
                f"{venue_id}: fee schedule must contain at least one tier."
            )
        self.tiers: List[FeeTierDefinition] = sorted(
            tier_list, key=lambda t: t.min_monthly_volume_shares
        )

        thresholds = [t.min_monthly_volume_shares for t in self.tiers]
        if len(set(thresholds)) != len(thresholds):
            raise FeeScheduleError(
                f"{venue_id}: duplicate tier thresholds {thresholds} -- tier "
                f"assignment would be ambiguous."
            )
        if self.tiers[0].min_monthly_volume_shares != 0:
            raise FeeScheduleError(
                f"{venue_id}: schedule has no base tier. The lowest threshold is "
                f"{self.tiers[0].min_monthly_volume_shares:,} shares, so volume below "
                f"it would fall through to no tier at all. Add an explicit tier at 0."
            )

        self._schedule_warnings = self._check_model_consistency()

    def _check_model_consistency(self) -> List[str]:
        """
        Verifies each tier against the declared pricing model.

        A schedule that contradicts its declared model is the failure this skill
        exists to catch: routing passive flow to a venue believed to pay makers when
        it actually charges them. A definitional violation raises; a tier that merely
        fails to pay the side it is supposed to pay is surfaced as a warning, because
        real schedules do include zero-rebate and fee-charging tiers.
        """
        warnings: List[str] = []
        for tier in self.tiers:
            maker, taker = tier.maker_rate_per_share, tier.taker_rate_per_share
            if self.pricing_model is PricingModel.MAKER_TAKER:
                if maker > taker:
                    raise FeeScheduleError(
                        f"{self.venue_id}/{tier.tier_name}: declared MAKER_TAKER but "
                        f"the maker rate {maker:+.5f} is worse than the taker rate "
                        f"{taker:+.5f}. That is an inverted schedule -- declare "
                        f"TAKER_MAKER or correct the rates."
                    )
                if maker > 0:
                    warnings.append(
                        f"{tier.tier_name}: MAKER_TAKER venue CHARGES makers "
                        f"{maker:+.5f}/sh at this tier -- passive flow earns no "
                        f"rebate here."
                    )
            else:  # TAKER_MAKER
                if taker > maker:
                    raise FeeScheduleError(
                        f"{self.venue_id}/{tier.tier_name}: declared TAKER_MAKER but "
                        f"the taker rate {taker:+.5f} is worse than the maker rate "
                        f"{maker:+.5f}. That is a maker-taker schedule -- declare "
                        f"MAKER_TAKER or correct the rates."
                    )
                if taker > 0:
                    warnings.append(
                        f"{tier.tier_name}: inverted venue CHARGES takers "
                        f"{taker:+.5f}/sh at this tier -- aggressive flow earns no "
                        f"rebate here."
                    )
        return warnings

    def _resolve_tier(
        self, qualifying_volume: int
    ) -> Tuple[FeeTierDefinition, Optional[FeeTierDefinition]]:
        """
        Returns ``(active_tier, next_tier)``.

        A base tier at threshold 0 is guaranteed by ``__init__``, so every
        non-negative volume qualifies for exactly one tier and the caller can never
        be silently assigned a tier it does not qualify for.
        """
        active = self.tiers[0]
        next_tier: Optional[FeeTierDefinition] = (
            self.tiers[1] if len(self.tiers) > 1 else None
        )
        for idx, tier in enumerate(self.tiers):
            if qualifying_volume >= tier.min_monthly_volume_shares:
                active = tier
                next_tier = self.tiers[idx + 1] if idx + 1 < len(self.tiers) else None
        return active, next_tier

    @staticmethod
    def _signed_cost(
        maker_shares: float, taker_shares: float, tier: FeeTierDefinition
    ) -> float:
        return (
            maker_shares * tier.maker_rate_per_share
            + taker_shares * tier.taker_rate_per_share
        )

    def analyze_fee_tier_and_rebates(
        self,
        summary: VenueVolumeSummary,
        incremental_maker_fraction: Optional[float] = None,
    ) -> FeeTierAnalysisReport:
        """
        Determines the active tier, computes signed net execution cost, and evaluates
        the net benefit of reaching the next tier.

        ``incremental_maker_fraction`` is the maker share of the additional volume
        required to reach the next tier. It defaults to the observed maker mix; when
        no volume is being priced there is no mix to observe and it defaults to 0.0
        (all-taker), the conservative assumption on a maker-taker venue.
        """
        if not isinstance(summary, VenueVolumeSummary):
            raise TypeError(
                f"summary must be a VenueVolumeSummary, got {type(summary).__name__}."
            )
        if (
            summary.pricing_model
            and _normalize(summary.pricing_model) != self.pricing_model.value
        ):
            raise FeeScheduleError(
                f"summary.pricing_model {summary.pricing_model!r} contradicts the "
                f"engine's {self.pricing_model.value!r}."
            )

        maker_shares = summary.rolling_30d_maker_volume_shares
        taker_shares = summary.rolling_30d_taker_volume_shares
        priced_vol = summary.priced_volume_shares

        warnings = list(self._schedule_warnings)

        # --- 1. Qualifying volume. The whole point of Rule 610(d) is that this is
        #        NOT necessarily the volume being priced.
        if self.qualification_basis is TierQualificationBasis.PRIOR_PERIOD:
            if summary.qualifying_volume_shares is None:
                raise FeeScheduleError(
                    f"{self.venue_id}: qualification_basis is PRIOR_PERIOD, so "
                    f"qualifying_volume_shares must be supplied from a completed "
                    f"prior period. Under Reg NMS Rule 610(d) the volume being "
                    f"priced cannot determine its own rate, so it must not be "
                    f"substituted for the prior period's volume."
                )
            qualifying_vol = summary.qualifying_volume_shares
            if qualifying_vol == priced_vol and priced_vol > 0:
                # The likeliest way to defeat the PRIOR_PERIOD guard is to satisfy it
                # by handing back the priced volume, which reintroduces exactly the
                # current-period tiering Rule 610(d) prohibits. It can be a genuine
                # coincidence, so this is surfaced rather than raised.
                warnings.append(
                    f"qualifying_volume_shares ({qualifying_vol:,}) equals the priced "
                    f"volume. Under PRIOR_PERIOD it must come from a COMPLETED PRIOR "
                    f"period -- confirm this is a coincidence and not the priced "
                    f"volume passed back, which would reinstate the current-period "
                    f"tiering Rule 610(d) prohibits."
                )
        else:
            qualifying_vol = (
                summary.qualifying_volume_shares
                if summary.qualifying_volume_shares is not None
                else priced_vol
            )

        active_tier, next_tier = self._resolve_tier(qualifying_vol)

        # --- 2. Signed per-side economics. Computed in full precision and rounded
        #        once, at the reporting boundary, so rounding never compounds.
        maker_side = maker_shares * active_tier.maker_rate_per_share
        taker_side = taker_shares * active_tier.taker_rate_per_share
        net_cost = maker_side + taker_side

        # Legacy fields keep their published meaning: a "fee" is the charged portion
        # and a "rebate" the credited portion of the corresponding side.
        gross_taker_fees = max(0.0, taker_side)
        gross_maker_rebates = max(0.0, -maker_side)
        if self.pricing_model is PricingModel.TAKER_MAKER:
            warnings.append(
                "Inverted venue: read maker_side_cost_usd / taker_side_cost_usd. "
                "gross_taker_fees_usd and gross_maker_rebates_usd describe the "
                "maker-taker orientation and are 0.0 here by construction."
            )

        effective_cps = net_cost / priced_vol if priced_vol > 0 else 0.0

        # --- 3. Tier jump economics.
        vol_needed = 0
        gross_savings = 0.0
        incremental_cost = 0.0
        net_benefit = 0.0
        benefit_period: Optional[str] = None

        if next_tier is not None:
            vol_needed = max(0, next_tier.min_monthly_volume_shares - qualifying_vol)

            # Savings on the priced mix if it were billed at the next tier's rates.
            # Deliberately NOT clamped at zero: a next tier can be worse (a lower
            # rebate paired with a lower fee), and hiding that produces exactly the
            # wrong routing decision.
            cost_at_next = self._signed_cost(maker_shares, taker_shares, next_tier)
            gross_savings = net_cost - cost_at_next

            if incremental_maker_fraction is None:
                mix = (maker_shares / priced_vol) if priced_vol > 0 else 0.0
            else:
                mix = float(incremental_maker_fraction)
                if not 0.0 <= mix <= 1.0:
                    raise FeeScheduleError(
                        "incremental_maker_fraction must be in [0, 1], got "
                        f"{incremental_maker_fraction!r}."
                    )

            inc_maker = vol_needed * mix
            inc_taker = vol_needed - inc_maker

            if self.qualification_basis is TierQualificationBasis.PRIOR_PERIOD:
                # The extra shares trade in the CURRENT period and are therefore
                # billed at the CURRENT tier; the better rate only applies next
                # period. Rule 610(d) is precisely this: today's volume cannot
                # reprice today's fills.
                billing_tier = active_tier
                benefit_period = "NEXT_PERIOD"
            else:
                # Crossing the threshold reprices the rolling window, so the
                # incremental shares are billed at the next tier as well.
                billing_tier = next_tier
                benefit_period = "CURRENT_PERIOD"

            incremental_cost = self._signed_cost(inc_maker, inc_taker, billing_tier)
            net_benefit = gross_savings - incremental_cost

            if vol_needed > 0 and net_benefit <= 0.0:
                warnings.append(
                    f"Tier jump to '{next_tier.tier_name}' is not economic: "
                    f"{vol_needed:,} additional shares cost "
                    f"{_fmt_usd(incremental_cost)} against "
                    f"{_fmt_usd(gross_savings)} gross savings. This ignores adverse "
                    f"selection and market impact on the forced volume, so the true "
                    f"case is weaker still."
                )

        basis_note = (
            "tier fixed by prior-period volume (Reg NMS Rule 610(d))"
            if self.qualification_basis is TierQualificationBasis.PRIOR_PERIOD
            else "tier fixed by rolling current-window volume (non-NMS venue)"
        )
        notes = (
            f"FEE TIER ANALYSIS [{self.venue_id}]: Active Tier = "
            f"'{active_tier.tier_name}' ({basis_note}; qualifying vol "
            f"{qualifying_vol:,} sh, priced vol {priced_vol:,} sh). "
            f"Net Cost = {_fmt_usd(net_cost)} ({_fmt_per_share(effective_cps)}/sh). "
            f"Maker side = {_fmt_usd(maker_side)}, "
            f"Taker side = {_fmt_usd(taker_side)}."
        )
        logger.info(notes)
        for warning in warnings:
            logger.warning("[%s] %s", self.venue_id, warning)

        return FeeTierAnalysisReport(
            venue_id=self.venue_id,
            active_tier_name=active_tier.tier_name,
            pricing_model=self.pricing_model.value,
            total_volume_shares=priced_vol,
            gross_taker_fees_usd=round(gross_taker_fees, _USD_DP),
            gross_maker_rebates_usd=round(gross_maker_rebates, _USD_DP),
            net_transaction_cost_usd=round(net_cost, _USD_DP),
            effective_cost_per_share=round(effective_cps, _PER_SHARE_DP),
            next_tier_name=next_tier.tier_name if next_tier else None,
            volume_needed_for_next_tier_shares=vol_needed,
            estimated_monthly_savings_at_next_tier_usd=round(gross_savings, _USD_DP),
            audit_notes=notes,
            tier_qualification_basis=self.qualification_basis.value,
            qualifying_volume_shares=qualifying_vol,
            maker_side_cost_usd=round(maker_side, _USD_DP),
            taker_side_cost_usd=round(taker_side, _USD_DP),
            tier_jump_benefit_period=benefit_period,
            incremental_volume_cost_usd=round(incremental_cost, _USD_DP),
            net_tier_jump_benefit_usd=round(net_benefit, _USD_DP),
            warnings=warnings,
        )


def check_reg_nms_access_fee_cap(
    tiers: Sequence[FeeTierDefinition],
    cap_per_share: float = REG_NMS_610C_ACCESS_FEE_CAP_USD,
) -> List[str]:
    """
    Flags tiers whose taker (access) fee exceeds the Reg NMS Rule 610(c) cap.

    Applies only to US NMS stocks priced at or above $1.00 per share. The cap
    currently in force is $0.0030/share; the 2024 amendments reduce it to $0.0010,
    with compliance deferred to the first business day of November 2027 by the SEC's
    June 11 2026 exemptive order. Pass ``REG_NMS_610C_AMENDED_CAP_USD`` to test a
    schedule against the reduced cap ahead of that date.

    Returns a list of human-readable breach descriptions; an empty list means no
    breach. Sub-$1.00 securities are capped as a percentage of quotation price and
    are out of scope for this per-share check.
    """
    if cap_per_share <= 0:
        raise FeeScheduleError(f"cap_per_share must be > 0, got {cap_per_share}.")
    breaches: List[str] = []
    for tier in tiers:
        if tier.taker_rate_per_share > cap_per_share:
            breaches.append(
                f"{tier.tier_name}: taker fee ${tier.taker_rate_per_share:.5f}/sh "
                f"exceeds the Rule 610(c) cap of ${cap_per_share:.5f}/sh."
            )
    return breaches
