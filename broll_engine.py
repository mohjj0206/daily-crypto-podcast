"""Pixabay 1080p 實景動態影片（B-Roll）穿插引擎。

重點原則：
- 讀取環境變數 PIXABAY_API_KEY；無金鑰、API 錯誤或逾時一律靜默回傳 None，
  由 media_engine 退回原本的動態深色底圖，不影響主流程。
- 支援主片橫式（16:9）與 Shorts 直式（9:16）1080p MP4 下載與快取。
- 同一集內輪換避免重複；查無結果時自動退回金融／科技備援關鍵字池。
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request

# ── 常數 ──────────────────────────────────────────────
HORIZONTAL = "horizontal"
VERTICAL = "vertical"

_ORIENTATION_WH = {
    HORIZONTAL: (1920, 1080),
    VERTICAL: (1080, 1920),
}

# 內建高質感英文金融／科技關鍵字池（Pixabay 命中率最高）。
# 當傳入關鍵字為空或含中文字元時，依集內輪替序位取詞保底，確保每頁都有底片。
ENGLISH_KEYWORD_POOL = [
    "stock market chart",
    "cryptocurrency bitcoin",
    "server room data center",
    "modern city night",
    "financial trading screen",
    "digital network technology",
]

# 查無結果時的金融／科技備援關鍵字池（英文，Pixabay 命中率最高）。
FALLBACK_KEYWORD_POOL = [
    "stock market",
    "cryptocurrency",
    "data center",
    "abstract technology",
    "bitcoin",
    "trading screen",
    "server room",
    "global finance",
]

# 每個 API 請求的逾時秒數與重試次數。
_REQUEST_TIMEOUT = 12
_MAX_RETRIES = 2

# 同集輪換狀態：記錄每個版型已用過的清單，避免重複。
_episode_used = {HORIZONTAL: [], VERTICAL: []}

# 英文保底關鍵字輪替序位（每呼叫一次取下一個，讓每頁底片畫面有變化）。
_keyword_rotation = {"index": 0}

# 最近一次 API 查詢失敗原因（供日誌印出）。
_LAST_QUERY_ERROR = ""

# 偵測中文字元（含 CJK 統一表意文字）。
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def reset_episode_rotation():
    """每次開新的一集前呼叫，清除同集輪換紀錄。"""
    _episode_used[HORIZONTAL] = []
    _episode_used[VERTICAL] = []
    _keyword_rotation["index"] = 0


def _next_fallback_keyword():
    """依輪替序位從內建英文池取下一個保底關鍵字。"""
    pool = ENGLISH_KEYWORD_POOL
    keyword = pool[_keyword_rotation["index"] % len(pool)]
    _keyword_rotation["index"] += 1
    return keyword


def _api_key():
    return (os.environ.get("PIXABAY_API_KEY") or "").strip()


def _cache_dir(cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _orientation(value):
    return VERTICAL if str(value).lower() in ("vertical", "9:16", "portrait") else HORIZONTAL


def _pick_video_url(hit):
    """從 Pixabay 一筆 hit 中挑出解析度足夠的 MP4 連結，優先 large。"""
    videos = (hit or {}).get("videos") or {}
    for key in ("large", "medium", "small", "tiny"):
        node = videos.get(key) or {}
        url = node.get("url")
        if url:
            return url
    return None


def _query_pixabay(keyword, orientation):
    """向 Pixabay 查詢，回傳 hits 清單；任何錯誤回傳空清單並記錄失敗原因。"""
    global _LAST_QUERY_ERROR
    width, height = _ORIENTATION_WH[orientation]
    params = {
        "key": _api_key(),
        "q": keyword,
        "video_type": "film",
        "orientation": orientation,
        "category": "business",
        "safesearch": "true",
        "per_page": "20",
    }
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(params)
    last_error = ""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "daily-crypto-podcast/1.0"})
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            hits = payload.get("hits") or []
            _LAST_QUERY_ERROR = ""
            return hits
        except Exception as exc:  # noqa: BLE001 - 任何錯誤都退回空清單
            last_error = str(exc) or exc.__class__.__name__
            if attempt >= _MAX_RETRIES:
                _LAST_QUERY_ERROR = last_error
                return []
            time.sleep(0.6 * (attempt + 1))
    _LAST_QUERY_ERROR = last_error
    return []


def _download(url, dest_path):
    """下載 MP4 到 dest_path，失敗回傳 False。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "daily-crypto-podcast/1.0"})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT * 2) as resp:
            data = resp.read()
        if not data:
            return False
        with open(dest_path, "wb") as handle:
            handle.write(data)
        return True
    except Exception:
        return False


