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
import subprocess
import tempfile

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
_episode_used_ids = set()
_episode_manifest = []

# 英文保底關鍵字輪替序位（每呼叫一次取下一個，讓每頁底片畫面有變化）。
_keyword_rotation = {"index": 0}

# 使用者關鍵字起始輪替序位（每呼叫一次順移，避免每頁都用同一組詞命中同一支底片）。
_query_rotation = {"index": 0}

# 最近一次 API 查詢失敗原因（供日誌印出）。
_LAST_QUERY_ERROR = ""

# 本集 B-Roll 使用統計，區分主片橫式與 Shorts 直式。
_episode_stats = {
    HORIZONTAL: {"requested": 0, "success": 0, "failed": 0, "missing_key": 0},
    VERTICAL: {"requested": 0, "success": 0, "failed": 0, "missing_key": 0},
}

# 偵測中文字元（含 CJK 統一表意文字）。
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def reset_episode_rotation():
    """每次開新的一集前呼叫，清除同集輪換紀錄。"""
    _episode_used[HORIZONTAL] = []
    _episode_used[VERTICAL] = []
    _episode_used_ids.clear()
    _episode_manifest.clear()
    _keyword_rotation["index"] = 0
    _query_rotation["index"] = 0
    for stats in _episode_stats.values():
        for key in stats:
            stats[key] = 0


def get_episode_stats():
    """回傳本集 Pixabay 使用統計，供製片流程檢查實際覆蓋率。"""
    return {orientation: dict(stats) for orientation, stats in _episode_stats.items()}



def get_episode_manifest():
    """回傳本集實際採用素材的來源與授權稽核資料。"""
    return [dict(item) for item in _episode_manifest]


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
        # 不鎖定 business 類別；新聞事件、資安、挖礦與科技素材常被歸在其他類別。
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
    orient = _orientation(orientation)
    _episode_stats[orient]["requested"] += 1
    if not _api_key():
        _episode_stats[orient]["missing_key"] += 1
        print("[Pixabay B-Roll] 警告：未偵測到 PIXABAY_API_KEY 環境變數")
        return None

    cache_path = _cache_dir(cache_dir)
    used = _episode_used[orient]

    # 每頁最多查 4 組，避免每張投影片為了找素材連續打十多次 API。
    # 優先使用本頁播報衍生的前 2 組具體關鍵字，再用輪替通用詞保底。
    queries = _clean_keywords(keywords)[:2]
    fallback_keyword = _next_fallback_keyword()
    if fallback_keyword not in queries:
        queries.append(fallback_keyword)
    fallback_index = _query_rotation["index"] % len(FALLBACK_KEYWORD_POOL)
    fallback_keyword_2 = FALLBACK_KEYWORD_POOL[fallback_index]
    if fallback_keyword_2 not in queries:
        queries.append(fallback_keyword_2)
    _query_rotation["index"] += 1

    last_reason = "查無結果"
    for query in queries:
        hits = _query_pixabay(query, orient)
        if not hits:
            if _LAST_QUERY_ERROR:
                last_reason = _LAST_QUERY_ERROR
            continue
        # 嚴格禁止同一支 Pixabay 影片在同一集重複使用（橫式／直式共用 ID 集合）。
        for hit in hits:
            video_id = hit.get("id")
            if video_id is None or video_id in _episode_used_ids:
                continue
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
            _episode_used_ids.add(video_id)
            _episode_stats[orient]["success"] += 1
            # 保留 Pixabay API 回傳的原始來源資訊，供 YouTube 說明欄與稽核檔使用。
            _episode_manifest.append({
                "provider": "Pixabay",
                "asset_id": str(video_id),
                "title": str(hit.get("tags") or query).strip(),
                "creator": str(hit.get("user") or "").strip(),
                "source_url": str(hit.get("pageURL") or "").strip(),
                "search_query": query,
                "orientation": orient,
                "duration_seconds": hit.get("duration"),
                "tags": str(hit.get("tags") or "").strip(),
                "local_file": os.path.basename(local),
                "license": "Pixabay Content License",
                "license_url": "https://pixabay.com/service/license/",
                "attribution_required_by_provider": False,
            })
            print(
                f"[Pixabay B-Roll] 成功套用實景動態影片：{query} -> "
                f"{os.path.basename(local)}"
            )
            return local

    _episode_stats[orient]["failed"] += 1
    print(f"[Pixabay B-Roll] 查詢失敗 ({last_reason})，不重複使用舊素材，回傳無素材")
    return None

