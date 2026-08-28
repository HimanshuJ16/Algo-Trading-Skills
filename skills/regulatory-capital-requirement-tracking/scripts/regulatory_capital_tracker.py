"""Regulatory capital adequacy tracking for broker-dealers and investment firms.

This module answers one question: **given a firm's balance sheet and the set of
capital requirements that apply to it, is the firm above its regulatory floor,
inside its early-warning band, or deficient?**

It models an *absolute-currency-amount* capital test:

    net capital (a currency amount)  vs.  required capital (a currency amount)

That is the shape of the SEC net capital rule (17 CFR 240.15c3-1) and of the
FCA's own funds requirement for MIFIDPRU investment firms. It is **not** the
shape of the Basel III bank capital ratios, which are ratios to risk-weighted
assets -- see "Frameworks this does and does not model" below.

Two modelling points carry most of the correctness weight, and both were wrong
in version 1 of this skill.

1. Multiple requirements combine by **greater-of, not sum.**
   17 CFR 240.15c3-1(a): "Every broker or dealer must at all times have and
   maintain net capital no less than **the greater of** the highest minimum
   requirement applicable to its ratio requirement under paragraph (a)(1) of
   this section, or to any of its activities under paragraph (a)(2) of this
   section". MIFIDPRU 4.3.2R is built the same way: the own funds requirement
   of a non-SNI firm "is **the highest of**" its permanent minimum capital
   requirement, its fixed overheads requirement, or its K-factor requirement.
   Summing those components overstates the requirement -- for a firm with a
   GBP 750,000 PMR and a GBP 420,000 FOR it reports a GBP 1,170,000 floor
   against a real one of GBP 750,000, and manufactures deficits that are not
   deficits. ``AGGREGATION_GREATER_OF`` is therefore the default.
   ``AGGREGATION_SUM`` exists for genuinely additive regimes -- a Basel-style
   minimum with a capital conservation buffer stacked on top of it -- and must
   be asked for explicitly.

2. Risk-based haircuts are a **deduction from capital, not an addition to the
   requirement.** Under 15c3-1(c)(2), net capital is "the net worth of a broker
   or dealer, adjusted by" -- among other things -- (c)(2)(iv) deducting "fixed
   assets and assets which cannot be readily converted into cash" and (c)(2)(vi)
   "Deducting the percentages specified in paragraphs (c)(2)(vi)(A) through (M)
   of this section (or the deductions prescribed for securities positions set
   forth in Appendix A) of the market value of all securities, money market
   instruments or options". Haircuts therefore belong on the capital side of the
   comparison. Putting them on the requirement side changes the headroom
   arithmetic and, once combined with greater-of aggregation, changes the
   answer.

Frameworks this does and does not model
---------------------------------------
* **SEC Rule 15c3-1 (US broker-dealers)** -- modelled directly. Compute the
  applicable dollar minimum from (a)(2) and the applicable ratio requirement
  from (a)(1) yourself, pass both as components, and let greater-of pick.
  This module does not compute aggregate indebtedness or aggregate debit items
  for you.
* **FCA MIFIDPRU (UK investment firms)** -- modelled directly. Pass PMR, FOR
  and KFR as components (MIFIDPRU 4.3.2R).
* **Basel III (banks)** -- **not modelled.** Basel III ¶50 requires that
  "Common Equity Tier 1 must be at least 4.5% of risk-weighted assets at all
  times", Tier 1 "at least 6.0%" and Total Capital "at least 8.0%", with a
  further "capital conservation buffer of 2.5%, comprised of Common Equity
  Tier 1" (¶129). Those are three simultaneous tests against three different
  definitions of eligible capital, expressed as ratios to RWA. A single
  net-capital scalar cannot represent them. You can test **one** tier at a time
  by passing ``ratio x RWA`` as a component and that tier's own funds as the
  capital figure -- but that is your translation, not this module's, and the
  result speaks only to the tier you translated.

Scope and honesty boundary
--------------------------
This engine performs no accounting. It does not classify assets as allowable or
non-allowable, does not look up haircut percentages, does not compute fixed
overheads, and reaches no external system. It consumes figures your books and
records produce and reports the comparison. Garbage in is reported faithfully as
garbage out, which is why every input is validated rather than coerced.

Notification obligations are jurisdiction-specific and are surfaced only for
jurisdictions this module has been given a mapping for (see ``_NOTICE_RULES``).
A ``None`` in ``CapitalStatusReport.regulatory_notice`` means "this module has
no mapping for your regime", never "no notice is due".

See ``references/standards.md`` for the source behind every threshold.
"""

