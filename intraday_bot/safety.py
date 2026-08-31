from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time
from .config import IST, settings

@dataclass(frozen=True)
class SafetyState:
    paper_mode: bool
    live_enabled: bool
    broker_ok: bool
    data_fresh: bool
    database_ok: bool
    risk_ok: bool
    emergency_stop: bool
    reconciliation_ok: bool

    @property
    def live_authorized(self)->bool:
        return (not self.paper_mode and self.live_enabled and self.broker_ok and self.data_fresh and self.database_ok and self.risk_ok and not self.emergency_stop and self.reconciliation_ok)

def valid_session()->bool:
    n=datetime.now(IST);return n.weekday()<5 and time(9,15)<=n.time()<=time(15,30)
