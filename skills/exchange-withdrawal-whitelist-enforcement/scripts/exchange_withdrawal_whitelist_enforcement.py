import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "exchange-withdrawal-whitelist-enforcement"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
