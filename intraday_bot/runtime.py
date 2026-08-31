from __future__ import annotations

import json, os, time, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .brokers import DhanBroker, load_security_map
from .config import IST, settings
from .database import Database
from .research import conviction, fundamental_score, scrap_analysis
from .risk import position_size, risk_gate
from .technical import technical_setup

@dataclass
class Candidate:
    symbol:str; sector:str; decision:str; price:float; entry:float; entry_low:float; entry_high:float; max_chase:float; stop:float; target:float; rr:float; quantity:int; capital_required:float; max_risk:float; potential_reward:float; trend_score:float; technical_score:float; fundamental_score:float; conviction_score:float; liquidity_score:float; volume_score:float; market_score:float; news_score:float; ai_score:float; overall_score:float; reason:str; rejection_reason:str|None=None

def _universe()->list[dict[str,Any]]:
    p=Path("data/universe.json")
    if not p.exists(): return []
    try:
        x=json.loads(p.read_text()); return x if isinstance(x,list) else []
    except Exception: return []

def _quote(q:dict[str,Any])->tuple[float,float,float]:
    o=q.get("ohlc") or q.get("OHLC") or {}
    def n(v,d=0.0):
        try:return float(v)
        except:return d
    return n(q.get("last_price",q.get("ltp",q.get("lastPrice")))), n(q.get("prev_close",q.get("previousClose",o.get("close")))), n(q.get("volume",q.get("volumeTraded",q.get("volumeTradedToday"))))

def _sector(s:str)->str:
    groups={"BANKING":{"HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK"},"IT":{"TCS","INFY","WIPRO","HCLTECH","TECHM"},"ENERGY":{"RELIANCE","ONGC","NTPC","POWERGRID","COALINDIA"},"AUTO":{"MARUTI","M&M","EICHERMOT","TATAMOTORS"},"PHARMA":{"SUNPHARMA","CIPLA","DRREDDY"},"METALS":{"TATASTEEL","HINDALCO","JSWSTEEL"},"FMCG":{"ITC","HINDUNILVR"}}
    return next((k for k,v in groups.items() if s in v),"OTHER")

def _fundamental(s:str)->dict[str,Any]:
    p=Path("data/fundamentals.json")
    if not p.exists():return {}
    try:return json.loads(p.read_text()).get(s,{})
    except:return {}

def _ai_score(context:dict[str,Any])->tuple[float,str]:
    # AI is advisory. Missing credentials = NO_DECISION, never a BUY.
    scores=[]
    import requests
    prompt="Return JSON only: {score:0-10,decision:'BUY|SELL|HOLD|NO TRADE',confidence:0-1}. Advisory research only; never override deterministic risk. Context="+json.dumps(context,default=str)[:10000]
    key=os.getenv("OPENAI_API_KEY")
    if key:
        try:
            r=requests.post("https://api.openai.com/v1/chat/completions",headers={"Authorization":"Bearer "+key},json={"model":os.getenv("OPENAI_MODEL","gpt-4o-mini"),"messages":[{"role":"user","content":prompt}],"temperature":0},timeout=12)
            if r.ok:scores.append(float(json.loads(r.json()["choices"][0]["message"]["content"])["score"]))
        except Exception:pass
    key=os.getenv("ANTHROPIC_API_KEY")
    if key:
        try:
            r=requests.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":key,"anthropic-version":"2023-06-01","content-type":"application/json"},json={"model":os.getenv("ANTHROPIC_MODEL","claude-3-5-haiku-latest"),"max_tokens":300,"messages":[{"role":"user","content":prompt}]},timeout=12)
            if r.ok:scores.append(float(json.loads(r.json()["content"][0]["text"])["score"]))
        except Exception:pass
    if not scores:return 5.0,"NO_DECISION"
    return sum(scores)/len(scores),("AGREE" if max(scores)-min(scores)<=2 else "DISAGREE")