import logging
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)
# Library convention: emit nothing unless the host application configures
# logging. Every result is present on the returned report, so a caller with no
# handlers configured still gets the full outcome programmatically.
logger.addHandler(logging.NullHandler())

__all__ = [
    "CapitalInputError",
    "CapitalComponents",
    "CapitalRequirementSpec",
    "CapitalStatusReport",
    "RegulatoryCapitalTrackerEngine",
    "AGGREGATION_GREATER_OF",
    "AGGREGATION_SUM",
    "VALID_AGGREGATIONS",
    "STATUS_COMPLIANT",
    "STATUS_WARNING_BUFFER_BREACHED",
    "STATUS_CAPITAL_DEFICIT",
    "SEC_15C3_1_EARLY_WARNING_PCT",
]


class CapitalInputError(ValueError):
    """Raised when balance-sheet or requirement inputs cannot be evaluated.

    Always treat this as a **failed** capital check. An unevaluable balance
    sheet is not an adequate one.
    """


# --------------------------------------------------------------------------
# Aggregation of requirement components
# --------------------------------------------------------------------------
# GREATER_OF is the default because both frameworks this module models directly
# are greater-of regimes: 15c3-1(a) ("the greater of") and MIFIDPRU 4.3.2R
# ("the highest of"). SUM is opt-in for stacked regimes such as a Basel minimum
# plus a capital conservation buffer.
AGGREGATION_GREATER_OF = "GREATER_OF"
AGGREGATION_SUM = "SUM"

VALID_AGGREGATIONS = frozenset({AGGREGATION_GREATER_OF, AGGREGATION_SUM})

# --------------------------------------------------------------------------
# Statuses
# --------------------------------------------------------------------------
STATUS_COMPLIANT = "COMPLIANT"
STATUS_WARNING_BUFFER_BREACHED = "WARNING_BUFFER_BREACHED"
STATUS_CAPITAL_DEFICIT = "CAPITAL_DEFICIT"

# 17 CFR 240.17a-11(b)(3): notice is required within 24 hours when a firm's
# "total net capital is less than 120 percent of the broker's or dealer's
# required minimum net capital". The 120% figure in this module is that rule,
# not a house convention.
SEC_15C3_1_EARLY_WARNING_PCT = 1.20

# Jurisdiction -> notification text. Deliberately narrow: only regimes whose
# notification rule has been verified against primary source appear here. An
# absent jurisdiction yields None, which means "unmapped", not "nothing due".
_NOTICE_RULES: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "SEC_15C3_1": MappingProxyType({
        STATUS_CAPITAL_DEFICIT: (
            "17 CFR 240.17a-11(a)(1): net capital below the 15c3-1 minimum "
            "requires notice the SAME DAY. Operating a securities business "
            "while deficient is a continuing violation of 15c3-1(a), which "
            "requires the minimum be maintained 'at all times'."
        ),
        STATUS_WARNING_BUFFER_BREACHED: (
            "17 CFR 240.17a-11(b)(3): net capital below 120% of the required "
            "minimum requires notice WITHIN 24 HOURS."
        ),
    }),
})


def _check_finite(value: float, label: str) -> float:
    """Reject NaN and infinity before they reach a threshold comparison.

    NaN is the dangerous one: every comparison against it is False, so a NaN
    net capital would classify as a deficit purely by accident, and a NaN
    requirement would classify as compliant. Neither is a real answer.
    """
    # Strict about type on purpose. float("1e6") succeeds and float("1,000,000")
    # does not, so accepting strings makes whether a CSV column parses depend on
    # its formatting. bool is excluded because True would otherwise be a
    # perfectly valid currency amount of 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapitalInputError(f"{label} must be an int or float, got {value!r} ({type(value).__name__})")
    as_float = float(value)
    if not math.isfinite(as_float):
        raise CapitalInputError(f"{label} must be a finite number, got {as_float!r}")
    return as_float


def _check_non_negative(value: float, label: str) -> float:
    """Reject negative magnitudes for quantities that cannot be negative.

    A negative deduction or a negative liability adds phantom capital to the
    result. That is a sign error in the caller's books, not an input this
    module should silently honour.
    """
    as_float = _check_finite(value, label)
    if as_float < 0.0:
        raise CapitalInputError(f"{label} must be >= 0, got {as_float!r}")
    return as_float


