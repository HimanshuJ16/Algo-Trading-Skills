"""Japan FSA High-Speed Trader (HST) pre-trade compliance audit.

Encodes the registration and conduct regime introduced by the May 2017 amendment
to the Financial Instruments and Exchange Act (FIEA, Act No. 25 of 1948), in
force since 1 April 2018:

  * FIEA art. 2(41)   -- definition of "high-speed trading" (高速取引行為)
  * FIEA art. 2(42)   -- "high-speed trader" (高速取引行為者)
  * FIEA art. 66-50   -- registration requirement
  * FIEA art. 66-53   -- grounds for refusing registration
  * FIEA art. 66-55   -- business management structure obligation
  * FIEA art. 66-58   -- books and records
  * FIEA art. 29-2(1)(vii) -- notification route for financial instruments
                        business operators, who do NOT register as HSTs
  * FIEA art. 38(viii) -- a FIBO may not accept HST orders from an unregistered
                        person (the gatekeeper that actually stops the flow)

There is NO LATENCY THRESHOLD anywhere in this regime. The statutory test is
conjunctive and structural, not numeric (see ``classify_high_speed_trading``).
``latency_ms`` is carried on the spec and echoed on the report as an operational
metric only; it never affects classification. See references/standards.md.

Amounts are Japanese yen (JPY). The per-order value limit is a FIRM-SET hard
limit required in substance by the FSA Guidelines for Supervision of High-Speed
Traders III-2-1-2; the FSA does not publish a numeric figure.
"""
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# Destinations whose receipt of order information can make an act "high-speed
# trading" (Cabinet Office Order on Definitions under FIEA art. 2, art. 26(1),
# read with the FSA notice designating transmission destinations, FSA Notice
# No. 50 of 2017). The original notice designated seven venues: Tokyo Stock
# Exchange, Osaka Exchange, Nagoya Stock Exchange, Fukuoka Stock Exchange,
# Sapporo Stock Exchange, and the PTS operators SBI Japannext and Chi-X Japan
# (which now trades as Cboe Japan). The notice is amended over time -- Osaka
# Digital Exchange was added by the amendment published 26 June 2026.
#
# This is therefore a DATED SNAPSHOT, not a constant of the law. Re-verify it
# against the current FSA notice and override via the engine constructor; an
# order routed to a venue outside the designated set is outside art. 2(41)
# however fast and however co-located it is.
DEFAULT_DESIGNATED_VENUES: Tuple[str, ...] = (
    "TSE",              # 株式会社東京証券取引所
    "OSE",              # 株式会社大阪取引所
    "NSE",              # 株式会社名古屋証券取引所
    "FSE",              # 証券会員制法人福岡証券取引所
    "SSE",              # 証券会員制法人札幌証券取引所
    "SBI_JAPANNEXT",    # SBIジャパンネクスト証券株式会社 (PTS)
    "CBOE_JAPAN",       # Cboe Japan, formerly Chi-X Japan (PTS)
    "CHI_X_JAPAN",      # accepted alias for the same PTS operator
    "ODX",              # 株式会社大阪デジタルエクスチェンジ (PTS), added 2026-06-26
)
DESIGNATED_VENUES_AS_OF = "2026-06-26"

# Trading strategy types used in the 業務方法書 and reported to the authorities.
# FSA Guidelines for Supervision of High-Speed Traders III-3-1-1(2)(i); the same
# four categories are used in the FSA's quarterly "Trends in High-Speed Trading".
HST_STRATEGY_TYPES: Tuple[str, ...] = (
    "MARKET_MAKING",
    "ARBITRAGE",
    "DIRECTIONAL",
    "OTHER",
)

# Registration numbers are issued by the Director-General of the Kanto Local
# Finance Bureau in the form 関東財務局長（高速）第48号. Every registrant on the
# FSA register sits in this single series (numbers 1-90 issued as at the
# 23 July 2026 list, 53 of them live). An ASCII rendering is also accepted.
_KANTO_HST_REGISTRATION_PATTERN = re.compile(
    r"(?:関東財務局長\s*[（(]\s*高速\s*[)）]\s*第\s*(?P<jp>\d+)\s*号)"
    r"|(?:KANTO\W*(?:LFB|LOCAL\W*FINANCE\W*BUREAU)?\W*(?:HST|HIGH\W*SPEED)\W*"
    r"(?:NO\.?|#)?\W*(?P<en>\d+))",
    re.IGNORECASE,
)

