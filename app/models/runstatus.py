from enum import Enum


class RunStatus(Enum):
    SUCCESS = "SUCCESS"
    INFEASIBLE = "INFEASIBLE"
    DATA_ERROR = "DATA_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    COMMUNICATION_ERROR = "COMMUNICATION_ERROR"
    UNKNOWN = "UNKNOWN"

    @staticmethod
    def factory(s: str) -> "RunStatus":
        for status in RunStatus:
            if status.value == s:
                return status
        return RunStatus.UNKNOWN
