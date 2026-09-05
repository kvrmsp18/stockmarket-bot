from __future__ import annotations

from datetime import datetime, timezone
import uuid

from .database import Database
from .scrap_portfolio import scrap_portfolio_exposure_check


def _portfolio_positions(db: Database, mode: str) -> list[dict]:
    """Return only open simulated positions for the requested execution mode."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT symbol, quantity, entry_price, current_price, mode FROM positions WHERE closed_at IS NULL AND mode=?",
            (mode,),
        ).fetchall()
    return [
        {
            "symbol": str(row[0]).upper(),
            "quantity": int(row[1] or 0),
            "entry_price": float(row[2] or 0),
            "current_price": float(row[3] or row[2] or 0),
            "mode": str(row[4] or "").upper(),
        }
        for row in rows
    ]


def fill(db: Database, signal: dict, mode: str = "PAPER") -> str:
    """Create a simulated fill for PAPER or LIVE_TEST only; never places a broker order.

    The SCRAP portfolio limits are enforced immediately before the simulated fill,
    using only open simulated positions. Manual/real broker holdings are never
    included. The check is based on the isolated reference capital carried by the
    signal and the candidate's actual entry notional.
    """
    mode = str(mode or "PAPER").upper()
    if mode not in {"PAPER", "LIVE_TEST"}:
        raise ValueError("Simulated fill mode must be PAPER or LIVE_TEST")

    quantity = int(signal.get("quantity") or 0)
    entry = float(signal.get("entry") or 0)
    if quantity <= 0 or entry <= 0:
        raise ValueError("Invalid simulated fill quantity or entry")

    positions = _portfolio_positions(db, mode)
    sector = str(signal.get("sector") or "UNKNOWN").strip().upper() or "UNKNOWN"
    capital = float(signal.get("reference_capital") or signal.get("paper_reference_capital") or 1000.0)
    exposure = scrap_portfolio_exposure_check(
        symbol=str(signal["symbol"]).upper(),
        sector=sector,
        candidate_notional=quantity * entry,
        reference_capital=capital,
        positions=positions,
    )
    if not exposure["allowed"]:
        raise ValueError(exposure["reason"])

    prefix = "PAPER-" if mode == "PAPER" else "LIVETEST-"
    order_id = prefix + uuid.uuid4().hex[:16]
    pos_id = "POS-" + uuid.uuid4().hex[:16]
    with db.connect() as con:
        con.execute(
            "INSERT INTO orders(order_id,signal_id,ts,symbol,side,state,quantity,price,payload) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                order_id,
                signal.get("signal_id"),
                datetime.now(timezone.utc).isoformat(),
                signal["symbol"],
                signal["decision"],
                "FILLED",
                quantity,
                entry,
                str({**signal, "execution_mode": mode, "simulated": True, "scrap_portfolio_check": exposure}),
            ),
        )
        con.execute(
            "INSERT INTO positions(position_id,symbol,mode,side,quantity,entry_price,current_price,stop,target,opened_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                pos_id,
                signal["symbol"],
                mode,
                signal["decision"],
                quantity,
                entry,
                entry,
                float(signal["stop"]),
                float(signal["target"]),
                datetime.now(timezone.utc).isoformat(),
                str({**signal, "execution_mode": mode, "simulated": True, "scrap_portfolio_check": exposure}),
            ),
        )
    return order_id
