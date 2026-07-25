from dataclasses import dataclass
from typing import List

@dataclass
class Config:
    name: str

class Engine:
    def __init__(self, config: Config):
        self.config = config
    def run(self) -> bool:
        return True