@dataclass(frozen=True)
class CapitalComponents:
    """Balance-sheet inputs to the net capital computation.

    Field names follow 17 CFR 240.15c3-1(c)(2), where net capital is "the net
    worth of a broker or dealer, adjusted by" the items below.

    Attributes:
        total_assets: **Total** assets, not just the liquid ones. Non-allowable
            assets are deducted separately below; passing an already-filtered
            figure here double-deducts them and understates net capital.
        total_liabilities: Total liabilities **excluding** any liability
            subordinated under a satisfactory subordination agreement. Under
            15c3-1(c)(2)(ii) such liabilities are excluded from the liability
            side; pass them in ``qualifying_subordinated_debt`` instead. Do not
            pass the same amount in both fields.
        non_allowable_assets: 15c3-1(c)(2)(iv) -- "fixed assets and assets which
            cannot be readily converted into cash". Must not exceed
            ``total_assets``.
        securities_haircuts: 15c3-1(c)(2)(vi) -- the percentage deductions
            applied to the market value of securities positions. A deduction
            from capital, never an addition to the requirement.
        qualifying_subordinated_debt: Subordinated liabilities that satisfy a
            satisfactory subordination agreement (15c3-1(c)(2)(ii),
            Appendix D). Subordinated debt that does **not** qualify is an
            ordinary liability and belongs in ``total_liabilities``.

    All amounts are in a single currency; this module does no FX conversion.
    Amounts are used exactly as given -- round to your reporting precision
    before constructing, because binary floats will not do it for you.
    """

    total_assets: float
    total_liabilities: float
    non_allowable_assets: float = 0.0
    securities_haircuts: float = 0.0
    qualifying_subordinated_debt: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_assets", _check_non_negative(self.total_assets, "total_assets"))
        object.__setattr__(
            self, "total_liabilities", _check_non_negative(self.total_liabilities, "total_liabilities")
        )
        object.__setattr__(
            self, "non_allowable_assets", _check_non_negative(self.non_allowable_assets, "non_allowable_assets")
        )
        object.__setattr__(
            self, "securities_haircuts", _check_non_negative(self.securities_haircuts, "securities_haircuts")
        )
        object.__setattr__(
            self,
            "qualifying_subordinated_debt",
            _check_non_negative(self.qualifying_subordinated_debt, "qualifying_subordinated_debt"),
        )
        # Non-allowable assets are a subset of assets. A figure larger than the
        # balance sheet is a unit or scope error (a haircut passed here, a
        # consolidated figure against an entity-level balance sheet), and it
        # would silently drive net capital deeply negative.
        if self.non_allowable_assets > self.total_assets:
            raise CapitalInputError(
                f"non_allowable_assets ({self.non_allowable_assets!r}) exceeds "
                f"total_assets ({self.total_assets!r}); non-allowable assets are "
                f"a subset of total assets"
            )


