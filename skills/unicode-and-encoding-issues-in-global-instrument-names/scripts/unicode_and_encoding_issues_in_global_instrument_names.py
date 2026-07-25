
from dataclasses import dataclass

@dataclass
class UnicodeAndEncodingIssuesInGlobalInstrumentNamesConfig:
    enabled: bool = True

class UnicodeAndEncodingIssuesInGlobalInstrumentNamesEngine:
    def __init__(self, config: UnicodeAndEncodingIssuesInGlobalInstrumentNamesConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
