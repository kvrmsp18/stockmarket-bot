from __future__ import annotations
import json, os, time, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock
from pathlib import Path
from typing import Any
from .brokers import DhanBroker
from .config import IST, settings
from .database import Database
from .paper import fill
from .research import conviction, fundamental_score, scrap_analysis
from .risk import position_size, risk_gate
from .technical import technical_setup

def market_open()->bool:
    n=datetime.now(IST); return n.weekday()<5 and clock(9,15)<=n.time()<=clock(15,30)

def universe()->list[dict[str,Any]]:
    p=Path("data/universe.json")
    if not p.exists(): return []
    try:
        x=json.loads(p.read_text(encoding="utf-8")); return x if isinstance(x,list) else []
    except Exception:return []

def quote(q:dict[str,Any])->tuple[float,float,float]:
    o=q.get("ohlc") or q.get("OHLC") or {}
    def n(v):
        try:return float(v)
        except:return 0.0
    return n(q.get("last_price",q.get("ltp",q.get("lastPrice")))),n(q.get("prev_close",q.get("previousClose",o.get("close")))),n(q.get("volume",q.get("volumeTraded",q.get("volumeTradedToday"))))

def sector(s:str)->str:
    groups={"BANKING":{"HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK"},"IT":{"TCS","INFY","WIPRO","HCLTECH","TECHM"},"ENERGY":{"RELIANCE","ONGC","NTPC","POWERGRID","COALINDIA"},"AUTO":{"MARUTI","M&M","EICHERMOT","TATAMOTORS"},"PHARMA":{"SUNPHARMA","CIPLA","DRREDDY"},"METALS":{"TATASTEEL","HINDALCO","JSWSTEEL"},"FMCG":{"ITC","HINDUNILVR"}}
    return next((k for k,v in groups.items() if s in v),"OTHER")

def fundamentals(s:str)->dict[str,Any]:
    p=Path("data/fundamentals.json")
    if not p.exists():return {}
    try:
        x=json.loads(p.read_text(encoding="utf-8")); return x.get(s,{}) if isinstance(x,dict) else {}
    except:return {}

def analyse(broker,item,price,volume,funds)->dict[str,Any]:
    s=item["symbol"]; f=fundamentals(s); scrap=scrap_analysis(s,f)
    base={"symbol":s,"sector":sector(s),"theme":"UNKNOWN","price":price,"decision":"NO TRADE","scrap_score":scrap.scrap_score,"fundamental_score":0.0,"technical_score":0.0,"trend_score":0.0,"multi_timeframe_score":0.0,"liquidity_score":5.0,"volume_score":min(10,volume/1e6),"market_score":5.0,"news_score":5.0,"ai_score":5.0}
    if scrap.rejection_reason:base.update({"reason":"SCRAP rejection","rejection_reason":scrap.rejection_reason});return base
    h=broker.history(str(item["security_id"]),item.get("exchange_segment","NSE_EQ"),5)
    if len(h)<60:base.update({"reason":"DATA UNAVAILABLE","rejection_reason":"DATA_ERROR"});return base
    t=technical_setup(h); fs=fundamental_score(f); cv=conviction(f); ai_state="NO_DECISION"
    base.update({"entry":t["entry"],"entry_zone":[t["entry_low"],t["entry_high"]],"max_chase":t["max_chase"],"stop":t["stop"],"target":t["target"],"rr":t["rr"],"trend_score":t["trend_score"],"technical_score":t["technical_score"],"fundamental_score":fs,"conviction_score":cv["overall"],"ai_consensus":ai_state})
    if t["direction"]=="HOLD":base.update({"reason":"No valid intraday direction","rejection_reason":"TECHNICAL_REJECTION"});return base
    ok,why=risk_gate(t["rr"],0,0,0)
    if not ok:base.update({"reason":"Risk gate failed","rejection_reason":why});return base
    size=position_size(t["entry"],t["stop"],t["target"],settings.reference_capital,funds,liquidity_qty=max(1,int(volume/1000) if volume else 1))
    if not size.quantity:base.update({"reason":"No safe affordable quantity","rejection_reason":"INSUFFICIENT_FUNDS"});return base
    base.update({"decision":t["direction"],"quantity":size.quantity,"capital_required":size.capital_required,"max_risk":size.max_risk,"potential_reward":size.potential_reward,"overall_score":t["trend_score"]*.35+fs*.10+cv["overall"]*.10+min(10,volume/1e6)*.10+5*.45,"reason":f"Trend={t['trend_state']}; R:R={t['rr']:.2f}; AI={ai_state}"})
    return base

