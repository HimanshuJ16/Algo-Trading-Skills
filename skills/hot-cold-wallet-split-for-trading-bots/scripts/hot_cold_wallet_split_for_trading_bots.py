import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "hot-cold-wallet-split-for-trading-bots"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