@dataclass(frozen=True)
class CapitalRequirementSpec:
    """The capital requirement(s) applicable to this firm.

    Attributes:
        jurisdiction: Free-text label for the regime, e.g. ``"SEC_15C3_1"`` or
            ``"FCA_MIFIDPRU"``. Used for reporting and to look up notification
            obligations; an unmapped value simply yields no notice text.
        requirement_components: Named requirement amounts, each a positive
            currency amount, e.g. ``{"MINIMUM_DOLLAR_15c3-1(a)(2)(i)": 250000.0,
            "RATIO_REQ_15c3-1(a)(1)(i)": 310000.0}`` or
            ``{"PMR": 750000.0, "FOR": 420000.0, "KFR": 310000.0}``. At least
            one component is required -- there is no regime with no floor.
        aggregation: ``AGGREGATION_GREATER_OF`` (default, correct for 15c3-1(a)
            and MIFIDPRU 4.3.2R) or ``AGGREGATION_SUM`` for genuinely stacked
            regimes such as a Basel minimum plus a conservation buffer.
        early_warning_pct: Multiple of the requirement below which the firm is
            in its warning band. Defaults to the 1.20 of 17 CFR 240.17a-11(b)(3).
            That figure is a **US** rule. Applied to a MIFIDPRU firm or any other
            regime it is a prudent house buffer and nothing more -- set it to
            whatever your own regime and wind-down planning actually require.
            Must be >= 1.0: a value below 1.0 would place the warning line under
            the regulatory floor, leaving an empty warning band and a tool that
            silently never warns.
    """

    jurisdiction: str
    requirement_components: Mapping[str, float]
    aggregation: str = AGGREGATION_GREATER_OF
    early_warning_pct: float = SEC_15C3_1_EARLY_WARNING_PCT

    def __post_init__(self) -> None:
        if not isinstance(self.jurisdiction, str) or not self.jurisdiction.strip():
            raise CapitalInputError("jurisdiction must be a non-empty string")

        if not isinstance(self.requirement_components, Mapping):
            raise CapitalInputError("requirement_components must be a mapping of name -> amount")
        if not self.requirement_components:
            raise CapitalInputError(
                "requirement_components must contain at least one component; "
                "a firm with no capital floor is not a regime this module models"
            )

        validated: Dict[str, float] = {}
        for name, amount in self.requirement_components.items():
            if not isinstance(name, str) or not name.strip():
                raise CapitalInputError(f"requirement component name must be a non-empty string, got {name!r}")
            as_float = _check_finite(amount, f"requirement component {name!r}")
            # Zero and negative components are rejected rather than ignored: a
            # component that evaluated to zero usually means the caller failed
            # to compute it, and under greater-of it would vanish silently.
            if as_float <= 0.0:
                raise CapitalInputError(
                    f"requirement component {name!r} must be > 0, got {as_float!r}; "
                    f"omit the component entirely if it does not apply"
                )
            validated[name] = as_float
        object.__setattr__(self, "requirement_components", MappingProxyType(validated))

        if self.aggregation not in VALID_AGGREGATIONS:
            raise CapitalInputError(
                f"aggregation must be one of {sorted(VALID_AGGREGATIONS)}, got {self.aggregation!r}"
            )

        pct = _check_finite(self.early_warning_pct, "early_warning_pct")
        if pct < 1.0:
            raise CapitalInputError(
                f"early_warning_pct must be >= 1.0, got {pct!r}; a warning threshold "
                f"below the regulatory floor would never fire before a breach"
            )
        object.__setattr__(self, "early_warning_pct", pct)


@dataclass(frozen=True)
class CapitalStatusReport:
    """Outcome of one capital adequacy evaluation.

    Numeric fields are **unrounded**. Rounding before a threshold comparison can
    turn a deficit of a few cents into a pass, so the comparison is made on
    exact values and only ``audit_notes`` is formatted for human reading.

    Attributes:
        binding_component: Name of the requirement component that set
            ``total_capital_required``. Under greater-of this is the component
            that binds -- the thing to manage. Under sum it is
            ``"SUM_OF_ALL_COMPONENTS"``.
        regulatory_notice: Notification obligation triggered by ``status`` in
            this jurisdiction, or ``None`` if this module has no mapping for
            the jurisdiction. ``None`` never means "no notice is due".
    """

    jurisdiction: str
    net_capital_available: float
    total_capital_required: float
    binding_component: str
    capital_headroom: float
    capital_ratio: float
    early_warning_threshold: float
    is_compliant: bool
    is_warning: bool
    status: str
    regulatory_notice: Optional[str]
    audit_notes: str


