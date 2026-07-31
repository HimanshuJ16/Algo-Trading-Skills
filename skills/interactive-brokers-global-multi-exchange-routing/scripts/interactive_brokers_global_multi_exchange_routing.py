import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_SEC_TYPES = {"STK", "OPT", "FUT", "CASH", "IND"}
VALID_ROUTING_MODES = {"SMART_BEST_EXECUTION", "SMART_MAX_REBATE", "DIRECT_EXCHANGE"}

@dataclass
class IbkrContractSpec:
    symbol: str                         # e.g. 'AAPL', 'BMW', '00700'
    sec_type: str                       # 'STK', 'OPT', 'FUT', 'CASH'
    currency: str                       # 'USD', 'EUR', 'HKD', 'GBP'
    exchange: str                       # 'SMART', 'ISLAND', 'NYSE', 'FWB', 'SEHK', 'DTB'
    primary_exchange: str               # e.g. 'NASDAQ', 'NYSE', 'IBIS', 'FWB', 'SEHK'
    routing_mode: str = "SMART_BEST_EXECUTION" # 'SMART_BEST_EXECUTION', 'SMART_MAX_REBATE', 'DIRECT_EXCHANGE'

@dataclass
class IbkrOrderPayload:
    contract: IbkrContractSpec
    action: str                         # 'BUY' or 'SELL'
    order_type: str                     # 'LMT', 'MKT', 'STP'
    quantity: int
    lmt_price: Optional[float] = None

@dataclass
class IbkrRoutingReport:
    symbol: str
    sec_type: str
    currency: str
    target_exchange: str
    primary_exchange: str
    routing_mode: str
    is_contract_valid: bool
    is_currency_matched: bool
    status: str                         # 'IBKR_ROUTING_VALIDATED', 'REJECTED_INVALID_SEC_TYPE', 'REJECTED_CURRENCY_MISMATCH', 'REJECTED_MISSING_PRIMARY_EXCHANGE'
    audit_notes: str

class IbkrGlobalRoutingEngine:
    """
    Quantitative order gateway engine for Interactive Brokers (IBKR / TWS API),
    configuring multi-exchange SmartRouting (NBBO, MaxRebate) vs Direct Routing across global venues (US, Europe, HKEX, Eurex).
    """
    def __init__(self):
        pass

    def validate_hkex_symbol(self, symbol: str) -> str:
        """
        Validates Hong Kong stock code is a 5-digit numeric string (e.g. '00700').
        """
        clean = symbol.strip()
        if clean.isdigit():
            clean = clean.zfill(5)
        if len(clean) != 5 or not clean.isdigit():
            raise ValueError(f"Invalid HKEX Symbol '{symbol}'. HKEX stocks must be 5-digit numeric codes (e.g. 00700).")
        return clean

    def audit_and_route_order(self, payload: IbkrOrderPayload) -> IbkrRoutingReport:
        """
        Audits IBKR Contract parameters, currency matching, primaryExchange hints, and SmartRouting configuration.
        """
        c = payload.contract
        sec_clean = c.sec_type.strip().upper()
        curr_clean = c.currency.strip().upper()
        exch_clean = c.exchange.strip().upper()
        prim_clean = c.primary_exchange.strip().upper()
        mode_clean = c.routing_mode.strip().upper()

        # 1. Audit Security Type
        if sec_clean not in VALID_SEC_TYPES:
            notes = f"IBKR REJECT [{c.symbol}]: Invalid secType '{c.sec_type}'. Must be one of {VALID_SEC_TYPES}."
            logger.error(notes)
            return IbkrRoutingReport(
                symbol=c.symbol, sec_type=sec_clean, currency=curr_clean, target_exchange=exch_clean,
                primary_exchange=prim_clean, routing_mode=mode_clean, is_contract_valid=False,
                is_currency_matched=False, status="REJECTED_INVALID_SEC_TYPE", audit_notes=notes
            )

        # 2. Audit Regional Symbol Formatting
        symbol_clean = c.symbol.strip().upper()
        if exch_clean == "SEHK" or curr_clean == "HKD":
            try:
                symbol_clean = self.validate_hkex_symbol(c.symbol)
            except ValueError as e:
                notes = f"IBKR REJECT [{c.symbol}]: {str(e)}"
                logger.error(notes)
                return IbkrRoutingReport(
                    symbol=c.symbol, sec_type=sec_clean, currency=curr_clean, target_exchange=exch_clean,
                    primary_exchange=prim_clean, routing_mode=mode_clean, is_contract_valid=False,
                    is_currency_matched=True, status="REJECTED_INVALID_SYMBOL_FORMAT", audit_notes=notes
                )

        # 3. Audit Primary Exchange Disambiguation for SMART Routing
        if exch_clean == "SMART" and not prim_clean:
            notes = f"IBKR REJECT [{symbol_clean}]: SmartRouted orders MUST specify 'primary_exchange' (e.g. NASDAQ/NYSE/IBIS)."
            logger.warning(notes)
            return IbkrRoutingReport(
                symbol=symbol_clean, sec_type=sec_clean, currency=curr_clean, target_exchange=exch_clean,
                primary_exchange="", routing_mode=mode_clean, is_contract_valid=False,
                is_currency_matched=True, status="REJECTED_MISSING_PRIMARY_EXCHANGE", audit_notes=notes
            )

        # 4. Audit Currency vs Region Mismatch
        is_curr_ok = True
        if exch_clean in {"ISLAND", "NYSE"} and curr_clean != "USD":
            is_curr_ok = False
        elif exch_clean in {"FWB", "IBIS", "DTB"} and curr_clean != "EUR":
            is_curr_ok = False
        elif exch_clean == "SEHK" and curr_clean != "HKD":
            is_curr_ok = False

        if not is_curr_ok:
            notes = f"IBKR REJECT [{symbol_clean}]: Currency mismatch! Exchange '{exch_clean}' does not support currency '{curr_clean}'."
            logger.error(notes)
            return IbkrRoutingReport(
                symbol=symbol_clean, sec_type=sec_clean, currency=curr_clean, target_exchange=exch_clean,
                primary_exchange=prim_clean, routing_mode=mode_clean, is_contract_valid=True,
                is_currency_matched=False, status="REJECTED_CURRENCY_MISMATCH", audit_notes=notes
            )

        notes = (
            f"IBKR ROUTING VALIDATED [{symbol_clean} ({sec_clean}) - {curr_clean}]: "
            f"Destination = {exch_clean} (Primary: {prim_clean}), Mode = {mode_clean}. "
            f"Action = {payload.action} {payload.quantity:,} shares @ {payload.order_type}."
        )
        logger.info(notes)

        return IbkrRoutingReport(
            symbol=symbol_clean,
            sec_type=sec_clean,
            currency=curr_clean,
            target_exchange=exch_clean,
            primary_exchange=prim_clean,
            routing_mode=mode_clean,
            is_contract_valid=True,
            is_currency_matched=True,
            status="IBKR_ROUTING_VALIDATED",
            audit_notes=notes
        )
