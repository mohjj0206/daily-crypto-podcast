"""方案 A 第一階段：本地自檢與 90 天歷史。不呼叫 Gemini。"""
import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import presentation

HISTORY_DIR = "history_db"
INSIGHTS_PATH = "learning_insights.json"
DATE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
MONEY_DECIMAL = re.compile(r"(?:US)?\$\s*-?\d[\d,]*\.\d+(?![TBM])")
CURRENCY_WORD = re.compile(r"台幣|新台幣|NT\$|\bTWD\b", re.I)
WRONG_HOST = re.compile(r"比特王|比特旺|比特網|以太貓|以太猫")
SYSTEM_LEAK = re.compile(r"抓不到|無法取得|資料缺失|數據缺失|連線失敗|API\s*異常")
KEYWORD = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,}")
STOPWORDS = {"今日", "市場", "比特", "幣看", "趨勢", "分析", "解析"}


def _today():
    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def _read_history(today):
    rows = []
    if not os.path.isdir(HISTORY_DIR):
        return rows
    for name in os.listdir(HISTORY_DIR):
        match = DATE_FILE.match(name)
        if not match:
            continue
        try:
            used = datetime.fromisoformat(match.group(1)).date()
        except ValueError:
            continue
        if (today - used).days > 14 or (today - used).days < 0:
            continue
        path = os.path.join(HISTORY_DIR, name)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        audit = data.get("quality_audit") if isinstance(data.get("quality_audit"), dict) else {}
        rows.append({
            "date": data.get("date") or match.group(1),
            "angle_id": data.get("angle_id") or "",
            "angle_title": data.get("angle_title") or "",
            "title": data.get("title") or "",
            "core_point": data.get("core_point") or "",
            "score": audit.get("score"),
            "warnings": list(audit.get("warnings") or []),
        })
    rows.sort(key=lambda row: row["date"])
    return rows


def _keywords(titles):
    found = []
    for title in titles:
        for token in KEYWORD.findall(title or ""):
            if token in STOPWORDS or token in found:
                continue
            found.append(token)
            if len(found) >= 12:
                return found
    return found


def prepare_learning_insights():
    """產片前讀近 14 日摘要，寫出今日要避開的角度與上一集警告。"""
    today = _today()
    rows = _read_history(today)
    recent = [row for row in rows if (_today_delta(today, row["date"])) < 7]
    avoid_ids = []
    avoid_angles = []
    for row in recent:
        if row["angle_id"] and row["angle_id"] not in avoid_ids:
            avoid_ids.append(row["angle_id"])
        label = row["angle_title"] or row["angle_id"]
        if label and label not in avoid_angles:
            avoid_angles.append(label)
    previous = rows[-1]["warnings"] if rows else []
    insights = {
        "as_of": today.isoformat(),
        "avoid_angle_ids": avoid_ids,
        "avoid_angles": avoid_angles,
        "recent_title_keywords": _keywords([row["title"] for row in recent]),
        "previous_warnings": previous,
        "history_days": len(rows),
    }
    with open(INSIGHTS_PATH, "w", encoding="utf-8") as handle:
        json.dump(insights, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        f"[daily_learner] 近 {len(rows)} 天歷史，避開 {len(avoid_ids)} 個角度，"
        f"上一集警告 {len(previous)} 則。未呼叫 API。"
    )
    return insights


def _today_delta(today, iso_date):
    try:
        return (today - datetime.fromisoformat(iso_date).date()).days
    except ValueError:
        return 99


def _plain_length(script):
    text = re.sub(r"\[(?:SLIDE_\d+|excited|serious|analytical|debate)\]", "", script or "")
    text = re.sub(r"^(?:男|女)[：:]\s*", "", text, flags=re.M)
    return len(re.sub(r"\s+", "", text))


def _subtitle_lines(path):
    if not path or not os.path.exists(path):
        return None
    lines = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            if not raw.startswith("Dialogue:"):
                continue
            text = raw.split(",", 9)[-1].strip()
            text = re.sub(r"\{[^}]*\}", "", text)
            if text:
                lines.append(text)
    return lines


def _split_numbers(lines):
    hits = []
    for previous, current in zip(lines, lines[1:]):
        if re.search(r"\d,$", previous) and re.match(r"\d", current):
            hits.append(f"{previous} / {current}")
        elif re.search(r"\$\d[\d,]*$", previous) and re.match(r"\d", current):
            hits.append(f"{previous} / {current}")
    return hits


