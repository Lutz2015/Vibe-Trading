from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Protocol
from urllib.request import Request, urlopen

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

try:
    import akshare as ak
except ImportError:  # optional provider
    ak = None

try:
    import jqdatasdk as jq
except ImportError:  # optional provider
    jq = None

app = FastAPI(title="market-data", version="0.1.0")


class Quote(BaseModel):
    symbol: str
    name: str
    open: float
    high: float
    low: float
    price: float
    prev_close: float
    volume: float
    amount: float
    ts: str
    source: str


class Bar(BaseModel):
    symbol: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    source: str


class MarketDataProvider(Protocol):
    name: str

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        ...

    def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[Bar]:
        ...


def normalize_symbol(symbol: str) -> str:
    token = symbol.strip().upper()
    if "." in token:
        code, exchange = token.split(".", 1)
        return f"{code}.{exchange}"
    if token.startswith("SH") and len(token) == 8:
        return f"{token[2:]}.SH"
    if token.startswith("SZ") and len(token) == 8:
        return f"{token[2:]}.SZ"
    if len(token) == 6 and token[0] in {"5", "6", "9"}:
        return f"{token}.SH"
    if len(token) == 6:
        return f"{token}.SZ"
    raise ValueError(f"Unsupported symbol format: {symbol}")


def to_sina_symbol(symbol: str) -> str:
    std = normalize_symbol(symbol)
    code, exchange = std.split(".", 1)
    return f"{exchange.lower()}{code}"


@dataclass
class SinaProvider:
    name: str = "sina"

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        sina_symbols = [to_sina_symbol(symbol) for symbol in symbols]
        url = f"https://hq.sinajs.cn/list={','.join(sina_symbols)}"
        request = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
        )
        with urlopen(request, timeout=10) as response:
            content = response.read().decode("gbk", errors="replace")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return [self._parse_line(line) for line in lines]

    def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[Bar]:
        raise NotImplementedError("Sina provider does not support historical daily bars.")

    def _parse_line(self, line: str) -> Quote:
        left, raw = line.split("=", 1)
        raw_symbol = left.split("_")[-1]
        payload = raw.strip().strip(";").strip('"')
        parts = payload.split(",")
        if len(parts) < 10:
            raise ValueError(f"Unexpected quote payload: {line}")
        ts_date, ts_time = extract_date_time(parts)
        code = raw_symbol[2:]
        exchange = raw_symbol[:2].upper()
        std_symbol = f"{code}.{exchange}"
        return Quote(
            symbol=std_symbol,
            name=parts[0],
            open=float(parts[1] or 0.0),
            prev_close=float(parts[2] or 0.0),
            price=float(parts[3] or 0.0),
            high=float(parts[4] or 0.0),
            low=float(parts[5] or 0.0),
            volume=float(parts[8] or 0.0),
            amount=float(parts[9] or 0.0),
            ts=f"{ts_date} {ts_time}".strip(),
            source=self.name,
        )


def extract_date_time(parts: list[str]) -> tuple[str, str]:
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    time_re = re.compile(r"^\d{2}:\d{2}:\d{2}$")
    date_token = ""
    time_token = ""
    for token in reversed(parts):
        clean = token.strip()
        if not clean:
            continue
        if not time_token and time_re.match(clean):
            time_token = clean
            continue
        if not date_token and date_re.match(clean):
            date_token = clean
            break
    return date_token, time_token


@dataclass
class AkshareProvider:
    name: str = "akshare"

    def __post_init__(self) -> None:
        if ak is None:
            raise RuntimeError("akshare is not installed. Install it or switch provider to sina.")

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        frame = ak.stock_zh_a_spot_em()
        quotes: list[Quote] = []
        now = datetime.utcnow().isoformat()
        for symbol in symbols:
            std = normalize_symbol(symbol)
            code = std.split(".")[0]
            rows = frame[frame["代码"] == code]
            if rows.empty:
                continue
            row = rows.iloc[0]
            quotes.append(
                Quote(
                    symbol=std,
                    name=str(row["名称"]),
                    open=float(row["今开"]),
                    prev_close=float(row["昨收"]),
                    price=float(row["最新价"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    volume=float(row["成交量"]),
                    amount=float(row["成交额"]),
                    ts=now,
                    source=self.name,
                )
            )
        return quotes

    def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[Bar]:
        std = normalize_symbol(symbol)
        code, exchange = std.split(".", 1)
        if exchange == "SH":
            raw_code = f"{code}"
        else:
            raw_code = f"{code}"
        frame = ak.stock_zh_a_hist(
            symbol=raw_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        bars: list[Bar] = []
        for _, row in frame.iterrows():
            bars.append(
                Bar(
                    symbol=std,
                    trade_date=str(row["日期"]),
                    open=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=float(row["成交量"]),
                    amount=float(row["成交额"]),
                    source=self.name,
                )
            )
        return bars


@dataclass
class EastmoneyProvider:
    name: str = "eastmoney"

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        quotes: list[Quote] = []
        for symbol in symbols:
            quotes.append(self._get_single_quote(symbol))
        return quotes

    def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[Bar]:
        raise NotImplementedError("Eastmoney provider does not support daily bars in this adapter.")

    def _get_single_quote(self, symbol: str) -> Quote:
        std = normalize_symbol(symbol)
        secid = to_eastmoney_secid(std)
        fields = "f43,f44,f45,f46,f47,f48,f57,f58"
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields={fields}"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com"})
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))

        data = payload.get("data")
        if data is None:
            raise ValueError(f"empty_data for {std}")

        now = datetime.utcnow().isoformat()
        return Quote(
            symbol=std,
            name=str(data.get("f58", "")),
            open=to_price(data.get("f46")),
            prev_close=0.0,
            price=to_price(data.get("f43")),
            high=to_price(data.get("f44")),
            low=to_price(data.get("f45")),
            volume=float(data.get("f47") or 0.0),
            amount=float(data.get("f48") or 0.0),
            ts=now,
            source=self.name,
        )


