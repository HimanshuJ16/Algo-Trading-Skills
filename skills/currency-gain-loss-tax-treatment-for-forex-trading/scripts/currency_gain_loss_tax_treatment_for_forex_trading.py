"""US federal tax characterization of forex trading gains and losses.

Models the two characterizations that a US taxpayer's currency positions can
receive, and estimates the federal income tax under each:

* **IRC Section 988** — ordinary income or loss. The default for a "section 988
  transaction": "any foreign currency gain or loss attributable to a section 988
  transaction shall be computed separately and treated as ordinary income or
  loss" (26 U.S.C. Sec. 988(a)(1)(A)).
* **IRC Section 1256** — the 60/40 split: "40 percent" short-term and
  "60 percent" long-term capital gain or loss (26 U.S.C. Sec. 1256(a)(3)), with
  mandatory year-end mark-to-market (Sec. 1256(a)(1)).

Three legal points drive the design, and each is a correction of a widespread
retail-forex misconception:

1. **The Sec. 988(a)(1)(B) election produces *capital* character, not 60/40.**
   The statute lets a taxpayer elect to treat currency gain or loss "attributable
   to a forward contract, a futures contract, or option described in subsection
   (c)(1)(B)(iii) ... as capital gain or loss." It says nothing about 60/40. The
   60/40 rate only follows if the contract independently qualifies as a
   Sec. 1256 contract.
2. **Spot transactions are not eligible for that election.** It reaches only
   forward, futures, and option contracts under Sec. 988(c)(1)(B)(iii); Treas.
   Reg. Sec. 1.988-3(b)(1) mirrors this. A pure spot trade has no election.
3. **Whether a retail forex contract is a Sec. 1256(g)(2) "foreign currency
   contract" is unsettled.** Wright v. Commissioner, 809 F.3d 877 (6th Cir.
   2016) read the definition broadly; proposed regulations REG-130675-17
   (87 FR 40224, July 6, 2022) would limit it to foreign currency *forward*
   contracts and expressly overrule Wright. This engine therefore never assumes
   Sec. 1256 eligibility — the caller must assert it per contract.

Loss mechanics matter as much as rates. A net Sec. 1256 loss is not simply
"capped at $3,000": under Sec. 1212(c) it "shall be a carryback to each of the 3
taxable years preceding the loss year" against prior Sec. 1256 gains (elected on
Form 6781, box D, line 6), the remainder offsets current capital gains, then up
to $3,000 ($1,500 married filing separately) of ordinary income under
Sec. 1211(b), and anything left carries forward indefinitely under Sec. 1212(b).

THIS IS NOT TAX ADVICE. The engine is a US-federal-only estimator for modeling
and record-keeping. It does not model the 3.8% net investment income tax
(Sec. 1411), state or local tax, the Sec. 475(f) trader mark-to-market election,
the Sec. 461(l) excess business loss limitation, straddles (Sec. 1092), or the
trader-versus-investor determination. Engage a qualified tax professional before
making or relying on any election.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Instrument categories the engine knows how to characterize.
SPOT_FOREX = "SPOT_FOREX"
CURRENCY_FUTURES = "CURRENCY_FUTURES"
FORWARDS = "FORWARDS"
VALID_INSTRUMENT_TYPES = frozenset({SPOT_FOREX, CURRENCY_FUTURES, FORWARDS})

# Recommendation values.
ELECT_SECTION_1256 = "ELECT_SECTION_1256"
REMAIN_SECTION_988 = "REMAIN_SECTION_988"
INSUFFICIENT_ELIGIBILITY_BASIS = "INSUFFICIENT_ELIGIBILITY_BASIS"

# IRC Sec. 1211(b): capital loss deduction against ordinary income.
CAPITAL_LOSS_ORDINARY_OFFSET_CAP = 3_000.0
CAPITAL_LOSS_ORDINARY_OFFSET_CAP_MFS = 1_500.0

# IRC Sec. 1256(a)(3) statutory split.
SEC1256_LONG_TERM_FRACTION = 0.60
SEC1256_SHORT_TERM_FRACTION = 0.40

# IRC Sec. 1212(c): carryback window, in years.
SEC1256_CARRYBACK_YEARS = 3


@dataclass
class ForexTradeRecord:
    """
    One closed or open currency position for a single tax year.

    Args:
        trade_id: caller's identifier, used in eligibility warnings.
        pair: e.g. 'EUR/USD'.
        instrument_type: one of ``VALID_INSTRUMENT_TYPES``.
        realized_pnl_usd: realized gain (+) or loss (-) in USD.
        trade_date: ISO 'YYYY-MM-DD'. Used to filter to the tax year.
        unrealized_mtm_pnl_usd: year-end mark-to-market gain/loss on a position
            still open at year end. Only enters the Sec. 1256 scenario, where
            marking to market is mandatory (Sec. 1256(a)(1)).
        is_open_at_year_end: whether the position was open on the last business
            day of the tax year.
        sec1256_eligible: the caller's affirmative determination that this
            contract is a Sec. 1256 contract. ``None`` means "not determined"
            and is treated as ineligible, with a warning — the engine will not
            infer eligibility from the instrument label.
    """

    trade_id: str
    pair: str
    instrument_type: str
    realized_pnl_usd: float
    trade_date: str
    unrealized_mtm_pnl_usd: float = 0.0
    is_open_at_year_end: bool = False
    sec1256_eligible: Optional[bool] = None


@dataclass
class ForexTaxReport:
    """
    Estimated federal tax under each scenario, plus the caveats.

    Both scenarios cover the same book, so ``potential_tax_savings_usd`` is a
    like-for-like difference:

    * ``sec988_tax_liability_usd`` - no election: the whole book is ordinary.
    * ``sec1256_tax_liability_usd`` - election made: positions determined
      Sec. 1256-eligible move to 60/40 and are marked to market; the rest stays
      ordinary under Sec. 988.
    """

    total_realized_pnl_usd: float
    sec988_tax_liability_usd: float
    sec988_effective_tax_rate_pct: float
    sec1256_tax_liability_usd: float
    sec1256_effective_tax_rate_pct: float
    potential_tax_savings_usd: float
    recommended_election: str
    rationale: str
    # Detail added so the estimate is auditable rather than a bare number.
    total_mtm_pnl_usd: float = 0.0
    sec1256_scenario_pnl_usd: float = 0.0
    sec1256_loss_carryback_usd: float = 0.0
    sec1256_loss_offsetting_capital_gains_usd: float = 0.0
    sec1256_loss_against_ordinary_usd: float = 0.0
    sec1256_loss_carryforward_usd: float = 0.0
    pnl_by_instrument_type: Dict[str, float] = field(default_factory=dict)
    eligibility_warnings: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)


class ForexTaxTreatmentEngine:
    """
    Compares IRC Sec. 988 ordinary treatment against Sec. 1256 60/40 treatment
    for a tax year's currency positions, and reports which is cheaper along with
    the legal conditions that must hold for the comparison to be meaningful.

    Rates are decimal fractions, NOT percentages: pass ``0.37`` for 37%.

    Args:
        ordinary_income_rate: marginal ordinary rate. 37% is the top federal
            rate for tax year 2026.
        ltcg_rate: long-term capital gains rate (top statutory rate 20%).
        stcg_rate: short-term capital gains rate — short-term capital gain is
            taxed at ordinary rates, so this normally equals
            ``ordinary_income_rate``.
        married_filing_separately: selects the $1,500 Sec. 1211(b) cap instead
            of $3,000.
        prior_sec1256_gains_usd: net Sec. 1256 contract gains reported in the 3
            preceding tax years that remain available to absorb a carryback
            under Sec. 1212(c). Zero disables carryback modeling.
        other_capital_gains_usd: other current-year capital gains available to
            absorb a capital loss before the Sec. 1211(b) cap applies.
    """

    def __init__(
        self,
        ordinary_income_rate: float = 0.37,
        ltcg_rate: float = 0.20,
        stcg_rate: float = 0.37,
        married_filing_separately: bool = False,
        prior_sec1256_gains_usd: float = 0.0,
        other_capital_gains_usd: float = 0.0,
    ) -> None:
        for label, rate in (
            ("ordinary_income_rate", ordinary_income_rate),
            ("ltcg_rate", ltcg_rate),
            ("stcg_rate", stcg_rate),
        ):
            if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not math.isfinite(rate):
                raise ValueError(f"{label} must be a finite number, got {rate!r}.")
            if not 0.0 <= rate <= 1.0:
                raise ValueError(
                    f"{label} must be a decimal fraction in [0, 1], got {rate!r}. "
                    "Rates are fractions, not percentages: pass 0.37 for 37%."
                )
        for label, amount in (
            ("prior_sec1256_gains_usd", prior_sec1256_gains_usd),
            ("other_capital_gains_usd", other_capital_gains_usd),
        ):
            if not math.isfinite(amount) or amount < 0.0:
                raise ValueError(f"{label} must be a finite, non-negative USD amount, got {amount!r}.")

        self.ordinary_income_rate = float(ordinary_income_rate)
        self.ltcg_rate = float(ltcg_rate)
        self.stcg_rate = float(stcg_rate)
        self.married_filing_separately = bool(married_filing_separately)
        self.prior_sec1256_gains_usd = float(prior_sec1256_gains_usd)
        self.other_capital_gains_usd = float(other_capital_gains_usd)

        # IRC Sec. 1256(a)(3): 60% long-term / 40% short-term.
        self.sec1256_blended_rate = round(
            SEC1256_LONG_TERM_FRACTION * self.ltcg_rate + SEC1256_SHORT_TERM_FRACTION * self.stcg_rate,
            6,
        )

    @property
    def capital_loss_ordinary_offset_cap(self) -> float:
        """IRC Sec. 1211(b)(1): $3,000, or $1,500 for married filing separately."""
        return (
            CAPITAL_LOSS_ORDINARY_OFFSET_CAP_MFS
            if self.married_filing_separately
            else CAPITAL_LOSS_ORDINARY_OFFSET_CAP
        )

    # ------------------------------------------------------------------ #
    # Validation and bucketing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_trades(trades: Sequence[ForexTradeRecord]) -> None:
        if not isinstance(trades, Sequence) or isinstance(trades, (str, bytes)):
            raise TypeError("trades must be a sequence of ForexTradeRecord.")
        seen: set = set()
        for t in trades:
            if not isinstance(t, ForexTradeRecord):
                raise TypeError(f"Expected ForexTradeRecord, got {type(t).__name__}.")
            if t.instrument_type not in VALID_INSTRUMENT_TYPES:
                raise ValueError(
                    f"Trade {t.trade_id!r}: unknown instrument_type {t.instrument_type!r}. "
                    f"Expected one of {sorted(VALID_INSTRUMENT_TYPES)}."
                )
            if not math.isfinite(t.realized_pnl_usd) or not math.isfinite(t.unrealized_mtm_pnl_usd):
                raise ValueError(f"Trade {t.trade_id!r}: PnL values must be finite.")
            if t.unrealized_mtm_pnl_usd != 0.0 and not t.is_open_at_year_end:
                raise ValueError(
                    f"Trade {t.trade_id!r}: unrealized_mtm_pnl_usd is non-zero but "
                    "is_open_at_year_end is False. Mark-to-market applies only to positions "
                    "open on the last business day of the tax year (IRC Sec. 1256(a)(1))."
                )
            if t.trade_id in seen:
                raise ValueError(f"Duplicate trade_id {t.trade_id!r}: PnL would be double counted.")
            seen.add(t.trade_id)

    @staticmethod
    def _in_tax_year(trade: ForexTradeRecord, tax_year: Optional[int]) -> bool:
        if tax_year is None:
            return True
        date = trade.trade_date
        if not isinstance(date, str) or len(date) < 4 or not date[:4].isdigit():
            raise ValueError(
                f"Trade {trade.trade_id!r}: trade_date {date!r} is not ISO 'YYYY-MM-DD'; "
                "cannot filter to a tax year."
            )
        return int(date[:4]) == tax_year

    def _eligibility_warnings(self, trades: Sequence[ForexTradeRecord]) -> List[str]:
        """
        Flags every position whose Sec. 1256 eligibility the caller has not
        affirmatively determined, and every spot position, which has no
        Sec. 988(a)(1)(B) election available at all.
        """
        warnings: List[str] = []
        for t in trades:
            if t.instrument_type == SPOT_FOREX and t.sec1256_eligible:
                warnings.append(
                    f"Trade {t.trade_id!r} ({t.pair}): asserted Sec. 1256-eligible while typed "
                    "SPOT_FOREX. A spot transaction is not a forward, futures, or option contract "
                    "under Sec. 988(c)(1)(B)(iii), so no Sec. 988(a)(1)(B) election is available; "
                    "Sec. 1256(g)(2) eligibility requires a qualifying contract. Confirm the "
                    "instrument's legal form with a tax professional."
                )
            elif t.sec1256_eligible is None:
                warnings.append(
                    f"Trade {t.trade_id!r} ({t.pair}, {t.instrument_type}): sec1256_eligible not "
                    "determined; excluded from the Sec. 1256 scenario. Eligibility under "
                    "Sec. 1256(g)(2) is unsettled for retail forex - see Wright v. Commissioner, "
                    "809 F.3d 877 (6th Cir. 2016) and proposed regs REG-130675-17."
                )
        return warnings

    # ------------------------------------------------------------------ #
    # Scenario tax computation
    # ------------------------------------------------------------------ #
    def _sec988_tax(self, pnl: float) -> float:
        """
        Ordinary treatment. A negative return value is a tax *benefit*.

        Sec. 988 losses are ordinary and not subject to the Sec. 1211(b) capital
        loss cap; the engine models them as fully deductible in the current year.
        Actual deductibility can still be limited (e.g. Sec. 461(l) excess
        business loss), which is out of scope — see ``caveats``.
        """
        return pnl * self.ordinary_income_rate

    def _sec1256_tax(self, pnl: float) -> Dict[str, float]:
        """
        60/40 treatment, including the statutory loss waterfall.

        Returns the tax (negative = benefit) plus the components of the loss
        waterfall so the estimate can be audited.
        """
        if pnl >= 0.0:
            return {
                "tax": pnl * self.sec1256_blended_rate,
                "carryback": 0.0,
                "offset_capital_gains": 0.0,
                "against_ordinary": 0.0,
                "carryforward": 0.0,
            }

        remaining = abs(pnl)

        # Sec. 1212(c): carryback to each of the 3 preceding years, limited to
        # net Sec. 1256 contract gain in those years. Character stays 60/40, so
        # the recovered tax is valued at the blended rate.
        carryback = min(remaining, self.prior_sec1256_gains_usd)
        remaining -= carryback

        # Sec. 1211(b): losses allowed "to the extent of the gains from such
        # sales or exchanges" first.
        offset_gains = min(remaining, self.other_capital_gains_usd)
        remaining -= offset_gains

        # Sec. 1211(b)(1): then the lower of $3,000/$1,500 or the excess.
        against_ordinary = min(remaining, self.capital_loss_ordinary_offset_cap)
        remaining -= against_ordinary

        # Sec. 1212(b): the rest carries forward indefinitely. No current-year
        # benefit is credited; its amount is reported instead.
        carryforward = remaining

        benefit = (
            carryback * self.sec1256_blended_rate
            + offset_gains * self.sec1256_blended_rate
            + against_ordinary * self.ordinary_income_rate
        )
        return {
            "tax": -benefit,
            "carryback": carryback,
            "offset_capital_gains": offset_gains,
            "against_ordinary": against_ordinary,
            "carryforward": carryforward,
        }

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def evaluate_forex_tax(
        self,
        trades: Sequence[ForexTradeRecord],
        tax_year: Optional[int] = None,
    ) -> ForexTaxReport:
        """
        Estimates federal tax under Sec. 988 versus Sec. 1256 for one tax year.

        Args:
            trades: the year's currency positions.
            tax_year: if given, only trades whose ``trade_date`` falls in this
                calendar year are included. ``None`` uses every trade supplied
                and assumes the caller has already filtered — a multi-year log
                passed with ``None`` silently mixes tax years.

        Returns:
            A ``ForexTaxReport``. ``recommended_election`` is
            ``INSUFFICIENT_ELIGIBILITY_BASIS`` when no position has been
            affirmatively determined Sec. 1256-eligible, because in that case
            the 60/40 scenario has no legal basis to stand on.
        """
        self._validate_trades(trades)
        in_year = [t for t in trades if self._in_tax_year(t, tax_year)]

        warnings = self._eligibility_warnings(in_year)

        realized_total = sum(t.realized_pnl_usd for t in in_year)
        mtm_total = sum(t.unrealized_mtm_pnl_usd for t in in_year)

        pnl_by_type: Dict[str, float] = {}
        for t in in_year:
            pnl_by_type[t.instrument_type] = round(
                pnl_by_type.get(t.instrument_type, 0.0) + t.realized_pnl_usd + t.unrealized_mtm_pnl_usd, 2
            )

        # Scenario A ("no election"): the whole book is ordinary under Sec. 988.
        # Sec. 988 has no mark-to-market regime, so year-end unrealized PnL is
        # excluded here and enters only the Sec. 1256 scenario.
        sec988_tax = self._sec988_tax(realized_total)

        # Scenario B ("election made"): the positions the caller affirmatively
        # determined eligible move to 60/40 and are marked to market per
        # Sec. 1256(a)(1); everything else stays ordinary under Sec. 988. Both
        # scenarios therefore cover the same book, so the difference between
        # them is the actual value of electing.
        eligible = [t for t in in_year if t.sec1256_eligible]
        sec1256_pnl = sum(t.realized_pnl_usd + t.unrealized_mtm_pnl_usd for t in eligible)
        eligible_realized = sum(t.realized_pnl_usd for t in eligible)
        sec1256 = self._sec1256_tax(sec1256_pnl)
        # Non-elected remainder keeps ordinary treatment in scenario B.
        sec1256_tax = self._sec988_tax(realized_total - eligible_realized) + sec1256["tax"]

        sec1256_base = (realized_total - eligible_realized) + sec1256_pnl
        sec988_eff_rate = (sec988_tax / realized_total * 100.0) if realized_total else 0.0
        sec1256_eff_rate = (sec1256_tax / sec1256_base * 100.0) if sec1256_base else 0.0

        tax_savings = sec988_tax - sec1256_tax

        if not eligible:
            rec = INSUFFICIENT_ELIGIBILITY_BASIS
            rationale = (
                f"Net realized PnL of ${realized_total:,.2f} defaults to Sec. 988 ordinary "
                "treatment. No position was affirmatively determined to be a Sec. 1256 contract, "
                "so no 60/40 comparison is presented. Set sec1256_eligible on each qualifying "
                "contract only after a professional determination under Sec. 1256(g)(2)."
            )
        elif tax_savings > 0.0:
            rec = ELECT_SECTION_1256
            rationale = (
                f"Sec. 1256-eligible PnL of ${sec1256_pnl:,.2f} (including ${mtm_total:,.2f} "
                f"year-end mark-to-market). 60/40 treatment at a blended "
                f"{self.sec1256_blended_rate * 100:.1f}% versus ordinary "
                f"{self.ordinary_income_rate * 100:.1f}% reduces estimated federal tax by "
                f"${tax_savings:,.2f}. The Sec. 988(a)(1)(B) election gives capital character; "
                "60/40 follows only because these contracts are Sec. 1256 contracts."
            )
        else:
            rec = REMAIN_SECTION_988
            if sec1256_pnl < 0.0:
                rationale = (
                    f"Net loss of ${abs(sec1256_pnl):,.2f} on Sec. 1256-eligible contracts. "
                    f"Sec. 988 ordinary loss is not subject to the Sec. 1211(b) capital loss cap, "
                    f"worth ${abs(tax_savings):,.2f} more in current-year benefit. Under Sec. 1256 "
                    f"the loss would break down as ${sec1256['carryback']:,.2f} carried back "
                    f"(Sec. 1212(c), {SEC1256_CARRYBACK_YEARS} years, Form 6781 box D), "
                    f"${sec1256['offset_capital_gains']:,.2f} against other capital gains, "
                    f"${sec1256['against_ordinary']:,.2f} against ordinary income, and "
                    f"${sec1256['carryforward']:,.2f} carried forward indefinitely "
                    "(Sec. 1212(b)) - deferred, not forfeited."
                )
            else:
                rationale = (
                    "Ordinary rate is at or below the Sec. 1256 blended rate; the election "
                    "offers no rate advantage on these positions."
                )

        caveats = [
            "NOT TAX ADVICE - US federal estimate only; engage a qualified tax professional.",
            "The Sec. 988(a)(1)(B) election produces capital character, not automatically 60/40. "
            "60/40 requires the contract to be a Sec. 1256 contract in its own right.",
            "The election must be made per transaction by identifying it in books and records "
            "on the date the transaction is entered into (Treas. Reg. Sec. 1.988-3(b)(3)), with a "
            "verification statement attached to the return (Sec. 1.988-3(b)(4)).",
            "Sec. 1256(g)(2) eligibility for retail forex is unsettled: proposed regs "
            "REG-130675-17 (87 FR 40224, July 6, 2022) would limit 'foreign currency contract' to "
            "forward contracts and overrule Wright v. Commissioner, 809 F.3d 877 (6th Cir. 2016). "
            "Status not verified as final; confirm before relying on it.",
            "Excludes the 3.8% net investment income tax (Sec. 1411), state and local tax, the "
            "Sec. 475(f) trader mark-to-market election, the Sec. 461(l) excess business loss "
            "limitation, and straddle rules (Sec. 1092).",
            "Loss carryforward is reported but given no current-year value; the carryback and "
            "capital-gain offsets are valued at the 60/40 blended rate, an approximation where "
            "the absorbed gains' actual character or year differs.",
        ]
        if tax_year is None:
            caveats.append(
                "tax_year was not supplied: every trade passed was included regardless of its "
                "trade_date. Confirm the log covers exactly one tax year."
            )

        logger.info(
            "FOREX TAX AUDIT: tax_year=%s realized=$%.2f mtm=$%.2f sec1256_eligible_pnl=$%.2f -> %s",
            tax_year, realized_total, mtm_total, sec1256_pnl, rec,
        )
        for w in warnings:
            logger.warning("ELIGIBILITY: %s", w)

        return ForexTaxReport(
            total_realized_pnl_usd=round(realized_total, 2),
            sec988_tax_liability_usd=round(sec988_tax, 2),
            sec988_effective_tax_rate_pct=round(sec988_eff_rate, 2),
            sec1256_tax_liability_usd=round(sec1256_tax, 2),
            sec1256_effective_tax_rate_pct=round(sec1256_eff_rate, 2),
            potential_tax_savings_usd=round(tax_savings, 2),
            recommended_election=rec,
            rationale=rationale,
            total_mtm_pnl_usd=round(mtm_total, 2),
            sec1256_scenario_pnl_usd=round(sec1256_pnl, 2),
            sec1256_loss_carryback_usd=round(sec1256["carryback"], 2),
            sec1256_loss_offsetting_capital_gains_usd=round(sec1256["offset_capital_gains"], 2),
            sec1256_loss_against_ordinary_usd=round(sec1256["against_ordinary"], 2),
            sec1256_loss_carryforward_usd=round(sec1256["carryforward"], 2),
            pnl_by_instrument_type=pnl_by_type,
            eligibility_warnings=warnings,
            caveats=caveats,
        )
