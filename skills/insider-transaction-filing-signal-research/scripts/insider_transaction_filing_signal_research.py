"""
SEC Form 4 Insider Transaction Filing Signal Engine.

Quantitative factor-research engine for Section 16 insider filings. Builds a
point-in-time, role-weighted net insider sentiment score from open-market
purchases and sales reported on SEC Form 4, and implements the Cohen, Malloy &
Pomorski routine-vs-opportunistic trader classifier.

Two independent classification schemes are supported, and they are NOT the same
thing:

1. **Rule 10b5-1 plan status** (`PlanStatus`) -- the Form 4 checkbox introduced
   by SEC Release 33-11138. Filtering plan trades is a *research convention*, not
   a validated alpha improvement: see "10b5-1 is not alpha-free" below.

2. **CMP routine/opportunistic classification** (`classify_trader_regularity`) --
   Cohen, Malloy & Pomorski's trade-timing test. This is the method their 82 bps
   result rests on. It uses no 10b5-1 information at all.

Point-in-time discipline
------------------------
Form 4 carries two distinct dates and conflating them is look-ahead bias:

* `transaction_date` -- when the trade executed. NOT public on that day.
* `filing_datetime`  -- when EDGAR accepted and disseminated the filing. This is
  the earliest moment the information is tradable.

Rule 16a-3(g)(1) requires Form 4 "before the end of the second business day
following the day on which the subject transaction has been executed." For plan
trades where the insider does not select the execution date, Rule 16a-3(g)(2)-(4)
deem the *broker-notification* date to be the execution date, capped at the third
business day after the trade date -- so the lawful trade-to-file gap can reach
roughly five business days. This engine scores only filings whose
`filing_datetime` is at or before the caller-supplied `as_of` instant.

10b5-1 is not alpha-free
------------------------
The claim that Rule 10b5-1 trades carry no predictive content is contradicted by
the SEC's own economic analysis in Release 33-11138 (Section V), which documents
-2.5% six-month industry-adjusted returns following the first sale under plans
whose first trade fell within 30 days of adoption, and single-trade plans (49% of
plans studied) that were "consistently loss-avoiding regardless of cooling-off
period," avoiding declines up to -4%. Excluding plan trades therefore discards
documented signal in exchange for a cleaner opportunistic subsample. Set
`exclude_plan_trades=False` to keep them.

The checkbox is also not available historically. Before the compliance date of
1 April 2023 the designation was voluntary: "Today, the disclosure of a purchase
or sale under a Rule 10b5-1 trading arrangement in Forms 4 and 5 is voluntary,
resulting in a lack of consistent and comprehensive information about such
trades" (Release 33-11138). A pre-2023 record must therefore be supplied as
`PlanStatus.UNKNOWN`, not `NON_PLAN`; `treat_unknown_plan_status_as_plan`
controls how those are handled and the report counts them so the bias is visible.

Sources
-------
* SEC Form 4 (OMB 3235-0287), General Instructions 1, 8 and 10:
  <https://www.sec.gov/files/form4.pdf>
* 17 CFR 240.16a-3(g) -- filing deadline and deemed date of execution.
* SEC Release No. 33-11138, "Insider Trading Arrangements and Related
  Disclosures" (14 Dec 2022):
  <https://www.sec.gov/files/rules/final/2022/33-11138.pdf>
* EDGAR Ownership XML Technical Specification v3 -- `isDirector` / `isOfficer` /
  `isTenPercentOwner` / `isOther` booleans and the free-text `officerTitle`.
* Cohen, L., Malloy, C. & Pomorski, L. (2012). "Decoding Inside Information."
  *Journal of Finance* 67(3), 1009-1043.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# Date on which the Rule 10b5-1(c) checkbox on Forms 4 and 5 became mandatory.
# Section 16 reporting persons must comply for reports FILED on or after this
# date (SEC Release 33-11138). Before it, plan disclosure was voluntary.
RULE_10B5_1_CHECKBOX_MANDATORY_FROM = date(2023, 4, 1)

# Form 4 General Instruction 8 transaction codes. Only P and S are open-market
# *or private* purchases/sales; everything else is a grant, exercise, gift,
# withholding, expiry or other exempt event and carries no directional
# open-market sentiment.
PURCHASE_CODE = "P"
SALE_CODE = "S"
FORM4_TRANSACTION_CODES: Mapping[str, str] = MappingProxyType({
    "P": "Open market or private purchase of non-derivative or derivative security",
    "S": "Open market or private sale of non-derivative or derivative security",
    "V": "Transaction voluntarily reported earlier than required",
    "A": "Grant, award or other acquisition pursuant to Rule 16b-3(d)",
    "D": "Disposition to the issuer pursuant to Rule 16b-3(e)",
    "F": "Payment of exercise price or tax liability by delivering/withholding securities",
    "I": "Discretionary transaction in accordance with Rule 16b-3(f)",
    "M": "Exercise or conversion of derivative security exempted under Rule 16b-3",
    "C": "Conversion of derivative security",
    "E": "Expiration of short derivative position",
    "H": "Expiration or cancellation of long derivative position with value received",
    "O": "Exercise of out-of-the-money derivative security",
    "X": "Exercise of in-the-money or at-the-money derivative security",
    "G": "Bona fide gift",
    "L": "Small acquisition under Rule 16a-6",
    "W": "Acquisition or disposition by will or the laws of descent and distribution",
    "Z": "Deposit into or withdrawal from voting trust",
    "J": "Other acquisition or disposition (described in explanation of responses)",
    "K": "Transaction in equity swap or instrument with similar characteristics",
    "U": "Disposition pursuant to a tender of shares in a change of control transaction",
})

# Illustrative default role weights. NO published source establishes these
# values; they encode the ordering that officers with operational visibility are
# better informed than outside beneficial owners. Recalibrate per universe
# before live use.
DEFAULT_ROLE_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "CEO": 1.0,
    "CFO": 1.0,
    "OTHER_OFFICER": 0.8,
    "DIRECTOR": 0.6,
    "TEN_PCT_OWNER": 0.3,
})

# Free-text `officerTitle` (EDGAR spec: string, max 30 chars) matched to a tier.
# Order matters: the first pattern that matches wins.
_OFFICER_TITLE_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("CEO", re.compile(r"\bC\.?\s?E\.?\s?O\b|\bCHIEF\s+EXECUTIVE", re.I)),
    ("CFO", re.compile(r"\bC\.?\s?F\.?\s?O\b|\bCHIEF\s+FINANCIAL", re.I)),
)


class PlanStatus(Enum):
    """Rule 10b5-1(c) status of a reported transaction.

    ``UNKNOWN`` is the correct value for any filing made before
    ``RULE_10B5_1_CHECKBOX_MANDATORY_FROM``: an unchecked box in that era means
    "not disclosed", not "not a plan trade".
    """

    PLAN = "PLAN"
    NON_PLAN = "NON_PLAN"
    UNKNOWN = "UNKNOWN"


class TraderRegularity(Enum):
    """Cohen-Malloy-Pomorski trade-timing classification of an insider."""

    ROUTINE = "ROUTINE"
    OPPORTUNISTIC = "OPPORTUNISTIC"
    UNCLASSIFIED = "UNCLASSIFIED"


class SignalClassification(Enum):
    """Directional output of the engine."""

    STRONG_BULLISH_OPPORTUNISTIC_BUY = "STRONG_BULLISH_OPPORTUNISTIC_BUY"
    BEARISH_OPPORTUNISTIC_SELL = "BEARISH_OPPORTUNISTIC_SELL"
    NEUTRAL = "NEUTRAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class Form4TransactionRecord:
    """One Table I transaction line from a SEC Form 4 filing.

    The relationship flags mirror the EDGAR Ownership XML
    ``reportingOwnerRelationship`` element and are deliberately **not** mutually
    exclusive: a founder-CEO on the board sets ``is_officer``, ``is_director``
    and ``is_ten_percent_owner`` simultaneously.

    Args:
        filing_id: EDGAR accession number or other unique filing key.
        insider_name: Reporting owner name, used as the identity key for the
            CMP regularity classifier. Normalise upstream if the vendor feed
            spells the same person inconsistently.
        transaction_code: Form 4 General Instruction 8 code. Combined codes such
            as ``"S/K"`` are accepted; the leading code is used.
        shares: Share quantity. Must be finite and strictly positive; direction
            comes from the transaction code, never from a negative quantity.
        price: Per-share transaction price in USD. Must be finite and strictly
            positive.
        transaction_date: Execution date. NOT the date the market learns of it.
        filing_datetime: Timezone-aware instant at which EDGAR disseminated the
            filing. This is the earliest tradable moment and the only date the
            point-in-time filter uses.
        is_open_market: Whether the trade actually executed on-market. Codes P
            and S cover open market *or private* transactions, so this cannot be
            inferred from the code alone.
        officer_title: Free-text title from the filing (EDGAR caps it at 30
            characters), e.g. ``"Chairman, CEO and Secretary"``.
    """

    filing_id: str
    insider_name: str
    transaction_code: str
    shares: float
    price: float
    transaction_date: date
    filing_datetime: datetime
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent_owner: bool = False
    is_other: bool = False
    officer_title: str = ""
    plan_status: PlanStatus = PlanStatus.UNKNOWN
    is_open_market: bool = True

    def notional_usd(self) -> float:
        """Gross unsigned transaction value in USD."""
        return float(self.shares) * float(self.price)


@dataclass
class InsiderFilingSignalReport:
    """Audit-grade result of one point-in-time Form 4 signal evaluation."""

    symbol: str
    as_of: datetime
    filings_supplied: int
    filings_in_scope: int
    not_yet_public_excluded_count: int
    outside_lookback_excluded_count: int
    routine_10b5_1_filtered_count: int
    unknown_plan_status_count: int
    routine_trader_filtered_count: int
    non_open_market_excluded_count: int
    non_purchase_sale_code_count: int
    unclassified_role_count: int
    opportunistic_buys_count: int
    opportunistic_sales_count: int
    distinct_insiders_count: int
    total_opportunistic_buy_notional_usd: float
    total_opportunistic_sell_notional_usd: float
    weighted_net_sentiment_score: float
    signal_classification: SignalClassification
    # Largest transaction_date -> filing_datetime gap over the in-scope filings,
    # in calendar days. A diagnostic on the feed's reporting latency, measured
    # before the plan/open-market/code filters so it covers everything the
    # evaluation actually saw. Calendar days, not business days: it is not a
    # Rule 16a-3(g) compliance test.
    max_trade_to_file_lag_days: Optional[int]
    audit_notes: str
    non_purchase_sale_codes_seen: Dict[str, int] = field(default_factory=dict)


class InsiderFilingSignalError(ValueError):
    """Raised when a Form 4 record or engine argument is unusable."""


def parse_primary_transaction_code(raw_code: str) -> str:
    """Return the leading Form 4 transaction code from a possibly combined code.

    General Instruction 8 directs filers to report equity-swap-linked trades with
    a combined code such as ``"S/K"`` or ``"P/K"``. Matching the raw string
    against ``"S"`` silently drops every one of those trades.

    Raises:
        InsiderFilingSignalError: if the code is blank or not a Form 4 code.
    """
    code = (raw_code or "").strip().upper()
    if not code:
        raise InsiderFilingSignalError("transaction_code must not be empty")
    primary = code.split("/")[0].strip()
    if primary not in FORM4_TRANSACTION_CODES:
        raise InsiderFilingSignalError(
            f"unrecognised Form 4 transaction code {raw_code!r} "
            f"(primary component {primary!r}); see Form 4 General Instruction 8"
        )
    return primary


def classify_trader_regularity(
    trade_history: Iterable[Form4TransactionRecord],
    classification_year: int,
    lookback_years: int = 3,
) -> Dict[str, TraderRegularity]:
    """Classify insiders as routine or opportunistic, per Cohen-Malloy-Pomorski.

    CMP define a routine trader as "an insider who placed a trade in the same
    calendar month for at least three consecutive years", and require "an insider
    to make at least one trade in each of the three preceding years in order to
    define her as either an opportunistic or a routine trader" (*Decoding Inside
    Information*, Journal of Finance 67(3), 2012, Section II). An insider who
    traded in each of the preceding years without a repeated calendar month is
    opportunistic; an insider missing any of those years is UNCLASSIFIED and CMP
    would leave them out of the portfolio entirely.

    Classification uses only transactions dated strictly before
    ``classification_year``, matching CMP's convention of designating insiders at
    the beginning of each calendar year from prior history alone. It is therefore
    free of look-ahead by construction.

    Args:
        trade_history: Prior Form 4 transactions, any symbol, any code. CMP
            classify on the timing of trades, not on their direction.
        classification_year: Calendar year the labels apply to.
        lookback_years: Number of immediately preceding years examined. CMP use
            three and report similar results for one to five.

    Returns:
        Mapping of ``insider_name`` to its regularity label. Insiders with no
        transactions in the window are absent from the mapping; the engine treats
        an absent insider as UNCLASSIFIED.

    Raises:
        InsiderFilingSignalError: if ``lookback_years`` is below 1.
    """
    if lookback_years < 1:
        raise InsiderFilingSignalError("lookback_years must be >= 1")

    window = list(range(classification_year - lookback_years, classification_year))
    months_by_insider: Dict[str, Dict[int, Set[int]]] = {}

    for record in trade_history:
        year = record.transaction_date.year
        if year not in window:
            continue
        by_year = months_by_insider.setdefault(record.insider_name, {})
        by_year.setdefault(year, set()).add(record.transaction_date.month)

    labels: Dict[str, TraderRegularity] = {}
    for insider, by_year in months_by_insider.items():
        if not all(year in by_year for year in window):
            labels[insider] = TraderRegularity.UNCLASSIFIED
            continue
        repeated_month = set.intersection(*(by_year[year] for year in window))
        labels[insider] = (
            TraderRegularity.ROUTINE if repeated_month else TraderRegularity.OPPORTUNISTIC
        )
    return labels


class InsiderFilingSignalEngine:
    """Point-in-time, role-weighted net insider sentiment from SEC Form 4 filings.

    The engine scores only open-market purchases (code P) and sales (code S) that
    were already disseminated by EDGAR as of the evaluation instant. Every other
    filing is excluded and counted, so the report reconciles exactly::

        not_yet_public_excluded_count
      + outside_lookback_excluded_count
      + routine_10b5_1_filtered_count
      + routine_trader_filtered_count
      + non_open_market_excluded_count
      + non_purchase_sale_code_count
      + opportunistic_buys_count
      + opportunistic_sales_count
      = filings_supplied

    ``unknown_plan_status_count`` and ``unclassified_role_count`` are diagnostic
    overlays on that partition, not additional exclusive buckets.
    """

    def __init__(
        self,
        role_weights: Optional[Mapping[str, float]] = None,
        bullish_threshold: float = 0.3,
        bearish_threshold: float = -0.3,
        min_total_notional_usd: float = 0.0,
        min_distinct_insiders: int = 1,
        exclude_plan_trades: bool = True,
        treat_unknown_plan_status_as_plan: bool = False,
        unknown_role_weight: float = 0.0,
    ) -> None:
        """Configure the engine.

        Args:
            role_weights: Overrides for ``DEFAULT_ROLE_WEIGHTS``. Copied, so the
                caller cannot mutate engine state after construction. All weights
                must be finite and non-negative -- a negative weight would invert
                the trade's direction and break the score's [-1, +1] bound.
            bullish_threshold: Score at or above which the signal is bullish.
            bearish_threshold: Score at or below which the signal is bearish.
                Both thresholds are illustrative defaults with no published
                basis; recalibrate out-of-sample per universe.
            min_total_notional_usd: Scored gross notional below which the engine
                emits ``INSUFFICIENT_DATA``. The score is a scale-free ratio, so
                without a floor a single de-minimis purchase saturates it at
                +1.00 and reads identically to a large, broad insider bid.
            min_distinct_insiders: Number of distinct scored insiders required
                before a directional signal is emitted.
            exclude_plan_trades: Whether to drop Rule 10b5-1 plan trades. True
                reproduces the conventional opportunistic-only subsample; see the
                module docstring for why that discards documented signal.
            treat_unknown_plan_status_as_plan: How to handle records whose plan
                status was never disclosed (everything filed before
                ``RULE_10B5_1_CHECKBOX_MANDATORY_FROM``). True is the
                conservative choice and drops them; False scores them and relies
                on ``unknown_plan_status_count`` to expose the contamination.
            unknown_role_weight: Weight applied when the filer sets none of the
                weighted relationship flags (officer, director, ten-percent
                owner). Defaults to 0.0, which excludes the trade from the score
                rather than guessing a middle weight for an unparseable role.

        Raises:
            InsiderFilingSignalError: on an invalid weight or threshold.
        """
        merged: Dict[str, float] = dict(DEFAULT_ROLE_WEIGHTS)
        if role_weights:
            merged.update(role_weights)
        for role, weight in merged.items():
            if not math.isfinite(weight) or weight < 0.0:
                raise InsiderFilingSignalError(
                    f"role weight for {role!r} must be finite and non-negative, got {weight!r}"
                )
        if not math.isfinite(unknown_role_weight) or unknown_role_weight < 0.0:
            raise InsiderFilingSignalError(
                f"unknown_role_weight must be finite and non-negative, got {unknown_role_weight!r}"
            )
        if not (-1.0 <= bearish_threshold < bullish_threshold <= 1.0):
            raise InsiderFilingSignalError(
                "require -1.0 <= bearish_threshold < bullish_threshold <= 1.0, got "
                f"({bearish_threshold}, {bullish_threshold})"
            )
        if min_total_notional_usd < 0.0 or min_distinct_insiders < 1:
            raise InsiderFilingSignalError(
                "min_total_notional_usd must be >= 0 and min_distinct_insiders >= 1"
            )

        self._role_weights: Mapping[str, float] = MappingProxyType(merged)
        self.bullish_threshold = bullish_threshold
        self.bearish_threshold = bearish_threshold
        self.min_total_notional_usd = min_total_notional_usd
        self.min_distinct_insiders = min_distinct_insiders
        self.exclude_plan_trades = exclude_plan_trades
        self.treat_unknown_plan_status_as_plan = treat_unknown_plan_status_as_plan
        self.unknown_role_weight = unknown_role_weight

    @property
    def role_weights(self) -> Mapping[str, float]:
        """Read-only view of the configured role weight schedule."""
        return self._role_weights

    def resolve_role_tier(self, record: Form4TransactionRecord) -> Optional[str]:
        """Return the highest-weighted role tier a filer qualifies for.

        Form 4 relationship flags are independent booleans, so one filer can be
        an officer, a director and a ten-percent owner at once. The best-informed
        capacity governs, so the tier with the largest configured weight wins.

        Returns:
            The tier key, or ``None`` if the filer sets none of the weighted
            flags (``is_officer``, ``is_director``, ``is_ten_percent_owner``).
            ``is_other`` carries no weight tier: the Form 4 ``otherText`` field
            is free text with no defined information hierarchy.
        """
        tiers: List[str] = []
        if record.is_officer:
            tiers.append(self._officer_tier(record.officer_title))
        if record.is_director:
            tiers.append("DIRECTOR")
        if record.is_ten_percent_owner:
            tiers.append("TEN_PCT_OWNER")
        if not tiers:
            return None
        return max(tiers, key=lambda tier: self._role_weights.get(tier, 0.0))

    @staticmethod
    def _officer_tier(officer_title: str) -> str:
        """Map a free-text ``officerTitle`` to a weight tier."""
        title = (officer_title or "").strip()
        for tier, pattern in _OFFICER_TITLE_PATTERNS:
            if pattern.search(title):
                return tier
        return "OTHER_OFFICER"

    def get_role_weight(self, record: Form4TransactionRecord) -> float:
        """Weight for one record, or ``unknown_role_weight`` if unclassifiable."""
        tier = self.resolve_role_tier(record)
        if tier is None:
            return self.unknown_role_weight
        return self._role_weights.get(tier, self.unknown_role_weight)

    def analyze_form4_filings(
        self,
        symbol: str,
        filings: Sequence[Form4TransactionRecord],
        as_of: datetime,
        lookback_days: Optional[int] = None,
        trader_regularity: Optional[Mapping[str, TraderRegularity]] = None,
    ) -> InsiderFilingSignalReport:
        """Score one symbol's insider sentiment as of a specific instant.

        Args:
            symbol: Instrument identifier, for the audit record.
            filings: Form 4 transaction lines. May include filings not yet public
                at ``as_of``; they are excluded and counted, not an error, so the
                caller can pass a full history and roll ``as_of`` forward.
            as_of: Timezone-aware evaluation instant. A filing counts only if its
                ``filing_datetime`` is at or before this.
            lookback_days: If given, additionally restrict to filings disseminated
                within this many days before ``as_of``. ``None`` scores every
                in-scope filing supplied.
            trader_regularity: Optional CMP labels from
                ``classify_trader_regularity``. When supplied, trades by ROUTINE
                insiders are excluded. UNCLASSIFIED insiders are kept and are
                visible through the report's counters.

        Returns:
            An ``InsiderFilingSignalReport`` whose exclusion counters and scored
            counts sum to ``filings_supplied``.

        Raises:
            InsiderFilingSignalError: on a naive ``as_of``, an invalid
                ``lookback_days``, or a malformed record.
        """
        if not symbol or not symbol.strip():
            raise InsiderFilingSignalError("symbol must be a non-empty string")
        self._require_aware(as_of, "as_of")
        if lookback_days is not None and lookback_days < 0:
            raise InsiderFilingSignalError("lookback_days must be >= 0 when supplied")

        not_yet_public = 0
        outside_lookback = 0
        plan_filtered = 0
        unknown_plan = 0
        routine_trader_filtered = 0
        non_open_market = 0
        non_ps_code = 0
        unclassified_role = 0
        opp_buys = 0
        opp_sells = 0

        buy_notional = 0.0
        sell_notional = 0.0
        weighted_buy = 0.0
        weighted_sell = 0.0
        scored_insiders: Set[str] = set()
        other_codes: Dict[str, int] = {}
        max_lag_days: Optional[int] = None
        seen_filing_ids: Set[str] = set()

        for record in filings:
            self._validate_record(record)

            # Form 4/A amendments restate transaction lines, and vendor feeds
            # frequently carry the original and the amendment side by side. The
            # engine cannot tell which supersedes which -- that has to be
            # resolved upstream -- so it warns rather than double-counting in
            # silence.
            if record.filing_id in seen_filing_ids:
                logger.warning(
                    "INSIDER SIGNAL [%s]: filing_id %s appears more than once; "
                    "resolve Form 4/A amendments upstream or the trade is "
                    "counted twice",
                    symbol, record.filing_id,
                )
            seen_filing_ids.add(record.filing_id)

            # 1. Point-in-time gate. A filing the market has not seen yet cannot
            #    inform a position, whatever its transaction date says.
            if record.filing_datetime > as_of:
                not_yet_public += 1
                continue
            if lookback_days is not None:
                age_days = (as_of - record.filing_datetime).days
                if age_days > lookback_days:
                    outside_lookback += 1
                    continue

            lag = (record.filing_datetime.date() - record.transaction_date).days
            max_lag_days = lag if max_lag_days is None else max(max_lag_days, lag)

            # 2. Rule 10b5-1 plan policy. UNKNOWN is its own case: pre-April-2023
            #    filings simply did not carry a reliable designation.
            if record.plan_status is PlanStatus.UNKNOWN:
                unknown_plan += 1
                if self.exclude_plan_trades and self.treat_unknown_plan_status_as_plan:
                    plan_filtered += 1
                    continue
            elif record.plan_status is PlanStatus.PLAN and self.exclude_plan_trades:
                plan_filtered += 1
                continue

            # 3. CMP routine-trader exclusion, when labels were supplied.
            if trader_regularity is not None:
                label = trader_regularity.get(
                    record.insider_name, TraderRegularity.UNCLASSIFIED
                )
                if label is TraderRegularity.ROUTINE:
                    routine_trader_filtered += 1
                    continue

            # 4. Open-market gate. Codes P and S cover open market *or private*
            #    transactions, so the flag carries information the code does not.
            if not record.is_open_market:
                non_open_market += 1
                continue

            code = parse_primary_transaction_code(record.transaction_code)
            if code not in (PURCHASE_CODE, SALE_CODE):
                non_ps_code += 1
                other_codes[code] = other_codes.get(code, 0) + 1
                continue

            weight = self.get_role_weight(record)
            if self.resolve_role_tier(record) is None:
                unclassified_role += 1
                logger.warning(
                    "INSIDER SIGNAL [%s]: filing %s for %s sets no weighted "
                    "Section 16 relationship flag (officer/director/10%% owner); "
                    "applying unknown_role_weight=%.2f",
                    symbol, record.filing_id, record.insider_name,
                    self.unknown_role_weight,
                )

            notional = record.notional_usd()
            if code == PURCHASE_CODE:
                opp_buys += 1
                buy_notional += notional
                weighted_buy += notional * weight
            else:
                opp_sells += 1
                sell_notional += notional
                weighted_sell += notional * weight
            if weight > 0.0:
                scored_insiders.add(record.insider_name)

        total_weighted = weighted_buy + weighted_sell
        if total_weighted > 0.0:
            raw_score = (weighted_buy - weighted_sell) / total_weighted
            # Bounded by construction given non-negative weights and positive
            # notionals; clamped so a float rounding artefact cannot leak out.
            net_sentiment = round(max(-1.0, min(1.0, raw_score)), 4)
        else:
            net_sentiment = 0.0

        scored_notional = buy_notional + sell_notional
        classification = self._classify(
            net_sentiment=net_sentiment,
            scored_notional=scored_notional,
            distinct_insiders=len(scored_insiders),
            total_weighted=total_weighted,
        )

        notes = (
            f"INSIDER FILING REPORT [{symbol}] as of {as_of.isoformat()}: "
            f"{len(filings)} supplied, {not_yet_public} not yet public, "
            f"{outside_lookback} outside lookback, {plan_filtered} Rule 10b5-1 filtered "
            f"({unknown_plan} undisclosed plan status), "
            f"{routine_trader_filtered} routine-trader filtered, "
            f"{non_open_market} non-open-market, {non_ps_code} non-P/S codes. "
            f"Scored: {opp_buys} buys (${buy_notional:,.0f}), "
            f"{opp_sells} sales (${sell_notional:,.0f}) across "
            f"{len(scored_insiders)} insider(s). "
            f"Weighted net sentiment = {net_sentiment:+.2f} ({classification.value})."
        )
        logger.info(notes)

        return InsiderFilingSignalReport(
            symbol=symbol,
            as_of=as_of,
            filings_supplied=len(filings),
            filings_in_scope=len(filings) - not_yet_public - outside_lookback,
            not_yet_public_excluded_count=not_yet_public,
            outside_lookback_excluded_count=outside_lookback,
            routine_10b5_1_filtered_count=plan_filtered,
            unknown_plan_status_count=unknown_plan,
            routine_trader_filtered_count=routine_trader_filtered,
            non_open_market_excluded_count=non_open_market,
            non_purchase_sale_code_count=non_ps_code,
            unclassified_role_count=unclassified_role,
            opportunistic_buys_count=opp_buys,
            opportunistic_sales_count=opp_sells,
            distinct_insiders_count=len(scored_insiders),
            total_opportunistic_buy_notional_usd=round(buy_notional, 2),
            total_opportunistic_sell_notional_usd=round(sell_notional, 2),
            weighted_net_sentiment_score=net_sentiment,
            signal_classification=classification,
            max_trade_to_file_lag_days=max_lag_days,
            audit_notes=notes,
            non_purchase_sale_codes_seen=other_codes,
        )

    def _classify(
        self,
        net_sentiment: float,
        scored_notional: float,
        distinct_insiders: int,
        total_weighted: float,
    ) -> SignalClassification:
        """Apply the sample floors, then the directional thresholds."""
        if total_weighted <= 0.0:
            return SignalClassification.INSUFFICIENT_DATA
        if scored_notional < self.min_total_notional_usd:
            return SignalClassification.INSUFFICIENT_DATA
        if distinct_insiders < self.min_distinct_insiders:
            return SignalClassification.INSUFFICIENT_DATA
        if net_sentiment >= self.bullish_threshold:
            return SignalClassification.STRONG_BULLISH_OPPORTUNISTIC_BUY
        if net_sentiment <= self.bearish_threshold:
            return SignalClassification.BEARISH_OPPORTUNISTIC_SELL
        return SignalClassification.NEUTRAL

    def _validate_record(self, record: Form4TransactionRecord) -> None:
        """Reject records that would silently corrupt the score.

        Raises:
            InsiderFilingSignalError: on a non-finite or non-positive quantity or
                price, a naive ``filing_datetime``, or a filing dated before its
                own transaction.
        """
        if not isinstance(record, Form4TransactionRecord):
            raise InsiderFilingSignalError(
                f"expected Form4TransactionRecord, got {type(record).__name__}"
            )
        for name, value in (("shares", record.shares), ("price", record.price)):
            numeric = float(value)
            if not math.isfinite(numeric):
                raise InsiderFilingSignalError(
                    f"filing {record.filing_id}: {name} must be finite, got {value!r}"
                )
            if numeric <= 0.0:
                raise InsiderFilingSignalError(
                    f"filing {record.filing_id}: {name} must be > 0, got {value!r}; "
                    "direction is carried by the transaction code, not the sign"
                )
        self._require_aware(
            record.filing_datetime, f"filing {record.filing_id}: filing_datetime"
        )
        if record.filing_datetime.date() < record.transaction_date:
            raise InsiderFilingSignalError(
                f"filing {record.filing_id}: filing_datetime "
                f"{record.filing_datetime.date()} precedes transaction_date "
                f"{record.transaction_date}"
            )
        if (
            record.plan_status is not PlanStatus.UNKNOWN
            and record.filing_datetime.date() < RULE_10B5_1_CHECKBOX_MANDATORY_FROM
        ):
            logger.warning(
                "filing %s was filed %s, before the Rule 10b5-1 checkbox became "
                "mandatory on %s; an asserted plan_status of %s rests on voluntary "
                "disclosure and may be wrong",
                record.filing_id, record.filing_datetime.date(),
                RULE_10B5_1_CHECKBOX_MANDATORY_FROM, record.plan_status.value,
            )

    @staticmethod
    def _require_aware(value: datetime, label: str) -> None:
        """Reject timezone-naive datetimes.

        A naive instant makes the point-in-time comparison depend on the host's
        local clock, which is exactly how look-ahead bias gets in unnoticed.
        """
        if not isinstance(value, datetime):
            raise InsiderFilingSignalError(
                f"{label} must be a datetime, got {type(value).__name__}"
            )
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise InsiderFilingSignalError(
                f"{label} must be timezone-aware; EDGAR dissemination cut-offs are "
                "defined in US/Eastern and a naive instant silently adopts the host clock"
            )
