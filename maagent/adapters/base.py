from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class RunResult:
    adapter: str
    task: str
    status: RunStatus
    started_at: str = ""
    finished_at: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class Adapter(ABC):
    name: str = "base"

    @abstractmethod
    def start(self, config: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def run(self, task: dict[str, Any]) -> RunResult:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...
