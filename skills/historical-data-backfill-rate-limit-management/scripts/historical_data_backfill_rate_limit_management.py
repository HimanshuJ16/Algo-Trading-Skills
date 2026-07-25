from dataclasses import dataclass

@dataclass
class Config:
    name: str

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def process(self, data):
        return data