def run_cycle()->dict[str,Any]:
    start=time.monotonic();db=Database();uni=universe();result={"cycle_id":uuid.uuid4().hex,"mode":"PAPER","market_open":market_open(),"stocks_observed":len(uni),"quotes":0,"candidates":[],"rejections":{},"orders":[],"errors":[]};db.event("engine","INFO","CYCLE_START",result)
    if not uni:result["errors"].append("DATA_UNAVAILABLE: empty universe");return finish(result,start,db)
    broker=DhanBroker()
    try:qmap=broker.bulk_quotes(uni);result["quotes"]=len(qmap)
    except Exception as e:result["errors"].append("DATA_ERROR: "+str(e));db.event("market_data","ERROR","DATA_ERROR",{"error":str(e)});return finish(result,start,db)
    ranked=[]
    for item in uni:
        p,prev,v=quote(qmap.get(str(item["security_id"]),{}))
        if p>0:ranked.append((abs(p/prev-1)*100 if prev else 0,item,p,v))
    ranked.sort(reverse=True,key=lambda z:z[0]);shortlist=ranked[:min(60,len(ranked))]
    try:funds=float(broker.funds() or settings.reference_capital)
    except:funds=settings.reference_capital
    with ThreadPoolExecutor(max_workers=settings.scan_workers) as ex:
        futures=[ex.submit(analyse,broker,item,p,v,funds) for _,item,p,v in shortlist]
        for fut in as_completed(futures):
            try:
                c=fut.result()
                if c["decision"] in {"BUY","SELL"}:result["candidates"].append(c)
                else:
                    r=c.get("rejection_reason") or "NO_TRADE";result["rejections"][r]=result["rejections"].get(r,0)+1
            except Exception as e:result["errors"].append("ANALYSIS_ERROR: "+str(e))
    result["candidates"].sort(key=lambda x:x.get("overall_score",0),reverse=True)
    if result["market_open"]:
        open_symbols={r["symbol"] for r in db.recent("positions",100) if not r.get("closed_at")}
        for c in result["candidates"][:settings.max_positions]:
            if c["symbol"] in open_symbols:db.event("execution","INFO","DUPLICATE_ORDER",{},c["symbol"]);continue
            if c["decision"]=="BUY" and c["price"]>c["max_chase"]:c["decision"]="NO TRADE";c["rejection_reason"]="ENTRY_EXPIRED";continue
            sid="SIG-"+uuid.uuid4().hex[:16];c["signal_id"]=sid;db.signal(sid,c["symbol"],c["decision"],c)
            oid=fill(db,c);result["orders"].append({"order_id":oid,"status":"FILLED","mode":"PAPER","symbol":c["symbol"],"side":c["decision"],"quantity":c["quantity"],"price":c["entry"]})
    result["execution_gate"]="PAPER_MODE";return finish(result,start,db)

def finish(result,start,db):
    result["duration_seconds"]=round(time.monotonic()-start,3);result["ended_at"]=datetime.now(IST).isoformat();result["positions_open"]=int(db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at IS NULL") or 0);result["realized_pnl"]=float(db.scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades") or 0);Path("data").mkdir(exist_ok=True);Path("data/monitor_status.json").write_text(json.dumps(result,indent=2,default=str),encoding="utf-8");db.event("engine","INFO","CYCLE_END",result);return result
