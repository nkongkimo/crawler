#!/usr/bin/env python3
"""抓取 00981A 每日持股，並比較新增與移除股票。

功能：
  1. 從統一投信官網下載 00981A 當日持股 Excel 檔
  2. 解析 Excel，提取股票代號、名稱、股數、權重
  3. 儲存為 CSV 格式
  4. 自動比較與前一天的持股差異，找出新增/移除股票
  5. 輸出變動清單

使用方式：
  python3 etf_00981A_changes.py
  python3 etf_00981A_changes.py --prev-file data/00981A_holdings_20260514.csv
"""

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

# 統一投信官網
BASE_URL = "https://www.ezmoney.com.tw"
# 00981A ETF 持股 Excel 下載連結
HOLDINGS_URL = "/ETF/Fund/AssetExcelNPOI?fundCode=49YTW"
# 預設輸出資料夾
DEFAULT_OUTPUT_DIR = Path("data")
# 用於匹配 XLSX 快照檔案名稱的正則模式
SNAPSHOT_PATTERN = re.compile(r"00981A_holdings_(\d{8})\.xlsx")


def parse_roc_date(value: str) -> dt.date:
    """將民國年月日格式（如 115/05/15）轉換為 Python datetime.date 物件。
    
    Args:
        value: 民國年月日字串，可支援 /, -, . 等分隔符
        
    Returns:
        對應的 datetime.date 物件
        
    Raises:
        ValueError: 若無法解析日期格式
    """
    match = re.search(r"(\d{2,4})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})", value)
    if not match:
        raise ValueError(f"無法解析日期：{value!r}")
    year, month, day = map(int, match.groups())
    # 民國年份轉西元：民國 115 = 2026（115 + 1911）
    if year < 1911:
        year += 1911
    return dt.date(year, month, day)


def fetch_holdings_xlsx() -> bytes:
    """從統一投信網站下載當日 00981A 持股 Excel 檔。
    
    Returns:
        Excel 檔的二進制內容
        
    Raises:
        requests.HTTPError: 若下載失敗
    """
    url = urljoin(BASE_URL, HOLDINGS_URL)
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    return response.content


def col_letter_to_index(col: str) -> int:
    """將 Excel 欄位字母（A, B, C...）轉換為 0-indexed 的數字。
    
    Args:
        col: 欄位字母，如 'A', 'B', 'AA' 等
        
    Returns:
        對應的欄位索引（0-indexed）
    """
    result = 0
    for char in col.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def parse_xlsx_rows(raw: bytes) -> list[list[str]]:
    """解析 XLSX 檔案，將其轉換為二維列表（逐行逐列的字串）。
    
    本函數直接處理 XLSX 的 XML 結構，無需額外套件。
    
    Args:
        raw: XLSX 檔的二進制內容
        
    Returns:
        二維列表，每一列代表 Excel 的一列資料
    """

    workbook = zipfile.ZipFile(BytesIO(raw))
    shared_strings = []
    if "xl/sharedStrings.xml" in workbook.namelist():
        tree = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        for item in tree.findall(f".//{namespace}t"):
            shared_strings.append(item.text or "")

def parse_xlsx_rows(raw: bytes) -> list[list[str]]:
    """解析 XLSX 檔案，將其轉換為二維列表（逐行逐列的字串）。
    
    本函數直接處理 XLSX 的 XML 結構，無需額外套件。
    
    Args:
        raw: XLSX 檔的二進制內容
        
    Returns:
        二維列表，每一列代表 Excel 的一列資料
    """
    from io import BytesIO
    import xml.etree.ElementTree as ET

    # 開啟 XLSX 為 ZIP 檔案，提取 XML 內容
    workbook = zipfile.ZipFile(BytesIO(raw))
    
    # 讀取共享字串表（存放文字內容）
    shared_strings = []
    if "xl/sharedStrings.xml" in workbook.namelist():
        tree = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        for item in tree.findall(f".//{namespace}t"):
            shared_strings.append(item.text or "")

    # 讀取 Sheet1 工作表
    sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows: list[list[str]] = []
    
    # 逐列解析工作表
    for row_elem in sheet.findall(f".//{namespace}row"):
        cells: dict[int, str] = {}
        max_index = -1
        
        # 逐儲存格解析
        for cell in row_elem.findall(f"{namespace}c"):
            # 取得儲存格位置（如 A1, B2 等）
            ref = cell.get("r", "")
            match = re.match(r"([A-Z]+)\d+", ref)
            col_index = col_letter_to_index(match.group(1)) if match else 0
            max_index = max(max_index, col_index)
            
            value = ""
            cell_type = cell.get("t")
            
            # 根據儲存格類型解析內容
            if cell_type == "s":
                # 類型 "s" = 共享字串參考
                raw_index = cell.find(f"{namespace}v")
                if raw_index is not None and raw_index.text is not None:
                    value = shared_strings[int(raw_index.text)]
            elif cell_type == "inlineStr":
                # 類型 "inlineStr" = 內嵌字串
                inline = cell.find(f"{namespace}is/{namespace}t")
                if inline is not None and inline.text is not None:
                    value = inline.text
            else:
                # 其他類型（數值、日期等）直接讀取
                value_elem = cell.find(f"{namespace}v")
                if value_elem is not None and value_elem.text is not None:
                    value = value_elem.text
            cells[col_index] = value.strip()
        
        # 按欄位索引順序組成一列資料
        row = [cells.get(i, "") for i in range(max_index + 1)]
        rows.append(row)
    return rows


