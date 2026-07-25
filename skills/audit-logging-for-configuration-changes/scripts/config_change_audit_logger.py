from dataclasses import dataclass

@dataclass
class Config:
    name: str

class ConfigChangeAuditLogger:
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return True
