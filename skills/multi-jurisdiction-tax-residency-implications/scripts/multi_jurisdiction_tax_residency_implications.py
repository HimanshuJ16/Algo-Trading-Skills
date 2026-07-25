from dataclasses import dataclass

@dataclass
class Record:
    id: str
    value: float

class MultiJurisdictionTaxResidencyImplicationsEngine:
    def __init__(self):
        self.records = []
        
    def add_record(self, record: Record):
        self.records.append(record)
        
    def process(self):
        return sum(r.value for r in self.records)
