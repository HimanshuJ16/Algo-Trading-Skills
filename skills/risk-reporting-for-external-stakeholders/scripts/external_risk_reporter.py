"""Aggregate risk disclosure for parties outside the firm's information barrier.

This module builds one artefact: an **aggregate** risk report addressed to a
named external stakeholder, carrying a digest that lets the recipient detect
alteration in transit, and a redaction check that fails the report rather than
shipping a position-level identifier by accident.

What it is not
--------------
**It does not produce a statutory filing, and no output of this module should be
submitted as one.** That boundary is the single most important thing to
understand here, because version 1 of this skill blurred it. Form PF and AIFMD
Annex IV are not "aggregate risk reports with the positions removed" -- they
require exactly the position-level and counterparty-level detail this module
deliberately withholds:

* Form PF Question 35 (section 2b, qualifying hedge funds): "For each open
  position of the reporting fund that represents 5% or more of the reporting
  fund's net asset value, provide the information requested below", monthly,
  with sub-asset class.
* Form PF Questions 22 and 23: "Identify the five counterparties to which the
  reporting fund has the greatest mark-to-market [exposure]" and the five with
  the greatest exposure to the fund.
* AIFMD Annex IV (Commission Delegated Regulation (EU) No 231/2013, Art. 110(1),
  implementing Art. 24 of Directive 2011/61/EU) requires principal exposures and
  most-traded instruments per AIF.

Redacting positions out of a statutory filing is not information-barrier
hygiene; it is an incomplete filing. Those go through the statutory channel
(PFRD for Form PF; the AIFM's national competent authority for Annex IV) and are
protected there by the regime's own confidentiality provisions -- for Form PF,
"The SEC does not intend to make public information reported on Form PF that is
identifiable to any particular adviser or private fund" (Form PF, General
Instructions, Federal Information Law notice; Advisers Act s.204(b), 17 CFR
275.204(b)-1).

What this module *is* for is the discretionary channel that sits alongside those
filings: the LP risk letter, the prime broker's daily exposure feed, the risk
appendix handed to an auditor, a supervisory information request answered in
aggregate. On that channel, withholding positions is correct and the failure
mode is leaking them.

Three correctness points carry most of the weight, and all three were wrong in
version 1.

1. **"Top N" must rank, not slice.** Version 1 took
   ``list(concentrations.items())[:5]``, which is the first five in *insertion
   order*. Handed an unsorted mapping it disclosed an arbitrary five sectors
   under a heading that says "largest five" -- understating concentration to the
   LP whenever the caller's dict was not already sorted. Ranking is now by
   **absolute** exposure, because a -40% net short is a larger concentration
   than a +5% long.

2. **A NAV denominator must never be fudged.** Version 1 divided by
   ``max(nav, 1.0)``. A fund with zero or negative NAV -- the exact situation in
   which a leverage report matters -- silently produced a leverage figure
   denominated in one dollar, and a NaN NAV propagated a NaN straight into a
   digested, dispatched report. Non-positive and non-finite NAV are now
   rejected.

3. **An unkeyed SHA-256 is a checksum, not a signature.** Version 1 called its
   output a "cryptographic report signature". Anyone can recompute an unkeyed
   digest over content they have altered, so it authenticates nothing; it
   detects accidental corruption, and only if the recipient obtains the expected
   digest over a channel the attacker does not control. Authentication needs a
   key: HMAC (NIST FIPS 198-1) for a shared secret, or a digital signature
   (NIST FIPS 186-5) for non-repudiation. The field is now named
   ``content_digest``, ``authentication`` states plainly which of the two you
   have, and passing ``hmac_key`` to the engine adds a real HMAC-SHA256 tag.
   Version 1's digest also covered only five fields -- fund, stakeholder, date,
   NAV and gross leverage -- so VaR, Sharpe, drawdown, concentrations and
   liquidity could all be altered with the digest still verifying. The digest
   now covers every reported field except its own envelope.

Scope and honesty boundary
--------------------------
This engine computes no risk. It does not calculate VaR, Sharpe, drawdown,
sector exposure or days-to-liquidate; it does not value a portfolio, convert
currencies, or reach any external system. It consumes figures your risk system
produces, validates that they are internally consistent and expressible, decides
what leaves the firm, and seals the result. Garbage in is rejected where it can
be detected and reported faithfully where it cannot.

``gross_leverage`` and ``net_leverage`` here are simple exposure-to-NAV ratios.
They are **not** AIFMD leverage: that is defined by the gross method (Art. 7)
and the commitment method (Art. 8) of Regulation 231/2013 and requires
instrument-by-instrument conversion this module does not perform. Do not put
these numbers in an Annex IV leverage field.

See ``references/standards.md`` for the source behind every claim above.
"""

import hashlib
import hmac
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)
# Library convention: emit nothing unless the host application configures
# logging. Everything is present on the returned report, so a caller with no
# handlers still gets the full outcome programmatically.
logger.addHandler(logging.NullHandler())

