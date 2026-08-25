"""
exchange-for-physical-efp-transactions: pre-submission validator and basis calculator
for the Exchange for Physical (EFP) leg of an Exchange for Related Position (EFRP).

What this module does
---------------------
Given one party's two EFP legs (its futures leg and its physical/cash leg) it:

1. validates that the two legs are on **opposite sides** of the market, which is the
   defining structural requirement of an EFRP;
2. checks that the physical quantity is equivalent to the futures quantity, within a
   tolerance the operator configures;
3. records the operator's bona fide attestations (ownership transfer, non-transitory
   execution, account independence) so the compliance decision is auditable;
4. computes the observed EFP basis against a cost-of-carry fair basis;
5. emits a structured ``EfpAuditReport`` plus an internal pre-submission payload.

What this module does NOT do
----------------------------
- **It does not submit anything.** EFRPs are submitted through venue systems (CME Direct
  or CME ClearPort at CME Group, ICE Block at ICE). ``efrp_clearing_payload`` is an
  internal pre-submission record, not a venue wire format, so its ``reporting_status`` is
  ``PENDING_SUBMISSION``; the caller sets a real status only once the venue acknowledges.
- **It cannot verify a bona fide transaction.** Whether title actually passed, whether the
  trade was transitory, and whether the accounts are independently controlled are facts
  about the outside world. The engine requires the operator to attest to them and records
  the attestation; it does not and cannot check them.
- **It does not decide the quantity rule for you.** Exchange rules require the related
  position to be *approximately* equivalent (ICE Futures U.S. Rule 4.06(b)(i); CME Rule
  538.C), not identical, and venue/product tolerances differ — Eurex, for example, permits
  the opposite FX transaction's nominal value to deviate from the FX future's by up to
  20%. The default here is a near-exact match, which is a strict *house* control, not a
  restatement of any rule. Set ``quantity_tolerance_ratio`` to what your venue and product
  actually permit.
- **It does not model hedge-ratio equivalence.** Rule 538 / Rule 4.06 permit appropriate
  hedge ratios to establish equivalence (e.g. an EFP-I against an index basket, or an EFR
  against a swap). This module compares a single physical quantity against
  ``contracts x multiplier`` in one unit and is not the right tool for those.

Cost-of-carry fair basis
------------------------
    theoretical_basis = S * (exp((r + u - y) * T) - 1)

from the carry relation F = S * exp((r + u - y) * T) (Hull, *Options, Futures and Other
Derivatives*, determination of forward and futures prices), where ``u`` is the
proportional annual storage cost and ``y`` the convenience yield — for an equity or index
underlying set ``u = 0`` and ``y`` to the dividend yield. ``r``, ``u`` and ``y`` are
continuously compounded annual rates on the same day-count basis as ``T``.

For a consumption commodity that relation is an upper bound rather than an equality, so a
*positive* mispricing (observed basis richer than carry) is the enforceable side while a
negative one is a view on convenience yield, not a riskless trade. See
``commodity-futures-storage-and-carry-cost-modeling`` for the full treatment.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

#: One party's admissible futures-leg sides.
VALID_FUTURES_SIDES = ("BUY_FUTURES", "SELL_FUTURES")

#: One party's admissible physical-leg sides.
VALID_PHYSICAL_SIDES = ("BUY_PHYSICAL", "SELL_PHYSICAL")

#: The only two structurally valid EFP leg pairings for a single party. An EFRP consists of
#: two discrete but related simultaneous transactions in which the buyer of the related
#: position is the seller of the corresponding Exchange contract and vice versa
#: (ICE Futures U.S. Rule 4.06(b)(i); CME Rule 538). Same-direction legs are not an EFRP.
VALID_SIDE_PAIRS = (
    ("BUY_FUTURES", "SELL_PHYSICAL"),
    ("SELL_FUTURES", "BUY_PHYSICAL"),
)


@dataclass
class EfpFuturesLeg:
    """
    The futures leg of an EFP, from the perspective of the party running this engine.

    ``futures_price_usd`` is quoted per unit of ``quantity_unit`` (e.g. USD per troy ounce
    for GC), *not* per contract.
    """
    futures_symbol: str                 # e.g. 'GC_202612' (Gold Futures) or 'CL_202609' (Crude Oil)
    contract_count: int
    contract_multiplier: float          # e.g. 100.0 (100 oz per Gold contract)
    futures_price_usd: float
    side: str                           # 'BUY_FUTURES' or 'SELL_FUTURES'
    quantity_unit: str = ""             # e.g. 'TROY_OZ', 'BBL'. Checked against the physical leg when both are set.


@dataclass
class EfpPhysicalLeg:
    """
    The cash/physical leg of an EFP, from the perspective of the same party.

    Its ``side`` must be the opposite of the futures leg's side. ``spot_price_usd`` is
    quoted per unit of ``quantity_unit``, on the same basis as ``futures_price_usd``.
    """
    physical_symbol: str                # e.g. 'SPOT_GOLD_USD' or 'PHYSICAL_WTI_CRUDE'
    physical_quantity: float            # e.g. 1000.0 oz
    spot_price_usd: float
    side: str                           # 'SELL_PHYSICAL' or 'BUY_PHYSICAL'
    quantity_unit: str = ""             # e.g. 'TROY_OZ', 'BBL'. Checked against the futures leg when both are set.


@dataclass
class EfrpBonaFideAttestation:
    """
    The operator's explicit attestations for the three bona fide requirements the engine
    cannot verify from prices and quantities.

    These are *records*, not checks. Setting a flag to True asserts a fact about the outside
    world; the engine stores it in the audit trail so the compliance decision is
    attributable, and refuses to approve the EFP when a flag is False or the attestation is
    absent.

    - ``ownership_transfer_confirmed`` — a bona fide transfer of ownership of the cash
      commodity between the parties, or a bona fide legally binding contract consistent with
      market convention (ICE Rule 4.06(b)(ii); CME Rule 538).
    - ``non_transitory_confirmed`` — execution is not contingent on another EFRP or
      related-position trade between the parties that offsets the related position without
      the incurrence of material market risk (ICE Rule 4.06(b)(iii); CME Rule 538.K). Narrow
      venue exceptions exist (e.g. immediately offsetting FX EFPs); when relying on one,
      cite it in ``supporting_document_ref``.
    - ``accounts_independently_controlled`` — the accounts are independently controlled with
      different beneficial ownership, or independently controlled accounts of separate legal
      entities under common beneficial ownership, or independently controlled accounts
      within one legal entity whose controllers operate in separate business units
      (ICE Rule 4.06(b)(iv); CME Rule 538.B).
    """
    ownership_transfer_confirmed: bool
    non_transitory_confirmed: bool
    accounts_independently_controlled: bool
    attested_by: str
    supporting_document_ref: str = ""

    def failed_requirements(self) -> Dict[str, str]:
        """Maps each unattested requirement to the rule it derives from."""
        failures: Dict[str, str] = {}
        if not self.ownership_transfer_confirmed:
            failures["ownership_transfer_confirmed"] = "ICE Rule 4.06(b)(ii) / CME Rule 538"
        if not self.non_transitory_confirmed:
            failures["non_transitory_confirmed"] = "ICE Rule 4.06(b)(iii) / CME Rule 538.K"
        if not self.accounts_independently_controlled:
            failures["accounts_independently_controlled"] = "ICE Rule 4.06(b)(iv) / CME Rule 538.B"
        return failures


@dataclass
class EfpAuditReport:
    efp_transaction_id: str
    futures_symbol: str
    physical_symbol: str
    observed_efp_basis_usd: float       # Futures Price - Spot Price, per unit
    theoretical_fair_basis_usd: float   # Spot * (exp((r + u - y) * T) - 1), per unit
    basis_mispricing_usd: float         # Observed Basis - Theoretical Basis, per unit
    is_quantity_equivalent: bool
    status: str                         # One of STATUSES
    efrp_clearing_payload: Optional[Dict[str, str]]
    audit_notes: str
    required_physical_quantity: float = 0.0
    quantity_deviation_ratio: float = 0.0   # |actual - required| / required
    is_side_structure_valid: bool = False
    is_bona_fide_attested: bool = False
    attestation_failures: Dict[str, str] = field(default_factory=dict)


#: Every status ``evaluate_efp_transaction`` can return.
STATUSES = (
    "EFP_APPROVED",
    "SIDE_DIRECTION_VIOLATION",
    "UNIT_MISMATCH_REJECTION",
    "QUANTITY_MISMATCH_REJECTION",
    "BONA_FIDE_ATTESTATION_MISSING",
    "RULE_538_VIOLATION",
)


def _require_finite(value: float, name: str, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{context}: {name} must be numeric, got {type(value).__name__}.")
    if not math.isfinite(value):
        raise ValueError(
            f"{context}: {name} must be finite, got {value}. A non-finite input propagates "
            "silently through the basis arithmetic and yields an approved report built on NaN."
        )
    return float(value)


def _require_positive(value: float, name: str, context: str) -> float:
    _require_finite(value, name, context)
    if value <= 0:
        raise ValueError(f"{context}: {name} must be > 0, got {value}.")
    return float(value)


class ExchangeForPhysicalEngine:
    """
    Pre-submission validator and basis calculator for the EFP leg of an EFRP.

    Fail-closed: an EFP is approved, and a pre-submission payload produced, only when the
    leg directions are structurally valid, the quantities are equivalent within the
    configured tolerance, and every bona fide requirement has been affirmatively attested.
    """

    def __init__(
        self,
        quantity_tolerance: float = 1e-4,
        quantity_tolerance_ratio: Optional[float] = None,
    ):
        """
        :param quantity_tolerance: absolute quantity tolerance, in the legs' quantity unit.
            The 1e-4 default is a near-exact match — a strict house control, stricter than
            the "approximately equivalent" standard the rules set, and meaningless across
            units unless both legs are quoted in the same one.
        :param quantity_tolerance_ratio: optional relative tolerance as a fraction of the
            required quantity (e.g. 0.20 for the 20% deviation Eurex permits on the FX leg
            of an FX-futures EFP). When set, the allowance becomes
            ``max(quantity_tolerance, quantity_tolerance_ratio * required_qty)``. Set it to
            what your venue and product actually permit; do not assume one venue's tolerance
            transfers to another.
        """
        _require_finite(quantity_tolerance, "quantity_tolerance", "ExchangeForPhysicalEngine")
        if quantity_tolerance < 0:
            raise ValueError(f"quantity_tolerance must be >= 0, got {quantity_tolerance}.")
        if quantity_tolerance_ratio is not None:
            _require_finite(
                quantity_tolerance_ratio, "quantity_tolerance_ratio", "ExchangeForPhysicalEngine"
            )
            if not 0.0 <= quantity_tolerance_ratio < 1.0:
                raise ValueError(
                    f"quantity_tolerance_ratio must be in [0, 1), got {quantity_tolerance_ratio}."
                )
        self.quantity_tolerance = float(quantity_tolerance)
        self.quantity_tolerance_ratio = quantity_tolerance_ratio

    def evaluate_efp_transaction(
        self,
        efp_id: str,
        futures_leg: EfpFuturesLeg,
        physical_leg: EfpPhysicalLeg,
        risk_free_rate: float = 0.04,        # r: 4.0% per annum, continuously compounded
        time_to_expiry_years: float = 0.25,
        attestation: Optional[EfrpBonaFideAttestation] = None,
        storage_cost_rate: float = 0.0,      # u: proportional annual storage cost
        convenience_yield: float = 0.0,      # y: convenience yield, or dividend yield for an index
    ) -> EfpAuditReport:
        """
        Validates the EFP structure and computes its basis against cost-of-carry fair value.

        Rejections are returned as an ``EfpAuditReport`` with a non-approved ``status``;
        malformed inputs raise instead, because a malformed EFP is a programming error
        rather than a compliance outcome.

        Basis figures are computed and reported on every rejection path too: a rejected EFP
        still has a real observed basis, and zero-filling those fields produces an audit
        record indistinguishable from a genuine zero basis.

        :param attestation: required for approval. ``None`` yields
            ``BONA_FIDE_ATTESTATION_MISSING`` — the engine will not approve an EFP whose
            bona fide status nobody has asserted.
        """
        context = f"evaluate_efp_transaction[{efp_id}]"
        if not isinstance(efp_id, str) or not efp_id.strip():
            raise ValueError("evaluate_efp_transaction: efp_id must be a non-empty string.")

        # --- Input validation -------------------------------------------------------
        if not isinstance(futures_leg.contract_count, int) or isinstance(futures_leg.contract_count, bool):
            raise TypeError(
                f"{context}: contract_count must be an int, got "
                f"{type(futures_leg.contract_count).__name__}."
            )
        if futures_leg.contract_count <= 0:
            raise ValueError(f"{context}: contract_count must be > 0, got {futures_leg.contract_count}.")
        _require_positive(futures_leg.contract_multiplier, "contract_multiplier", context)
        _require_positive(physical_leg.physical_quantity, "physical_quantity", context)
        # Prices are checked for finiteness but NOT for positivity: physically settled
        # commodity futures can and do trade negative (CME WTI settled at -$37.63 on
        # 2020-04-20). The carry relation is not meaningful at a non-positive spot, so that
        # case is flagged below rather than silently priced.
        _require_finite(futures_leg.futures_price_usd, "futures_price_usd", context)
        _require_finite(physical_leg.spot_price_usd, "spot_price_usd", context)
        _require_finite(risk_free_rate, "risk_free_rate", context)
        _require_finite(storage_cost_rate, "storage_cost_rate", context)
        _require_finite(convenience_yield, "convenience_yield", context)
        _require_finite(time_to_expiry_years, "time_to_expiry_years", context)
        if time_to_expiry_years < 0:
            raise ValueError(
                f"{context}: time_to_expiry_years must be >= 0, got {time_to_expiry_years}. "
                "A negative maturity inverts the carry term and reports a fair basis of the "
                "wrong sign."
            )
        if futures_leg.side not in VALID_FUTURES_SIDES:
            raise ValueError(
                f"{context}: futures_leg.side must be one of {VALID_FUTURES_SIDES}, "
                f"got {futures_leg.side!r}."
            )
        if physical_leg.side not in VALID_PHYSICAL_SIDES:
            raise ValueError(
                f"{context}: physical_leg.side must be one of {VALID_PHYSICAL_SIDES}, "
                f"got {physical_leg.side!r}."
            )

        required_physical_qty = float(futures_leg.contract_count) * futures_leg.contract_multiplier
        qty_diff = abs(physical_leg.physical_quantity - required_physical_qty)
        deviation_ratio = qty_diff / required_physical_qty

        # --- Basis arithmetic (computed for approvals and rejections alike) ----------
        observed_basis = round(futures_leg.futures_price_usd - physical_leg.spot_price_usd, 4)
        net_carry_rate = risk_free_rate + storage_cost_rate - convenience_yield
        if physical_leg.spot_price_usd <= 0:
            logger.warning(
                "%s: spot price %.4f is not positive; the cost-of-carry fair basis is not "
                "economically meaningful and must not be traded off.",
                context, physical_leg.spot_price_usd,
            )
        theoretical_basis = round(
            physical_leg.spot_price_usd * (math.exp(net_carry_rate * time_to_expiry_years) - 1.0), 4
        )
        basis_mispricing = round(observed_basis - theoretical_basis, 4)

        def _reject(
            status: str,
            msg: str,
            quantity_ok: bool,
            sides_ok: bool,
            attested: bool,
            failures: Dict[str, str],
        ) -> EfpAuditReport:
            logger.error(msg)
            return EfpAuditReport(
                efp_transaction_id=efp_id,
                futures_symbol=futures_leg.futures_symbol,
                physical_symbol=physical_leg.physical_symbol,
                observed_efp_basis_usd=observed_basis,
                theoretical_fair_basis_usd=theoretical_basis,
                basis_mispricing_usd=basis_mispricing,
                is_quantity_equivalent=quantity_ok,
                status=status,
                efrp_clearing_payload=None,
                audit_notes=msg,
                required_physical_quantity=required_physical_qty,
                quantity_deviation_ratio=deviation_ratio,
                is_side_structure_valid=sides_ok,
                is_bona_fide_attested=attested,
                attestation_failures=failures,
            )

        # --- Check 1: opposite-side leg structure -----------------------------------
        sides_ok = (futures_leg.side, physical_leg.side) in VALID_SIDE_PAIRS
        if not sides_ok:
            msg = (
                f"SIDE DIRECTION VIOLATION [{efp_id}]: legs are {futures_leg.side} and "
                f"{physical_leg.side}. An EFRP requires the buyer of the related position to be "
                f"the seller of the corresponding Exchange contract and vice versa "
                f"(ICE Rule 4.06(b)(i); CME Rule 538). Same-direction legs double the exposure "
                f"instead of exchanging it."
            )
            return _reject(
                "SIDE_DIRECTION_VIOLATION", msg,
                quantity_ok=False, sides_ok=False, attested=False, failures={},
            )

        # --- Check 2: quantity unit agreement ---------------------------------------
        f_unit, p_unit = futures_leg.quantity_unit.strip(), physical_leg.quantity_unit.strip()
        if f_unit and p_unit:
            if f_unit.upper() != p_unit.upper():
                msg = (
                    f"UNIT MISMATCH REJECTION [{efp_id}]: futures leg is quoted in {f_unit!r} but "
                    f"the physical leg in {p_unit!r}. Quantity equivalence and the basis spread "
                    f"are both meaningless across units (1,000 bbl is 42,000 gal)."
                )
                return _reject(
                    "UNIT_MISMATCH_REJECTION", msg,
                    quantity_ok=False, sides_ok=True, attested=False, failures={},
                )
        else:
            logger.warning(
                "%s: quantity_unit is not set on both legs, so quantity equivalence is being "
                "checked as a bare number with no unit agreement.", context,
            )

        # --- Check 3: quantity equivalence ------------------------------------------
        allowance = self.quantity_tolerance
        if self.quantity_tolerance_ratio is not None:
            allowance = max(allowance, self.quantity_tolerance_ratio * required_physical_qty)
        if qty_diff > allowance:
            msg = (
                f"QUANTITY MISMATCH REJECTION [{efp_id}]: Physical quantity "
                f"{physical_leg.physical_quantity} does not match futures equivalent "
                f"{required_physical_qty} ({futures_leg.contract_count} contracts x "
                f"{futures_leg.contract_multiplier}); deviation {qty_diff} "
                f"({deviation_ratio:.4%}) exceeds the configured allowance {allowance}."
            )
            return _reject(
                "QUANTITY_MISMATCH_REJECTION", msg,
                quantity_ok=False, sides_ok=True, attested=False, failures={},
            )

        # --- Check 4: bona fide attestations ----------------------------------------
        if attestation is None:
            msg = (
                f"BONA FIDE ATTESTATION MISSING [{efp_id}]: no EfrpBonaFideAttestation supplied. "
                f"Ownership transfer, non-transitory execution and account independence cannot be "
                f"inferred from prices; they must be attested and recorded before submission "
                f"(ICE Rule 4.06(b)(ii)-(iv); CME Rule 538)."
            )
            return _reject(
                "BONA_FIDE_ATTESTATION_MISSING", msg,
                quantity_ok=True, sides_ok=True, attested=False, failures={},
            )
        if not isinstance(attestation.attested_by, str) or not attestation.attested_by.strip():
            raise ValueError(
                f"{context}: attestation.attested_by must name the attesting person or desk, "
                f"got {attestation.attested_by!r}. An unattributable attestation is not a record."
            )
        failures = attestation.failed_requirements()
        if failures:
            msg = (
                f"RULE 538 VIOLATION [{efp_id}]: unattested bona fide requirement(s) "
                f"{sorted(failures)} (see {sorted(set(failures.values()))}). Attested by "
                f"{attestation.attested_by!r}."
            )
            return _reject(
                "RULE_538_VIOLATION", msg,
                quantity_ok=True, sides_ok=True, attested=False, failures=failures,
            )

        # --- Approved: build the internal pre-submission record ----------------------
        clearing_payload = {
            "rule": "EFRP - CME Rule 538 / ICE Futures U.S. Rule 4.06",
            "efp_id": efp_id,
            "futures_symbol": futures_leg.futures_symbol,
            "futures_side": futures_leg.side,
            "futures_contracts": str(futures_leg.contract_count),
            "futures_price": f"{futures_leg.futures_price_usd:.2f}",
            "physical_symbol": physical_leg.physical_symbol,
            "physical_side": physical_leg.side,
            "physical_qty": f"{physical_leg.physical_quantity:.2f}",
            "spot_price": f"{physical_leg.spot_price_usd:.2f}",
            "observed_basis": f"{observed_basis:.4f}",
            "attested_by": attestation.attested_by,
            "supporting_document_ref": attestation.supporting_document_ref,
            # This engine does not talk to any venue. The status stays PENDING_SUBMISSION
            # until CME Direct / CME ClearPort / ICE Block acknowledges the trade.
            "reporting_status": "PENDING_SUBMISSION",
        }

        notes = (
            f"EFP TRANSACTION APPROVED [{efp_id}]: {futures_leg.contract_count} contracts "
            f"({futures_leg.futures_symbol}, {futures_leg.side}) exchanged for "
            f"{physical_leg.physical_quantity} units ({physical_leg.physical_symbol}, "
            f"{physical_leg.side}). Observed Basis = ${observed_basis:+.4f}/unit "
            f"(Theoretical Fair Basis = ${theoretical_basis:+.4f}/unit at net carry "
            f"{net_carry_rate:.4%}, Mispricing = ${basis_mispricing:+.4f}/unit). Bona fide "
            f"attested by {attestation.attested_by}. NOT YET SUBMITTED - submit via the venue's "
            f"EFRP facility within its reporting deadline."
        )
        logger.info(notes)

        return EfpAuditReport(
            efp_transaction_id=efp_id,
            futures_symbol=futures_leg.futures_symbol,
            physical_symbol=physical_leg.physical_symbol,
            observed_efp_basis_usd=observed_basis,
            theoretical_fair_basis_usd=theoretical_basis,
            basis_mispricing_usd=basis_mispricing,
            is_quantity_equivalent=True,
            status="EFP_APPROVED",
            efrp_clearing_payload=clearing_payload,
            audit_notes=notes,
            required_physical_quantity=required_physical_qty,
            quantity_deviation_ratio=deviation_ratio,
            is_side_structure_valid=True,
            is_bona_fide_attested=True,
            attestation_failures={},
        )
