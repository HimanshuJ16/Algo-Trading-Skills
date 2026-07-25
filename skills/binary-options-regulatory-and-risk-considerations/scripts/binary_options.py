from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime
import enum

class Jurisdiction(enum.Enum):
    US_CFTC = "US_CFTC"
    EU_ESMA = "EU_ESMA"
    UK_FCA = "UK_FCA"
    UNREGULATED = "UNREGULATED"

@dataclass
class TradeContext:
    asset_id: str
    underlying: str
    notional: float
    strike: float
    expiry: datetime
    jurisdiction: Jurisdiction
    client_type: str  # "RETAIL" or "PROFESSIONAL"

class RegulatoryException(Exception):
    pass

class RiskException(Exception):
    pass

class ComplianceEngine:
    def __init__(self):
        # Banned jurisdictions for retail clients
        self.banned_jurisdictions_for_retail = [
            Jurisdiction.EU_ESMA,
            Jurisdiction.UK_FCA
        ]
        
    def validate_trade(self, context: TradeContext) -> bool:
        if context.jurisdiction == Jurisdiction.UNREGULATED:
            raise RegulatoryException("Trading on unregulated venues is strictly prohibited.")
            
        if context.client_type == "RETAIL" and context.jurisdiction in self.banned_jurisdictions_for_retail:
            raise RegulatoryException(f"Binary options trading for retail clients is banned in {context.jurisdiction.value}.")
            
        return True

class RiskEngine:
    def __init__(self, max_notional_per_trade: float = 1_000_000.0, max_pin_risk_exposure: float = 5_000_000.0):
        self.max_notional_per_trade = max_notional_per_trade
        self.max_pin_risk_exposure = max_pin_risk_exposure

    def check_limits(self, context: TradeContext) -> bool:
        if context.notional > self.max_notional_per_trade:
            raise RiskException(f"Notional {context.notional} exceeds maximum per trade limit of {self.max_notional_per_trade}.")
        return True
        
class BinaryOptionsManager:
    """Manages order flow integrating compliance and risk frameworks."""
    def __init__(self, compliance_engine: ComplianceEngine, risk_engine: RiskEngine):
        self.compliance_engine = compliance_engine
        self.risk_engine = risk_engine
        
    def process_order(self, context: TradeContext) -> Dict[str, str]:
        try:
            self.compliance_engine.validate_trade(context)
            self.risk_engine.check_limits(context)
            return {"status": "APPROVED", "message": "Order passed regulatory and risk checks."}
        except RegulatoryException as e:
            return {"status": "REJECTED_REGULATORY", "message": str(e)}
        except RiskException as e:
            return {"status": "REJECTED_RISK", "message": str(e)}
