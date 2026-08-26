"""
insurance-coverage-assessment-for-custodied-crypto: quantifies how much of a firm's
custodied digital assets a custodian's insurance programme would actually return.

What this module is and is not
------------------------------
It is a **scenario calculator** for counterparty risk sizing. It converts a documented
policy structure into a bracketed recovery estimate so treasury can size uninsured
exposure. It is not a coverage opinion: only the policy wording, read by a broker or
coverage counsel, determines whether a given loss is covered at all.

Policy form does not equal wallet tier
--------------------------------------
The common shorthand -- "specie covers cold, crime covers hot" -- is wrong, and
believing it is the single most likely way to mis-audit a custodian.

* "Specie" is a Lloyd's *class* for high-value moveable property, written on an
  all-risks physical loss or damage basis. Digital asset specie wordings are offered
  for cold, warm **and** hot wallets.
* "Crime" is a *form* covering theft, employee dishonesty, and fraudulent transfer.

Which tier a policy attaches to is set by its wording, not its name. Two live
examples pointing in opposite directions:

* Coinbase Custody Trust maintains a **commercial crime** policy of $320M that, per
  the fund disclosures it appears in, "covers the loss of client assets held in cold
  storage" -- a crime form covering the cold tier.
* BitGo's $250M programme is cold-storage only where BitGo holds all keys, and per
  BitGo's own insurance pages does **not** extend to hot wallets.

So ``hot_crime_policy_limit_usd`` and ``cold_specie_policy_limit_usd`` are named for
the repository's established vocabulary, but each should be populated with the limit
of whichever policy the auditor has confirmed attaches to that wallet tier.

The two scenarios this engine reports
-------------------------------------
A single "coverage" number is misleading, because recovery depends on how many of the
custodian's clients are hit by the same event:

* **Isolated loss** (upper bound): only this firm's assets are lost. Recovery is
  capped by the per-occurrence limit, not by any sharing. Crime forms are typically
  occurrence-based, with the full limit available for each occurrence.
* **Pooled loss** (lower bound): the custodian's whole book is hit at once and the
  shared limit is split pro-rata. Fund disclosures state the custodian's insurance
  "is shared among all of the ... customers, is not specific to the Trust", and that
  the fund "may be forced to share such insurance proceeds with other clients".

Real recovery normally sits between the two. Reporting only the pooled figure
understates coverage; reporting only the isolated figure understates risk. This engine
reports both and treats the pooled figure as the planning number.

The ordering can invert, and that inversion is real rather than a modelling artefact.
Where the retention exceeds the firm's own balance, a loss confined to this firm falls
entirely inside the deductible and recovers nothing, while a pool-wide loss still
erodes the retention at the tower level and pays this firm a pro-rata share. A firm
whose balance is smaller than its custodian's retention is, for practical purposes,
uninsured against any incident that affects only it.

Known limitations -- read before relying on the output
------------------------------------------------------
* **The client is usually not the named insured.** The policy belongs to the
  custodian. Absent a dedicated loss-payee endorsement, the firm has no direct claim
  on proceeds, only a contractual claim against the custodian.
* **Insurance does not address custodian insolvency.** In a custodian bankruptcy
  clients may be treated as general unsecured creditors of the estate. No coverage
  ratio computed here mitigates that; it is a segregation and bankruptcy-remoteness
  question. See ``custody-solution-vendor-due-diligence-checklist``.
* **The dilution denominator is often undisclosed.** Custodians commonly decline, for
  security reasons, to reveal the cold/hot split of the omnibus wallets holding client
  assets. ``total_custodian_cold_aum_usd`` is therefore usually an estimate, and every
  report says so.
* **Hot and cold limits are modelled as independent towers.** If the custodian buys a
  single tower with one shared annual aggregate across both tiers, summing the two
  limits overstates recovery. Confirm this from the binder.
* **Neither FDIC nor SIPC protection applies** to custodied digital assets.
* Sub-limits per peril, annual aggregate erosion from prior claims, coinsurance, and
  the custodian's own capital reserves are not modelled.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Engineering defaults, not regulatory thresholds. No regulator prescribes a minimum
#: custody insurance coverage ratio; these encode a conservative treasury posture --
#: fully cover the actively exploitable hot tier, and treat a >5% net uninsured book
#: as a shortfall worth escalating. Override them to match a firm's own risk appetite.
DEFAULT_MIN_HOT_COVERAGE_RATIO = 1.0
DEFAULT_MIN_NET_COVERAGE_RATIO = 0.95

#: Ratio comparisons are made on unrounded values with a float tolerance, so that a
#: coverage shortfall is never hidden by presentation rounding.
_RATIO_TOLERANCE = 1e-12

STATUS_FULLY_INSURED = "FULLY_INSURED"
STATUS_PARTIALLY_INSURED_SHORTFALL = "PARTIALLY_INSURED_SHORTFALL"
STATUS_CRITICAL_HOT_WALLET_UNINSURED = "CRITICAL_HOT_WALLET_UNINSURED"


class CustodyInsuranceError(ValueError):
    """Raised when a custody insurance spec or engine configuration is invalid.

    An insurance audit must fail loudly. A negative policy limit, a NaN AUM, or a firm
    cold balance larger than the custodian's entire cold pool are data-entry errors,
    and auditing them anyway produces an authoritative-looking coverage percentage
    built on garbage -- historically, in the direction of *overstating* coverage.
    """


def _validate_amount(value: float, label: str) -> float:
    """Return ``value`` as a finite, non-negative float or raise ``CustodyInsuranceError``."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise CustodyInsuranceError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(amount):
        raise CustodyInsuranceError(
            f"{label} must be a finite number, got {value!r}. NaN or infinity here "
            f"silently propagates into the coverage ratio and can yield a "
            f"'{STATUS_FULLY_INSURED}' verdict for an uninsured book."
        )
    if amount < 0.0:
        raise CustodyInsuranceError(f"{label} must be >= 0, got {amount!r}.")
    return amount


