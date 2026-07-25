
from dataclasses import dataclass

@dataclass
class CurrencyPairQuotingConventionNormalizationConfig:
    enabled: bool = True

class CurrencyPairQuotingConventionNormalizationEngine:
    def __init__(self, config: CurrencyPairQuotingConventionNormalizationConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
