"""US stock market data fetcher — QuantGPT

Provides OHLCV data for US equities via yfinance (Yahoo Finance),
aligned with the same interface as market_data.py (A-share).

Data sources:
- yfinance (primary, free, no API key required)
- Parquet cache (data/us_stocks/{ticker}.parquet)

Universe presets:
- sp500     : S&P 500 components (via yfinance)
- nasdaq100 : NASDAQ 100 components
- us_nyse   : Major NYSE stocks
- us_nasdaq : Major NASDAQ stocks

Benchmarks:
- SPY (S&P 500), QQQ (NASDAQ 100), IWM (Russell 2000), DIA (Dow 30)
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ─── Stock Universe Definitions ─────────────────────────────────────

US_UNIVERSES: dict[str, list[str]] = {
    "sp500": [],       # dynamically fetched below
    "nasdaq100": [],
    "us_nyse": [
        "JPM", "BAC", "GS", "MS", "C", "WFC", "BK", "AXP",
        "XOM", "CVX", "COP", "OXY",
        "GE", "CAT", "MMM", "BA", "HON", "UNP", "UPS",
        "KO", "PG", "PEP", "WMT", "COST", "MCD", "DIS",
        "JNJ", "PFE", "MRK", "ABBV", "UNH", "ABT", "TMO",
        "V", "MA", "HD", "NKE", "VZ", "T", "IBM", "CSCO",
        "ORCL", "CRM", "AMD", "INTC",
    ],
    "us_nasdaq": [
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA",
        "META", "TSLA", "AVGO", "ADBE", "NFLX", "COST",
        "CMCSA", "PEP", "TMUS", "AMAT", "INTU", "QCOM",
        "TXN", "BKNG", "SBUX", "AMD", "GILD", "REGN",
        "VRTX", "ISRG", "MRNA", "ADP", "MU", "PANW",
    ],
}

# S&P 500 / NASDAQ 100 — fetch dynamically from Wikipedia
def _fetch_sp500_components() -> list[str]:
    """Fetch current S&P 500 tickers from Wikipedia."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        df = tables[0]
        tickers = sorted(df["Symbol"].str.replace(".", "-", regex=False).tolist())
        logger.info(f"Fetched {len(tickers)} S&P 500 components")
        return tickers
    except Exception as e:
        logger.warning(f"Failed to fetch S&P 500 list: {e}")
        return US_UNIVERSES["us_nyse"].copy()


