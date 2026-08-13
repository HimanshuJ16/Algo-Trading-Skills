"""binary-options-regulatory-and-risk-considerations: pre-trade compliance and risk
gate for binary options (and binary-payout event contracts).

Design premise: binary options rules go stale fast. ESMA's EU-wide prohibition expired
on 1 July 2019 and was replaced by per-member-state measures; ASIC's ban was extended to
2031; the venue list in the joint CFTC/SEC alert has since changed. This module therefore
treats regulatory data as *dated, caller-supplied configuration* subject to a staleness
check, and hardcodes as little as possible. Every deny carries a reason code and a
citation so the decision is auditable.

Jurisdiction coverage here is deliberately partial and is decision *support*, not legal
advice. See references/standards.md for the cited basis of each rule and for the
jurisdictions that are explicitly out of scope.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Mapping, Optional
import enum
import logging
import math
import threading

logger = logging.getLogger(__name__)

# Date on which the hardcoded rule predicates below were last verified against primary
# regulator sources. `ComplianceEngine` warns once past `RULESET_MAX_AGE`, because a
# silently stale binary options ruleset is the failure mode this skill exists to prevent.
RULESET_LAST_VERIFIED = datetime(2026, 8, 13, tzinfo=timezone.utc)
RULESET_MAX_AGE = timedelta(days=180)


class Jurisdiction(enum.Enum):
    """Regulatory regime governing the client relationship.

    `EU_ESMA` is retained for backwards compatibility but no longer denotes a live
    EU-wide measure — see `_rule_eu_esma`.
    """

    US_CFTC = "US_CFTC"
    EU_ESMA = "EU_ESMA"
    UK_FCA = "UK_FCA"
    UNREGULATED = "UNREGULATED"
    AU_ASIC = "AU_ASIC"
    CA_CSA = "CA_CSA"


class ClientCategory(enum.Enum):
    """MiFID II / FCA-style client categorisation."""

    RETAIL = "RETAIL"
    PROFESSIONAL = "PROFESSIONAL"
    ELIGIBLE_COUNTERPARTY = "ELIGIBLE_COUNTERPARTY"

    @classmethod
    def coerce(cls, value: object) -> "ClientCategory":
        """Strictly normalise a client category.

        Accepts the enum or a case-insensitive string so existing callers passing
        `"PROFESSIONAL"` keep working, but rejects anything unrecognised. A permissive
        `==` comparison against a free-text field is a regulatory fail-open: `"Retail"`
        is not equal to `"RETAIL"`, so a retail client silently clears a retail ban.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls[value.strip().upper()]
            except KeyError:
                pass
        raise ValueError(
            f"Unrecognised client category {value!r}; expected one of "
            f"{[c.value for c in cls]}"
        )


class VenueStatus(enum.Enum):
    """Registration status of the execution venue with the relevant regulator."""

    REGISTERED = "REGISTERED"
    UNREGISTERED = "UNREGISTERED"
    UNKNOWN = "UNKNOWN"


class RegulatoryException(Exception):
    """Raised when a trade is prohibited by the applicable regulatory regime."""

    def __init__(self, message: str, reason_code: str = "REG_DENIED", citation: str = ""):
        super().__init__(message)
        self.reason_code = reason_code
        self.citation = citation


class RiskException(Exception):
    """Raised when a trade breaches a firm risk limit."""

    def __init__(self, message: str, reason_code: str = "RISK_DENIED"):
        super().__init__(message)
        self.reason_code = reason_code


def _require_aware(value: datetime, name: str) -> datetime:
    """Rejects naive datetimes.

    Comparing a naive and an aware datetime raises `TypeError` deep inside a rule, and
    silently assuming UTC would produce a wrong answer to a legally material question
    (term to maturity). Fail loudly at the boundary instead.
    """
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime; got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{name} must be timezone-aware (e.g. datetime.now(timezone.utc))"
        )
    return value