# Order of precedence when an order breaches several requirements at once. The
# headline `status` reports the first breach present in this order; `breaches`
# always carries the complete set.
_BREACH_SEVERITY_ORDER: Tuple[str, ...] = (
    "REJECTED_UNREGISTERED_HST",
    "REJECTED_MISSING_REGISTRATION_ID",
    "REJECTED_UNNOTIFIED_FIBO_HST",
    "REJECTED_NO_JAPAN_REPRESENTATIVE",
    "REJECTED_MISSING_KILL_SWITCH",
    "REJECTED_MISSING_HST_ORDER_FLAG",
    "REJECTED_INVALID_TRADING_STRATEGY",
    "REJECTED_PRE_TRADE_LIMIT_EXCEEDED",
)

STATUS_APPROVED = "FSA_HST_APPROVED"
STATUS_NOT_HST = "NOT_HIGH_SPEED_TRADING"


@dataclass
class JapanFsaHstTraderSpec:
    """A single order presented for a Japan FSA high-speed trading audit.

    Attributes:
        trader_id: Internal identifier of the trading entity.
        fsa_hst_reg_id: Registration number as issued, e.g.
            '関東財務局長（高速）第48号'. Required whenever
            ``is_registered_with_fsa`` is True -- a registration that cannot be
            evidenced on the order cannot be relied on in an audit.
        is_registered_with_fsa: Entity holds a live FIEA art. 66-50 registration.
        is_algo_automated: The decision to trade is made automatically by an
            electronic data processing system. First limb of FIEA art. 2(41).
        is_colocated: The order server sits in the facility housing the venue's
            matching engine, or a place adjacent or proximate to it. Cabinet
            Office Order on Definitions art. 26(2)(i).
        latency_ms: Observed order latency. OPERATIONAL METRIC ONLY -- it is
            recorded and echoed on the report but has no bearing on
            classification, because FIEA art. 2(41) contains no latency
            threshold. Must be a finite, non-negative number when supplied.
        order_value_jpy: Order value in JPY, checked against the firm's own
            pre-trade hard limit.
        has_kill_switch_enabled: A kill switch able to cancel anomalous orders
            already sent to the market is armed. FSA Guidelines for Supervision
            of High-Speed Traders III-2-1-2.
        has_resident_compliance_manager: A representative or agent in Japan
            (国内における代表者又は国内における代理人) has been appointed, able
            to respond substantively to regulatory enquiries rather than merely
            relay them. Required of foreign applicants -- FIEA art. 66-53(5)(c)
            and (6)(b), Guidelines III-3-1-3(1)(i)(g).
        venue: Destination venue code. Only transmission to a DESIGNATED venue
            can constitute high-speed trading. Blank means "not supplied": the
            leg is then resolved conservatively as satisfied and a warning is
            recorded, so a missing datum can never suppress classification.
        has_contention_free_transmission: A mechanism prevents this transmission
            from contending with other transmissions -- e.g. a contract for
            exclusive use of a virtual server (Cabinet Office Order on
            Definitions art. 26(2)(ii); Guidelines III-3-1-2). None means "not
            supplied" and is resolved conservatively as satisfied.
        is_hst_order_flagged: The order carries the exchange's high-speed
            trading indicator. TSE Business Regulations art. 14(1)(7).
        trading_strategy_type: Strategy type accompanying the order, one of
            ``HST_STRATEGY_TYPES``. TSE Brokerage Agreement Standards art. 6(5)
            requires the customer to indicate it on each entrustment.
        notified_strategy_types: Strategy types recorded in the entity's
            業務方法書 (Cabinet Office Order on Financial Instruments Business
            art. 328(iv)). When supplied, the order's strategy must be among
            them. None disables the cross-check.
        is_financial_instruments_business_operator: Entity is a registered FIBO
            or registered financial institution. Such firms do NOT register as
            high-speed traders; they notify under FIEA art. 29-2(1)(vii).
        has_filed_fiea_29_2_notification: The art. 29-2(1)(vii) notification
            covering high-speed trading has been filed. Only read when
            ``is_financial_instruments_business_operator`` is True.
        is_foreign_entity: Entity is a foreign corporation or a non-resident
            individual. None means "not supplied" and is resolved conservatively
            as foreign, so the Japan representative requirement is applied.
    """

    trader_id: str
    fsa_hst_reg_id: str
    is_registered_with_fsa: bool
    is_algo_automated: bool
    is_colocated: bool
    latency_ms: float
    order_value_jpy: float
    has_kill_switch_enabled: bool
    has_resident_compliance_manager: bool
    venue: str = ""
    has_contention_free_transmission: Optional[bool] = None
    is_hst_order_flagged: bool = False
    trading_strategy_type: str = ""
    notified_strategy_types: Optional[Tuple[str, ...]] = None
    is_financial_instruments_business_operator: bool = False
    has_filed_fiea_29_2_notification: bool = False
    is_foreign_entity: Optional[bool] = None


