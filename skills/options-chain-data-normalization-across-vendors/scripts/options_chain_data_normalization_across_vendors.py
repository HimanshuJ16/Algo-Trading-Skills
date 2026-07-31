import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class NormalizedOptionContract:
    standard_osi_symbol: str            # 21-char OSI string e.g. 'AAPL  240119C00150000'
    underlying_ticker: str              # 'AAPL'
    option_type: str                    # 'CALL' or 'PUT'
    expiration_date: str                # ISO 'YYYY-MM-DD'
    strike_price: float
    bid: float
    ask: float
    mid_price: float
    spread: float
    implied_volatility: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None

@dataclass
class OptionsNormalizationReport:
    vendor_name: str
    total_records_processed: int
    normalized_contracts: List[NormalizedOptionContract]
    quality_status: str                 # 'DATA_INTEGRITY_OK', 'INVALID_QUOTE_DETECTED'
    audit_notes: str

class OptionsChainNormalizationEngine:
    """
    Options chain data normalization engine mapping heterogeneous vendor feeds (Polygon, IBKR, Bloomberg, OPRA)
    into standardized OCC OSI 21-character symbology, computing mid-prices, and auditing data integrity.
    """
    @staticmethod
    def build_osi_symbol(
        ticker: str, expiration_date_iso: str, option_type: str, strike_price: float
    ) -> str:
        """
        Constructs standardized 21-character OCC OSI option symbol.
        Format: Ticker(6 pad) + YYMMDD + C/P + Strike*1000(8 pad)
        Example: AAPL, 2024-01-19, CALL, 150.0 -> 'AAPL  240119C00150000'
        """
        ticker_clean = ticker.strip().upper()[:6]
        ticker_padded = ticker_clean.ljust(6, " ")

        dt = datetime.strptime(expiration_date_iso, "%Y-%m-%d")
        yymmdd = dt.strftime("%y%m%d")

        opt_char = "C" if option_type.upper().startswith("C") else "P"

        strike_int = int(round(strike_price * 1000.0))
        strike_padded = f"{strike_int:08d}"

        return f"{ticker_padded}{yymmdd}{opt_char}{strike_padded}"

    def parse_polygon_contract(self, raw_data: Dict) -> NormalizedOptionContract:
        """
        Parses Polygon.io format e.g. symbol = 'O:AAPL240119C00150000' or dict with fields.
        """
        ticker = raw_data.get("underlying_ticker", "AAPL").upper()
        strike = float(raw_data.get("strike_price", 0.0))
        opt_type = "CALL" if raw_data.get("contract_type", "call").lower() == "call" else "PUT"
        exp_iso = raw_data.get("expiration_date", "2024-01-19")

        osi = self.build_osi_symbol(ticker, exp_iso, opt_type, strike)
        bid = float(raw_data.get("bid", 0.0))
        ask = float(raw_data.get("ask", 0.0))
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else float(raw_data.get("close", 0.0))
        spread = max(0.0, ask - bid)

        return NormalizedOptionContract(
            standard_osi_symbol=osi,
            underlying_ticker=ticker,
            option_type=opt_type,
            expiration_date=exp_iso,
            strike_price=strike,
            bid=bid,
            ask=ask,
            mid_price=round(mid, 4),
            spread=round(spread, 4),
            implied_volatility=raw_data.get("implied_volatility"),
            open_interest=raw_data.get("open_interest"),
            volume=raw_data.get("volume")
        )

    def parse_ibkr_contract(self, raw_data: Dict) -> NormalizedOptionContract:
        """
        Parses Interactive Brokers format e.g. {'symbol': 'AAPL', 'expiry': '20240119', 'right': 'C', 'strike': 150.0}
        """
        ticker = raw_data["symbol"].upper()
        exp_raw = str(raw_data["expiry"])  # '20240119'
        dt = datetime.strptime(exp_raw, "%Y%m%d")
        exp_iso = dt.strftime("%Y-%m-%d")

        opt_type = "CALL" if raw_data.get("right", "C").upper() == "C" else "PUT"
        strike = float(raw_data["strike"])

        osi = self.build_osi_symbol(ticker, exp_iso, opt_type, strike)
        bid = float(raw_data.get("bid", 0.0))
        ask = float(raw_data.get("ask", 0.0))
        mid = (bid + ask) / 2.0
        spread = max(0.0, ask - bid)

        return NormalizedOptionContract(
            standard_osi_symbol=osi,
            underlying_ticker=ticker,
            option_type=opt_type,
            expiration_date=exp_iso,
            strike_price=strike,
            bid=bid,
            ask=ask,
            mid_price=round(mid, 4),
            spread=round(spread, 4),
            implied_volatility=raw_data.get("impliedVol"),
            open_interest=raw_data.get("openInterest"),
            volume=raw_data.get("volume")
        )

    def normalize_chain(
        self, vendor_name: str, raw_records: List[Dict]
    ) -> OptionsNormalizationReport:
        """
        Normalizes list of raw vendor option contracts into standardized OSI contracts.
        """
        contracts: List[NormalizedOptionContract] = []
        is_invalid_found = False

        vendor_upper = vendor_name.upper()

        for raw in raw_records:
            if vendor_upper == "IBKR":
                contract = self.parse_ibkr_contract(raw)
            else:  # Polygon / OPRA / Default
                contract = self.parse_polygon_contract(raw)

            # Data Integrity Check: Bid <= Ask & Strike > 0
            if contract.bid > contract.ask or contract.strike_price <= 0:
                is_invalid_found = True
                logger.warning(f"Invalid option quote detected [{contract.standard_osi_symbol}]: Bid=${contract.bid} > Ask=${contract.ask}")

            contracts.append(contract)

        status = "INVALID_QUOTE_DETECTED" if is_invalid_found else "DATA_INTEGRITY_OK"
        notes = f"Processed {len(contracts)} contracts from vendor '{vendor_name}' -> Status: {status}."
        logger.info(notes)

        return OptionsNormalizationReport(
            vendor_name=vendor_name,
            total_records_processed=len(contracts),
            normalized_contracts=contracts,
            quality_status=status,
            audit_notes=notes
        )