__all__ = [
    "ExternalReportError",
    "ReportInputError",
    "DisclosurePolicyError",
    "RedactionError",
    "StakeholderType",
    "LiquidityConvention",
    "DisclosurePolicy",
    "PortfolioRiskState",
    "ExternalRiskReport",
    "RiskReportingForExternalStakeholdersEngine",
    "canonical_report_bytes",
    "verify_report",
    "AUTHENTICATION_NONE",
    "AUTHENTICATION_HMAC_SHA256",
    "DIGEST_ALGORITHM",
    "DIGEST_COVERED_FIELDS",
    "DEFAULT_IDENTIFIER_FIELDS",
    "FORM_PF_Q32_LIQUIDITY_BUCKETS",
    "LIQUIDITY_SUM_TOLERANCE_PCT",
    "SECTOR_SUM_TOLERANCE_PCT",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ExternalReportError(ValueError):
    """Base class for every failure that must stop a report being dispatched."""


class ReportInputError(ExternalReportError):
    """Raised when portfolio inputs cannot be evaluated or are inconsistent.

    Always treat this as a **failed** report, never as a report to send with a
    caveat. An unevaluable risk state is not a low-risk one.
    """


class DisclosurePolicyError(ExternalReportError):
    """Raised when no disclosure policy is defined for the requested recipient.

    Deliberately fail-closed. Version 1 routed any unrecognised stakeholder into
    an ``else`` branch that disclosed the **full** sector breakdown, so adding a
    ``PROSPECTIVE_INVESTOR`` member to the enum would have silently granted it
    the widest disclosure in the module. Silence is not consent to disclose.
    """


class RedactionError(ExternalReportError):
    """Raised when a supplied position identifier appears in the disclosed payload.

    The report is not returned. A leak detected at generation time is a bug in
    the caller's aggregation; a leak detected after dispatch is an incident.
    """


# ---------------------------------------------------------------------------
# Recipients and disclosure policy
# ---------------------------------------------------------------------------
class StakeholderType(str, Enum):
    """Who the report is addressed to. Determines the disclosure policy."""

    LIMITED_PARTNER = "LIMITED_PARTNER"  # LP / institutional investor
    REGULATOR = "REGULATOR"              # Supervisory request, NOT a filing
    PRIME_BROKER = "PRIME_BROKER"        # Daily exposure & liquidity feed
    AUDITOR = "AUDITOR"                  # Independent audit / assurance


@dataclass(frozen=True)
class DisclosurePolicy:
    """How much sector detail a given recipient receives, and why.

    Kept deliberately thin. Every knob here differentiates recipients in a way
    the firm can defend to a compliance officer; inventing further asymmetries
    (withholding Sharpe from a prime broker, say) with no contractual or
    statutory basis would be policy dressed up as engineering.
    """

    max_sector_rows: Optional[int]  # None == disclose every sector supplied
    rationale: str


_DISCLOSURE_POLICIES: Mapping[StakeholderType, DisclosurePolicy] = MappingProxyType({
    StakeholderType.LIMITED_PARTNER: DisclosurePolicy(
        max_sector_rows=5,
        rationale=(
            "Top-5 sector breakdown. Scope and cadence are set by the LPA and "
            "side letters, not by rule."
        ),
    ),
    StakeholderType.REGULATOR: DisclosurePolicy(
        max_sector_rows=None,
        rationale=(
            "Full sector breakdown for a supervisory information request. Not a "
            "statutory filing -- see disclosure_notice."
        ),
    ),
    StakeholderType.PRIME_BROKER: DisclosurePolicy(
        max_sector_rows=3,
        rationale=(
            "Exposure and liquidity for margin and credit monitoring; sector "
            "detail limited to the three largest under the PB agreement."
        ),
    ),
    StakeholderType.AUDITOR: DisclosurePolicy(
        max_sector_rows=None,
        rationale=(
            "Full sector breakdown within the audit engagement's scope."
        ),
    ),
})

_DISCLOSURE_NOTICES: Mapping[StakeholderType, str] = MappingProxyType({
    StakeholderType.LIMITED_PARTNER: (
        "Aggregate risk disclosure. Individual positions are withheld. Content "
        "and cadence are governed by the LPA and side letters -- there is no US "
        "federal quarterly-statement mandate: the SEC Private Fund Advisers "
        "rule, including the quarterly statement rule, was vacated in full by "
        "the Fifth Circuit in June 2024."
    ),
    StakeholderType.REGULATOR: (
        "Aggregate risk disclosure in response to a supervisory request. THIS IS "
        "NOT A STATUTORY FILING. Form PF section 2b requires position-level "
        "detail (Q35: each open position >=5% of NAV) and counterparty-level "
        "detail (Q22/Q23: five largest counterparties); AIFMD Annex IV requires "
        "principal exposures and most-traded instruments. Do not satisfy either "
        "obligation with a redacted report -- file through PFRD or the AIFM's "
        "national competent authority."
    ),
    StakeholderType.PRIME_BROKER: (
        "Aggregate exposure and liquidity feed for margin and credit "
        "monitoring. Contractual, not statutory; scope is set by the prime "
        "brokerage agreement."
    ),
    StakeholderType.AUDITOR: (
        "Aggregate risk disclosure for audit purposes. Position-level records "
        "required by the engagement are supplied through the audit workpaper "
        "channel, not through this report."
    ),
})


# ---------------------------------------------------------------------------
# Liquidity profile conventions
# ---------------------------------------------------------------------------
class LiquidityConvention(str, Enum):
    """How to read ``liquidity_days_to_liquidate_pct``.

    The two conventions are not distinguishable from the numbers alone, and a
    recipient who guesses wrong misreads the fund's liquidity by a wide margin.
    Version 1 carried no convention at all: its own example,
    ``{"1_DAY": 85.0, "7_DAYS": 100.0}``, reads as 85%-then-100% cumulative, but
    read as buckets it sums to 185% of the portfolio. Stating which one applies
    is not optional.
    """

    #: Non-overlapping buckets that together account for the portfolio, summing
    #: to approximately 100%. This is Form PF Question 32's schema: "Specify the
    #: percentage by value of the reporting fund's positions that may be
    #: liquidated within each of the periods specified below. Each investment
    #: should be assigned to only one period... The total should add up to
    #: approximately 100%."
    BUCKETED = "BUCKETED"

    #: Running totals: the percentage liquidatable *within* each horizon, so the
    #: series is non-decreasing in horizon order and ends at or below 100%.
    #: Keys must be supplied in ascending horizon order.
    CUMULATIVE = "CUMULATIVE"


#: The bucket schedule Form PF Question 32 uses. Provided for reference; the
#: engine does not require these exact labels, because funds legitimately report
#: internal schedules on the LP and PB channels.
FORM_PF_Q32_LIQUIDITY_BUCKETS: Tuple[str, ...] = (
    "1 day or less",
    "2 days - 7 days",
    "8 days - 30 days",
    "31 days - 90 days",
    "91 days - 180 days",
    "181 days - 365 days",
    "Longer than 365 days",
)

#: Form PF Q32 says the buckets "should add up to approximately 100%". One
#: percentage point of slack accommodates rounding in the caller's aggregation
#: without accepting a profile that is wrong by a whole bucket.
LIQUIDITY_SUM_TOLERANCE_PCT: float = 1.0

#: Sector exposures are expressed as a percentage of NAV, so their absolute
#: values cannot together exceed gross exposure as a percentage of NAV. Half a
#: percentage point of slack for rounding.
SECTOR_SUM_TOLERANCE_PCT: float = 0.5


# ---------------------------------------------------------------------------
# Integrity envelope
# ---------------------------------------------------------------------------
DIGEST_ALGORITHM = "SHA-256"

#: No key was supplied. The digest detects accidental corruption; it does not
#: authenticate the sender, because anyone can recompute an unkeyed hash over
#: content they have altered.
AUTHENTICATION_NONE = "NONE (unkeyed digest: integrity only, not authenticity)"

#: A shared secret was supplied. HMAC-SHA256 (NIST FIPS 198-1) authenticates the
#: report to any holder of that key. It is symmetric, so it does not provide
#: non-repudiation -- for that, sign with an asymmetric scheme (FIPS 186-5).
AUTHENTICATION_HMAC_SHA256 = "HMAC-SHA256"

#: Every reported field the digest covers. The rule is simple enough to state to
#: a recipient: the digest covers the whole report except ``report_id``, which is
#: derived from the digest, and the envelope fields that carry the digest itself.
DIGEST_COVERED_FIELDS: Tuple[str, ...] = (
    "preparer_firm_name",
    "stakeholder_type",
    "fund_name",
    "report_date_iso",
    "total_aum_usd",
    "net_asset_value_usd",
    "gross_exposure_usd",
    "net_exposure_usd",
    "gross_leverage",
    "net_leverage",
    "var_pct_of_nav",
    "var_confidence_pct",
    "var_horizon_days",
    "annualized_sharpe",
    "max_drawdown_pct",
    "disclosed_concentrations",
    "disclosed_liquidity",
    "liquidity_convention",
    "are_proprietary_positions_redacted",
    "redaction_verified",
    "redaction_note",
    "positions_withheld_count",
    "disclosure_notice",
    "audit_notes",
)

#: Position-dict keys the redaction check treats as identifiers. Restricted on
#: purpose: scanning *every* string value in a position dict would turn ordinary
#: fields like ``{"side": "LONG", "currency": "USD"}`` into tokens and reject a
#: perfectly good report whose sector label happens to contain "USD".
DEFAULT_IDENTIFIER_FIELDS: Tuple[str, ...] = (
    "symbol",
    "ticker",
    "instrument",
    "instrument_id",
    "security_id",
    "isin",
    "cusip",
    "sedol",
    "figi",
    "ric",
    "bloomberg_ticker",
    "contract",
    "issuer",
    "name",
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _finite(value: Any, label: str) -> float:
    """Reject NaN, infinity and non-numeric input before it reaches a report.

    NaN is the dangerous one. Every comparison against it is False, so a NaN
    would pass a range check written as ``if v < lo or v > hi: raise`` and land
    in a digested, dispatched deliverable. ``bool`` is excluded because ``True``
    would otherwise be a valid currency amount of 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportInputError(
            f"{label} must be an int or float, got {value!r} ({type(value).__name__})"
        )
    as_float = float(value)
    if not math.isfinite(as_float):
        raise ReportInputError(f"{label} must be a finite number, got {as_float!r}")
    return as_float


def _non_negative(value: Any, label: str) -> float:
    as_float = _finite(value, label)
    if as_float < 0.0:
        raise ReportInputError(f"{label} must be >= 0, got {as_float!r}")
    return as_float


def _positive(value: Any, label: str) -> float:
    as_float = _finite(value, label)
    if as_float <= 0.0:
        raise ReportInputError(f"{label} must be > 0, got {as_float!r}")
    return as_float


def _in_range(value: Any, label: str, low: float, high: float) -> float:
    as_float = _finite(value, label)
    if not (low <= as_float <= high):
        raise ReportInputError(f"{label} must be within [{low}, {high}], got {as_float!r}")
    return as_float


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_date(value: Any, label: str) -> str:
    """Require an unambiguous ``YYYY-MM-DD`` calendar date.

    ``date.fromisoformat`` alone is too permissive on Python 3.11+, where it
    also accepts ``20260805`` and week dates. A report period that a recipient
    has to guess at is a defect in a document that will be filed and compared
    against others.
    """
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise ReportInputError(f"{label} must be an ISO 'YYYY-MM-DD' string, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ReportInputError(f"{label} is not a valid calendar date: {value!r}") from exc
    return value


def _clean_mapping(value: Any, label: str) -> Dict[str, float]:
    """Coerce a caller-supplied mapping to ``{str: finite float}``.

    Non-string keys are rejected rather than stringified: a key that changes
    representation between generation and verification breaks the digest, and a
    recipient cannot tell that from tampering.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ReportInputError(f"{label} must be a mapping, got {type(value).__name__}")
    cleaned: Dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ReportInputError(f"{label} keys must be non-empty strings, got {key!r}")
        cleaned[key] = _finite(raw, f"{label}[{key!r}]")
    return cleaned


# ---------------------------------------------------------------------------
# Portfolio risk state
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PortfolioRiskState:
    """The risk figures your risk system produced, as they will be reported.

    Frozen on purpose: the inputs behind a sealed deliverable should not change
    under the caller after the digest is computed.

    Every field is validated at construction, so an invalid state cannot exist
    to be reported.

    Attributes:
        fund_name: The reporting fund, as it should appear on the deliverable.
        report_date_iso: Period end date, ``YYYY-MM-DD``.
        total_aum_usd: Assets under management of the entity named in
            ``fund_name``, on the adviser's own stated AUM basis (for a US
            adviser, typically regulatory AUM per Form ADV Instruction 5.b).
            This engine neither computes nor reconciles it.
        net_asset_value_usd: NAV, strictly positive. It is the denominator of
            both leverage ratios; a zero or negative NAV means the leverage
            question has no numeric answer, and saying so is more useful than
            printing one.
        gross_exposure_usd: Long plus absolute short exposure. Non-negative.
        net_exposure_usd: Long minus short. **Signed** -- a net-short book is
            legitimate and reports a negative net leverage.
        var_pct_of_nav: VaR expressed as a **positive percentage of NAV**,
            matching Form PF Q40(b)(vii)-(ix), "VaR at the end of the 1st month
            of the reporting period (as a % of NAV)".
        var_confidence_pct: The confidence interval the VaR was computed at, in
            percent (e.g. ``99.0``). Required, because Form PF Q40(b)(i) makes
            the filer state it: VaR is defined as "the loss over a target
            horizon that will not be exceeded at some specified confidence
            level", and two funds' VaR figures are not comparable without it.
        var_horizon_days: The VaR horizon in days (Form PF Q40(b)(ii)).
        annualized_sharpe_ratio: Signed, finite. Not computed here.
        max_drawdown_pct: Peak-to-trough loss as a positive percentage in
            ``[0, 100]``.
        top_sector_concentrations: Sector exposure as a percentage of NAV.
            Values may be negative for net-short sectors. Need not be sorted --
            the engine ranks them -- and need not be exhaustive.
        liquidity_days_to_liquidate_pct: Portfolio liquidity profile, read
            according to ``liquidity_convention``.
        liquidity_convention: ``BUCKETED`` (Form PF Q32 schema, the default) or
            ``CUMULATIVE``.
        proprietary_positions: The position-level detail being withheld. Supply
            it: the engine never copies it into the report, and having it is
            what lets the engine *verify* that no identifier leaked into the
            aggregates instead of merely asserting so.
    """

    fund_name: str
    report_date_iso: str
    total_aum_usd: float
    net_asset_value_usd: float
    gross_exposure_usd: float
    net_exposure_usd: float
    var_pct_of_nav: float
    var_confidence_pct: float
    var_horizon_days: int
    annualized_sharpe_ratio: float
    max_drawdown_pct: float
    top_sector_concentrations: Mapping[str, float]
    liquidity_days_to_liquidate_pct: Mapping[str, float]
    liquidity_convention: LiquidityConvention = LiquidityConvention.BUCKETED
    proprietary_positions: Optional[Sequence[Mapping[str, Any]]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.fund_name, str) or not self.fund_name.strip():
            raise ReportInputError(f"fund_name must be a non-empty string, got {self.fund_name!r}")

        object.__setattr__(self, "fund_name", self.fund_name.strip())
        object.__setattr__(self, "report_date_iso", _iso_date(self.report_date_iso, "report_date_iso"))
        object.__setattr__(self, "total_aum_usd", _non_negative(self.total_aum_usd, "total_aum_usd"))
        object.__setattr__(self, "net_asset_value_usd", _positive(self.net_asset_value_usd, "net_asset_value_usd"))
        object.__setattr__(self, "gross_exposure_usd", _non_negative(self.gross_exposure_usd, "gross_exposure_usd"))
        object.__setattr__(self, "net_exposure_usd", _finite(self.net_exposure_usd, "net_exposure_usd"))
        object.__setattr__(self, "var_pct_of_nav", _in_range(self.var_pct_of_nav, "var_pct_of_nav", 0.0, 100.0))
        object.__setattr__(
            self, "var_confidence_pct",
            _in_range(self.var_confidence_pct, "var_confidence_pct", 50.0, 99.9999),
        )
        object.__setattr__(self, "annualized_sharpe_ratio", _finite(self.annualized_sharpe_ratio, "annualized_sharpe_ratio"))
        object.__setattr__(self, "max_drawdown_pct", _in_range(self.max_drawdown_pct, "max_drawdown_pct", 0.0, 100.0))

        if isinstance(self.var_horizon_days, bool) or not isinstance(self.var_horizon_days, int):
            raise ReportInputError(f"var_horizon_days must be an int, got {self.var_horizon_days!r}")
        if self.var_horizon_days < 1:
            raise ReportInputError(f"var_horizon_days must be >= 1, got {self.var_horizon_days!r}")

        if not isinstance(self.liquidity_convention, LiquidityConvention):
            try:
                object.__setattr__(self, "liquidity_convention", LiquidityConvention(self.liquidity_convention))
            except ValueError as exc:
                raise ReportInputError(
                    f"liquidity_convention must be one of "
                    f"{[c.value for c in LiquidityConvention]}, got {self.liquidity_convention!r}"
                ) from exc

        object.__setattr__(
            self, "top_sector_concentrations",
            _clean_mapping(self.top_sector_concentrations, "top_sector_concentrations"),
        )
        object.__setattr__(
            self, "liquidity_days_to_liquidate_pct",
            _clean_mapping(self.liquidity_days_to_liquidate_pct, "liquidity_days_to_liquidate_pct"),
        )

        if self.proprietary_positions is not None:
            if isinstance(self.proprietary_positions, (str, bytes, Mapping)):
                raise ReportInputError(
                    "proprietary_positions must be a sequence of mappings, got "
                    f"{type(self.proprietary_positions).__name__}"
                )
            positions = tuple(self.proprietary_positions)
            for index, position in enumerate(positions):
                if not isinstance(position, Mapping):
                    raise ReportInputError(
                        f"proprietary_positions[{index}] must be a mapping, got "
                        f"{type(position).__name__}"
                    )
            object.__setattr__(self, "proprietary_positions", positions)

        self._validate_exposure_consistency()
        self._validate_sector_consistency()
        self._validate_liquidity_profile()

    # -- cross-field invariants --------------------------------------------
    def _validate_exposure_consistency(self) -> None:
        """Gross exposure is ``|long| + |short|``; net is ``long - short``.

        By the triangle inequality gross >= |net| always holds. A violation is
        not a market condition, it is the two fields swapped or one of them
        computed on a different basis -- and it would report as a plausible
        leverage pair if left alone.
        """
        gross = self.gross_exposure_usd
        net_abs = abs(self.net_exposure_usd)
        if net_abs > gross and not math.isclose(net_abs, gross, rel_tol=1e-9, abs_tol=1e-6):
            raise ReportInputError(
                f"|net_exposure_usd| ({net_abs:,.2f}) exceeds gross_exposure_usd "
                f"({gross:,.2f}); gross must be at least |net|. Check whether the "
                "two fields are swapped or computed on different bases."
            )

    def _validate_sector_consistency(self) -> None:
        """Sector exposures are percentages of NAV, so they are bounded by gross.

        Holds under both a gross-exposure and a net-signed convention, since
        |net sector| <= gross sector. Only a genuine inconsistency trips it --
        for example percentages of *gross* mistakenly labelled percentages of
        NAV on a sub-1x book.
        """
        if not self.top_sector_concentrations:
            return
        total_abs = sum(abs(v) for v in self.top_sector_concentrations.values())
        ceiling = (self.gross_exposure_usd / self.net_asset_value_usd) * 100.0 + SECTOR_SUM_TOLERANCE_PCT
        if total_abs > ceiling:
            raise ReportInputError(
                f"top_sector_concentrations absolute values sum to {total_abs:.4f}% of NAV, "
                f"which exceeds gross exposure of {ceiling - SECTOR_SUM_TOLERANCE_PCT:.4f}% of NAV. "
                "Sector percentages must be expressed as a percentage of NAV, on the "
                "same basis as gross_exposure_usd / net_asset_value_usd."
            )

    def _validate_liquidity_profile(self) -> None:
        """Enforce whichever convention the caller declared.

        An empty profile means "not reported" and is allowed through; a profile
        that contradicts its own declared convention is not, because a recipient
        reading it under the stated convention would be misled about how quickly
        the book can be wound down.
        """
        profile = self.liquidity_days_to_liquidate_pct
        if not profile:
            return

        values = list(profile.values())
        if self.liquidity_convention is LiquidityConvention.BUCKETED:
            for key, value in profile.items():
                if value < 0.0:
                    raise ReportInputError(
                        f"liquidity_days_to_liquidate_pct[{key!r}] must be >= 0 under the "
                        f"BUCKETED convention, got {value!r}"
                    )
            total = sum(values)
            if abs(total - 100.0) > LIQUIDITY_SUM_TOLERANCE_PCT:
                raise ReportInputError(
                    f"BUCKETED liquidity buckets sum to {total:.4f}%, outside "
                    f"100% +/- {LIQUIDITY_SUM_TOLERANCE_PCT}%. Form PF Q32 assigns each "
                    "investment to exactly one bucket and the total 'should add up to "
                    "approximately 100%'. If these are running totals, declare "
                    "LiquidityConvention.CUMULATIVE instead."
                )
            return

        # CUMULATIVE
        previous = 0.0
        for key, value in profile.items():
            if not (0.0 <= value <= 100.0):
                raise ReportInputError(
                    f"liquidity_days_to_liquidate_pct[{key!r}] must be within [0, 100] under "
                    f"the CUMULATIVE convention, got {value!r}"
                )
            if value < previous:
                raise ReportInputError(
                    f"CUMULATIVE liquidity profile decreases at {key!r} ({value!r} after "
                    f"{previous!r}). Running totals cannot fall as the horizon lengthens; "
                    "supply horizons in ascending order, or declare "
                    "LiquidityConvention.BUCKETED."
                )
            previous = value


# ---------------------------------------------------------------------------
# The deliverable
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExternalRiskReport:
    """A sealed aggregate risk disclosure addressed to one external recipient.

    Attributes:
        report_id: Content-addressed identifier, ending in the first 12 hex
            digits of ``content_digest``. Regenerating the same report yields
            the same id; amending any reported figure yields a different one.
            Excluded from digest coverage because it is derived from the digest.
        are_proprietary_positions_redacted: Structurally always ``True`` -- the
            engine has no code path that copies ``proprietary_positions`` into a
            report. It is **not** evidence that the disclosed aggregates are
            free of position-level identifiers. That is ``redaction_verified``.
        redaction_verified: ``True`` only when the engine actually checked
            supplied position identifiers against the disclosed payload and
            found none. ``False`` means "not checked", never "checked and
            clean"; ``redaction_note`` says why.
        positions_withheld_count: How many positions were withheld, or ``None``
            when the caller supplied no position list.
        content_digest: SHA-256 over the canonical form of every field listed in
            ``digest_covers``. An **unkeyed digest detects corruption; it does
            not authenticate the sender.** Anyone can recompute it over altered
            content, so it is only meaningful if the recipient obtains the
            expected value over a channel the sender authenticates separately.
        authentication: Which of the two you actually have --
            ``AUTHENTICATION_NONE`` or ``AUTHENTICATION_HMAC_SHA256``.
        authentication_tag: HMAC-SHA256 over the same canonical bytes, present
            only when the engine was given a key.
        digest_covers: The exact field names the digest protects. Give this to
            the recipient; a digest whose coverage is unstated is a digest the
            recipient cannot rely on.
    """

    report_id: str
    preparer_firm_name: str
    stakeholder_type: StakeholderType
    fund_name: str
    report_date_iso: str
    total_aum_usd: float
    net_asset_value_usd: float
    gross_exposure_usd: float
    net_exposure_usd: float
    gross_leverage: float
    net_leverage: float
    var_pct_of_nav: float
    var_confidence_pct: float
    var_horizon_days: int
    annualized_sharpe: float
    max_drawdown_pct: float
    disclosed_concentrations: Dict[str, float]
    disclosed_liquidity: Dict[str, float]
    liquidity_convention: LiquidityConvention
    are_proprietary_positions_redacted: bool
    redaction_verified: bool
    redaction_note: str
    positions_withheld_count: Optional[int]
    disclosure_notice: str
    audit_notes: str
    content_digest: str
    digest_algorithm: str
    authentication: str
    authentication_tag: Optional[str]
    digest_covers: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Canonical serialization and verification
# ---------------------------------------------------------------------------
def _json_safe(value: Any, label: str) -> Any:
    """Normalise to a JSON type with one unambiguous representation.

    Enums become their ``.value`` explicitly rather than relying on how the JSON
    encoder happens to treat a ``str`` subclass, which is the kind of detail
    that changes between interpreter versions and silently breaks verification
    of reports issued by an older release.
    """
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReportInputError(f"{label} is not finite and cannot be digested: {value!r}")
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v, f"{label}[{k!r}]") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, f"{label}[{i}]") for i, v in enumerate(value)]
    raise ReportInputError(f"{label} has type {type(value).__name__}, which cannot be digested")