@dataclass
class CustodyInsuranceSpec:
    """Documented insurance structure for one firm at one custodian.

    Every figure should trace to the insurance binder, the custody agreement, or a
    written custodian representation -- not to a marketing page. The engine audits
    what it is told and cannot detect a misstated limit or an unread exclusion.
    """

    custodian_name: str
    firm_hot_wallet_aum_usd: float
    firm_cold_wallet_aum_usd: float
    #: Limit of the policy confirmed to attach to the hot/warm tier, whatever form it
    #: is written on. Set to 0.0 where the custodian insures cold storage only -- that
    #: is the actual position at several major custodians.
    hot_crime_policy_limit_usd: float
    #: Limit of the policy confirmed to attach to the cold tier.
    cold_specie_policy_limit_usd: float
    #: Total client assets in the custodian's cold pool, the pro-rata denominator.
    #: Usually an estimate: custodians often decline to disclose the omnibus cold/hot
    #: split. Required whenever the firm holds cold assets on a shared limit.
    total_custodian_cold_aum_usd: float
    #: Retention borne before the policy pays. Institutional crime retentions are
    #: material and often per-occurrence, so several occurrences can mean several
    #: retentions. Some custodians (BitGo states this) absorb the deductible
    #: themselves, in which case 0.0 is correct -- confirm rather than assume.
    hot_policy_deductible_usd: float = 0.0
    cold_policy_deductible_usd: float = 0.0
    #: Total client assets in the custodian's hot pool. Hot balances are held in
    #: omnibus wallets and are diluted exactly as cold balances are; supply this to
    #: model that. Left as ``None``, the hot tier is modelled as undiluted, which is
    #: optimistic, and the report says so.
    total_custodian_hot_aum_usd: Optional[float] = None
    #: True where the firm holds a dedicated limit reserved to it -- e.g. excess specie
    #: with the firm named as dedicated customer loss payee. A dedicated limit is not
    #: shared, so pro-rata dilution does not apply to it.
    cold_limit_is_dedicated_to_firm: bool = False


@dataclass
class CustodyInsuranceReport:
    """Bracketed recovery estimate for one firm/custodian pair.

    Fields without a scenario qualifier describe the **pooled loss** scenario -- the
    conservative planning case in which the custodian's whole book is hit at once and
    the shared limit is split pro-rata.
    """

    custodian_name: str
    total_firm_aum_usd: float
    hot_wallet_coverage_pct: float
    cold_wallet_pro_rata_dilution_pct: float
    cold_wallet_effective_coverage_usd: float
    cold_wallet_effective_coverage_pct: float
    total_uninsured_shortfall_usd: float
    net_insured_coverage_pct: float
    status: str
    audit_notes: str
    #: Pro-rata dilution applied to the hot tier; 100.0 when the hot pool was not
    #: supplied, in which case the hot tier is modelled as undiluted.
    hot_wallet_pro_rata_dilution_pct: float = 100.0
    #: Upper bound: recovery if only this firm's assets are lost, so the per-occurrence
    #: limit is not shared with other clients.
    isolated_loss_total_recovery_usd: float = 0.0
    isolated_loss_net_coverage_pct: float = 0.0
    isolated_loss_uninsured_shortfall_usd: float = 0.0
    #: Modelling assumptions and provenance caveats that materially qualify the figures
    #: above. Reproduce these alongside any number taken from this report.
    assumptions: List[str] = field(default_factory=list)


