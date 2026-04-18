"""
Finance MCP server (SSE transport, bearer-token auth).

Routes index queries to Yahoo Finance and everything else to the running
ibkr-agent HTTP server. Exposes three tools to Claude:

  get_quote(symbol)      — auto-routes: ^ prefix / known index → yfinance, else → ibkr
  get_major_indexes()    — snapshot of all major indexes via yfinance
  ask_ibkr(question)     — natural-language query forwarded to ibkr-agent /query
"""

import os
import sys
import httpx
import yfinance as yf
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

load_dotenv(Path(__file__).parent / ".env")

IBKR_BASE = os.environ.get("IBKR_AGENT_URL", "http://localhost:8000")

# Bare symbols (no ^) that are still indexes
_INDEX_ALIASES = {
    "SPX", "SP500", "SPY500",
    "NDX", "NASDAQ", "COMP",
    "DJI", "DOW", "DJIA",
    "RUT", "RUSSELL",
    "VIX",
    "FTSE", "DAX", "GDAXI", "CAC", "CAC40",
    "NIKKEI", "N225",
    "HSI", "HANGSENG",
    "STOXX50", "SX5E",
}

MAJOR_INDEXES = [
    ("^GSPC",     "S&P 500"),
    ("^IXIC",     "NASDAQ Composite"),
    ("^DJI",      "Dow Jones"),
    ("^RUT",      "Russell 2000"),
    ("^VIX",      "VIX"),
    ("^FTSE",     "FTSE 100"),
    ("^N225",     "Nikkei 225"),
    ("^HSI",      "Hang Seng"),
    ("^STOXX50E", "Euro Stoxx 50"),
    ("^GDAXI",    "DAX"),
]


def _is_index(symbol: str) -> bool:
    s = symbol.upper().strip()
    return s.startswith("^") or s in _INDEX_ALIASES


mcp = FastMCP("Finance")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_quote(symbol: str) -> dict:
    """
    Get a real-time quote for any symbol — stock, ETF, or market index.

    For market indexes use Yahoo Finance ticker format:
      ^GSPC (S&P 500), ^IXIC (NASDAQ Composite), ^DJI (Dow Jones),
      ^RUT (Russell 2000), ^VIX, ^FTSE (FTSE 100), ^N225 (Nikkei 225),
      ^HSI (Hang Seng), ^STOXX50E (Euro Stoxx 50), ^GDAXI (DAX), etc.

    For stocks, ETFs, or anything in the IBKR account use the plain ticker:
      AAPL, MSFT, SPY, QQQ, NVDA, TSLA, BRK.B, etc.

    Routing is automatic: index symbols go to Yahoo Finance, everything else
    goes to IBKR. When unsure whether something is an index, use the Yahoo
    Finance ^TICKER format — it will be handled correctly.
    """
    sym = symbol.upper().strip()
    if _is_index(sym):
        return _yf_quote(sym)
    return _ibkr_quote(sym)


@mcp.tool()
def get_major_indexes() -> list:
    """
    Return current quotes for all major global market indexes:
    S&P 500, NASDAQ Composite, Dow Jones, Russell 2000, VIX, FTSE 100,
    Nikkei 225, Hang Seng, Euro Stoxx 50, DAX.

    Use this when the user asks "how are markets doing?", "what's the market at?",
    or wants a broad market overview without naming a specific index.
    """
    results = []
    for ticker, name in MAJOR_INDEXES:
        q = _yf_quote(ticker)
        q["name"] = name
        results.append(q)
    return results


@mcp.tool()
async def ask_ibkr(question: str) -> str:
    """
    Ask a natural-language question answered using live IBKR data.
    Use this for anything portfolio- or stock-specific:

      - "How is my portfolio performing today?"
      - "What stocks are on my watchlist?"
      - "What is Apple's P/E ratio and 52-week range?"
      - "Show me TSLA's price trend over the last month"
      - "What are my biggest positions?"

    Do NOT use this for market index questions — use get_quote or
    get_major_indexes instead.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{IBKR_BASE}/query",
            json={"query": question, "history": []},
        )
        resp.raise_for_status()
        return resp.json()["answer"]


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

class _BearerAuthMiddleware(BaseHTTPMiddleware):
    """Accepts Authorization: Bearer <token> or X-API-Key: <token>."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        x_key = request.headers.get("x-api-key", "")
        q_token = request.query_params.get("token", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        elif x_key:
            provided = x_key.strip()
        else:
            provided = q_token.strip()
        if provided != self._token:
            return PlainTextResponse("Unauthorized", status_code=401)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    api_key = os.environ.get("MCP_API_KEY", "")
    if not api_key:
        sys.exit("MCP_API_KEY is not set — copy .env.example to .env and fill it in.")

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8080"))

    app = mcp.sse_app()
    app.add_middleware(_BearerAuthMiddleware, token=api_key)

    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _yf_quote(symbol: str) -> dict:
    t = yf.Ticker(symbol)
    info = t.fast_info
    last = getattr(info, "last_price", None)
    prev = getattr(info, "previous_close", None)
    high = getattr(info, "day_high", None)
    low  = getattr(info, "day_low", None)
    vol  = getattr(info, "last_volume", None)
    chg  = (last - prev) if (last is not None and prev is not None) else None
    pct  = (chg / prev * 100) if (chg is not None and prev) else None
    return {
        "symbol":     symbol,
        "last":       last,
        "prev_close": prev,
        "change":     round(chg, 4) if chg is not None else None,
        "change_pct": round(pct, 4) if pct is not None else None,
        "day_high":   high,
        "day_low":    low,
        "volume":     vol,
        "source":     "yahoo_finance",
    }


def _ibkr_quote(symbol: str) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{IBKR_BASE}/stock/{symbol}/quote")
        resp.raise_for_status()
        data = resp.json()
        data["source"] = "ibkr"
        return data
