"""IDX (Bursa Efek Indonesia) pre-trade order validation against JATS rules.

Encodes IDX Peraturan Nomor II-A (Perdagangan Efek Bersifat Ekuitas) as published
on the IDX "Trading Hours and Mechanism" page: Kep-00196/BEI/12-2024, effective
8 April 2025 -- the amendment that made Auto Rejection asymmetric.

All prices are whole Rupiah (Rp). IDX quotes equities in integer Rupiah, so this
module works in integer arithmetic for every tick, band and lot decision rather
than relying on float tolerances.
"""
import logging
import math
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Market segments ("pasar"): 'RG' Pasar Reguler, 'TN' Pasar Tunai, 'NG' Pasar Negosiasi.
VALID_IDX_BOARDS = ("RG", "TN", "NG")

# Segments trading on the continuous JATS order book under round-lot, tick,
# volume and Auto Rejection control. Pasar Negosiasi is bilaterally negotiated
# and is exempt from all four.
ORDER_BOOK_SEGMENTS = ("RG", "TN")

# Listing boards. Auto Rejection percentages differ between the three "ordinary"
# boards and the Acceleration / Special Monitoring (Papan Pemantauan Khusus) boards.
ORDINARY_LISTING_BOARDS = ("MAIN", "DEVELOPMENT", "NEW_ECONOMY")
RESTRICTED_LISTING_BOARDS = ("ACCELERATION", "WATCHLIST")
VALID_LISTING_BOARDS = ORDINARY_LISTING_BOARDS + RESTRICTED_LISTING_BOARDS

VALID_SIDES = ("BUY", "SELL")

SHARES_PER_LOT = 100

# Fraksi Harga: (exclusive upper bound of the reference-price band, tick size).
# The tick is selected from the PREVIOUS closing price and is fixed for the whole
# trading day -- it does not move when the intraday price crosses a band.
FRAKSI_HARGA_SCHEDULE: Tuple[Tuple[Optional[int], int], ...] = (
    (200, 1),
    (500, 2),
    (2000, 5),
    (5000, 10),
    (None, 25),
)

# Auto Rejection Atas (upper) for MAIN / DEVELOPMENT / NEW_ECONOMY boards, keyed
# by the INCLUSIVE upper bound of the reference-price band -- IDX publishes these
# bands as "Rp 50-200 / >Rp 200-5,000 / >Rp 5,000", unlike the Fraksi Harga bands
# above which are exclusive ("<Rp 200 / Rp 200-500"). A Rp 200 reference price
# therefore takes a Rp 2 tick but a 35% ARA; that asymmetry is IDX's, not a bug.
# Auto Rejection Bawah (lower) has been a flat 15% across all bands since
# 8 April 2025.
AUTO_REJECTION_ATAS_SCHEDULE: Tuple[Tuple[Optional[int], float], ...] = (
    (200, 35.0),
    (5000, 25.0),
    (None, 20.0),
)
AUTO_REJECTION_BAWAH_PCT = 15.0

# Acceleration / Watchlist boards: +/- Rp 1 absolute for a reference price of
# Rp 1-10, and +/- 10% above that.
RESTRICTED_BOARD_ABSOLUTE_BAND_MAX_PRICE = 10
RESTRICTED_BOARD_ABSOLUTE_BAND_RUPIAH = 1
RESTRICTED_BOARD_AUTO_REJECTION_PCT = 10.0

# Lowest price tradeable on the continuous order book. IDX has announced a move
# to Rp 1 for Pasar Reguler/Tunai (member trials 22 and 29 August 2026, targeted
# 7 September 2026); until that takes effect the floor is Rp 50 on the ordinary
# boards. Override via the engine constructor if IDX moves first.
MINIMUM_PRICE_ORDINARY_BOARDS = 50
MINIMUM_PRICE_RESTRICTED_BOARDS = 1

# Volume Auto Rejection: an order is rejected if its size exceeds 50,000 lots or
# 5% of listed shares, whichever is SMALLER.
MAX_ORDER_LOTS = 50_000
MAX_ORDER_LISTED_SHARE_FRACTION = 0.05


