"""深度故事圖解線（數據分析版型 A）——完全獨立主程式，不修改任何舊程式。

設計目標：
- 深色專業財經數據分析版型：主標題、核心 KPI 卡、數據圖表與口播同步重點。
- 僅呈現素材中有來源依據的數據；缺少可靠數值時不虛構圖表，改顯示分析重點。
- 主片採 16:9 資訊圖解，Shorts 維持直式精華版型。
- 雙人 TTS、逐頁字幕與 Pixabay 動態實景素材維持獨立運作。

輸出：story_main.mp4、story_short.mp4、story_thumb.jpg、story_metadata.txt。
"""

import base64
import html
import json
import os
import re
import subprocess
import tempfile
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import presentation
import script_writer
import market_data
import broll_engine
import media_engine
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright


# ── 常數 ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TW_TZ = timezone(timedelta(hours=8))
TODAY_STR = datetime.now(TW_TZ).strftime("%Y年%m月%d日")
IS_TEST = os.environ.get("TEST_MODE", "").strip().lower() == "true"

OUTRO_DURATION = 12.0
OUTRO_PAUSE = 1.5
MAIN_W, MAIN_H = media_engine.MAIN_VIDEO_WIDTH, media_engine.MAIN_VIDEO_HEIGHT
FPS = media_engine.VIDEO_FPS


