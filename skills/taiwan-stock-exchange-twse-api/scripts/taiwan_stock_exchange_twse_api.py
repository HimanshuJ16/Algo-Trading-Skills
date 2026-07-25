from dataclasses import dataclass

@dataclass
class Config:
    api_key: str

class IntegrationEngine:
    def __init__(self, config: Config):
        self.config = config
    
    def connect(self) -> bool:
        return True

    def process(self) -> str:
        return "success"