def canonical_report_bytes(fields: Mapping[str, Any]) -> bytes:
    """Serialise the digest-covered fields to one canonical byte string.

    Canonical means: keys sorted, no insignificant whitespace, UTF-8, NaN and
    infinity refused. Mapping order is therefore *not* covered -- deliberately,
    so a recipient who round-trips the payload through their own JSON tooling
    still verifies. The ranked order of ``disclosed_concentrations`` is a
    presentation property of the report object, not part of the seal.
    """
    payload = {name: _json_safe(fields[name], name) for name in DIGEST_COVERED_FIELDS}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _covered_fields_of(report: ExternalRiskReport) -> Dict[str, Any]:
    return {name: getattr(report, name) for name in DIGEST_COVERED_FIELDS}


def _slug(text: str) -> str:
    """Filesystem- and identifier-safe fragment of a fund name, for report ids."""
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")[:32] or "FUND"


def _derive_report_id(
    stakeholder: StakeholderType,
    report_date_iso: str,
    fund_name: str,
    content_digest: str,
) -> str:
    """Derive the content-addressed report id.

    Deterministic in the digest, so ``verify_report`` re-derives it rather than
    trusting the value on the report. Keeping it out of the digested payload
    avoids the circularity of hashing a field computed from the hash, but that
    would otherwise leave the identifier -- the thing that ties the report to the
    dispatch log and the audit trail -- unprotected.
    """
    return (
        f"RPT-{stakeholder.value}-{report_date_iso}-"
        f"{_slug(fund_name)}-{content_digest[:12].upper()}"
    )


