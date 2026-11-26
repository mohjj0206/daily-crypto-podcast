"""極簡故事圖解線（方案 A）——完全獨立主程式，不修改任何舊程式。

設計目標（模仿《投資家富士》視覺節奏）：
- 極簡高留白舞台：移除頂部數據條、底部指標卡與花俏方框，85% 畫面為深邃黑藍漸層
  （或疊加 65% 暗化遮罩的 Pixabay 1080p 實景底片）。
- 語音同步逐行亮起（Kinetic Typography）：中央 1 個大數字對比 ＋ 最多 3～4 行短句
  （每行 ≤14 字），依該頁 TTS 累積秒數依序浮現；正在講的那行 56px 純白／琥珀金發光，
  已講完的上一行退為柔和暗灰。
- 僅左下／右下保留 190×190 貓狗雙主播圓形頭像與底部 66pt 字幕，12 秒動態片尾。
- 單一重磅主題：從今日完整新聞正文只挑 1 個最震撼事件深挖，開頭 30 秒一句話帶過
  BTC／ETH 現價，其餘 95% 深挖該主題，總長 10～13 分鐘、3,500～4,200 字。
- 強制生活比喻法：解釋任何技術／宏觀／鏈上指標必先用台灣人秒懂的生活場景打比方。
- 雙軌 TTS 數字發音：字幕保留 0.6、27.53%，送 TTS 前轉自然中文讀法。

輸出：story_main.mp4、story_short.mp4、story_thumb.jpg、story_metadata.txt。
"""

import html
import json
import os
import re
import subprocess
import tempfile
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

# 單一主題總口播字數目標（不含開場報價與片尾）。
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
      "lines": ["短句1", "短句2", "短句3"],
      "dialogue": [{{"role": "女", "text": "這一頁的口播段落"}}]
    }}
  ]
}}

【slides 規則】
- 共 9 頁，全部口播加總約 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字（約 9～11 分鐘）。
- 每頁必須有扎實的雙人對話拆解：每頁 dialogue 合計約 300～380 字口播，且必須同時包含「台灣人秒懂的生活場景比喻」＋「具體事件與數據推演」，嚴禁空泛重複或湊字。
- 每頁 lines 為 2～{MAX_LINES_PER_SLIDE} 行極簡短句，每行 ≤{MAX_LINE_CHARS} 字；投影片畫面一律維持極簡短句，絕不可把整段口播塞進畫面。
- 每頁 dialogue 為該頁要唸的 1～3 段對話，是畫面短句的口語展開。
- big 為該頁唯一的視覺焦點（大數字對比或一句震撼短標）。

【今日新聞正文】
{news_joined}
"""


def _len_lines(slide):
    return [str(x).strip() for x in (slide.get("lines") or []) if str(x).strip()]


def _normalize_story(raw, news_items, prices):
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

    return f"""今日日期：{TODAY_STR}

【任務】以下是一份使用者提供的「ChatGPT 深度討論素材」。請完整保留其中的所有核心論點、數據與洞察，改寫成一檔 9 頁、約 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字的《投資家富士》風格雙人生活化比喻深度長談。

{opening}

【寫稿硬性規則】
1. 忠於素材：必須完整覆蓋素材中的全部核心論點、數據與洞察，不得遺漏或捏造新事件。
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
      "lines": ["短句1", "短句2", "短句3"],
      "dialogue": [{{"role": "女", "text": "這一頁的口播段落"}}]
    }}
  ]
}}

【slides 規則】
- 共 9 頁，全部口播加總約 {TARGET_MIN_CHARS}～{TARGET_MAX_CHARS} 字（約 9～11 分鐘）。
- 每頁必須有扎實的雙人對話拆解：每頁 dialogue 合計約 300～380 字口播，且必須同時包含「台灣人秒懂的生活場景比喻」＋「素材中的具體論點與數據推演」，嚴禁空泛重複或湊字。
- 每頁 lines 為 2～{MAX_LINES_PER_SLIDE} 行極簡短句，每行 ≤{MAX_LINE_CHARS} 字；投影片畫面一律維持極簡短句，絕不可把整段口播塞進畫面。
- 每頁 dialogue 為該頁要唸的 1～3 段對話，是畫面短句的口語展開。
- big 為該頁唯一的視覺焦點（大數字對比或一句震撼短標）。

