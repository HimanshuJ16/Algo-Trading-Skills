"""FX outright forward and FX swap position tracking.

Stateless valuation engine for a book of FX outright forwards and FX swaps. It
computes the Covered Interest Rate Parity (CIRP) forward rate, forward/swap
points, discounted mark-to-market, net currency exposure, and a maturity-bucket
gap view.

Three properties of this domain drive the design and are easy to get wrong:

* **The two legs of a currency pair do not share a day-count basis.** USD and
  EUR money markets accrue on Actual/360; sterling and yen accrue on
  Actual/365. Applying one denominator to both legs of GBP/USD misprices the
  6-month forward by roughly 3.8 pips (see ``references/standards.md``). The
  basis is therefore resolved per currency, not per engine.
* **A forward's mark-to-market is a present value.** The rate difference is a
  cash flow at maturity, denominated in the *quote* currency. Reporting it
  undiscounted overstates the position by the quote-currency carry over the
  remaining life.
* **Mark-to-market P&L in different quote currencies cannot be added.** A book
  of EUR/USD and USD/JPY forwards produces USD and JPY cash flows. This engine
  reports P&L per quote currency and produces a single total only when the
  caller supplies explicit conversion rates.

The CIRP rate this engine computes is a *theoretical* forward, not a tradable
one. Covered interest parity has not held since 2008 — the residual is the
cross-currency basis (BIS Working Paper 590). Where an observable market
forward exists, pass it in ``market_forward_rate`` and the engine marks to it.

All rates follow the ``BASE/QUOTE`` convention: the rate is units of quote
currency per one unit of base currency, and a ``BUY`` is long the base currency.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# --- Contract taxonomy ---------------------------------------------------------
# BIS Triennial Survey terminology: an outright forward settles more than two
# business days out; an FX swap is a near leg and a far leg in opposite
# directions. A swap must be supplied as its two legs, never as one row.
CONTRACT_FX_FORWARD = "FX_FORWARD"
CONTRACT_FX_SWAP = "FX_SWAP"
VALID_CONTRACT_TYPES: Tuple[str, ...] = (CONTRACT_FX_FORWARD, CONTRACT_FX_SWAP)

SIDE_BUY = "BUY"    # long base currency
SIDE_SELL = "SELL"  # short base currency
VALID_SIDES: Tuple[str, ...] = (SIDE_BUY, SIDE_SELL)

LEG_NEAR = "NEAR"
LEG_FAR = "FAR"
VALID_SWAP_LEGS: Tuple[str, ...] = (LEG_NEAR, LEG_FAR)

# --- Valuation basis -----------------------------------------------------------
MTM_BASIS_OBSERVED = "OBSERVED_MARKET_FORWARD"
MTM_BASIS_CIRP = "CIRP_THEORETICAL"

# --- Money-market day-count denominators --------------------------------------
# Sourced per currency in references/standards.md. Only currencies whose
# convention was verified against the rate administrator are listed; anything
# else falls back to ``default_day_count_basis`` and is logged, never guessed
# silently.
MONEY_MARKET_DAY_COUNT_BASIS: Dict[str, int] = {
    "USD": 360,   # SOFR / USD money market — actual days over a 360-day year
    "EUR": 360,   # euro money market convention (actual/360)
    "GBP": 365,   # SONIA / sterling OIS — actual/365
    "JPY": 365,   # TONA / TORF — actual/365 (fixed)
}
DEFAULT_DAY_COUNT_BASIS = 360

# --- Pip / forward-point scaling ----------------------------------------------
# Points per one unit of the quoted rate. A pip is the fourth decimal for most
# pairs (10,000 points per unit) but the second decimal where the quote
# currency is yen (100 points per unit).
QUOTE_CURRENCY_PIP_FACTOR: Dict[str, float] = {"JPY": 100.0}
DEFAULT_PIP_FACTOR = 10_000.0

# --- Maturity gap buckets ------------------------------------------------------
# Library convention for gap reporting, not a market standard. Upper bounds are
# inclusive, in calendar days.
DEFAULT_MATURITY_BUCKETS: Tuple[Tuple[str, int], ...] = (
    ("0-1M", 31),
    ("1M-3M", 92),
    ("3M-6M", 184),
    ("6M-1Y", 366),
)
BUCKET_BEYOND_ONE_YEAR = "1Y+"

# Below this, the trade settles inside the conventional spot window and is not
# an outright forward in the BIS sense.
SPOT_SETTLEMENT_DAYS = 2

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


@dataclass
class FxContractPosition:
    """One valuation row: an outright forward, or one leg of an FX swap.

    Args:
        contract_id: Trade identifier. Both legs of an FX swap share it.
        currency_pair: ``'BASE/QUOTE'``, e.g. ``'EUR/USD'``. Must agree with
            ``base_currency`` and ``quote_currency``.
        base_currency: ISO 4217 code of the currency being bought or sold.
        quote_currency: ISO 4217 code the rate is expressed in.
        contract_type: ``'FX_FORWARD'`` or ``'FX_SWAP'``.
        position_side: ``'BUY'`` (long base) or ``'SELL'`` (short base).
        notional_base_currency: Contract amount in **base** currency, > 0. The
            direction lives in ``position_side``, never in the sign here.
        agreed_forward_rate: Contracted all-in forward rate (outright, not
            points), quote per base.
        days_to_maturity: Calendar days from the valuation date to settlement.
            ``0`` means settling today. This is *remaining* life, not original
            tenor — passing the original tenor freezes the position at trade
            date and never lets the mark converge to spot.
        swap_leg: ``'NEAR'`` or ``'FAR'`` — required for ``'FX_SWAP'``, must be
            ``None`` for an outright forward.
        settlement_date_iso: Optional ``YYYY-MM-DD`` settlement date, carried
            through for audit. Not used in any calculation.
    """

    contract_id: str
    currency_pair: str
    base_currency: str
    quote_currency: str
    contract_type: str
    position_side: str
    notional_base_currency: float
    agreed_forward_rate: float
    days_to_maturity: int
    swap_leg: Optional[str] = None
    settlement_date_iso: Optional[str] = None


@dataclass
class FxValuationDetail:
    """Per-leg valuation output.

    ``swap_points`` is the market forward points, i.e. the points a swap of the
    same tenor would trade at. ``undiscounted_mtm_quote`` is the maturity cash
    flow; ``mtm_pv_quote`` is that cash flow discounted at the quote-currency
    rate and is the figure to report. Both are in ``quote_currency``.
    """

    contract_id: str
    currency_pair: str
    base_currency: str
    quote_currency: str
    contract_type: str
    swap_leg: Optional[str]
    position_side: str
    notional_base: float
    days_to_maturity: int
    agreed_forward_rate: float
    current_spot_rate: float
    cirp_forward_rate: float
    valuation_forward_rate: float
    mtm_basis: str
    pip_factor: float
    swap_points: float                  # (F_valuation - Spot) * pip_factor
    contract_forward_points: float      # (F_contract  - Spot) * pip_factor
    base_day_count_basis: int
    quote_day_count_basis: int
    undiscounted_mtm_quote: float
    quote_discount_factor: float
    mtm_pv_quote: float
    base_exposure: float                # signed, base currency units
    quote_exposure: float               # signed, quote currency units
    maturity_bucket: str


@dataclass
class FxForwardPositionReport:
    """Portfolio-level audit output.

    P&L is reported **per quote currency**; there is no cross-currency total
    unless ``reporting_currency`` and conversion rates were supplied, because
    adding USD and JPY cash flows is meaningless.
    """

    total_open_contracts: int
    unrealized_mtm_pv_by_quote_currency: Dict[str, float]
    unrealized_mtm_undiscounted_by_quote_currency: Dict[str, float]
    net_exposure_by_currency: Dict[str, float]
    net_exposure_by_maturity_bucket: Dict[str, Dict[str, float]]
    valuation_details: List[FxValuationDetail]
    audit_notes: str
    reporting_currency: Optional[str] = None
    net_unrealized_mtm_pv_reporting_currency: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


class FxForwardSwapTrackingEngine:
    """Prices and marks a book of FX outright forwards and FX swaps.

    Args:
        day_count_basis: Optional per-currency overrides, e.g. ``{'AUD': 365}``.
            Merged over ``MONEY_MARKET_DAY_COUNT_BASIS``. A single integer is
            **rejected**: one denominator cannot express a GBP/USD or USD/JPY
            pair, whose legs accrue on different bases.
        default_day_count_basis: Denominator used for currencies absent from
            both the verified table and the overrides. Each such currency is
            logged once at WARNING.
        pip_factor_overrides: Optional per-pair points scaling, e.g.
            ``{'USD/KRW': 100.0}``. Keyed by currency pair.
        maturity_buckets: ``(label, inclusive_upper_bound_days)`` pairs in
            ascending order. Anything beyond the last bound is bucketed as
            ``'1Y+'``.
        report_decimals: Decimal places for monetary amounts in the report.
            Rounding is applied only when building output objects; all
            intermediate arithmetic runs at full precision.
    """

    def __init__(
        self,
        day_count_basis: Optional[Mapping[str, int]] = None,
        default_day_count_basis: int = DEFAULT_DAY_COUNT_BASIS,
        pip_factor_overrides: Optional[Mapping[str, float]] = None,
        maturity_buckets: Optional[Sequence[Tuple[str, int]]] = None,
        report_decimals: int = 2,
    ) -> None:
        if isinstance(day_count_basis, (int, float)) and not isinstance(day_count_basis, bool):
            raise TypeError(
                "day_count_basis must be a mapping of currency -> denominator "
                "(e.g. {'USD': 360, 'GBP': 365}), not a single number. The two legs of a "
                "currency pair do not share a day-count basis. To force one denominator "
                "everywhere, pass default_day_count_basis=<n> and day_count_basis={}."
            )
        if default_day_count_basis <= 0:
            raise ValueError("default_day_count_basis must be > 0.")
        if report_decimals < 0:
            raise ValueError("report_decimals must be >= 0.")

        self.day_count_basis: Dict[str, int] = dict(MONEY_MARKET_DAY_COUNT_BASIS)
        for ccy, basis in (day_count_basis or {}).items():
            if isinstance(basis, bool) or not isinstance(basis, int) or basis <= 0:
                raise ValueError(
                    f"day_count_basis['{ccy}'] must be a positive integer, got {basis!r}."
                )
            self.day_count_basis[str(ccy).upper()] = basis

        self.default_day_count_basis = default_day_count_basis
        self.pip_factor_overrides: Dict[str, float] = {
            str(k).upper(): float(v) for k, v in (pip_factor_overrides or {}).items()
        }
        self.maturity_buckets: Tuple[Tuple[str, int], ...] = tuple(
            maturity_buckets if maturity_buckets is not None else DEFAULT_MATURITY_BUCKETS
        )
        self._validate_buckets()
        self.report_decimals = report_decimals
        self._unknown_basis_logged: Set[str] = set()

    # --- configuration helpers -------------------------------------------------

    def _validate_buckets(self) -> None:
        if not self.maturity_buckets:
            raise ValueError("maturity_buckets must contain at least one bucket.")
        last = -1
        for label, upper in self.maturity_buckets:
            if isinstance(upper, bool) or not isinstance(upper, int) or upper <= last:
                raise ValueError(
                    "maturity_buckets bounds must be strictly ascending non-negative "
                    f"integers; got {label!r} -> {upper!r}."
                )
            last = upper

    def day_count_basis_for(self, currency: str) -> int:
        """Money-market day-count denominator for ``currency``.

        Falls back to ``default_day_count_basis`` for unlisted currencies and
        logs that fallback once per currency — an unverified convention is a
        pricing assumption, not a default worth hiding.
        """
        ccy = currency.upper()
        basis = self.day_count_basis.get(ccy)
        if basis is None:
            if ccy not in self._unknown_basis_logged:
                self._unknown_basis_logged.add(ccy)
                logger.warning(
                    "No verified money-market day-count basis for %s; assuming Actual/%d. "
                    "Confirm the convention with the rate administrator and pass it via "
                    "day_count_basis.", ccy, self.default_day_count_basis,
                )
            return self.default_day_count_basis
        return basis

    def pip_factor_for(self, currency_pair: str, quote_currency: str) -> float:
        """Points per one unit of the quoted rate for this pair."""
        override = self.pip_factor_overrides.get(currency_pair.upper())
        if override is not None:
            if override <= 0:
                raise ValueError(
                    f"pip_factor_overrides['{currency_pair}'] must be > 0, got {override}."
                )
            return override
        return QUOTE_CURRENCY_PIP_FACTOR.get(quote_currency.upper(), DEFAULT_PIP_FACTOR)

    def maturity_bucket_for(self, days_to_maturity: int) -> str:
        """Gap-report bucket label for a remaining life in calendar days."""
        for label, upper in self.maturity_buckets:
            if days_to_maturity <= upper:
                return label
        return BUCKET_BEYOND_ONE_YEAR

    # --- pricing ---------------------------------------------------------------

    def calculate_cirp_forward_rate(
        self,
        spot_rate: float,
        base_interest_rate: float,
        quote_interest_rate: float,
        days_to_maturity: int,
        base_currency: Optional[str] = None,
        quote_currency: Optional[str] = None,
    ) -> float:
        """Theoretical forward rate under Covered Interest Rate Parity.

        ``F = S * (1 + r_quote * T/B_quote) / (1 + r_base * T/B_base)``

        Each leg accrues simple interest on **its own** money-market basis
        ``B``. Supply ``base_currency``/``quote_currency`` so the verified
        conventions apply; omitting them uses ``default_day_count_basis`` for
        both legs, which is only correct when the two currencies share a basis.

        The result is a theoretical benchmark, not the tradable forward where a
        cross-currency basis is present, and it is returned unrounded — 1e-6 on
        a rate is 100 quote-currency units on a 100mm notional, so round at your
        reporting boundary rather than here.

        Args:
            spot_rate: Current spot, quote per base. Must be finite and > 0.
            base_interest_rate: Base-currency simple rate as a decimal
                (``0.03`` = 3%). May be negative.
            quote_interest_rate: Quote-currency simple rate as a decimal.
            days_to_maturity: Calendar days to settlement, >= 0.
            base_currency: ISO code used to resolve the base leg's basis.
            quote_currency: ISO code used to resolve the quote leg's basis.

        Returns:
            The CIRP forward rate, in quote per base.

        Raises:
            ValueError: On a non-positive or non-finite spot, a non-finite
                rate, negative ``days_to_maturity``, or an accrual factor that
                is not strictly positive (a deeply negative rate over a long
                tenor drives ``1 + r*t`` to zero or below, where the
                simple-interest parity form breaks down).
        """
        if not math.isfinite(spot_rate) or spot_rate <= 0:
            raise ValueError(f"spot_rate must be finite and > 0, got {spot_rate!r}.")
        if not math.isfinite(base_interest_rate) or not math.isfinite(quote_interest_rate):
            raise ValueError(
                "base_interest_rate and quote_interest_rate must be finite, got "
                f"{base_interest_rate!r} and {quote_interest_rate!r}."
            )
        if days_to_maturity < 0:
            raise ValueError(f"days_to_maturity must be >= 0, got {days_to_maturity}.")

        base_basis = (
            self.day_count_basis_for(base_currency)
            if base_currency else self.default_day_count_basis
        )
        quote_basis = (
            self.day_count_basis_for(quote_currency)
            if quote_currency else self.default_day_count_basis
        )

        base_accrual = 1.0 + base_interest_rate * (days_to_maturity / float(base_basis))
        quote_accrual = 1.0 + quote_interest_rate * (days_to_maturity / float(quote_basis))
        if base_accrual <= 0 or quote_accrual <= 0:
            raise ValueError(
                "Simple-interest accrual factor is not positive "
                f"(base {base_accrual:.6f}, quote {quote_accrual:.6f}). Check the sign and "
                "magnitude of the rates against the tenor."
            )

        return spot_rate * (quote_accrual / base_accrual)

    def calculate_forward_points(
        self,
        forward_rate: float,
        spot_rate: float,
        currency_pair: str,
        quote_currency: str,
    ) -> float:
        """Forward (swap) points: ``(F - S)`` scaled to the pair's pip size.

        A pip is the fourth decimal for most pairs and the second decimal where
        the quote currency is yen; using 10,000 on USD/JPY overstates the points
        by 100x.
        """
        return (forward_rate - spot_rate) * self.pip_factor_for(currency_pair, quote_currency)

    # --- validation ------------------------------------------------------------

    @staticmethod
    def _validate_currency(code: str, label: str) -> str:
        upper = str(code).strip().upper()
        if not _CURRENCY_RE.match(upper):
            raise ValueError(f"{label} must be a 3-letter ISO 4217 code, got {code!r}.")
        return upper

    def _validate_position(self, pos: FxContractPosition) -> None:
        if not str(pos.contract_id).strip():
            raise ValueError("contract_id must be a non-empty string.")

        base = self._validate_currency(pos.base_currency, "base_currency")
        quote = self._validate_currency(pos.quote_currency, "quote_currency")
        if base == quote:
            raise ValueError(
                f"Contract '{pos.contract_id}': base and quote currency are both {base}."
            )
        pair = str(pos.currency_pair).strip().upper()
        if pair != f"{base}/{quote}":
            raise ValueError(
                f"Contract '{pos.contract_id}': currency_pair {pos.currency_pair!r} does not "
                f"match base/quote ({base}/{quote}). A mismatched pair inverts the quote."
            )

        contract_type = str(pos.contract_type).strip().upper()
        if contract_type not in VALID_CONTRACT_TYPES:
            raise ValueError(
                f"Contract '{pos.contract_id}': contract_type must be one of "
                f"{VALID_CONTRACT_TYPES}, got {pos.contract_type!r}."
            )

        side = str(pos.position_side).strip().upper()
        if side not in VALID_SIDES:
            raise ValueError(
                f"Contract '{pos.contract_id}': position_side must be one of {VALID_SIDES}, "
                f"got {pos.position_side!r}. Unrecognised sides are never defaulted to SELL."
            )

        leg = None if pos.swap_leg is None else str(pos.swap_leg).strip().upper()
        if contract_type == CONTRACT_FX_SWAP:
            if leg not in VALID_SWAP_LEGS:
                raise ValueError(
                    f"Contract '{pos.contract_id}': an FX_SWAP row must declare "
                    f"swap_leg={VALID_SWAP_LEGS}. Supply the swap as its two legs, not as a "
                    "single row."
                )
        elif leg is not None:
            raise ValueError(
                f"Contract '{pos.contract_id}': swap_leg is only valid on an FX_SWAP, got "
                f"{pos.swap_leg!r} on an {contract_type}."
            )

        if isinstance(pos.notional_base_currency, bool) or not isinstance(
            pos.notional_base_currency, (int, float)
        ) or not math.isfinite(pos.notional_base_currency):
            raise ValueError(
                f"Contract '{pos.contract_id}': notional_base_currency must be a finite number."
            )
        if pos.notional_base_currency <= 0:
            raise ValueError(
                f"Contract '{pos.contract_id}': notional_base_currency must be > 0, got "
                f"{pos.notional_base_currency!r}. Direction belongs in position_side."
            )
        if isinstance(pos.agreed_forward_rate, bool) or not isinstance(
            pos.agreed_forward_rate, (int, float)
        ):
            raise ValueError(
                f"Contract '{pos.contract_id}': agreed_forward_rate must be a number, got "
                f"{pos.agreed_forward_rate!r}."
            )
        if not math.isfinite(pos.agreed_forward_rate) or pos.agreed_forward_rate <= 0:
            raise ValueError(
                f"Contract '{pos.contract_id}': agreed_forward_rate must be finite and > 0, "
                f"got {pos.agreed_forward_rate!r}."
            )
        if isinstance(pos.days_to_maturity, bool) or not isinstance(pos.days_to_maturity, int):
            raise ValueError(
                f"Contract '{pos.contract_id}': days_to_maturity must be an int, got "
                f"{pos.days_to_maturity!r}."
            )
        if pos.days_to_maturity < 0:
            raise ValueError(
                f"Contract '{pos.contract_id}': days_to_maturity must be >= 0, got "
                f"{pos.days_to_maturity}. A matured contract is settled, not marked."
            )

    @staticmethod
    def _validate_market_data(
        pair: str, data: Mapping[str, float]
    ) -> Tuple[float, float, float, Optional[float]]:
        if not isinstance(data, Mapping):
            raise ValueError(
                f"market_rates['{pair}'] must be a mapping, got {type(data).__name__}."
            )
        for key in ("spot", "r_base", "r_quote"):
            if key not in data:
                raise ValueError(f"market_rates['{pair}'] is missing required key '{key}'.")
        try:
            spot = float(data["spot"])
            r_base = float(data["r_base"])
            r_quote = float(data["r_quote"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"market_rates['{pair}'] contains a non-numeric value: {exc}"
            ) from exc
        if not math.isfinite(spot) or spot <= 0:
            raise ValueError(
                f"market_rates['{pair}']['spot'] must be finite and > 0, got {spot!r}."
            )
        if not math.isfinite(r_base) or not math.isfinite(r_quote):
            raise ValueError(f"market_rates['{pair}'] interest rates must be finite.")

        raw_observed = data.get("market_forward_rate")
        observed: Optional[float] = None
        if raw_observed is not None:
            try:
                observed = float(raw_observed)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"market_rates['{pair}']['market_forward_rate'] is not numeric: {exc}"
                ) from exc
            if not math.isfinite(observed) or observed <= 0:
                raise ValueError(
                    f"market_rates['{pair}']['market_forward_rate'] must be finite and > 0, "
                    f"got {raw_observed!r}."
                )
        return spot, r_base, r_quote, observed

    @staticmethod
    def _validate_swap_legs(positions: Sequence[FxContractPosition]) -> None:
        """An FX swap is two opposite legs; anything else is a data error.

        Two same-direction legs booked under one ``contract_id`` double the
        exposure instead of rolling it, and net to zero P&L only by accident.
        """
        swaps: Dict[str, List[FxContractPosition]] = {}
        for pos in positions:
            if str(pos.contract_type).strip().upper() == CONTRACT_FX_SWAP:
                swaps.setdefault(str(pos.contract_id), []).append(pos)

        for contract_id, legs in swaps.items():
            found = {str(leg.swap_leg).strip().upper() for leg in legs}
            if len(legs) != 2 or found != set(VALID_SWAP_LEGS):
                raise ValueError(
                    f"FX swap '{contract_id}' must have exactly one NEAR leg and one FAR leg; "
                    f"found {sorted(found)} across {len(legs)} row(s)."
                )
            near = next(l for l in legs if str(l.swap_leg).strip().upper() == LEG_NEAR)
            far = next(l for l in legs if str(l.swap_leg).strip().upper() == LEG_FAR)
            if str(near.position_side).strip().upper() == str(far.position_side).strip().upper():
                raise ValueError(
                    f"FX swap '{contract_id}': near and far legs are both "
                    f"{str(near.position_side).strip().upper()}. The far leg must reverse the "
                    "near leg."
                )
            if far.days_to_maturity <= near.days_to_maturity:
                raise ValueError(
                    f"FX swap '{contract_id}': far leg matures in {far.days_to_maturity} days, "
                    f"not after the near leg's {near.days_to_maturity}."
                )
            if str(near.currency_pair).strip().upper() != str(far.currency_pair).strip().upper():
                raise ValueError(
                    f"FX swap '{contract_id}': legs quote different pairs "
                    f"({near.currency_pair} vs {far.currency_pair})."
                )

    # --- portfolio audit -------------------------------------------------------

    def audit_portfolio_positions(
        self,
        positions: Sequence[FxContractPosition],
        market_rates: Mapping[str, Mapping[str, float]],
        reporting_currency: Optional[str] = None,
        reporting_fx_rates: Optional[Mapping[str, float]] = None,
    ) -> FxForwardPositionReport:
        """Revalue a book of FX forwards and swap legs.

        For each row: resolve the per-currency day-count bases, compute the CIRP
        forward, mark against the observed market forward where one was supplied
        (otherwise against CIRP), discount the resulting quote-currency cash
        flow, and accumulate exposure by currency and by maturity bucket.

        Args:
            positions: Non-empty sequence of ``FxContractPosition``. FX swaps
                must appear as two legs sharing a ``contract_id``.
            market_rates: ``{currency_pair: {'spot', 'r_base', 'r_quote'}}``,
                optionally with ``'market_forward_rate'`` — the observable
                outright for that pair and tenor. Where present it is used for
                the mark, because CIRP off domestic rates omits the
                cross-currency basis. Supplying one rate set per pair assumes
                every position in that pair shares a tenor; book distinct
                tenors under distinct pair keys or audit them separately.
            reporting_currency: Optional ISO code for a single consolidated
                P&L figure.
            reporting_fx_rates: ``{quote_currency: units of reporting currency
                per 1 unit}``. Required for every quote currency in the book
                other than ``reporting_currency`` itself.

        Returns:
            ``FxForwardPositionReport``.

        Raises:
            ValueError: On an empty book, an invalid position, missing or
                invalid market data, an ill-formed FX swap, a duplicated row, or
                a requested reporting currency without the rates to reach it.
        """
        if not positions:
            raise ValueError("Positions list cannot be empty.")

        for pos in positions:
            self._validate_position(pos)
        self._validate_swap_legs(positions)

        seen_keys: Set[Tuple[str, Optional[str]]] = set()
        for pos in positions:
            leg = None if pos.swap_leg is None else str(pos.swap_leg).strip().upper()
            key = (str(pos.contract_id), leg)
            if key in seen_keys:
                raise ValueError(
                    f"Duplicate position row for contract_id '{pos.contract_id}'"
                    + (f" leg {leg}" if leg else "")
                    + ". Duplicated rows double the reported exposure."
                )
            seen_keys.add(key)

        details: List[FxValuationDetail] = []
        warnings: List[str] = []
        mtm_pv: Dict[str, float] = {}
        mtm_undiscounted: Dict[str, float] = {}
        exposure: Dict[str, float] = {}
        bucket_exposure: Dict[str, Dict[str, float]] = {}

        for pos in positions:
            pair = str(pos.currency_pair).strip().upper()
            base = str(pos.base_currency).strip().upper()
            quote = str(pos.quote_currency).strip().upper()
            side = str(pos.position_side).strip().upper()
            leg = None if pos.swap_leg is None else str(pos.swap_leg).strip().upper()

            m_data = market_rates.get(pair)
            if m_data is None:
                raise ValueError(f"Missing market rate data for currency pair '{pair}'.")
            spot, r_base, r_quote, observed_forward = self._validate_market_data(pair, m_data)

            base_basis = self.day_count_basis_for(base)
            quote_basis = self.day_count_basis_for(quote)

            cirp_forward = self.calculate_cirp_forward_rate(
                spot_rate=spot,
                base_interest_rate=r_base,
                quote_interest_rate=r_quote,
                days_to_maturity=pos.days_to_maturity,
                base_currency=base,
                quote_currency=quote,
            )
            if observed_forward is not None:
                valuation_forward = observed_forward
                mtm_basis = MTM_BASIS_OBSERVED
            else:
                valuation_forward = cirp_forward
                mtm_basis = MTM_BASIS_CIRP

            if pos.days_to_maturity <= SPOT_SETTLEMENT_DAYS:
                msg = (
                    f"Contract '{pos.contract_id}'"
                    + (f" ({leg} leg)" if leg else "")
                    + f" settles in {pos.days_to_maturity} day(s), inside the conventional "
                    "spot window; it is a spot exposure rather than an outright forward."
                )
                warnings.append(msg)
                logger.warning(msg)

            direction = 1.0 if side == SIDE_BUY else -1.0
            rate_diff = direction * (valuation_forward - pos.agreed_forward_rate)
            undiscounted = pos.notional_base_currency * rate_diff

            # The P&L is a quote-currency cash flow at settlement: discount it on
            # the quote currency's own money-market basis.
            quote_accrual = 1.0 + r_quote * (pos.days_to_maturity / float(quote_basis))
            if quote_accrual <= 0:
                raise ValueError(
                    f"Contract '{pos.contract_id}': quote-currency accrual factor "
                    f"{quote_accrual:.6f} is not positive; cannot discount the mark."
                )
            discount_factor = 1.0 / quote_accrual
            pv = undiscounted * discount_factor

            mtm_pv[quote] = mtm_pv.get(quote, 0.0) + pv
            mtm_undiscounted[quote] = mtm_undiscounted.get(quote, 0.0) + undiscounted

            # A forward commits both currencies: long base is short quote.
            base_exposure = direction * pos.notional_base_currency
            quote_exposure = -base_exposure * pos.agreed_forward_rate
            exposure[base] = exposure.get(base, 0.0) + base_exposure
            exposure[quote] = exposure.get(quote, 0.0) + quote_exposure

            bucket = self.maturity_bucket_for(pos.days_to_maturity)
            slot = bucket_exposure.setdefault(bucket, {})
            slot[base] = slot.get(base, 0.0) + base_exposure
            slot[quote] = slot.get(quote, 0.0) + quote_exposure

            pip_factor = self.pip_factor_for(pair, quote)
            details.append(
                FxValuationDetail(
                    contract_id=pos.contract_id,
                    currency_pair=pair,
                    base_currency=base,
                    quote_currency=quote,
                    contract_type=str(pos.contract_type).strip().upper(),
                    swap_leg=leg,
                    position_side=side,
                    notional_base=pos.notional_base_currency,
                    days_to_maturity=pos.days_to_maturity,
                    agreed_forward_rate=pos.agreed_forward_rate,
                    current_spot_rate=spot,
                    cirp_forward_rate=self._round_rate(cirp_forward),
                    valuation_forward_rate=self._round_rate(valuation_forward),
                    mtm_basis=mtm_basis,
                    pip_factor=pip_factor,
                    swap_points=round((valuation_forward - spot) * pip_factor, 2),
                    contract_forward_points=round(
                        (pos.agreed_forward_rate - spot) * pip_factor, 2
                    ),
                    base_day_count_basis=base_basis,
                    quote_day_count_basis=quote_basis,
                    undiscounted_mtm_quote=self._round_money(undiscounted),
                    quote_discount_factor=round(discount_factor, 10),
                    mtm_pv_quote=self._round_money(pv),
                    base_exposure=self._round_money(base_exposure),
                    quote_exposure=self._round_money(quote_exposure),
                    maturity_bucket=bucket,
                )
            )

        reporting_total: Optional[float] = None
        reporting_ccy: Optional[str] = None
        if reporting_currency is not None:
            reporting_ccy = self._validate_currency(reporting_currency, "reporting_currency")
            try:
                rates = {
                    self._validate_currency(k, "reporting_fx_rates key"): float(v)
                    for k, v in (reporting_fx_rates or {}).items()
                }
            except TypeError as exc:
                raise ValueError(
                    f"reporting_fx_rates contains a non-numeric rate: {exc}"
                ) from exc
            rates.setdefault(reporting_ccy, 1.0)
            total = 0.0
            for ccy, amount in mtm_pv.items():
                rate = rates.get(ccy)
                if rate is None:
                    raise ValueError(
                        f"reporting_fx_rates is missing '{ccy}', required to express the book "
                        f"in {reporting_ccy}. P&L in different quote currencies is not additive."
                    )
                if not math.isfinite(rate) or rate <= 0:
                    raise ValueError(
                        f"reporting_fx_rates['{ccy}'] must be finite and > 0, got {rate!r}."
                    )
                total += amount * rate
            reporting_total = self._round_money(total)

        pv_rounded = {c: self._round_money(v) for c, v in sorted(mtm_pv.items())}
        undisc_rounded = {c: self._round_money(v) for c, v in sorted(mtm_undiscounted.items())}
        exposure_rounded = {c: self._round_money(v) for c, v in sorted(exposure.items())}
        buckets_rounded = {
            b: {c: self._round_money(v) for c, v in sorted(slot.items())}
            for b, slot in sorted(bucket_exposure.items())
        }

        notes = (
            f"FX FORWARD & SWAP AUDIT COMPLETE ({len(details)} valuation rows): "
            f"MtM PV by quote currency = {pv_rounded}. Net exposure = {exposure_rounded}."
        )
        if reporting_ccy is not None and reporting_total is not None:
            notes += f" Consolidated MtM PV = {reporting_total:,.2f} {reporting_ccy}."
        if warnings:
            notes += f" {len(warnings)} warning(s) raised."
        logger.info(notes)

        return FxForwardPositionReport(
            total_open_contracts=len(details),
            unrealized_mtm_pv_by_quote_currency=pv_rounded,
            unrealized_mtm_undiscounted_by_quote_currency=undisc_rounded,
            net_exposure_by_currency=exposure_rounded,
            net_exposure_by_maturity_bucket=buckets_rounded,
            valuation_details=details,
            audit_notes=notes,
            reporting_currency=reporting_ccy,
            net_unrealized_mtm_pv_reporting_currency=reporting_total,
            warnings=warnings,
        )

    # --- presentation ----------------------------------------------------------

    def _round_money(self, value: float) -> float:
        return round(value, self.report_decimals)

    @staticmethod
    def _round_rate(value: float) -> float:
        return round(value, 8)
