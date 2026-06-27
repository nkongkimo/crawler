"""
爬取 ezmoney.com.tw ETF 基金投資組合
回傳欄位：股票代號、股票名稱、股數、權重(%)

用法：
    uv run python crawler/ezmoney_etf_portfolio.py 49YTW
    uv run python crawler/ezmoney_etf_portfolio.py 49YTW --output portfolio.csv
"""

import argparse       # 解析命令列參數
import csv            # 讀寫 CSV 檔案
import html           # 解碼 HTML 實體（如 &amp; → &）
import json           # 解析 JSON 字串
import sys            # 存取 sys.exit 和 sys.stderr
from dataclasses import dataclass  # 用來定義純資料類別
from typing import Optional        # 型別提示（Optional 在此未直接使用，保留供擴充）

import requests              # 發送 HTTP 請求
from bs4 import BeautifulSoup  # 解析 HTML DOM

# ezmoney ETF 基金資訊頁面的基底 URL
BASE_URL = "https://www.ezmoney.com.tw/ETF/Fund/Info"

# 模擬瀏覽器的請求標頭，避免被網站擋掉
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",  # 優先回應繁體中文
}


@dataclass
class Holding:
    """代表單一成分股的資料結構。"""
    stock_code: str    # 股票代號，例如 "2330"
    stock_name: str    # 股票名稱，例如 "台積電"
    shares: float      # 持有股數
    weight_pct: float  # 佔基金淨值的權重（百分比）


def fetch_portfolio(fund_code: str) -> list[Holding]:
    """抓取指定基金代碼的投資組合（股票成分）。"""
    session = requests.Session()          # 建立 Session 以共用連線與 headers
    session.headers.update(HEADERS)       # 套用瀏覽器偽裝標頭

    # 發送 GET 請求，帶入 fundCode 查詢參數，逾時 20 秒
    resp = session.get(BASE_URL, params={"fundCode": fund_code}, timeout=20)
    resp.raise_for_status()  # 若 HTTP 狀態碼非 2xx 則拋出例外

    # 用 lxml 解析 HTML，速度較快且容錯性高
    soup = BeautifulSoup(resp.text, "lxml")
    # 找到 id="DataAsset" 的 div，該元素的 data-content 屬性存有 JSON 資料
    data_div = soup.find("div", id="DataAsset")
    if not data_div:
        # 找不到時代表基金代碼有誤或頁面結構改變
        raise ValueError(
            f"找不到 DataAsset，請確認基金代碼 {fund_code!r} 是否正確"
        )

    # data-content 是 HTML 編碼的 JSON 字串，先 unescape 再解析
    raw_json = html.unescape(data_div["data-content"])
    asset_list: list[dict] = json.loads(raw_json)  # 轉成 Python list[dict]

    holdings: list[Holding] = []
    for asset in asset_list:
        # 只處理資產類型為「股票」的區塊，跳過債券、現金等
        if asset.get("AssetName") != "股票":
            continue
        details = asset.get("Details") or []  # 取得該類別下的個股明細列表
        for row in details:
            holdings.append(
                Holding(
                    stock_code=row["DetailCode"].strip(),  # 去除前後空白
                    stock_name=row["DetailName"].strip(),  # 去除前後空白
                    shares=row["Share"],       # 持有股數（浮點數）
                    weight_pct=row["NavRate"], # 淨值佔比（浮點數，單位 %）
                )
            )
        break  # 只取第一個「股票」資產類別，避免重複讀入

    # 依權重由高到低排序，方便閱讀
    holdings.sort(key=lambda h: h.weight_pct, reverse=True)
    return holdings


def print_table(holdings: list[Holding]) -> None:
    # 印出欄位標頭，左對齊代號與名稱、右對齊數字欄位
    header = f"{'股票代號':<10} {'股票名稱':<16} {'股數':>15} {'權重(%)':>8}"
    print(header)
    print("-" * len(header))  # 印出與標頭等長的分隔線
    for h in holdings:
        print(
            f"{h.stock_code:<10} {h.stock_name:<16} "
            f"{h.shares:>15,.0f} {h.weight_pct:>8.2f}%"  # 股數加千分位、權重保留兩位小數
        )


def save_csv(holdings: list[Holding], path: str) -> None:
    # 以 utf-8-sig 編碼儲存，讓 Excel 開啟時能正確顯示中文（BOM 標記）
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["股票代號", "股票名稱", "股數", "權重(%)"])  # 寫入欄位標題列
        for h in holdings:
            writer.writerow(
                [h.stock_code, h.stock_name, h.shares, h.weight_pct]
            )
    print(f"已儲存至 {path}")


def main() -> None:
    # 建立命令列解析器
    parser = argparse.ArgumentParser(description="ezmoney ETF 投資組合爬蟲")
    parser.add_argument("fund_code", help="基金代碼，例如 49YTW")             # 必要位置參數
    parser.add_argument("--output", "-o", help="輸出 CSV 路徑（選填）")        # 選填的輸出路徑
    args = parser.parse_args()  # 解析使用者輸入的參數

    holdings = fetch_portfolio(args.fund_code)  # 爬取成分股資料

    if not holdings:
        # 沒有任何成分股時提示錯誤並以非零代碼退出
        print(f"沒有找到 {args.fund_code} 的投資組合資料", file=sys.stderr)
        sys.exit(1)

    # 印出摘要資訊與成分股表格
    print(f"\n基金代碼：{args.fund_code}　共 {len(holdings)} 檔成分股\n")
    print_table(holdings)

    # 若使用者有指定 --output，則另存為 CSV
    if args.output:
        save_csv(holdings, args.output)


if __name__ == "__main__":
    main()  # 直接執行此檔案時的進入點
