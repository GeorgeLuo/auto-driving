"""An in-process control target that records successful writes."""


class RecordingTarget:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def acquire(self) -> None:
        pass

    def write(self, control) -> dict:
        self.writes.append(control.reason)
        return {"boundary": "test_target", "write": len(self.writes)}

    def release(self) -> None:
        pass
