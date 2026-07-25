import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "insurance-coverage-assessment-for-custodied-crypto"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
