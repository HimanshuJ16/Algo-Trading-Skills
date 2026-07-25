from dataclasses import dataclass

@dataclass
class Config:
    api_key: str
    secret: str

class IntegrationEngine:
    def __init__(self, config: Config):
        self.config = config
        
    def connect(self):
        return True
        
    def disconnect(self):
        return True