def _clean_keywords(keywords):
    """把傳入的關鍵字（字串或清單）整理成去重、非空且「純英文」的查詢字串清單。

    含中文字元的項目一律丟棄（Pixabay 對中文查詢命中率極低），確保保底機制能接手。
    """
    if not keywords:
        return []
    if isinstance(keywords, str):
        candidates = [part for part in keywords.replace("，", ",").split(",")]
    else:
        candidates = list(keywords)
    cleaned = []
    for item in candidates:
        text = str(item).strip()
        if not text or text in cleaned:
            continue
        if _CJK_RE.search(text):
            continue
        cleaned.append(text)
    return cleaned


def fetch_pixabay_broll(keywords, orientation=HORIZONTAL, cache_dir="assets/broll_cache"):
    """取得一支 1080p 實景動態影片的本地快取路徑。

    參數：
        keywords: 英文關鍵字，可為字串（以逗號分隔）或字串清單。若為空或含中文，
                  自動改採內建英文高質感關鍵字池輪替保底。
        orientation: "horizontal"（16:9）或 "vertical"（9:16）。
        cache_dir: 快取資料夾。

    回傳：
        成功時回傳本地 MP4 檔案路徑並印出成功日誌；無金鑰、查無結果、API 錯誤
        或逾時一律印出對應日誌後回傳 None。
    """
    if not _api_key():
        print("[Pixabay B-Roll] 警告：未偵測到 PIXABAY_API_KEY 環境變數")
        return None

    orient = _orientation(orientation)
    cache_path = _cache_dir(cache_dir)
    used = _episode_used[orient]

    # 組出查詢順序：先使用者英文關鍵字，再輪替保底池，最後備援池，去重。
    queries = _clean_keywords(keywords)
    fallback_keyword = _next_fallback_keyword()
    if fallback_keyword not in queries:
        queries.append(fallback_keyword)
    for fallback in FALLBACK_KEYWORD_POOL:
        if fallback not in queries:
            queries.append(fallback)

    last_reason = "查無結果"
    for query in queries:
        hits = _query_pixabay(query, orient)
        if not hits:
            if _LAST_QUERY_ERROR:
                last_reason = _LAST_QUERY_ERROR
            continue
        # 先挑同集還沒用過的，再退回允許重複。
        ordered_hits = sorted(
            hits,
            key=lambda hit: (hit.get("id") in used),
        )
        for hit in ordered_hits:
            video_id = hit.get("id")
            video_url = _pick_video_url(hit)
            if not video_url:
                continue
            ext = os.path.splitext(video_url.split("?")[0])[1] or ".mp4"
            dest = os.path.join(cache_path, f"{orient}_{video_id}{ext}")
            if os.path.exists(dest):
                local = dest
            else:
                if not _download(video_url, dest):
                    last_reason = "下載失敗"
                    continue
                local = dest
            used.append(video_id)
            print(
                f"[Pixabay B-Roll] 成功套用實景動態影片：{query} -> "
                f"{os.path.basename(local)}"
            )
            return local

    print(f"[Pixabay B-Roll] 查詢失敗 ({last_reason})，退回預設暗底")
    return None