def audit_episode(script, subtitle_path, is_test):
    """只檢查口播與字幕。公開帳本頁與 dca_portfolio.json 的台幣對照不納入。"""
    warnings = []
    text = script or ""
    ledger_name = os.path.basename(str(subtitle_path or ""))
    if ledger_name in {"index.html", "dca_portfolio.json"}:
        subtitle_lines = []
    else:
        subtitle_lines = _subtitle_lines(subtitle_path)
    combined = text if subtitle_lines is None else text + "\n" + "\n".join(subtitle_lines)
    if CURRENCY_WORD.search(combined):
        warnings.append("出現台幣或 TWD")
    if SYSTEM_LEAK.search(combined):
        warnings.append("系統露餡：提到抓不到、無法取得、資料缺失或連線失敗")
    if MONEY_DECIMAL.search(combined):
        warnings.append("美元金額仍帶小數")
    for term in presentation.EXCLUDED_PLATFORMS:
        if term.upper() in {"AI", "HODL", "HODLER"}:
            continue
        if re.search(re.escape(term), combined, flags=re.I):
            warnings.append(f"禁忌詞：{term}")
    if re.search(r"(?<![A-Za-z])AI(?![A-Za-z])", combined):
        warnings.append("禁忌詞：AI")
    if re.search(r"\bHODL(?:er)?\b", combined, flags=re.I):
        warnings.append("禁忌詞：HODL")
    if WRONG_HOST.search(combined):
        warnings.append("角色名不是比特汪或以太喵")
    if subtitle_lines is None:
        warnings.append("找不到字幕，無法檢查斷行")
    else:
        splits = _split_numbers(subtitle_lines)
        if splits:
            warnings.append("字幕切斷數字：" + splits[0])
    length = _plain_length(text)
    minimum = 80 if is_test else 5200
    if length < minimum:
        warnings.append(f"字數 {length}，低於 {minimum}")
    score = 100
    for warning in warnings:
        if warning.startswith("出現台幣"):
            score -= 25
        elif warning.startswith("美元金額") or warning.startswith("系統露餡"):
            score -= 20
        elif warning.startswith("字數"):
            score -= 20
        elif warning.startswith("找不到字幕"):
            score -= 5
        else:
            score -= 15
    score = max(0, min(100, score))
    audit = {"score": score, "warnings": warnings, "char_count": length}
    print(f"[daily_learner] 品質自檢 {score}/100" + (f"：{'；'.join(warnings)}" if warnings else ""))
    return audit


def _snapshot(mdata):
    btc = mdata.get("btc_metrics") or {}
    mvrv = mdata.get("mvrv") or {}

    def money(value):
        if value is None or value == "":
            return None
        return presentation.round_usd(value)

    return {
        "btc_usd": money(mdata.get("spot_prices", {}).get("BTC")),
        "eth_usd": money(mdata.get("spot_prices", {}).get("ETH")),
        "btc_change_pct": None if mdata.get("btc_pct") is None else round(float(mdata["btc_pct"]), 2),
        "eth_change_pct": None if mdata.get("eth_pct") is None else round(float(mdata["eth_pct"]), 2),
        "fear_greed": mdata.get("fng_num"),
        "mvrv": mvrv.get("value"),
        "support_usd": money(btc.get("support")),
        "resistance_usd": money(btc.get("resistance")),
        "btc_rsi": mdata.get("btc_rsi"),
    }


def archive_episode(is_test, upload_id, mdata, sdata, audit):
    """只有正式模式且主片上傳成功才寫 history_db，並刪除超過 90 天的日檔。"""
    if is_test:
        print("[daily_learner] TEST_MODE，不寫入 history_db。")
        return None
    upload_text = str(upload_id or "")
    if not upload_text or upload_text.startswith("UPLOAD") or upload_text.startswith("TEST"):
        print("[daily_learner] 主片未上傳成功，不寫入 history_db。")
        return None
    today = _today()
    os.makedirs(HISTORY_DIR, exist_ok=True)
    record = {
        "date": today.isoformat(),
        "angle_id": sdata.get("angle_id") or "",
        "angle_title": sdata.get("angle_title") or "",
        "title": sdata.get("ai_title") or "",
        "core_point": sdata.get("angle_title") or sdata.get("ai_title") or "",
        "market_snapshot": _snapshot(mdata),
        "quality_audit": audit,
    }
    path = os.path.join(HISTORY_DIR, f"{today.isoformat()}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    cutoff = today - timedelta(days=90)
    for name in os.listdir(HISTORY_DIR):
        match = DATE_FILE.match(name)
        if not match:
            continue
        try:
            used = datetime.fromisoformat(match.group(1)).date()
        except ValueError:
            continue
        if used < cutoff:
            os.remove(os.path.join(HISTORY_DIR, name))
            print(f"[daily_learner] 已刪除超過 90 天的 {name}")
    print(f"[daily_learner] 已寫入 {path}")
    return path