def find_holdings_table(rows: list[list[str]]) -> tuple[list[str], list[dict[str, str]]]:
    """從解析後的 XLSX 行列資料中找到持股表，並提取成分股清單。
    
    持股表的標題列為：股票代號 | 股票名稱 | 股數 | 持股權重
    
    Args:
        rows: 二維列表，由 parse_xlsx_rows() 產生
        
    Returns:
        (標題列, 成分股清單)，其中成分股清單為字典列表
        每個字典包含 stock_id, stock_name, shares, weight 鍵值
        
    Raises:
        RuntimeError: 若無法找到持股標題列或解析失敗
    """
    # 尋找標題列：股票代號 | 股票名稱 | ...
    header_row = None
    for index, row in enumerate(rows):
        if len(row) >= 4 and row[0] == "股票代號" and row[1] == "股票名稱":
            header_row = row
            data_start = index + 1
            break
    if header_row is None:
        raise RuntimeError("無法在 XLSX 中找到持股標題列")

    # 逐列提取成分股資料
    holdings: list[dict[str, str]] = []
    for row in rows[data_start:]:
        # 空列表示資料結束
        if not row or not row[0].strip():
            break
        # 遇到「合計」或「註」開頭的列，停止解析
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
    """從 XLSX 首列提取資料日期。
    
    通常首列為「資料日期：115/05/15」格式
    
    Args:
        rows: 二維列表，由 parse_xlsx_rows() 產生
        
    Returns:
        資料日期，若無法解析則回傳今日日期
    """
    if rows and rows[0] and rows[0][0].startswith("資料日期"):
        return parse_roc_date(rows[0][0])
    return dt.date.today()


def write_snapshot(path: Path, raw: bytes) -> None:
    """將原始 XLSX 檔案寫入磁碟（已廢棄，保留用於相容性）。
    
    Args:
        path: 儲存路徑
        raw: XLSX 二進制內容
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    """將成分股資料寫成 CSV 檔案。
    
    Args:
        path: CSV 檔案輸出路徑
        columns: CSV 欄位標題列表
        rows: 資料列表，每個元素為字典
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def find_latest_snapshot(directory: Path, before: dt.date | None = None) -> Path | None:
    """尋找指定目錄中最新的 XLSX 快照檔案（已廢棄）。
    
    Args:
        directory: 搜尋的目錄
        before: 若指定，只搜尋此日期前的快照
        
    Returns:
        最新快照檔案路徑，若無則回傳 None
    """
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
    """尋找指定目錄中最新的 CSV 快照檔案。
    
    Args:
        directory: 搜尋的目錄
        before: 若指定，只搜尋此日期前的快照
        
    Returns:
        最新快照檔案路徑，若無則回傳 None
    """
    best_date: dt.date | None = None
    best_path: Path | None = None
    for path in sorted(directory.glob("00981A_holdings_*.csv")):
        # 從檔案名提取日期
        match = re.search(r"00981A_holdings_(\d{8})\.csv", path.name)
        if not match:
            continue
        snapshot_date = dt.datetime.strptime(match.group(1), "%Y%m%d").date()
        # 若有 before 限制，跳過不符合的檔案
        if before and snapshot_date >= before:
            continue
        if best_date is None or snapshot_date > best_date:
            best_date = snapshot_date
            best_path = path
    return best_path


def load_xlsx_snapshot(path: Path) -> list[dict[str, str]]:
    """從 XLSX 快照檔案載入成分股資料（已廢棄）。
    
    Args:
        path: XLSX 檔案路徑
        
    Returns:
        成分股清單（字典列表）
    """
    raw = path.read_bytes()
    _, holdings = find_holdings_table(parse_xlsx_rows(raw))
    return holdings


