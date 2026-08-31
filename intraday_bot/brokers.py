from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests

from .config import settings

@dataclass
class BrokerHealth:
    connected: bool
    authenticated: bool
    message: str

class BrokerInterface:
    def health(self) -> BrokerHealth: raise NotImplementedError
    def funds(self) -> float | None: raise NotImplementedError
    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]: raise NotImplementedError
    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5) -> pd.DataFrame: raise NotImplementedError
    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]: raise NotImplementedError
    def positions(self) -> list[dict[str, Any]]: return []
    def orders(self) -> list[dict[str, Any]]: return []

class PaperTradingBroker(BrokerInterface):
    def __init__(self, capital: float | None = None) -> None: self.capital=float(capital or settings.reference_capital)
    def health(self) -> BrokerHealth: return BrokerHealth(True, True, "PAPER BROKER READY")
    def funds(self) -> float | None: return self.capital
    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]: return {}
    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5) -> pd.DataFrame: return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        if live: raise RuntimeError("PaperTradingBroker cannot place live orders")
        return {"order_id":"PAPER-"+uuid.uuid4().hex[:16],"status":"FILLED","symbol":symbol,"side":side,"quantity":quantity,"price":price,"mode":"PAPER"}

class DhanBroker(BrokerInterface):
    """Dhan adapter. Strategy code never contains broker-specific calls."""
    def __init__(self) -> None:
        self.client_id=os.getenv("DHAN_CLIENT_ID","").strip(); self.token=os.getenv("DHAN_ACCESS_TOKEN","").strip(); self.base=settings.dhan_base_url.rstrip("/")
        self.session=requests.Session(); self.session.headers.update({"access-token":self.token,"client-id":self.client_id,"Content-Type":"application/json","Accept":"application/json"})
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.client_id or not self.token: raise RuntimeError("DHAN_AUTH_UNAVAILABLE")
        r=self.session.request(method,self.base+path,timeout=15,**kwargs)
        if r.status_code>=400: raise RuntimeError(f"DHAN_HTTP_{r.status_code}: {r.text[:300]}")
        return r.json()
    def health(self) -> BrokerHealth:
        if not self.client_id or not self.token: return BrokerHealth(False,False,"DHAN credentials unavailable")
        try: self.funds(); return BrokerHealth(True,True,"DHAN CONNECTED")
        except Exception as exc: return BrokerHealth(False,False,str(exc))
    def funds(self) -> float | None:
        data=self._request("GET","/v2/fundlimit"); body=data.get("data",data) if isinstance(data,dict) else {}
        for key in ("availabelBalance","availableBalance","availabel_balance","available_balance","sodLimit"):
            if body.get(key) is not None: return float(body[key])
        return None
    def bulk_quotes(self, instruments: list[dict[str,Any]]) -> dict[str,dict[str,Any]]:
        groups:dict[str,list[str]]={}
        for item in instruments:
            ex=item.get("exchange_segment","NSE_EQ"); sid=str(item.get("security_id",""))
            if sid: groups.setdefault(ex,[]).append(sid)
        out={}
        for ex,ids in groups.items():
            for start in range(0,len(ids),500):
                data=self._request("POST","/v2/marketfeed/quote",json={ex:ids[start:start+500]}); body=data.get("data",data) if isinstance(data,dict) else {}; rows=body.get(ex,body) if isinstance(body,dict) else {}
                if isinstance(rows,dict): out.update({str(k):v for k,v in rows.items() if isinstance(v,dict)})
        return out
    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5) -> pd.DataFrame:
        data=self._request("POST","/v2/charts/intraday",json={"securityId":str(security_id),"exchangeSegment":exchange_segment,"instrument":"EQUITY","interval":str(interval)})
        body=data.get("data",data) if isinstance(data,dict) else {}; keys=["timestamp","open","high","low","close","volume"]
        if not all(k in body for k in keys): return pd.DataFrame(columns=keys)
        return pd.DataFrame({"timestamp":pd.to_datetime(body["timestamp"],unit="s",utc=True),"open":body["open"],"high":body["high"],"low":body["low"],"close":body["close"],"volume":body["volume"]}).dropna(subset=["close"]).reset_index(drop=True)
    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str,Any]:
        if not live or not settings.live_mode_requested: raise RuntimeError("LIVE_EXECUTION_BLOCKED")
        payload={"transactionType":side.upper(),"exchangeSegment":kwargs.get("exchange_segment","NSE_EQ"),"productType":"INTRADAY","orderType":kwargs.get("order_type","LIMIT"),"validity":"DAY","securityId":str(kwargs["security_id"]),"quantity":int(quantity),"price":float(price),"disclosedQuantity":0,"afterMarketOrder":False}
        return self._request("POST","/v2/orders",json=payload)
    def positions(self)->list[dict[str,Any]]:
        data=self._request("GET","/v2/positions"); return data.get("data",data) if isinstance(data,dict) else []
    def orders(self)->list[dict[str,Any]]:
        data=self._request("GET","/v2/orders"); return data.get("data",data) if isinstance(data,dict) else []

def load_security_map()->dict[str,dict[str,Any]]:
    raw=os.getenv("DHAN_SECURITY_IDS_JSON","").strip()
    if not raw: return {}
    try: obj=json.loads(raw)
    except json.JSONDecodeError: return {}
    return {str(s).upper():(v if isinstance(v,dict) else {"security_id":v,"exchange_segment":"NSE_EQ"}) for s,v in obj.items()}
