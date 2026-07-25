import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "key-rotation-schedule-for-hot-wallet-keys"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
