import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "multi-signature-approval-for-large-transfers"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
