from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

from .brokers import DhanBroker, PaperTradingBroker, load_security_map
from .config import IST, settings
from .database import Database
from .research import conviction, fundamental_score, scrap_analysis, source_valuation
from .risk import position_size, risk_gate
from .technical import technical_setup


@dataclass
class Candidate:
    symbol: str
    sector: str
    theme: str
    decision: str
    price: float
    entry: float
    entry_low: float
    entry_high: float
    max_chase: float
    stop: float
    target: float
    rr: float
    quantity: int
    capital_required: float
    max_risk: float
    potential_reward: float
    trend_score: float
    technical_score: float
    fundamental_score: float
    conviction_score: float
    liquidity_score: float
    volume_score: float
    market_score: float
    news_score: float
    ai_score: float
    reason: str
    rejection_reason: str | None = None


def market_open(now: datetime | None = None) -> bool:
    n=(now or datetime.now(IST)).astimezone(IST)
    return n.weekday()<5 and dtime(9,15)<=n.time()<=dtime(15,30)


def _num(value: Any, default: float = 0.0) -> float:
    try: return float(value)
    except (TypeError,ValueError): return default


def _quote(q: dict[str,Any]) -> tuple[float,float,float]:
    # Dhan quote payloads vary by endpoint/version; support flat and nested OHLC forms.
    price=_num(q.get("last_price", q.get("ltp", q.get("lastPrice"))))
    ohlc=q.get("ohlc") or q.get("OHLC") or {}
    prev=_num(q.get("prev_close", q.get("previous_close", q.get("previousClose", ohlc.get("close"))))
    vol=_num(q.get("volume", q.get("volumeTraded", q.get("volumeTradedToday"))))
    return price,prev,vol


def _load_universe() -> list[dict[str,Any]]:
    p=Path("data/universe.json")
    if p.exists():
        try:
            data=json.loads(p.read_text())
            if isinstance(data,list): return data
        except Exception: pass
    mapping=load_security_map()
    return [{"symbol":s,"security_id":v.get("security_id"),"exchange_segment":v.get("exchange_segment","NSE_EQ")} for s,v in mapping.items() if v.get("security_id")]


def _sector(symbol: str) -> str:
    sectors={"HDFCBANK":"BANKING","ICICIBANK":"BANKING","SBIN":"BANKING","KOTAKBANK":"BANKING","INFY":"IT","TCS":"IT","WIPRO":"IT","RELIANCE":"ENERGY","ONGC":"ENERGY","TATASTEEL":"METALS","HINDALCO":"METALS","SUNPHARMA":"PHARMA","CIPLA":"PHARMA","ITC":"FMCG","HINDUNILVR":"FMCG","MARUTI":"AUTO","M&M":"AUTO","EICHERMOT":"AUTO"}
    return sectors.get(symbol,"OTHER")


def _fundamentals(symbol: str) -> dict[str,Any]:
    p=Path("data/fundamentals.json")
    if not p.exists(): return {}
    try:
        data=json.loads(p.read_text())
        return data.get(symbol,{}) if isinstance(data,dict) else {}
    except Exception: return {}


def _ai_consensus(payload: dict[str,Any]) -> tuple[float,str]:
    """Optional advisory AI. Failure becomes NO_DECISION and never blocks risk safety."""
    import os, requests
    results=[]
    prompt=("Return JSON only with score 0-10, decision BUY/SELL/HOLD/NO TRADE, confidence 0-1. "
            "You are advisory only; do not override risk. Context: "+json.dumps(payload,default=str)[:12000])
    key=os.getenv("OPENAI_API_KEY")
    if key:
        try:
            r=requests.post("https://api.openai.com/v1/chat/completions",headers={"Authorization":f"Bearer {key}"},json={"model":os.getenv("OPENAI_MODEL","gpt-4o-mini"),"messages":[{"role":"system","content":"You are a financial research assistant."},{"role":"user","content":prompt}],"temperature":0},timeout=20)
            if r.ok:
                text=r.json()["choices"][0]["message"]["content"]; obj=json.loads(text); results.append(float(obj.get("score",5)))
        except Exception: pass
    key=os.getenv("ANTHROPIC_API_KEY")
    if key:
        try:
            r=requests.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":key,"anthropic-version":"2023-06-01","content-type":"application/json"},json={"model":os.getenv("ANTHROPIC_MODEL","claude-3-5-haiku-latest"),"max_tokens":500,"messages":[{"role":"user","content":prompt}]},timeout=20)
            if r.ok:
                text=r.json()["content"][0]["text"]; obj=json.loads(text); results.append(float(obj.get("score",5)))
        except Exception: pass
    if not results: return 5.0,"NO_DECISION"
    avg=sum(results)/len(results); return avg,"AGREE" if max(results)-min(results)<=2 else "DISAGREE"


def _charges(turnover: float) -> float:
    brokerage=min(40.0, turnover*0.0003)
    stt=turnover*0.00025
    exchange=turnover*0.0000307
    sebi=turnover/10_000_000*10
    stamp=turnover*0.00003
    gst=(brokerage+exchange+sebi)*0.18
    return brokerage+stt+exchange+sebi+stamp+gst


class TradingEngine:
    def __init__(self) -> None:
        settings.validate()
        self.db=Database()
        self.live_requested=settings.live_mode_requested
        self.broker=DhanBroker() if (self.live_requested or os.getenv("USE_DHAN_DATA","true").lower()!="false") else PaperTradingBroker()
        self.security_map=load_security_map()

    def _safety_gate(self) -> tuple[bool,str]:
        if settings.mode!="LIVE" or not settings.live_enabled: return True,"PAPER_MODE"
        if not settings.live_mode_requested: return False,"LIVE_DISABLED"
        health=self.broker.health()
        if not (health.connected and health.authenticated): return False,"BROKER_UNHEALTHY"
        if self.db.scalar("SELECT COUNT(*) FROM events WHERE event_type='RECONCILIATION_ERROR'") or self.db.scalar("SELECT COUNT(*) FROM events WHERE event_type='EMERGENCY_STOP'"): return False,"SAFETY_LOCK"
        return True,"LIVE_SAFETY_PASSED"

    def run(self) -> dict[str,Any]:
        started=time.monotonic(); cycle_id=uuid.uuid4().hex; started_at=datetime.now(timezone.utc).isoformat(); self.db.event("engine","INFO","CYCLE_START",{"cycle_id":cycle_id})
        universe=_load_universe(); symbols=[x["symbol"] for x in universe]
        result={"cycle_id":cycle_id,"started_at":started_at,"mode":"PAPER" if not self.live_requested else "LIVE","market_open":market_open(),"stocks_observed":len(symbols),"quotes":0,"candidates":[],"rejections":{},"orders":[],"errors":[]}
        if not symbols:
            result["errors"].append("DATA_UNAVAILABLE: universe is empty")
            self.db.event("market_data","ERROR","DATA_ERROR",{"reason":"EMPTY_UNIVERSE"}); return self._finish(result,started)
        try:
            quotes=self.broker.bulk_quotes(universe)
        except Exception as exc:
            result["errors"].append(f"DATA_ERROR: {exc}"); self.db.event("market_data","ERROR","DATA_ERROR",{"error":str(exc)}); return self._finish(result,started)
        result["quotes"]=len(quotes)
        ranked=[]
        for item in universe:
            sid=str(item.get("security_id","")); q=quotes.get(sid,{})
            price,prev,vol=_quote(q)
            if price<=0: continue
            ret=(price/prev-1) if prev>0 else 0
            ranked.append((abs(ret)*100 + min(vol/1_000_000,5),item,price,vol))
        ranked.sort(key=lambda z:z[0],reverse=True)
        # Full universe is observed in bulk; expensive candle/fundamental/AI analysis is bounded to the best movers.
        shortlist=ranked[:min(80,len(ranked))]
        funds=self.broker.funds() if self.broker.health().connected else settings.reference_capital
        funds=float(funds or settings.reference_capital)
        with ThreadPoolExecutor(max_workers=settings.scan_workers) as pool:
            futures={pool.submit(self._analyse_one,item,price,vol,funds):item for _,item,price,vol in shortlist}
            for fut in as_completed(futures):
                try:
                    cand=fut.result()
                    if cand.decision in {"BUY","SELL"}: result["candidates"].append(asdict(cand))
                    else: result["rejections"][cand.rejection_reason or "NO_TRADE"]=result["rejections"].get(cand.rejection_reason or "NO_TRADE",0)+1
                except Exception as exc:
                    result["errors"].append(f"ANALYSIS_ERROR: {exc}")
        result["candidates"].sort(key=lambda x:x["overall_score" if "overall_score" in x else "trend_score"],reverse=True)
        self._manage_positions(quotes, universe)
        self._execute(result)
        return self._finish(result,started)

    def _analyse_one(self,item:dict[str,Any],price:float,volume:float,funds:float)->Candidate:
        symbol=item["symbol"]; sector=_sector(symbol); f=_fundamentals(symbol); research=scrap_analysis(symbol,f)
        if research.rejection_reason: return Candidate(symbol,sector,"UNKNOWN","NO TRADE",price,price,price,price,price,price,price,0,0,0,0,0,0,0,0,0,0,0,5,5,"SCRAP rejection",research.rejection_reason)
        sid=str(item.get("security_id","")); history=self.broker.history(sid,item.get("exchange_segment","NSE_EQ"),5)
        if len(history)<60: return Candidate(symbol,sector,"UNKNOWN","NO TRADE",price,price,price,price,price,price,price,0,0,0,0,0,0,0,0,0,0,0,5,5,"DATA UNAVAILABLE","DATA_ERROR")
        tech=technical_setup(history)
        fs=fundamental_score(f); vs=source_valuation(f.get("eps"),f.get("pe")); conv=conviction(f); cs=float(conv["overall"])
        ai_score,ai_state=_ai_consensus({"symbol":symbol,"sector":sector,"technical":tech,"fundamental":f,"conviction":conv})
        if tech["direction"]=="HOLD": return Candidate(symbol,sector,"UNKNOWN","HOLD",price,tech["entry"],tech["entry_low"],tech["entry_high"],tech["max_chase"],tech["stop"],tech["target"],tech["rr"],0,0,0,tech["trend_score"],tech["technical_score"],fs,cs,min(10,volume/1e6),min(10,abs(tech["indicators"].get("rel_volume") or 0)*3),5,5,ai_score,"No valid intraday direction","TECHNICAL_REJECTION")
        if tech["rr"]<settings.min_rr: return Candidate(symbol,sector,"UNKNOWN","NO TRADE",price,tech["entry"],tech["entry_low"],tech["entry_high"],tech["max_chase"],tech["stop"],tech["target"],tech["rr"],0,0,0,tech["trend_score"],tech["technical_score"],fs,cs,5,5,5,ai_score,"R:R below configured minimum","RISK_REJECTION")
        ok,reason=risk_gate(tech["rr"],0,0,0)
        if not ok: return Candidate(symbol,sector,"UNKNOWN","NO TRADE",price,tech["entry"],tech["entry_low"],tech["entry_high"],tech["max_chase"],tech["stop"],tech["target"],tech["rr"],0,0,0,tech["trend_score"],tech["technical_score"],fs,cs,5,5,5,ai_score,"Risk gate failed",reason)
        size=position_size(tech["entry"],tech["stop"],tech["target"],settings.reference_capital,funds,liquidity_qty=max(1,int(volume/1000) if volume else 1))
        if size.quantity<=0: return Candidate(symbol,sector,"UNKNOWN","NO TRADE",price,tech["entry"],tech["entry_low"],tech["entry_high"],tech["max_chase"],tech["stop"],tech["target"],tech["rr"],0,0,0,tech["trend_score"],tech["technical_score"],fs,cs,5,5,5,ai_score,"No affordable safe quantity","INSUFFICIENT_FUNDS")
        overall=(tech["trend_score"]*0.30+fs*0.10+cs*0.10+ai_score*0.10+5*0.15+min(10,volume/1e6)*0.10+5*0.15)
        return Candidate(symbol,sector,"UNKNOWN",tech["direction"],price,tech["entry"],tech["entry_low"],tech["entry_high"],tech["max_chase"],tech["stop"],tech["target"],tech["rr"],size.quantity,size.capital_required,size.max_risk,tech["trend_score"],tech["technical_score"],fs,cs,5,min(10,volume/1e6),5,ai_score,f"Trend {tech['trend_state']}; AI {ai_state}; source valuation {vs if vs is not None else 'DATA UNAVAILABLE'}")

    def _execute(self,result:dict[str,Any])->None:
        allowed,gate=self._safety_gate(); result["execution_gate"]=gate
        if not allowed: return
        existing={r["symbol"] for r in self.db.recent("positions",100) if r.get("closed_at") is None}
        for c in result["candidates"][:settings.max_positions]:
            if c["symbol"] in existing:
                self.db.event("execution","INFO","DUPLICATE_ORDER",{"reason":"position already open"},c["symbol"]); continue
            signal_id=f"SIG-{uuid.uuid4().hex[:16]}"; self.db.signal(signal_id,c["symbol"],c["decision"],c)
            if not result["market_open"]: continue
            if c["entry"]>c["max_chase"] and c["decision"]=="BUY":
                self.db.event("strategy","INFO","ENTRY_EXPIRED",c,c["symbol"]); continue
            if self.live_requested:
                try: order=self.broker.order(c["symbol"],c["decision"],c["quantity"],c["entry"],live=True,security_id=self.security_map[c["symbol"]]["security_id"])
                except Exception as exc: self.db.event("execution","ERROR","ORDER_FAILED",{"error":str(exc)},c["symbol"],"LIVE"); continue
            else:
                order=self.broker.order(c["symbol"],c["decision"],c["quantity"],c["entry"],live=False)
            oid=str(order.get("order_id",uuid.uuid4().hex)); result["orders"].append(order)
            self.db.event("execution","INFO","ORDER_FILLED",order,c["symbol"],"LIVE" if self.live_requested else "PAPER")
            pos_id=f"POS-{uuid.uuid4().hex[:16]}"
            payload={**c,"signal_id":signal_id,"order_id":oid}
            with self.db.connect() as con:
                con.execute("INSERT INTO positions(position_id,symbol,mode,side,quantity,entry_price,current_price,stop,target,opened_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(pos_id,c["symbol"],"LIVE" if self.live_requested else "PAPER",c["decision"],c["quantity"],c["entry"],c["entry"],c["stop"],c["target"],datetime.now(timezone.utc).isoformat(),json.dumps(payload)))

    def _manage_positions(self,quotes:dict[str,dict[str,Any]],universe:list[dict[str,Any]])->None:
        sid_to_symbol={str(x.get("security_id")):x["symbol"] for x in universe}
        with self.db.connect() as con:
            rows=con.execute("SELECT * FROM positions WHERE closed_at IS NULL").fetchall()
            for p in rows:
                item=next((x for x in universe if x["symbol"]==p["symbol"]),None)
                if not item: continue
                price,_,_= _quote(quotes.get(str(item.get("security_id")),{}))
                if price<=0: continue
                side=p["side"]; exit_reason=None
                if side=="BUY" and price<=p["stop"]: exit_reason="STOP_LOSS"
                elif side=="BUY" and price>=p["target"]: exit_reason="TARGET"
                elif side=="SELL" and price>=p["stop"]: exit_reason="STOP_LOSS"
                elif side=="SELL" and price<=p["target"]: exit_reason="TARGET"
                if datetime.now(IST).time()>=dtime(settings.square_off_hour,settings.square_off_minute): exit_reason="EOD_SQUARE_OFF"
                if exit_reason:
                    gross=(price-p["entry_price"])*p["quantity"]*(1 if side=="BUY" else -1); charges=_charges((price+p["entry_price"])*p["quantity"]); net=gross-charges; trade_id=f"TRD-{uuid.uuid4().hex[:16]}"
                    con.execute("UPDATE positions SET current_price=?,closed_at=?,pnl=? WHERE position_id=?",(price,datetime.now(timezone.utc).isoformat(),net,p["position_id"]))
                    con.execute("INSERT INTO trades(trade_id,symbol,mode,side,quantity,entry_price,exit_price,gross_pnl,charges,net_pnl,exit_reason,opened_at,closed_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(trade_id,p["symbol"],p["mode"],side,p["quantity"],p["entry_price"],price,gross,charges,net,exit_reason,p["opened_at"],datetime.now(timezone.utc).isoformat(),p["payload"]))
                    self.db.event("execution","INFO","POSITION_CLOSED",{"trade_id":trade_id,"net_pnl":net,"exit_reason":exit_reason},p["symbol"],p["mode"])

    def _finish(self,result:dict[str,Any],started:float)->dict[str,Any]:
        result["duration_seconds"]=round(time.monotonic()-started,3); result["ended_at"]=datetime.now(timezone.utc).isoformat()
        result["positions_open"]=self.db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at IS NULL") or 0
        result["realized_pnl"]=float(self.db.scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades") or 0)
        Path("data").mkdir(exist_ok=True)
        Path("data/monitor_status.json").write_text(json.dumps(result,indent=2,default=str))
        self.db.event("engine","INFO","CYCLE_END",result)
        return result


def run_cycle()->dict[str,Any]:
    return TradingEngine().run()
