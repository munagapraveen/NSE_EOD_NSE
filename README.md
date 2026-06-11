# NSE EOD

Standalone NSE end-of-day downloader built around NSE public archives for price history and NSE public files for symbol master maintenance.

## Scope

- NSE stock, ETF, and index master sync
- NSE raw EOD history download from daily archive files
- Yahoo historical shares outstanding download for market cap only
- NSE split/bonus sync
- self-managed split-adjusted OHLCV materialization
- moving averages: MA5, MA10, MA20, MA50, MA100, MA200
- symbol rename detection and application

## Key modules

- `config.py`
- `db.py`
- `nse.py`
- `sync_symbols.py`
- `download_eod.py`
- `sync_share_counts.py`
- `sync_corporate_actions.py`
- `adjust_splits.py`
- `symbol_change_handler.py`

## Suggested flow

1. Sync master lists
2. Bootstrap raw EOD from `2024-01-01`
3. Download share counts
4. Sync corporate actions and rebuild affected symbols
5. Apply symbol changes when needed
6. Run daily incremental refresh

## Notes

- Raw prices are sourced from NSE archives, not Yahoo.
- Index master data comes from NSE/NSE Indices-facing sources, while daily index prices come from NSE archive files.
- Yahoo is used only for `shares_outstanding`.
- Market cap is stored in `marketcap`, not in `adjusted_eod_prices`.