def fetch_pixabay_broll_reel(
    keywords,
    duration_seconds,
    orientation=HORIZONTAL,
    cache_dir="assets/broll_cache",
    clip_seconds=4.0,
    max_clips=4,
):
    """建立不循環的動態背景串接：每個來源只取約 4 秒，素材不夠時接原生動態底片。"""
    orient = _orientation(orientation)
    target_duration = max(0.1, float(duration_seconds or 0.1))
    clip_seconds = max(3.0, min(5.0, float(clip_seconds)))
    max_clips = max(1, min(12, int(max_clips)))
    required = min(max_clips, max(1, int((target_duration + clip_seconds - 0.001) // clip_seconds)))

    if not _api_key():
        _episode_stats[orient]["missing_key"] += required
        _episode_stats[orient]["requested"] += required
        print("[Pixabay B-Roll] 缺少 API Key，使用既有動態背景。")
        return None

    queries = _clean_keywords(keywords)
    if not queries:
        queries = [_next_fallback_keyword()]
    # 一次搜尋取得候選池，避免每 4 秒都呼叫一次 API。
    hits = []
    used_queries = []
    for query in queries[:2]:
        used_queries.append(query)
        hits = _query_pixabay(query, orient)
        if hits:
            break
    if not hits:
        fallback = _next_fallback_keyword()
        if fallback not in used_queries:
            hits = _query_pixabay(fallback, orient)
            if hits:
                queries = [fallback]

    selected = []
    cache_path = _cache_dir(cache_dir)
    _episode_stats[orient]["requested"] += required
    for hit in hits:
        if len(selected) >= required:
            break
        video_id = hit.get("id")
        if video_id is None or video_id in _episode_used_ids:
            continue
        video_url = _pick_video_url(hit)
        if not video_url:
            continue
        ext = os.path.splitext(video_url.split("?")[0])[1] or ".mp4"
        source_path = os.path.join(cache_path, f"{orient}_{video_id}{ext}")
        if not os.path.exists(source_path) and not _download(video_url, source_path):
            continue
        _episode_used[orient].append(video_id)
        _episode_used_ids.add(video_id)
        selected.append((source_path, hit, str(video_id)))

    if not selected:
        _episode_stats[orient]["failed"] += required
        return None

    width, height = _ORIENTATION_WH[orient]
    reel_path = os.path.join(
        cache_path,
        f"reel_{orient}_{int(time.time() * 1000)}_{len(selected)}.mp4",
    )
    with tempfile.TemporaryDirectory(prefix="broll_reel_") as work:
        segment_paths = []
        elapsed = 0.0
        for index, (source_path, hit, video_id) in enumerate(selected):
            remaining = target_duration - elapsed
            if remaining <= 0:
                break
            try:
                source_duration = float(hit.get("duration") or clip_seconds)
            except (TypeError, ValueError):
                source_duration = clip_seconds
            segment_duration = min(clip_seconds, remaining, source_duration)
            # 只採用至少 3 秒的完整片段；尾端不足 3 秒改由原生動態背景補齊。
            if segment_duration < 3.0:
                continue
            segment_path = os.path.join(work, f"segment_{index:02d}.mp4")
            command = [
                "ffmpeg", "-y", "-i", source_path,
                "-t", f"{segment_duration:.3f}",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps=24,setsar=1,format=yuv420p",
                "-an", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "27", "-pix_fmt", "yuv420p", segment_path,
            ]
            try:
                subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.CalledProcessError):
                continue
            if os.path.isfile(segment_path) and os.path.getsize(segment_path) > 0:
                segment_paths.append(segment_path)
                elapsed += segment_duration
                _episode_stats[orient]["success"] += 1
                _episode_manifest.append({
                    "provider": "Pixabay",
                    "asset_id": str(video_id),
                    "title": str(hit.get("tags") or (queries[0] if queries else "")).strip(),
                    "creator": str(hit.get("user") or "").strip(),
                    "source_url": str(hit.get("pageURL") or "").strip(),
                    "search_query": str(queries[0] if queries else ""),
                    "orientation": orient,
                    "duration_seconds": hit.get("duration"),
                    "tags": str(hit.get("tags") or "").strip(),
                    "local_file": os.path.basename(source_path),
                    "license": "Pixabay Content License",
                    "license_url": "https://pixabay.com/service/license/",
                    "attribution_required_by_provider": False,
                    "segment_seconds": round(segment_duration, 3),
                })

        if not segment_paths:
            _episode_stats[orient]["failed"] += required
            return None

        list_path = os.path.join(work, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for path in segment_paths:
                handle.write(f"file '{path}'\n")
        concat_path = os.path.join(work, "reel_clips.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
             "-pix_fmt", "yuv420p", concat_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # 素材池不足以覆蓋整段口播時，接原生動態背景，不重播舊素材。
        if elapsed + 0.05 < target_duration:
            fallback_path = os.path.join(work, "motion_fallback.mp4")
            remainder = target_duration - elapsed
            subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"color=c=#07101f:s={width}x{height}:r=24:d={remainder:.3f}",
                    "-vf",
                    "drawgrid=width=72:height=72:thickness=1:color=0x355070@0.42,"
                    "zoompan=z='min(zoom+0.00035,1.04)':d=1:"
                    f"s={width}x{height}:fps=24,format=yuv420p",
                    "-an", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "27", "-pix_fmt", "yuv420p", fallback_path,
                ],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with open(list_path, "w", encoding="utf-8") as handle:
                handle.write(f"file '{concat_path}'\n")
                handle.write(f"file '{fallback_path}'\n")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
                 "-pix_fmt", "yuv420p", reel_path],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            os.replace(concat_path, reel_path)

    print(
        f"[Pixabay B-Roll] 已建立 {len(segment_paths)} 段不重複素材串接，"
        f"每段最長 {clip_seconds:.1f}s，總長目標 {target_duration:.1f}s。"
    )
    return reel_path
