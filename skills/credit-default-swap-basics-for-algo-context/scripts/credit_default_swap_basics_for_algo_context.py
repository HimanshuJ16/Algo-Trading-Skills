import dataclasses

@dataclasses.dataclass
class Configuration:
    enabled: bool = True
    threshold: float = 0.5

class CreditDefaultSwapBasicsForAlgoContextEngine:
    def __init__(self, config: Configuration = None):
        self.config = config or Configuration()
        
    def execute(self, data: dict) -> dict:
        if not self.config.enabled:
            return {"status": "disabled"}
        return {"status": "success", "processed": True, "value": data.get("value", 0) * self.config.threshold}