# ── 頭像路徑解析（含檔案存在檢查與圓形裁切備援） ──────────────
def _circularize_avatar(path, out_path):
    """把任意頭像圖裁成正方形並套用圓形遮罩，輸出帶 alpha 的過場 PNG。"""
    with Image.open(path) as source:
        avatar = source.convert("RGBA")
    width, height = avatar.size
    crop = min(width, height)
    left = (width - crop) // 2
    top = (height - crop) // 2
    avatar = avatar.crop((left, top, left + crop, top + crop)).resize(
        (225, 225), Image.LANCZOS
    )
    mask = Image.new("L", (225, 225), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 224, 224), fill=255)
    out = Image.new("RGBA", (225, 225), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    out.save(out_path)
    return out_path


def resolve_avatar_input(preferred, speaker):
    """確保傳給 FFmpeg -i 的頭像一定存在且為圓形。

    依序檢查 `<speaker>_avatar.png`、`<speaker>_single.png`、
    `<speaker>_single.png.png`、`avatar.png`，取第一個存在的檔案，
    重新裁為圓形後**確認產出檔案存在**再回傳；若某來源損壞（如 CI 產生的
    0 位元或截圖失敗），自動跳到下一個候選，避免 FFmpeg 拿到不存在的
    `cat_avatar.png`／`dog_avatar.png` 而 exit status 234。
    """
    candidates = [
        preferred or f"{speaker}_avatar.png",
        f"{speaker}_avatar.png",
        f"{speaker}_single.png",
        f"{speaker}_single.png.png",
        "avatar.png",
    ]
    seen = set()
    out_path = f"{speaker}_avatar_overlay.png"
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not os.path.exists(candidate):
            continue
        try:
            produced = _circularize_avatar(candidate, out_path)
        except (OSError, ValueError) as error:
            print(
                f"[Minimal] 頭像來源 {candidate} 無法讀取（{error}），"
                "改用下一個候選。"
            )
            continue
        if os.path.exists(produced):
            if candidate != preferred:
                print(
                    f"[Minimal] 使用圓形備援頭像 {candidate} -> {produced}。"
                )
            return produced
    raise FileNotFoundError(
        f"No usable avatar for {speaker}: tried {list(seen)}."
    )

# 單一主題總口播字數目標（含開場；頁數不設硬性限制）。
TARGET_MIN_CHARS = 2600
TARGET_MAX_CHARS = 3400
# 每頁短句上限與每行字數上限。
MAX_LINES_PER_SLIDE = 4
MAX_LINE_CHARS = 14

# 根目錄自訂深度素材檔（CHATGPT.txt）。CI 跑在 Linux 為大小寫敏感，
# 因此同時偵測 .txt 與 .TXT 兩種檔名，避免跨平台找不到檔案。
CHATGPT_FILENAMES = ("CHATGPT.txt", "CHATGPT.TXT")
# 有效內文（去除 # 註解與空白行後）超過此字數才進入專屬主題製片模式。
CHATGPT_MIN_CHARS = 100
# CHATGPT.txt 的預設註解內容（無實質素材時的狀態，也是製片後還原的內容）。
CHATGPT_DEFAULT_CONTENT = (
    "# 若要使用 ChatGPT 深度討論內容製片，請將完整文章貼於此檔案並執行 Minimal Story workflow；"
    "若無實質內容則自動抓取當日即時新聞。\n"
)

# 男主播唯一名稱為「比特汪」：本模組自己的文字清洗函式，
# 無條件把「位元汪／位元犬」等錯誤譯名一律修正為「比特汪」。
_WRONG_DOG_RE = re.compile(r"位元[汪犬]")


def _clean_text(text):
    """共用 presentation.sanitize_text 後，再保險封殺位元汪／位元犬錯字。"""
    cleaned = presentation.sanitize_text(str(text or ""))
    return _WRONG_DOG_RE.sub("比特汪", cleaned)


# ── 抓取開場用 BTC／ETH 現價（CoinGecko 簡單行情） ──────────
def fetch_opening_prices():
    prices = {"BTC": None, "ETH": None}
    try:
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
        btc = data.get("bitcoin") or {}
        eth = data.get("ethereum") or {}
        prices["BTC"] = {"usd": btc.get("usd"), "change": btc.get("usd_24h_change")}
        prices["ETH"] = {"usd": eth.get("usd"), "change": eth.get("usd_24h_change")}
    except Exception as err:  # 靜默退回 None，由寫稿邏輯改成不報價開場
        print(f"[Minimal] 開場報價抓取失敗，改以不報價開場：{err}")
    return prices


# ── 抓取今日完整新聞正文 ─────────────────────────────────
def gather_news():
    entries = market_data.fetch_rss_news(TW_TZ)
    enriched = market_data.enrich_top_news_with_full_text(entries, max_articles=6)
    items = []
    for published_at, entry in enriched:
        title = _clean_text(entry.get("title", ""))
        full_text = entry.get("full_text") or script_writer.clean_txt(
            entry.get("summary", "")
        )
        full_text = _clean_text(full_text)
        items.append(
            {
                "title": title,
                "published": published_at.strftime("%Y-%m-%d %H:%M"),
                "text": full_text,
            }
        )
    return items


# ── 根目錄 CHATGPT.txt 自訂深度素材接入 ────────────────────
def _chatgpt_path():
    """回傳實際存在的 CHATGPT 素材檔路徑；找不到時回傳預設檔名（供寫入用）。"""
    for name in CHATGPT_FILENAMES:
        candidate = os.path.join(BASE_DIR, name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(BASE_DIR, CHATGPT_FILENAMES[0])


def read_chatgpt_material():
    """讀取根目錄 CHATGPT.txt，過濾以 # 開頭的註解行與空白行後回傳純內文。

    回傳 (material_text, used_path)：material_text 為 '' 代表無有效素材；
    used_path 為實際讀到的檔案路徑（找不到時為 None）。
    """
    path = _chatgpt_path()
    if not os.path.exists(path):
        return "", None
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except Exception as err:  # 讀檔失敗則靜默退回當日新聞流程
        print(f"[Minimal] 讀取 CHATGPT.txt 失敗，改走當日新聞：{err}")
        return "", None
    kept = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append(stripped)
    material = "\n".join(kept).strip()
    return material, path


def reset_chatgpt_file(path=None):
    """製片完成後把 CHATGPT 素材檔還原為預設註解狀態，避免下次重複製片。"""
    target = path or _chatgpt_path()
    try:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(CHATGPT_DEFAULT_CONTENT)
        print(f"[Minimal] 已重置 {os.path.basename(target)} 為預設註解狀態。")
        return True
    except Exception as err:
        print(f"[Minimal] 重置 CHATGPT.txt 失敗：{err}")
        return False


# ── 單一主題 × 生活比喻 寫稿引擎 ──────────────────────────
STORY_SYSTEM_PROMPT = (
    "你是台灣最會講故事的加密貨幣雙人主持節目編劇，擅長把艱澀的區塊鏈、宏觀經濟與"
    "鏈上數據，用台灣人一秒就懂的生活場景比喻講清楚（例如餐廳主廚試菜、買房交屋驗屋、"
    "大哥把粗活外包給 L2 小弟）。你只回傳合法 JSON，絕不輸出多餘文字。"
)


def _build_story_prompt(news_items, prices):
    news_block = []
    for idx, item in enumerate(news_items, 1):
        news_block.append(
            f"【新聞 {idx}】標題：{item['title']}\n"
            f"時間：{item['published']}\n正文：{item['text'][:2400]}"
        )
    news_joined = "\n\n".join(news_block)

    btc = prices.get("BTC") or {}
    eth = prices.get("ETH") or {}
    if btc.get("usd") is not None:
        opening = (
            f"開場第一句用一句話帶過 BTC 現價 {presentation.format_usd_price(btc['usd'])} 美元"
            f"（24H {btc.get('change', 0):+.2f}%）與 ETH 現價 "
            f"{presentation.format_usd_price(eth['usd']) if eth.get('usd') is not None else '未知'} 美元，"
            "30 秒內講完，之後 95% 全部深挖單一主題。"
        )
    else:
        opening = "開場第一句話不要報價，直接切入今日最震撼的單一主題。"

    return f"""今日日期：{TODAY_STR}

【任務】只從以下今日新聞中，挑選「最震撼、最值得深挖的 1 個單一核心事件或機構研報」作為整集唯一主軸，做一檔 9～11 分鐘、約 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字的精品長談。

{opening}

【寫稿硬性規則】
1. 單一主題：整集只深挖 1 個事件，禁止變成新聞逐條播報。
2. 強制生活比喻法：凡是解釋任何區塊鏈技術、宏觀經濟或鏈上指標，必須先用一個台灣人秒懂的生活場景打比方，再用白話拆解數據與背後邏輯，嚴禁堆砌枯燥術語。
3. 雙人對話：由「男」與「女」兩位主播交替口播，自然生活化。
4. 所有數字（價格、百分比、倍數）都要寫進口播，字幕會保留原始格式（如 0.6、27.53%）。
5. 內容必須完全基於提供的新聞正文，不得捏造未經證實的數字或事件。

【輸出 JSON 結構】只輸出 JSON，不要 markdown 或註解：
{{
  "topic_title": "整集核心主題標題（20 字內、有懸念）",
  "topic_summary": "一句話說明本集深挖的單一事件",
  "broll_keywords": "英文 Pixabay 實景底片關鍵字，逗號分隔，3～5 個",
  "yt_title": "65～88 字元高點擊懸念標題",
  "opening": [{{"role": "男", "text": "開場 30 秒報價＋點出主題的對話"}}],
  "slides": [
    {{
      "big": "中央大數字對比，例如 15 分鐘 ➔ 6 秒（12 字內）",
      "lines": ["分析重點1", "分析重點2", "分析重點3"],
      "metrics": [{{"label": "指標名稱", "value": "素材中的數值", "detail": "數值意義"}}],
      "chart": {{"title": "比較圖表", "unit": "%", "labels": ["項目A", "項目B"], "values": [12.5, 8.2]}},
      "dialogue": [{{"role": "女", "text": "這一頁的口播段落"}}]
    }}
  ]
}}

【slides 規則】
- 建議依素材內容安排約 6～10 頁，但不是硬性頁數；可少於或多於此範圍。以完整論點、合理段落節奏與口播總長為優先，禁止為湊頁數拆散或重複內容。全部口播加總約 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字（約 9～11 分鐘）。
- 每頁必須有扎實的雙人對話拆解：每頁 dialogue 合計約 300～380 字口播，且必須同時包含「台灣人秒懂的生活場景比喻」＋「具體事件與數據推演」，嚴禁空泛重複或湊字。
- 每頁盡量提供 2～4 個 metrics，label/value/detail 必須來自素材中可核實的數據；沒有數值依據時可少於 2 個，嚴禁捏造。
- 至少有 2 組可比較的真實數值時才提供 chart；labels 與 values 必須一一對應，不能自行估算。
- lines 保留 2～4 個短分析結論，每行 ≤{MAX_LINE_CHARS} 字，作為 KPI／圖表旁的解讀。
- dialogue 為該頁要唸的 1～3 段對話，必須解釋畫面 KPI、圖表及其限制。
- big 是本頁主標題，不再獨佔整張畫面的唯一資訊。

【今日新聞正文】
{news_joined}
"""


def _len_lines(slide):
    return [str(x).strip() for x in (slide.get("lines") or []) if str(x).strip()]


def _normalize_metrics(raw_metrics, require_source=False):
    """整理 KPI 卡片，只保留素材明確提供的數據。"""
    metrics = []
    for item in raw_metrics or []:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"))
        value = _clean_text(item.get("value"))
        detail = _clean_text(item.get("detail"))
        source = _clean_text(item.get("source"))
        if label and value and (source or not require_source):
            metrics.append({
                "label": label[:18], "value": value[:24],
                "detail": detail[:42], "source": source[:90],
            })
        if len(metrics) >= 4:
            break
    return metrics


def _normalize_chart(raw_chart, require_source=False):
    """整理比較圖表；只接受明確數值，避免缺值或文字被誤畫成數據。"""
    if not isinstance(raw_chart, dict):
        return None
    labels = raw_chart.get("labels") or []
    values = raw_chart.get("values") or []
    points = []
    for label, value in zip(labels, values):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not str(label).strip():
            continue
        points.append({"label": _clean_text(label)[:14], "value": number})
        if len(points) >= 6:
            break
    source = _clean_text(raw_chart.get("source"))
    if len(points) < 2 or (require_source and not source):
        return None
    return {
        "title": _clean_text(raw_chart.get("title") or "數據比較")[:30],
        "unit": _clean_text(raw_chart.get("unit") or "")[:12],
        "source": source[:120],
        "points": points,
    }


def _normalize_story(raw, news_items, prices, require_source=False):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("story JSON 不是物件")

    slides_in = data.get("slides") or []
    if not slides_in:
        raise ValueError("story 缺少 slides")

    opening = []
    for line in data.get("opening") or []:
        role = "男" if str(line.get("role")) == "男" else "女"
        text = _clean_text(line.get("text"))
        if text:
            opening.append((role, text))
    # 開場沒有內容時，用一句不報價的預設開場。
    if not opening:
        opening = [("男", f"今天我們只聊一件最關鍵的事，先把時間留給這個主題。")]

    slides = []
    for slide in slides_in:
        lines = _len_lines(slide)[:MAX_LINES_PER_SLIDE]
        lines = [ln[:MAX_LINE_CHARS] for ln in lines]
        if not lines:
            continue
        dialogue = []
        for line in slide.get("dialogue") or []:
            role = "男" if str(line.get("role")) == "男" else "女"
            text = _clean_text(line.get("text"))
            if text:
                dialogue.append((role, text))
        if not dialogue:
            continue
        slides.append(
            {
                "big": _clean_text(slide.get("big"))[:16],
                "lines": lines,
                "metrics": _normalize_metrics(slide.get("metrics"), require_source),
                "chart": _normalize_chart(slide.get("chart"), require_source),
                "source_sections": [
                    _clean_text(section) for section in (slide.get("source_sections") or [])
                    if str(section).strip()
                ],
                "dialogue": dialogue,
            }
        )

    if not slides:
        raise ValueError("story 沒有可用的 slides")

    return {
        "topic_title": _clean_text(data.get("topic_title") or "極簡故事")[:30],
        "topic_summary": _clean_text(data.get("topic_summary")),
        "broll_keywords": str(data.get("broll_keywords") or "cryptocurrency,finance,data"),
        "yt_title": _clean_text(data.get("yt_title") or data.get("topic_title") or "極簡故事"),
        "opening": opening,
        "slides": slides,
    }


def _fallback_story(news_items, prices):
    """模型解析失敗時的內建完整保底結構，確保 build_story 永不回傳 None。"""
    topic_title = "今日市場深度聚焦"
    if news_items:
        topic_title = _clean_text(news_items[0].get("title") or topic_title)[:30] or topic_title
    summary = _clean_text(news_items[0].get("title")) if news_items else ""
    opening = [("男", "今天我們只聊一件最關鍵的事，先把時間留給這個主題。")]
    slides = [
        {
            "big": topic_title[:16],
            "lines": ["今日重點", "一次看懂"],
            "dialogue": [("女", "今天的市場出現關鍵變化，我們用生活化的方式把它講清楚。")],
        }
    ]
    return {
        "topic_title": topic_title,
        "topic_summary": summary,
        "broll_keywords": "cryptocurrency,finance,data",
        "yt_title": topic_title,
        "opening": opening,
        "slides": slides,
    }


def build_story(news_items, prices):
    """呼叫 Gemini 產製單一主題、生活比喻、逐頁短句的極簡故事腳本。

    100% 回傳非空 dict：模型呼叫或解析失敗時退回內建保底結構，絕不回傳 None。
    """
    prompt = _build_story_prompt(news_items, prices)
    try:
        raw = script_writer.call_gemini(
            STORY_SYSTEM_PROMPT + "\n\n" + prompt,
            max_output_tokens=16384,
            timeout=300,
            json_mode=True,
        )
        story = _normalize_story(raw, news_items, prices)
    except Exception as err:  # 模型 429／解析失敗時改用內建保底故事
        print(f"[Minimal] 寫稿失敗，改用內建保底故事：{err}")
        story = _fallback_story(news_items, prices)
    total = sum(len(d[1]) for d in story["opening"])
    for slide in story["slides"]:
        total += sum(len(d[1]) for d in slide["dialogue"])
    print(f"[Minimal] 主題：{story['topic_title']}｜頁數 {len(story['slides'])}｜口播約 {total} 字")
    return story


# ── CHATGPT.txt 自訂深度素材 × 生活比喻 寫稿引擎 ─────────────
def _extract_source_sections(material):
    """擷取原文的一級章節標題，作為深度影片的最低覆蓋清單。"""
    sections = []
    for line in (material or "").splitlines():
        match = re.match(r"^\s*([一二三四五六七八九十]+、\s*.+?)\s*$", line)
        if match:
            title = match.group(1).strip()
            if title not in sections:
                sections.append(title)
    return sections


def _section_coverage_keywords(title):
    """從章節標題取出可驗證的核心詞，容許模型漏填 metadata 但正文確實有涵蓋。"""
    text = re.sub(r"^[一二三四五六七八九十]+、", "", str(title or "")).lower()
    for phrase in (
        "什麼是", "它解決了", "為什麼", "憑什麼", "最容易混淆的",
        "給一般投資人的", "實戰檢核清單", "硬核技術面深度拆解",
        "真實用途與", "估值迷思破解", "破題懸念",
    ):
        text = text.replace(phrase, " ")
    keywords = set(re.findall(r"[a-z][a-z0-9.]*|\d+(?:\.\d+)?", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 2:
            keywords.add(run)
        elif len(run) > 2:
            keywords.update(run[index:index + 2] for index in range(len(run) - 1))
    return keywords


def _section_covered_by_content(title, slides):
    """以章節標題核心詞檢查口播與投影片文字，避免 metadata 漏填造成誤判。"""
    keywords = _section_coverage_keywords(title)
    if not keywords:
        return False, []
    fragments = []
    for slide in slides:
        fragments.extend([slide.get("big", "")] + list(slide.get("lines") or []))
        for item in slide.get("dialogue") or []:
            fragments.append(item.get("text", "") if isinstance(item, dict) else str(item))
    content = " ".join(str(value) for value in fragments).lower()
    matched = sorted(keyword for keyword in keywords if keyword in content)
    return len(matched) >= min(2, len(keywords)), matched


def _build_custom_prompt(material, prices):
    btc = prices.get("BTC") or {}
    eth = prices.get("ETH") or {}
    if btc.get("usd") is not None:
        opening = (
            f"開場第一句用一句話帶過 BTC 現價 {presentation.format_usd_price(btc['usd'])} 美元"
            f"（24H {btc.get('change', 0):+.2f}%）與 ETH 現價 "
            f"{presentation.format_usd_price(eth['usd']) if eth.get('usd') is not None else '未知'} 美元，"
            "30 秒內講完，之後 95% 全部深挖這份自訂深度素材。"
        )
    else:
        opening = "開場第一句話不要報價，直接切入這份自訂深度素材的核心主題。"

    required_sections = _extract_source_sections(material)
    section_checklist = "\n".join(f"- {section}" for section in required_sections)
    return f"""今日日期：{TODAY_STR}

【任務】以下是一份使用者提供的「ChatGPT 深度討論素材」。請完整保留其中的所有核心論點、數據與洞察，改寫成一檔約 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字的《投資家富士》風格雙人生活化比喻深度長談；頁數依素材論點自然安排，不預設固定頁數。

{opening}

【寫稿硬性規則】
1. 忠於素材：必須完整覆蓋素材中的全部核心論點、數據與洞察，不得遺漏或捏造新事件。
1A. 章節覆蓋：下列原文一級章節必須全部納入簡報，逐字填入相關頁面的 source_sections；不可漏掉、改寫標題或只在片尾一筆帶過。
{section_checklist}
2. 強制生活比喻法：凡是解釋任何區塊鏈技術、宏觀經濟或鏈上指標，必須先用一個台灣人秒懂的生活場景打比方，再用白話拆解數據與背後邏輯，嚴禁堆砌枯燥術語。
3. 雙人對話：由「男」（比特汪）與「女」（以太喵）兩位主播交替口播，生動生活化、有來有往。
4. 所有數字（價格、百分比、倍數）都要寫進口播，字幕會保留原始格式（如 0.6、27.53%）。
5. 內容必須完全基於提供的素材，不得捏造未經證實的數字或事件。

【輸出 JSON 結構】只輸出 JSON，不要 markdown 或註解：
{{
  "topic_title": "整集核心主題標題（20 字內、有懸念）",
  "topic_summary": "一句話說明本集深挖的單一主題",
  "broll_keywords": "英文 Pixabay 實景底片關鍵字，逗號分隔，3～5 個",
  "yt_title": "65～88 字元高點擊懸念標題",
  "opening": [{{"role": "男", "text": "開場 30 秒報價＋點出主題的對話"}}],
  "slides": [
    {{
      "big": "中央大數字對比，例如 15 分鐘 ➔ 6 秒（12 字內）",
      "lines": ["分析重點1", "分析重點2", "分析重點3"],
      "metrics": [{{"label": "指標名稱", "value": "素材中的數值", "detail": "數值意義", "source": "原文中支持此數值的短句"}}],
      "chart": {{"title": "比較圖表", "unit": "%", "labels": ["項目A", "項目B"], "values": [12.5, 8.2], "source": "原文中支持整組比較的短句"}},
      "source_sections": ["必須逐字引用上方清單中的原文一級章節標題"],
      "dialogue": [{{"role": "女", "text": "這一頁的口播段落"}}]
    }}
  ]
}}

【slides 規則】
- 建議依素材內容安排約 6～10 頁，但不是硬性頁數；可少於或多於此範圍。以完整論點、合理段落節奏與口播總長為優先，禁止為湊頁數拆散或重複內容。全部口播加總約 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字（約 9～11 分鐘）。
- 每頁必須有扎實的雙人對話拆解：每頁 dialogue 以 240～340 字為目標，且必須同時包含「台灣人秒懂的生活場景比喻」＋「素材中的具體論點與數據推演」，嚴禁空泛重複或湊字；各頁長度可依原文論點自然調整，不得為了湊字重複內容。
- 每頁盡量提供 2～4 個 metrics，label/value/detail 必須來自素材中可核實的數據；每張 KPI 卡都必須附上 source 原文短句，沒有證據就不要輸出該卡。
- chart 必須附 source 原文短句，且 labels／values 必須能由該證據支持；沒有證據就不要輸出 chart。
- 至少有 2 組可比較的真實數值時才提供 chart；labels 與 values 必須一一對應，不能自行估算。
- lines 保留 2～4 個短分析結論，每行 ≤{MAX_LINE_CHARS} 字，作為 KPI／圖表旁的解讀。
- dialogue 為該頁要唸的 1～3 段對話，必須解釋畫面 KPI、圖表及其限制。
- big 是本頁主標題，不再獨佔整張畫面的唯一資訊。

【自訂深度素材正文】
{material}
"""


def build_custom_story(material, prices):
    """把 CHATGPT.txt 自訂深度素材改寫為頁數彈性的深度分析長談。

    模型呼叫、JSON 解析或內容品質檢查失敗時直接中止，
    不得使用通用保底故事取代使用者提供的深度報導。
    """
    prompt = _build_custom_prompt(material, prices)
    try:
        raw = script_writer.call_gemini(
            STORY_SYSTEM_PROMPT + "\n\n" + prompt,
            max_output_tokens=16384,
            timeout=300,
            json_mode=True,
        )
        raw_data = json.loads(raw)
        required_sections = _extract_source_sections(material)

        def _validate_story_output(output_text):
            output_data = json.loads(output_text)
            raw_slides = output_data.get("slides") or []
            covered_sections = {
                section
                for slide in raw_slides
                for section in (slide.get("source_sections") or [])
            }
            missing_sections = []
            inferred_coverage = {}
            for section in required_sections:
                if section in covered_sections:
                    continue
                is_covered, matched = _section_covered_by_content(section, raw_slides)
                inferred_coverage[section] = matched
                if not is_covered:
                    missing_sections.append(section)
            if inferred_coverage:
                print(
                    "[Minimal] 章節正文覆蓋補充檢查："
                    + json.dumps(inferred_coverage, ensure_ascii=False)
                )
            if missing_sections:
                raise ValueError("原文一級章節未完整覆蓋：" + "；".join(missing_sections))

            normalized = _normalize_story(output_text, [], prices, require_source=True)
            lengths = [
                sum(len(dialogue_text) for _, dialogue_text in slide["dialogue"])
                for slide in normalized["slides"]
            ]
            count = sum(lengths) + sum(len(d[1]) for d in normalized["opening"])
            if count > TARGET_MAX_CHARS:
                raise ValueError(
                    f"深度報導口播總字數超出上限：{count} 字"
                    f"（要求 {TARGET_MIN_CHARS:,}～{TARGET_MAX_CHARS:,} 字，含開場）。"
                )
            return normalized, lengths, count

        story, page_lengths, total = _validate_story_output(raw)

        # 首稿不足時採多輪定向補強，避免單次補稿仍不足便直接中止。
        # 每輪以上一輪稿件為基礎，並重新檢查原文一級章節覆蓋與口播總字數。
        repair_attempts = 2
        for repair_index in range(1, repair_attempts + 1):
            if total >= TARGET_MIN_CHARS:
                break
            missing_chars = TARGET_MIN_CHARS - total
            print(
                f"[Minimal] 第 {repair_index}/{repair_attempts} 次補強："
                f"目前口播 {total} 字，距離下限還差 {missing_chars} 字。"
            )
            repair_prompt = f"""你是專業財經 Podcast 主筆。以下是《CHATGPT.TXT》深度報導稿，目前口播長度不足。

【素材與稿件狀況】
- 原始研究素材共 {len(material)} 個字元，包含多個一級章節與大量技術、數據、風險及投資觀點。
- 目前口播共 {total} 字；至少還需增加 {missing_chars} 字，才能達到最低要求。
- 原文一級章節清單：{json.dumps(required_sections, ensure_ascii=False)}

【本輪補強任務】
- 請將 opening 與所有 slides.dialogue 的口播合計擴充至 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字，目標約 {int((TARGET_MIN_CHARS + TARGET_MAX_CHARS) / 2)} 字。
- 目前版本可能過度濃縮原始研究，請逐一檢查原文一級章節，將尚未在口播中充分解釋的核心論點補進最適合的既有頁面。
- 優先補充：技術如何運作、為何形成該結果、數據能證明與不能證明什麼、風險如何傳導、不同立場的反方論點、對一般使用者與投資人的實際影響。
- 不要只加形容詞或重複結論；每段新增口播都必須提供新的解釋、推論或具體例子。
- 保留原本 topic_title、topic_summary、yt_title、broll_keywords、頁面順序與 source_sections；必要時可在既有頁面內增加 dialogue 段落。
- 頁數完全彈性，不得為湊頁數拆分、複製或重複內容。
- 不得捏造數字、事件、來源或原素材沒有支持的結論；可用明確標示的假設例子解釋機制，但不可把假設說成真實數據。
- 保持比特汪與以太喵自然交替對話；新增內容必須能直接口播。
- 不要把 metrics、lines、chart 的文字計入口播字數；字數只計 opening[].text 與 slides[].dialogue[].text。
- 僅輸出完整合法 JSON，不要 Markdown、說明或程式碼區塊。

【原始素材】
{material}

【目前完整 JSON 稿件】
{json.dumps(raw_data, ensure_ascii=False)}
"""
            repaired_raw = script_writer.call_gemini(
                STORY_SYSTEM_PROMPT + "\\n\\n" + repair_prompt,
                max_output_tokens=16384,
                timeout=300,
                json_mode=True,
            )
            raw_data = json.loads(repaired_raw)
            story, page_lengths, total = _validate_story_output(repaired_raw)
            print(
                f"[Minimal] 第 {repair_index} 次補強後："
                f"口播 {total} 字｜各頁字數 {page_lengths}"
            )

        if total < TARGET_MIN_CHARS:
            raise ValueError(
                f"深度報導口播總字數仍不足：{total} 字"
                f"（要求 {TARGET_MIN_CHARS:,}～{TARGET_MAX_CHARS:,} 字，含開場）。"
            )
        print(
            f"[Minimal] 深度報導品質檢查通過：{len(story['slides'])} 頁｜"
            f"口播 {total} 字｜各頁字數 {page_lengths}"
        )
    except Exception as err:
        print(f"[Minimal] CHATGPT.TXT 深度報導製稿失敗，停止本次製片以保留原始素材：{err}")
        raise RuntimeError(
            "CHATGPT.TXT 深度報導未通過品質檢查；已中止製片，不會改用一般新聞或保底故事。"
        ) from err
    return story


# ── 極簡高留白舞台 HTML ──────────────────────────────────
def _broll_background_html(broll_path):
    """將 Pixabay MP4 內嵌為 data URI，避開 Playwright set_content 的 file:// 權限限制。"""
    if not broll_path:
        return ""
    if not os.path.isfile(broll_path) or os.path.getsize(broll_path) < 1024:
        raise RuntimeError(f"Pixabay 素材不存在或檔案異常：{broll_path}")
    with open(broll_path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return (
        '<video class="broll" autoplay muted loop playsinline preload="auto" '
        f'src="data:video/mp4;base64,{encoded}"></video>'
        '<div class="dark-mask"></div>'
    )


def _slide_html(slide, reveal_seconds, broll_path, page_label):
    big = html.escape(slide["big"])
    lines = slide.get("lines") or []
    metrics = slide.get("metrics") or []
    chart = slide.get("chart")
    line_markup = "".join(
        f'<div class="insight" data-i="{i}"><span class="insight-index">{i + 1:02d}</span>'
        f'<span>{html.escape(ln)}</span></div>'
        for i, ln in enumerate(lines[:4])
    )
    metric_markup = "".join(
        '<div class="metric-card">'
        f'<div class="metric-label">{html.escape(item["label"])}</div>'
        f'<div class="metric-value">{html.escape(item["value"])}</div>'
        f'<div class="metric-detail">{html.escape(item["detail"])}</div>'
        f'<div class="metric-source">{html.escape(item.get("source") or "")}</div>'
        '</div>'
        for item in metrics[:4]
    )
    metrics_section = f'<div class="metrics">{metric_markup}</div>' if metric_markup else ""
    chart_markup = ""
    if chart and chart.get("points"):
        max_value = max(abs(point["value"]) for point in chart["points"]) or 1.0
        bars = []
        for point in chart["points"]:
            width = max(3.0, min(100.0, abs(point["value"]) / max_value * 100))
            value_text = f'{point["value"]:,.2f}'.rstrip("0").rstrip(".")
            bars.append(
                '<div class="chart-row">'
                f'<div class="chart-label">{html.escape(point["label"])}</div>'
                f'<div class="chart-track"><div class="chart-bar {"negative" if point["value"] < 0 else "positive"}" '
                f'style="width:{width:.1f}%"></div></div>'
                f'<div class="chart-value">{value_text}{html.escape(chart.get("unit") or "")}</div>'
                '</div>'
            )
        chart_markup = (
            '<section class="panel">'
            f'<div class="panel-title">{html.escape(chart["title"])}</div>'
            f'{"".join(bars)}'
            f'<div class="chart-source">{html.escape(chart.get("source") or "")}</div>'
            '</section>'
        )
    else:
        chart_markup = (
            '<section class="panel chart-placeholder">'
            '<div class="panel-title">數據圖表</div>'
            '<div class="placeholder-note">本頁沒有足夠的可比較數值，避免使用推測數據製圖</div>'
            '</section>'
        )
    reveal_json = json.dumps([round(x, 3) for x in reveal_seconds])
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<style>
@page {{ margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; width: {MAIN_W}px; height: {MAIN_H}px;
  overflow: hidden; background: #050914; }}
.stage {{ position: relative; width: {MAIN_W}px; height: {MAIN_H}px;
  padding: 58px 86px 54px; overflow: hidden;
  background: radial-gradient(100% 100% at 50% 0%, #142746 0%, #091426 48%, #050914 100%);
  font-family: "Noto Sans CJK TC", "Noto Sans CJK", "Microsoft JhengHei", sans-serif; }}
.broll {{ position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; z-index: 0; }}
.dark-mask {{ position: absolute; inset: 0; z-index: 1;
  background: rgba(3, 7, 16, 0.46); }}
.content {{ position: relative; z-index: 3; height: 100%; display: flex; flex-direction: column; }}
.topline {{ display: flex; align-items: center; justify-content: space-between;
  color: #9fb4d1; font-size: 24px; letter-spacing: 3px; font-weight: 700; }}
.brand {{ color: #fbbf24; }}
.headline {{ margin-top: 22px; display: flex; align-items: flex-end; gap: 28px; }}
.big {{ color: #ffffff; font-size: 66px; font-weight: 900; line-height: 1.16;
  letter-spacing: 1px; max-width: 1420px; overflow-wrap: anywhere;
  text-shadow: 0 4px 24px rgba(0,0,0,.55); }}
.headline-mark {{ flex: 0 0 12px; height: 62px; background: #fbbf24; border-radius: 8px; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px; margin-top: 32px; min-height: 190px; }}
.metric-card {{ min-width: 0; padding: 22px 24px; border: 1px solid rgba(125,160,205,.28);
  border-radius: 18px; background: rgba(9,22,42,.9); }}
.metric-label {{ color: #9fb4d1; font-size: 24px; font-weight: 700; }}
.metric-value {{ margin-top: 12px; color: #fbbf24; font-size: 48px; font-weight: 900;
  line-height: 1.12; overflow-wrap: anywhere; }}
.metric-detail {{ margin-top: 10px; color: #d5deea; font-size: 20px; line-height: 1.35; }}
.metric-source {{ margin-top: 7px; color: #8296b2; font-size: 15px; line-height: 1.25; }}
.chart-source {{ margin-top: 10px; color: #8296b2; font-size: 16px; line-height: 1.3; }}
.lower {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 28px;
  flex: 1; min-height: 0; }}
.panel {{ min-width: 0; padding: 24px 28px; border: 1px solid rgba(125,160,205,.25);
  border-radius: 18px; background: rgba(8,19,37,.92); overflow: hidden; }}
.panel-title {{ color: #ffffff; font-size: 30px; font-weight: 800; margin-bottom: 22px; }}
.placeholder-note {{ color: #9fb4d1; font-size: 25px; line-height: 1.6; padding-top: 24px; }}
.chart-row {{ display: grid; grid-template-columns: 170px 1fr 150px; gap: 16px;
  align-items: center; margin: 22px 0; }}
.chart-label {{ color: #c9d6e7; font-size: 22px; overflow-wrap: anywhere; }}
.chart-track {{ height: 22px; border-radius: 20px; background: rgba(148,163,184,.18); overflow: hidden; }}
.chart-bar {{ height: 100%; border-radius: 20px; background: linear-gradient(90deg,#38bdf8,#fbbf24); }}
.chart-bar.negative {{ background: linear-gradient(90deg,#fb7185,#be123c); }}
.chart-value {{ color: #fbbf24; font-size: 22px; font-weight: 800; text-align: right; }}
.insights {{ display: flex; flex-direction: column; gap: 22px; }}
.insight {{ display: flex; align-items: flex-start; gap: 16px; color: #e8eef7;
  font-size: 26px; font-weight: 700; line-height: 1.35; }}
.insight-index {{ flex: 0 0 42px; color: #fbbf24; font-size: 20px; padding-top: 5px; }}
.insight.active {{ color: #ffffff; text-shadow: 0 0 18px rgba(251,191,36,.3); }}
.insight.done {{ color: #9aa9bd; }}
.page-footer {{ margin-top: 20px; display: flex; justify-content: space-between;
  color: #8195b2; font-size: 18px; letter-spacing: 1px; }}
</style></head>
<body>
<div class="stage">
  {_broll_background_html(broll_path)}
  <div class="content">
    <div class="topline"><span class="brand">幣看深潛｜數據分析</span><span>{html.escape(page_label)}</span></div>
    <div class="headline"><div class="headline-mark"></div><div class="big">{big}</div></div>
    {metrics_section}
    <div class="lower">
      {chart_markup}
      <section class="panel">
        <div class="panel-title">本頁解讀</div>
        <div class="insights">{line_markup}</div>
      </section>
    </div>
    <div class="page-footer"><span>DATA-DRIVEN DEEP DIVE</span><span>比特汪 × 以太喵</span></div>
  </div>
</div>
<script>
(function () {{
  const nodes = Array.prototype.slice.call(document.querySelectorAll(".insight"));
  const times = {reveal_json};
  const start = performance.now();
  function apply(active) {{
    for (let i = 0; i < nodes.length; i++) {{
      nodes[i].classList.remove("active", "done");
      if (i < active) nodes[i].classList.add("done");
      else if (i === active) nodes[i].classList.add("active");
    }}
  }}
  function tick(now) {{
    const t = (now - start) / 1000;
    let active = -1;
    for (let i = 0; i < times.length; i++) if (t >= times[i]) active = i;
    apply(active);
    if (active < nodes.length - 1) requestAnimationFrame(tick);
  }}
  apply(-1);
  requestAnimationFrame(tick);
}})();
</script>
</body></html>"""



def render_slide_video(idx, slide, dur_sec, reveal_seconds, broll_path, browser, page_label):
    html_doc = _slide_html(slide, reveal_seconds, broll_path, page_label)
    out_mp4 = f"story_p{idx}.mp4"
    record_seconds = dur_sec + 0.6
    tpad = ",tpad=stop_mode=clone:stop_duration=1"
    with tempfile.TemporaryDirectory(prefix=f"story_slide_{idx}_") as record_dir:
        context = browser.new_context(
            viewport={"width": MAIN_W, "height": MAIN_H},
            device_scale_factor=1,
            record_video_dir=record_dir,
            record_video_size={"width": MAIN_W, "height": MAIN_H},
        )
        page = context.new_page()
        video = page.video
        try:
            page.set_content(html_doc, wait_until="load")
            page.evaluate("() => document.fonts.ready.then(() => true)")
            if broll_path:
                page.wait_for_function(
                    "() => { const v = document.querySelector('.broll'); "
                    "return !!v && v.readyState >= 2 && v.videoWidth > 0 && v.videoHeight > 0; }",
                    timeout=20000,
                )
                page.evaluate("() => document.querySelector('.broll').play()")
                page.wait_for_function(
                    "() => { const v = document.querySelector('.broll'); return v && v.currentTime > 0.15; }",
                    timeout=10000,
                )
                print(f"[Minimal] 已確認 Pixabay 影片正在播放：{os.path.basename(broll_path)}")
            page.wait_for_timeout(max(1, round(record_seconds * 1000)))
        finally:
            context.close()
        recording_path = video.path()
        filters = (
            f"fps={FPS},scale={MAIN_W}:{MAIN_H}:flags=lanczos,"
            f"trim=duration={record_seconds:.6f},setpts=PTS-STARTPTS{tpad},format=yuv420p"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", recording_path,
                "-vf", filters, "-t", f"{dur_sec:.6f}",
                "-an", "-r", str(FPS), "-c:v", "libx264",
                "-preset", "medium", "-crf", media_engine.VIDEO_CRF,
                "-pix_fmt", "yuv420p", out_mp4,
            ],
            check=True,
        )
    return out_mp4


# ── 逐頁 TTS 合成（雙軌發音） ────────────────────────────
def synth_dialogues(groups, prefix):
    """groups: list of (role, text)。回傳 [(idx, fn, display, dur), ...]。"""
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(
            executor.map(
                media_engine.synth_audio_segment,
                enumerate(groups),
                [prefix] * len(groups),
            )
        )
    return results


def concat_audio(files, out_name):
    list_name = f"{out_name}.txt"
    with open(list_name, "w", encoding="utf-8") as handle:
        for fn in files:
            handle.write(f"file '{os.path.abspath(fn)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_name,
         "-c", "copy", out_name],
        check=True,
    )
    return out_name


def distribute_reveal(n_lines, total_dur):
    """把整頁音檔秒數依序分配給每一行，回傳每行揭示的累積秒數。"""
    if n_lines <= 0:
        return []
    per = total_dur / n_lines
    return [round(per * (i + 1), 3) for i in range(n_lines)]
# ── 底部 54pt 自動換行字幕（ASS） ────────────────────────
def _fmt_ass(seconds):
    seconds = max(0.0, seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean_subtitle_text(text):
    """字幕去除中英文標點；保留 1.25、27.53% 等數字格式。"""
    source = presentation.strip_bracket_tags(
        presentation.sanitize_text(str(text or ""))
    )
    source = re.sub(r"\s+", " ", source).strip()
    cleaned = []
    for index, char in enumerate(source):
        if char == "." and index > 0 and index + 1 < len(source):
            if source[index - 1].isdigit() and source[index + 1].isdigit():
                cleaned.append(char)
                continue
        if char == "%" and index > 0 and source[index - 1].isdigit():
            cleaned.append(char)
            continue
        if unicodedata.category(char).startswith("P"):
            continue
        cleaned.append(char)
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()


def _split_ass_subtitle(text, max_width, font_size=54):
    """依字型實際寬度切成單行片段；每個片段獨立計時，絕不使用 ASS 多行換行。"""
    font = ImageFont.truetype(media_engine.get_system_font_path(), font_size)
    tokens = re.findall(r"\d+(?:\.\d+)?%?|[A-Za-z]+(?:[A-Za-z0-9._-]*[A-Za-z0-9])?|.", text)
    chunks = []
    current = ""
    for token in tokens:
        candidate = current + token
        if current and font.getlength(candidate) > max_width:
            if current.strip():
                chunks.append(current.strip())
            current = token.strip()
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def build_ass(events, out_path):
    """逐行字幕：同一時間只顯示一行，依口播時長分配每行顯示時間。"""
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {MAIN_W}\nPlayResY: {MAIN_H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Main,Noto Sans CJK TC,54,&H00FFFFFF,&H00FFFFFF,&H00202838,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,2,280,280,235,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    font = ImageFont.truetype(media_engine.get_system_font_path(), 54)
    lines = []
    max_width = max(400, MAIN_W - 560)
    for start, end, spoken in events:
        if end <= start:
            continue
        clean = _clean_subtitle_text(spoken)
        if not clean:
            continue
        chunks = _split_ass_subtitle(clean, max_width, font_size=54)
        if not chunks:
            continue
        weights = [max(1.0, font.getlength(chunk)) for chunk in chunks]
        total_weight = sum(weights)
        duration = end - start
        elapsed_weight = 0.0
        line_start = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            elapsed_weight += weight
            line_end = end if index == len(chunks) - 1 else start + duration * elapsed_weight / total_weight
            if line_end > line_start:
                lines.append(
                    f"Dialogue: 0,{_fmt_ass(line_start)},{_fmt_ass(line_end)},Main,,280,280,235,,{chunk}"
                )
            line_start = line_end
    if not lines:
        lines.append("Dialogue: 0,0:00:00.00,0:00:01.00,Main,,0,0,0,,")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n".join(lines) + "\n")
    print(f"[Minimal] 逐行字幕完成：{len(lines)} 行；每個時間區間僅一行，已移除標點。")
    return out_path


# ── 把開場＋各頁整理成統一頁面列表 ────────────────────────
def _split_summary_lines(summary):
    summary = presentation.sanitize_text(summary or "")
    if not summary:
        return ["今日最關鍵的一件事"]
    parts = [p for p in re.split(r"[，。；、,;\n]+", summary) if p.strip()]
    lines = []
    for part in parts:
        piece = part.strip()[:MAX_LINE_CHARS]
        if piece:
            lines.append(piece)
        if len(lines) >= MAX_LINES_PER_SLIDE:
            break
    return lines or [summary[:MAX_LINE_CHARS]]


def build_pages(story):
    # 防呆：story 若為 None／非 dict（上游漏回傳）一律降級為空 dict，確保 .get 不崩潰。
    if not isinstance(story, dict):
        story = {}
    # 防呆：topic_title／各字串欄位若為 None（模型漏填）不得拋出 TypeError。
    topic_title = (
        story.get("topic_title") or story.get("title") or "今日市場深度聚焦"
    ).strip()
    pages = []
    pages.append(
        {
            "big": topic_title[:16],
            "lines": _split_summary_lines(story.get("topic_summary") or ""),
            "dialogue": story.get("opening") or [],
        }
    )
    for slide in story.get("slides") or []:
        pages.append(
            {
                "big": (slide.get("big") or "").strip(),
                "lines": slide.get("lines") or [],
                "metrics": slide.get("metrics") or [],
                "chart": slide.get("chart"),
                "dialogue": slide.get("dialogue") or [],
            }
        )
    return pages
# ── 主片合成 ─────────────────────────────────────────────
def _escape_filter_path(path):
    r"""把檔案路徑轉成 FFmpeg filtergraph 內可安全使用的形式。

    `subtitles=`/`ass=` 濾鏡的路徑若含 `:`、`\`、`'` 會被 filtergraph 解析器
    誤判，導致 libass 打不開檔案而回傳 exit status 234。這裡統一轉為絕對
    路徑、正斜線，並跳脫 filtergraph 保留字元。
    """
    absolute = os.path.abspath(path).replace("\\", "/")
    return absolute.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _ensure_nonempty_file(path, label):
    """確認輸入檔案存在且大小 > 0，否則丟出含檢查重點的錯誤。"""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"[Minimal] 主片合成缺少輸入 [{label}]：{path} 不存在。"
            "請檢查前一步驟是否成功產出該檔案。"
        )
    size = os.path.getsize(path)
    if size <= 0:
        raise RuntimeError(
            f"[Minimal] 主片合成輸入 [{label}] 為空檔（0 bytes）：{path}。"
        )
    return size


def _mux_story_avatars(video_path, audio_path, output_path, subtitle_path, speaker_intervals, speech_end):
    """沿用 Render and Upload Podcast 的 VTuber 合成方式，依實際說話時間切換兩位主持人。"""
    talk_videos = {
        "cat": "cat_vtober.mp4" if os.path.exists("cat_vtober.mp4") else None,
        "dog": "dor_vtober.mp4" if os.path.exists("dor_vtober.mp4") else None,
    }
    assets = media_engine.create_speaker_avatars(talk_videos)
    cat_speaking = "+".join(
        f"between(t,{start:.6f},{end:.6f})" for start, end in speaker_intervals["F"]
    ) or "0"
    dog_speaking = "+".join(
        f"between(t,{start:.6f},{end:.6f})" for start, end in speaker_intervals["M"]
    ) or "0"
    speech_window = f"between(t,0,{speech_end:.6f})"
    command = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path]
    indexes = {}

    def add_input(name, path, is_video=False):
        indexes[name] = len(indexes) + 2
        if is_video:
            command.extend(["-stream_loop", "-1", "-i", path])
        else:
            command.extend(["-loop", "1", "-framerate", str(FPS), "-i", path])

    add_input("cat_idle", assets["cat_idle"])
    add_input("cat_talk", talk_videos["cat"] or assets["cat_speaking"], bool(talk_videos["cat"]))
    add_input("dog_idle", assets["dog_idle"])
    add_input("dog_talk", talk_videos["dog"] or assets["dog_speaking"], bool(talk_videos["dog"]))
    if any(talk_videos.values()):
        add_input("circle_mask", assets["circle_mask"])
    for speaker in ("cat", "dog"):
        if talk_videos[speaker]:
            add_input(f"{speaker}_ring", assets[f"{speaker}_ring"])

    filters = [f"[0:v]ass='{_escape_filter_path(subtitle_path)}'[v_sub]"]
    active = [speaker for speaker in ("cat", "dog") if talk_videos[speaker]]
    if len(active) == 2:
        filters.append(f"[{indexes['circle_mask']}:v]format=gray,split=2[cat_mask][dog_mask]")
    elif active:
        filters.append(f"[{indexes['circle_mask']}:v]format=gray[{active[0]}_mask]")
    for speaker in active:
        filters.append(
            f"[{indexes[f'{speaker}_talk']}:v]fps={FPS},"
            "scale=252:252:force_original_aspect_ratio=increase,crop=252:252,setsar=1,format=rgba"
            f"[{speaker}_square];[{speaker}_square][{speaker}_mask]alphamerge[{speaker}_talk_circle]"
        )
    cat_talk = "[cat_talk_circle]" if talk_videos["cat"] else f"[{indexes['cat_talk']}:v]"
    dog_talk = "[dog_talk_circle]" if talk_videos["dog"] else f"[{indexes['dog_talk']}:v]"
    filters.extend([
        f"[v_sub][{indexes['cat_idle']}:v]overlay=x=42:y=817.5:enable='{speech_window}*not({cat_speaking})'[v_cat_idle]",
        f"[v_cat_idle]{cat_talk}overlay=x=42:y='804+6*sin(t*14)':eval=frame:enable='{cat_speaking}'[v_cat_talk]",
    ])
    stage = "v_cat_talk"
    if talk_videos["cat"]:
        filters.append(
            f"[{stage}][{indexes['cat_ring']}:v]overlay=x=42:y='804+6*sin(t*14)':eval=frame:enable='{cat_speaking}'[v_cat_speaking]"
        )
        stage = "v_cat_speaking"
    filters.extend([
        f"[{stage}][{indexes['dog_idle']}:v]overlay=x=1653:y=817.5:enable='{speech_window}*not({dog_speaking})'[v_dog_idle]",
        f"[v_dog_idle]{dog_talk}overlay=x=1653:y='804+6*sin(t*16)':eval=frame:enable='{dog_speaking}'[v_dog_talk]",
    ])
    stage = "v_dog_talk"
    if talk_videos["dog"]:
        filters.append(
            f"[{stage}][{indexes['dog_ring']}:v]overlay=x=1653:y='804+6*sin(t*16)':eval=frame:enable='{dog_speaking}'[v_dog_speaking]"
        )
        stage = "v_dog_speaking"
    filters.append(f"[{stage}]null[v_out]")
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[v_out]", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", media_engine.VIDEO_CRF,
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", output_path,
    ])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print("[Minimal] VTuber 雙人動畫合成失敗：")
        print(result.stderr[-3000:])
        raise subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr
        )
    print(
        "[Minimal] VTuber 動畫已合成："
        f"以太喵={'影片動畫' if talk_videos['cat'] else '靜態說話圖'}，"
        f"比特汪={'影片動畫' if talk_videos['dog'] else '靜態說話圖'}"
    )


def render_main_story(story):
    pages = build_pages(story)
    broll_engine.reset_episode_rotation()

    # 1. 逐頁合成 TTS，算出每頁時長與字幕事件。
    page_data = []
    subtitle_events = []
    speaker_intervals = {"F": [], "M": []}
    cursor = 0.0
    all_audio = []
    global_index = 0
    for page in pages:
        groups = page["dialogue"]
        synth = synth_dialogues(groups, f"story_line{global_index}_")
        global_index += len(groups)
        page_dur = 0.0
        page_audio = []
        for idx, fn, display, dur in synth:
            segment_start = cursor + page_dur
            page_dur += dur
            segment_end = cursor + page_dur
            subtitle_events.append((segment_start, segment_end, display))
            role = groups[idx][0] if idx < len(groups) else "男"
            speaker_intervals["F" if role == "女" else "M"].append((segment_start, segment_end))
            page_audio.append(fn)
        page_audio_path = concat_audio(page_audio, f"story_page_{len(page_data)}.mp3")
        reveal = distribute_reveal(len(page["lines"]), page_dur)
        page_data.append(
            {
                "page": page,
                "dur": page_dur,
                "audio": page_audio_path,
                "reveal": reveal,
                "start": cursor,
            }
        )
        all_audio.append(page_audio_path)
        cursor += page_dur

    dialogue_end = cursor

    # 2. 用 Playwright 逐頁錄影。
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        try:
            if not broll_engine._api_key():
                raise RuntimeError(
                    "Minimal Story 必須使用 Pixabay 實景素材，但未設定 PIXABAY_API_KEY；"
                    "已停止輸出，避免再次產生沒有 Pixabay 的影片。"
                )
            slide_videos = []
            broll_manifest = []
            last_broll_path = None
            validated_broll = set()
            for i, item in enumerate(page_data):
                label = "開場" if i == 0 else f"{i:02d}"
                requested_broll = broll_engine.fetch_pixabay_broll(
                    story.get("broll_keywords"), broll_engine.HORIZONTAL
                )
                reused = False
                if requested_broll:
                    broll_path = requested_broll
                    last_broll_path = requested_broll
                elif last_broll_path:
                    broll_path = last_broll_path
                    reused = True
                    print(
                        f"[Minimal] 第 {i + 1} 頁 Pixabay 查詢未命中，"
                        f"沿用上一支已驗證素材：{os.path.basename(broll_path)}"
                    )
                else:
                    raise RuntimeError(
                        "Pixabay 第一支實景影片下載失敗，停止製片；"
                        "不允許以暗底或空白畫面冒充 Pixabay 素材。"
                    )
                if broll_path not in validated_broll:
                    probe = subprocess.check_output(
                        [
                            "ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type", "-of", "json", broll_path,
                        ],
                        text=True,
                    )
                    stream_info = json.loads(probe)
                    if not any(x.get("codec_type") == "video" for x in stream_info.get("streams", [])):
                        raise RuntimeError(f"Pixabay 素材沒有可解碼的影像串流：{broll_path}")
                    validated_broll.add(broll_path)
                out = render_slide_video(
                    i, item["page"], item["dur"], item["reveal"],
                    broll_path, browser, label,
                )
                slide_videos.append(out)
                broll_manifest.append({
                    "slide": i + 1,
                    "asset": os.path.basename(broll_path),
                    "source": "Pixabay",
                    "reused": reused,
                })

            if len(broll_manifest) != len(page_data) or not validated_broll:
                raise RuntimeError("Pixabay 實際套用檢查未通過，停止輸出。")
            with open("story_broll_manifest.json", "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "source": "Pixabay",
                        "slides": broll_manifest,
                        "unique_assets": len(validated_broll),
                        "applied_slides": len(broll_manifest),
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            print(
                f"[Minimal] Pixabay 實際套用完成：{len(broll_manifest)}/{len(page_data)} 頁，"
                f"使用 {len(validated_broll)} 支已驗證影片。"
            )
        finally:
            browser.close()

    # 3. 拼接語音 + 片尾停頓 + 片尾音樂。
    media_engine.create_outro_audio(OUTRO_DURATION, OUTRO_PAUSE)
    voice = concat_audio(all_audio, "story_voice.mp3")
    full_audio = concat_audio(
        [voice, "outro_pause.mp3", "outro_music.mp3"], "story_full_audio.mp3"
    )

    # 4. 拼接所有頁面影片 + 片尾影片。
    media_engine.create_outro_frame()
    media_engine.create_outro_video(OUTRO_DURATION)
    concat_list = "story_video_concat.txt"
    with open(concat_list, "w", encoding="utf-8") as handle:
        for fn in slide_videos:
            handle.write(f"file '{os.path.abspath(fn)}'\n")
        handle.write(f"file '{os.path.abspath('outro.mp4')}'\n")
    base_result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", "story_base.mp4"],
        capture_output=True,
        text=True,
    )
    if base_result.returncode != 0:
        print("[Minimal] 底片拼接（story_base.mp4）失敗，stderr 末尾內容：")
        print(base_result.stderr[-2000:])
        raise subprocess.CalledProcessError(
            base_result.returncode, base_result.args,
            output=base_result.stdout, stderr=base_result.stderr,
        )
    _ensure_nonempty_file("story_base.mp4", "主片底片")

    # 5. 字幕 + 雙頭像 + 混音合成最終主片。
    ass_path = build_ass(subtitle_events, "story_subs.ass")
    transition_times = [item["start"] for item in page_data[1:]]
    mixed = "story_mixed.mp3"
    media_engine.mix_voice_with_bed(full_audio, mixed, transition_times)

    # 執行 FFmpeg 前確認主片底片與字幕存在，再依每段 TTS 時間疊入兩位主持人的動態頭像。
    _ensure_nonempty_file("story_base.mp4", "主片底片")
    _ensure_nonempty_file(ass_path, "字幕檔")
    _mux_story_avatars(
        "story_base.mp4", mixed, "story_main.mp4", ass_path,
        speaker_intervals, dialogue_end,
    )
    print(f"[Minimal] 主片完成：story_main.mp4（口播 {dialogue_end:.1f}s + 片尾）")
    return {"dialogue_end": dialogue_end, "pages": page_data}


# ── 縮圖 ─────────────────────────────────────────────────
def create_thumbnail(story):
    W, H = 1280, 720
    image = Image.new("RGB", (W, H), "#05070f")
    draw = ImageDraw.Draw(image)
    for y in range(H):
        ratio = y / H
        r = max(int(13 - 8 * ratio), 5)
        g = max(int(24 - 16 * ratio), 7)
        b = max(int(48 - 33 * ratio), 15)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    font_path = media_engine.get_system_font_path()
    title_font = ImageFont.truetype(font_path, 78)
    big_font = ImageFont.truetype(font_path, 96)

    topic = story["topic_title"]
    big = story["slides"][0]["big"] if story.get("slides") else topic
    bbox = draw.textbbox((0, 0), big, font=big_font)
    draw.text(((W - (bbox[2] - bbox[0])) / 2, 120), big, fill="#fbbf24", font=big_font)
    lines, cur = [], ""
    for ch in list(topic):
        if draw.textbbox((0, 0), cur + ch, font=title_font)[2] > 1080:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    y = 300
    for ln in lines[:3]:
        bbox = draw.textbbox((0, 0), ln, font=title_font)
        draw.text(((W - (bbox[2] - bbox[0])) / 2, y), ln, fill="#ffffff", font=title_font)
        y += 96
    image.save("story_thumb.jpg", "JPEG", quality=92)
# ── Shorts 直式精華（9:16） ──────────────────────────────
def _short_html(page, reveal_seconds, broll_path=None):
    big = html.escape(page["big"])
    line_markup = "".join(
        f'<div class="line">{html.escape(ln)}</div>' for ln in page["lines"]
    )
    reveal_json = json.dumps([round(x, 3) for x in reveal_seconds])
    SW, SH = media_engine.SHORT_VIDEO_WIDTH, media_engine.SHORT_VIDEO_HEIGHT
    broll_markup = _broll_background_html(broll_path) if broll_path else ""
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<style>
html,body{{margin:0;width:{SW}px;height:{SH}px;overflow:hidden;background:#05070f;
font-family:"Noto Sans CJK TC","Microsoft JhengHei",sans-serif;}}
.stage{{position:relative;width:100%;height:100%;
background:radial-gradient(120% 90% at 50% 20%,#0d1830 0%,#0a1122 45%,#05070f 100%);}}
.broll{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;z-index:0;}}
.dark-mask{{position:absolute;inset:0;z-index:1;background:rgba(3,6,15,0.55);}}
.center{{position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);width:92%;text-align:center;z-index:3;}}
.big{{color:#fbbf24;font-size:88px;font-weight:900;line-height:1.12;margin-bottom:70px;
text-shadow:0 6px 40px rgba(251,191,36,.45);}}
.lines{{display:flex;flex-direction:column;gap:46px;align-items:center;}}
.line{{font-size:62px;font-weight:800;color:#5b6675;opacity:.32;
transition:color .5s,opacity .5s,text-shadow .5s,transform .5s;}}
.line.active{{color:#fff;opacity:1;transform:scale(1.05);
text-shadow:0 0 26px rgba(251,191,36,.75),0 0 54px rgba(251,191,36,.4);}}
.line.done{{color:#3b4656;opacity:.5;}}
</style></head><body><div class="stage">{broll_markup}<div class="center">
<div class="big">{big}</div><div class="lines">{line_markup}</div></div></div>
<script>(function(){{
const nodes=[].slice.call(document.querySelectorAll(".line"));const times={reveal_json};
const start=performance.now();
function apply(a){{for(let i=0;i<nodes.length;i++){{nodes[i].classList.remove("active","done");
if(i<a)nodes[i].classList.add("done");else if(i===a)nodes[i].classList.add("active");}}}}
function tick(n){{const t=(n-start)/1000;let a=-1;for(let i=0;i<times.length;i++)if(t>=times[i])a=i;
apply(a);if(a<nodes.length-1)requestAnimationFrame(tick);}}apply(-1);requestAnimationFrame(tick);}})();
</script></body></html>"""


def render_short_story(story, main_result):
    W, H = media_engine.SHORT_VIDEO_WIDTH, media_engine.SHORT_VIDEO_HEIGHT
    short_pages = main_result["pages"][:2]
    short_total = min(60.0, sum(item["dur"] for item in short_pages))
    remaining = short_total
    clip_durations = []
    for item in short_pages:
        clip_duration = min(item["dur"], max(0.0, remaining))
        clip_durations.append(clip_duration)
        remaining -= clip_duration
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        try:
            clips = []
            for i, item in enumerate(short_pages):
                clip_duration = clip_durations[i]
                if clip_duration <= 0:
                    continue
                # 每頁 Shorts 也輪替套用直式 Pixabay 實景動態底片。
                short_broll_path = broll_engine.fetch_pixabay_broll(
                    story.get("broll_keywords"), broll_engine.VERTICAL
                )
                doc = _short_html(item["page"], item["reveal"], short_broll_path)
                out = f"story_short_p{i}.mp4"
                rec_sec = clip_duration + 0.4
                with tempfile.TemporaryDirectory() as rd:
                    ctx = browser.new_context(
                        viewport={"width": W, "height": H}, device_scale_factor=1,
                        record_video_dir=rd, record_video_size={"width": W, "height": H},
                    )
                    page = ctx.new_page()
                    video = page.video
                    try:
                        page.set_content(doc, wait_until="load")
                        page.evaluate("() => document.fonts.ready.then(() => true)")
                        page.wait_for_timeout(max(1, round(rec_sec * 1000)))
                    finally:
                        ctx.close()
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", video.path(),
                         "-vf", f"fps={FPS},scale={W}:{H}:flags=lanczos,"
                                f"trim=duration={rec_sec:.4f},setpts=PTS-STARTPTS,format=yuv420p",
                         "-t", f"{clip_duration:.4f}", "-an", "-r", str(FPS),
                         "-c:v", "libx264", "-preset", "medium",
                         "-crf", media_engine.VIDEO_CRF, "-pix_fmt", "yuv420p", out],
                        check=True,
                    )
                clips.append(out)

            broll_stats = broll_engine.get_episode_stats()[broll_engine.VERTICAL]
            if os.environ.get("PIXABAY_API_KEY", "").strip():
                coverage = (
                    broll_stats["success"] / broll_stats["requested"]
                    if broll_stats["requested"] else 0.0
                )
                print(
                    f"[Minimal] Shorts Pixabay 覆蓋率：{broll_stats['success']}/"
                    f"{broll_stats['requested']}（{coverage:.0%}）"
                )
                min_coverage = float(os.environ.get("PIXABAY_MIN_COVERAGE", "0.5"))
                if coverage < min_coverage:
                    raise RuntimeError(
                        f"Shorts Pixabay B-Roll 覆蓋率 {coverage:.0%}，"
                        f"低於最低要求 {min_coverage:.0%}；請檢查 API Key／配額／直式素材搜尋。"
                    )
            else:
                print("[Minimal] 未設定 Pixabay API Key，Shorts 將使用預設動態底圖。")
        finally:
            browser.close()

    short_audio_full = concat_audio([p["audio"] for p in short_pages], "story_short_audio_full.mp3")
    short_audio = "story_short_audio.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", short_audio_full, "-t", f"{short_total:.4f}",
         "-c:a", "libmp3lame", "-q:a", "2", short_audio],
        check=True,
    )
    list_name = "story_short_concat.txt"
    with open(list_name, "w", encoding="utf-8") as handle:
        for fn in clips:
            handle.write(f"file '{os.path.abspath(fn)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_name,
         "-c", "copy", "story_short_base.mp4"], check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", "story_short_base.mp4", "-i", short_audio,
         "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
         "story_short.mp4"], check=True,
    )
    print("[Minimal] Shorts 完成：story_short.mp4")
# ── Metadata ─────────────────────────────────────────────
def write_metadata(story, main_result):
    lines = [f"【極簡故事】{story['yt_title']}\n", "本集核心看點："]
    for slide in story["slides"][:3]:
        head = slide["lines"][0] if slide["lines"] else ""
        lines.append(f"• {slide['big']}｜{head}")
    lines.append("")
    lines.append("⏱️ 章節（依實際語音秒數）")
    cursor = 0.0
    for item in main_result["pages"]:
        label = item["page"]["big"] or "開場"
        m, s = divmod(int(cursor), 60)
        lines.append(f"{m:02d}:{s:02d} {label}")
        cursor += item["dur"]
    lines.append("")
    lines.append("免責聲明：本節目為市場資訊分享，不構成任何投資建議。加密貨幣波動劇烈，請審慎評估風險。")
    with open("story_metadata.txt", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("[Minimal] Metadata 完成：story_metadata.txt")


# ── 主流程 ───────────────────────────────────────────────
def main():
    enable_yt = os.environ.get("ENABLE_YOUTUBE_UPLOAD", "false").strip().lower() == "true"
    print(f"=== 極簡故事圖解線啟動 [{TODAY_STR}]（測試模式:{IS_TEST}）===")
    prices = fetch_opening_prices()

    # 優先使用根目錄 CHATGPT.txt 自訂深度素材；無有效內容（<100 字）時退回當日新聞。
    material, chatgpt_path = read_chatgpt_material()
    used_chatgpt = len(material) >= CHATGPT_MIN_CHARS
    if used_chatgpt:
        print(
            f"[Minimal] 偵測到 CHATGPT.txt 自訂深度素材（共 {len(material)} 字），"
            "進入專屬主題製片模式！"
        )
        print("[Minimal 1/4] 以 CHATGPT.txt 自訂深度素材撰寫生活比喻長談...")
        story = build_custom_story(material, prices)
    else:
        print("[Minimal 1/4] 抓取今日完整新聞正文...")
        news_items = gather_news()
        print(
            f"[Minimal 2/4] 選定單一主題並撰寫生活比喻長談"
            f"（{len(news_items)} 則素材）..."
        )
        story = build_story(news_items, prices)

    print("[Minimal 3/4] 合成逐行亮起極簡舞台主片與 Shorts...")
    main_result = render_main_story(story)
    create_thumbnail(story)
    render_short_story(story, main_result)
    print("[Minimal 4/4] 輸出 metadata...")
    write_metadata(story, main_result)

    # 輸出標準化發佈清單，供雲端一鍵發佈腳本（publish_preview_to_youtube.py）使用。
    import youtube_uploader
    story_description = (
        open("story_metadata.txt", encoding="utf-8").read()
        if os.path.exists("story_metadata.txt") else ""
    )
    short_total = min(60.0, sum(item["dur"] for item in main_result["pages"][:2]))
    youtube_uploader.write_publish_bundle(
        pipeline_type="story",
        main_video_path="story_main.mp4",
        short_video_path="story_short.mp4",
        thumb_path="story_thumb.jpg",
        main_title=story["yt_title"],
        main_description=story_description,
        short_title=f"{story['yt_title']}｜{int(short_total)} 秒精華 #Shorts",
        short_description=(
            f"完整極簡故事：https://is.gd/2JOYTF\n\n"
            "📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://is.gd/2JOYTF\n\n"
            "本節目為市場資訊分享，不構成任何投資建議。\n#Shorts #比特幣 #加密貨幣 #幣看趨勢"
        ),
        tags=["加密貨幣", "比特幣", "以太幣", "區塊鏈", "極簡故事", "幣看趨勢"],
        first_comment=(
            "📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://is.gd/2JOYTF\n"
            f"你對「{story['yt_title']}」這集的看法是？留言告訴比特汪與以太喵！👇"
        ),
    )

    if not enable_yt:
        print("[ENABLE_YOUTUBE_UPLOAD=false] 跳過 YouTube 上傳，保留 story_main.mp4 等供 Releases 預覽。")
        return
    youtube = youtube_uploader.build_youtube_client()
    if youtube is None:
        raise RuntimeError("YouTube 用戶端建立失敗；保留 CHATGPT.txt 原始素材，不執行重置。")
    description = (
        open("story_metadata.txt", encoding="utf-8").read()
        if os.path.exists("story_metadata.txt") else ""
    )
    video_id = youtube_uploader.upload_main_video(
        youtube, video_path="story_main.mp4", thumb_path="story_thumb.jpg",
        title=story["yt_title"], description=description,
        tags=["加密貨幣", "比特幣", "以太幣", "區塊鏈", "極簡故事", "幣看趨勢"],
        show_dca=False, cur_p=0, btc_pct=0.0, fng_num=0, is_test=IS_TEST,
    )
    youtube_uploader.upload_short_video(
        youtube, short_path="story_short.mp4", main_video_id=video_id,
        cur_p=0, fng_num=0, today_str=TODAY_STR,
    )


if __name__ == "__main__":
    main()
    print("[Minimal] 縮圖完成：story_thumb.jpg")