@dataclass
class IdxOrderPayload:
    """A single equity order presented to the IDX/JATS gateway.

    Attributes:
        ticker: 4-letter IDX equity code, e.g. 'BBCA', 'TLKM'.
        board_type: Market segment -- 'RG', 'TN' or 'NG'.
        side: 'BUY' or 'SELL'.
        price: Limit price in whole Rupiah.
        quantity: Order size in shares (not lots).
        reference_price: Acuan Harga -- normally the previous Pasar Reguler
            closing price (or the theoretical price after a corporate action, or
            the listing price on a debut). Drives BOTH the applicable tick size
            and the Auto Rejection band, so it must be the previous close, never
            the current order price or last traded price.
        listing_board: Listing board driving Auto Rejection percentages.
        listed_shares: Total shares listed, used for the 5%-of-listed-shares
            volume cap. When None only the 50,000-lot cap is enforced.
    """

    ticker: str
    board_type: str
    side: str
    price: float
    quantity: int
    reference_price: float
    listing_board: str = "MAIN"
    listed_shares: Optional[int] = None


@dataclass
class IdxOrderReport:
    """Structured outcome of a pre-trade IDX order audit."""

    ticker: str
    board_type: str
    side: str
    price: float
    applicable_fraksi_harga: int      # Tick size in Rp (1, 2, 5, 10 or 25)
    quantity_shares: int
    lots_count: int
    is_price_tick_valid: bool
    is_board_lot_valid: bool
    is_auto_rejection_valid: bool
    status: str
    audit_notes: str
    listing_board: str = "MAIN"
    is_minimum_price_valid: bool = True
    is_order_volume_valid: bool = True
    # Auto Rejection band in Rp; None on Pasar Negosiasi, where AR does not apply.
    auto_rejection_lower_price: Optional[float] = None
    auto_rejection_upper_price: Optional[float] = None
    # Jenjang maksimum perubahan harga (10x the tick), informational only --
    # see references/standards.md for why it is not enforced here.
    max_price_step: int = 0