def _analyse(broker,item,price,volume,funds)->Candidate:
    s=item["symbol"]; sec=_sector(s); f=_fundamental(s); scrap=scrap_analysis(s,f)
    if scrap.rejection_reason:return Candidate(s,sec,"NO TRADE",price,price,price,price,price,price,price,0,0,0,0,0,0,0,0,0,0,0,0,5,5,0,"SCRAP rejection",scrap.rejection_reason)
    h=broker.history(str(item["security_id"]),item.get("exchange_segment","NSE_EQ"),5)
    if len(h)<60:return Candidate(s,sec,"NO TRADE",price,price,price,price,price,price,price,0,0,0,0,0,0,0,0,0,0,0,0,5,5,0,"DATA UNAVAILABLE","DATA_ERROR")
    t=technical_setup(h); fs=fundamental_score(f); cv=conviction(f); cs=float(cv["overall"]); ai,ais=_ai_score({"symbol":s,"sector":sec,"technical":t,"fundamental":f,"conviction":cv})
    if t["direction"]=="HOLD": return Candidate(s,sec,"HOLD",price,t["entry"],t["entry_low"],t["entry_high"],t["max_chase"],t["stop"],t["target"],t["rr"],0,0,0,0,t["trend_score"],t["technical_score"],fs,cs,5,min(10,volume/1e6),5,5,0,"No valid intraday direction","TECHNICAL_REJECTION")
    ok,why=risk_gate(t["rr"],0,0,0)
    if not ok:return Candidate(s,sec,"NO TRADE",price,t["entry"],t["entry_low"],t["entry_high"],t["max_chase"],t["stop"],t["target"],t["rr"],0,0,0,0,t["trend_score"],t["technical_score"],fs,cs,5,min(10,volume/1e6),5,ai,0,"Risk gate failed",why)
    size=position_size(t["entry"],t["stop"],t["target"],settings.reference_capital,funds,liquidity_qty=max(1,int(volume/1000) if volume else 1))
    if not size.quantity:return Candidate(s,sec,"NO TRADE",price,t["entry"],t["entry_low"],t["entry_high"],t["max_chase"],t["stop"],t["target"],t["rr"],0,0,0,0,t["trend_score"],t["technical_score"],fs,cs,5,min(10,volume/1e6),5,ai,0,"No safe affordable quantity","INSUFFICIENT_FUNDS")
    overall=t["trend_score"]*.30+fs*.10+cs*.10+ai*.10+min(10,volume/1e6)*.10+5*.40
    return Candidate(s,sec,t["direction"],price,t["entry"],t["entry_low"],t["entry_high"],t["max_chase"],t["stop"],t["target"],t["rr"],size.quantity,size.capital_required,size.max_risk,size.potential_reward,t["trend_score"],t["technical_score"],fs,cs,5,min(10,volume/1e6),5,5,overall,f"Trend={t['trend_state']}; AI={ais}; intraday R:R={t['rr']:.2f}")

def _market_open()->bool:
    n=datetime.now(IST); return n.weekday()<5 and dtime(9,15)<=n.time()<=dtime(15,30)

def run_cycle()->dict[str,Any]:
    started=time.monotonic(); db=Database(); universe=_universe(); result={"cycle_id":uuid.uuid4().hex,"mode":"PAPER","market_open":_market_open(),"stocks_observed":len(universe),"quotes":0,"candidates":[],"rejections":{},"orders":[],"errors":[]}
    use_dhan=os.getenv("USE_DHAN_DATA","true").lower()!="false"
    broker=DhanBroker() if use_dhan else None
    db.event("engine","INFO","CYCLE_START",result)
    if not universe:
        result["errors"].append("DATA_UNAVAILABLE: data/universe.json is missing or empty"); return _finish(result,started,db)
    try:quotes=broker.bulk_quotes(universe) if broker else {}
    except Exception as e:
        result["errors"].append("DATA_ERROR: "+str(e)); db.event("market_data","ERROR","DATA_ERROR",{"error":str(e)}); return _finish(result,started,db)
    result["quotes"]=len(quotes)
    ranked=[]
    for item in universe:
        price,prev,vol=_quote(quotes.get(str(item["security_id"]),{}))
        if price>0:ranked.append((abs(price/prev-1)*100 if prev else 0,item,price,vol))
    ranked.sort(reverse=True,key=lambda x:x[0]); shortlist=ranked[:min(60,len(ranked))]
    try:funds=float((broker.funds() if broker else settings.reference_capital) or settings.reference_capital)
    except:funds=settings.reference_capital
    if broker:
        with ThreadPoolExecutor(max_workers=settings.scan_workers) as ex:
            fs=[ex.submit(_analyse,broker,item,p,v,funds) for _,item,p,v in shortlist]
            for f in as_completed(fs):
                try:
                    c=f.result()
                    if c.decision in {"BUY","SELL"}:result["candidates"].append(asdict(c))
                    else:result["rejections"][c.rejection_reason or "NO_TRADE"]=result["rejections"].get(c.rejection_reason or "NO_TRADE",0)+1
                except Exception as e:result["errors"].append("ANALYSIS_ERROR: "+str(e))
    result["candidates"].sort(key=lambda x:x["overall_score"],reverse=True)
    # Paper execution is deliberately enabled only when real market data exists.
    if result["market_open"] and not settings.live_mode_requested:
        for c in result["candidates"][:settings.max_positions]:
            if c["entry"]>c["max_chase"] and c["decision"]=="BUY":
                db.event("strategy","INFO","ENTRY_EXPIRED",c,c["symbol"]);continue
            sid="SIG-"+uuid.uuid4().hex[:16]; db.signal(sid,c["symbol"],c["decision"],c)
            db.event("execution","INFO","PAPER_SIGNAL",{"signal_id":sid,**c},c["symbol"],"PAPER")
    elif settings.live_mode_requested:
        # Live is intentionally gated off until the deterministic readiness gate is implemented and explicitly enabled.
        result["execution_gate"]="LIVE_SAFETY_REQUIRES_EXPLICIT_ACTIVATION"
    return _finish(result,started,db)

def _finish(result,started,db):
    result["duration_seconds"]=round(time.monotonic()-started,3); result["ended_at"]=datetime.now(ZoneInfo("UTC")).isoformat(); result["realized_pnl"]=float(db.scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades") or 0); result["positions_open"]=int(db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at IS NULL") or 0)
    Path("data").mkdir(exist_ok=True); Path("data/monitor_status.json").write_text(json.dumps(result,indent=2,default=str),encoding="utf-8"); db.event("engine","INFO","CYCLE_END",result); return result
