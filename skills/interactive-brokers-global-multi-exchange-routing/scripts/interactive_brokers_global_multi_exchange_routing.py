"""
interactive-brokers-global-multi-exchange-routing: pre-flight validator for Interactive
Brokers (TWS API / ib_insync) contract and routing parameters before an order is placed
across IBKR's global venues.

WHAT THIS IS -- AND THE HARD LIMIT ON IT
This module is a *local pre-flight screen*. It catches the parameter mistakes that produce
IBKR error 200 ("No security definition has been found for the request. The specified
contract does not match any in IB's database, usually because of an incorrect or missing
parameter") or an ambiguous-contract error, before you burn a round trip -- and it catches
order-field mistakes that IBKR rejects at order entry.

It is NOT, and cannot be, authoritative. IBKR's contract database is the only authority on
whether a contract exists, which currency it trades in, and where it may be routed:

  * `ContractDetails.validExchanges` is documented as "Valid exchange fields when placing
    an order for this contract" -- that list, not a local table, decides whether your
    `exchange` value is legal.
  * `ContractDetails.aggGroup == -1` marks a contract that *cannot* be smart-routed, so
    whether `SMART` is even available is a per-contract fact you must look up.
  * `Contract.conId` is "the unique IB contract identifier". Resolving to a conId via
    `reqContractDetails` and submitting on the conId removes symbol ambiguity entirely.

So: run this validator, then resolve with `reqContractDetails`, then submit. An
`IBKR_ROUTING_VALIDATED` status means "no known-bad parameter found", never "this contract
exists" or "this route is permitted".

DESIGN RULES THIS MODULE FOLLOWS
1. It never mutates a symbol into a different symbol. An earlier version of this skill
   zero-padded Hong Kong codes to five digits ("700" -> "00700"); IBKR's own SEHK contract
   sample uses `symbol = "1"` for the security listed under HKEX code 00001, so padding
   turns a resolvable symbol into an unresolvable one. Suspicious formatting is reported as
   a warning, not silently rewritten.
2. It rejects only on a *positive* contradiction of documented behaviour. An exchange code
   it does not know about produces a warning, not a rejection -- IBKR reaches 170+ markets
   and no hard-coded table stays complete.
3. Advisory conditions (a missing `primaryExchange` hint, an unknown venue, a rebate
   preference that is not an order-level field) surface as `warnings`, because rejecting a
   valid order is as much a production failure as accepting an invalid one.

Sources for every external claim are listed in `references/standards.md`.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging
import math
import re
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

__all__ = [
    "IbkrContractSpec",
    "IbkrOrderPayload",
    "IbkrRoutingReport",
    "IbkrVenueProfile",
    "IbkrGlobalRoutingEngine",
    "VALID_SEC_TYPES",
    "VALID_ROUTING_MODES",
    "VALID_ACTIONS",
    "VENUE_REGISTRY",
    "SMART_DESTINATION",
]

Quantity = Union[int, float, Decimal, str]

# `Contract.secType` values enumerated in the TWS API Contract class reference. The previous
# revision of this skill accepted only {STK, OPT, FUT, CASH, IND} and therefore rejected
# valid futures options, warrants, bonds and combos.
VALID_SEC_TYPES: FrozenSet[str] = frozenset({
    "STK", "OPT", "FUT", "IND", "FOP", "CASH", "BAG",
    "WAR", "BOND", "CMDTY", "NEWS", "FUND", "CFD", "CRYPTO",
})

# Security types for which `primaryExchange` is meaningful. The TWS API Contract reference
# scopes it to smart-routed *stock* ambiguity ("For smart routed contracts, used to define
# contract in case of ambiguity. Should be defined as native exchange of contract"), and
# the Basic Contracts page calls it "good practice to include for all stocks" -- stocks,
# not futures, options or forex.
PRIMARY_EXCHANGE_RELEVANT_SEC_TYPES: FrozenSet[str] = frozenset({"STK", "WAR", "FUND", "CFD"})

# Local abstraction, not an IBKR wire field. `Contract.exchange` is either "SMART" or a
# direct venue code; IBKR exposes no per-order "routing mode". SMART_MAX_REBATE maps to an
# *account/TWS-level* election available under the Cost Plus commission structure for
# non-marketable orders, so it is validated here and flagged, never emitted as an order
# field.
VALID_ROUTING_MODES: FrozenSet[str] = frozenset({
    "SMART_BEST_EXECUTION", "SMART_MAX_REBATE", "DIRECT_EXCHANGE",
})
SMART_ROUTING_MODES: FrozenSet[str] = frozenset({"SMART_BEST_EXECUTION", "SMART_MAX_REBATE"})

SMART_DESTINATION = "SMART"

# `Order.action`: "Generally available values are BUY and SELL. Additionally, SSHORT and
# SLONG are available in some institutional-accounts only."
VALID_ACTIONS: FrozenSet[str] = frozenset({"BUY", "SELL", "SSHORT", "SLONG"})
INSTITUTIONAL_ACTIONS: FrozenSet[str] = frozenset({"SSHORT", "SLONG"})

# Order types that carry a limit price. `Order.lmtPrice` is documented as "Used for limit,
# stop-limit and relative orders. In all other cases specify zero."
LIMIT_PRICE_ORDER_TYPES: FrozenSet[str] = frozenset({
    "LMT", "STP LMT", "LIT", "LOC", "REL", "TRAIL LIMIT",
})
# Deliberately partial: IBKR supports many more order types, and an unrecognised one is
# warned about rather than rejected.
KNOWN_ORDER_TYPES: FrozenSet[str] = frozenset({
    "MKT", "LMT", "STP", "STP LMT", "MIT", "LIT", "MOC", "LOC",
    "REL", "TRAIL", "TRAIL LIMIT", "PEG MID", "MKT PRT", "IBKRATS",
})

_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_HKEX_STOCK_CODE_RE = re.compile(r"^\d{1,5}$")
_MAINLAND_STOCK_CODE_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class IbkrVenueProfile:
    """
    A single IBKR `exchange` destination code and the currencies it is known to quote.

    `currencies=None` means "unconstrained" (forex venues), not "unknown". A venue absent
    from `VENUE_REGISTRY` is unknown, and unknown venues are warned about, never rejected.
    """

    code: str
    region: str
    currencies: Optional[FrozenSet[str]]
    note: str = ""

    def accepts_currency(self, currency: str) -> bool:
        """True if this venue is documented to quote `currency` (or is unconstrained)."""
        return self.currencies is None or currency in self.currencies


def _venue(
    code: str, region: str, currencies: Optional[Sequence[str]], note: str = "",
) -> IbkrVenueProfile:
    return IbkrVenueProfile(
        code=code,
        region=region,
        currencies=None if currencies is None else frozenset(currencies),
        note=note,
    )


# Starting reference only -- reconcile against `ContractDetails.validExchanges` per
# contract. Currency sets are the currencies each venue is documented to quote, not a
# promise that any given contract trades in all of them.
VENUE_REGISTRY: Dict[str, IbkrVenueProfile] = {
    # --- United States -------------------------------------------------------------
    "ISLAND": _venue("ISLAND", "US", ["USD"],
                     "NASDAQ direct-route destination; also the primaryExchange value in "
                     "IBKR's shipped contract-ambiguity sample."),
    "NASDAQ": _venue("NASDAQ", "US", ["USD"],
                     "Accepted as a primaryExchange hint for NASDAQ-listed stocks."),
    "NYSE": _venue("NYSE", "US", ["USD"]),
    "ARCA": _venue("ARCA", "US", ["USD"],
                   "primaryExchange value used in the TWS API Basic Contracts SPY example."),
    "AMEX": _venue("AMEX", "US", ["USD"]),
    "BATS": _venue("BATS", "US", ["USD"]),
    "IEX": _venue("IEX", "US", ["USD"]),
    "MEMX": _venue("MEMX", "US", ["USD"]),
    "IBKRATS": _venue("IBKRATS", "US", ["USD"], "IBKR's own ATS; non-marketable orders only."),
    # --- Europe --------------------------------------------------------------------
    "IBIS": _venue("IBIS", "EU", ["EUR"], "Xetra (Deutsche Boerse)."),
    "IBIS2": _venue("IBIS2", "EU", ["EUR"],
                    "Xetra segment code; confirm which of IBIS/IBIS2 applies via validExchanges."),
    "FWB": _venue("FWB", "EU", ["EUR"], "Frankfurt floor."),
    "SBF": _venue("SBF", "EU", ["EUR"], "Euronext Paris."),
    "AEB": _venue("AEB", "EU", ["EUR"], "Euronext Amsterdam."),
    "BVME": _venue("BVME", "EU", ["EUR"], "Borsa Italiana."),
    "LSE": _venue("LSE", "UK", ["GBP", "USD", "EUR"],
                  "LSE quotes GBP domestically plus USD/EUR lines for depositary receipts "
                  "and ETFs."),
    "DTB": _venue("DTB", "EU", ["EUR", "CHF"],
                  "Eurex. Legacy code used throughout IBKR's shipped contract samples; Eurex "
                  "also lists CHF-denominated SMI products."),
    "EUREX": _venue("EUREX", "EU", ["EUR", "CHF"],
                    "Eurex. Code used in current TWS API Basic Contracts examples."),
    # --- Hong Kong / Greater China -------------------------------------------------
    "SEHK": _venue("SEHK", "HK", ["HKD", "CNH"],
                   "HKEX runs the HKD-RMB Dual Counter Model, so an SEHK line is not "
                   "necessarily HKD."),
    "SEHKNTL": _venue("SEHKNTL", "HK", ["CNH"],
                      "Shanghai-Hong Kong Stock Connect northbound; 6-digit mainland codes."),
    "SEHKSZSE": _venue("SEHKSZSE", "HK", ["CNH"],
                       "Shenzhen-Hong Kong Stock Connect; 6-digit mainland codes."),
    "HKFE": _venue("HKFE", "HK", ["HKD", "CNH", "USD"],
                   "HKEX derivatives; alphabetic product symbols such as HSI, not numeric "
                   "stock codes."),
    # --- FX ------------------------------------------------------------------------
    "IDEALPRO": _venue("IDEALPRO", "GLOBAL", None,
                       "Forex. `symbol` is the base currency and `currency` the quote "
                       "currency."),
}

# SEHK-family venues whose *equity* symbols are numeric codes.
_HK_LOCAL_EQUITY_VENUES: FrozenSet[str] = frozenset({"SEHK"})
_HK_CONNECT_VENUES: FrozenSet[str] = frozenset({"SEHKNTL", "SEHKSZSE"})
_NUMERIC_CODE_SEC_TYPES: FrozenSet[str] = frozenset({"STK", "WAR", "FUND"})

STATUS_VALIDATED = "IBKR_ROUTING_VALIDATED"
STATUS_INVALID_SEC_TYPE = "REJECTED_INVALID_SEC_TYPE"
STATUS_MISSING_EXCHANGE = "REJECTED_MISSING_EXCHANGE"
STATUS_ROUTING_MODE_CONFLICT = "REJECTED_ROUTING_MODE_CONFLICT"
STATUS_INVALID_CURRENCY = "REJECTED_INVALID_CURRENCY"
STATUS_INVALID_SYMBOL_FORMAT = "REJECTED_INVALID_SYMBOL_FORMAT"
STATUS_CURRENCY_MISMATCH = "REJECTED_CURRENCY_MISMATCH"
STATUS_INVALID_ORDER_PARAMS = "REJECTED_INVALID_ORDER_PARAMS"


@dataclass(frozen=True)
class IbkrContractSpec:
    """
    The subset of `IBApi.Contract` this validator reasons about.

    `primary_exchange` is optional: IBKR scopes it to disambiguating smart-routed *stock*
    contracts, and its own shipped samples smart-route stocks without it.
    """

    symbol: str
    sec_type: str
    currency: str
    exchange: str
    primary_exchange: str = ""
    routing_mode: str = "SMART_BEST_EXECUTION"


@dataclass(frozen=True)
class IbkrOrderPayload:
    """The order-entry fields validated alongside the contract."""

    contract: IbkrContractSpec
    action: str
    order_type: str
    quantity: Quantity
    lmt_price: Optional[float] = None


@dataclass(frozen=True)
class IbkrRoutingReport:
    """
    Outcome of a pre-flight audit.

    `status == IBKR_ROUTING_VALIDATED` means no known-bad parameter was found. It does not
    mean the contract exists or that the destination is permitted for it -- which is what
    `requires_contract_details_check` exists to keep visible.
    """

    symbol: str
    sec_type: str
    currency: str
    target_exchange: str
    primary_exchange: str
    routing_mode: str
    is_contract_valid: bool
    is_currency_matched: bool
    status: str
    audit_notes: str
    warnings: Tuple[str, ...] = ()
    resolved_venue: str = ""
    currency_check_performed: bool = False
    requires_contract_details_check: bool = True


class IbkrGlobalRoutingEngine:
    """
    Pre-flight validator for IBKR contract, routing and order parameters.

    Deterministic and stateless: the same payload always produces the same report, and no
    instance state is carried between calls. Inject a custom `venue_registry` to extend or
    narrow the venue table without editing this module.
    """

    def __init__(self, venue_registry: Optional[Mapping[str, IbkrVenueProfile]] = None) -> None:
        self._venues: Dict[str, IbkrVenueProfile] = dict(
            VENUE_REGISTRY if venue_registry is None else venue_registry
        )

    # ------------------------------------------------------------------ symbol helpers
    def validate_hkex_symbol(self, symbol: str, exchange: str = "SEHK") -> str:
        """
        Validate a Hong Kong equity symbol and return it **unchanged**.

        IBKR's shipped SEHK contract sample uses `symbol = "1"` for the security listed under
        HKEX code 00001, so the IBKR-side symbol is the plain numeric code, not the
        zero-padded display form. This function therefore validates and returns; it never
        pads. HKEX allocates 1- to 5-digit codes for Hong Kong listings; Stock Connect venues
        carry 6-digit mainland codes.

        Raises:
            ValueError: the symbol is not a numeric code of the right width for `exchange`.
        """
        clean = symbol.strip()
        venue = exchange.strip().upper()
        if venue in _HK_CONNECT_VENUES:
            if not _MAINLAND_STOCK_CODE_RE.match(clean):
                raise ValueError(
                    f"Invalid Stock Connect symbol '{symbol}' for {venue}. Connect venues use "
                    f"6-digit mainland codes (e.g. '603737'), quoted in CNH."
                )
            return clean
        if not _HKEX_STOCK_CODE_RE.match(clean):
            raise ValueError(
                f"Invalid SEHK symbol '{symbol}'. HKEX equity codes are 1-5 numeric digits; "
                f"pass the code as IBKR lists it and confirm with reqContractDetails."
            )
        return clean

    # ------------------------------------------------------------------ main entry point
    def audit_and_route_order(self, payload: IbkrOrderPayload) -> IbkrRoutingReport:
        """
        Audit contract identity, routing destination, currency and order fields.

        Checks run contract-first so a malformed contract is reported ahead of a malformed
        order field. The first positive contradiction of documented IBKR behaviour rejects;
        everything advisory accumulates in `warnings`.
        """
        c = payload.contract
        sec = c.sec_type.strip().upper()
        currency = c.currency.strip().upper()
        exchange = c.exchange.strip().upper()
        primary = c.primary_exchange.strip().upper()
        mode = c.routing_mode.strip().upper()
        symbol = c.symbol.strip().upper()
        warnings: List[str] = []

        def reject(status: str, reason: str, **overrides: object) -> IbkrRoutingReport:
            label = symbol or repr(c.symbol)
            notes = f"IBKR REJECT [{label}]: {reason}"
            logger.error(notes)
            fields: Dict[str, object] = dict(
                symbol=symbol, sec_type=sec, currency=currency, target_exchange=exchange,
                primary_exchange=primary, routing_mode=mode, is_contract_valid=False,
                is_currency_matched=False, status=status, audit_notes=notes,
                warnings=tuple(warnings), resolved_venue="", currency_check_performed=False,
                requires_contract_details_check=True,
            )
            fields.update(overrides)
            return IbkrRoutingReport(**fields)  # type: ignore[arg-type]

        # 1. Security type ------------------------------------------------------------
        if sec not in VALID_SEC_TYPES:
            return reject(
                STATUS_INVALID_SEC_TYPE,
                f"Invalid secType '{c.sec_type}'. TWS API secType values: "
                f"{', '.join(sorted(VALID_SEC_TYPES))}.",
            )

        # 2. Destination present ------------------------------------------------------
        if not exchange:
            return reject(
                STATUS_MISSING_EXCHANGE,
                "Contract.exchange is empty. Every IBKR order needs a destination: 'SMART' "
                "or a direct venue code drawn from ContractDetails.validExchanges.",
            )

        # 3. Routing mode, and mode/destination consistency ---------------------------
        if mode not in VALID_ROUTING_MODES:
            return reject(
                STATUS_ROUTING_MODE_CONFLICT,
                f"Unknown routing_mode '{c.routing_mode}'. Expected one of "
                f"{', '.join(sorted(VALID_ROUTING_MODES))}.",
            )
        if mode in SMART_ROUTING_MODES and exchange != SMART_DESTINATION:
            return reject(
                STATUS_ROUTING_MODE_CONFLICT,
                f"routing_mode '{mode}' requires exchange='SMART', but exchange is "
                f"'{exchange}'. A direct venue code bypasses SmartRouting entirely.",
            )
        if mode == "DIRECT_EXCHANGE" and exchange == SMART_DESTINATION:
            return reject(
                STATUS_ROUTING_MODE_CONFLICT,
                "routing_mode 'DIRECT_EXCHANGE' requires an explicit venue code, but "
                "exchange='SMART' hands the order to SmartRouting.",
            )
        if mode == "SMART_MAX_REBATE":
            warnings.append(
                "SMART_MAX_REBATE is not an order-level TWS API field. Rebate-seeking routing "
                "of non-marketable orders is an account/TWS election available under the Cost "
                "Plus commission structure; setting it here changes nothing on the wire. "
                "Configure it in TWS order-routing settings and confirm your commission "
                "structure supports it."
            )

        # 4. Currency shape -----------------------------------------------------------
        if not _ISO_CURRENCY_RE.match(currency):
            return reject(
                STATUS_INVALID_CURRENCY,
                f"Contract.currency '{c.currency}' is not a 3-letter ISO-4217-style code "
                f"(e.g. 'USD', 'EUR', 'HKD', 'CNH').",
            )

        # 5. primaryExchange hygiene --------------------------------------------------
        # Runs before the symbol check so that both the symbol rule and the currency rule
        # below resolve the listing venue from the *same* normalised value.
        primary, primary_error = self._normalise_primary_exchange(primary, sec, exchange, warnings)
        if primary_error is not None:
            return reject(STATUS_ROUTING_MODE_CONFLICT, primary_error, primary_exchange=primary)

        # 6. Symbol format, scoped by security type and venue -------------------------
        symbol_error = self._validate_symbol(symbol, sec, exchange, primary, currency, warnings)
        if symbol_error is not None:
            return reject(STATUS_INVALID_SYMBOL_FORMAT, symbol_error, primary_exchange=primary)

        # 7. Currency vs venue --------------------------------------------------------
        venue_code = exchange if exchange != SMART_DESTINATION else primary
        profile = self._venues.get(venue_code) if venue_code else None
        currency_checked = False
        if sec == "CASH":
            warnings.append(
                "Forex (secType='CASH'): 'currency' is the quote currency of the pair and "
                "'symbol' the base currency, so no venue/region currency rule applies."
            )
        elif profile is None:
            if not venue_code:
                warnings.append(
                    "Currency could not be checked against a venue: exchange='SMART' with no "
                    "primary_exchange hint leaves the listing venue unknown locally. Confirm "
                    "with reqContractDetails before submitting."
                )
            else:
                warnings.append(
                    f"Venue '{venue_code}' is not in the local registry, so its currency was "
                    f"not checked. IBKR reaches 170+ markets; treat "
                    f"ContractDetails.validExchanges as the authority."
                )
        else:
            currency_checked = True
            if not profile.accepts_currency(currency):
                accepted = ", ".join(sorted(profile.currencies or ()))
                return reject(
                    STATUS_CURRENCY_MISMATCH,
                    f"Currency mismatch: venue '{profile.code}' ({profile.region}) is "
                    f"documented to quote [{accepted}], not '{currency}'.",
                    is_contract_valid=True,
                    resolved_venue=profile.code,
                    currency_check_performed=True,
                )

        # 8. Order fields -------------------------------------------------------------
        order_error = self._validate_order_fields(payload, warnings)
        if order_error is not None:
            return reject(
                STATUS_INVALID_ORDER_PARAMS, order_error,
                is_contract_valid=True,
                resolved_venue=profile.code if profile else "",
                currency_check_performed=currency_checked,
            )

        notes = (
            f"IBKR PRE-FLIGHT PASSED [{symbol} ({sec}) - {currency}]: destination={exchange}"
            + (f", primaryExchange={primary}" if primary else "")
            + f", mode={mode}, order={payload.action.strip().upper()} "
            f"{self._format_quantity(payload.quantity)} @ {payload.order_type.strip().upper()}. "
            f"Resolve with reqContractDetails and submit on conId before treating this as "
            f"routable."
        )
        logger.info(notes)
        for warning in warnings:
            logger.warning("IBKR PRE-FLIGHT WARNING [%s]: %s", symbol, warning)

        return IbkrRoutingReport(
            symbol=symbol,
            sec_type=sec,
            currency=currency,
            target_exchange=exchange,
            primary_exchange=primary,
            routing_mode=mode,
            is_contract_valid=True,
            is_currency_matched=True,
            status=STATUS_VALIDATED,
            audit_notes=notes,
            warnings=tuple(warnings),
            resolved_venue=profile.code if profile else "",
            currency_check_performed=currency_checked,
            requires_contract_details_check=True,
        )

    # ------------------------------------------------------------------ internals
    def _validate_symbol(
        self,
        symbol: str,
        sec: str,
        exchange: str,
        primary: str,
        currency: str,
        warnings: List[str],
    ) -> Optional[str]:
        """Return an error string, or None. Never mutates the symbol."""
        if not symbol:
            return "Contract.symbol is empty."

        if sec == "CASH":
            if not _ISO_CURRENCY_RE.match(symbol):
                return (
                    f"Forex symbol '{symbol}' is not a 3-letter currency code. For secType "
                    f"'CASH' the symbol is the base currency (e.g. symbol='EUR', "
                    f"currency='GBP', exchange='IDEALPRO')."
                )
            if symbol == currency:
                return f"Forex pair is degenerate: base and quote currency are both '{symbol}'."
            return None

        # Numeric-code venues only bind equity-like security types. HKEX derivatives on HKFE
        # use alphabetic product symbols (HSI, MHI), so the numeric rule must not reach them.
        venue = exchange if exchange != SMART_DESTINATION else primary
        if sec in _NUMERIC_CODE_SEC_TYPES and venue in (_HK_LOCAL_EQUITY_VENUES | _HK_CONNECT_VENUES):
            try:
                self.validate_hkex_symbol(symbol, venue)
            except ValueError as exc:
                return str(exc)
            if venue in _HK_LOCAL_EQUITY_VENUES and symbol.startswith("0") and len(symbol) > 1:
                warnings.append(
                    f"Symbol '{symbol}' is zero-padded. HKEX publishes padded display codes, "
                    f"but IBKR's shipped SEHK sample uses the unpadded code (symbol='1' for "
                    f"HKEX 00001). Confirm the exact IBKR symbol with reqContractDetails "
                    f"rather than assuming either form."
                )
        return None

    def _normalise_primary_exchange(
        self, primary: str, sec: str, exchange: str, warnings: List[str],
    ) -> Tuple[str, Optional[str]]:
        """Apply the documented primaryExchange rules. Returns (primary, error_or_None)."""
        if primary == SMART_DESTINATION:
            return primary, (
                "primary_exchange='SMART' is invalid: primaryExchange names the contract's "
                "native listing venue, never the routing destination."
            )
        if "." in primary:
            trimmed = primary.split(".", 1)[0]
            warnings.append(
                f"primary_exchange '{primary}' contains a period. The TWS API Contract "
                f"reference states that for exchanges whose name contains a period, only the "
                f"part before it is used (ENEXT for ENEXT.BE); normalised to '{trimmed}'."
            )
            primary = trimmed
        if primary and sec not in PRIMARY_EXCHANGE_RELEVANT_SEC_TYPES:
            warnings.append(
                f"primary_exchange '{primary}' is set on a '{sec}' contract. IBKR scopes "
                f"primaryExchange to disambiguating stock contracts; it carries no meaning "
                f"here."
            )
        if (
            not primary
            and exchange == SMART_DESTINATION
            and sec in PRIMARY_EXCHANGE_RELEVANT_SEC_TYPES
        ):
            warnings.append(
                "No primary_exchange hint on a SmartRouted stock. IBKR does not require it -- "
                "its own samples smart-route stocks without one -- but calls it 'good practice "
                "to include for all stocks', and it is what resolves the ambiguity error when "
                "a symbol/currency pair matches more than one listing."
            )
        return primary, None

    def _validate_order_fields(
        self, payload: IbkrOrderPayload, warnings: List[str],
    ) -> Optional[str]:
        """Return an error string, or None."""
        action = payload.action.strip().upper()
        order_type = payload.order_type.strip().upper()

        if action not in VALID_ACTIONS:
            return (
                f"Invalid action '{payload.action}'. TWS API actions are BUY and SELL "
                f"(SSHORT/SLONG in some institutional accounts only)."
            )
        if action in INSTITUTIONAL_ACTIONS:
            warnings.append(
                f"action='{action}' is available in some institutional accounts only; a retail "
                f"account will have the order rejected at entry."
            )

        if not order_type:
            return "Order.orderType is empty."
        if order_type not in KNOWN_ORDER_TYPES:
            warnings.append(
                f"order_type '{order_type}' is not in this validator's known set; IBKR supports "
                f"many more. Confirm the venue accepts it before relying on this pass."
            )

        if isinstance(payload.quantity, bool):
            return f"Quantity {payload.quantity!r} is a bool, not a size."
        try:
            qty = Decimal(str(payload.quantity))
        except (InvalidOperation, ValueError, TypeError):
            return f"Quantity {payload.quantity!r} is not a number."
        if not qty.is_finite():
            return f"Quantity {payload.quantity!r} is not finite."
        if qty <= 0:
            return (
                f"Quantity must be strictly positive; got {qty}. Direction is carried by "
                f"'action', never by a negative size."
            )
        if qty != qty.to_integral_value():
            warnings.append(
                f"Fractional quantity {qty}: TWS API v10 types totalQuantity as Decimal, but "
                f"fractional sizes are accepted only for eligible instruments and accounts."
            )

        price = payload.lmt_price
        if order_type in LIMIT_PRICE_ORDER_TYPES:
            if price is None:
                return f"order_type '{order_type}' requires lmt_price, which is None."
            if isinstance(price, bool) or not isinstance(price, (int, float, Decimal)):
                return f"lmt_price {price!r} is not a number."
            # Finiteness must be settled before any comparison: a NaN Decimal raises
            # InvalidOperation on `<=`, and a NaN float would silently compare False.
            if isinstance(price, float) and not math.isfinite(price):
                return f"lmt_price {price!r} is not finite."
            if isinstance(price, Decimal) and not price.is_finite():
                return f"lmt_price {price!r} is not finite."
            if price <= 0:
                return f"lmt_price must be strictly positive for '{order_type}'; got {price}."
        elif price is not None:
            warnings.append(
                f"lmt_price={price} is set on a '{order_type}' order. IBKR documents lmtPrice "
                f"as used for limit, stop-limit and relative orders and zero otherwise; the "
                f"value will be ignored or rejected."
            )
        return None

    @staticmethod
    def _format_quantity(quantity: Quantity) -> str:
        """Render size without asserting a unit -- 'shares' is wrong for futures and forex."""
        try:
            qty = Decimal(str(quantity))
        except (InvalidOperation, ValueError, TypeError):
            return str(quantity)
        if not qty.is_finite():
            return str(quantity)
        text = f"{qty:,f}"
        # Only strip inside a fractional part -- rstrip('0') on "100" would yield "1".
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