def _validate_positive_finite(value: object, name: str) -> float:
    """Rejects NaN, Inf, non-numeric, and non-positive values.

    NaN is called out explicitly: `float('nan') > limit` evaluates False, so a naive
    bounds check silently approves an unbounded notional.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric; got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite; got {value!r}")
    if numeric <= 0:
        raise ValueError(f"{name} must be > 0; got {numeric}")
    return numeric


@dataclass
class TradeContext:
    """A proposed binary option trade.

    Field order preserves the original public API, so positional construction of the
    first seven fields continues to work.

    Args:
        asset_id: Identifier for this trade. Used as the idempotency key in
            `RiskEngine`, so re-submitting the same id replaces rather than
            double-counts the exposure.
        underlying: Underlying reference asset.
        notional: Maximum amount at risk, in a single reporting currency. Must be
            finite and > 0.
        strike: Strike / barrier level. Must be finite and > 0.
        expiry: Expiry timestamp. Must be timezone-aware — term to maturity is a
            legally material term (Canada's MI 91-102 keys its prohibition on it), so
            an ambiguous naive timestamp is rejected rather than assumed to be UTC.
        jurisdiction: Regime governing the client relationship.
        client_type: Client categorisation; accepts `ClientCategory` or a
            case-insensitive string. Normalised into `client_category`.
        venue: Execution venue identifier, for the audit record.
        venue_status: Whether `venue` is registered with the relevant regulator.
            Defaults to `UNKNOWN`, which is denied — an unverified venue is the single
            largest documented source of binary options harm.
        is_natural_person: Whether the client is an individual. Distinct from
            `client_type`: Canada's prohibition applies to individuals *including*
            accredited investors, so a professional categorisation does not exempt them.
            `None` means unknown, and rules that depend on it fail closed.
    """

    asset_id: str
    underlying: str
    notional: float
    strike: float
    expiry: datetime
    jurisdiction: Jurisdiction
    client_type: object
    venue: Optional[str] = None
    venue_status: VenueStatus = VenueStatus.UNKNOWN
    is_natural_person: Optional[bool] = None

    client_category: ClientCategory = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must be a non-empty string")
        if not isinstance(self.jurisdiction, Jurisdiction):
            raise ValueError(
                f"jurisdiction must be a Jurisdiction member; got {self.jurisdiction!r}"
            )
        if not isinstance(self.venue_status, VenueStatus):
            raise ValueError(
                f"venue_status must be a VenueStatus member; got {self.venue_status!r}"
            )
        self.notional = _validate_positive_finite(self.notional, "notional")
        self.strike = _validate_positive_finite(self.strike, "strike")
        _require_aware(self.expiry, "expiry")
        self.client_category = ClientCategory.coerce(self.client_type)

    def term_to_maturity(self, now: datetime) -> timedelta:
        """Time remaining until expiry, as of `now`."""
        return self.expiry - now


# --- Jurisdiction rules -----------------------------------------------------------
#
# Each rule returns None to allow, or a (reason_code, message, citation) tuple to deny.
# Citations are recorded on the exception so a compliance audit can reconstruct which
# rule fired. See references/standards.md for the primary sources.

RuleResult = Optional[tuple]


def _rule_unregulated(ctx: "TradeContext", now: datetime) -> RuleResult:
    return (
        "REG_UNREGULATED_VENUE",
        "Trading on unregulated venues is strictly prohibited.",
        "CFTC/SEC Investor Alert: Binary Options and Fraud",
    )


def _rule_us_cftc(ctx: "TradeContext", now: datetime) -> RuleResult:
    """US: binary options are permitted, but only on a CFTC-registered designated
    contract market (or, for security-based binaries, a registered national securities
    exchange).

    The venue requirement is enforced for every jurisdiction by the venue gate in
    `ComplianceEngine.validate_trade`, so by the time this rule runs the venue is
    already known to be registered and there is nothing further to deny on.
    """
    return None


def _rule_uk_fca(ctx: "TradeContext", now: datetime) -> RuleResult:
    """UK: permanent retail prohibition in force since 2 April 2019, covering all
    binary options including securitised binary options."""
    if ctx.client_category is ClientCategory.RETAIL:
        return (
            "REG_UK_RETAIL_PROHIBITED",
            "Marketing, distribution or sale of binary options to UK retail clients "
            "is prohibited.",
            "FCA PS19/11; FCA Handbook COBS 22.4 (in force 2 April 2019)",
        )
    return None


def _rule_eu_esma(ctx: "TradeContext", now: datetime) -> RuleResult:
    """EU: ESMA's temporary EU-wide prohibition (Decision (EU) 2018/795) expired on
    1 July 2019 and was not renewed. The binding measures are now national, adopted
    per member state under MiFIR Article 42, so an EU-wide verdict cannot be reached
    from this enum member alone.

    Retail is denied because every national measure ESMA cited as its reason for not
    renewing is 'at least as stringent' as the lapsed EU measure. Non-retail is also
    denied, but as *unevaluable*: the caller must resolve the specific member state.
    """
    if ctx.client_category is ClientCategory.RETAIL:
        return (
            "REG_EU_RETAIL_PROHIBITED",
            "Binary options may not be marketed, distributed or sold to retail clients "
            "in the EU under national product intervention measures.",
            "ESMA 'ceases renewal of product intervention measure relating to binary "
            "options' (measure expired 1 July 2019); national measures under MiFIR Art 42",
        )
    return (
        "REG_JURISDICTION_UNRESOLVED",
        "EU_ESMA is not a determinative regime: ESMA's EU-wide prohibition expired on "
        "1 July 2019 and binding measures are now national. Resolve the specific member "
        "state's measure before trading.",
        "ESMA public statement, 1 July 2019; MiFIR Article 42",
    )


def _rule_au_asic(ctx: "TradeContext", now: datetime) -> RuleResult:
    """Australia: issue and distribution of binary options to retail clients banned
    from 3 May 2021, extended until 1 October 2031."""
    if ctx.client_category is ClientCategory.RETAIL:
        return (
            "REG_AU_RETAIL_PROHIBITED",
            "Issue and distribution of binary options to Australian retail clients is "
            "banned.",
            "ASIC Corporations (Product Intervention Order-Binary Options) Instrument "
            "2021/240, in force 3 May 2021, extended to 1 October 2031",
        )
    return None


def _rule_ca_csa(ctx: "TradeContext", now: datetime) -> RuleResult:
    """Canada (CSA jurisdictions other than British Columbia): prohibits binary
    options with a term to maturity of under 30 days with or to an individual.

    This rule does not consult `client_category`: MI 91-102 applies to individuals
    including those who are accredited investors, so a professional categorisation is
    not an exemption. Getting this wrong is a fail-open, which is why an unknown
    `is_natural_person` denies rather than defaults.
    """
    if ctx.is_natural_person is None:
        return (
            "REG_CLIENT_TYPE_UNRESOLVED",
            "Canadian prohibition turns on whether the client is an individual, which "
            "was not supplied (is_natural_person is None).",
            "CSA Multilateral Instrument 91-102",
        )
    if ctx.is_natural_person and ctx.term_to_maturity(now) < timedelta(days=30):
        return (
            "REG_CA_SHORT_TERM_PROHIBITED",
            "Binary options with a term to maturity under 30 days may not be "
            "advertised, offered or sold to an individual, including an accredited "
            "investor.",
            "CSA Multilateral Instrument 91-102 (in force 12 December 2017; adopted in "
            "all CSA jurisdictions other than British Columbia)",
        )
    return None


DEFAULT_JURISDICTION_RULES: Dict[Jurisdiction, Callable[["TradeContext", datetime], RuleResult]] = {
    Jurisdiction.UNREGULATED: _rule_unregulated,
    Jurisdiction.US_CFTC: _rule_us_cftc,
    Jurisdiction.UK_FCA: _rule_uk_fca,
    Jurisdiction.EU_ESMA: _rule_eu_esma,
    Jurisdiction.AU_ASIC: _rule_au_asic,
    Jurisdiction.CA_CSA: _rule_ca_csa,
}


class ComplianceEngine:
    """Evaluates a trade against the applicable jurisdiction rule.

    Default-deny: a jurisdiction with no rule, or a venue whose registration status is
    not established, is rejected rather than approved.
    """

    def __init__(
        self,
        jurisdiction_rules: Optional[
            Mapping[Jurisdiction, Callable[["TradeContext", datetime], RuleResult]]
        ] = None,
        *,
        ruleset_last_verified: datetime = RULESET_LAST_VERIFIED,
        ruleset_max_age: timedelta = RULESET_MAX_AGE,
    ) -> None:
        """
        Args:
            jurisdiction_rules: Overrides the built-in rule table. Supply your own when
                you have counsel-reviewed, per-member-state rules — the built-ins are
                deliberately partial.
            ruleset_last_verified: When these rules were last checked against primary
                regulator sources.
            ruleset_max_age: Age past which `validate_trade` logs a staleness warning.
        """
        self.jurisdiction_rules = dict(
            DEFAULT_JURISDICTION_RULES if jurisdiction_rules is None else jurisdiction_rules
        )
        self.ruleset_last_verified = ruleset_last_verified
        self.ruleset_max_age = ruleset_max_age
        # Retained for backwards compatibility with callers that introspected this.
        self.banned_jurisdictions_for_retail = [
            Jurisdiction.EU_ESMA,
            Jurisdiction.UK_FCA,
            Jurisdiction.AU_ASIC,
        ]

    def ruleset_age(self, now: datetime) -> timedelta:
        return now - self.ruleset_last_verified

    def validate_trade(self, context: TradeContext, now: Optional[datetime] = None) -> bool:
        """Returns True if permitted; raises `RegulatoryException` otherwise.

        Args:
            now: Injected evaluation time, for deterministic testing. Defaults to the
                current UTC time.
        """
        now = _require_aware(now or datetime.now(timezone.utc), "now")

        # Re-derive from the current field value: TradeContext is mutable, so a
        # category cached at construction could be stale by evaluation time.
        context.client_category = ClientCategory.coerce(context.client_type)

        if self.ruleset_age(now) > self.ruleset_max_age:
            logger.warning(
                f"Binary options ruleset last verified {self.ruleset_last_verified.date()} "
                f"({self.ruleset_age(now).days} days ago); re-verify against primary "
                "regulator sources before relying on these decisions."
            )

        if context.expiry <= now:
            raise RegulatoryException(
                f"Option already expired at {context.expiry.isoformat()}.",
                reason_code="REG_ALREADY_EXPIRED",
            )

        # Venue gate, applied in every jurisdiction rather than only in the US. An
        # unverified venue is the largest documented source of binary options harm, and
        # "we did not record the venue" must not read as "the venue was fine".
        if context.venue_status is VenueStatus.UNREGISTERED:
            raise RegulatoryException(
                f"Venue {context.venue!r} is not registered with the relevant regulator.",
                reason_code="REG_VENUE_NOT_REGISTERED",
                citation="CFTC 'Avoid Unregistered Binary Options Trading Platforms'; "
                "CFTC/SEC Investor Alert: Binary Options and Fraud",
            )
        if context.venue_status is VenueStatus.UNKNOWN:
            raise RegulatoryException(
                f"Venue {context.venue!r} registration status is unknown; establish it "
                "against the regulator's current register before trading.",
                reason_code="REG_VENUE_STATUS_UNKNOWN",
                citation="CFTC 'Avoid Unregistered Binary Options Trading Platforms'",
            )

        rule = self.jurisdiction_rules.get(context.jurisdiction)
        if rule is None:
            raise RegulatoryException(
                f"No rule configured for jurisdiction {context.jurisdiction.value}; "
                "denying by default.",
                reason_code="REG_NO_RULE_CONFIGURED",
            )

        outcome = rule(context, now)
        if outcome is not None:
            reason_code, message, citation = outcome
            raise RegulatoryException(message, reason_code=reason_code, citation=citation)

        return True


class RiskEngine:
    """Per-trade and aggregate exposure limits for a binary options book.

    Exposure is measured as notional (maximum amount at risk), which for a binary
    payoff is the correct worst case: the payoff is discontinuous, so there is no
    partial-loss regime to net against.

    Thread-safe: `check_limits` and `register_trade` are guarded by a lock, since a
    pre-trade gate is typically called from multiple order-handling threads and an
    unsynchronised read-then-register lets two trades each pass a limit they jointly
    breach.
    """

    def __init__(
        self,
        max_notional_per_trade: float = 1_000_000.0,
        max_pin_risk_exposure: float = 5_000_000.0,
        *,
        max_aggregate_notional: Optional[float] = None,
        pin_window: timedelta = timedelta(hours=24),
    ) -> None:
        """
        Args:
            max_notional_per_trade: Cap on a single trade's notional.
            max_pin_risk_exposure: Cap on *aggregate registered notional expiring within
                `pin_window`*. This is a concentration control on near-expiry
                discontinuous payoffs. It is deliberately NOT a Greeks-based measure —
                it uses no spot price and no volatility model. For delta/gamma
                management near the strike see `options-pin-risk-management-at-expiry`.
            max_aggregate_notional: Cap on total registered notional across the book.
                `None` disables the check.
            pin_window: Time-to-expiry threshold defining "near expiry". A firm policy
                parameter, not a regulatory threshold.
        """
        self.max_notional_per_trade = _validate_positive_finite(
            max_notional_per_trade, "max_notional_per_trade"
        )
        self.max_pin_risk_exposure = _validate_positive_finite(
            max_pin_risk_exposure, "max_pin_risk_exposure"
        )
        if max_aggregate_notional is not None:
            max_aggregate_notional = _validate_positive_finite(
                max_aggregate_notional, "max_aggregate_notional"
            )
        self.max_aggregate_notional = max_aggregate_notional
        if not isinstance(pin_window, timedelta) or pin_window <= timedelta(0):
            raise ValueError("pin_window must be a positive timedelta")
        self.pin_window = pin_window

        self._lock = threading.RLock()
        self._book: Dict[str, TradeContext] = {}

    def _exposures(self, now: datetime, excluding: Optional[str] = None) -> tuple:
        total = 0.0
        near_expiry = 0.0
        for trade_id, trade in self._book.items():
            if trade_id == excluding or trade.expiry <= now:
                continue
            total += trade.notional
            if trade.term_to_maturity(now) <= self.pin_window:
                near_expiry += trade.notional
        return total, near_expiry

    def current_exposure(self, now: Optional[datetime] = None) -> Dict[str, float]:
        """Aggregate and near-expiry notional currently registered, excluding trades
        that have already expired."""
        now = _require_aware(now or datetime.now(timezone.utc), "now")
        with self._lock:
            total, near_expiry = self._exposures(now)
        return {"aggregate_notional": total, "near_expiry_notional": near_expiry}

    def check_limits(self, context: TradeContext, now: Optional[datetime] = None) -> bool:
        """Returns True if the trade fits within all limits; raises `RiskException`
        otherwise. Does not mutate the book — call `register_trade` on approval."""
        now = _require_aware(now or datetime.now(timezone.utc), "now")

        if context.notional > self.max_notional_per_trade:
            raise RiskException(
                f"Notional {context.notional} exceeds maximum per trade limit of "
                f"{self.max_notional_per_trade}.",
                reason_code="RISK_PER_TRADE_NOTIONAL",
            )

        with self._lock:
            # Exclude any prior registration under the same id: a resubmission of the
            # same trade replaces it rather than double-counting.
            total, near_expiry = self._exposures(now, excluding=context.asset_id)

        if self.max_aggregate_notional is not None:
            projected = total + context.notional
            if projected > self.max_aggregate_notional:
                raise RiskException(
                    f"Aggregate notional {projected} would exceed limit of "
                    f"{self.max_aggregate_notional}.",
                    reason_code="RISK_AGGREGATE_NOTIONAL",
                )

        if context.term_to_maturity(now) <= self.pin_window:
            projected_near = near_expiry + context.notional
            if projected_near > self.max_pin_risk_exposure:
                raise RiskException(
                    f"Near-expiry notional {projected_near} (expiring within "
                    f"{self.pin_window}) would exceed pin risk limit of "
                    f"{self.max_pin_risk_exposure}.",
                    reason_code="RISK_PIN_EXPOSURE",
                )

        return True

    def register_trade(self, context: TradeContext) -> None:
        """Records an approved trade against the book. Idempotent by `asset_id`."""
        with self._lock:
            self._book[context.asset_id] = context

    def release_trade(self, asset_id: str) -> bool:
        """Removes a trade from the book. Returns True if it was present."""
        with self._lock:
            return self._book.pop(asset_id, None) is not None


class BinaryOptionsManager:
    """Manages order flow integrating compliance and risk frameworks."""

    def __init__(self, compliance_engine: ComplianceEngine, risk_engine: RiskEngine) -> None:
        self.compliance_engine = compliance_engine
        self.risk_engine = risk_engine

    def process_order(
        self,
        context: TradeContext,
        now: Optional[datetime] = None,
        register: bool = True,
    ) -> Dict[str, str]:
        """Runs the compliance gate, then the risk gate.

        Compliance is evaluated first and deliberately: a prohibited trade must be
        reported as a regulatory rejection even if it would also have breached a risk
        limit, because the two have different escalation paths.

        Returns a decision record with `status`, `message`, `reason_code`, `citation`,
        and `asset_id`. Every decision is logged for the compliance audit trail.

        Args:
            register: Whether to book an approved trade's exposure. Pass False to
                evaluate an order without affecting aggregate limits.
        """
        now = _require_aware(now or datetime.now(timezone.utc), "now")
        try:
            self.compliance_engine.validate_trade(context, now=now)
            self.risk_engine.check_limits(context, now=now)
        except RegulatoryException as e:
            decision = {
                "status": "REJECTED_REGULATORY",
                "message": str(e),
                "reason_code": e.reason_code,
                "citation": e.citation,
                "asset_id": context.asset_id,
            }
            logger.warning(
                f"Order {context.asset_id} rejected on regulatory grounds "
                f"[{e.reason_code}] in {context.jurisdiction.value}: {e}"
            )
            return decision
        except RiskException as e:
            decision = {
                "status": "REJECTED_RISK",
                "message": str(e),
                "reason_code": e.reason_code,
                "citation": "",
                "asset_id": context.asset_id,
            }
            logger.warning(
                f"Order {context.asset_id} rejected on risk grounds [{e.reason_code}]: {e}"
            )
            return decision

        if register:
            self.risk_engine.register_trade(context)

        logger.info(
            f"Order {context.asset_id} approved in {context.jurisdiction.value} "
            f"for {context.client_category.value} client on venue {context.venue!r} "
            f"(notional {context.notional})"
        )
        return {
            "status": "APPROVED",
            "message": "Order passed regulatory and risk checks.",
            "reason_code": "APPROVED",
            "citation": "",
            "asset_id": context.asset_id,
        }
