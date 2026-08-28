"""
section-1256-contract-tax-treatment-us-futures: US federal tax engine for IRC
Sec. 1256 contracts -- the mandatory year-end mark, the 60/40 character split,
the Sec. 1212(c) net section 1256 contracts loss carryback, and the mapping onto
IRS Form 6781, Part I.

Statutory and administrative basis:

- **Sec. 1256(a)(1)** -- "each section 1256 contract held by the taxpayer at the
  close of the taxable year shall be treated as sold for its fair market value on
  the last business day of such taxable year (and any gain or loss shall be taken
  into account for the taxable year)". The mark runs to the **last business day**,
  which is not always December 31.
- **Sec. 1256(a)(2)** -- "proper adjustment shall be made in the amount of any
  gain or loss subsequently realized for gain or loss taken into account by
  reason of paragraph (1)". A contract carried across a year end must therefore
  have the prior year's mark removed from the figure reported this year. See
  ``Section1256Trade.prior_year_end_cumulative_mark_usd``.
- **Sec. 1256(a)(3)** -- gain or loss is "short-term capital gain or loss, to the
  extent of 40 percent" and "long-term capital gain or loss, to the extent of 60
  percent". The Instructions for Form 6781 add that this holds "regardless of how
  long the contracts were held", and that the wash sale rules do not apply.
- **Sec. 1256(a)(4)** -- Sec. 1092 and Sec. 263(g) are turned off only where
  *every* offsetting position in the straddle is a Sec. 1256 contract. A mixed
  straddle is therefore not safe to run through Part I; see Limitations.
- **Sec. 1256(b)(1)** -- the five qualifying types: regulated futures contract,
  foreign currency contract, nonequity option, dealer equity option, dealer
  securities futures contract. **Sec. 1256(b)(2)** excludes any securities futures
  contract or option on one (unless a dealer securities futures contract) and any
  "interest rate swap, currency swap, basis swap, interest rate cap, interest rate
  floor, commodity swap, equity swap, equity index swap, credit default swap, or
  similar agreement".
- **Sec. 1256(g)(3), (g)(6)** -- a nonequity option is "any listed option which is
  not an equity option"; an equity option is an option on stock "or the value of
  which is determined directly or indirectly by reference to any stock or any
  narrow-based security index". Pub. 550 describes nonequity options as including
  broad-based stock index options.
- **Sec. 1256(e); Instructions for Form 6781** -- the mark "doesn't apply if you
  properly and timely identified a section 1256 contract as a hedge", and "the
  gain or loss on a hedging transaction is treated as ordinary income or loss",
  entered as a Form 6781 line 4 adjustment rather than in the 60/40 computation.
- **Sec. 1212(c); Form 6781 box D** -- a taxpayer other than a corporation, estate
  or trust may elect to carry a net section 1256 contracts loss back 3 years,
  against Sec. 1256 gains only, earliest year first, "40 percent ... short-term"
  and "60 percent ... long-term", and only to the extent it does not increase or
  produce a net operating loss.
- **Sec. 1211(b) / Sec. 1212(b)** -- whatever survives the carryback is an
  ordinary capital loss: allowed against capital gains, then the lower of $3,000
  ($1,500 married filing separately) or the excess, remainder carried forward
  indefinitely.
- **Sec. 1411(c)(1)(A)(ii)** -- Sec. 1256 gain of a taxpayer trading financial
  instruments or commodities is net investment income, so the 3.8% NIIT can apply
  on top of the capital rates. Supply it via ``net_investment_income_tax_rate``;
  it is **not** applied by default because it turns on a MAGI threshold this
  engine does not see.
- **Sec. 1402(i); Instructions for Form 6781** -- options and commodities dealers
  take Sec. 1256 trading gain or loss into account in net earnings from
  self-employment. Non-dealers do not.

Form 6781, Part I line map produced by this module::

    line 5  net_section_1256_pnl_usd        (realized + year-end mark, adjusted)
    line 6  carryback_elected_usd           (positive number; box D)
    line 7  net_after_carryback_usd
    line 8  short_term_40_pct_usd  -> Schedule D (Form 1040) line 4
    line 9  long_term_60_pct_usd   -> Schedule D (Form 1040) line 11

Limitations (documented, deliberate):

- **Not tax advice and not a return preparer.** The output is audit support for a
  return position. Have it reviewed by a qualified US tax professional.
- **US federal only.** No state conformity; several states diverge on capital
  loss carryovers and carrybacks.
- **Eligibility is an input, never an inference.** The engine does not decide
  whether an instrument is a Sec. 1256 contract from its symbol. Whether a given
  listed option is a nonequity option under Sec. 1256(g)(3) -- and whether a
  retail forex position is a Sec. 1256(g)(2) foreign currency contract -- are
  legal determinations the caller asserts via ``contract_type``.
- **Straddles are routed out, not priced.** Sec. 1092 loss deferral, the
  Sec. 1256(d) mixed straddle election and Form 6781 Part II are out of scope.
  Flag mixed-straddle legs with ``is_part_of_mixed_straddle`` and they are
  excluded with a warning rather than silently absorbed into Part I.
- **The Sec. 1212(c) carryback is modelled on its first prong only.** Form 6781
  box D also caps the net section 1256 contracts loss at the capital loss
  carryovers that would arise if line 6 were zero, and caps the amount reaching
  any single carryback year at that year's Schedule D line 16 gain, with no
  increase to an NOL. Those require the full prior-year Schedule D; the engine
  takes the aggregate Sec. 1256 gain the caller supplies and warns.
- **Sec. 988 interaction is not modelled.** A Sec. 988(a)(1)(B) or
  Sec. 988(c)(1)(D) election changes the answer for currency contracts; see
  ``currency-gain-loss-tax-treatment-for-forex-trading``.
- **Monetary amounts are IEEE-754 doubles.** Sums use ``math.fsum`` and reported
  figures are rounded to cents, but this is not decimal-exact accounting.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Sec. 1256(a)(3): 60 percent long-term, 40 percent short-term, regardless of
#: holding period. Form 6781 lines 9 and 8 respectively.
SECTION_1256_LONG_TERM_FRACTION = 0.60
SECTION_1256_SHORT_TERM_FRACTION = 0.40

#: Sec. 1211(b). Statutory, not inflation-indexed.
SECTION_1211B_LIMIT_USD = 3000.0
SECTION_1211B_LIMIT_MFS_USD = 1500.0

#: Sec. 1212(c)(1): carryback to "each of the 3 taxable years preceding the loss
#: year". Exposed so callers and tests do not hard-code the number.
SECTION_1212C_CARRYBACK_YEARS = 3

FILING_STATUS_SINGLE = "SINGLE"
FILING_STATUS_MFJ = "MARRIED_FILING_JOINTLY"
FILING_STATUS_MFS = "MARRIED_FILING_SEPARATELY"
FILING_STATUS_HOH = "HEAD_OF_HOUSEHOLD"
FILING_STATUS_QSS = "QUALIFYING_SURVIVING_SPOUSE"

VALID_FILING_STATUSES = frozenset({
    FILING_STATUS_SINGLE, FILING_STATUS_MFJ, FILING_STATUS_MFS,
    FILING_STATUS_HOH, FILING_STATUS_QSS,
})

#: Top statutory rates used as defaults. The 37% top ordinary/short-term rate for
#: 2026 is confirmed by IR-2025-103 (Rev. Proc. 2025-32); 20% is the top
#: Sec. 1(h)(1)(D) adjusted net capital gain rate. Both are *maxima* -- pass the
#: taxpayer's actual marginal rates for anything other than a worst-case estimate.
DEFAULT_MAX_LONG_TERM_RATE = 0.20
DEFAULT_MAX_SHORT_TERM_RATE = 0.37
#: Sec. 1411 net investment income tax. Not applied unless the caller supplies it,
#: because it turns on a MAGI threshold outside this engine's inputs.
NET_INVESTMENT_INCOME_TAX_RATE = 0.038

FORM_6781_LINE_8_DESTINATION = (
    "Form 6781 Part I line 8 (40% short-term) -> Schedule D (Form 1040) line 4")
FORM_6781_LINE_9_DESTINATION = (
    "Form 6781 Part I line 9 (60% long-term) -> Schedule D (Form 1040) line 11")


class Section1256ContractType(str, Enum):
    """Contract classification, asserted by the caller -- never inferred here.

    The first five members are the Sec. 1256(b)(1) qualifying types. The
    remaining members name the common exclusions, so a non-qualifying position
    can be carried through the blotter and reported instead of dropped.
    """

    #: Sec. 1256(b)(1)(A) / (g)(1): CME, CBOT, NYMEX, ICE futures (ES, NQ, CL, ZB).
    REGULATED_FUTURES = "REGULATED_FUTURES"
    #: Sec. 1256(b)(1)(B) / (g)(2): interbank foreign currency contract. Whether a
    #: retail forex position qualifies is unsettled -- see
    #: `currency-gain-loss-tax-treatment-for-forex-trading`.
    FOREIGN_CURRENCY_CONTRACT = "FOREIGN_CURRENCY_CONTRACT"
    #: Sec. 1256(b)(1)(C) / (g)(3): "any listed option which is not an equity
    #: option". Broad-based stock index options (SPX, NDX, RUT, VIX) are the
    #: retail case (Pub. 550).
    NONEQUITY_OPTION = "NONEQUITY_OPTION"
    #: Sec. 1256(b)(1)(D) / (g)(4): available only to a registered options dealer.
    DEALER_EQUITY_OPTION = "DEALER_EQUITY_OPTION"
    #: Sec. 1256(b)(1)(E): available only to a securities futures dealer.
    DEALER_SECURITIES_FUTURES_CONTRACT = "DEALER_SECURITIES_FUTURES_CONTRACT"

    #: Sec. 1256(g)(6): option on stock, or on a narrow-based security index.
    #: Options on single stocks and on ETF shares (SPY, QQQ, IWM) sit here -- an
    #: ETF share is stock, so an option on it is an equity option.
    EQUITY_OPTION = "EQUITY_OPTION"
    #: Sec. 1256(b)(2)(A): securities futures contract, or option on one, held by
    #: someone other than a dealer.
    SECURITIES_FUTURES_CONTRACT = "SECURITIES_FUTURES_CONTRACT"
    #: Sec. 1256(b)(2)(B): interest rate / currency / basis / commodity / equity /
    #: equity index / credit default swap, cap, floor, or similar agreement.
    SWAP_OR_NOTIONAL_PRINCIPAL_CONTRACT = "SWAP_OR_NOTIONAL_PRINCIPAL_CONTRACT"
    #: Anything else the caller has determined is outside Sec. 1256.
    OTHER_NON_SECTION_1256 = "OTHER_NON_SECTION_1256"


#: The Sec. 1256(b)(1) qualifying set. Membership drives Part I inclusion.
SECTION_1256_QUALIFYING_TYPES = frozenset({
    Section1256ContractType.REGULATED_FUTURES,
    Section1256ContractType.FOREIGN_CURRENCY_CONTRACT,
    Section1256ContractType.NONEQUITY_OPTION,
    Section1256ContractType.DEALER_EQUITY_OPTION,
    Section1256ContractType.DEALER_SECURITIES_FUTURES_CONTRACT,
})

#: Types that presuppose registered dealer status (Sec. 1256(g)(4), (g)(9)) and
#: that also pull Sec. 1256 P&L into self-employment earnings under Sec. 1402(i).
DEALER_ONLY_TYPES = frozenset({
    Section1256ContractType.DEALER_EQUITY_OPTION,
    Section1256ContractType.DEALER_SECURITIES_FUTURES_CONTRACT,
})


def is_section_1256_contract(contract_type: Section1256ContractType) -> bool:
    """True when ``contract_type`` is one of the five Sec. 1256(b)(1) types."""
    return contract_type in SECTION_1256_QUALIFYING_TYPES


@dataclass
class Section1256Trade:
    """One contract position for a single tax year.

    ``realized_pnl_usd`` is gain or loss on the portion closed or terminated
    during the year; ``year_end_mark_pnl_usd`` is the Sec. 1256(a)(1) mark on the
    portion still held at the close of the year. The two must describe disjoint
    portions of the position -- overlapping them double-counts.

    **Sec. 1256(a)(2) adjustment.** ``prior_year_end_cumulative_mark_usd`` is the
    gain or loss on *this contract* already taken into account at the prior year
    end. Leave it ``None`` for a contract entered into during this tax year. When
    it is supplied, ``realized_pnl_usd`` and ``year_end_mark_pnl_usd`` must be
    measured **from original cost** (inception-to-date), and the engine subtracts
    the prior mark to make the "proper adjustment" the statute requires. A
    Form 1099-B box 11 figure is already adjusted by the broker -- do **not** also
    set this field for such a trade, or the prior mark is removed twice.
    """

    trade_id: str
    symbol: str
    contract_type: Section1256ContractType
    realized_pnl_usd: float
    year_end_mark_pnl_usd: float = 0.0
    is_open_at_year_end: bool = False
    prior_year_end_cumulative_mark_usd: Optional[float] = None
    #: Sec. 1256(e): identified as a hedge before the close of the day the
    #: transaction was entered into. Excluded from the mark; gain or loss is
    #: ordinary and belongs on Form 6781 line 4, not in the 60/40 split.
    is_identified_hedging_transaction: bool = False
    #: A leg of a straddle in which at least one but not all positions are
    #: Sec. 1256 contracts. Sec. 1256(a)(4) does not shelter it from Sec. 1092,
    #: so it is routed out rather than run through Part I.
    is_part_of_mixed_straddle: bool = False


@dataclass
class Form6781TaxSummary:
    """Form 6781 Part I audit support. Monetary fields are USD, rounded to cents."""

    #: Gain or loss on contracts closed or terminated during the year.
    total_realized_pnl_usd: float
    #: Sec. 1256(a)(1) mark on contracts held at the close of the year.
    total_year_end_mark_pnl_usd: float
    #: Sec. 1256(a)(2): prior-year marks removed. Negative reduces this year's P&L.
    prior_year_mark_adjustment_usd: float
    #: Form 6781 line 5.
    net_section_1256_pnl_usd: float
    #: Form 6781 line 6, entered as a positive number when box D is checked.
    carryback_elected_usd: float
    #: Form 6781 line 7.
    net_after_carryback_usd: float
    #: Form 6781 line 8.
    short_term_40_pct_usd: float
    #: Form 6781 line 9.
    long_term_60_pct_usd: float
    form_6781_line_8_destination: str
    form_6781_line_9_destination: str

    #: Sec. 1212(c)(4) first prong: Sec. 1256 losses in excess of Sec. 1256 gains
    #: plus the Sec. 1211(b) allowance. Zero in a gain year.
    net_section_1256_contracts_loss_usd: float = 0.0
    #: Sec. 1211(b): loss absorbed by other capital gains, then the $3,000/$1,500
    #: allowance against ordinary income.
    loss_offset_against_other_capital_gains_usd: float = 0.0
    ordinary_income_deduction_usd: float = 0.0
    #: Sec. 1212(b): carried forward indefinitely.
    capital_loss_carryforward_usd: float = 0.0

    #: Estimated federal tax on a net gain, or estimated tax benefit of a net loss.
    estimated_tax_usd: float = 0.0
    estimated_tax_if_all_short_term_usd: float = 0.0
    tax_savings_vs_short_term_usd: float = 0.0
    estimated_loss_tax_benefit_usd: float = 0.0
    blended_rate_applied: float = 0.0

    #: Amounts kept out of Part I, each with a warning. Sec. 1256(e) hedging P&L
    #: is ordinary; mixed-straddle legs need Form 6781 Part II; non-Sec. 1256
    #: positions belong on Form 8949 / Schedule D by their own holding period.
    excluded_hedging_pnl_usd: float = 0.0
    excluded_mixed_straddle_pnl_usd: float = 0.0
    excluded_non_section_1256_pnl_usd: float = 0.0
    section_1256_trade_count: int = 0
    excluded_trade_count: int = 0

    #: Machine-readable audit trail. An empty list is the only clean result.
    warnings: List[str] = field(default_factory=list)
    audit_notes: str = ""


class Section1256ContractTaxTreatmentUsFuturesEngine:
    """IRC Sec. 1256 mark-to-market, 60/40 character split and Form 6781 Part I.

    Args:
        filing_status: One of ``VALID_FILING_STATUSES``. Drives the Sec. 1211(b)
            allowance ($1,500 for married filing separately).
        long_term_capital_gains_rate: Marginal rate applied to the 60% long-term
            portion. Defaults to the top Sec. 1(h) rate; pass the taxpayer's own.
        short_term_capital_gains_rate: Marginal ordinary rate applied to the 40%
            short-term portion. Defaults to the top 2026 rate.
        net_investment_income_tax_rate: Sec. 1411 NIIT, ``0.0`` by default because
            it depends on a MAGI threshold this engine cannot see. Pass
            ``NET_INVESTMENT_INCOME_TAX_RATE`` (0.038) for a taxpayer over the
            threshold. It applies to the whole net gain and is character-blind, so
            it raises the estimated tax without changing the 60/40 saving.

    Rates are decimal fractions. ``37.0`` is rejected rather than silently read as
    37%, which would overstate tax a hundredfold.
    """

    def __init__(
        self,
        filing_status: str = FILING_STATUS_SINGLE,
        long_term_capital_gains_rate: float = DEFAULT_MAX_LONG_TERM_RATE,
        short_term_capital_gains_rate: float = DEFAULT_MAX_SHORT_TERM_RATE,
        net_investment_income_tax_rate: float = 0.0,
    ) -> None:
        if filing_status not in VALID_FILING_STATUSES:
            raise ValueError(
                f"filing_status must be one of {sorted(VALID_FILING_STATUSES)}, "
                f"got {filing_status!r}"
            )
        self.filing_status = filing_status

        self.long_term_capital_gains_rate = self._require_rate(
            long_term_capital_gains_rate, "long_term_capital_gains_rate")
        self.short_term_capital_gains_rate = self._require_rate(
            short_term_capital_gains_rate, "short_term_capital_gains_rate")
        self.net_investment_income_tax_rate = self._require_rate(
            net_investment_income_tax_rate, "net_investment_income_tax_rate")

        self.capital_loss_allowance_usd = (
            SECTION_1211B_LIMIT_MFS_USD
            if filing_status == FILING_STATUS_MFS
            else SECTION_1211B_LIMIT_USD
        )
        self.trades: List[Section1256Trade] = []
        self._trade_ids: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Validation helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _require_finite(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{label} must be a real number, got {type(value).__name__}")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite, got {value!r}")
        return value

    @classmethod
    def _require_rate(cls, value: float, label: str) -> float:
        value = cls._require_finite(value, label)
        if not 0.0 <= value < 1.0:
            raise ValueError(
                f"{label} must be a decimal fraction in [0, 1), got {value!r} -- "
                f"pass 0.37 for 37%, not 37.0"
            )
        return value

    def _validate_trade(self, trade: Section1256Trade) -> None:
        if not isinstance(trade, Section1256Trade):
            raise TypeError(
                f"trade must be a Section1256Trade, got {type(trade).__name__}")
        if not isinstance(trade.contract_type, Section1256ContractType):
            raise TypeError(
                f"trade {trade.trade_id}: contract_type must be a "
                f"Section1256ContractType, got {type(trade.contract_type).__name__}"
            )
        if not trade.trade_id:
            raise ValueError("trade_id must be a non-empty string")
        if trade.trade_id in self._trade_ids:
            raise ValueError(
                f"duplicate trade_id {trade.trade_id!r} (already recorded for symbol "
                f"{self._trade_ids[trade.trade_id]!r}); duplicate blotter rows "
                f"double-count Sec. 1256 P&L"
            )

        self._require_finite(
            trade.realized_pnl_usd, f"trade {trade.trade_id}: realized_pnl_usd")
        self._require_finite(
            trade.year_end_mark_pnl_usd, f"trade {trade.trade_id}: year_end_mark_pnl_usd")
        if trade.prior_year_end_cumulative_mark_usd is not None:
            self._require_finite(
                trade.prior_year_end_cumulative_mark_usd,
                f"trade {trade.trade_id}: prior_year_end_cumulative_mark_usd")

        # Sec. 1256(a)(1) makes the year-end mark mandatory for every contract
        # held at the close of the year. A mark carried on a position flagged as
        # closed is a data error, and silently dropping it understates income.
        if not trade.is_open_at_year_end and trade.year_end_mark_pnl_usd != 0.0:
            raise ValueError(
                f"trade {trade.trade_id}: year_end_mark_pnl_usd is "
                f"{trade.year_end_mark_pnl_usd} but is_open_at_year_end is False. "
                f"Set is_open_at_year_end=True for a contract held at the close of "
                f"the tax year, or move the amount to realized_pnl_usd"
            )
        if trade.is_identified_hedging_transaction and trade.is_part_of_mixed_straddle:
            raise ValueError(
                f"trade {trade.trade_id}: a position identified as a Sec. 1256(e) "
                f"hedge is excluded from the straddle rules; it cannot also be "
                f"reported as a mixed straddle leg"
            )
        # Sec. 1256(e): "the mark-to-market rules don't apply if you properly and
        # timely identified a section 1256 contract as a hedge" (Instructions for
        # Form 6781). A hedge carrying a year-end mark is a contradiction, and
        # letting it through would recognise income the statute does not.
        if trade.is_identified_hedging_transaction and trade.year_end_mark_pnl_usd != 0.0:
            raise ValueError(
                f"trade {trade.trade_id}: an identified Sec. 1256(e) hedging transaction "
                f"is not marked to market, so year_end_mark_pnl_usd must be 0.0 "
                f"(got {trade.year_end_mark_pnl_usd})"
            )

    # ------------------------------------------------------------------ #
    # Blotter
    # ------------------------------------------------------------------ #
    def add_trade(self, trade: Section1256Trade) -> None:
        """Record one position.

        Every trade is retained, including non-qualifying ones: they are excluded
        from the 60/40 computation but reported in
        ``excluded_non_section_1256_pnl_usd`` with a warning, so nothing
        disappears between the blotter and the return.

        Raises:
            TypeError: ``trade`` or one of its numeric fields has the wrong type.
            ValueError: duplicate ``trade_id``, a non-finite amount, a year-end
                mark on a position flagged closed, or an incoherent flag pair.
        """
        self._validate_trade(trade)
        self._trade_ids[trade.trade_id] = trade.symbol
        self.trades.append(trade)

    # ------------------------------------------------------------------ #
    # Partition
    # ------------------------------------------------------------------ #
    def _partition(self) -> Tuple[List[Section1256Trade], float, float, float, List[str]]:
        """Split the blotter into Part I trades and the three excluded buckets."""
        part_i: List[Section1256Trade] = []
        hedging: List[float] = []
        mixed_straddle: List[float] = []
        non_1256: List[float] = []
        warnings: List[str] = []

        for trade in self.trades:
            gross = trade.realized_pnl_usd + trade.year_end_mark_pnl_usd

            if not is_section_1256_contract(trade.contract_type):
                non_1256.append(gross)
                warnings.append(
                    f"{trade.trade_id} ({trade.symbol}): {trade.contract_type.value} is not a "
                    f"Sec. 1256(b)(1) contract. Excluded from the 60/40 split; report on "
                    f"Form 8949 / Schedule D by actual holding period."
                )
                continue

            if trade.is_identified_hedging_transaction:
                hedging.append(gross)
                warnings.append(
                    f"{trade.trade_id} ({trade.symbol}): identified Sec. 1256(e) hedging "
                    f"transaction. Not marked to market; gain or loss is ORDINARY and belongs "
                    f"in the Form 6781 line 4 adjustment, not in the 60/40 split."
                )
                continue

            if trade.is_part_of_mixed_straddle:
                mixed_straddle.append(gross)
                warnings.append(
                    f"{trade.trade_id} ({trade.symbol}): mixed straddle leg. Sec. 1256(a)(4) "
                    f"disapplies Sec. 1092 only where every leg is a Sec. 1256 contract, so "
                    f"this is excluded. Resolve under Sec. 1256(d) / Form 6781 Part II."
                )
                continue

            if trade.contract_type in DEALER_ONLY_TYPES:
                warnings.append(
                    f"{trade.trade_id} ({trade.symbol}): {trade.contract_type.value} requires "
                    f"registered dealer status (Sec. 1256(g)(4), (g)(9)), and Sec. 1402(i) "
                    f"pulls a dealer's Sec. 1256 P&L into self-employment earnings."
                )
            if trade.is_open_at_year_end and trade.year_end_mark_pnl_usd == 0.0:
                warnings.append(
                    f"{trade.trade_id} ({trade.symbol}): open at year end with a zero mark. "
                    f"Sec. 1256(a)(1) requires a mark to fair market value on the LAST "
                    f"BUSINESS DAY of the tax year; confirm the value rather than "
                    f"defaulting it."
                )
            part_i.append(trade)

        return (
            part_i,
            math.fsum(hedging),
            math.fsum(mixed_straddle),
            math.fsum(non_1256),
            warnings,
        )

    # ------------------------------------------------------------------ #
    # Core calculation
    # ------------------------------------------------------------------ #
    def generate_form_6781_summary(
        self,
        elect_section_1212c_carryback: bool = False,
        prior_section_1256_gains_usd: float = 0.0,
        other_capital_gains_usd: float = 0.0,
        taxpayer_is_estate_or_trust: bool = False,
    ) -> Form6781TaxSummary:
        """Compute Form 6781 Part I and the loss waterfall for one tax year.

        Args:
            elect_section_1212c_carryback: Check box D and carry a net section
                1256 contracts loss back ``SECTION_1212C_CARRYBACK_YEARS`` years.
                Ignored in a gain year.
            prior_section_1256_gains_usd: Aggregate net Sec. 1256 gain in the 3
                preceding years, which caps the carryback. Supply the amount that
                would appear on those years' Schedule D line 16 counting only
                Sec. 1256 gains and losses; the per-year cap, the earliest-year
                ordering and the no-NOL limit are the caller's responsibility.
            other_capital_gains_usd: Other net capital gain for the year. A
                Sec. 1256 loss is absorbed by it before the Sec. 1211(b) cap, so
                omitting it overstates the loss lost to the $3,000 allowance.
            taxpayer_is_estate_or_trust: Corporations, estates and trusts cannot
                make the box D election (Sec. 1212(c); Instructions for Form 6781).

        Raises:
            TypeError: a monetary argument is not a real number.
            ValueError: an amount is not finite, ``prior_section_1256_gains_usd``
                is negative, or the box D election is requested for an estate or
                trust.
        """
        prior_section_1256_gains_usd = self._require_finite(
            prior_section_1256_gains_usd, "prior_section_1256_gains_usd")
        other_capital_gains_usd = self._require_finite(
            other_capital_gains_usd, "other_capital_gains_usd")
        if prior_section_1256_gains_usd < 0.0:
            raise ValueError(
                "prior_section_1256_gains_usd is a gain ceiling and must not be negative; "
                "a prior year with no net Sec. 1256 gain contributes 0.0"
            )
        if elect_section_1212c_carryback and taxpayer_is_estate_or_trust:
            raise ValueError(
                "Sec. 1212(c) does not apply to an estate or trust, and corporations, "
                "estates and trusts cannot check Form 6781 box D"
            )

        part_i, hedging_pnl, straddle_pnl, non_1256_pnl, warnings = self._partition()

        realized = math.fsum(t.realized_pnl_usd for t in part_i)
        year_end_mark = math.fsum(t.year_end_mark_pnl_usd for t in part_i)

        # Sec. 1256(a)(2): remove gain or loss already taken into account at the
        # prior year end, so a carried contract is not taxed on it twice.
        adjustment = -math.fsum(
            t.prior_year_end_cumulative_mark_usd for t in part_i
            if t.prior_year_end_cumulative_mark_usd is not None
        )
        adjusted_ids = sorted(
            t.trade_id for t in part_i if t.prior_year_end_cumulative_mark_usd is not None
        )
        if adjusted_ids:
            warnings.append(
                f"Sec. 1256(a)(2) prior-year mark of ${-adjustment:,.2f} removed for "
                f"{len(adjusted_ids)} contract(s) ({', '.join(adjusted_ids)}). Their realized "
                f"and mark amounts must be inception-to-date; a Form 1099-B box 11 figure is "
                f"already adjusted by the broker and must not be adjusted again."
            )

        net_pnl = realized + year_end_mark + adjustment  # Form 6781 line 5

        blended_rate = (
            SECTION_1256_LONG_TERM_FRACTION * self.long_term_capital_gains_rate
            + SECTION_1256_SHORT_TERM_FRACTION * self.short_term_capital_gains_rate
        )

        carryback = 0.0
        net_1256_loss = 0.0
        offset_gains = 0.0
        ordinary_deduction = 0.0
        carryforward = 0.0
        loss_benefit = 0.0

        if net_pnl < 0.0:
            gross_loss = -net_pnl
            # Sec. 1212(c)(4) first prong. Sec. 1256 gains are already netted into
            # net_pnl, so the excess over the Sec. 1211(b) allowance is what the
            # first prong leaves. The second prong (capital loss carryovers with
            # line 6 at zero) needs the full Schedule D and is not computed.
            net_1256_loss = max(0.0, gross_loss - self.capital_loss_allowance_usd)

            if elect_section_1212c_carryback:
                carryback = min(net_1256_loss, prior_section_1256_gains_usd)
                if carryback > 0.0:
                    warnings.append(
                        f"Sec. 1212(c) carryback of ${carryback:,.2f} reflects only the "
                        f"aggregate prior Sec. 1256 gain supplied. Verify the per-year "
                        f"Schedule D line 16 cap, earliest-year-first ordering, and that no "
                        f"carryback year's net operating loss is increased or created, then "
                        f"file Form 1045 or an amended return with an amended Form 6781 and "
                        f"Schedule D."
                    )
                elif prior_section_1256_gains_usd <= 0.0:
                    warnings.append(
                        "Form 6781 box D elected but prior_section_1256_gains_usd is 0.00: "
                        "Sec. 1212(c) carries back only against prior Sec. 1256 gains, so "
                        "nothing is carried back."
                    )
                else:
                    warnings.append(
                        f"Form 6781 box D elected but the loss does not exceed the "
                        f"Sec. 1211(b) allowance of ${self.capital_loss_allowance_usd:,.2f}, "
                        f"so there is no net section 1256 contracts loss to carry back."
                    )

            remaining = gross_loss - carryback
            offset_gains = min(remaining, max(0.0, other_capital_gains_usd))
            after_gains = remaining - offset_gains
            ordinary_deduction = min(self.capital_loss_allowance_usd, after_gains)
            carryforward = after_gains - ordinary_deduction
            if carryforward > 0.0:
                warnings.append(
                    f"Sec. 1212(b): ${carryforward:,.2f} carries forward indefinitely, "
                    f"retaining its 60/40 character. It is deferred, not forfeited."
                )
            # The carried-back and offset loss shelters gain carrying the same
            # 60/40 character; the Sec. 1211(b) allowance offsets ordinary income.
            loss_benefit = (
                (carryback + offset_gains) * blended_rate
                + ordinary_deduction * self.short_term_capital_gains_rate
            )

        net_after_carryback = net_pnl + carryback  # Form 6781 line 7
        long_term = net_after_carryback * SECTION_1256_LONG_TERM_FRACTION
        short_term = net_after_carryback * SECTION_1256_SHORT_TERM_FRACTION

        estimated_tax = 0.0
        tax_if_all_short_term = 0.0
        tax_savings = 0.0
        if net_after_carryback > 0.0:
            estimated_tax = (
                long_term * self.long_term_capital_gains_rate
                + short_term * self.short_term_capital_gains_rate
                + net_after_carryback * self.net_investment_income_tax_rate
            )
            tax_if_all_short_term = net_after_carryback * (
                self.short_term_capital_gains_rate + self.net_investment_income_tax_rate
            )
            tax_savings = tax_if_all_short_term - estimated_tax
            if self.net_investment_income_tax_rate == 0.0:
                warnings.append(
                    "estimated_tax_usd excludes the Sec. 1411 net investment income tax. "
                    "Sec. 1256 gain of a trader in commodities or financial instruments is "
                    "net investment income (Sec. 1411(c)(1)(A)(ii)); pass "
                    "net_investment_income_tax_rate=0.038 above the MAGI threshold."
                )

        notes = (
            f"FORM 6781 PART I: line 5 net Sec. 1256 P&L ${net_pnl:,.2f} "
            f"(realized ${realized:,.2f} + year-end mark ${year_end_mark:,.2f} "
            f"+ Sec. 1256(a)(2) adjustment ${adjustment:,.2f}); "
            f"line 6 carryback ${carryback:,.2f}; line 7 ${net_after_carryback:,.2f}; "
            f"line 8 short-term 40% ${short_term:,.2f}; "
            f"line 9 long-term 60% ${long_term:,.2f}. "
            f"{len(part_i)} Sec. 1256 contract(s) in Part I, "
            f"{len(self.trades) - len(part_i)} excluded."
        )
        logger.info(notes)
        for warning in warnings:
            logger.warning(warning)

        return Form6781TaxSummary(
            total_realized_pnl_usd=round(realized, 2),
            total_year_end_mark_pnl_usd=round(year_end_mark, 2),
            prior_year_mark_adjustment_usd=round(adjustment, 2),
            net_section_1256_pnl_usd=round(net_pnl, 2),
            carryback_elected_usd=round(carryback, 2),
            net_after_carryback_usd=round(net_after_carryback, 2),
            short_term_40_pct_usd=round(short_term, 2),
            long_term_60_pct_usd=round(long_term, 2),
            form_6781_line_8_destination=FORM_6781_LINE_8_DESTINATION,
            form_6781_line_9_destination=FORM_6781_LINE_9_DESTINATION,
            net_section_1256_contracts_loss_usd=round(net_1256_loss, 2),
            loss_offset_against_other_capital_gains_usd=round(offset_gains, 2),
            ordinary_income_deduction_usd=round(ordinary_deduction, 2),
            capital_loss_carryforward_usd=round(carryforward, 2),
            estimated_tax_usd=round(estimated_tax, 2),
            estimated_tax_if_all_short_term_usd=round(tax_if_all_short_term, 2),
            tax_savings_vs_short_term_usd=round(tax_savings, 2),
            estimated_loss_tax_benefit_usd=round(loss_benefit, 2),
            blended_rate_applied=round(blended_rate, 6),
            excluded_hedging_pnl_usd=round(hedging_pnl, 2),
            excluded_mixed_straddle_pnl_usd=round(straddle_pnl, 2),
            excluded_non_section_1256_pnl_usd=round(non_1256_pnl, 2),
            section_1256_trade_count=len(part_i),
            excluded_trade_count=len(self.trades) - len(part_i),
            warnings=warnings,
            audit_notes=notes,
        )
