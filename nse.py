"""NSE helpers for master data, archives, and corporate actions."""

from datetime import date, datetime, timedelta
from io import StringIO
import re
from urllib.parse import urljoin

import pandas as pd
import requests

from config import (
    HTTP_HEADERS,
    INDEX_CLOSE_URL,
    NSE_ALL_INDICES_URL,
    NSE_CORP_ACTIONS_URL,
    NSE_SYMBOL_PAGE_URL,
    SECURITY_BHAVCOPY_URL,
)
from logger import get_logger

log = get_logger(__name__)


def round_to_2dp(value):
    if pd.isna(value):
        return value
    return round(float(value), 2)


def create_session():
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    return session


def _resolve_csv_link(html, label_pattern):
    matches = re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S)
    for href, text in matches:
        cleaned = re.sub(r"\s+", " ", text).strip().lower()
        pattern = label_pattern.replace(" ", r"\s*")
        if re.search(pattern, cleaned, flags=re.I):
            return urljoin(NSE_SYMBOL_PAGE_URL, href)
    raise ValueError(f"Could not find NSE CSV link for pattern: {label_pattern}")


def _fetch_csv_from_page(page_url, label_pattern, max_retries=3):
    session = create_session()
    last_err = None
    for attempt in range(max_retries):
        try:
            page = session.get(page_url, timeout=30)
            page.raise_for_status()
            csv_url = _resolve_csv_link(page.text, label_pattern)
            data = session.get(csv_url, timeout=30)
            data.raise_for_status()
            return pd.read_csv(StringIO(data.text))
        except Exception as exc:
            last_err = exc
            log.warning(f"Attempt {attempt + 1}/{max_retries} failed for {label_pattern}: {exc}")
    raise last_err


def fetch_securities_master():
    df = _fetch_csv_from_page(
        NSE_SYMBOL_PAGE_URL,
        r"securities available for equity segment",
    )
    cols = {str(col).strip().upper(): col for col in df.columns}
    rename = {}
    for src, target in [
        ("SYMBOL", "symbol"),
        ("NAME OF COMPANY", "company_name"),
        ("ISIN NUMBER", "isin"),
        ("SERIES", "series"),
    ]:
        if src in cols:
            rename[cols[src]] = target
    df = df.rename(columns=rename)
    required = ["symbol", "company_name", "isin", "series"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"NSE securities file missing columns: {missing}")
    df = df[required].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["company_name"] = df["company_name"].astype(str).str.strip()
    df["isin"] = df["isin"].fillna("").astype(str).str.strip().str.upper()
    df["series"] = df["series"].fillna("").astype(str).str.strip().str.upper()
    return df


def fetch_etf_master():
    df = _fetch_csv_from_page(
        NSE_SYMBOL_PAGE_URL,
        r"securities available for trading in etf",
    )
    cols = {str(col).strip().upper(): col for col in df.columns}
    rename = {}
    for src, target in [
        ("SYMBOL", "symbol"),
        ("COMPANY NAME", "company_name"),
        ("NAME OF COMPANY", "company_name"),
        ("ISIN", "isin"),
        ("ISIN NUMBER", "isin"),
    ]:
        if src in cols:
            rename[cols[src]] = target
    df = df.rename(columns=rename)
    for col in ["symbol", "company_name", "isin"]:
        if col not in df.columns:
            df[col] = ""
    df["series"] = "ETF"
    df = df[["symbol", "company_name", "isin", "series"]].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["company_name"] = df["company_name"].astype(str).str.strip()
    df["isin"] = df["isin"].fillna("").astype(str).str.strip().str.upper()
    return df


def fetch_indices_master():
    session = create_session()
    session.get("https://www.nseindia.com/", timeout=15)
    response = session.get(NSE_ALL_INDICES_URL, timeout=15)
    response.raise_for_status()
    data = response.json().get("data", [])
    rows = []
    for item in data:
        symbol = item.get("indexSymbol")
        if symbol:
            rows.append({"symbol": str(symbol).strip().upper()})
    return pd.DataFrame(rows)


