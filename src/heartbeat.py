from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Heartbeat:
    service: str
    status: str
    timestamp: datetime


def create_heartbeat(
    service: str,
    status: str = "OK",
) -> Heartbeat:
    if not service or not service.strip():
        raise ValueError("Service name is required")

    if not status or not status.strip():
        raise ValueError("Heartbeat status is required")

    return Heartbeat(
        service=service.strip(),
        status=status.strip().upper(),
        timestamp=datetime.now(timezone.utc),
    )