class IdxStockExchangeApiEngine:
    """Pre-trade validator for Indonesia Stock Exchange (IDX / BEI) equity orders.

    Enforces the JATS constraints of IDX Peraturan Nomor II-A: 4-letter equity
    codes, Fraksi Harga tick sizes, the 100-share round lot, the minimum price
    floor, the per-order volume cap and the asymmetric Auto Rejection band.

    This is a client-side pre-trade filter, not a substitute for the exchange's
    own controls: JATS remains authoritative and may reject an order this engine
    approves (trading halts, suspensions and short-sale rules are not modelled).
    """

    def __init__(
        self,
        max_auto_rejection_pct: Optional[float] = None,
        minimum_price_ordinary_boards: int = MINIMUM_PRICE_ORDINARY_BOARDS,
    ) -> None:
        """
        Args:
            max_auto_rejection_pct: Optional in-house symmetric cap, in percent,
                applied ON TOP of the exchange schedule (the tighter of the two
                wins). Leave as None to audit against the IDX limits alone. This
                is a house risk control, not an IDX rule.
            minimum_price_ordinary_boards: Price floor in Rp for the Main,
                Development and New Economy boards. Exposed so the announced
                Rp 50 -> Rp 1 change can be adopted without a code change.
        """
        if max_auto_rejection_pct is not None and max_auto_rejection_pct <= 0:
            raise ValueError(
                f"max_auto_rejection_pct must be positive or None, got {max_auto_rejection_pct}."
            )
        if minimum_price_ordinary_boards < 1:
            raise ValueError(
                f"minimum_price_ordinary_boards must be >= 1, got {minimum_price_ordinary_boards}."
            )
        self.max_auto_rejection_pct = max_auto_rejection_pct
        self.minimum_price_ordinary_boards = minimum_price_ordinary_boards

    # ---------------------------------------------------------------- helpers

    def validate_ticker(self, ticker: str) -> str:
        """Normalises and validates a 4-letter IDX equity code.

        Rights ('TLKM-R'), warrants ('TLKM-W') and other suffixed instruments are
        deliberately rejected: their trading rules are out of scope here.
        """
        if not isinstance(ticker, str):
            raise TypeError(f"IDX ticker must be a string, got {type(ticker).__name__}.")
        clean = ticker.strip().upper()
        if len(clean) != 4 or not clean.isalpha() or not clean.isascii():
            raise ValueError(
                f"Invalid IDX Ticker '{ticker}'. Equity codes must be 4 ASCII letters "
                f"(e.g. BBCA). Rights ('-R') and warrants ('-W') are out of scope."
            )
        return clean

    def get_idx_fraksi_harga(self, reference_price: float) -> int:
        """Returns the Fraksi Harga (tick size) in Rp for a reference price.

        IDX selects the tick from the PREVIOUS trading day's closing price and
        holds it fixed for the whole trading day, adjusting it only on the next
        trading day if that day's close lands in a different band. Passing the
        live order price here would mis-size the tick on any day the price
        crosses a band boundary.

        Bands: <200 -> Rp 1; 200-<500 -> Rp 2; 500-<2,000 -> Rp 5;
        2,000-<5,000 -> Rp 10; >=5,000 -> Rp 25.
        """
        price = self._require_positive_price(reference_price, "Reference price")
        for upper_bound, tick in FRAKSI_HARGA_SCHEDULE:
            if upper_bound is None or price < upper_bound:
                return tick
        raise AssertionError("FRAKSI_HARGA_SCHEDULE must end with an open band.")

    def get_auto_rejection_bounds(
        self, reference_price: float, listing_board: str = "MAIN"
    ) -> Tuple[float, float]:
        """Returns the (lower, upper) Auto Rejection prices in Rp.

        Main / Development / New Economy boards: Auto Rejection Atas is 35% for a
        reference price of Rp 50-200, 25% above Rp 200 up to Rp 5,000 and 20%
        above Rp 5,000; Auto Rejection Bawah is a flat 15% (asymmetric since
        8 April 2025). Acceleration and Watchlist boards use +/- Rp 1 up to a
        Rp 10 reference price and +/- 10% above it.

        The lower bound is clamped to the applicable minimum price floor: a stock
        already sitting at the floor cannot be sold below it.
        """
        price = self._require_positive_price(reference_price, "Reference price")
        board = self._normalise_listing_board(listing_board)

        if board in RESTRICTED_LISTING_BOARDS:
            if price <= RESTRICTED_BOARD_ABSOLUTE_BAND_MAX_PRICE:
                lower = price - RESTRICTED_BOARD_ABSOLUTE_BAND_RUPIAH
                upper = price + RESTRICTED_BOARD_ABSOLUTE_BAND_RUPIAH
            else:
                lower = price * (1.0 - RESTRICTED_BOARD_AUTO_REJECTION_PCT / 100.0)
                upper = price * (1.0 + RESTRICTED_BOARD_AUTO_REJECTION_PCT / 100.0)
        else:
            lower = price * (1.0 - AUTO_REJECTION_BAWAH_PCT / 100.0)
            upper = price * (1.0 + self._auto_rejection_atas_pct(price) / 100.0)

        # Optional house cap tightens, never widens, the exchange band.
        if self.max_auto_rejection_pct is not None:
            house = self.max_auto_rejection_pct / 100.0
            lower = max(lower, price * (1.0 - house))
            upper = min(upper, price * (1.0 + house))

        return max(lower, float(self._minimum_price(board))), upper

    def _auto_rejection_atas_pct(self, reference_price: float) -> float:
        for upper_bound, pct in AUTO_REJECTION_ATAS_SCHEDULE:
            if upper_bound is None or reference_price <= upper_bound:
                return pct
        raise AssertionError("AUTO_REJECTION_ATAS_SCHEDULE must end with an open band.")

    def _minimum_price(self, listing_board: str) -> int:
        if listing_board in RESTRICTED_LISTING_BOARDS:
            return MINIMUM_PRICE_RESTRICTED_BOARDS
        return self.minimum_price_ordinary_boards

    def max_order_shares(self, listed_shares: Optional[int] = None) -> int:
        """Per-order volume cap in shares.

        50,000 lots or 5% of listed shares, whichever is smaller. Falls back to
        the lot cap alone when the listed share count is unknown.
        """
        lot_cap = MAX_ORDER_LOTS * SHARES_PER_LOT
        if listed_shares is None:
            return lot_cap
        if (
            isinstance(listed_shares, bool)
            or not isinstance(listed_shares, int)
            or listed_shares <= 0
        ):
            raise ValueError(
                f"listed_shares must be a positive integer or None, got {listed_shares!r}."
            )
        return min(lot_cap, math.floor(MAX_ORDER_LISTED_SHARE_FRACTION * listed_shares))

    @staticmethod
    def _require_positive_price(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
        price = float(value)
        if not math.isfinite(price) or price <= 0.0:
            raise ValueError(f"{label} must be a finite positive Rupiah amount, got {value!r}.")
        return price

    @staticmethod
    def _normalise_listing_board(listing_board: str) -> str:
        if not isinstance(listing_board, str):
            raise TypeError(
                f"listing_board must be a string, got {type(listing_board).__name__}."
            )
        board = listing_board.strip().upper().replace("-", "_").replace(" ", "_")
        if board not in VALID_LISTING_BOARDS:
            raise ValueError(
                f"Invalid IDX listing board '{listing_board}'. "
                f"Must be one of {VALID_LISTING_BOARDS}."
            )
        return board

    @staticmethod
    def _normalise_segment(board_type: str) -> str:
        if not isinstance(board_type, str):
            raise TypeError(f"board_type must be a string, got {type(board_type).__name__}.")
        board = board_type.strip().upper()
        if board not in VALID_IDX_BOARDS:
            raise ValueError(
                f"Invalid IDX market segment '{board_type}'. Must be one of {VALID_IDX_BOARDS}."
            )
        return board

    @staticmethod
    def _normalise_side(side: str) -> str:
        if not isinstance(side, str):
            raise TypeError(f"side must be a string, got {type(side).__name__}.")
        clean = side.strip().upper()
        if clean not in VALID_SIDES:
            raise ValueError(f"Invalid order side '{side}'. Must be one of {VALID_SIDES}.")
        return clean

    @staticmethod
    def _validate_quantity(quantity: int) -> int:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise TypeError(
                f"Order quantity must be an integer number of shares, "
                f"got {type(quantity).__name__}."
            )
        if quantity <= 0:
            raise ValueError(f"Order quantity must be positive, got {quantity}.")
        return quantity

    # ------------------------------------------------------------------- main

    def validate_and_route_order(self, payload: IdxOrderPayload) -> IdxOrderReport:
        """Audits an order against IDX Peraturan II-A and returns an IdxOrderReport.

        Structurally invalid input (bad ticker, segment, side, quantity or
        reference price) raises; genuine exchange-rule breaches are reported as a
        rejection status so the caller can log and route around them.

        Pasar Negosiasi ('NG') is bilaterally negotiated: round lot, tick size,
        the volume cap and Auto Rejection are all inapplicable there and are
        reported as satisfied with a null Auto Rejection band.
        """
        ticker_clean = self.validate_ticker(payload.ticker)
        board_clean = self._normalise_segment(payload.board_type)
        listing_board = self._normalise_listing_board(payload.listing_board)
        side_clean = self._normalise_side(payload.side)
        quantity = self._validate_quantity(payload.quantity)
        reference_price = self._require_positive_price(
            payload.reference_price, "Reference price"
        )
        price = self._require_positive_price(payload.price, "Order price")

        is_order_book = board_clean in ORDER_BOOK_SEGMENTS
        tick_size = self.get_idx_fraksi_harga(reference_price)
        minimum_price = self._minimum_price(listing_board)

        # 1. Minimum price floor (order-book segments only).
        is_min_price_valid = (not is_order_book) or price >= minimum_price

        # 2. Fraksi Harga alignment. IDX quotes whole Rupiah, so a fractional
        #    price is by definition off-tick. Integer arithmetic, no tolerance.
        if not is_order_book:
            is_tick_valid = True
        elif not float(price).is_integer():
            is_tick_valid = False
        else:
            is_tick_valid = int(price) % tick_size == 0

        # 3. Round lot: 100 shares on Pasar Reguler and Pasar Tunai; Pasar
        #    Negosiasi permits odd lots.
        is_lot_valid = (not is_order_book) or quantity % SHARES_PER_LOT == 0
        lots_count = quantity // SHARES_PER_LOT if quantity % SHARES_PER_LOT == 0 else 0

        # 4. Per-order volume Auto Rejection (order-book segments only).
        max_shares = self.max_order_shares(payload.listed_shares)
        is_volume_valid = (not is_order_book) or quantity <= max_shares

        # 5. Price Auto Rejection band (order-book segments only).
        if is_order_book:
            ar_lower, ar_upper = self.get_auto_rejection_bounds(
                reference_price, listing_board
            )
            is_ar_valid = ar_lower <= price <= ar_upper
        else:
            ar_lower, ar_upper = None, None
            is_ar_valid = True

        status, notes = self._classify(
            ticker=ticker_clean,
            board=board_clean,
            side=side_clean,
            price=price,
            quantity=quantity,
            lots_count=lots_count,
            tick_size=tick_size,
            minimum_price=minimum_price,
            max_shares=max_shares,
            ar_lower=ar_lower,
            ar_upper=ar_upper,
            is_min_price_valid=is_min_price_valid,
            is_tick_valid=is_tick_valid,
            is_lot_valid=is_lot_valid,
            is_volume_valid=is_volume_valid,
            is_ar_valid=is_ar_valid,
        )

        return IdxOrderReport(
            ticker=ticker_clean,
            board_type=board_clean,
            side=side_clean,
            price=price,
            applicable_fraksi_harga=tick_size,
            quantity_shares=quantity,
            lots_count=lots_count,
            is_price_tick_valid=is_tick_valid,
            is_board_lot_valid=is_lot_valid,
            is_auto_rejection_valid=is_ar_valid,
            status=status,
            audit_notes=notes,
            listing_board=listing_board,
            is_minimum_price_valid=is_min_price_valid,
            is_order_volume_valid=is_volume_valid,
            auto_rejection_lower_price=ar_lower,
            auto_rejection_upper_price=ar_upper,
            max_price_step=tick_size * 10,
        )

    def _classify(
        self,
        *,
        ticker: str,
        board: str,
        side: str,
        price: float,
        quantity: int,
        lots_count: int,
        tick_size: int,
        minimum_price: int,
        max_shares: int,
        ar_lower: Optional[float],
        ar_upper: Optional[float],
        is_min_price_valid: bool,
        is_tick_valid: bool,
        is_lot_valid: bool,
        is_volume_valid: bool,
        is_ar_valid: bool,
    ) -> Tuple[str, str]:
        """Maps the individual audits to a single status plus an audit note."""
        if not is_min_price_valid:
            notes = (
                f"IDX REJECTED [{ticker}]: Price Rp {price:,.0f} is below the "
                f"Rp {minimum_price:,} minimum for the {board} segment."
            )
            logger.warning(notes)
            return "PRICE_BELOW_MINIMUM", notes
        if not is_tick_valid:
            notes = (
                f"IDX REJECTED [{ticker}]: Price Rp {price:,.2f} violates Fraksi Harga "
                f"tick size (Rp {tick_size})."
            )
            logger.warning(notes)
            return "INVALID_TICK_SIZE", notes
        if not is_lot_valid:
            notes = (
                f"IDX REJECTED [{ticker}]: Quantity {quantity:,} shares is not a multiple "
                f"of 1 Lot ({SHARES_PER_LOT} shares)."
            )
            logger.warning(notes)
            return "INVALID_BOARD_LOT", notes
        if not is_volume_valid:
            notes = (
                f"IDX REJECTED [{ticker}]: Quantity {quantity:,} shares exceeds the "
                f"per-order volume cap of {max_shares:,} shares."
            )
            logger.warning(notes)
            return "INVALID_ORDER_VOLUME", notes
        if not is_ar_valid:
            notes = (
                f"IDX REJECTED [{ticker}]: Price Rp {price:,.0f} is outside the Auto "
                f"Rejection band Rp {ar_lower:,.2f} - Rp {ar_upper:,.2f}."
            )
            logger.warning(notes)
            return "AUTO_REJECTION_EXCEEDED", notes

        # On Pasar Negosiasi the tick and the band are reported for information
        # only -- neither is enforced there -- so label them as such rather than
        # letting a reader infer they were checked.
        if ar_lower is None:
            tick_note = f"Fraksi Harga = Rp {tick_size}, not enforced on Pasar Negosiasi"
            band_note = "Auto Rejection n/a (Pasar Negosiasi)"
        else:
            tick_note = f"Fraksi Harga = Rp {tick_size}"
            band_note = f"Auto Rejection band Rp {ar_lower:,.2f} - Rp {ar_upper:,.2f}"
        notes = (
            f"IDX ORDER VALIDATED [{ticker} - {board} Segment]: {side} {quantity:,} shares "
            f"({lots_count:,} Lots) @ Rp {price:,.0f} ({tick_note}; {band_note})."
        )
        logger.info(notes)
        return "IDX_ORDER_VALIDATED", notes
