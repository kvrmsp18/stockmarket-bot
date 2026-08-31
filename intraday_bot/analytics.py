from __future__ import annotations

from collections import Counter
from typing import Any
import pandas as pd


def market_regime(index_returns:dict[str,float], breadth_ratio:float|None=None, vix:float|None=None)->dict[str,Any]:
    score=sum(1 if v>0 else -1 if v<0 else 0 for v in index_returns.values())
    if breadth_ratio is not None: score += 1 if breadth_ratio>1.2 else -1 if breadth_ratio<0.8 else 0
    if vix is not None and vix>25: state="HIGH RISK"
    elif score>=2: state="STRONG BULLISH"
    elif score==1: state="BULLISH"
    elif score==-1: state="BEARISH"
    elif score<=-2: state="STRONG BEARISH"
    else: state="NEUTRAL"
    return {"state":state,"score":score,"inputs":index_returns,"breadth_ratio":breadth_ratio,"india_vix":vix}


def breadth(rows:list[dict[str,Any]])->dict[str,Any]:
    adv=sum(1 for r in rows if r.get("return",0)>0); dec=sum(1 for r in rows if r.get("return",0)<0); unchanged=len(rows)-adv-dec
    return {"advances":adv,"declines":dec,"unchanged":unchanged,"advance_decline_ratio":adv/dec if dec else None,"bullish_percentage":adv/len(rows)*100 if rows else 0,"bearish_percentage":dec/len(rows)*100 if rows else 0,"new_highs":sum(bool(r.get("new_high")) for r in rows),"new_lows":sum(bool(r.get("new_low")) for r in rows)}


def sector_analysis(rows:list[dict[str,Any]])->list[dict[str,Any]]:
    groups={}
    for r in rows:
        sec=r.get("sector","OTHER"); groups.setdefault(sec,[]).append(float(r.get("return",0)))
    out=[]
    for sec,vals in groups.items(): out.append({"sector":sec,"return":sum(vals)/len(vals),"breadth":sum(v>0 for v in vals)/len(vals)*100,"strength":min(10,max(0,5+sum(vals)/len(vals)*100))})
    return sorted(out,key=lambda x:x["strength"],reverse=True)


def value_migration(factors:dict[str,float])->dict[str,Any]:
    keys=["technology","cost","regulatory","consumer_preference"]
    available={k:float(factors[k]) for k in keys if k in factors}
    leader=max(available,key=available.get) if available else None
    return {"factors":available,"dominant_factor":leader,"question":"WHERE IS VALUE MIGRATION HAPPENING?"}


def inflection(history:list[float])->dict[str,Any]:
    if len(history)<4:return {"state":"SOURCE-UNCLEAR","reason":"insufficient history"}
    a=sum(history[-3:])/3; b=sum(history[-6:-3])/3 if len(history)>=6 else history[-4]
    return {"state":"POSITIVE INFLECTION" if a>b*1.02 else "NEGATIVE INFLECTION" if a<b*.98 else "NO CLEAR INFLECTION","recent_average":a,"prior_average":b}


def value_chain(nodes:list[dict[str,Any]])->list[dict[str,Any]]:
    return sorted(nodes,key=lambda x:(float(x.get("margin_change",0)),float(x.get("demand_change",0))),reverse=True)


def profit_pools(companies:list[dict[str,Any]])->list[dict[str,Any]]:
    return sorted(companies,key=lambda x:(float(x.get("profit_growth",0)),float(x.get("roce",0)),float(x.get("relative_strength",0))),reverse=True)
