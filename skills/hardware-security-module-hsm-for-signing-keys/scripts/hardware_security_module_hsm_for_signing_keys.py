import dataclasses

@dataclasses.dataclass
class Config:
    name: str = "hardware-security-module-hsm-for-signing-keys"

class Engine:
    def __init__(self, config: Config):
        self.config = config

    def run(self):
        return True
