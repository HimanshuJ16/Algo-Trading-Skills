import dataclasses

@dataclasses.dataclass
class Result:
    success: bool
    message: str

class RiskModelBacktesterEngine:
    def __init__(self):
        pass
        
    def execute(self, param: bool) -> Result:
        if param:
            return Result(True, "Success")
        return Result(False, "Failure")