【自訂深度素材正文】
{material}
"""


def build_custom_story(material, prices):
    """把 CHATGPT.txt 自訂深度素材交給 AI 改寫為 9 頁極簡長談。

    100% 回傳非空 dict：模型呼叫或解析失敗時退回內建保底結構，絕不回傳 None。
    """
    prompt = _build_custom_prompt(material, prices)
    try:
        raw = script_writer.call_gemini(
            STORY_SYSTEM_PROMPT + "\n\n" + prompt,
            max_output_tokens=16384,
            timeout=300,
            json_mode=True,
        )
        story = _normalize_story(raw, [], prices)
    except Exception as err:  # 模型 429／解析失敗時改用內建保底故事
        print(f"[Minimal] 自訂素材寫稿失敗，改用內建保底故事：{err}")
        story = _fallback_story([], prices)
    total = sum(len(d[1]) for d in story["opening"])
    for slide in story["slides"]:
        total += sum(len(d[1]) for d in slide["dialogue"])
    print(
        f"[Minimal] 自訂主題：{story['topic_title']}｜頁數 {len(story['slides'])}"
        f"｜口播約 {total} 字"
    )
    return story


# ── 極簡高留白舞台 HTML ──────────────────────────────────
def _broll_background_html(broll_path):
    if not broll_path:
        return ""
    return (
        '<video class="broll" autoplay muted loop playsinline '
        f'src="file://{os.path.abspath(broll_path)}"></video>'
        '<div class="dark-mask"></div>'
    )


def _slide_html(slide, reveal_seconds, broll_path, page_label):
    big = html.escape(slide["big"])
    lines = slide["lines"]
    line_markup = "".join(
        f'<div class="line" data-i="{i}">{html.escape(ln)}</div>'
        for i, ln in enumerate(lines)
    )
    reveal_json = json.dumps([round(x, 3) for x in reveal_seconds])
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<style>
@page {{ margin: 0; }}
html, body {{ margin: 0; padding: 0; width: {MAIN_W}px; height: {MAIN_H}px;
  overflow: hidden; background: #05070f; }}
.stage {{ position: relative; width: {MAIN_W}px; height: {MAIN_H}px;
  background: radial-gradient(120% 120% at 50% 18%, #0d1830 0%, #0a1122 42%, #05070f 100%);
  font-family: "Noto Sans CJK TC", "Noto Sans CJK", "Microsoft JhengHei", sans-serif; }}
.broll {{ position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; z-index: 0; }}
.dark-mask {{ position: absolute; inset: 0; z-index: 1;
  background: rgba(3, 6, 15, 0.55); }}
.page-label {{ position: absolute; top: 54px; left: 50%; transform: translateX(-50%);
  z-index: 3; color: #8fa6c8; font-size: 26px; letter-spacing: 6px; font-weight: 700; }}
.center {{ position: absolute; z-index: 3; left: 50%; top: 50%;
  transform: translate(-50%, -54%); width: 1640px; text-align: center; }}
.big {{ color: #fbbf24; font-size: 132px; font-weight: 900; line-height: 1.1;
  margin-bottom: 64px; letter-spacing: 2px;
  text-shadow: 0 6px 40px rgba(251, 191, 36, .45), 0 0 70px rgba(56, 189, 248, .25);
  animation: bigIn 1.1s cubic-bezier(.16,1,.3,1) both; }}
@keyframes bigIn {{ from {{ opacity: 0; transform: translateY(30px) scale(.94); }}
  to {{ opacity: 1; transform: none; }} }}
.lines {{ display: flex; flex-direction: column; gap: 40px; align-items: center; }}
.line {{ font-size: 56px; font-weight: 800; letter-spacing: 1px;
  color: #5b6675; opacity: .32; transition: color .5s ease, opacity .5s ease,
  text-shadow .5s ease, transform .5s ease; transform: scale(.98); }}
.line.active {{ color: #ffffff; opacity: 1; transform: scale(1.04);
  text-shadow: 0 0 26px rgba(251, 191, 36, .75), 0 0 54px rgba(251, 191, 36, .4); }}
.line.done {{ color: #3b4656; opacity: .5; transform: scale(.98); }}
</style></head>
<body>
<div class="stage">
  {_broll_background_html(broll_path)}
  <div class="page-label">{html.escape(page_label)}</div>
  <div class="center">
    <div class="big">{big}</div>
    <div class="lines">{line_markup}</div>
  </div>
</div>
<script>
(function () {{
  const nodes = Array.prototype.slice.call(document.querySelectorAll(".line"));
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
# ── 底部 66pt 字幕（ASS） ────────────────────────────────
def _fmt_ass(seconds):
    seconds = max(0.0, seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(events, out_path):
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {MAIN_W}\nPlayResY: {MAIN_H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Main,Noto Sans CJK TC,66,&H00FFFFFF,&H00FFFFFF,&H00202838,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,2,80,80,70,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for start, end, text in events:
        if not text or end <= start:
            continue
        safe = presentation.sanitize_text(text).replace("\n", " ")
        lines.append(
            f"Dialogue: 0,{_fmt_ass(start)},{_fmt_ass(end)},Main,,0,0,0,,{safe}"
        )
    # libass 對完全沒有 Dialogue 的 ASS 會報錯退出（exit status 234），
    # 因此若沒有任何有效事件，補一行 1 秒的空白佔位對白，確保表頭完整可用。
    if not lines:
        lines.append("Dialogue: 0,0:00:00.00,0:00:01.00,Main,,0,0,0,,")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n".join(lines) + "\n")
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


def render_main_story(story):
    pages = build_pages(story)
    broll_engine.reset_episode_rotation()

    # 1. 逐頁合成 TTS，算出每頁時長與字幕事件。
    page_data = []
    subtitle_events = []
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
            page_dur += dur
            subtitle_events.append((cursor + page_dur - dur, cursor + page_dur, display))
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
            slide_videos = []
            for i, item in enumerate(page_data):
                label = "開場" if i == 0 else f"{i:02d}"
                # 每一頁都輪替呼叫 Pixabay，確保每頁投影片都有實景動態底片。
                broll_path = broll_engine.fetch_pixabay_broll(
                    story.get("broll_keywords"), broll_engine.HORIZONTAL
                )
                out = render_slide_video(
                    i, item["page"], item["dur"], item["reveal"],
                    broll_path, browser, label,
                )
                slide_videos.append(out)
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

    avatar_size = 190
    avatars = media_engine.create_speaker_avatars(
        {"cat": "cat_vtober.mp4" if os.path.exists("cat_vtober.mp4") else None,
         "dog": "dor_vtober.mp4" if os.path.exists("dor_vtober.mp4") else None}
    )
    cat_img = resolve_avatar_input(avatars["cat_idle"], "cat")
    dog_img = resolve_avatar_input(avatars["dog_idle"], "dog")

    # 執行 FFmpeg 前，逐一確認所有輸入檔案存在且非空，避免盲猜 exit status 234。
    _ensure_nonempty_file("story_base.mp4", "主片底片")
    _ensure_nonempty_file(ass_path, "字幕檔")
    _ensure_nonempty_file(cat_img, "貓頭像")
    _ensure_nonempty_file(dog_img, "狗頭像")

    margin = 48
    bottom = MAIN_H - avatar_size - 150
    filter_complex = (
        f"[0:v]subtitles='{_escape_filter_path(ass_path)}'[base];"
        f"[1:v]scale={avatar_size}:{avatar_size}[cat];"
        f"[2:v]scale={avatar_size}:{avatar_size}[dog];"
        f"[base][cat]overlay={margin}:{bottom}[v1];"
        f"[v1][dog]overlay={MAIN_W - avatar_size - margin}:{bottom}[vout]"
    )
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", "story_base.mp4", "-i", cat_img, "-i", dog_img,
            "-i", mixed,
            "-filter_complex", filter_complex, "-map", "[vout]", "-map", "3:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", media_engine.VIDEO_CRF,
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest",
            "story_main.mp4",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[Minimal] FFmpeg 主片合成失敗，stderr 末尾內容：")
        print(result.stderr[-2000:])
        raise subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr
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
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        try:
            clips = []
            for i, item in enumerate(short_pages):
                # 每頁 Shorts 也輪替套用直式 Pixabay 實景動態底片。
                short_broll_path = broll_engine.fetch_pixabay_broll(
                    story.get("broll_keywords"), broll_engine.VERTICAL
                )
                doc = _short_html(item["page"], item["reveal"], short_broll_path)
                out = f"story_short_p{i}.mp4"
                rec_sec = item["dur"] + 0.4
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
                         "-t", f"{item['dur']:.4f}", "-an", "-r", str(FPS),
                         "-c:v", "libx264", "-preset", "medium",
                         "-crf", media_engine.VIDEO_CRF, "-pix_fmt", "yuv420p", out],
                        check=True,
                    )
                clips.append(out)
        finally:
            browser.close()

    short_audio = concat_audio([p["audio"] for p in short_pages], "story_short_audio.mp3")
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
    youtube_uploader.write_publish_bundle(
        pipeline_type="story",
        main_video_path="story_main.mp4",
        short_video_path="story_short.mp4",
        thumb_path="story_thumb.jpg",
        main_title=story["yt_title"],
        main_description=story_description,
        short_title=f"{story['yt_title']}｜60 秒精華 #Shorts",
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

    # 製片成功後，若使用了 CHATGPT.txt 自訂素材，還原為預設註解狀態（防止重複製片）。
    if used_chatgpt:
        reset_chatgpt_file(chatgpt_path)

    if not enable_yt:
        print("[ENABLE_YOUTUBE_UPLOAD=false] 跳過 YouTube 上傳，保留 story_main.mp4 等供 Releases 預覽。")
        return
    youtube = youtube_uploader.build_youtube_client()
    if youtube is None:
        return
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