class RegulatoryCapitalTrackerEngine:
    """Evaluates net capital against an applicable regulatory capital floor.

    The engine holds one :class:`CapitalRequirementSpec` and evaluates balance
    sheets against it. The spec is **required**: version 1 of this skill
    defaulted to a USD 250,000 SEC minimum, which is the 15c3-1(a)(2)(i) figure
    for a broker-dealer carrying customer accounts and simply wrong for an
    introducing broker (USD 50,000 under (a)(2)(iv)), a dealer (USD 100,000
    under (a)(2)(iii)), or a UK firm dealing on own account (GBP 750,000 PMR
    under MIFIDPRU 4.4). A silent wrong floor is worse than no floor.
    """

    def __init__(self, spec: CapitalRequirementSpec):
        if not isinstance(spec, CapitalRequirementSpec):
            raise CapitalInputError(
                "spec must be a CapitalRequirementSpec; there is no safe default "
                "capital requirement, it depends on the firm's permissions"
            )
        self.spec = spec

    def calculate_net_capital(self, components: CapitalComponents) -> float:
        """Net capital per 17 CFR 240.15c3-1(c)(2).

        ``(total_assets - total_liabilities)`` is net worth; qualifying
        subordinated debt is added back because (c)(2)(ii) excludes it from the
        liability side; non-allowable assets ((c)(2)(iv)) and securities
        haircuts ((c)(2)(vi)) are deducted.

        The result may legitimately be negative -- a firm can be net capital
        deficient to the point of insolvency, and the caller needs to see the
        magnitude of the hole, not a floor of zero.
        """
        if not isinstance(components, CapitalComponents):
            raise CapitalInputError("components must be a CapitalComponents instance")
        net_worth = components.total_assets - components.total_liabilities
        return (
            net_worth
            + components.qualifying_subordinated_debt
            - components.non_allowable_assets
            - components.securities_haircuts
        )

    def calculate_required_capital(self) -> Tuple[float, str]:
        """Aggregate the requirement components into a single floor.

        Returns:
            ``(required_amount, binding_component_name)``. Under greater-of, ties
            resolve to the component whose name sorts first, so the result is
            deterministic regardless of mapping insertion order.
        """
        components = self.spec.requirement_components
        if self.spec.aggregation == AGGREGATION_SUM:
            total = float(sum(components.values()))
            # Individually-finite components can still overflow to infinity when
            # summed. An infinite floor would classify every firm as deficient
            # for a reason that has nothing to do with its balance sheet.
            if not math.isfinite(total):
                raise CapitalInputError(
                    f"summed requirement components overflowed to {total!r}; "
                    f"check the magnitude and units of {sorted(components)}"
                )
            return total, "SUM_OF_ALL_COMPONENTS"
        # GREATER_OF: 15c3-1(a) "the greater of", MIFIDPRU 4.3.2R "the highest of".
        binding = max(sorted(components), key=lambda name: components[name])
        return components[binding], binding

    def evaluate_capital_adequacy(self, components: CapitalComponents) -> CapitalStatusReport:
        """Classify the firm as compliant, in its warning band, or deficient.

        Boundaries are inclusive at the floor and exclusive at the warning line,
        matching the regulatory text: 15c3-1(a) requires net capital "no less
        than" the requirement, so exactly-at-the-floor is not a breach; and
        17a-11(b)(3) triggers when net capital "is less than 120 percent" of the
        minimum, so exactly-at-120% is not a warning.
        """
        net_capital = self.calculate_net_capital(components)
        total_required, binding_component = self.calculate_required_capital()

        # Guaranteed > 0 by CapitalRequirementSpec validation, so no sentinel
        # ratio and no division guard is needed here.
        capital_ratio = net_capital / total_required
        headroom = net_capital - total_required
        warning_threshold = total_required * self.spec.early_warning_pct

        is_compliant = net_capital >= total_required
        is_warning = is_compliant and net_capital < warning_threshold

        if not is_compliant:
            status = STATUS_CAPITAL_DEFICIT
        elif is_warning:
            status = STATUS_WARNING_BUFFER_BREACHED
        else:
            status = STATUS_COMPLIANT

        regulatory_notice = _NOTICE_RULES.get(self.spec.jurisdiction, {}).get(status)

        notes = (
            f"REGULATORY CAPITAL [{status}] ({self.spec.jurisdiction}): "
            f"Net Capital = {net_capital:,.2f}, Required = {total_required:,.2f} "
            f"(binding: {binding_component}, {self.spec.aggregation}), "
            f"Headroom = {headroom:,.2f}, Ratio = {capital_ratio:.2%}, "
            f"Early-warning line = {warning_threshold:,.2f}."
        )
        if regulatory_notice:
            notes = f"{notes} {regulatory_notice}"

        if status == STATUS_CAPITAL_DEFICIT:
            logger.critical(notes)
        elif status == STATUS_WARNING_BUFFER_BREACHED:
            logger.warning(notes)
        else:
            logger.info(notes)

        return CapitalStatusReport(
            jurisdiction=self.spec.jurisdiction,
            net_capital_available=net_capital,
            total_capital_required=total_required,
            binding_component=binding_component,
            capital_headroom=headroom,
            capital_ratio=capital_ratio,
            early_warning_threshold=warning_threshold,
            is_compliant=is_compliant,
            is_warning=is_warning,
            status=status,
            regulatory_notice=regulatory_notice,
            audit_notes=notes,
        )