def load_csv_snapshot(path: Path) -> list[dict[str, str]]:
    """從 CSV 快照檔案載入成分股資料。
    
    Args:
        path: CSV 檔案路徑
        
    Returns:
        成分股清單（字典列表）
    """
    holdings: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            holdings.append(row)
    return holdings


def compare_holdings(
    previous: list[dict[str, str]], current: list[dict[str, str]]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """比較兩個成分股清單，找出新增與移除的股票。
    
    Args:
        previous: 先前的成分股清單
        current: 目前的成分股清單
        
    Returns:
        (新增股票清單, 移除股票清單)
    """
    # 建立字典以快速查詢：股票代號 -> 完整股票資訊
    prev_map = {row["stock_id"]: row for row in previous}
    curr_map = {row["stock_id"]: row for row in current}
    
    # 新增 = 目前有但先前沒有的股票
    added = [curr_map[sid] for sid in sorted(curr_map) if sid not in prev_map]
    # 移除 = 先前有但目前沒有的股票
    removed = [prev_map[sid] for sid in sorted(prev_map) if sid not in curr_map]
    return added, removed


def main() -> None:
    """主程式流程：
    1. 解析命令列參數
    2. 下載當日持股 Excel
    3. 解析並儲存為 CSV
    4. 若有前一天快照，比較新增/移除股票
    5. 輸出比較結果
    """
    # === 第 1 步：解析命令列參數 ===
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

    # === 第 2 步：下載當日持股 Excel ===
    raw = fetch_holdings_xlsx()
    
    # === 第 3 步：解析 Excel 檔案 ===
    rows = parse_xlsx_rows(raw)
    snapshot_date = parse_sheet_date(rows)  # 從 Excel 首列提取資料日期
    _, current_holdings = find_holdings_table(rows)  # 提取成分股表

    # === 第 4 步：儲存當日持股為 CSV ===
    snapshot_dir = args.snapshot_dir
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    
    current_csv = snapshot_dir / f"00981A_holdings_{snapshot_date:%Y%m%d}.csv"
    write_csv(current_csv, ["stock_id", "stock_name", "shares", "weight"], current_holdings)
    print(f"已儲存當日持股：{current_csv}")

    # === 第 5 步：尋找前一天的快照進行比較 ===
    prev_path = None
    if args.prev_file:
        # 若使用者指定了 --prev-file，使用該檔案
        prev_path = args.prev_file
    else:
        # 否則自動尋找目錄中最新的（比今日早的）快照
        prev_path = find_latest_snapshot_csv(snapshot_dir, before=snapshot_date)

    # 若無前一天快照，無法進行比較
    if prev_path is None:
        print("沒有找到先前的持股快照，無法比較新增/移除股票。")
        print(f"目前共 {len(current_holdings)} 檔成分股。")
        sys.exit(0)

    # === 第 6 步：載入前一天快照並比較 ===
    previous_holdings = load_csv_snapshot(prev_path)
    added, removed = compare_holdings(previous_holdings, current_holdings)

    # === 第 7 步：輸出比較結果 ===
    print(f"比對基準檔案：{prev_path.name}")
    print(f"目前成分股數量：{len(current_holdings)}，先前成分股數量：{len(previous_holdings)}")
    print(f"新增 {len(added)} 檔股票，移除 {len(removed)} 檔股票。\n")

    # 列印新增股票
    if added:
        print("新增股票：")
        for row in added:
            print(f"  {row['stock_id']} {row['stock_name']} {row['shares']} {row['weight']}")
    
    # 列印移除股票
    if removed:
        print("\n移除股票：")
        for row in removed:
            print(f"  {row['stock_id']} {row['stock_name']} {row['shares']} {row['weight']}")

    # === 第 8 步：儲存變動清單為 CSV ===
    if added or removed:
        diff_csv = snapshot_dir / f"00981A_changes_{snapshot_date:%Y%m%d}.csv"
        diff_rows: list[dict[str, str]] = []
        
        # 將新增股票標記為 "added"
        for row in added:
            diff_rows.append({**row, "change_type": "added"})
        
        # 將移除股票標記為 "removed"
        for row in removed:
            diff_rows.append({**row, "change_type": "removed"})
        
        write_csv(diff_csv, ["change_type", "stock_id", "stock_name", "shares", "weight"], diff_rows)
        print(f"已儲存變動結果：{diff_csv}")


if __name__ == "__main__":
    main()