@dataclass
class JapanFsaHstReport:
    """Structured, auditable outcome of a Japan FSA HST pre-trade audit.

    Every boolean is evaluated on every audit, so no field on this report is a
    placeholder: a False here always means the corresponding check ran and
    failed. ``breaches`` carries the complete set of failures; ``status`` is the
    most serious of them, ranked by ``_BREACH_SEVERITY_ORDER``.

    ``is_fsa_registered`` means "the applicable registration route is
    satisfied", which depends on ``registration_route``: an art. 66-50 HST
    registration for a plain high-speed trader, or the art. 29-2(1)(vii)
    notification for a financial instruments business operator, which never
    holds an HST registration number at all.
    """

    trader_id: str
    is_hst_classified: bool
    is_fsa_registered: bool
    is_kill_switch_active: bool
    is_pre_trade_limit_valid: bool
    status: str
    audit_notes: str
    registration_route: str = "HST_REGISTRATION"
    venue: str = ""
    latency_ms: float = 0.0
    trading_strategy_type: str = ""
    is_order_flagged_as_hst: bool = False
    is_strategy_type_valid: bool = True
    is_japan_representative_valid: bool = True
    breaches: Tuple[str, ...] = field(default_factory=tuple)
    warnings: Tuple[str, ...] = field(default_factory=tuple)


