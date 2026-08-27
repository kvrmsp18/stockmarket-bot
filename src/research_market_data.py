"""Official-exchange-first, read-only NSE/BSE market research data.

The research engine does not use Yahoo Finance, Twelve Data, or Alpha Vantage.
Official NSE/BSE public endpoints are the first source. DhanHQ read-only chart
history is used for intraday intervals that the public exchange history APIs do
not expose consistently. No broker order API is used by this module.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import requests

from .dhan_api import DhanAPIError, DhanAuthenticationError, DhanHQClient

IST = ZoneInfo("Asia/Kolkata")


class ResearchMarketDataError(RuntimeError):
    """Raised when market research data cannot be safely consumed."""


@dataclass(frozen=True)
class ResearchBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class ResearchMarketDataProvider(Protocol):
    def history(self, symbol: str, *, period: str = "3mo", interval: str = "1d") -> tuple[ResearchBar, ...]: ...


class BaseMarketDataProvider:
    def __init__(self, session: requests.Session | None = None, timeout: float = 12.0) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    @staticmethod
    def _make_bar(symbol: str, timestamp: datetime, o: Any, h: Any, l: Any, c: Any, volume: Any = 0) -> ResearchBar:
        try:
            values = [float(o), float(h), float(l), float(c)]
            v = int(float(volume or 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ResearchMarketDataError(f"Malformed OHLCV data for {symbol}.") from exc
        if min(values) <= 0 or v < 0 or values[1] < max(values[0], values[2], values[3]) or values[2] > min(values[0], values[1], values[3]):
            raise ResearchMarketDataError(f"Invalid OHLCV data for {symbol}.")
        return ResearchBar(symbol, timestamp.astimezone(IST), values[0], values[1], values[2], values[3], v)

    @staticmethod
    def _validate(symbol: str, bars: list[ResearchBar]) -> tuple[ResearchBar, ...]:
        if not bars:
            raise ResearchMarketDataError(f"No valid OHLCV bars returned for {symbol}.")
        return tuple(sorted(bars, key=lambda x: x.timestamp))


class YahooFinanceMarketDataProvider(BaseMarketDataProvider):
    """Backward-compatible free Yahoo chart adapter used by the dashboard.

    This adapter is retained specifically so the proven dashboard scan path
    remains runnable after the research layer was upgraded to official-exchange
    first. It is read-only and requires no paid market-data API key.
    """

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    name = "yahoo_finance_chart"

    def history(self, symbol: str, *, period: str = "3mo", interval: str = "1d") -> tuple[ResearchBar, ...]:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ResearchMarketDataError("A market-data symbol is required.")
        try:
            response = self.session.get(
                f"{self.BASE_URL}/{normalized}",
                params={"range": period, "interval": interval, "events": "history"},
                headers={"User-Agent": "nse-bse-intraday-ai/1.0"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ResearchMarketDataError(f"Yahoo Finance network error for {normalized}: {exc}") from exc
        if not response.ok:
            raise ResearchMarketDataError(f"Yahoo Finance returned HTTP {response.status_code} for {normalized}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResearchMarketDataError(f"Yahoo Finance returned invalid JSON for {normalized}.") from exc
        result = payload.get("chart", {}).get("result") if isinstance(payload, dict) else None
        if not isinstance(result, list) or not result or not result[0]:
            error = payload.get("chart", {}).get("error") if isinstance(payload, dict) else None
            detail = error.get("description") if isinstance(error, dict) else "No chart data returned."
            raise ResearchMarketDataError(f"Yahoo Finance has no usable data for {normalized}: {detail}")
        chart = result[0]
        timestamps = chart.get("timestamp") or []
        quote_groups = chart.get("indicators", {}).get("quote", [])
        if not isinstance(quote_groups, list) or not quote_groups or not quote_groups[0]:
            raise ResearchMarketDataError(f"Yahoo Finance returned no OHLCV series for {normalized}.")
        quote = quote_groups[0]
        opens, highs = quote.get("open") or [], quote.get("high") or []
        lows, closes = quote.get("low") or [], quote.get("close") or []
        volumes = quote.get("volume") or []
        bars: list[ResearchBar] = []
        for index, raw_timestamp in enumerate(timestamps):
            values = (opens[index] if index < len(opens) else None, highs[index] if index < len(highs) else None, lows[index] if index < len(lows) else None, closes[index] if index < len(closes) else None)
            if any(value is None for value in values):
                continue
            try:
                timestamp = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
                o, h, l, c = (float(values[0]), float(values[1]), float(values[2]), float(values[3]))
                volume = int(volumes[index]) if index < len(volumes) and volumes[index] is not None else 0
            except (TypeError, ValueError, OverflowError) as exc:
                raise ResearchMarketDataError(f"Yahoo Finance returned malformed OHLCV data for {normalized}.") from exc
            if min(o, h, l, c) <= 0 or volume < 0 or h < max(o, l, c) or l > min(o, h, c):
                raise ResearchMarketDataError(f"Yahoo Finance returned invalid OHLCV data for {normalized}.")
            bars.append(ResearchBar(normalized, timestamp, o, h, l, c, volume))
        return self._validate(normalized, bars)


class OfficialNSEMarketDataProvider(BaseMarketDataProvider):
    """Official NSE public web/API endpoints."""
    BASE_URL = "https://www.nseindia.com"
    name = "official_nse"

    def __init__(self, session: requests.Session | None = None, timeout: float = 12.0) -> None:
        super().__init__(session=session, timeout=timeout)
        self._bootstrapped = False
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36", "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/market-data/equity-stock-price", "Connection": "keep-alive"})

    @property
    def configured(self) -> bool:
        return True

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if not self._bootstrapped:
                self.session.get(self.BASE_URL, timeout=self.timeout)
                self._bootstrapped = True
            response = self.session.get(f"{self.BASE_URL}{path}", params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ResearchMarketDataError(f"Official NSE network error: {exc}") from exc
        if not response.ok:
            raise ResearchMarketDataError(f"Official NSE returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResearchMarketDataError("Official NSE returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ResearchMarketDataError("Official NSE returned an invalid payload.")
        return payload

    def history(self, symbol: str, *, period: str = "3mo", interval: str = "1d") -> tuple[ResearchBar, ...]:
        normalized = symbol.strip().upper()
        if not normalized.endswith(".NS"):
            raise ResearchMarketDataError(f"Official NSE provider requires an NSE symbol: {normalized}")
        if interval not in {"1d", "1D"}:
            raise ResearchMarketDataError("Official NSE public historical endpoint provides daily equity history only.")
        end = datetime.now(IST).date(); start = end - timedelta(days=_period_days(period))
        payload = self._get("/api/historical/cm/equity", {"symbol": normalized.rsplit(".", 1)[0], "series": '["EQ"]', "from": _nse_date(start), "to": _nse_date(end)})
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ResearchMarketDataError(f"Official NSE returned no historical data for {normalized}.")
        bars=[]
        for row in rows:
            if not isinstance(row,dict): continue
            try:
                bars.append(self._make_bar(normalized,_parse_exchange_date(row.get("CH_TIMESTAMP") or row.get("mTIMESTAMP") or row.get("date")),row.get("CH_OPENING_PRICE"),row.get("CH_TRADE_HIGH_PRICE"),row.get("CH_TRADE_LOW_PRICE"),row.get("CH_CLOSING_PRICE"),row.get("CH_TOT_TRADED_QTY",0)))
            except (TypeError,ValueError,ResearchMarketDataError): continue
        return self._validate(normalized,bars)

    def quote(self, symbol: str) -> dict[str, Any]:
        return self._get("/api/quote-equity", {"symbol": symbol.strip().upper().removesuffix(".NS")})


class OfficialBSEMarketDataProvider(BaseMarketDataProvider):
    """Official BSE India public chart/history endpoint."""
    BASE_URL = "https://api.bseindia.com"
    name = "official_bse"

    def __init__(self, session: requests.Session | None = None, timeout: float = 12.0) -> None:
        super().__init__(session=session, timeout=timeout)
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36", "Accept": "application/json,text/plain,*/*", "Referer": "https://www.bseindia.com/"})

    @property
    def configured(self) -> bool: return True

    def history(self, symbol: str, *, period: str = "3mo", interval: str = "1d") -> tuple[ResearchBar, ...]:
        normalized=symbol.strip().upper()
        if not normalized.endswith(".BO"): raise ResearchMarketDataError(f"Official BSE provider requires a BSE symbol: {normalized}")
        if interval not in {"1d","1D"}: raise ResearchMarketDataError("Official BSE public historical endpoint is used for daily equity history only.")
        code=_bse_scrip_code(normalized)
        if not code: raise ResearchMarketDataError(f"No BSE scrip code is configured for {normalized}.")
        end=datetime.now(IST).date(); start=end-timedelta(days=_period_days(period))
        try: response=self.session.get(f"{self.BASE_URL}/BseIndiaAPI/api/StockReachGraph/w",params={"flag":1,"scripcode":code,"fromdate":start.strftime("%d/%m/%Y"),"todate":end.strftime("%d/%m/%Y")},timeout=self.timeout)
        except requests.RequestException as exc: raise ResearchMarketDataError(f"Official BSE network error: {exc}") from exc
        if not response.ok: raise ResearchMarketDataError(f"Official BSE returned HTTP {response.status_code}.")
        try: payload=response.json()
        except ValueError as exc: raise ResearchMarketDataError("Official BSE returned invalid JSON.") from exc
        rows=payload.get("Data") if isinstance(payload,dict) else None
        if not isinstance(rows,list): raise ResearchMarketDataError(f"Official BSE returned no historical data for {normalized}.")
        bars=[]
        for row in rows:
            if not isinstance(row,dict): continue
            try: bars.append(self._make_bar(normalized,_parse_exchange_date(row.get("dttm") or row.get("date") or row.get("Date")),row.get("Opn") or row.get("open"),row.get("Hgh") or row.get("high"),row.get("Lw") or row.get("low"),row.get("Cls") or row.get("close"),row.get("Vol") or row.get("volume",0)))
            except (TypeError,ValueError,ResearchMarketDataError): continue
        return self._validate(normalized,bars)


class DhanIntradayMarketDataProvider(BaseMarketDataProvider):
    """Read-only Dhan chart history with broker-rate-limit protection."""
    MASTER_URL="https://images.dhan.co/api-data/api-scrip-master.csv"; name="dhanhq_intraday"
    def __init__(self,client:DhanHQClient|None=None,timeout:float=12.0)->None:
        super().__init__(timeout=timeout); self.client=client or DhanHQClient(timeout=timeout); self.session=self.client.session
        try:self.min_request_interval=max(0.0,float(os.getenv("DHAN_INTRADAY_MIN_INTERVAL_SECONDS","0.6")))
        except (TypeError,ValueError):self.min_request_interval=0.6
        self.rate_limit_retry_seconds=max(1.0,float(os.getenv("DHAN_INTRADAY_RATE_LIMIT_RETRY_SECONDS","2.0"))); self._last_request_monotonic=0.0
    @property
    def configured(self)->bool:return self.client.configured
    def _wait_before_request(self)->None:
        remaining=self.min_request_interval-(time.monotonic()-self._last_request_monotonic)
        if remaining>0: time.sleep(remaining)
    def _chart_history(self,**kwargs:Any)->object:
        self._wait_before_request()
        body={"securityId":str(kwargs["security_id"]),"exchangeSegment":kwargs["exchange"],"instrument":"EQUITY","expiryCode":0,"interval":kwargs["interval_value"],"fromDate":kwargs["start"].isoformat(),"toDate":kwargs["today"].isoformat()}
        try:
            payload=self.client._request("POST","/charts/intraday",json=body); self._last_request_monotonic=time.monotonic(); return payload
        except DhanAPIError as exc:
            self._last_request_monotonic=time.monotonic(); message=str(exc)
            if "HTTP 429" not in message and "Too many requests" not in message: raise
            time.sleep(self.rate_limit_retry_seconds); self._wait_before_request()
            payload=self.client._request("POST","/charts/intraday",json=body); self._last_request_monotonic=time.monotonic(); return payload
    def history(self,symbol:str,*,period:str="5d",interval:str="15m")->tuple[ResearchBar,...]:
        if not self.configured: raise ResearchMarketDataError("Dhan is not configured for intraday chart data.")
        exchange,security_id=self._resolve(symbol)
        if interval not in {"5m","15m","1h","60m"}: raise ResearchMarketDataError(f"Dhan intraday provider does not support interval {interval}.")
        today=datetime.now(IST).date(); start=today-timedelta(days=_period_days(period)); value={"5m":5,"15m":15,"1h":60,"60m":60}[interval]
        try: payload=self._chart_history(security_id=security_id,exchange=exchange,interval_value=value,start=start,today=today)
        except (DhanAPIError,DhanAuthenticationError) as exc: raise ResearchMarketDataError(f"Dhan intraday data failed: {exc}") from exc
        return self._parse_dhan(normalized_symbol(symbol),payload)
    def _resolve(self,symbol:str)->tuple[str,int]:
        normalized=symbol.strip().upper(); explicit=_load_security_map().get(normalized) or _load_security_map().get(normalized.rsplit(".",1)[0])
        if explicit:return explicit["exchange"],int(explicit["security_id"])
        master=_load_dhan_master(self.session,self.timeout); base=normalized.rsplit(".",1)[0]; exchange="BSE_EQ" if normalized.endswith(".BO") else "NSE_EQ"; target=_normalize_equity_symbol(base)
        for row in master:
            if row[0]==exchange and (row[2].upper()==base or _normalize_equity_symbol(row[2])==target):return row[0],row[1]
        raise ResearchMarketDataError(f"No Dhan security ID found for {normalized}.")
    def _parse_dhan(self,symbol:str,payload:object)->tuple[ResearchBar,...]:
        if not isinstance(payload,dict):raise ResearchMarketDataError(f"Invalid Dhan chart response for {symbol}.")
        data=payload.get("data",payload); bars=[]
        if isinstance(data,dict):
            ts=data.get("timestamp") or data.get("timestamps") or []; opens=data.get("open") or []; highs=data.get("high") or []; lows=data.get("low") or []; closes=data.get("close") or []; vols=data.get("volume") or []
            for i,raw in enumerate(ts):
                try:bars.append(self._make_bar(symbol,_parse_timestamp(raw),opens[i],highs[i],lows[i],closes[i],vols[i] if i<len(vols) else 0))
                except (IndexError,TypeError,ValueError,OverflowError,ResearchMarketDataError):continue
        if not bars:raise ResearchMarketDataError(f"Dhan returned no intraday OHLCV bars for {symbol}.")
        return self._validate(symbol,bars)


class OfficialExchangeFirstMarketDataProvider:
    """Official NSE/BSE first; Dhan is used only for required intraday bars."""
    name="official_exchange_first"
    def __init__(self,timeout:float=12.0)->None:
        self.timeout=timeout; self.last_source=None; self.last_errors=(); self.nse=OfficialNSEMarketDataProvider(timeout=timeout); self.bse=OfficialBSEMarketDataProvider(timeout=timeout); self.dhan=DhanIntradayMarketDataProvider(timeout=timeout)
    @property
    def available_sources(self)->tuple[str,...]:
        sources=["official_nse","official_bse"]
        if self.dhan.configured:sources.append("dhanhq_intraday")
        return tuple(sources)
    def history(self,symbol:str,*,period:str="5d",interval:str="15m")->tuple[ResearchBar,...]:
        normalized=symbol.strip().upper(); errors=[]
        if interval not in {"1d","1D"}:
            if self.dhan.configured:
                try:bars=self.dhan.history(normalized,period=period,interval=interval); self.last_source=self.dhan.name; self.last_errors=(); return bars
                except ResearchMarketDataError as exc:errors.append(f"{self.dhan.name}: {exc}")
            errors.append("Official NSE/BSE public equity history endpoints do not expose a stable intraday OHLCV series for this interval.")
        else:
            provider=self.nse if normalized.endswith(".NS") else self.bse if normalized.endswith(".BO") else None
            if provider:
                try:bars=provider.history(normalized,period=period,interval=interval); self.last_source=provider.name; self.last_errors=(); return bars
                except ResearchMarketDataError as exc:errors.append(f"{provider.name}: {exc}")
        self.last_source=None; self.last_errors=tuple(errors); raise ResearchMarketDataError(f"No usable market data for {normalized}: {'; '.join(errors)}")

ResilientMarketDataProvider=OfficialExchangeFirstMarketDataProvider

def normalized_symbol(symbol:str)->str:return symbol.strip().upper()

def indian_equity_symbol(symbol:str,exchange:str="NSE")->str:
    normalized=symbol.strip().upper()
    if not normalized:raise ResearchMarketDataError("A symbol is required.")
    if normalized.endswith((".NS",".BO")):return normalized
    code=exchange.strip().upper()
    if code=="NSE":return f"{normalized}.NS"
    if code=="BSE":return f"{normalized}.BO"
    raise ResearchMarketDataError(f"Unsupported Indian exchange '{exchange}'.")

def _period_days(period:str)->int:
    p=period.strip().lower(); mapping={"1d":2,"5d":7,"7d":10,"1mo":35,"3mo":100,"6mo":200,"1y":380,"12mo":380}
    if p in mapping:return mapping[p]
    if p.endswith("d"):return max(2,int(p[:-1])+2)
    if p.endswith("mo"):return max(35,int(p[:-2])*31+5)
    if p.endswith("y"):return max(380,int(p[:-1])*365+15)
    raise ResearchMarketDataError(f"Unsupported history period '{period}'.")

def _nse_date(value:date)->str:return value.strftime("%d-%m-%Y")

def _parse_exchange_date(value:Any)->datetime:
    text=str(value or "").strip()
    for fmt in ("%d-%b-%Y","%d-%b-%Y %H:%M:%S","%d/%m/%Y","%Y-%m-%d","%Y-%m-%d %H:%M:%S"):
        try:return datetime.strptime(text,fmt).replace(tzinfo=IST)
        except ValueError:continue
    raise ValueError(f"Unsupported exchange date: {text}")

def _parse_timestamp(value:Any)->datetime:
    if isinstance(value,(int,float)):
        number=float(value)
        if number>10_000_000_000:number/=1000
        return datetime.fromtimestamp(number,tz=timezone.utc).astimezone(IST)
    parsed=datetime.fromisoformat(str(value).strip().replace("Z","+00:00"))
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)

def _bse_scrip_code(symbol:str)->str:
    raw=os.getenv("BSE_SCRIP_CODES_JSON","").strip()
    if not raw:return ""
    try:payload=json.loads(raw)
    except json.JSONDecodeError:return ""
    return str(payload.get(symbol) or payload.get(symbol.rsplit(".",1)[0]) or "").strip() if isinstance(payload,dict) else ""

def _load_security_map()->dict[str,dict[str,Any]]:
    raw=os.getenv("DHAN_SECURITY_IDS_JSON","").strip()
    if not raw:return {}
    try:payload=json.loads(raw)
    except json.JSONDecodeError:return {}
    if not isinstance(payload,dict):return {}
    result={}
    for symbol,item in payload.items():
        if not isinstance(item,dict) or item.get("security_id") is None:continue
        try:result[str(symbol).upper()]={"exchange":str(item.get("exchange","NSE_EQ")).upper(),"security_id":int(item["security_id"])}
        except (TypeError,ValueError):continue
    return result

def _normalize_equity_symbol(value:str)->str:
    text=str(value or "").strip().upper()
    if "." in text:text=text.rsplit(".",1)[0]
    for suffix in ("-EQ","_EQ","-BE","_BE","-SM","_SM","-ST","_ST"):
        if text.endswith(suffix):text=text[:-len(suffix)];break
    return "".join(ch for ch in text if ch.isalnum())

@lru_cache(maxsize=1)
def _load_dhan_master_cached(url:str,timeout:float)->tuple[tuple[str,int,str],...]:
    try:
        response=requests.get(url,timeout=timeout,headers={"User-Agent":"nse-bse-intraday-ai/5.0"}); response.raise_for_status()
    except requests.RequestException as exc:raise ResearchMarketDataError(f"Dhan instrument master unavailable: {exc}") from exc
    reader=csv.DictReader(io.StringIO(response.content.decode("utf-8-sig",errors="replace"))); fields={f.strip().lower():f for f in (reader.fieldnames or []) if f}
    exch=next((fields[x] for x in ("sem_exm_exch_id","exchange") if x in fields),None); sec=next((fields[x] for x in ("sem_security_id","sem_smst_security_id","security_id") if x in fields),None); sym=next((fields[x] for x in ("sem_trading_symbol","trading_symbol","tradingsymbol","sem_custom_symbol") if x in fields),None)
    if not exch or not sec or not sym:raise ResearchMarketDataError("Dhan instrument master schema is unsupported.")
    out=[]
    for row in reader:
        exchange=str(row.get(exch,"")).upper()
        if exchange not in {"NSE","BSE"}:continue
        symbol=str(row.get(sym,"")).strip().upper()
        try:security_id=int(float(str(row.get(sec,"")).strip()))
        except (TypeError,ValueError):continue
        if symbol:out.append((f"{exchange}_EQ",security_id,symbol))
    if not out:raise ResearchMarketDataError("Dhan instrument master contains no NSE/BSE equity instruments.")
    return tuple(out)

def _load_dhan_master(session:requests.Session,timeout:float)->tuple[tuple[str,int,str],...]:
    return _load_dhan_master_cached(DhanIntradayMarketDataProvider.MASTER_URL,timeout)
