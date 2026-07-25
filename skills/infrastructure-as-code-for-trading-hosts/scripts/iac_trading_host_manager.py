from dataclasses import dataclass

@dataclass
class Config:
    name: str

class MainEngine:
    def __init__(self, config: Config):
        self.config = config
    
    def run(self):
        return True
