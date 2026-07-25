from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class InputData:
    data: str
    value: float

class BreachRcaGenerator:
    def __init__(self):
        self.history = []
        
    def process(self, data: InputData) -> bool:
        self.history.append(data)
        if data.value < 0:
            return False
        return True