def to_price(value: object) -> float:
    if value is None:
        return 0.0
    return float(value) / 100.0


def to_eastmoney_secid(symbol: str) -> str:
    std = normalize_symbol(symbol)
    code, exchange = std.split(".", 1)
    if exchange == "SH":
        return f"1.{code}"
    if exchange == "SZ":
        return f"0.{code}"
    raise ValueError(f"Unsupported exchange for eastmoney: {exchange}")


def to_joinquant_symbol(symbol: str) -> str:
    std = normalize_symbol(symbol)
    code, exchange = std.split(".", 1)
    if exchange == "SH":
        return f"{code}.XSHG"
    if exchange == "SZ":
        return f"{code}.XSHE"
    raise ValueError(f"Unsupported exchange for joinquant: {exchange}")


@dataclass
class JoinquantProvider:
    name: str = "joinquant"
    _authed: bool = False

    def _ensure_auth(self) -> None:
        if self._authed:
            return
        if jq is None:
            raise RuntimeError("jqdatasdk is not installed. Install it or switch provider.")
        username = os.getenv("JQ_USERNAME", "").strip()
        password = os.getenv("JQ_PASSWORD", "").strip()
        if not username or not password:
            raise RuntimeError("missing JQ_USERNAME/JQ_PASSWORD environment variables")
        jq.auth(username, password)
        self._authed = True

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        self._ensure_auth()
        quotes: list[Quote] = []
        for symbol in symbols:
            jq_symbol = to_joinquant_symbol(symbol)
            std = normalize_symbol(symbol)
            frame = jq.get_price(
                jq_symbol,
                count=2,
                frequency="daily",
                fields=["open", "close", "high", "low", "volume", "money"],
            )
            if frame is None or frame.empty:
                continue
            current = frame.iloc[-1]
            prev_close = float(frame.iloc[-2]["close"]) if len(frame) > 1 else float(current["open"])
            ts = str(frame.index[-1])
            quotes.append(
                Quote(
                    symbol=std,
                    name=jq_symbol,
                    open=float(current["open"]),
                    prev_close=prev_close,
                    price=float(current["close"]),
                    high=float(current["high"]),
                    low=float(current["low"]),
                    volume=float(current["volume"]),
                    amount=float(current["money"]),
                    ts=ts,
                    source=self.name,
                )
            )
        return quotes

    def get_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[Bar]:
        self._ensure_auth()
        jq_symbol = to_joinquant_symbol(symbol)
        std = normalize_symbol(symbol)
        frame = jq.get_price(
            jq_symbol,
            start_date=start_date,
            end_date=end_date,
            frequency="daily",
            fields=["open", "close", "high", "low", "volume", "money"],
        )
        if frame is None or frame.empty:
            return []
        bars: list[Bar] = []
        for idx, row in frame.iterrows():
            bars.append(
                Bar(
                    symbol=std,
                    trade_date=str(idx.date()),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    amount=float(row["money"]),
                    source=self.name,
                )
            )
        return bars


def build_provider() -> MarketDataProvider:
    provider_name = os.getenv("MARKET_DATA_PROVIDER", "sina").strip().lower()
    if provider_name == "sina":
        return SinaProvider()
    if provider_name == "akshare":
        return AkshareProvider()
    if provider_name == "eastmoney":
        return EastmoneyProvider()
    if provider_name == "joinquant":
        return JoinquantProvider()
    raise RuntimeError(f"Unknown provider: {provider_name}")


def build_fallback_provider() -> MarketDataProvider | None:
    fallback_name = os.getenv("MARKET_DATA_FALLBACK_PROVIDER", "").strip().lower()
    if not fallback_name:
        return None
    if fallback_name == provider.name:
        return None
    if fallback_name == "sina":
        return SinaProvider()
    if fallback_name == "akshare":
        return AkshareProvider()
    if fallback_name == "eastmoney":
        return EastmoneyProvider()
    if fallback_name == "joinquant":
        return JoinquantProvider()
    return None


provider = build_provider()
fallback_provider = build_fallback_provider()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "market-data", "provider": provider.name}


@app.get("/market/quotes", response_model=list[Quote])
def market_quotes(symbols: str = Query(..., description="Comma-separated symbols")) -> list[Quote]:
    symbol_list = [token.strip() for token in symbols.split(",") if token.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided.")
    try:
        return provider.get_quotes(symbol_list)
    except Exception as exc:
        if fallback_provider is not None:
            try:
                return fallback_provider.get_quotes(symbol_list)
            except Exception as fallback_exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"quote_fetch_failed: primary={exc}; fallback={fallback_exc}",
                ) from fallback_exc
        raise HTTPException(status_code=502, detail=f"quote_fetch_failed: {exc}") from exc


@app.get("/market/bars", response_model=list[Bar])
def market_bars(
    symbol: str,
    start_date: str = Query(..., description="YYYYMMDD"),
    end_date: str = Query(..., description="YYYYMMDD"),
) -> list[Bar]:
    try:
        return provider.get_daily_bars(symbol=symbol, start_date=start_date, end_date=end_date)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"bar_fetch_failed: {exc}") from exc


if __name__ == "__main__":
    port = int(os.getenv("MARKET_DATA_PORT", "8004"))
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=False)