def _fetch_nasdaq100_components() -> list[str]:
    """Fetch current NASDAQ 100 tickers from Wikipedia."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq_100")
        df = tables[4]  # the 5th table is the component list
        tickers = sorted(df["Ticker"].str.replace(".", "-", regex=False).tolist())
        logger.info(f"Fetched {len(tickers)} NASDAQ 100 components")
        return tickers
    except Exception as e:
        logger.warning(f"Failed to fetch NASDAQ 100 list: {e}")
        return US_UNIVERSES["us_nasdaq"].copy()


# Populate dynamic universes at module load
US_UNIVERSES["sp500"] = _fetch_sp500_components()
US_UNIVERSES["nasdaq100"] = _fetch_nasdaq100_components()

# ─── Benchmark Definitions ──────────────────────────────────────────

US_BENCHMARKS: dict[str, str] = {
    "sp500": "SPY",
    "nasdaq": "QQQ",
    "russell2000": "IWM",
    "dow30": "DIA",
}


def get_us_universe(name: str) -> list[str]:
    """Get stock list for a US universe name.

    Supports: sp500, nasdaq100, us_nyse, us_nasdaq, us_all
    """
    name = name.lower()
    if name == "us_all":
        return list(set(US_UNIVERSES["us_nyse"] + US_UNIVERSES["us_nasdaq"]))
    if name in US_UNIVERSES:
        return US_UNIVERSES[name]
    if name in ("sp500", "s&p500", "snp500"):
        return US_UNIVERSES["sp500"]
    if name in ("nasdaq100", "ndx"):
        return US_UNIVERSES["nasdaq100"]
    raise ValueError(f"Unknown US universe: {name}")


# ─── USMarketDataFetcher ────────────────────────────────────────────

class USMarketDataFetcher:
    """US stock data fetcher with per-ticker Parquet caching.

    Fetches daily OHLCV data via yfinance.
    Caches per ticker in data/us_stocks/{ticker}.parquet.
    """

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir or str(_PROJECT_ROOT / "data" / "us_stocks")
        os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_path(self, ticker: str) -> str:
        safe = ticker.replace(".", "_").upper()
        return os.path.join(self.cache_dir, f"{safe}.parquet")

    def _load_cache(self, ticker: str) -> pd.DataFrame | None:
        path = self._cache_path(ticker)
        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                return df
            except Exception as e:
                logger.warning(f"US cache load failed for {ticker}: {e}")
        return None

    def _save_cache(self, ticker: str, df: pd.DataFrame):
        if df is None or len(df) == 0:
            return
        df.to_parquet(self._cache_path(ticker), index=False)

    def fetch_stocks(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch multiple US stocks with caching. Returns concatenated DataFrame.

        Columns: trade_date, stock_code, open, high, low, close, volume, amount, pct_change
        """
        import yfinance as yf

        all_data: list[pd.DataFrame] = []
        to_fetch: list[str] = []

        req_start, req_end = pd.Timestamp(start_date), pd.Timestamp(end_date)

        # Check cache first
        for code in stock_codes:
            cached = self._load_cache(code)
            if cached is not None and len(cached) > 0:
                cache_min = cached["trade_date"].min()
                cache_max = cached["trade_date"].max()
                if cache_min <= req_start + pd.Timedelta(days=5) and cache_max >= req_end - pd.Timedelta(days=5):
                    filtered = cached[(cached["trade_date"] >= req_start) & (cached["trade_date"] <= req_end)].copy()
                    if len(filtered) > 0:
                        if "stock_code" not in filtered.columns:
                            filtered["stock_code"] = code
                        all_data.append(filtered)
                        continue
            to_fetch.append(code)

        if not to_fetch:
            if not all_data:
                return None
            result = pd.concat(all_data, ignore_index=True)
            if "amount" not in result.columns:
                result["amount"] = result["close"] * result["volume"]
            if "pct_change" not in result.columns:
                result["pct_change"] = result.groupby("stock_code")["close"].pct_change() * 100
            result = result.sort_values(["trade_date", "stock_code"]).reset_index(drop=True)
            return result

        # Fetch from yfinance
        try:
            logger.info(f"[yfinance] Fetching {len(to_fetch)} US stocks...")
            # yfinance download returns a DataFrame with MultiIndex columns
            yf_data = yf.download(
                to_fetch,
                start=start_date,
                end=(pd.Timestamp(end_date) + timedelta(days=1)).strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=True,
                actions=False,
            )

            if yf_data is None or yf_data.empty:
                logger.warning("[yfinance] No data returned")
            else:
                # yfinance returns columns as (OHLCV, Ticker) MultiIndex
                # or single-level for single ticker; handle both
                for code in to_fetch:
                    try:
                        if isinstance(yf_data.columns, pd.MultiIndex):
                            df_single = yf_data.xs(code, axis=1, level=1).copy()
                        else:
                            # Single ticker fallback
                            df_single = yf_data.copy()

                        if df_single.empty:
                            continue

                        df_single.columns = [c.lower() for c in df_single.columns]
                        df_single = df_single.reset_index()
                        df_single.rename(columns={"date": "trade_date", "adj close": "close"}, inplace=True)

                        if "trade_date" not in df_single.columns:
                            df_single.rename(columns={df_single.columns[0]: "trade_date"}, inplace=True)

                        df_single["trade_date"] = pd.to_datetime(df_single["trade_date"])
                        df_single["stock_code"] = code

                        # Ensure required columns
                        for col in ("open", "high", "low", "close", "volume"):
                            if col not in df_single.columns:
                                df_single[col] = float("nan")

                        # amount = close * volume (approximation)
                        df_single["amount"] = df_single["close"] * df_single["volume"]

                        # pct_change = daily return in %, per ticker
                        df_single["pct_change"] = df_single["close"].pct_change() * 100

                        # Drop rows with no close price
                        df_single = df_single.dropna(subset=["close"])

                        if len(df_single) == 0:
                            continue

                        # Update cache
                        existing = self._load_cache(code)
                        if existing is not None:
                            combined = (
                                pd.concat([existing, df_single])
                                .drop_duplicates("trade_date", keep="last")
                                .sort_values("trade_date")
                            )
                            self._save_cache(code, combined)
                        else:
                            self._save_cache(code, df_single)

                        # Filter to requested range
                        df_filtered = df_single[
                            (df_single["trade_date"] >= req_start) & (df_single["trade_date"] <= req_end)
                        ]
                        if len(df_filtered) > 0:
                            all_data.append(df_filtered)

                    except Exception as e:
                        logger.warning(f"[yfinance] Failed to process {code}: {e}")
                        continue

        except Exception as e:
            logger.error(f"[yfinance] Download failed: {e}")

        if not all_data:
            return None

        result = pd.concat(all_data, ignore_index=True)
        result = result.sort_values(["trade_date", "stock_code"]).reset_index(drop=True)

        # Select and order columns to match QuantGPT convention
        cols = ["trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount", "pct_change"]
        available_cols = [c for c in cols if c in result.columns]
        return result[available_cols]


# ─── Benchmark Returns ──────────────────────────────────────────────

def fetch_benchmark_returns(
    benchmark: str,
    start_date: str,
    end_date: str,
) -> pd.Series | None:
    """Fetch daily returns for a US benchmark ETF.

    Parameters
    ----------
    benchmark : str
        One of: sp500, nasdaq, russell2000, dow30, or a direct ticker (SPY, QQQ, etc.)
    start_date, end_date : str
        Date range in YYYY-MM-DD format.

    Returns
    -------
    pd.Series with datetime index and daily returns (as decimals, not %).
    """
    import yfinance as yf

    ticker = US_BENCHMARKS.get(benchmark.lower(), benchmark.upper())
    try:
        df = yf.download(
            ticker,
            start=start_date,
            end=(pd.Timestamp(end_date) + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty:
            logger.warning(f"[yfinance] No benchmark data for {ticker}")
            return None

        if isinstance(df.columns, pd.MultiIndex):
            # yfinance returns MultiIndex for single ticker too
            close = df.xs(ticker, axis=1, level=1)["Close"] if "Close" in df.columns else df.iloc[:, 0]
        else:
            close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        returns = close.squeeze().pct_change().dropna()
        returns.index = pd.to_datetime(returns.index)
        returns.name = ticker
        return returns
    except Exception as e:
        logger.error(f"[yfinance] Benchmark fetch failed for {ticker}: {e}")
        return None
