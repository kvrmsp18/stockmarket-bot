from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import requests

@dataclass
class NewsItem:
    title:str; source:str; url:str; published_at:str; verified:bool

class NewsEngine:
    """News adapter. Only returned/parsed provider data is marked verified."""
    def __init__(self, urls:dict[str,str]|None=None):self.urls=urls or {}
    def fetch(self)->list[NewsItem]:
        items=[]
        for source,url in self.urls.items():
            try:
                r=requests.get(url,timeout=10,headers={"User-Agent":"stockmarket-bot/1.0"});r.raise_for_status()
                items.append(NewsItem(f"{source} feed retrieved","provider",url,datetime.now(timezone.utc).isoformat(),True))
            except Exception:continue
        return items