class CustodyInsuranceAssessmentEngine:
    """Quantifies recoverable and uninsured capital across a custodian's hot and cold tiers.

    Args:
        min_hot_coverage_ratio: Hot-tier coverage below this fraction of hot AUM is
            classified critical. Default 1.0.
        min_net_coverage_ratio: Net pooled coverage below this fraction of total AUM is
            classified a shortfall. Default 0.95.

    Both are engineering defaults with no regulatory basis -- see
    ``DEFAULT_MIN_HOT_COVERAGE_RATIO``.
    """

    def __init__(
        self,
        min_hot_coverage_ratio: float = DEFAULT_MIN_HOT_COVERAGE_RATIO,
        min_net_coverage_ratio: float = DEFAULT_MIN_NET_COVERAGE_RATIO,
    ) -> None:
        for label, ratio in (
            ("min_hot_coverage_ratio", min_hot_coverage_ratio),
            ("min_net_coverage_ratio", min_net_coverage_ratio),
        ):
            if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
                raise CustodyInsuranceError(f"{label} must be a number, got {ratio!r}.")
            if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
                raise CustodyInsuranceError(
                    f"{label} must be a finite fraction in [0.0, 1.0], got {ratio!r}."
                )
        self.min_hot_coverage_ratio = float(min_hot_coverage_ratio)
        self.min_net_coverage_ratio = float(min_net_coverage_ratio)

    def audit_custody_insurance(self, spec: CustodyInsuranceSpec) -> CustodyInsuranceReport:
        """Audit a firm's custodied balances against the custodian's insurance programme.

        Returns a report bracketing recovery between an isolated-loss upper bound and a
        pooled-loss lower bound. Raises ``CustodyInsuranceError`` on any spec that
        cannot be audited honestly.
        """
        hot_aum, cold_aum, tot_firm_aum = self._validate(spec)
        assumptions: List[str] = []

        # 1. Hot tier. Diluted only when the hot pool is known; otherwise modelled
        #    undiluted, which is optimistic and disclosed as such.
        hot_pooled, hot_isolated, hot_dilution = self._tier_recovery(
            balance=hot_aum,
            limit=spec.hot_crime_policy_limit_usd,
            deductible=spec.hot_policy_deductible_usd,
            pool_aum=spec.total_custodian_hot_aum_usd,
            is_dedicated=False,
        )
        # Only meaningful where there is a hot limit to dilute; a 0.0 limit is already
        # reported as 0% coverage and needs no dilution caveat.
        if (
            spec.total_custodian_hot_aum_usd is None
            and hot_aum > 0.0
            and float(spec.hot_crime_policy_limit_usd) > 0.0
        ):
            assumptions.append(
                "total_custodian_hot_aum_usd not supplied: the hot tier is modelled as "
                "an undiluted dedicated limit. Hot balances are normally held in "
                "omnibus wallets on a shared limit, so hot recovery may be overstated."
            )
        hot_ratio = hot_pooled / hot_aum if hot_aum > 0.0 else 1.0

        # 2. Cold tier and pro-rata dilution of the shared specie/crime limit.
        cold_pooled, cold_isolated, cold_dilution = self._tier_recovery(
            balance=cold_aum,
            limit=spec.cold_specie_policy_limit_usd,
            deductible=spec.cold_policy_deductible_usd,
            pool_aum=spec.total_custodian_cold_aum_usd,
            is_dedicated=spec.cold_limit_is_dedicated_to_firm,
        )
        if cold_aum > 0.0:
            if spec.cold_limit_is_dedicated_to_firm:
                assumptions.append(
                    "cold_limit_is_dedicated_to_firm=True: the cold limit is treated as "
                    "reserved to this firm (e.g. excess specie with the firm named as "
                    "dedicated customer loss payee), so pro-rata dilution is not "
                    "applied. Confirm the endorsement names the firm."
                )
            else:
                assumptions.append(
                    "Pooled cold recovery divides the shared limit pro-rata by "
                    "total_custodian_cold_aum_usd, which custodians commonly decline to "
                    "disclose. Treat that denominator as an estimate and re-verify it "
                    "at each review."
                )
        cold_ratio = cold_pooled / cold_aum if cold_aum > 0.0 else 1.0

        # 3. Bracketed recovery. Pooled is the conservative planning number.
        pooled_recovery = hot_pooled + cold_pooled
        isolated_recovery = hot_isolated + cold_isolated
        net_ratio = pooled_recovery / tot_firm_aum
        isolated_ratio = isolated_recovery / tot_firm_aum

        assumptions.append(
            "Hot and cold limits are modelled as independent towers. If the custodian "
            "buys one tower with a shared annual aggregate across both tiers, this "
            "overstates recovery."
        )
        assumptions.append(
            "Recovery assumes the loss is a covered peril and that proceeds reach the "
            "firm. The policy is normally the custodian's, not the firm's; absent a "
            "loss-payee endorsement the firm holds a contractual claim on the "
            "custodian, not a direct claim on the insurer. Insurance does not address "
            "custodian insolvency, where clients may rank as unsecured creditors."
        )

        hot_coverage_pct = round(hot_ratio * 100.0, 2)
        cold_coverage_pct = round(cold_ratio * 100.0, 2)
        net_insured_pct = round(net_ratio * 100.0, 2)
        isolated_net_pct = round(isolated_ratio * 100.0, 2)
        uninsured_shortfall_usd = round(tot_firm_aum - pooled_recovery, 2)
        isolated_shortfall_usd = round(tot_firm_aum - isolated_recovery, 2)

        # 4. Classification on unrounded ratios, so presentation rounding can never
        #    promote a shortfall into a clean verdict.
        if hot_ratio < self.min_hot_coverage_ratio - _RATIO_TOLERANCE:
            status = STATUS_CRITICAL_HOT_WALLET_UNINSURED
            notes = (
                f"CUSTODY INSURANCE WARNING [{spec.custodian_name}]: Hot tier policy covers only "
                f"{hot_coverage_pct:.1f}% of hot AUM (${hot_pooled:,.0f}/${hot_aum:,.0f}) in the "
                f"pooled-loss scenario. Uninsured hot exposure."
            )
            logger.critical(notes)
        elif net_ratio < self.min_net_coverage_ratio - _RATIO_TOLERANCE:
            status = STATUS_PARTIALLY_INSURED_SHORTFALL
            notes = (
                f"CUSTODY INSURANCE AUDIT [{spec.custodian_name}]: Pooled-loss net coverage is "
                f"{net_insured_pct:.1f}% (${uninsured_shortfall_usd:,.2f} uninsured); isolated-loss "
                f"net coverage is {isolated_net_pct:.1f}%. Cold pro-rata dilution = "
                f"{cold_dilution * 100.0:.1f}%."
            )
            logger.warning(notes)
        else:
            status = STATUS_FULLY_INSURED
            notes = (
                f"CUSTODY INSURANCE APPROVED [{spec.custodian_name}]: Firm AUM ${tot_firm_aum:,.0f} is "
                f"{net_insured_pct:.1f}% covered in the pooled-loss scenario (hot "
                f"{hot_coverage_pct:.1f}%, cold {cold_coverage_pct:.1f}%). Subject to the "
                f"{len(assumptions)} recorded assumption(s)."
            )
            logger.info(notes)

        return CustodyInsuranceReport(
            custodian_name=spec.custodian_name,
            total_firm_aum_usd=tot_firm_aum,
            hot_wallet_coverage_pct=hot_coverage_pct,
            cold_wallet_pro_rata_dilution_pct=round(cold_dilution * 100.0, 2),
            cold_wallet_effective_coverage_usd=round(cold_pooled, 2),
            cold_wallet_effective_coverage_pct=cold_coverage_pct,
            total_uninsured_shortfall_usd=uninsured_shortfall_usd,
            net_insured_coverage_pct=net_insured_pct,
            status=status,
            audit_notes=notes,
            hot_wallet_pro_rata_dilution_pct=round(hot_dilution * 100.0, 2),
            isolated_loss_total_recovery_usd=round(isolated_recovery, 2),
            isolated_loss_net_coverage_pct=isolated_net_pct,
            isolated_loss_uninsured_shortfall_usd=isolated_shortfall_usd,
            assumptions=assumptions,
        )

    # -- internals ---------------------------------------------------------------

    def _validate(self, spec: CustodyInsuranceSpec) -> Tuple[float, float, float]:
        """Validate the spec and return ``(hot_aum, cold_aum, total_firm_aum)``."""
        if not str(spec.custodian_name).strip():
            raise CustodyInsuranceError("custodian_name must be a non-empty string.")

        hot_aum = _validate_amount(spec.firm_hot_wallet_aum_usd, "firm_hot_wallet_aum_usd")
        cold_aum = _validate_amount(spec.firm_cold_wallet_aum_usd, "firm_cold_wallet_aum_usd")
        _validate_amount(spec.hot_crime_policy_limit_usd, "hot_crime_policy_limit_usd")
        _validate_amount(spec.cold_specie_policy_limit_usd, "cold_specie_policy_limit_usd")
        _validate_amount(spec.total_custodian_cold_aum_usd, "total_custodian_cold_aum_usd")
        _validate_amount(spec.hot_policy_deductible_usd, "hot_policy_deductible_usd")
        _validate_amount(spec.cold_policy_deductible_usd, "cold_policy_deductible_usd")
        if spec.total_custodian_hot_aum_usd is not None:
            _validate_amount(spec.total_custodian_hot_aum_usd, "total_custodian_hot_aum_usd")

        tot_firm_aum = hot_aum + cold_aum
        if tot_firm_aum <= 0.0:
            raise CustodyInsuranceError("Total firm AUM must be > 0.")

        # The firm's own balance is a subset of the custodian's pool. A pool smaller
        # than the firm's balance is a data error, and silently clamping it -- as an
        # earlier revision did -- inflates the dilution factor and overstates coverage.
        if cold_aum > 0.0 and not spec.cold_limit_is_dedicated_to_firm:
            pool = float(spec.total_custodian_cold_aum_usd)
            if pool <= 0.0:
                raise CustodyInsuranceError(
                    "total_custodian_cold_aum_usd must be > 0 to compute pro-rata "
                    "dilution when the firm holds cold assets on a shared limit. "
                    "Obtain or estimate the custodian's cold pool. Do not set "
                    "cold_limit_is_dedicated_to_firm=True to bypass this: that asserts "
                    "the firm holds a limit reserved to it by endorsement, and is only "
                    "correct against written evidence of one."
                )
            if pool < cold_aum:
                raise CustodyInsuranceError(
                    f"total_custodian_cold_aum_usd ({pool:,.2f}) is smaller than "
                    f"firm_cold_wallet_aum_usd ({cold_aum:,.2f}); the firm's balance is "
                    f"part of the custodian's pool, so this is a data error."
                )

        if spec.total_custodian_hot_aum_usd is not None:
            hot_pool = float(spec.total_custodian_hot_aum_usd)
            if hot_aum > 0.0 and hot_pool < hot_aum:
                raise CustodyInsuranceError(
                    f"total_custodian_hot_aum_usd ({hot_pool:,.2f}) is smaller than "
                    f"firm_hot_wallet_aum_usd ({hot_aum:,.2f}); the firm's balance is "
                    f"part of the custodian's pool, so this is a data error."
                )

        return hot_aum, cold_aum, tot_firm_aum

    @staticmethod
    def _tier_recovery(
        balance: float,
        limit: float,
        deductible: float,
        pool_aum: Optional[float],
        is_dedicated: bool,
    ) -> Tuple[float, float, float]:
        """Recovery for one wallet tier under both scenarios.

        Returns ``(pooled_recovery, isolated_recovery, dilution_factor)``.

        *Isolated loss*: only this firm is hit, so the payout is ``min(balance, limit)``
        net of the retention, floored at zero. A retention larger than the balance
        therefore recovers nothing -- correct, not a defect.

        *Pooled loss*: the whole pool is hit. The retention is absorbed once at the
        tower level, leaving ``max(0, limit - deductible)`` of net proceeds to split
        pro-rata, so each client recovers that fraction of its own balance. The
        retention is deliberately not deducted a second time from this firm's share.

        A dedicated limit, or a pool that is unknown (``None``) or empty, means nothing
        is shared: both scenarios collapse to the isolated figure at a dilution of 1.0.
        An unknown pool is an optimistic assumption that callers must disclose.
        """
        isolated = max(0.0, min(float(balance), float(limit)) - float(deductible))
        if is_dedicated or pool_aum is None or float(pool_aum) <= 0.0:
            return isolated, isolated, 1.0
        net_limit = max(0.0, float(limit) - float(deductible))
        dilution = min(1.0, net_limit / float(pool_aum))
        return float(balance) * dilution, isolated, dilution
