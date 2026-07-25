import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "shamir-secret-sharing-for-key-backup"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