def fetch_symbol_changes():
    df = _fetch_csv_from_page(
        NSE_SYMBOL_PAGE_URL,
        r"changes in symbols",
    )
    has_header = any(re.search(r"OLD.SYMBOL", str(col), re.I) for col in df.columns)
    if not has_header:
        session = create_session()
        page = session.get(NSE_SYMBOL_PAGE_URL, timeout=30)
        page.raise_for_status()
        csv_url = _resolve_csv_link(page.text, r"changes in symbols")
        data = session.get(csv_url, timeout=30)
        data.raise_for_status()
        df = pd.read_csv(StringIO(data.text), header=None)
        if len(df.columns) >= 4:
            df.columns = ["company_name", "old_symbol", "new_symbol", "effective_date"] + list(df.columns[4:])
    else:
        cols = {str(col).strip().upper(): col for col in df.columns}
        rename = {}
        for src, target in [
            ("OLD SYMBOL", "old_symbol"),
            ("NEW SYMBOL", "new_symbol"),
            ("APPLICABLE FROM", "effective_date"),
        ]:
            if src in cols:
                rename[cols[src]] = target
        df = df.rename(columns=rename)

    def parse_dt(value):
        try:
            return datetime.strptime(str(value).strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
        except Exception:
            return None

    df = df[[col for col in ["old_symbol", "new_symbol", "effective_date"] if col in df.columns]].copy()
    df["effective_date"] = df["effective_date"].apply(parse_dt)
    df["old_symbol"] = df["old_symbol"].astype(str).str.strip().str.upper()
    df["new_symbol"] = df["new_symbol"].astype(str).str.strip().str.upper()
    return df


def fetch_nse_corporate_actions(start_date="01-01-2024", end_date=None):
    log.info(f"Fetching corporate actions from NSE ({start_date} to {end_date or 'today'})...")
    session = create_session()
    from_dt = start_date
    to_dt = end_date or datetime.today().strftime("%d-%m-%Y")
    url = f"{NSE_CORP_ACTIONS_URL}&from_date={from_dt}&to_date={to_dt}"
    session.headers.update({"Referer": "https://www.nseindia.com/market-data/corporate-actions"})
    try:
        session.get("https://www.nseindia.com/", timeout=15)
        response = session.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        log.warning(f"Could not fetch NSE corporate actions: {exc}")
        return pd.DataFrame()

    rows = []
    for item in data:
        symbol = item.get("symbol")
        ex_date = item.get("exDate")
        subject_raw = item.get("subject", "")
        subject = subject_raw.lower()
        if not symbol or not ex_date:
            continue
        if "split" in subject or "sub-division" in subject:
            match = re.search(r"from r[es]?\s*(\d+(?:\.\d+)?).*?to r[es]?\s*(\d+(?:\.\d+)?)", subject)
            if not match:
                match = re.search(r"(\d+(?:\.\d+)?)\s*to\s*(\d+(?:\.\d+)?)", subject)
            if match:
                old_fv = float(match.group(1))
                new_fv = float(match.group(2))
                if new_fv > 0:
                    rows.append({
                        "symbol": str(symbol).strip().upper(),
                        "ex_date": datetime.strptime(ex_date, "%d-%b-%Y").strftime("%Y-%m-%d"),
                        "action_type": "split",
                        "value": round(old_fv / new_fv, 6),
                        "source": "nse",
                        "note": subject_raw,
                    })
        if "bonus" in subject:
            match = re.search(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", subject)
            if match:
                bonus_qty = float(match.group(1))
                existing_qty = float(match.group(2))
                if existing_qty > 0:
                    rows.append({
                        "symbol": str(symbol).strip().upper(),
                        "ex_date": datetime.strptime(ex_date, "%d-%b-%Y").strftime("%Y-%m-%d"),
                        "action_type": "bonus",
                        "value": round((bonus_qty + existing_qty) / existing_qty, 6),
                        "source": "nse",
                        "note": subject_raw,
                    })
    return pd.DataFrame(rows)


def iter_calendar_dates(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _prepare_csv_response(url, session, timeout=30):
    response = session.get(url, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.text


def fetch_security_bhavcopy(report_date, session=None):
    session = session or create_session()
    ddmmyyyy = report_date.strftime("%d%m%Y")
    url = SECURITY_BHAVCOPY_URL.format(ddmmyyyy=ddmmyyyy)
    payload = _prepare_csv_response(url, session)
    if payload is None or not payload.strip():
        return pd.DataFrame()
    df = pd.read_csv(StringIO(payload))
    df.columns = [str(col).strip().upper() for col in df.columns]
    df["DATE"] = report_date.strftime("%Y-%m-%d")
    df["SOURCE_FILE"] = url
    return df


def fetch_index_close(report_date, session=None):
    session = session or create_session()
    ddmmyyyy = report_date.strftime("%d%m%Y")
    url = INDEX_CLOSE_URL.format(ddmmyyyy=ddmmyyyy)
    payload = _prepare_csv_response(url, session)
    if payload is None or not payload.strip():
        return pd.DataFrame()
    df = pd.read_csv(StringIO(payload))
    df.columns = [str(col).strip().upper() for col in df.columns]
    df["SOURCE_FILE"] = url
    return df


def _pick_column(df, candidates):
    available = {str(col).strip().upper(): col for col in df.columns}
    for candidate in candidates:
        if candidate in available:
            return available[candidate]
    return None


def normalize_security_bhavcopy(df):
    if df.empty:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume", "source", "source_file", "series"])

    symbol_col = _pick_column(df, ["SYMBOL"])
    series_col = _pick_column(df, ["SERIES"])
    date_col = _pick_column(df, ["DATE1", "DATE"])
    open_col = _pick_column(df, ["OPEN_PRICE", "OPEN"])
    high_col = _pick_column(df, ["HIGH_PRICE", "HIGH"])
    low_col = _pick_column(df, ["LOW_PRICE", "LOW"])
    close_col = _pick_column(df, ["CLOSE_PRICE", "CLOSE"])
    volume_col = _pick_column(df, ["TTL_TRD_QNTY", "TOTTRDQTY", "TOTTRD_QTY"])
    source_file_col = _pick_column(df, ["SOURCE_FILE"])

    if not all([symbol_col, date_col, open_col, high_col, low_col, close_col, volume_col]):
        raise ValueError(f"Unexpected security bhavcopy columns: {list(df.columns)}")

    out = pd.DataFrame()
    out["symbol"] = df[symbol_col].astype(str).str.strip().str.upper()
    out["series"] = df[series_col].astype(str).str.strip().str.upper() if series_col else ""
    out["date"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")
    for src, target in [
        (open_col, "open"),
        (high_col, "high"),
        (low_col, "low"),
        (close_col, "close"),
        (volume_col, "volume"),
    ]:
        out[target] = pd.to_numeric(df[src], errors="coerce")
    for col in ["open", "high", "low", "close"]:
        out[col] = out[col].apply(round_to_2dp)
    out["source"] = "nse-security-bhavcopy"
    out["source_file"] = df[source_file_col] if source_file_col else ""
    out = out.dropna(subset=["symbol", "date", "close"])
    return out


def normalize_index_close(df):
    if df.empty:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume", "source", "source_file"])

    symbol_col = _pick_column(df, ["INDEX NAME", "INDEX_NAME"])
    date_col = _pick_column(df, ["INDEX DATE", "DATE"])
    open_col = _pick_column(df, ["OPEN INDEX VALUE", "OPEN"])
    high_col = _pick_column(df, ["HIGH INDEX VALUE", "HIGH"])
    low_col = _pick_column(df, ["LOW INDEX VALUE", "LOW"])
    close_col = _pick_column(df, ["CLOSING INDEX VALUE", "CLOSE"])
    source_file_col = _pick_column(df, ["SOURCE_FILE"])

    if not all([symbol_col, date_col, open_col, high_col, low_col, close_col]):
        raise ValueError(f"Unexpected index close columns: {list(df.columns)}")

    out = pd.DataFrame()
    out["symbol"] = df[symbol_col].astype(str).str.strip().str.upper()
    out["date"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")
    for src, target in [
        (open_col, "open"),
        (high_col, "high"),
        (low_col, "low"),
        (close_col, "close"),
    ]:
        out[target] = pd.to_numeric(df[src], errors="coerce")
    for col in ["open", "high", "low", "close"]:
        out[col] = out[col].apply(round_to_2dp)
    out["volume"] = None
    out["source"] = "nse-index-close"
    out["source_file"] = df[source_file_col] if source_file_col else ""
    out = out.dropna(subset=["symbol", "date", "close"])
    return out
