#!/usr/bin/env python3
"""抓取 00981A 每日持股，並比較新增與移除股票。"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE_URL = "https://www.ezmoney.com.tw"
HOLDINGS_URL = "/ETF/Fund/AssetExcelNPOI?fundCode=49YTW"
DEFAULT_OUTPUT_DIR = Path("data")
SNAPSHOT_PATTERN = re.compile(r"00981A_holdings_(\d{8})\.xlsx")


def parse_roc_date(value: str) -> dt.date:
    match = re.search(r"(\d{2,4})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})", value)
    if not match:
        raise ValueError(f"無法解析日期：{value!r}")
    year, month, day = map(int, match.groups())
    if year < 1911:
        year += 1911
    return dt.date(year, month, day)


def fetch_holdings_xlsx() -> bytes:
    url = urljoin(BASE_URL, HOLDINGS_URL)
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    return response.content


def col_letter_to_index(col: str) -> int:
    result = 0
    for char in col.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def parse_xlsx_rows(raw: bytes) -> list[list[str]]:
    from io import BytesIO
    import xml.etree.ElementTree as ET

    workbook = zipfile.ZipFile(BytesIO(raw))
    shared_strings = []
    if "xl/sharedStrings.xml" in workbook.namelist():
        tree = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        for item in tree.findall(f".//{namespace}t"):
            shared_strings.append(item.text or "")

    sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows: list[list[str]] = []
    for row_elem in sheet.findall(f".//{namespace}row"):
        cells: dict[int, str] = {}
        max_index = -1
        for cell in row_elem.findall(f"{namespace}c"):
            ref = cell.get("r", "")
            match = re.match(r"([A-Z]+)\d+", ref)
            col_index = col_letter_to_index(match.group(1)) if match else 0
            max_index = max(max_index, col_index)
            value = ""
            cell_type = cell.get("t")
            if cell_type == "s":
                raw_index = cell.find(f"{namespace}v")
                if raw_index is not None and raw_index.text is not None:
                    value = shared_strings[int(raw_index.text)]
            elif cell_type == "inlineStr":
                inline = cell.find(f"{namespace}is/{namespace}t")
                if inline is not None and inline.text is not None:
                    value = inline.text
            else:
                value_elem = cell.find(f"{namespace}v")
                if value_elem is not None and value_elem.text is not None:
                    value = value_elem.text
            cells[col_index] = value.strip()
        row = [cells.get(i, "") for i in range(max_index + 1)]
        rows.append(row)
    return rows


def find_holdings_table(rows: list[list[str]]) -> tuple[list[str], list[dict[str, str]]]:
    header_row = None
    for index, row in enumerate(rows):
        if len(row) >= 4 and row[0] == "股票代號" and row[1] == "股票名稱":
            header_row = row
            data_start = index + 1
            break
    if header_row is None:
        raise RuntimeError("無法在 XLSX 中找到持股標題列")

    holdings: list[dict[str, str]] = []
    for row in rows[data_start:]:
        if not row or not row[0].strip():
            break
        if row[0].startswith("合計") or row[0].startswith("註"):
            break
        stock_id = row[0].strip()
        holdings.append(
            {
                "stock_id": stock_id,
                "stock_name": row[1].strip() if len(row) > 1 else "",
                "shares": row[2].strip() if len(row) > 2 else "",
                "weight": row[3].strip() if len(row) > 3 else "",
            }
        )
    if not holdings:
        raise RuntimeError("無法在 XLSX 中解析持股資料")
    return header_row, holdings


def parse_sheet_date(rows: list[list[str]]) -> dt.date:
    if rows and rows[0] and rows[0][0].startswith("資料日期"):
        return parse_roc_date(rows[0][0])
    return dt.date.today()


def write_snapshot(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def find_latest_snapshot(directory: Path, before: dt.date | None = None) -> Path | None:
    best_date: dt.date | None = None
    best_path: Path | None = None
    for path in sorted(directory.glob("00981A_holdings_*.xlsx")):
        match = SNAPSHOT_PATTERN.match(path.name)
        if not match:
            continue
        snapshot_date = dt.datetime.strptime(match.group(1), "%Y%m%d").date()
        if before and snapshot_date >= before:
            continue
        if best_date is None or snapshot_date > best_date:
            best_date = snapshot_date
            best_path = path
    return best_path


def find_latest_snapshot_csv(directory: Path, before: dt.date | None = None) -> Path | None:
    best_date: dt.date | None = None
    best_path: Path | None = None
    for path in sorted(directory.glob("00981A_holdings_*.csv")):
        match = re.search(r"00981A_holdings_(\d{8})\.csv", path.name)
        if not match:
            continue
        snapshot_date = dt.datetime.strptime(match.group(1), "%Y%m%d").date()
        if before and snapshot_date >= before:
            continue
        if best_date is None or snapshot_date > best_date:
            best_date = snapshot_date
            best_path = path
    return best_path


def load_xlsx_snapshot(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    _, holdings = find_holdings_table(parse_xlsx_rows(raw))
    return holdings


def load_csv_snapshot(path: Path) -> list[dict[str, str]]:
    holdings: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            holdings.append(row)
    return holdings


def compare_holdings(
    previous: list[dict[str, str]], current: list[dict[str, str]]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    prev_map = {row["stock_id"]: row for row in previous}
    curr_map = {row["stock_id"]: row for row in current}
    added = [curr_map[sid] for sid in sorted(curr_map) if sid not in prev_map]
    removed = [prev_map[sid] for sid in sorted(prev_map) if sid not in curr_map]
    return added, removed


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取 00981A 每日持股並比較新增/移除股票")
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="持股變動結果存放資料夾，預設 data",
    )
    parser.add_argument(
        "--prev-file",
        type=Path,
        help="指定先前持股 CSV 檔案，用來比較新增/移除股票",
    )
    args = parser.parse_args()

    raw = fetch_holdings_xlsx()
    rows = parse_xlsx_rows(raw)
    snapshot_date = parse_sheet_date(rows)
    _, current_holdings = find_holdings_table(rows)

    snapshot_dir = args.snapshot_dir
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    
    current_csv = snapshot_dir / f"00981A_holdings_{snapshot_date:%Y%m%d}.csv"
    write_csv(current_csv, ["stock_id", "stock_name", "shares", "weight"], current_holdings)
    print(f"已儲存當日持股：{current_csv}")

    prev_path = None
    if args.prev_file:
        prev_path = args.prev_file
    else:
        prev_path = find_latest_snapshot_csv(snapshot_dir, before=snapshot_date)

    if prev_path is None:
        print("沒有找到先前的持股快照，無法比較新增/移除股票。")
        print(f"目前共 {len(current_holdings)} 檔成分股。")
        sys.exit(0)

    previous_holdings = load_csv_snapshot(prev_path)
    added, removed = compare_holdings(previous_holdings, current_holdings)

    print(f"比對基準檔案：{prev_path.name}")
    print(f"目前成分股數量：{len(current_holdings)}，先前成分股數量：{len(previous_holdings)}")
    print(f"新增 {len(added)} 檔股票，移除 {len(removed)} 檔股票。\n")

    if added:
        print("新增股票：")
        for row in added:
            print(f"  {row['stock_id']} {row['stock_name']} {row['shares']} {row['weight']}")
    if removed:
        print("\n移除股票：")
        for row in removed:
            print(f"  {row['stock_id']} {row['stock_name']} {row['shares']} {row['weight']}")

    if added or removed:
        diff_csv = snapshot_dir / f"00981A_changes_{snapshot_date:%Y%m%d}.csv"
        diff_rows: list[dict[str, str]] = []
        for row in added:
            diff_rows.append({**row, "change_type": "added"})
        for row in removed:
            diff_rows.append({**row, "change_type": "removed"})
        write_csv(diff_csv, ["change_type", "stock_id", "stock_name", "shares", "weight"], diff_rows)
        print(f"已儲存變動結果：{diff_csv}")


if __name__ == "__main__":
    main()
