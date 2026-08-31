from __future__ import annotations
import json, os
from typing import Any
import requests

class AIEngine:
    """Independent advisory layer. It cannot submit orders or override safety."""
    def __init__(self): self.openai=os.getenv("OPENAI_API_KEY",""); self.claude=os.getenv("ANTHROPIC_API_KEY","")
    def analyse(self, context:dict[str,Any])->dict[str,Any]:
        prompt="Return JSON only with score 0-10, decision BUY/SELL/HOLD/NO TRADE, confidence 0-1, positives[], negatives[], risks[]. Advisory only. "+json.dumps(context,default=str)[:10000]
        answers=[]
        if self.openai:
            try:
                r=requests.post("https://api.openai.com/v1/chat/completions",headers={"Authorization":"Bearer "+self.openai},json={"model":os.getenv("OPENAI_MODEL","gpt-4o-mini"),"messages":[{"role":"user","content":prompt}],"temperature":0},timeout=15)
                if r.ok:answers.append(("ChatGPT",json.loads(r.json()["choices"][0]["message"]["content"])))
            except Exception as e:answers.append(("ChatGPT",{"error":str(e)}))
        if self.claude:
            try:
                r=requests.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":self.claude,"anthropic-version":"2023-06-01","content-type":"application/json"},json={"model":os.getenv("ANTHROPIC_MODEL","claude-3-5-haiku-latest"),"max_tokens":500,"messages":[{"role":"user","content":prompt}]},timeout=15)
                if r.ok:answers.append(("Claude",json.loads(r.json()["content"][0]["text"])))
            except Exception as e:answers.append(("Claude",{"error":str(e)}))
        valid=[x[1] for x in answers if isinstance(x[1],dict) and isinstance(x[1].get("score"),(int,float))]
        return {"responses":answers,"state":"NO DECISION" if not valid else ("AGREE" if len(valid)<2 or abs(float(valid[0]["score"])-float(valid[1]["score"]))<=2 else "DISAGREE"),"score":sum(float(x["score"]) for x in valid)/len(valid) if valid else 5.0}