class JapanFsaHstComplianceEngine:
    """Pre-trade auditor for Japan FSA high-speed trading obligations.

    Classifies an order against the FIEA art. 2(41) definition of high-speed
    trading, then -- only where the definition bites -- audits the registration
    or notification route, the Japan representative requirement for foreign
    entities, the kill switch, the exchange order flag and strategy type, and
    the firm's own pre-trade value limits.

    This is a client-side gate. It does not evidence registration with the FSA,
    does not replace the entity-level obligations that sit outside any single
    order (business method statement, books and records under FIEA art. 66-58,
    business reports under art. 66-59), and cannot stop an order the executing
    broker is separately obliged to refuse under FIEA art. 38(viii).
    """

    def __init__(
        self,
        max_order_value_limit_jpy: float = 100_000_000.0,
        soft_order_value_limit_jpy: Optional[float] = None,
        designated_venues: Optional[Iterable[str]] = None,
    ) -> None:
        """
        Args:
            max_order_value_limit_jpy: Firm-set per-order HARD limit in JPY.
                Breaching it rejects the order. The FSA requires hard and soft
                limits calibrated to the trader's characteristics and scale
                (Guidelines III-2-1-2) but publishes no figure -- the JPY 100M
                default is a placeholder to be replaced with the limit the firm
                has actually calibrated and documented, not a regulatory
                threshold.
            soft_order_value_limit_jpy: Firm-set per-order SOFT limit in JPY.
                Breaching it raises a warning and lets the order through. Must
                not exceed the hard limit.
            designated_venues: Venue codes designated under Cabinet Office Order
                on Definitions art. 26(1). Defaults to
                ``DEFAULT_DESIGNATED_VENUES``, a snapshot as at
                ``DESIGNATED_VENUES_AS_OF``; supply the current list explicitly
                in production.
        """
        self.max_order_value_limit_jpy = self._require_positive_amount(
            max_order_value_limit_jpy, "max_order_value_limit_jpy"
        )
        if soft_order_value_limit_jpy is None:
            self.soft_order_value_limit_jpy: Optional[float] = None
        else:
            self.soft_order_value_limit_jpy = self._require_positive_amount(
                soft_order_value_limit_jpy, "soft_order_value_limit_jpy"
            )
            if self.soft_order_value_limit_jpy > self.max_order_value_limit_jpy:
                raise ValueError(
                    f"soft_order_value_limit_jpy ({self.soft_order_value_limit_jpy:,.0f}) "
                    f"must not exceed max_order_value_limit_jpy "
                    f"({self.max_order_value_limit_jpy:,.0f})."
                )

        venues = DEFAULT_DESIGNATED_VENUES if designated_venues is None else designated_venues
        normalised = tuple(self._normalise_venue(v) for v in venues)
        if not normalised:
            raise ValueError("designated_venues must not be empty.")
        self.designated_venues: Tuple[str, ...] = normalised

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _normalise_venue(venue: str) -> str:
        if not isinstance(venue, str):
            raise TypeError(f"Venue code must be a string, got {type(venue).__name__}.")
        return venue.strip().upper().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _require_positive_amount(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
        amount = float(value)
        if not math.isfinite(amount) or amount <= 0.0:
            raise ValueError(f"{label} must be a finite positive JPY amount, got {value!r}.")
        return amount

    @staticmethod
    def parse_hst_registration_number(fsa_hst_reg_id: str) -> Optional[int]:
        """Extracts the serial number from a Kanto Local Finance Bureau HST id.

        Accepts the issued Japanese form ('関東財務局長（高速）第48号') and an
        ASCII rendering ('Kanto LFB (HST) No. 48'). Returns None when the string
        does not match either form -- the caller decides what to do with that,
        because the FSA publishes the register as text and a format this helper
        does not recognise is not by itself proof of an invalid registration.
        """
        if not isinstance(fsa_hst_reg_id, str):
            raise TypeError(
                f"fsa_hst_reg_id must be a string, got {type(fsa_hst_reg_id).__name__}."
            )
        match = _KANTO_HST_REGISTRATION_PATTERN.search(fsa_hst_reg_id)
        if match is None:
            return None
        digits = match.group("jp") or match.group("en")
        return int(digits)

    def classify_high_speed_trading(self, spec: JapanFsaHstTraderSpec) -> Tuple[bool, Tuple[str, ...]]:
        """Applies the FIEA art. 2(41) definition. Returns (is_hst, warnings).

        The definition is conjunctive and structural. An act is high-speed
        trading when BOTH of the following hold:

          1. the decision to trade is made automatically by an electronic data
             processing system; and
          2. the information needed to place the order is transmitted to a
             DESIGNATED exchange or PTS by a method prescribed for shortening
             the time that transmission normally takes -- which the Cabinet
             Office Order on Definitions art. 26(2) makes a two-part test:
             (i) the order server is in, adjacent to, or proximate to the
             facility housing the venue's matching engine, and (ii) a mechanism
             prevents that transmission contending with other transmissions.

        Speed of execution is not a criterion and no millisecond threshold
        exists. ``spec.latency_ms`` is deliberately not read here.

        Inputs supplied as "unknown" (blank ``venue``, ``None``
        ``has_contention_free_transmission``) resolve to satisfied and raise a
        warning, so a missing field can never make an order look out of scope.
        """
        warnings: list = []

        # This method is public and may be called without a full audit, so it
        # guards its own inputs rather than relying on _validate_spec.
        if not isinstance(spec, JapanFsaHstTraderSpec):
            raise TypeError(
                f"spec must be a JapanFsaHstTraderSpec, got {type(spec).__name__}."
            )
        if not isinstance(spec.venue, str):
            raise TypeError(f"venue must be a string, got {type(spec.venue).__name__}.")

        if spec.venue.strip():
            venue_designated = self._normalise_venue(spec.venue) in self.designated_venues
        else:
            venue_designated = True
            warnings.append(
                "Venue not supplied; the designated-destination leg of FIEA art. 2(41) "
                "was assumed satisfied. Supply the venue to evaluate it."
            )

        if spec.has_contention_free_transmission is None:
            contention_free = True
            warnings.append(
                "has_contention_free_transmission not supplied; Cabinet Office Order on "
                "Definitions art. 26(2)(ii) was assumed satisfied."
            )
        else:
            contention_free = bool(spec.has_contention_free_transmission)

        is_hst = bool(
            spec.is_algo_automated
            and spec.is_colocated
            and venue_designated
            and contention_free
        )
        return is_hst, tuple(warnings)

    # ------------------------------------------------------------------ audit

    def audit_japan_fsa_hst_trader(self, spec: JapanFsaHstTraderSpec) -> JapanFsaHstReport:
        """Audits one order against the Japan FSA high-speed trading regime.

        Every check runs; nothing short-circuits. The returned report lists all
        breaches and reports the most serious one as ``status``.

        Raises:
            TypeError / ValueError: on malformed input. A compliance gate fails
                loudly on bad data rather than silently approving it.
        """
        self._validate_spec(spec)

        is_hst, warnings = self.classify_high_speed_trading(spec)
        warnings = list(warnings)
        breaches: list = []

        is_fibo = bool(spec.is_financial_instruments_business_operator)
        registration_route = "FIEA_29_2_NOTIFICATION" if is_fibo else "HST_REGISTRATION"

        # --- Registration / notification route (FIEA arts. 66-50, 29-2(1)(vii))
        registration_id = ""
        if is_fibo:
            # A registered FIBO does not register as a high-speed trader; it
            # files a notification against its existing registration.
            is_registered = bool(spec.has_filed_fiea_29_2_notification)
            if is_hst and not is_registered:
                breaches.append("REJECTED_UNNOTIFIED_FIBO_HST")
        else:
            is_registered = bool(spec.is_registered_with_fsa)
            registration_id = spec.fsa_hst_reg_id.strip()
            if is_hst and not is_registered:
                breaches.append("REJECTED_UNREGISTERED_HST")
            elif is_hst and not registration_id:
                breaches.append("REJECTED_MISSING_REGISTRATION_ID")
            elif is_registered and registration_id:
                if self.parse_hst_registration_number(registration_id) is None:
                    warnings.append(
                        f"Registration id '{registration_id}' does not match the Kanto Local "
                        f"Finance Bureau format (関東財務局長（高速）第N号). Verify it against "
                        f"the FSA register of high-speed traders."
                    )

        # --- Representative or agent in Japan (FIEA art. 66-53(5)(c), (6)(b))
        # Applies to foreign applicants. Unknown is treated as foreign.
        applies_to_foreign = spec.is_foreign_entity is None or bool(spec.is_foreign_entity)
        is_japan_representative_valid = bool(spec.has_resident_compliance_manager)
        if is_hst and applies_to_foreign and not is_japan_representative_valid:
            breaches.append("REJECTED_NO_JAPAN_REPRESENTATIVE")
        if not applies_to_foreign:
            # Not required of a domestic entity; report it as satisfied rather
            # than as a failed check.
            is_japan_representative_valid = True

        # --- Kill switch (Guidelines III-2-1-2, statutory hook FIEA art. 66-55)
        is_kill_switch_active = bool(spec.has_kill_switch_enabled)
        if is_hst and not is_kill_switch_active:
            breaches.append("REJECTED_MISSING_KILL_SWITCH")

        # --- Exchange order flag (TSE Business Regulations art. 14(1)(7))
        is_order_flagged = bool(spec.is_hst_order_flagged)
        if is_hst and not is_order_flagged:
            breaches.append("REJECTED_MISSING_HST_ORDER_FLAG")

        # --- Strategy type (TSE Brokerage Agreement Standards art. 6(5))
        is_strategy_type_valid, strategy_type = self._audit_strategy_type(spec, is_hst)
        if is_hst and not is_strategy_type_valid:
            breaches.append("REJECTED_INVALID_TRADING_STRATEGY")

        # --- Firm pre-trade value limits (house control, applied to every order)
        is_limit_valid = spec.order_value_jpy <= self.max_order_value_limit_jpy
        if not is_limit_valid:
            breaches.append("REJECTED_PRE_TRADE_LIMIT_EXCEEDED")
        elif (
            self.soft_order_value_limit_jpy is not None
            and spec.order_value_jpy > self.soft_order_value_limit_jpy
        ):
            warnings.append(
                f"Order value JPY {spec.order_value_jpy:,.0f} exceeds the soft pre-trade "
                f"limit (JPY {self.soft_order_value_limit_jpy:,.0f}) but is within the hard "
                f"limit (JPY {self.max_order_value_limit_jpy:,.0f})."
            )

        status = self._rank_status(breaches, is_hst)
        notes = self._compose_notes(spec, status, is_hst, breaches, warnings)

        if breaches:
            logger.critical(notes)
        elif warnings:
            logger.warning(notes)
        else:
            logger.info(notes)

        return JapanFsaHstReport(
            trader_id=spec.trader_id,
            is_hst_classified=is_hst,
            is_fsa_registered=is_registered,
            is_kill_switch_active=is_kill_switch_active,
            is_pre_trade_limit_valid=is_limit_valid,
            status=status,
            audit_notes=notes,
            registration_route=registration_route,
            venue=self._normalise_venue(spec.venue) if spec.venue.strip() else "",
            latency_ms=float(spec.latency_ms),
            trading_strategy_type=strategy_type,
            is_order_flagged_as_hst=is_order_flagged,
            is_strategy_type_valid=is_strategy_type_valid,
            is_japan_representative_valid=is_japan_representative_valid,
            breaches=tuple(breaches),
            warnings=tuple(warnings),
        )

    # --------------------------------------------------------------- internals

    def _audit_strategy_type(
        self, spec: JapanFsaHstTraderSpec, is_hst: bool
    ) -> Tuple[bool, str]:
        raw = spec.trading_strategy_type
        if not isinstance(raw, str):
            raise TypeError(
                f"trading_strategy_type must be a string, got {type(raw).__name__}."
            )
        strategy = raw.strip().upper().replace("-", "_").replace(" ", "_")
        if not is_hst:
            return True, strategy
        if strategy not in HST_STRATEGY_TYPES:
            return False, strategy
        if spec.notified_strategy_types is not None:
            notified = tuple(
                s.strip().upper().replace("-", "_").replace(" ", "_")
                for s in spec.notified_strategy_types
            )
            if strategy not in notified:
                return False, strategy
        return True, strategy

    @staticmethod
    def _rank_status(breaches: Iterable[str], is_hst: bool) -> str:
        present = set(breaches)
        for candidate in _BREACH_SEVERITY_ORDER:
            if candidate in present:
                return candidate
        return STATUS_APPROVED if is_hst else STATUS_NOT_HST

    @staticmethod
    def _compose_notes(
        spec: JapanFsaHstTraderSpec,
        status: str,
        is_hst: bool,
        breaches: Iterable[str],
        warnings: Iterable[str],
    ) -> str:
        breach_list = list(breaches)
        classification = (
            "classified as high-speed trading (FIEA art. 2(41))"
            if is_hst
            else "NOT high-speed trading under FIEA art. 2(41)"
        )
        head = (
            f"JAPAN FSA HST AUDIT [{spec.trader_id}]: {status}. Order {classification}; "
            f"order value JPY {spec.order_value_jpy:,.0f}"
        )
        if is_hst and spec.fsa_hst_reg_id.strip():
            head += f"; registration {spec.fsa_hst_reg_id.strip()}"
        if breach_list:
            head += f". Breaches: {', '.join(breach_list)}"
        warning_list = list(warnings)
        if warning_list:
            head += f". Warnings: {' | '.join(warning_list)}"
        return head + "."

    def _validate_spec(self, spec: JapanFsaHstTraderSpec) -> None:
        if not isinstance(spec, JapanFsaHstTraderSpec):
            raise TypeError(
                f"spec must be a JapanFsaHstTraderSpec, got {type(spec).__name__}."
            )
        if not isinstance(spec.trader_id, str) or not spec.trader_id.strip():
            raise ValueError("trader_id must be a non-empty string.")
        if not isinstance(spec.fsa_hst_reg_id, str):
            raise TypeError(
                f"fsa_hst_reg_id must be a string, got {type(spec.fsa_hst_reg_id).__name__}."
            )
        if not isinstance(spec.venue, str):
            raise TypeError(f"venue must be a string, got {type(spec.venue).__name__}.")
        self._require_positive_amount(spec.order_value_jpy, "order_value_jpy")

        if isinstance(spec.latency_ms, bool) or not isinstance(spec.latency_ms, (int, float)):
            raise TypeError(
                f"latency_ms must be numeric, got {type(spec.latency_ms).__name__}."
            )
        if not math.isfinite(float(spec.latency_ms)) or float(spec.latency_ms) < 0.0:
            raise ValueError(
                f"latency_ms must be a finite non-negative number, got {spec.latency_ms!r}."
            )

        if spec.notified_strategy_types is not None:
            if isinstance(spec.notified_strategy_types, str):
                raise TypeError(
                    "notified_strategy_types must be a sequence of strings, not a bare string."
                )
            for entry in spec.notified_strategy_types:
                if not isinstance(entry, str):
                    raise TypeError(
                        f"notified_strategy_types entries must be strings, "
                        f"got {type(entry).__name__}."
                    )
