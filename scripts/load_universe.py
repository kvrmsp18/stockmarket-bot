from __future__ import annotations

import csv, io, json
from pathlib import Path
import requests

URL="https://images.dhan.co/api-data/api-scrip-master.csv"
OUT=Path("data/universe.json")
MIN_SYMBOLS=1000

r=requests.get(URL,timeout=60,headers={"User-Agent":"stockmarket-bot/1.0"}); r.raise_for_status()
reader=csv.DictReader(io.StringIO(r.content.decode("utf-8-sig",errors="replace")))
fields={str(x).strip().upper():x for x in (reader.fieldnames or []) if x}
required=["SEM_EXM_EXCH_ID","SEM_SEGMENT","SEM_INSTRUMENT_NAME","SEM_TRADING_SYMBOL","SEM_SMST_SECURITY_ID"]
missing=[x for x in required if x not in fields]
if missing: raise SystemExit(f"Dhan master missing required columns: {missing}")
series_key=fields.get("SEM_SERIES")
seen=set(); universe=[]
for row in reader:
    exchange=str(row.get(fields["SEM_EXM_EXCH_ID"],"")).strip().upper()
    segment=str(row.get(fields["SEM_SEGMENT"],"")).strip().upper()
    instrument=str(row.get(fields["SEM_INSTRUMENT_NAME"],"")).strip().upper()
    symbol=str(row.get(fields["SEM_TRADING_SYMBOL"],"")).strip().upper()
    sid=str(row.get(fields["SEM_SMST_SECURITY_ID"],"")).strip()
    series=str(row.get(series_key,"")).strip().upper() if series_key else ""
    # Complete NSE cash equity only. If Dhan exposes series, EQ is required.
    if exchange!="NSE" or segment!="E" or instrument!="EQUITY" or not symbol or not sid: continue
    if series_key and series not in {"EQ",""}: continue
    if symbol in seen: continue
    seen.add(symbol); universe.append({"symbol":symbol,"security_id":sid,"exchange_segment":"NSE_EQ","series":series or "EQ"})
universe.sort(key=lambda x:x["symbol"])
if len(universe)<MIN_SYMBOLS: raise SystemExit(f"Only {len(universe)} genuine NSE cash-equity symbols found; refusing partial universe")
OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(universe,indent=2),encoding="utf-8")
print(f"COMPLETE NSE CASH-EQUITY UNIVERSE: {len(universe)} symbols")
print("First:",", ".join(x["symbol"] for x in universe[:10]))
print("Last:",", ".join(x["symbol"] for x in universe[-10:]))
