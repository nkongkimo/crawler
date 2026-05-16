#!/usr/bin/env python3
"""Download Taiwan Stock Exchange price data.

Examples:
  python twse_stock_scraper.py mi-index --date 20260512
  python twse_stock_scraper.py mi-index --start 20260501 --end 20260512 -o prices.csv
  python twse_stock_scraper.py stock-day --stock-no 2330 --date 20260501
  python twse_stock_scraper.py t86 --stock-no 00981A --date 20260512
  python twse_stock_scraper.py t86 --stock-no 00981A --start 20260501 --end 20260512 -o t86_00981A.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TWSE_BASE = "https://www.twse.com.tw"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


MI_INDEX_COLUMNS = [
    "date",
    "stock_id",
    "stock_name",
    "trade_volume",
    "transaction",
    "trade_value",
    "open",
    "high",
    "low",
    "close",
    "change_sign",
    "change",
    "last_best_bid_price",
    "last_best_bid_volume",
    "last_best_ask_price",
    "last_best_ask_volume",
    "price_earning_ratio",
]

T86_COLUMNS = [
    "date",
    "stock_id",
    "stock_name",
    "foreign_buy_shares",
    "foreign_sell_shares",
    "foreign_net_shares",
    "investment_trust_buy_shares",
    "investment_trust_sell_shares",
    "investment_trust_net_shares",
    "dealer_net_shares",
    "dealer_self_buy_shares",
    "dealer_self_sell_shares",
    "dealer_self_net_shares",
    "dealer_hedge_buy_shares",
    "dealer_hedge_sell_shares",
    "dealer_hedge_net_shares",
    "total_net_shares",
    "foreign_buy_lots",
    "foreign_sell_lots",
    "foreign_net_lots",
    "investment_trust_buy_lots",
    "investment_trust_sell_lots",
    "investment_trust_net_lots",
    "dealer_net_lots",
    "dealer_self_buy_lots",
    "dealer_self_sell_lots",
    "dealer_self_net_lots",
    "dealer_hedge_buy_lots",
    "dealer_hedge_sell_lots",
    "dealer_hedge_net_lots",
    "total_net_lots",
]


def parse_yyyymmdd(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式請用 YYYYMMDD，例如 20260512") from exc


def date_range(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    if end < start:
        raise ValueError("--end 不可早於 --start")

    current = start
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def request_bytes(
    path: str,
    params: dict[str, str],
    timeout: int = 20,
    referer: str = "/zh/trading/historical/mi-index.html",
) -> bytes:
    url = f"{TWSE_BASE}{path}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/csv,application/json,text/plain,*/*",
            "Referer": f"{TWSE_BASE}{referer}",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise RuntimeError(f"TWSE 回應 HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"無法連線 TWSE: {exc.reason}") from exc


def decode_twse_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp950", "big5", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def clean_cell(value: str) -> str:
    value = value.strip()
    if value.startswith("="):
        value = value[1:].strip()
    return value.strip('"')


def normalize_mi_index_row(trade_date: dt.date, row: list[str]) -> dict[str, str] | None:
    row = [clean_cell(cell) for cell in row]
    if len(row) < 16:
        return None
    if not re.fullmatch(r"\d{4,6}[A-Z]?", row[0]):
        return None

    # TWSE sometimes splits +/- and change into separate columns.
    if len(row) >= 17:
        values = row[:17]
    else:
        values = row[:10] + [""] + row[10:16]

    return dict(zip(MI_INDEX_COLUMNS, [trade_date.strftime("%Y-%m-%d")] + values))


def parse_mi_index_csv(trade_date: dt.date, text: str) -> list[dict[str, str]]:
    reader = csv.reader(text.splitlines())
    rows = list(reader)
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if any("證券代號" in cell for cell in row) and any("證券名稱" in cell for cell in row)
        ),
        None,
    )
    if header_index is None:
        return []

    records: list[dict[str, str]] = []
    for row in rows[header_index + 1 :]:
        record = normalize_mi_index_row(trade_date, row)
        if record is not None:
            records.append(record)
    return records


def fetch_mi_index(trade_date: dt.date, stock_type: str) -> list[dict[str, str]]:
    raw = request_bytes(
        "/exchangeReport/MI_INDEX",
        {
            "response": "csv",
            "date": trade_date.strftime("%Y%m%d"),
            "type": stock_type,
        },
    )
    text = decode_twse_csv(raw)
    return parse_mi_index_csv(trade_date, text)


def fetch_stock_day(stock_no: str, trade_date: dt.date) -> tuple[list[str], list[list[str]]]:
    raw = request_bytes(
        "/exchangeReport/STOCK_DAY",
        {
            "response": "json",
            "date": trade_date.strftime("%Y%m%d"),
            "stockNo": stock_no,
        },
    )
    payload = json.loads(raw.decode("utf-8-sig"))
    if payload.get("stat") != "OK":
        raise RuntimeError(payload.get("stat", "TWSE 沒有回傳資料"))
    return payload["fields"], payload["data"]


def parse_twse_int(value: str) -> int:
    value = clean_cell(value).replace(",", "")
    if not value or value == "--":
        return 0
    return int(value)


def find_field(row: dict[str, str], *patterns: str) -> str:
    for key, value in row.items():
        if all(pattern in key for pattern in patterns):
            return value
    return "0"


def shares_to_lots(shares: int, unit_size: int) -> str:
    return f"{shares / unit_size:.3f}".rstrip("0").rstrip(".")


def normalize_t86_row(
    trade_date: dt.date,
    fields: list[str],
    row: list[str],
    unit_size: int,
) -> dict[str, str]:
    values = [clean_cell(cell) for cell in row]
    row_by_field = dict(zip(fields, values))

    stock_id = values[0] if values else ""
    stock_name = values[1] if len(values) > 1 else ""
    foreign_buy = parse_twse_int(find_field(row_by_field, "外", "買進"))
    foreign_sell = parse_twse_int(find_field(row_by_field, "外", "賣出"))
    foreign_net = parse_twse_int(find_field(row_by_field, "外", "買賣超"))
    investment_buy = parse_twse_int(find_field(row_by_field, "投信", "買進"))
    investment_sell = parse_twse_int(find_field(row_by_field, "投信", "賣出"))
    investment_net = parse_twse_int(find_field(row_by_field, "投信", "買賣超"))
    dealer_net = parse_twse_int(find_field(row_by_field, "自營商買賣超股數"))
    dealer_self_buy = parse_twse_int(find_field(row_by_field, "自營商", "買進", "自行"))
    dealer_self_sell = parse_twse_int(find_field(row_by_field, "自營商", "賣出", "自行"))
    dealer_self_net = parse_twse_int(find_field(row_by_field, "自營商", "買賣超", "自行"))
    dealer_hedge_buy = parse_twse_int(find_field(row_by_field, "自營商", "買進", "避險"))
    dealer_hedge_sell = parse_twse_int(find_field(row_by_field, "自營商", "賣出", "避險"))
    dealer_hedge_net = parse_twse_int(find_field(row_by_field, "自營商", "買賣超", "避險"))
    total_net = parse_twse_int(find_field(row_by_field, "三大法人", "買賣超"))

    shares = {
        "foreign_buy_shares": foreign_buy,
        "foreign_sell_shares": foreign_sell,
        "foreign_net_shares": foreign_net,
        "investment_trust_buy_shares": investment_buy,
        "investment_trust_sell_shares": investment_sell,
        "investment_trust_net_shares": investment_net,
        "dealer_net_shares": dealer_net,
        "dealer_self_buy_shares": dealer_self_buy,
        "dealer_self_sell_shares": dealer_self_sell,
        "dealer_self_net_shares": dealer_self_net,
        "dealer_hedge_buy_shares": dealer_hedge_buy,
        "dealer_hedge_sell_shares": dealer_hedge_sell,
        "dealer_hedge_net_shares": dealer_hedge_net,
        "total_net_shares": total_net,
    }
    record = {
        "date": trade_date.strftime("%Y-%m-%d"),
        "stock_id": stock_id,
        "stock_name": stock_name,
        **{key: str(value) for key, value in shares.items()},
    }
    for key, value in shares.items():
        record[key.replace("_shares", "_lots")] = shares_to_lots(value, unit_size)
    return record


def fetch_t86(
    trade_date: dt.date,
    select_type: str,
    stock_no: str | None,
    unit_size: int,
) -> list[dict[str, str]]:
    raw = request_bytes(
        "/fund/T86",
        {
            "response": "json",
            "date": trade_date.strftime("%Y%m%d"),
            "selectType": select_type,
        },
        referer="/zh/trading/foreign/t86.html",
    )
    payload = json.loads(raw.decode("utf-8-sig"))
    if payload.get("stat") != "OK":
        return []

    fields = payload.get("fields", [])
    records = [
        normalize_t86_row(trade_date, fields, row, unit_size)
        for row in payload.get("data", [])
        if not stock_no or row[0] == stock_no
    ]
    return records


def write_dict_csv(path: Path, columns: list[str], records: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)


def write_rows_csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(rows)


def run_mi_index(args: argparse.Namespace) -> None:
    if args.date:
        dates = [args.date]
    else:
        dates = list(date_range(args.start, args.end))

    records: list[dict[str, str]] = []
    for index, trade_date in enumerate(dates, start=1):
        if args.skip_weekends and trade_date.weekday() >= 5:
            continue
        daily_records = fetch_mi_index(trade_date, args.type)
        print(f"{trade_date}: {len(daily_records)} rows")
        records.extend(daily_records)
        if index < len(dates):
            time.sleep(args.sleep)

    output = args.output or Path(f"twse_mi_index_{dates[0]:%Y%m%d}_{dates[-1]:%Y%m%d}.csv")
    write_dict_csv(output, MI_INDEX_COLUMNS, records)
    print(f"saved: {output}")


def run_stock_day(args: argparse.Namespace) -> None:
    fields, rows = fetch_stock_day(args.stock_no, args.date)
    output = args.output or Path(f"twse_stock_day_{args.stock_no}_{args.date:%Y%m}.csv")
    write_rows_csv(output, fields, rows)
    print(f"{args.stock_no} {args.date:%Y-%m}: {len(rows)} rows")
    print(f"saved: {output}")


def run_t86(args: argparse.Namespace) -> None:
    if args.date:
        dates = [args.date]
    else:
        dates = list(date_range(args.start, args.end))

    records: list[dict[str, str]] = []
    for index, trade_date in enumerate(dates, start=1):
        if args.skip_weekends and trade_date.weekday() >= 5:
            continue
        daily_records = fetch_t86(trade_date, args.select_type, args.stock_no, args.unit_size)
        print(f"{trade_date}: {len(daily_records)} rows")
        records.extend(daily_records)
        if index < len(dates):
            time.sleep(args.sleep)

    output = args.output or Path(f"twse_t86_{args.stock_no or 'all'}_{dates[0]:%Y%m%d}_{dates[-1]:%Y%m%d}.csv")
    write_dict_csv(output, T86_COLUMNS, records)
    print(f"saved: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TWSE 股價資料爬蟲")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mi_index = subparsers.add_parser("mi-index", help="抓每日收盤行情，含所有上市股票")
    group = mi_index.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", type=parse_yyyymmdd, help="單日日期，格式 YYYYMMDD")
    group.add_argument("--start", type=parse_yyyymmdd, help="起始日期，格式 YYYYMMDD")
    mi_index.add_argument("--end", type=parse_yyyymmdd, help="結束日期，格式 YYYYMMDD")
    mi_index.add_argument("--type", default="ALLBUT0999", help="TWSE 分類，預設 ALLBUT0999")
    mi_index.add_argument("--sleep", type=float, default=3.0, help="連續抓取時每次間隔秒數")
    mi_index.add_argument("--output", "-o", type=Path, help="輸出 CSV 路徑")
    mi_index.add_argument("--skip-weekends", action="store_true", help="區間抓取時略過週末")
    mi_index.set_defaults(func=run_mi_index)

    stock_day = subparsers.add_parser("stock-day", help="抓單一上市股票單月日成交資訊")
    stock_day.add_argument("--stock-no", required=True, help="股票代號，例如 2330")
    stock_day.add_argument("--date", required=True, type=parse_yyyymmdd, help="月份內任一天，格式 YYYYMMDD")
    stock_day.add_argument("--output", "-o", type=Path, help="輸出 CSV 路徑")
    stock_day.set_defaults(func=run_stock_day)

    t86 = subparsers.add_parser("t86", help="抓三大法人買賣超日報，可指定單一股票/ETF")
    group = t86.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", type=parse_yyyymmdd, help="單日日期，格式 YYYYMMDD")
    group.add_argument("--start", type=parse_yyyymmdd, help="起始日期，格式 YYYYMMDD")
    t86.add_argument("--end", type=parse_yyyymmdd, help="結束日期，格式 YYYYMMDD")
    t86.add_argument("--stock-no", default="00981A", help="證券代號，預設 00981A")
    t86.add_argument("--select-type", default="ALLBUT0999", help="TWSE 分類，預設 ALLBUT0999")
    t86.add_argument("--unit-size", type=int, default=1000, help="每張股數，預設 1000，用來換算 lots")
    t86.add_argument("--sleep", type=float, default=3.0, help="連續抓取時每次間隔秒數")
    t86.add_argument("--output", "-o", type=Path, help="輸出 CSV 路徑")
    t86.add_argument("--skip-weekends", action="store_true", help="區間抓取時略過週末")
    t86.set_defaults(func=run_t86)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in {"mi-index", "t86"} and args.start and not args.end:
        parser.error("使用 --start 時也要提供 --end")
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
