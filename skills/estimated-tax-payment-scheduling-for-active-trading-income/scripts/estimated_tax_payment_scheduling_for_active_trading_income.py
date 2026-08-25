"""estimated-tax-payment-scheduling-for-active-trading-income: US **federal**
individual quarterly estimated tax scheduling under IRC section 6654.

Scope
-----
This module implements the **regular installment method** of IRC section 6654 for a
**calendar-year US individual** (including a sole proprietor, an LLC member, or an
S corporation shareholder reporting pass-through trading income on Form 1040). It
computes the required annual payment under section 6654(d)(1)(B), splits it into
the four 25% required installments of section 6654(d)(1)(A), credits wage
withholding rateably under section 6654(g), places each installment on a real
payable date adjusted under section 7503, and measures the cumulative shortfall
that is what actually triggers the addition to tax.

It is **not** in scope for:

* **State estimated tax.** Section 6654 is federal. States set their own rules and
  several diverge materially — California requires 30%/40%/0%/30% instalments
  (R&TC 19136.1) and withdraws the prior-year safe harbor entirely once current-year
  AGI reaches $1,000,000 (R&TC 19136.3). Running this engine's federal output
  through a state schedule will underpay.
* **Corporations.** A C corporation computes estimated tax under section 6655, not
  section 6654. Only pass-through income arriving on an individual return is in
  scope.
* **Nonresident aliens** described in section 6072(c), who have **three** required
  instalments under section 6654(j), not four.
* **Fiscal-year taxpayers.** Section 6654(k) substitutes corresponding months; this
  engine emits the calendar-year dates of the section 6654(c)(2) table only.
* **The annualized income installment method** of section 6654(d)(2) and Form 2210
  Schedule AI (22.5% / 45% / 67.5% / 90% of annualized tax). A trader whose gains
  are concentrated late in the year and who cannot rely on the prior-year safe
  harbor usually needs that method to reduce the early instalments; this engine
  will over-state Q1 and Q2 for that taxpayer. See ``references/standards.md``.

What "tax" means here
---------------------
``projected_current_year_tax_usd`` and ``prior_year_tax_usd`` must be stated on the
section 6654(f) basis: the chapter 1 income tax **plus** the chapter 2
self-employment tax **plus** the chapter 2A net investment income tax, reduced by
credits other than the section 31 wage withholding credit. For an active trader the
chapter 2A component is not optional: section 1411(c)(2)(B) makes a trade or
business of trading in financial instruments or commodities an applicable trade or
business, so trading gains carry the 3.8% NIIT above the section 1411(b) MAGI
thresholds. Omitting it understates the 90% current-year target.

Withholding is **not** subtracted from these figures. It is supplied separately as
``current_year_withholding_usd`` and credited under section 6654(g).

Determinism
-----------
Nothing in this module reads the wall clock. ``as_of_date`` is an explicit optional
argument, so a report is reproducible and auditable. Money is computed in integer
cents; cumulative requirements round **up** to the cent so an instalment is never
reported as short of its statutory 25% share, and the four instalments still sum
exactly to the required annual payment.

This module performs a mechanical statutory computation. It is not tax advice, and
it does not verify eligibility facts asserted by the caller.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Filing statuses -------------------------------------------------------

FILING_STATUS_SINGLE = "SINGLE"
FILING_STATUS_MARRIED_FILING_JOINTLY = "MARRIED_FILING_JOINTLY"
FILING_STATUS_MARRIED_FILING_SEPARATELY = "MARRIED_FILING_SEPARATELY"
FILING_STATUS_HEAD_OF_HOUSEHOLD = "HEAD_OF_HOUSEHOLD"
FILING_STATUS_QUALIFYING_SURVIVING_SPOUSE = "QUALIFYING_SURVIVING_SPOUSE"

VALID_FILING_STATUSES = frozenset({
    FILING_STATUS_SINGLE,
    FILING_STATUS_MARRIED_FILING_JOINTLY,
    FILING_STATUS_MARRIED_FILING_SEPARATELY,
    FILING_STATUS_HEAD_OF_HOUSEHOLD,
    FILING_STATUS_QUALIFYING_SURVIVING_SPOUSE,
})

# --- Statutory constants ---------------------------------------------------

#: Section 6654(d)(1)(C) prior-year AGI threshold above which the prior-year safe
#: harbor is 110% rather than 100%. A fixed statutory dollar amount: it is not
#: indexed for inflation.
HIGH_AGI_THRESHOLD_USD = 150_000.0

#: Section 6654(d)(1)(C) threshold substituted for a married individual filing a
#: separate return.
HIGH_AGI_THRESHOLD_MFS_USD = 75_000.0

#: Section 6654(e)(1): no addition to tax where the tax shown, reduced by the
#: section 31 withholding credit, is less than this amount. Also fixed.
DE_MINIMIS_THRESHOLD_USD = 1_000.0

#: Section 6654(d)(1)(B)(i).
CURRENT_YEAR_SAFE_HARBOR_RATE = 0.90

#: Section 6654(d)(1)(B)(ii) and (d)(1)(C).
PRIOR_YEAR_RATE_STANDARD_PCT = 100.0
PRIOR_YEAR_RATE_HIGH_AGI_PCT = 110.0

#: Section 6654(c)(1): "There shall be 4 required installments for each taxable
#: year", each 25% of the required annual payment under section 6654(d)(1)(A).
INSTALLMENT_COUNT = 4

#: Section 6654(c)(2) table, as (quarter label, month, day-of-month, year offset
#: from the taxable year). The 4th instalment falls in the following calendar year.
_STATUTORY_DUE_DATES: Tuple[Tuple[str, int, int, int], ...] = (
    ("Q1", 4, 15, 0),
    ("Q2", 6, 15, 0),
    ("Q3", 9, 15, 0),
    ("Q4", 1, 15, 1),
)

BASIS_PRIOR_YEAR = "PRIOR_YEAR"
BASIS_CURRENT_YEAR_90PCT = "CURRENT_YEAR_90PCT"

EXCEPTION_DE_MINIMIS = "IRC_6654(e)(1)_DE_MINIMIS_UNDER_1000"
EXCEPTION_NO_PRIOR_YEAR_LIABILITY = "IRC_6654(e)(2)_NO_PRIOR_YEAR_LIABILITY"

STATUS_PAID = "PAID"
STATUS_SCHEDULED = "SCHEDULED"
STATUS_OVERDUE = "OVERDUE"

#: The DC legal-holiday calendar below is accurate from 2005 (the first year
#: Emancipation Day was observed as a DC legal holiday). Refuse to emit dates for
#: earlier years rather than emit silently wrong ones.
MIN_SUPPORTED_TAX_YEAR = 2005
MAX_SUPPORTED_TAX_YEAR = 2100

_CENTS = 100


class EstimatedTaxError(ValueError):
    """Raised when an input to the estimated tax engine is invalid.

    A tax schedule must fail loudly rather than emit a plausible-looking number.
    A negative liability, a NaN, or a prior-year safe harbor claimed for a year in
    which no return was filed all produce an authoritative-looking payment schedule
    that under-funds the taxpayer and is only discovered when the section 6654
    addition to tax is assessed.
    """


# --- Section 7503 date adjustment -----------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """Return the ``n``-th ``weekday`` (Mon=0) of ``month``. ``n`` is 1-based."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """Return the last ``weekday`` (Mon=0) of ``month``."""
    if month == 12:
        last_day = dt.date(year, 12, 31)
    else:
        last_day = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return last_day - dt.timedelta(days=(last_day.weekday() - weekday) % 7)


def _observed(day: dt.date) -> dt.date:
    """Observance date of a fixed-date holiday: Saturday shifts back to the
    preceding Friday, Sunday forward to the following Monday."""
    if day.weekday() == 5:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


def dc_legal_holidays(year: int) -> frozenset:
    """Legal holidays in the District of Columbia for ``year``, as observed.

    Section 7503 defines "legal holiday" by reference to the District of Columbia,
    which is why Emancipation Day (April 16) moves the federal Q1 estimated tax
    deadline for every taxpayer in the country. Two entries are year-gated because
    they did not always exist: Emancipation Day has been a DC legal holiday since
    2005, and Juneteenth since 2021.
    """
    holidays = {
        _observed(dt.date(year, 1, 1)),                  # New Year's Day
        _nth_weekday(year, 1, 0, 3),                     # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                     # Washington's Birthday
        _last_weekday(year, 5, 0),                       # Memorial Day
        _observed(dt.date(year, 7, 4)),                  # Independence Day
        _nth_weekday(year, 9, 0, 1),                     # Labor Day
        _nth_weekday(year, 10, 0, 2),                    # Columbus Day
        _observed(dt.date(year, 11, 11)),                # Veterans Day
        _nth_weekday(year, 11, 3, 4),                    # Thanksgiving Day
        _observed(dt.date(year, 12, 25)),                # Christmas Day
    }
    if year >= 2005:
        holidays.add(_observed(dt.date(year, 4, 16)))    # Emancipation Day (DC)
    if year >= 2021:
        holidays.add(_observed(dt.date(year, 6, 19)))    # Juneteenth
    return frozenset(holidays)


def apply_section_7503(day: dt.date) -> dt.date:
    """Advance ``day`` to the next day that is not a Saturday, Sunday, or DC legal
    holiday, per IRC section 7503.

    The IRS applies this to estimated tax payments as well as returns. The rule
    compounds: in 2017 April 15 was a Saturday and Emancipation Day (April 16,
    a Sunday) was observed on Monday April 17, so the Q1 payment was not due until
    Tuesday April 18.
    """
    holidays = dc_legal_holidays(day.year) | dc_legal_holidays(day.year + 1)
    current = day
    while current.weekday() >= 5 or current in holidays:
        current += dt.timedelta(days=1)
    return current


def statutory_installment_due_dates(tax_year: int) -> Tuple[dt.date, ...]:
    """Unadjusted section 6654(c)(2) due dates for a calendar-year taxpayer."""
    return tuple(
        dt.date(tax_year + year_offset, month, day)
        for _label, month, day, year_offset in _STATUTORY_DUE_DATES
    )


def adjusted_installment_due_dates(tax_year: int) -> Tuple[dt.date, ...]:
    """Section 6654(c)(2) due dates after the section 7503 weekend/holiday shift."""
    return tuple(apply_section_7503(d) for d in statutory_installment_due_dates(tax_year))


# --- Money helpers ---------------------------------------------------------


def _to_cents(amount: float) -> int:
    """Convert dollars to integer cents, rounding half away from zero.

    ``round()`` is deliberately avoided: it rounds half to even, so a schedule
    built from it can systematically fall a cent short of the statutory 25% share.
    """
    return int(math.floor(amount * _CENTS + 0.5))


def _to_dollars(cents: int) -> float:
    return cents / _CENTS


# --- Data structures -------------------------------------------------------


@dataclass
class QuarterlyTaxInstallment:
    """One of the four section 6654(c) required instalments.

    Attributes:
        quarter_name: ``'Q1'``..``'Q4'``.
        due_date_description: Human-readable form of ``due_date``.
        statutory_due_date: The unadjusted section 6654(c)(2) date.
        due_date: ``statutory_due_date`` after the section 7503 weekend and DC
            legal-holiday shift. This is the date a payment is actually timely.
        required_payment_usd: The section 6654(d)(1)(A) required instalment — this
            quarter's 25% share of the required annual payment. Individual
            instalments may differ from one another by one cent so that the four
            sum exactly to the required annual payment.
        withholding_credit_usd: Share of wage withholding deemed paid on this due
            date under section 6654(g).
        estimated_tax_due_usd: What must actually be remitted with Form 1040-ES on
            this date, after the withholding credit and after carrying forward any
            surplus from earlier instalments.
        payment_made_usd: Estimated tax actually paid for this instalment, as
            supplied by the caller.
        cumulative_required_usd: Required instalments through this quarter.
        cumulative_credited_usd: Withholding credit plus estimated payments through
            this quarter.
        shortfall_usd: ``cumulative_required_usd - cumulative_credited_usd``,
            floored at zero. This cumulative figure — not the single-quarter
            payment — is what the section 6654 addition to tax is computed on.
        status: ``'PAID'`` (no shortfall), ``'OVERDUE'`` (shortfall and the due date
            has passed as of ``as_of_date``), or ``'SCHEDULED'`` (shortfall but not
            yet due, or no ``as_of_date`` was supplied).
    """

    quarter_name: str
    due_date_description: str
    statutory_due_date: dt.date
    due_date: dt.date
    required_payment_usd: float
    withholding_credit_usd: float
    estimated_tax_due_usd: float
    payment_made_usd: float
    cumulative_required_usd: float
    cumulative_credited_usd: float
    shortfall_usd: float
    status: str


@dataclass
class EstimatedTaxScheduleReport:
    """Outcome of one section 6654 scheduling run, suitable for retention.

    Attributes:
        applied_safe_harbor_pct: ``100.0`` or ``110.0`` when the prior-year option
            is available, otherwise ``None``.
        safe_harbor_prior_year_usd: The section 6654(d)(1)(B)(ii) figure, or
            ``None`` when the prior-year option is unavailable.
        safe_harbor_basis: Which limb of section 6654(d)(1)(B) produced the required
            annual payment.
        is_safe_harbor_compliant: True only when every *evaluated* instalment has
            zero cumulative shortfall. Instalments are evaluated up to
            ``evaluated_as_of``; when no date was supplied, all four are evaluated,
            so an unfunded forward plan correctly reports False.
        penalty_exception_applies: A section 6654(e) exception removes the addition
            to tax even where instalments were underpaid. It does not mean the
            schedule was funded.
    """

    trader_id: str
    tax_year: int
    filing_status: str
    prior_year_agi_usd: float
    prior_year_tax_usd: float
    projected_current_year_tax_usd: float
    current_year_withholding_usd: float
    high_agi_threshold_usd: float
    prior_year_safe_harbor_available: bool
    applied_safe_harbor_pct: Optional[float]
    safe_harbor_prior_year_usd: Optional[float]
    safe_harbor_current_year_90pct_usd: float
    required_annual_tax_payment_usd: float
    safe_harbor_basis: str
    quarterly_installment_usd: float
    total_estimated_tax_to_schedule_usd: float
    installments: List[QuarterlyTaxInstallment]
    max_cumulative_shortfall_usd: float
    is_safe_harbor_compliant: bool
    penalty_exception_applies: bool
    penalty_exception_basis: Optional[str]
    evaluated_as_of: Optional[dt.date]
    audit_notes: str


# --- Validation ------------------------------------------------------------


def _require_amount(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EstimatedTaxError(f"{label} must be a number, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise EstimatedTaxError(f"{label} must be finite, got {value!r}")
    if numeric < 0.0:
        raise EstimatedTaxError(f"{label} must not be negative, got {numeric}")
    return numeric


# --- Engine ----------------------------------------------------------------


class EstimatedTaxSchedulerEngine:
    """Computes the IRC section 6654 required annual payment and the four required
    instalments for a calendar-year US individual with trading income.

    Args:
        high_agi_threshold_usd: Optional override for the section 6654(d)(1)(C)
            AGI threshold. Leave as ``None`` — the default — to derive it from
            filing status ($75,000 for married filing separately, $150,000
            otherwise). Supply a value only for what-if analysis; doing so
            suppresses the married-filing-separately substitution.
    """

    def __init__(self, high_agi_threshold_usd: Optional[float] = None):
        if high_agi_threshold_usd is not None:
            high_agi_threshold_usd = _require_amount(
                high_agi_threshold_usd, "high_agi_threshold_usd")
        self.high_agi_threshold_usd = high_agi_threshold_usd

    def resolve_high_agi_threshold(self, filing_status: str) -> float:
        """Section 6654(d)(1)(C) AGI threshold applicable to ``filing_status``."""
        if self.high_agi_threshold_usd is not None:
            return self.high_agi_threshold_usd
        if filing_status == FILING_STATUS_MARRIED_FILING_SEPARATELY:
            return HIGH_AGI_THRESHOLD_MFS_USD
        return HIGH_AGI_THRESHOLD_USD

    def generate_estimated_tax_schedule(
        self,
        trader_id: str,
        tax_year: int,
        prior_year_agi_usd: float,
        prior_year_tax_usd: float,
        projected_current_year_tax_usd: float,
        filing_status: str = FILING_STATUS_SINGLE,
        current_year_withholding_usd: float = 0.0,
        payments_made_usd: Optional[Sequence[float]] = None,
        prior_year_was_12_months: bool = True,
        prior_year_return_filed: bool = True,
        us_person_entire_prior_year: bool = True,
        as_of_date: Optional[dt.date] = None,
    ) -> EstimatedTaxScheduleReport:
        """Build the section 6654 required annual payment and instalment schedule.

        Args:
            trader_id: Stable identifier used in the audit note.
            tax_year: Calendar tax year. Must be in
                ``[MIN_SUPPORTED_TAX_YEAR, MAX_SUPPORTED_TAX_YEAR]`` because the DC
                legal-holiday calendar used for the section 7503 shift is only
                accurate over that range.
            prior_year_agi_usd: AGI *shown on the prior year return* — the figure
                section 6654(d)(1)(C) tests, not current-year AGI.
            prior_year_tax_usd: Tax shown on the prior year return, on the section
                6654(f) basis.
            projected_current_year_tax_usd: Projected current-year tax on the
                section 6654(f) basis: chapter 1 income tax plus chapter 2
                self-employment tax plus the chapter 2A net investment income tax,
                reduced by credits other than the section 31 withholding credit.
                Do **not** subtract withholding here.
            filing_status: One of ``VALID_FILING_STATUSES``. Drives the section
                6654(d)(1)(C) threshold only.
            current_year_withholding_usd: Wage withholding (the section 31 credit)
                expected for the year. Deemed paid in four equal parts under
                section 6654(g), whenever it was actually withheld.
            payments_made_usd: Estimated tax already paid, per instalment, in
                quarter order. Shorter sequences are zero-padded. Amounts, not a
                count: a short payment is exactly what triggers the addition to tax.
            prior_year_was_12_months: Whether the preceding taxable year was a year
                of 12 months. Section 6654(d)(1)(B) flush text.
            prior_year_return_filed: Whether a return was filed for the preceding
                taxable year. Section 6654(d)(1)(B) flush text. When either this or
                ``prior_year_was_12_months`` is False the prior-year safe harbor is
                unavailable and the 90% current-year figure governs alone.
            us_person_entire_prior_year: Whether the individual was a US citizen or
                resident throughout the preceding taxable year. Section
                6654(e)(2)(C).
            as_of_date: Evaluation date. Supplied explicitly so reports are
                reproducible; when omitted, no instalment is treated as past due
                and all four are evaluated for compliance.

        Returns:
            An :class:`EstimatedTaxScheduleReport`.

        Raises:
            EstimatedTaxError: On any invalid input.
        """
        # --- Validate -----------------------------------------------------
        if not isinstance(trader_id, str) or not trader_id.strip():
            raise EstimatedTaxError("trader_id must be a non-empty string")
        if isinstance(tax_year, bool) or not isinstance(tax_year, int):
            raise EstimatedTaxError(
                f"tax_year must be an int, got {type(tax_year).__name__}")
        if not MIN_SUPPORTED_TAX_YEAR <= tax_year <= MAX_SUPPORTED_TAX_YEAR:
            raise EstimatedTaxError(
                f"tax_year {tax_year} outside supported range "
                f"[{MIN_SUPPORTED_TAX_YEAR}, {MAX_SUPPORTED_TAX_YEAR}]; the DC "
                f"legal-holiday calendar used for the section 7503 adjustment is "
                f"not accurate outside it")
        if filing_status not in VALID_FILING_STATUSES:
            raise EstimatedTaxError(
                f"filing_status {filing_status!r} not one of "
                f"{sorted(VALID_FILING_STATUSES)}")
        if as_of_date is not None and not isinstance(as_of_date, dt.date):
            raise EstimatedTaxError(
                f"as_of_date must be a datetime.date or None, got "
                f"{type(as_of_date).__name__}")

        prior_year_agi_usd = _require_amount(prior_year_agi_usd, "prior_year_agi_usd")
        prior_year_tax_usd = _require_amount(prior_year_tax_usd, "prior_year_tax_usd")
        projected_current_year_tax_usd = _require_amount(
            projected_current_year_tax_usd, "projected_current_year_tax_usd")
        current_year_withholding_usd = _require_amount(
            current_year_withholding_usd, "current_year_withholding_usd")

        for flag, label in (
            (prior_year_was_12_months, "prior_year_was_12_months"),
            (prior_year_return_filed, "prior_year_return_filed"),
            (us_person_entire_prior_year, "us_person_entire_prior_year"),
        ):
            if not isinstance(flag, bool):
                raise EstimatedTaxError(f"{label} must be a bool")

        payments = list(payments_made_usd or [])
        if len(payments) > INSTALLMENT_COUNT:
            raise EstimatedTaxError(
                f"payments_made_usd has {len(payments)} entries; there are only "
                f"{INSTALLMENT_COUNT} required instalments")
        payments = [
            _require_amount(p, f"payments_made_usd[{i}]") for i, p in enumerate(payments)
        ]
        payments += [0.0] * (INSTALLMENT_COUNT - len(payments))

        # --- Section 6654(d)(1)(B): required annual payment ---------------
        threshold_usd = self.resolve_high_agi_threshold(filing_status)
        current_year_target = projected_current_year_tax_usd * CURRENT_YEAR_SAFE_HARBOR_RATE
        current_year_target_cents = _to_cents(current_year_target)

        # Flush text of section 6654(d)(1)(B): clause (ii) is unavailable unless the
        # preceding year was a 12-month year AND a return was filed for it.
        prior_year_available = bool(prior_year_was_12_months and prior_year_return_filed)

        if prior_year_available:
            # Section 6654(d)(1)(C): strictly "exceeds" the threshold.
            safe_harbor_pct = (
                PRIOR_YEAR_RATE_HIGH_AGI_PCT
                if prior_year_agi_usd > threshold_usd
                else PRIOR_YEAR_RATE_STANDARD_PCT
            )
            prior_year_target_cents = _to_cents(
                prior_year_tax_usd * (safe_harbor_pct / 100.0))
            if prior_year_target_cents <= current_year_target_cents:
                required_annual_cents = prior_year_target_cents
                basis = BASIS_PRIOR_YEAR
            else:
                required_annual_cents = current_year_target_cents
                basis = BASIS_CURRENT_YEAR_90PCT
            prior_year_target_usd: Optional[float] = _to_dollars(prior_year_target_cents)
            applied_pct: Optional[float] = safe_harbor_pct
        else:
            required_annual_cents = current_year_target_cents
            basis = BASIS_CURRENT_YEAR_90PCT
            prior_year_target_usd = None
            applied_pct = None
            logger.warning(
                "Prior-year safe harbor unavailable for %s (%s): section "
                "6654(d)(1)(B) requires a 12-month preceding year with a return "
                "filed. Falling back to 90%% of projected current-year tax.",
                trader_id, tax_year,
            )

        withholding_cents = _to_cents(current_year_withholding_usd)

        # --- Section 6654(e) exceptions -----------------------------------
        # (e)(1): tax shown reduced by the section 31 credit is under $1,000.
        de_minimis = (
            _to_cents(projected_current_year_tax_usd) - withholding_cents
            < _to_cents(DE_MINIMIS_THRESHOLD_USD)
        )
        # (e)(2): 12-month preceding year, no prior-year liability, US person
        # throughout that year. All three conditions are conjunctive.
        no_prior_liability = bool(
            prior_year_was_12_months
            and _to_cents(prior_year_tax_usd) == 0
            and us_person_entire_prior_year
        )
        if de_minimis:
            exception_basis: Optional[str] = EXCEPTION_DE_MINIMIS
        elif no_prior_liability:
            exception_basis = EXCEPTION_NO_PRIOR_YEAR_LIABILITY
        else:
            exception_basis = None

        # --- Instalment schedule ------------------------------------------
        statutory_dates = statutory_installment_due_dates(tax_year)
        installments: List[QuarterlyTaxInstallment] = []

        prev_required = 0
        prev_withheld = 0
        prev_needed = 0
        prev_paid = 0
        max_shortfall_cents = 0
        compliant = True

        for idx, (label, _month, _day, _offset) in enumerate(_STATUTORY_DUE_DATES):
            statutory_due = statutory_dates[idx]
            due = apply_section_7503(statutory_due)

            # Round the cumulative requirement UP so no instalment is ever reported
            # as short of its 25% share; the 4th cumulative equals the annual figure
            # exactly, so the four instalments still sum to the required annual
            # payment.
            cum_required = (
                required_annual_cents * (idx + 1) + INSTALLMENT_COUNT - 1
            ) // INSTALLMENT_COUNT
            # Round the withholding credit DOWN so the section 6654(g) rateable
            # credit is never over-stated.
            cum_withheld = withholding_cents * (idx + 1) // INSTALLMENT_COUNT

            required_this = cum_required - prev_required
            withheld_this = cum_withheld - prev_withheld

            # Cumulative form carries a surplus from an earlier instalment forward,
            # as section 6654(b)(2) requires.
            cum_needed = max(0, cum_required - cum_withheld)
            due_this = max(0, cum_needed - prev_needed)

            cum_paid = prev_paid + _to_cents(payments[idx])
            cum_credited = cum_withheld + cum_paid
            shortfall = max(0, cum_required - cum_credited)

            past_due = as_of_date is not None and as_of_date > due
            if shortfall == 0:
                status = STATUS_PAID
            elif past_due:
                status = STATUS_OVERDUE
            else:
                status = STATUS_SCHEDULED

            # An instalment is evaluated for compliance once its due date has
            # passed; with no as_of_date, all four are evaluated so an unfunded
            # forward plan is not reported as already compliant.
            evaluated = as_of_date is None or past_due
            if evaluated and shortfall > 0:
                compliant = False

            # Tracked over every instalment, due or not, so a forward plan still
            # reports the largest funding gap it will open up.
            max_shortfall_cents = max(max_shortfall_cents, shortfall)

            installments.append(QuarterlyTaxInstallment(
                quarter_name=label,
                # Built without platform-specific strftime flags such as '%-d',
                # which are not portable to Windows.
                due_date_description=f"{due:%B} {due.day}, {due.year}",
                statutory_due_date=statutory_due,
                due_date=due,
                required_payment_usd=_to_dollars(required_this),
                withholding_credit_usd=_to_dollars(withheld_this),
                estimated_tax_due_usd=_to_dollars(due_this),
                payment_made_usd=payments[idx],
                cumulative_required_usd=_to_dollars(cum_required),
                cumulative_credited_usd=_to_dollars(cum_credited),
                shortfall_usd=_to_dollars(shortfall),
                status=status,
            ))

            prev_required = cum_required
            prev_withheld = cum_withheld
            prev_needed = cum_needed
            prev_paid = cum_paid

        required_annual_usd = _to_dollars(required_annual_cents)
        total_to_schedule = sum(i.estimated_tax_due_usd for i in installments)

        notes = (
            f"ESTIMATED TAX SCHEDULE [{trader_id} - {tax_year}, {filing_status}]: "
            f"IRC 6654 required annual payment = ${required_annual_usd:,.2f} "
            f"(basis: {basis}). Current-year 90% target = "
            f"${_to_dollars(current_year_target_cents):,.2f}; prior-year target = "
            + (
                f"${prior_year_target_usd:,.2f} at {applied_pct:.0f}% "
                f"(AGI threshold ${threshold_usd:,.2f})"
                if prior_year_target_usd is not None
                else "unavailable (section 6654(d)(1)(B) flush text)"
            )
            + f". Withholding credited rateably under 6654(g) = "
            f"${current_year_withholding_usd:,.2f}; estimated tax to remit via "
            f"Form 1040-ES = ${total_to_schedule:,.2f}. Safe harbor compliant: "
            f"{compliant}."
        )
        if exception_basis:
            notes += f" Section 6654(e) exception may apply: {exception_basis}."
        logger.info(notes)

        return EstimatedTaxScheduleReport(
            trader_id=trader_id,
            tax_year=tax_year,
            filing_status=filing_status,
            prior_year_agi_usd=prior_year_agi_usd,
            prior_year_tax_usd=prior_year_tax_usd,
            projected_current_year_tax_usd=projected_current_year_tax_usd,
            current_year_withholding_usd=current_year_withholding_usd,
            high_agi_threshold_usd=threshold_usd,
            prior_year_safe_harbor_available=prior_year_available,
            applied_safe_harbor_pct=applied_pct,
            safe_harbor_prior_year_usd=prior_year_target_usd,
            safe_harbor_current_year_90pct_usd=_to_dollars(current_year_target_cents),
            required_annual_tax_payment_usd=required_annual_usd,
            safe_harbor_basis=basis,
            quarterly_installment_usd=installments[0].required_payment_usd,
            total_estimated_tax_to_schedule_usd=total_to_schedule,
            installments=installments,
            max_cumulative_shortfall_usd=_to_dollars(max_shortfall_cents),
            is_safe_harbor_compliant=compliant,
            penalty_exception_applies=exception_basis is not None,
            penalty_exception_basis=exception_basis,
            evaluated_as_of=as_of_date,
            audit_notes=notes,
        )
