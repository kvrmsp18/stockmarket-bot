from __future__ import annotations

from datetime import datetime, timezone
import uuid

from .database import Database


def fill(db: Database, signal: dict, mode: str = "PAPER") -> str:
    """Confirm a paper fill and create a position. Only confirmed fills become trades later."""
    order_id="PAPER-"+uuid.uuid4().hex[:16]
    pos_id="POS-"+uuid.uuid4().hex[:16]
    with db.connect() as con:
        con.execute("INSERT INTO orders(order_id,signal_id,ts,symbol,side,state,quantity,price,payload) VALUES(?,?,?,?,?,?,?,?,?)",(order_id,signal.get("signal_id"),datetime.now(timezone.utc).isoformat(),signal["symbol"],signal["decision"],"FILLED",int(signal["quantity"]),float(signal["entry"]),str(signal)))
        con.execute("INSERT INTO positions(position_id,symbol,mode,side,quantity,entry_price,current_price,stop,target,opened_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(pos_id,signal["symbol"],mode,signal["decision"],int(signal["quantity"]),float(signal["entry"]),float(signal["entry"]),float(signal["stop"]),float(signal["target"]),datetime.now(timezone.utc).isoformat(),str(signal)))
    return order_id
