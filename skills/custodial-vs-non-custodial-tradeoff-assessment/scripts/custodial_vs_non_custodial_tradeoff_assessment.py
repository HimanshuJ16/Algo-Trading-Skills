import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "custodial-vs-non-custodial-tradeoff-assessment"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
