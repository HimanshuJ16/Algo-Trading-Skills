"""
mark-to-market-election-for-active-traders-us: US federal tax engine comparing
IRC Section 475(f) mark-to-market (MTM) accounting against default capital
accounting for a trader in securities.

Statutory basis (26 U.S.C., current through the 2024 edition of the U.S. Code):

- **Sec. 475(f)(1)(A)** -- a person engaged in a trade or business as a trader in
  securities may elect to recognize gain or loss on any security held in
  connection with that trade or business at the close of the taxable year "as if
  such security were sold for its fair market value on the last business day of
  such taxable year."
- **Sec. 475(a)** (applied through 475(f)(1)(A)) -- "proper adjustment shall be
  made in the amount of any gain or loss subsequently realized." A lot that was
  marked at a prior year end therefore re-marks from *that mark*, not from its
  original purchase price. See ``TaxLot.prior_year_end_mark_price``.
- **Sec. 475(f)(1)(B)** -- the mark does **not** apply to a security held other
  than in connection with the trading business, provided it is clearly
  identified as such in the taxpayer's records (rules similar to 475(b)(2): on
  or before the close of the day it was acquired). See
  ``is_identified_investment_security``.
- **Sec. 475(f)(1)(D) -> 475(d)(1)** -- for elected securities, "section 1091
  shall not apply (and section 1092 shall apply) to any loss recognized under
  subsection (a)", and "the rules of sections 263(g), 263A, and 1256(a) shall
  not apply". The wash-sale waiver is therefore scoped to Sec. 475 securities:
  it does not reach identified investment securities, and it does **not** waive
  the Sec. 1092 straddle rules.
- **Sec. 475(d)(3)(A)** -- marked gain or loss is ordinary. IRS Topic No. 429
  directs it to **Form 4797, Part II**.
- **Sec. 475(f)(2)** -- traders in *commodities* need a **separate** election.
  Because 475(f)(2) pulls in rules similar to 475(f)(1)(D) -> 475(d)(1), which
  turns off Sec. 1256(a), making that election forfeits 60/40 treatment on
  Sec. 1256 contracts. A 475(f)(1) securities election alone reaches no
  Sec. 1256 contract; this engine routes such items out rather than absorbing
  them (see Limitations).
- **Sec. 1211(b)** -- without the election, capital losses are allowed only
  against capital gains plus the lower of $3,000 ($1,500 married filing
  separately) or the excess. **Sec. 1212(b)** carries the remainder forward
  indefinitely.
- **Sec. 461(l)** -- the ordinary loss an electing trader may deduct against
  non-business income is *not* unlimited. A noncorporate taxpayer's aggregate
  net business loss above the threshold amount is an excess business loss (EBL),
  disallowed for the year and carried forward as a net operating loss
  (Instructions for Form 461). The threshold is inflation-adjusted; only years
  this module can cite are tabulated in
  ``PUBLISHED_EXCESS_BUSINESS_LOSS_THRESHOLD_USD``.

Election mechanics are **not** modelled here and cannot be inferred from a
boolean: per IRS Topic No. 429 and Rev. Proc. 99-17 the election statement is
due by the unextended due date of the return for the year *preceding* the first
effective year (a new taxpayer instead places the statement in its books and
records within 2 months and 15 days of the start of the election year), and the
method change is perfected on Form 3115 with a Sec. 481(a) adjustment. Passing
``is_mtm_elected=True`` asserts that this already happened; the engine validates
only that the tax year is not earlier than the stated first effective year.

Limitations (documented, deliberate):

- **Not tax advice, and not a return preparer.** Output is an audit-support
  computation. Form 4797, Form 461, Form 3115 and Schedule D positions must be
  reviewed by a qualified US tax professional.
- **Federal only.** No state conformity: several states do not follow
  Sec. 475(f), Sec. 461(l), or the federal NOL rules.
- **Sec. 481(a) is out of scope.** The catch-up adjustment in the first elected
  year (and its four-year spread for a positive adjustment) is not computed.
- **Sec. 1092 straddles are out of scope.** Sec. 475(d)(1) expressly preserves
  them for elected securities; loss deferral under Sec. 1092 is not applied.
- **Sec. 1256 contracts are routed out, not priced.** Unless the Sec. 475(f)(2)
  commodities election is in effect, items flagged ``is_section_1256_contract``
  are excluded from both branches and reported separately -- 60/40 character and
  Form 6781 belong to ``section-1256-contract-tax-treatment-us-futures``.
- **Wash sales are an input, not a computation.** The non-elected branch consumes
  ``potential_wash_sale_loss`` per trade; the 61-day scan, replacement matching
  and basis adjustment belong to ``wash-sale-rule-tracking-us``.
- **Character within the capital branch is not split.** Short-term vs long-term
  is not tracked, so the Sec. 1212(b) carryforward is reported in total only.
- **Monetary amounts are IEEE-754 doubles.** Sums use ``math.fsum`` so a long
  trade blotter does not accumulate ordering error, and every reported figure is
  rounded to cents, but this is not decimal-exact accounting.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Filing statuses recognised for the Sec. 1211(b) and Sec. 461(l) thresholds.
FILING_STATUS_SINGLE = "SINGLE"
FILING_STATUS_MFJ = "MARRIED_FILING_JOINTLY"
FILING_STATUS_MFS = "MARRIED_FILING_SEPARATELY"
FILING_STATUS_HOH = "HEAD_OF_HOUSEHOLD"
FILING_STATUS_QSS = "QUALIFYING_SURVIVING_SPOUSE"

VALID_FILING_STATUSES = frozenset({
    FILING_STATUS_SINGLE, FILING_STATUS_MFJ, FILING_STATUS_MFS,
    FILING_STATUS_HOH, FILING_STATUS_QSS,
})

#: Sec. 1211(b): the lower of $3,000 -- $1,500 for a married individual filing a
#: separate return -- or the excess of capital losses over capital gains. Not
#: inflation-indexed; the statutory figures have been unchanged since 1978.
SECTION_1211B_LIMIT_USD = 3000.0
SECTION_1211B_LIMIT_MFS_USD = 1500.0

#: Sec. 461(l)(3)(A)(ii)(II) threshold amount, by taxable year beginning in that
#: year, for a return other than a joint return. Doubled for a joint return.
#: Only years backed by a citation are listed -- an unlisted year raises rather
#: than extrapolating an inflation adjustment that Treasury has not published.
#:   2025: Instructions for Form 461 (2025) -- $313,000 / $626,000.
#:   2026: Rev. Proc. 2025-32 sec. .31 -- $256,000 / $512,000.
PUBLISHED_EXCESS_BUSINESS_LOSS_THRESHOLD_USD = {
    2025: 313_000.0,
    2026: 256_000.0,
}

#: Sec. 475(f)(1) reaches securities only; these statuses record what happened to
#: the Sec. 461(l) test rather than silently reporting an unlimited deduction.
EBL_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
EBL_STATUS_NOT_EVALUATED = "NOT_EVALUATED_SEE_FORM_461"
EBL_STATUS_WITHIN_THRESHOLD = "WITHIN_THRESHOLD"
EBL_STATUS_LIMITED = "EXCESS_BUSINESS_LOSS_DISALLOWED"

FORM_4797_PART_II = "Form 4797 Part II (Ordinary Income)"
FORM_8949_SCHEDULE_D = "Form 8949 / Schedule D (Capital)"

STATUS_MTM = "MTM_ORDINARY_TAX_COMPUTED"
STATUS_CAPITAL = "CAPITAL_TAX_COMPUTED"


@dataclass
class RealizedTrade:
    """One closed position.

    ``sell_price`` and ``cost_basis`` are **per unit**, both in USD; the trade's
    realized P&L is ``(sell_price - cost_basis) * quantity``.

    For a lot that was marked to market at a prior year end under Sec. 475(a),
    ``cost_basis`` must already be that prior mark, not the original purchase
    price -- Sec. 475(a) requires "proper adjustment ... in the amount of any
    gain or loss subsequently realized", and supplying the original cost would
    tax the same appreciation twice.

    A **closed short** is expressed with ``sell_price`` as the short-sale
    proceeds per unit and ``cost_basis`` as the cover price per unit, which
    yields the correct sign without a negative quantity.
    """
    trade_id: str
    symbol: str
    sell_date: str
    sell_price: float
    cost_basis: float
    quantity: float
    potential_wash_sale_loss: float = 0.0
    #: Sec. 475(f)(1)(B): clearly identified in the records, on or before the day
    #: acquired, as held other than in connection with the trading business.
    #: Such a security stays capital and stays subject to Sec. 1091.
    is_identified_investment_security: bool = False
    #: Sec. 1256 contract (regulated futures, broad-based index option, foreign
    #: currency contract, dealer equity option). Reached only by a Sec. 475(f)(2)
    #: commodities election.
    is_section_1256_contract: bool = False


@dataclass
class TaxLot:
    """One position open at the close of the taxable year.

    ``buy_price`` and ``year_end_fmv_price`` are per unit, in USD.
    """
    lot_id: str
    symbol: str
    buy_date: str
    buy_price: float
    quantity: float
    year_end_fmv_price: float
    #: Fair market value at which this lot was marked at the **prior** year end.
    #: Sec. 475(a) requires the subsequent mark to run from that adjusted basis,
    #: so leaving this ``None`` for a lot carried across a year end double-counts
    #: the prior year's mark. ``None`` means the lot was acquired during the
    #: current taxable year and has never been marked.
    prior_year_end_mark_price: Optional[float] = None
    #: Open short position. ``quantity`` stays positive and ``buy_price`` carries
    #: the short-sale proceeds per unit; the mark is the mirror image, because a
    #: short gains when fair market value falls below basis.
    is_short: bool = False
    is_identified_investment_security: bool = False
    is_section_1256_contract: bool = False

    def section_475_basis(self) -> float:
        """Adjusted basis from which this lot is marked (Sec. 475(a))."""
        return self.buy_price if self.prior_year_end_mark_price is None \
            else self.prior_year_end_mark_price

    def mark_to_market_pl(self) -> float:
        """Sec. 475(a) year-end mark for this lot, signed for its direction."""
        move = self.year_end_fmv_price - self.section_475_basis()
        return (-move if self.is_short else move) * self.quantity


@dataclass
class MtmTaxReport:
    """Audit-support summary. All monetary fields are USD, rounded to cents."""
    is_mtm_elected: bool
    total_realized_pl_usd: float
    unrealized_mtm_pl_usd: float
    wash_sale_disallowed_usd: float
    total_reportable_taxable_pl_usd: float
    reportable_loss_deduction_usd: float
    tax_form_mapping: str
    status: str
    audit_notes: str
    #: Sec. 461(l): ordinary business loss disallowed for the year and carried
    #: forward as an NOL. Zero when the test was not applicable or not evaluated.
    excess_business_loss_disallowed_usd: float = 0.0
    excess_business_loss_status: str = EBL_STATUS_NOT_APPLICABLE
    excess_business_loss_threshold_usd: Optional[float] = None
    #: Sec. 1212(b): capital loss in excess of the Sec. 1211(b) allowance,
    #: carried forward indefinitely. Non-elected branch only.
    capital_loss_carryforward_usd: float = 0.0
    #: Realized P&L of securities identified under Sec. 475(f)(1)(B) as held for
    #: investment: capital, still subject to Sec. 1091, excluded from the mark.
    excluded_investment_pl_usd: float = 0.0
    #: Realized P&L of Sec. 1256 contracts not reached by the elections in
    #: effect. Belongs on Form 6781, not computed here.
    excluded_section_1256_pl_usd: float = 0.0
    marked_lot_count: int = 0
    #: Machine-readable audit trail: capped wash-sale inputs, routed-out items,
    #: unevaluated limitations. Empty means nothing needed flagging.
    warnings: List[str] = field(default_factory=list)


class MarkToMarketTaxEngine:
    """Compares IRC Sec. 475(f) mark-to-market against default capital accounting.

    Args:
        max_capital_loss_deduction_usd: Override for the Sec. 1211(b) allowance.
            ``None`` derives it from ``filing_status`` ($1,500 for married filing
            separately, otherwise $3,000).
        filing_status: One of ``VALID_FILING_STATUSES``. Drives both the
            Sec. 1211(b) allowance and the Sec. 461(l) threshold.
        excess_business_loss_threshold_usd: Override for the Sec. 461(l)(3)(A)
            threshold, used **as supplied** -- it is not doubled for a joint
            return. ``None`` looks the year up in
            ``PUBLISHED_EXCESS_BUSINESS_LOSS_THRESHOLD_USD``, which is doubled
            for a joint return.
    """

    def __init__(
        self,
        max_capital_loss_deduction_usd: Optional[float] = None,
        filing_status: str = FILING_STATUS_SINGLE,
        excess_business_loss_threshold_usd: Optional[float] = None,
    ) -> None:
        if filing_status not in VALID_FILING_STATUSES:
            raise ValueError(
                f"filing_status must be one of {sorted(VALID_FILING_STATUSES)}, "
                f"got {filing_status!r}"
            )
        self.filing_status = filing_status

        if max_capital_loss_deduction_usd is None:
            max_capital_loss_deduction_usd = (
                SECTION_1211B_LIMIT_MFS_USD
                if filing_status == FILING_STATUS_MFS
                else SECTION_1211B_LIMIT_USD
            )
        self._require_finite(max_capital_loss_deduction_usd, "max_capital_loss_deduction_usd")
        if max_capital_loss_deduction_usd < 0.0:
            raise ValueError("max_capital_loss_deduction_usd must not be negative")
        self.max_capital_loss_deduction_usd = float(max_capital_loss_deduction_usd)

        if excess_business_loss_threshold_usd is not None:
            self._require_finite(
                excess_business_loss_threshold_usd, "excess_business_loss_threshold_usd")
            if excess_business_loss_threshold_usd < 0.0:
                raise ValueError("excess_business_loss_threshold_usd must not be negative")
            excess_business_loss_threshold_usd = float(excess_business_loss_threshold_usd)
        self.excess_business_loss_threshold_usd = excess_business_loss_threshold_usd

    # ------------------------------------------------------------------ #
    # Validation helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _require_finite(value: float, label: str) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{label} must be a real number, got {type(value).__name__}")
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} must be finite, got {value!r}")

    def _validate_trade(self, trade: RealizedTrade) -> None:
        for label, value in (
            (f"trade {trade.trade_id}: sell_price", trade.sell_price),
            (f"trade {trade.trade_id}: cost_basis", trade.cost_basis),
            (f"trade {trade.trade_id}: quantity", trade.quantity),
            (f"trade {trade.trade_id}: potential_wash_sale_loss", trade.potential_wash_sale_loss),
        ):
            self._require_finite(value, label)
        if trade.quantity <= 0.0:
            raise ValueError(
                f"trade {trade.trade_id}: quantity must be positive "
                f"(got {trade.quantity}); express a short close by its "
                f"sell_price/cost_basis, not by a negative quantity"
            )
        if trade.potential_wash_sale_loss < 0.0:
            raise ValueError(
                f"trade {trade.trade_id}: potential_wash_sale_loss is a disallowed-loss "
                f"magnitude and must not be negative (got {trade.potential_wash_sale_loss})"
            )
        if trade.is_identified_investment_security and trade.is_section_1256_contract:
            raise ValueError(
                f"trade {trade.trade_id}: a Sec. 1256 contract is not a security under "
                f"Sec. 475(c)(2) and cannot also be an identified investment security"
            )

    def _validate_lot(self, lot: TaxLot) -> None:
        for label, value in (
            (f"lot {lot.lot_id}: buy_price", lot.buy_price),
            (f"lot {lot.lot_id}: quantity", lot.quantity),
            (f"lot {lot.lot_id}: year_end_fmv_price", lot.year_end_fmv_price),
        ):
            self._require_finite(value, label)
        if lot.prior_year_end_mark_price is not None:
            self._require_finite(
                lot.prior_year_end_mark_price, f"lot {lot.lot_id}: prior_year_end_mark_price")
        if lot.quantity <= 0.0:
            raise ValueError(
                f"lot {lot.lot_id}: quantity must be positive (got {lot.quantity})")
        if lot.is_identified_investment_security and lot.is_section_1256_contract:
            raise ValueError(
                f"lot {lot.lot_id}: a Sec. 1256 contract cannot also be an identified "
                f"investment security"
            )

    # ------------------------------------------------------------------ #
    # Thresholds
    # ------------------------------------------------------------------ #
    def resolve_excess_business_loss_threshold(
        self, tax_year: Optional[int]
    ) -> Optional[float]:
        """Sec. 461(l)(3)(A) threshold for ``tax_year``, doubled for a joint return.

        A constructor override short-circuits the table and is returned as
        supplied (never doubled). Returns ``None`` when no citable amount exists
        for ``tax_year``, so the caller sees ``NOT_EVALUATED`` rather than an
        invented inflation figure.
        """
        if self.excess_business_loss_threshold_usd is not None:
            return self.excess_business_loss_threshold_usd
        if tax_year is None:
            return None
        base = PUBLISHED_EXCESS_BUSINESS_LOSS_THRESHOLD_USD.get(tax_year)
        if base is None:
            return None
        return base * 2.0 if self.filing_status == FILING_STATUS_MFJ else base

    # ------------------------------------------------------------------ #
    # Core calculation
    # ------------------------------------------------------------------ #
    def calculate_tax_liability(
        self,
        is_mtm_elected: bool,
        realized_trades: List[RealizedTrade],
        open_tax_lots: List[TaxLot],
        tax_year: Optional[int] = None,
        election_effective_first_tax_year: Optional[int] = None,
        elects_commodities_475f2: bool = False,
        other_net_business_income_usd: float = 0.0,
    ) -> MtmTaxReport:
        """Compute reportable P&L, loss deductibility and IRS form mapping.

        Args:
            is_mtm_elected: Asserts a **perfected** Sec. 475(f)(1) election --
                Rev. Proc. 99-17 statement filed by the unextended due date of
                the prior year's return (or, for a new taxpayer, placed in the
                books and records within 2 months and 15 days of the start of
                the election year) and the method change filed on Form 3115.
            tax_year: Calendar year the return covers. Required to look up the
                Sec. 461(l) threshold and to validate the election year.
            election_effective_first_tax_year: First year the election applies.
                Supplying it with a ``tax_year`` earlier than it is an error --
                a Sec. 475(f) election is never retroactive.
            elects_commodities_475f2: A separate Sec. 475(f)(2) commodities
                election is in effect. This pulls Sec. 1256 contracts into
                ordinary MTM income and forfeits their 60/40 treatment
                (Sec. 475(f)(1)(D) -> Sec. 475(d)(1) disapplies Sec. 1256(a)).
            other_net_business_income_usd: Net income or loss from the
                taxpayer's *other* trades or businesses. Sec. 461(l) is tested on
                the aggregate, so omitting a second business overstates the
                deductible trading loss.

        Raises:
            TypeError: A monetary or quantity input is not a real number.
            ValueError: A quantity is non-positive, an amount is not finite, the
                election year post-dates the tax year, or a flag combination is
                incoherent.
        """
        if not isinstance(is_mtm_elected, bool):
            raise TypeError(f"is_mtm_elected must be a bool, got {type(is_mtm_elected).__name__}")
        self._require_finite(other_net_business_income_usd, "other_net_business_income_usd")

        if tax_year is not None and not isinstance(tax_year, int):
            raise TypeError(f"tax_year must be an int, got {type(tax_year).__name__}")
        if (
            tax_year is not None
            and election_effective_first_tax_year is not None
            and tax_year < election_effective_first_tax_year
        ):
            raise ValueError(
                f"tax_year {tax_year} precedes election_effective_first_tax_year "
                f"{election_effective_first_tax_year}: a Sec. 475(f) election applies to "
                f"the year of election and later years only (Sec. 475(f)(3))"
            )

        for trade in realized_trades:
            self._validate_trade(trade)
        for lot in open_tax_lots:
            self._validate_lot(lot)

        warnings: List[str] = []

        if is_mtm_elected:
            return self._compute_mtm(
                realized_trades, open_tax_lots, tax_year, elects_commodities_475f2,
                other_net_business_income_usd, warnings,
            )
        return self._compute_capital(
            realized_trades, open_tax_lots, elects_commodities_475f2, warnings)

    # ------------------------------------------------------------------ #
    def _partition_trades(
        self,
        realized_trades: List[RealizedTrade],
        elects_commodities_475f2: bool,
        warnings: List[str],
    ) -> Tuple[List[RealizedTrade], float, float]:
        """Split trades into Sec. 475-elected, identified-investment and Sec. 1256."""
        elected: List[RealizedTrade] = []
        investment_pl: List[float] = []
        section_1256_pl: List[float] = []

        for trade in realized_trades:
            pl = self._trade_pl(trade)
            if trade.is_section_1256_contract and not elects_commodities_475f2:
                section_1256_pl.append(pl)
                continue
            if trade.is_identified_investment_security:
                investment_pl.append(pl)
                continue
            elected.append(trade)

        if section_1256_pl:
            warnings.append(
                f"{len(section_1256_pl)} Sec. 1256 contract trade(s) excluded: no "
                f"Sec. 475(f)(2) commodities election in effect. Report 60/40 character on "
                f"Form 6781 (see skill section-1256-contract-tax-treatment-us-futures)."
            )
        if investment_pl:
            warnings.append(
                f"{len(investment_pl)} trade(s) identified under Sec. 475(f)(1)(B) as held "
                f"for investment: capital treatment retained and Sec. 1091 still applies "
                f"(see skill wash-sale-rule-tracking-us). Not included in ordinary MTM P&L."
            )
        return elected, math.fsum(investment_pl), math.fsum(section_1256_pl)

    @staticmethod
    def _trade_pl(trade: RealizedTrade) -> float:
        return (trade.sell_price - trade.cost_basis) * trade.quantity

    def _allowed_wash_sale_disallowance(
        self, trade: RealizedTrade, warnings: List[str]
    ) -> float:
        """Sec. 1091(a) disallows a *loss*, and never more than the loss realized."""
        if trade.potential_wash_sale_loss == 0.0:
            return 0.0
        realized_loss = max(0.0, -self._trade_pl(trade))
        if trade.potential_wash_sale_loss > realized_loss:
            msg = (
                f"trade {trade.trade_id}: potential_wash_sale_loss "
                f"${trade.potential_wash_sale_loss:,.2f} exceeds the realized loss "
                f"${realized_loss:,.2f}; capped at the realized loss per Sec. 1091(a)"
            )
            logger.warning(msg)
            warnings.append(msg)
            return realized_loss
        return trade.potential_wash_sale_loss

    # ------------------------------------------------------------------ #
    def _compute_mtm(
        self,
        realized_trades: List[RealizedTrade],
        open_tax_lots: List[TaxLot],
        tax_year: Optional[int],
        elects_commodities_475f2: bool,
        other_net_business_income_usd: float,
        warnings: List[str],
    ) -> MtmTaxReport:
        elected_trades, investment_pl, section_1256_pl = self._partition_trades(
            realized_trades, elects_commodities_475f2, warnings)

        realized_pl = math.fsum(self._trade_pl(t) for t in elected_trades)

        # Sec. 475(f)(1)(D) -> Sec. 475(d)(1): Sec. 1091 does not apply to a loss
        # recognized under Sec. 475(a). Sec. 1092 straddle deferral is preserved
        # by the same sentence and is out of scope (see module docstring).
        wash_sale_disallowed = 0.0
        if any(t.potential_wash_sale_loss > 0.0 for t in elected_trades):
            warnings.append(
                "potential_wash_sale_loss supplied on Sec. 475-elected trades was ignored: "
                "Sec. 475(d)(1) disapplies Sec. 1091 to losses recognized under Sec. 475(a)."
            )

        marked_lots = [
            lot for lot in open_tax_lots
            if not lot.is_identified_investment_security
            and (elects_commodities_475f2 or not lot.is_section_1256_contract)
        ]
        skipped_lots = len(open_tax_lots) - len(marked_lots)
        if skipped_lots:
            warnings.append(
                f"{skipped_lots} open lot(s) not marked: identified investment securities "
                f"(Sec. 475(f)(1)(B)) and/or Sec. 1256 contracts outside the elections in effect."
            )
        unmarked_carryover = [
            lot.lot_id for lot in marked_lots
            if lot.prior_year_end_mark_price is None and tax_year is not None
            and len(lot.buy_date) >= 4 and lot.buy_date[:4].isdigit()
            and int(lot.buy_date[:4]) < tax_year
        ]
        if unmarked_carryover:
            warnings.append(
                f"lot(s) {', '.join(unmarked_carryover)} were acquired before tax year "
                f"{tax_year} but carry no prior_year_end_mark_price; Sec. 475(a) requires the "
                f"mark to run from the prior year-end mark, so basis may be overstated."
            )

        # Sec. 475(a)/(f)(1)(A): mark from the Sec. 475-adjusted basis.
        unrealized_mtm_pl = math.fsum(lot.mark_to_market_pl() for lot in marked_lots)

        total_reportable_pl = round(realized_pl + unrealized_mtm_pl, 2)

        (loss_deduction, ebl_disallowed, ebl_status, ebl_threshold) = \
            self._apply_excess_business_loss(
                total_reportable_pl, tax_year, other_net_business_income_usd, warnings)

        form_map = FORM_4797_PART_II
        notes = (
            f"SECTION 475(f) MTM ELECTED: Realized P&L = ${realized_pl:,.2f}, "
            f"Year-End MTM P&L = ${unrealized_mtm_pl:,.2f} over {len(marked_lots)} lot(s). "
            f"Total Ordinary P&L = ${total_reportable_pl:,.2f}. Sec. 1091 disapplied per "
            f"Sec. 475(d)(1). Reported on {form_map}. Sec. 461(l): {ebl_status}"
            + (f" (threshold ${ebl_threshold:,.2f}, "
               f"${ebl_disallowed:,.2f} deferred as NOL)." if ebl_threshold is not None else ".")
        )
        logger.info(notes)

        return MtmTaxReport(
            is_mtm_elected=True,
            total_realized_pl_usd=round(realized_pl, 2),
            unrealized_mtm_pl_usd=round(unrealized_mtm_pl, 2),
            wash_sale_disallowed_usd=wash_sale_disallowed,
            total_reportable_taxable_pl_usd=total_reportable_pl,
            reportable_loss_deduction_usd=round(loss_deduction, 2),
            tax_form_mapping=form_map,
            status=STATUS_MTM,
            audit_notes=notes,
            excess_business_loss_disallowed_usd=round(ebl_disallowed, 2),
            excess_business_loss_status=ebl_status,
            excess_business_loss_threshold_usd=ebl_threshold,
            capital_loss_carryforward_usd=0.0,
            excluded_investment_pl_usd=round(investment_pl, 2),
            excluded_section_1256_pl_usd=round(section_1256_pl, 2),
            marked_lot_count=len(marked_lots),
            warnings=warnings,
        )

    def _apply_excess_business_loss(
        self,
        trading_pl: float,
        tax_year: Optional[int],
        other_net_business_income_usd: float,
        warnings: List[str],
    ) -> Tuple[float, float, str, Optional[float]]:
        """Sec. 461(l): limit the current-year deduction of an aggregate business loss.

        Returns ``(loss_deduction, disallowed, status, threshold)`` where
        ``loss_deduction`` is negative or zero.
        """
        if trading_pl >= 0.0:
            return 0.0, 0.0, EBL_STATUS_NOT_APPLICABLE, None

        threshold = self.resolve_excess_business_loss_threshold(tax_year)
        if threshold is None:
            warnings.append(
                "Sec. 461(l) excess business loss NOT evaluated: no threshold supplied and no "
                f"published amount for tax_year={tax_year!r}. The reported deduction is the "
                "gross ordinary loss and may overstate the currently deductible amount; "
                "complete Form 461."
            )
            return trading_pl, 0.0, EBL_STATUS_NOT_EVALUATED, None

        aggregate_business_pl = trading_pl + other_net_business_income_usd
        if aggregate_business_pl >= -threshold:
            return trading_pl, 0.0, EBL_STATUS_WITHIN_THRESHOLD, threshold

        # Sec. 461(l)(3)(A): the excess of the aggregate loss over the threshold is
        # disallowed this year and becomes an NOL carryover (Instructions, Form 461).
        disallowed = -aggregate_business_pl - threshold
        allowed = trading_pl + disallowed
        warnings.append(
            f"Sec. 461(l): ${disallowed:,.2f} of the aggregate business loss exceeds the "
            f"${threshold:,.2f} threshold, is disallowed for the year, and carries forward "
            f"as a net operating loss (80%-of-taxable-income limited on use)."
        )
        return allowed, disallowed, EBL_STATUS_LIMITED, threshold

    # ------------------------------------------------------------------ #
    def _compute_capital(
        self,
        realized_trades: List[RealizedTrade],
        open_tax_lots: List[TaxLot],
        elects_commodities_475f2: bool,
        warnings: List[str],
    ) -> MtmTaxReport:
        # Without a Sec. 475(f)(1) election every security is capital, so the
        # Sec. 475(f)(1)(B) investment identification is irrelevant here and those
        # trades net in exactly like the rest. Sec. 1256 contracts are always
        # routed out: their 60/40 character (or, under a Sec. 475(f)(2) election,
        # their ordinary MTM treatment) cannot be expressed in a capital report.
        capital_trades = [t for t in realized_trades if not t.is_section_1256_contract]
        section_1256_trades = [t for t in realized_trades if t.is_section_1256_contract]
        section_1256_pl = math.fsum(self._trade_pl(t) for t in section_1256_trades)
        if section_1256_trades:
            warnings.append(
                f"{len(section_1256_trades)} Sec. 1256 contract trade(s) excluded from the "
                f"capital computation: "
                + ("a Sec. 475(f)(2) commodities election is in effect, so they are ordinary "
                   "MTM income and must be reported separately."
                   if elects_commodities_475f2 else
                   "report their 60/40 character on Form 6781 (see skill "
                   "section-1256-contract-tax-treatment-us-futures).")
            )

        realized_pl = math.fsum(self._trade_pl(t) for t in capital_trades)

        # Sec. 1091(d): the disallowed loss is added back to income this year and
        # rolls into the replacement lot's basis (computed by wash-sale-rule-tracking-us).
        wash_sale_disallowed = math.fsum(
            self._allowed_wash_sale_disallowance(t, warnings) for t in capital_trades
        )

        # Sec. 1256(a) marks open Sec. 1256 contracts even absent any election;
        # securities are not marked without a Sec. 475(f) election.
        if any(lot.is_section_1256_contract for lot in open_tax_lots):
            warnings.append(
                "Open Sec. 1256 contract lot(s) were not marked here: Sec. 1256(a) requires a "
                "year-end mark independent of any Sec. 475(f) election. Compute it in "
                "section-1256-contract-tax-treatment-us-futures or this return understates income."
            )
        unrealized_mtm_pl = 0.0

        net_capital_pl = round(realized_pl + wash_sale_disallowed, 2)

        # Sec. 1211(b): allowed only to the extent of capital gains, plus the lower
        # of the statutory allowance or the excess. Sec. 1212(b) carries the rest forward.
        if net_capital_pl < 0.0:
            loss_deduction = max(net_capital_pl, -self.max_capital_loss_deduction_usd)
            capital_loss_carryforward = round(net_capital_pl - loss_deduction, 2)
        else:
            loss_deduction = 0.0
            capital_loss_carryforward = 0.0

        form_map = FORM_8949_SCHEDULE_D
        notes = (
            f"STANDARD CAPITAL ACCOUNTING: Realized P&L = ${realized_pl:,.2f}, "
            f"Wash Sale Disallowed = ${wash_sale_disallowed:,.2f} (Sec. 1091). "
            f"Net Capital P&L = ${net_capital_pl:,.2f}. Sec. 1211(b) deduction against ordinary "
            f"income = ${abs(loss_deduction):,.2f}; Sec. 1212(b) carryforward = "
            f"${abs(capital_loss_carryforward):,.2f}. Reported on {form_map}."
        )
        logger.info(notes)

        return MtmTaxReport(
            is_mtm_elected=False,
            total_realized_pl_usd=round(realized_pl, 2),
            unrealized_mtm_pl_usd=unrealized_mtm_pl,
            wash_sale_disallowed_usd=round(wash_sale_disallowed, 2),
            total_reportable_taxable_pl_usd=net_capital_pl,
            reportable_loss_deduction_usd=round(loss_deduction, 2),
            tax_form_mapping=form_map,
            status=STATUS_CAPITAL,
            audit_notes=notes,
            excess_business_loss_disallowed_usd=0.0,
            excess_business_loss_status=EBL_STATUS_NOT_APPLICABLE,
            excess_business_loss_threshold_usd=None,
            capital_loss_carryforward_usd=capital_loss_carryforward,
            excluded_investment_pl_usd=0.0,
            excluded_section_1256_pl_usd=round(section_1256_pl, 2),
            marked_lot_count=0,
            warnings=warnings,
        )
