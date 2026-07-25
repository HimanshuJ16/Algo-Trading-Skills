import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from datetime import datetime

@dataclass
class TradeRecord:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: Optional[float]
    order_type: str
    venue: str
    algo_id: str
    trader_id: str
    client_id: str
    timestamps: Dict[str, str] # e.g., 'creation', 'submission', 'execution'
    fills: List[Dict[str, float]] # e.g., [{'price': 100.5, 'qty': 10, 'timestamp': '...'}]
    benchmark_price: Optional[float] = None
    regulatory_tags: Dict[str, str] = field(default_factory=dict)
    
    def calculate_average_fill_price(self) -> float:
        if not self.fills:
            return 0.0
        total_value = sum(f['price'] * f['qty'] for f in self.fills)
        total_qty = sum(f['qty'] for f in self.fills)
        return total_value / total_qty if total_qty > 0 else 0.0

@dataclass
class BestExAnalysis:
    order_id: str
    is_compliant: bool
    slippage: float
    notes: str
    record_hash: str

class BestExecutionRecordKeepingGlobalEngine:
    """
    Engine to enforce global best execution standards (e.g., MiFID II, SEC Rule 606) 
    and ensure immutable record-keeping.
    """
    def __init__(self, slippage_tolerance: float = 0.05):
        self.slippage_tolerance = slippage_tolerance
        self.audit_log = []

    def _generate_record_hash(self, record: TradeRecord) -> str:
        record_dict = asdict(record)
        # Ensure deterministic JSON string for hashing
        record_json = json.dumps(record_dict, sort_keys=True)
        return hashlib.sha256(record_json.encode('utf-8')).hexdigest()

    def run_best_ex_checks(self, record: TradeRecord) -> BestExAnalysis:
        if not record.timestamps.get('execution') and record.fills:
            return BestExAnalysis(record.order_id, False, 0.0, "Missing execution timestamp", "")
            
        avg_price = record.calculate_average_fill_price()
        
        slippage = 0.0
        if record.benchmark_price and avg_price > 0:
            if record.side.upper() == 'BUY':
                slippage = (avg_price - record.benchmark_price) / record.benchmark_price
            else:
                slippage = (record.benchmark_price - avg_price) / record.benchmark_price
                
        is_compliant = True
        notes = "Best execution standards met."
        
        if slippage > self.slippage_tolerance:
            is_compliant = False
            notes = f"Slippage {slippage:.2%} exceeded tolerance {self.slippage_tolerance:.2%}"
            
        if not record.regulatory_tags:
            is_compliant = False
            notes = "Missing regulatory tags (e.g., MiFID II/SEC)."

        record_hash = self._generate_record_hash(record)
        
        analysis = BestExAnalysis(
            order_id=record.order_id,
            is_compliant=is_compliant,
            slippage=slippage,
            notes=notes,
            record_hash=record_hash
        )
        
        self.audit_log.append({'record': asdict(record), 'analysis': asdict(analysis)})
        
        return analysis

    def export_audit_log(self, file_path: str):
        with open(file_path, 'w') as f:
            json.dump(self.audit_log, f, indent=4)
