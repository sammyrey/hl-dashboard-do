
from datetime import date
import requests
import pandas as pd

BASE = "https://api.polygon.io/v2/aggs/ticker/{symbol}/range/{mult}/{res}/{start}/{end}"

def _iso(d):
    if isinstance(d, date):
        return d.isoformat()
    return d

def fetch_aggs_range(symbol, mult, res, start, end, api_key):
    if not api_key:
        raise RuntimeError("Missing POLYGON_API_KEY")
    url = BASE.format(symbol=symbol.upper(), mult=mult, res=res, start=_iso(start), end=_iso(end))
    params = {"adjusted":"true", "sort":"asc", "limit":50000, "apiKey": api_key}
    out = []
    next_url = None
    while True:
        rq = requests.get(next_url or url, params=None if next_url else params, timeout=30)
        if rq.status_code != 200:
            raise RuntimeError(f"Polygon error {rq.status_code}: {rq.text}")
        data = rq.json()
        results = data.get("results", [])
        for r in results:
            out.append({
                "symbol": symbol.upper(),
                "ts": pd.to_datetime(r["t"], unit="ms", utc=True),
                "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"],
                "volume": r.get("v"), "vwap": r.get("vw"), "trades": r.get("n")
            })
        next_url = data.get("next_url")
        if not next_url:
            break
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out).sort_values("ts").reset_index(drop=True)
    return df
