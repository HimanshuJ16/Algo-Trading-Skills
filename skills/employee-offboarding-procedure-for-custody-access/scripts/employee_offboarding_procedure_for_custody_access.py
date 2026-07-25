from dataclasses import dataclass

@dataclass
class Result:
    success: bool
    message: str

class Analyzer:
    def __init__(self, config: dict):
        self.config = config

    def execute(self) -> Result:
        if not self.config:
            return Result(False, "No config provided")
        return Result(True, "Success")
