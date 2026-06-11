"""Configuration for the standalone NSE EOD project."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "nse_eod.db"
LOG_FILE = LOG_DIR / "nse_eod.log"
FAILED_SHARES_FILE = DATA_DIR / "share_download_failures_latest.csv"
FAILED_EOD_FILE = DATA_DIR / "eod_download_failures_latest.csv"

DEFAULT_BATCH_SIZE = 20
DEFAULT_HISTORY_START = "2024-01-01"
DEFAULT_SHARE_REFRESH_DAYS = 120

NSE_REPORTS_URL = "https://www.nseindia.com/all-reports"
NSE_SYMBOL_PAGE_URL = "https://www.nseindia.com/static/market-data/securities-available-for-trading"
NSE_CORP_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateactions?index=equities"
NSE_ALL_INDICES_URL = "https://www.nseindia.com/api/allIndices"

# Public daily archives used for raw EOD history.
SECURITY_BHAVCOPY_URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
INDEX_CLOSE_URL = "https://archives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