def verify_report(report: ExternalRiskReport, hmac_key: Optional[bytes] = None) -> bool:
    """Recompute the seal over the report as received.

    Returns ``True`` only if the digest matches, ``report_id`` re-derives from
    that digest, and -- when the report carries an HMAC tag -- the tag matches
    too. Comparison is constant-time (``hmac.compare_digest``).

    A ``True`` from the unkeyed path means the report has not been *corrupted*
    relative to its own digest. It does not mean the report came from the firm
    it names: an attacker who rewrites the figures can recompute the digest.
    Only the HMAC path answers that question, and only against a key the two
    parties share.

    Raises:
        ReportInputError: if the report carries an HMAC tag but no key was
            supplied. Silently downgrading to an integrity-only check would
            report success for a check that was never performed.
    """
    expected_digest = hashlib.sha256(canonical_report_bytes(_covered_fields_of(report))).hexdigest()
    if not hmac.compare_digest(expected_digest, report.content_digest):
        return False

    expected_id = _derive_report_id(
        report.stakeholder_type, report.report_date_iso, report.fund_name, expected_digest
    )
    if not hmac.compare_digest(expected_id, report.report_id):
        return False

    if report.authentication_tag is None:
        return True

    if hmac_key is None:
        raise ReportInputError(
            "report carries an HMAC authentication tag but no hmac_key was supplied; "
            "the digest matched, but authenticity was NOT verified"
        )
    expected_tag = hmac.new(
        hmac_key,
        canonical_report_bytes(_covered_fields_of(report)),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_tag, report.authentication_tag)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class RiskReportingForExternalStakeholdersEngine:
    """Builds aggregate risk disclosures for parties outside the information barrier.

    Args:
        firm_name: The preparer. Appears on the report and is digest-covered,
            because a sealed document should say who sealed it.
        hmac_key: Optional shared secret. Supplying it upgrades the envelope
            from an integrity-only digest to an HMAC-SHA256 authentication tag
            (NIST FIPS 198-1). Manage the key as a secret -- see
            `centralized-secrets-management-vault-integration`.
        identifier_fields: Position-dict keys treated as identifiers by the
            redaction check. Extend it when your position records name
            instruments under a key this module does not know; every field you
            leave out is a field the check cannot protect.
    """

    def __init__(
        self,
        firm_name: str = "Quantitative Asset Management",
        hmac_key: Optional[bytes] = None,
        identifier_fields: Optional[Iterable[str]] = None,
    ) -> None:
        if not isinstance(firm_name, str) or not firm_name.strip():
            raise ReportInputError(f"firm_name must be a non-empty string, got {firm_name!r}")
        if hmac_key is not None:
            if not isinstance(hmac_key, (bytes, bytearray)):
                raise ReportInputError(
                    f"hmac_key must be bytes, got {type(hmac_key).__name__}"
                )
            if len(hmac_key) == 0:
                raise ReportInputError("hmac_key must not be empty")

        self.firm_name = firm_name.strip()
        self._hmac_key = bytes(hmac_key) if hmac_key is not None else None
        self._identifier_fields = frozenset(
            f.lower() for f in (identifier_fields if identifier_fields is not None else DEFAULT_IDENTIFIER_FIELDS)
        )

    # -- disclosure policy --------------------------------------------------
    @staticmethod
    def resolve_policy(stakeholder_type: Any) -> Tuple[StakeholderType, DisclosurePolicy]:
        """Resolve a recipient to its policy, failing closed on anything unknown."""
        try:
            resolved = StakeholderType(stakeholder_type)
        except ValueError as exc:
            raise DisclosurePolicyError(
                f"no disclosure policy for stakeholder {stakeholder_type!r}; "
                f"known recipients are {[s.value for s in StakeholderType]}. "
                "Define a policy explicitly before disclosing to a new recipient type."
            ) from exc
        policy = _DISCLOSURE_POLICIES.get(resolved)
        if policy is None:  # pragma: no cover - guards a future enum member
            raise DisclosurePolicyError(
                f"stakeholder {resolved.value} has no entry in _DISCLOSURE_POLICIES; "
                "refusing to disclose under an undefined policy"
            )
        return resolved, policy

    # -- disclosure selection ----------------------------------------------
    @staticmethod
    def rank_concentrations(
        concentrations: Mapping[str, float],
        max_rows: Optional[int],
    ) -> Dict[str, float]:
        """Return the largest ``max_rows`` sectors by **absolute** exposure.

        Absolute, because concentration risk is about size, not direction: a
        -40% net short is a larger concentration than a +5% long, and ranking on
        the signed value would drop the short off the bottom of an LP's top-5.

        Ties break on sector name ascending so the same input always produces
        the same disclosure -- an ordering that shuffles between runs is an
        ordering an auditor cannot reconcile.
        """
        ranked = sorted(concentrations.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
        if max_rows is not None:
            ranked = ranked[:max_rows]
        return dict(ranked)

    # -- redaction ----------------------------------------------------------
    def _identifier_tokens(self, state: PortfolioRiskState) -> Tuple[str, ...]:
        if not state.proprietary_positions:
            return ()
        tokens = set()
        for position in state.proprietary_positions:
            for key, value in position.items():
                if not isinstance(key, str) or key.lower() not in self._identifier_fields:
                    continue
                if isinstance(value, str) and value.strip():
                    tokens.add(value.strip().upper())
        return tuple(sorted(tokens))

    @staticmethod
    def _scan_for_tokens(
        tokens: Sequence[str],
        surfaces: Mapping[str, Sequence[str]],
    ) -> Optional[Tuple[str, str, str]]:
        """Return the first ``(token, surface, text)`` leak found, or ``None``.

        Whole-word matching, so a position identifier ``F`` is caught in a
        sector label ``"F"`` but not inside ``"FINANCE"``. Case-insensitive,
        because a leak that survives ``.lower()`` is still a leak.
        """
        for surface_name, texts in surfaces.items():
            for text in texts:
                upper = text.upper()
                for token in tokens:
                    if re.search(rf"\b{re.escape(token)}\b", upper):
                        return token, surface_name, text
        return None

    def _verify_redaction(
        self,
        state: PortfolioRiskState,
        disclosed_concentrations: Mapping[str, float],
        disclosed_liquidity: Mapping[str, float],
    ) -> Tuple[bool, str, Optional[int]]:
        """Check that no supplied position identifier reached the disclosed payload.

        The engine never copies positions into a report, so the scalar risk
        metrics are structurally safe. The caller-controlled *keys* of the
        concentration and liquidity mappings are not: a sector breakdown keyed
        ``{"AAPL": 25.0}`` is a position disclosure wearing a sector label, and
        version 1 would have shipped it while asserting
        ``are_proprietary_positions_redacted=True``.

        Raises:
            RedactionError: when an identifier is found. The report is not
                returned -- an information barrier that logs a warning and sends
                the document anyway is not a barrier.
        """
        if state.proprietary_positions is None:
            return False, (
                "NOT VERIFIED: no proprietary_positions supplied, so the engine had "
                "nothing to check the disclosed aggregates against. Positions are "
                "structurally absent from this report, but that is not the same as "
                "having confirmed no identifier leaked into a sector or liquidity "
                "label. Supply the position list to enable the check."
            ), None

        withheld = len(state.proprietary_positions)
        tokens = self._identifier_tokens(state)
        if not tokens:
            return False, (
                f"NOT VERIFIED: {withheld} position(s) supplied but none carried a "
                f"recognised identifier field ({sorted(self._identifier_fields)}). "
                "Pass identifier_fields to the engine naming the keys your position "
                "records actually use."
            ), withheld

        leak = self._scan_for_tokens(
            tokens,
            {
                "disclosed_concentrations key": tuple(disclosed_concentrations.keys()),
                "disclosed_liquidity key": tuple(disclosed_liquidity.keys()),
                "fund_name": (state.fund_name,),
            },
        )
        if leak is not None:
            token, surface, text = leak
            raise RedactionError(
                f"position identifier {token!r} appears in {surface} {text!r}. "
                "The disclosed aggregates are not aggregate. Report not generated."
            )

        return True, (
            f"VERIFIED: {withheld} position(s) withheld; {len(tokens)} identifier(s) "
            "checked against every disclosed concentration key, liquidity key and the "
            "fund name, with no match."
        ), withheld

    # -- report generation --------------------------------------------------
    def generate_external_report(
        self,
        state: PortfolioRiskState,
        stakeholder_type: StakeholderType,
    ) -> ExternalRiskReport:
        """Build, verify and seal one aggregate risk disclosure.

        Raises:
            DisclosurePolicyError: the recipient has no defined policy.
            RedactionError: a position identifier reached the disclosed payload.
            ReportInputError: the state cannot be expressed in a report.
        """
        if not isinstance(state, PortfolioRiskState):
            raise ReportInputError(
                f"state must be a PortfolioRiskState, got {type(state).__name__}"
            )

        resolved_stakeholder, policy = self.resolve_policy(stakeholder_type)

        # Exact ratios. Version 1 rounded to two decimals before reporting; a
        # regulatory metric should carry the precision it was computed at and be
        # rounded only where it is rendered.
        gross_leverage = state.gross_exposure_usd / state.net_asset_value_usd
        net_leverage = state.net_exposure_usd / state.net_asset_value_usd

        disclosed_concentrations = self.rank_concentrations(
            state.top_sector_concentrations, policy.max_sector_rows
        )
        disclosed_liquidity = dict(state.liquidity_days_to_liquidate_pct)

        redaction_verified, redaction_note, positions_withheld = self._verify_redaction(
            state, disclosed_concentrations, disclosed_liquidity
        )

        disclosure_notice = _DISCLOSURE_NOTICES[resolved_stakeholder]

        audit_notes = (
            f"EXTERNAL RISK REPORT [{resolved_stakeholder.value}] {state.fund_name} "
            f"as at {state.report_date_iso}: NAV ${state.net_asset_value_usd:,.2f}, "
            f"gross leverage {gross_leverage:.4f}x, net leverage {net_leverage:.4f}x, "
            f"VaR {state.var_pct_of_nav:.4f}% of NAV at {state.var_confidence_pct:g}% "
            f"confidence over {state.var_horizon_days}d, Sharpe "
            f"{state.annualized_sharpe_ratio:.4f}, max drawdown "
            f"{state.max_drawdown_pct:.4f}%. Sectors disclosed: "
            f"{len(disclosed_concentrations)} of {len(state.top_sector_concentrations)} "
            f"({policy.rationale}). Liquidity convention: "
            f"{state.liquidity_convention.value}. Redaction {redaction_note}"
        )

        covered = {
            "preparer_firm_name": self.firm_name,
            "stakeholder_type": resolved_stakeholder,
            "fund_name": state.fund_name,
            "report_date_iso": state.report_date_iso,
            "total_aum_usd": state.total_aum_usd,
            "net_asset_value_usd": state.net_asset_value_usd,
            "gross_exposure_usd": state.gross_exposure_usd,
            "net_exposure_usd": state.net_exposure_usd,
            "gross_leverage": gross_leverage,
            "net_leverage": net_leverage,
            "var_pct_of_nav": state.var_pct_of_nav,
            "var_confidence_pct": state.var_confidence_pct,
            "var_horizon_days": state.var_horizon_days,
            "annualized_sharpe": state.annualized_sharpe_ratio,
            "max_drawdown_pct": state.max_drawdown_pct,
            "disclosed_concentrations": disclosed_concentrations,
            "disclosed_liquidity": disclosed_liquidity,
            "liquidity_convention": state.liquidity_convention,
            "are_proprietary_positions_redacted": True,
            "redaction_verified": redaction_verified,
            "redaction_note": redaction_note,
            "positions_withheld_count": positions_withheld,
            "disclosure_notice": disclosure_notice,
            "audit_notes": audit_notes,
        }

        canonical = canonical_report_bytes(covered)
        content_digest = hashlib.sha256(canonical).hexdigest()

        if self._hmac_key is not None:
            authentication = AUTHENTICATION_HMAC_SHA256
            authentication_tag: Optional[str] = hmac.new(
                self._hmac_key, canonical, hashlib.sha256
            ).hexdigest()
        else:
            authentication = AUTHENTICATION_NONE
            authentication_tag = None

        report_id = _derive_report_id(
            resolved_stakeholder, state.report_date_iso, state.fund_name, content_digest
        )

        # Deliberately not logging NAV, VaR or exposures: audit_notes carries
        # them on the returned object for the caller to persist to a controlled
        # dispatch log, rather than into whatever handlers the host application
        # happens to have attached.
        logger.info(
            "external risk report generated: id=%s stakeholder=%s digest=%s "
            "authentication=%s redaction_verified=%s",
            report_id,
            resolved_stakeholder.value,
            content_digest[:12],
            authentication,
            redaction_verified,
        )

        return ExternalRiskReport(
            report_id=report_id,
            preparer_firm_name=self.firm_name,
            stakeholder_type=resolved_stakeholder,
            fund_name=state.fund_name,
            report_date_iso=state.report_date_iso,
            total_aum_usd=state.total_aum_usd,
            net_asset_value_usd=state.net_asset_value_usd,
            gross_exposure_usd=state.gross_exposure_usd,
            net_exposure_usd=state.net_exposure_usd,
            gross_leverage=gross_leverage,
            net_leverage=net_leverage,
            var_pct_of_nav=state.var_pct_of_nav,
            var_confidence_pct=state.var_confidence_pct,
            var_horizon_days=state.var_horizon_days,
            annualized_sharpe=state.annualized_sharpe_ratio,
            max_drawdown_pct=state.max_drawdown_pct,
            disclosed_concentrations=disclosed_concentrations,
            disclosed_liquidity=disclosed_liquidity,
            liquidity_convention=state.liquidity_convention,
            are_proprietary_positions_redacted=True,
            redaction_verified=redaction_verified,
            redaction_note=redaction_note,
            positions_withheld_count=positions_withheld,
            disclosure_notice=disclosure_notice,
            audit_notes=audit_notes,
            content_digest=content_digest,
            digest_algorithm=DIGEST_ALGORITHM,
            authentication=authentication,
            authentication_tag=authentication_tag,
            digest_covers=DIGEST_COVERED_FIELDS,
        )

