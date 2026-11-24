import base64
import calendar
import html
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO

import feedparser
import presentation
import matplotlib
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from opencc import OpenCC
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


SUPPORTED_COINS = {
    "BTC": {
        "name": "比特幣",
        "english": "Bitcoin",
        "aliases": ("BTC", "Bitcoin", "比特幣"),
        "coingecko_id": "bitcoin",
        "color": "#f59e0b",
    },
    "ETH": {
        "name": "以太幣",
        "english": "Ethereum",
        "aliases": ("ETH", "Ethereum", "以太幣", "以太坊"),
        "coingecko_id": "ethereum",
        "color": "#818cf8",
    },
    "SOL": {
        "name": "Solana",
        "english": "Solana",
        "aliases": ("SOL", "Solana", "索拉納"),
        "coingecko_id": "solana",
        "color": "#34d399",
    },
    "XRP": {
        "name": "瑞波幣",
        "english": "XRP",
        "aliases": ("XRP", "Ripple", "瑞波幣"),
        "coingecko_id": "ripple",
        "color": "#38bdf8",
    },
    "ADA": {
        "name": "艾達幣",
        "english": "Cardano",
        "aliases": ("ADA", "Cardano", "艾達幣"),
        "coingecko_id": "cardano",
        "color": "#60a5fa",
    },
    "ZEC": {
        "name": "Zcash",
        "english": "Zcash",
        "aliases": ("ZEC", "Zcash", "大零幣"),
        "coingecko_id": "zcash",
        "color": "#fbbf24",
    },
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TW_TZ = timezone(timedelta(hours=8))
TODAY_STR = datetime.now(TW_TZ).strftime("%Y年%m月%d日")
IS_TEST = os.environ.get("TEST_MODE", "").strip().lower() == "true"
OUTRO_DURATION = 12.0
OUTRO_PAUSE = 1.5
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    r"C:\Windows\Fonts\msjhbd.ttc",
    r"C:\Windows\Fonts\msjh.ttc",
)


def clean_txt(value):
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    text = re.sub(r"(?:〈.*?〉)?這篇文章最早(?:發佈|出現)於.*", "", text)
    text = re.sub(r"(?:延伸閱讀|版權所有|copyright|©).*", "", text, flags=re.I)
    text = re.sub(
        r"動區動趨|動區|BlockTempo|區塊客|blockcast\.it|Blockcast",
        "市場消息",
        text,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", text).strip()


def fetch_bytes(url, timeout=20):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; DeepDivePodcast/1.0)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url, timeout=20):
    return json.loads(fetch_bytes(url, timeout).decode("utf-8"))


def fetch_feed(url):
    try:
        parsed = feedparser.parse(fetch_bytes(url))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"RSS 來源無法連線，略過 {url}: {exc}")
        return []
    if parsed.bozo:
        print(f"RSS 格式有誤，略過部分資料 {url}: {parsed.bozo_exception}")
    return parsed.entries


def entry_timestamp(entry):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(published) if published else 0


def article_from_entry(entry, source_url):
    title = clean_txt(entry.get("title", ""))
    summary = clean_txt(entry.get("summary", entry.get("description", "")))
    link = entry.get("link", "")
    if not title:
        return None
    return {
        "title": title,
        "summary": summary,
        "link": link,
        "published": entry_timestamp(entry),
        "source": source_url,
    }


def coin_alias_matches(text, alias):
    if alias.isascii() and alias.isupper():
        return re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", text, re.I) is not None
    return alias.casefold() in text.casefold()


def coin_match_score(article, coin_code):
    metadata = SUPPORTED_COINS[coin_code]
    title = article["title"]
    summary = article["summary"]
    title_matches = sum(coin_alias_matches(title, alias) for alias in metadata["aliases"])
    summary_matches = sum(coin_alias_matches(summary, alias) for alias in metadata["aliases"])
    return title_matches * 5 + summary_matches


def fetch_recent_news():
    feed_urls = (
        "https://blocktempo.com/feed/",
        "https://blockcast.it/feed/",
    )
    cutoff = time.time() - 48 * 3600
    articles = []
    for feed_url in feed_urls:
        for entry in fetch_feed(feed_url):
            article = article_from_entry(entry, feed_url)
            if article and article["published"] >= cutoff:
                articles.append(article)
    return deduplicate_articles(articles)


def deduplicate_articles(articles):
    seen = set()
    unique = []
    for article in sorted(articles, key=lambda item: item["published"], reverse=True):
        key = article["link"] or article["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique


def select_coin(target_coin, recent_news):
    requested = (target_coin or "AUTO").strip().upper()
    if requested not in ("", "AUTO") and requested not in SUPPORTED_COINS:
        raise ValueError(
            f"TARGET_COIN 必須是 AUTO 或 {', '.join(SUPPORTED_COINS)}，收到：{requested}"
        )
    if requested in SUPPORTED_COINS:
        return requested
    if not recent_news:
        raise RuntimeError("近 48 小時沒有可用的幣種新聞，無法自動選題。")

    candidates = []
    for article in recent_news:
        for coin_code in SUPPORTED_COINS:
            score = coin_match_score(article, coin_code)
            if score:
                freshness = article["published"] / 1_000_000_000
                candidates.append((score, freshness, coin_code, article))
    if not candidates:
        raise RuntimeError("近 48 小時新聞未提及支援的主流幣，無法自動選題。")
    _, _, coin_code, _ = max(candidates, key=lambda item: (item[0], item[1]))
    return coin_code


def search_feed_url(base_url, query):
    parameters = urllib.parse.urlencode({"s": query, "feed": "rss2"})
    return f"{base_url}/?{parameters}"


def fetch_topic_articles(coin_code, recent_news):
    metadata = SUPPORTED_COINS[coin_code]
    queries = (
        metadata["english"],
        f"{metadata['english']} upgrade",
        f"{metadata['english']} ecosystem",
        f"{metadata['english']} institution",
    )
    feeds = [
        search_feed_url(base_url, query)
        for base_url in ("https://blockcast.it",)
        for query in queries
    ]
    articles = []
    for feed_url in feeds:
        for entry in fetch_feed(feed_url):
            article = article_from_entry(entry, feed_url)
            if article and coin_match_score(article, coin_code):
                articles.append(article)
    articles.extend(
        article
        for article in recent_news
        if coin_match_score(article, coin_code)
    )
    return deduplicate_articles(articles)[:8]


def select_trigger_article(coin_code, recent_news, context_articles):
    matches = [
        article
        for article in recent_news
        if coin_match_score(article, coin_code)
    ]
    if matches:
        return max(
            matches,
            key=lambda article: (coin_match_score(article, coin_code), article["published"]),
        )
    if context_articles:
        return context_articles[0]
    return {
        "title": f"{SUPPORTED_COINS[coin_code]['name']}市場與生態發展觀察",
        "summary": "近 48 小時沒有找到符合條件的單一幣種新聞，專題將以可查證的行情與生態資料作為導火線。",
        "link": "",
        "published": 0,
        "source": "",
    }


def article_context(articles):
    if not articles:
        return "目前沒有取得可用的相關報導，請明確指出資料限制，不要虛構新聞或事件。"
    return "\n\n".join(
        f"{index}. {article['title']}\n摘要：{article['summary'] or '無摘要'}"
        for index, article in enumerate(articles, 1)
    )


def calc_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def calc_rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(len(values) - period, len(values))]
    gains = sum(max(change, 0) for change in changes) / period
    losses = sum(max(-change, 0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def load_market_data(coin_code):
    symbol = f"{coin_code}USDT"
    base_url = "https://data-api.binance.vision/api/v3"
    candles = fetch_json(f"{base_url}/klines?{urllib.parse.urlencode({'symbol': symbol, 'interval': '1d', 'limit': 210})}")
    if len(candles) < 2:
        raise RuntimeError(f"無法取得 {coin_code} 足夠的日線行情資料。")
    closes = [float(candle[4]) for candle in candles]
    ticker = fetch_json(f"{base_url}/ticker/24hr?{urllib.parse.urlencode({'symbol': symbol})}")
    fng_response = fetch_json("https://api.alternative.me/fng/?limit=1")
    fng_items = fng_response.get("data", [])
    if not fng_items:
        raise RuntimeError("恐慌與貪婪指數 API 沒有回傳資料。")
    return {
        "symbol": symbol,
        "closes": closes,
        "price": float(ticker["lastPrice"]),
        "change_24h": float(ticker["priceChangePercent"]),
        "sma_100": calc_sma(closes, 100),
        "sma_200": calc_sma(closes, 200),
        "rsi_14": calc_rsi(closes, 14),
        "fear_greed": fng_items[0],
    }


def format_price(value):
    if value is None:
        return "資料不足"
    return presentation.format_usd(value) + " USDT"


def market_report(coin_code, market):
    metadata = SUPPORTED_COINS[coin_code]
    fng = market["fear_greed"]
    rsi_display = "資料不足" if market["rsi_14"] is None else f"{market['rsi_14']:.2f}"
    return (
        f"日期：{TODAY_STR}\n主角：{metadata['name']}（{coin_code}，交易對 {market['symbol']}）\n"
        f"即時價格：{format_price(market['price'])}\n"
        f"24 小時漲跌：{market['change_24h']:+.2f}%\n"
        f"100 日均線：{format_price(market['sma_100'])}\n"
        f"200 日均線：{format_price(market['sma_200'])}\n"
        f"14 日 RSI：{rsi_display}\n"
        f"恐慌與貪婪指數：{fng.get('value', '未知')}（{fng.get('value_classification', '未知')}）"
    )


def call_gemini(prompt):
    api_key = os.environ["GEMINI_API_KEY"].strip()
    models = list(GEMINI_MODELS)
    custom_model = os.environ.get("GEMINI_MODEL", "").strip()
    if custom_model:
        models = [custom_model] + [model for model in models if model != custom_model]
    last_error = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 8192},
            }
        ).encode("utf-8")
        while True:
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                response = json.loads(urllib.request.urlopen(request, timeout=240).read().decode("utf-8"))
                return response["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                print(f"Gemini 模型 {model} 回應 HTTP {exc.code}：{detail[:300]}")
                last_error = exc
                if exc.code in (404, 429, 503):
                    print(f"Gemini 模型 {model} 回傳 {exc.code}，立即改試下一個備援。")
                    break
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, KeyError, IndexError, ValueError) as exc:
                print(f"Gemini 模型 {model} 呼叫失敗，改試備援模型：{exc}")
                last_error = exc
                break
    if last_error:
        raise RuntimeError("所有 Gemini 文字模型均無法完成請求。") from None
    raise RuntimeError("沒有可用的 Gemini 模型。")


def generate_illustration_prompt(topic_summary, coin_code):
    metadata = SUPPORTED_COINS[coin_code]
    prompt = (
        "Write one concise English image-generation prompt for a polished 3D financial editorial illustration. "
        "Use no text, letters, logos, watermarks, or UI. Describe an original visual metaphor, "
        "cinematic lighting, deep purple-blue background, cyan and gold accents, and a square composition. "
        f"Coin: {metadata['english']} ({coin_code}). Public headline summary: {topic_summary[:700]}"
    )
    return re.sub(r"^[`'\"]+|[`'\"]+$", "", call_gemini(prompt).strip())


def post_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(request, timeout=90).read().decode("utf-8"))


def generate_imagen_bytes(prompt):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"imagen-3.0-generate-002:predict?key={os.environ['GEMINI_API_KEY'].strip()}"
    )
    response = post_json(
        url,
        {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}},
    )
    predictions = response.get("predictions", [])
    if not predictions:
        raise ValueError("Imagen 回應中沒有 predictions。")
    encoded = predictions[0].get("bytesBase64Encoded")
    if not encoded:
        raise ValueError("Imagen 回應中沒有圖片資料。")
    return base64.b64decode(encoded, validate=True)


def generate_gemini_image_bytes(prompt):
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash-exp:generateContent?key={os.environ['GEMINI_API_KEY'].strip()}"
    )
    response = post_json(
        url,
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        },
    )
    for part in response.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        image_data = part.get("inlineData") or part.get("inline_data")
        if image_data and image_data.get("data"):
            return base64.b64decode(image_data["data"], validate=True)
    raise ValueError("Gemini 圖像回應中沒有 inline image。")


def fetch_coingecko_icon(coin_code):
    coin_id = SUPPORTED_COINS[coin_code]["coingecko_id"]
    details = fetch_json(f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false")
    icon_url = details.get("image", {}).get("large") or details.get("image", {}).get("small")
    if not icon_url:
        raise ValueError("CoinGecko 沒有提供官方圖示網址。")
    return fetch_bytes(icon_url)


def load_font(size, bold=False):
    for font_path in FONTS:
        if bold and "Regular" in font_path:
            continue
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_coin_badge(coin_code):
    metadata = SUPPORTED_COINS[coin_code]
    image = Image.new("RGB", (512, 512), "#100e2b")
    pixels = image.load()
    for y in range(512):
        blend = y / 511
        color = (
            int(18 - 8 * blend),
            int(22 - 10 * blend),
            int(58 - 18 * blend),
        )
        for x in range(512):
            pixels[x, y] = color
    draw = ImageDraw.Draw(image)
    draw.ellipse((54, 54, 458, 458), fill="#17172f", outline="#d6b66b", width=12)
    draw.ellipse((83, 83, 429, 429), outline=metadata["color"], width=5)
    symbol_font = load_font(116, bold=True)
    label_font = load_font(32)
    symbol = coin_code
    bounds = draw.textbbox((0, 0), symbol, font=symbol_font)
    draw.text(
        ((512 - (bounds[2] - bounds[0])) / 2, 188),
        symbol,
        font=symbol_font,
        fill="#f8fafc",
    )
    label = "DEEP DIVE"
    bounds = draw.textbbox((0, 0), label, font=label_font)
    draw.text(
        ((512 - (bounds[2] - bounds[0])) / 2, 332),
        label,
        font=label_font,
        fill="#d6b66b",
    )
    return image


def generate_topic_icon(topic_summary, coin_code):
    image_prompt = (
        f"Original cinematic 3D editorial illustration for {coin_code}, no text, no letters, "
        "no logos, no watermark. A single clear financial metaphor, polished metallic surfaces, "
        "deep purple-blue atmosphere, cyan rim light and restrained gold highlights, square composition."
    )
    try:
        image_prompt = generate_illustration_prompt(topic_summary, coin_code)
    except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
        print(f"無法取得 Gemini 圖像提示詞，改用安全預設提示詞：{type(exc).__name__}")

    for generator_name, generator in (
        ("Imagen", generate_imagen_bytes),
        ("Gemini 圖像模式", generate_gemini_image_bytes),
    ):
        try:
            image_bytes = generator(image_prompt)
            with Image.open(BytesIO(image_bytes)) as source:
                icon = ImageOps.fit(source.convert("RGB"), (512, 512), method=Image.Resampling.LANCZOS)
            icon.save("topic_icon.png")
            print(f"主題意象圖由 {generator_name} 生成。")
            return icon
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError) as exc:
            print(f"{generator_name} 生圖失敗，嘗試下一個備援：{type(exc).__name__}")

    try:
        image_bytes = fetch_coingecko_icon(coin_code)
        with Image.open(BytesIO(image_bytes)) as source:
            icon = ImageOps.fit(source.convert("RGB"), (512, 512), method=Image.Resampling.LANCZOS)
        icon.save("topic_icon.png")
        print("主題意象圖使用 CoinGecko 幣種圖示備援。")
        return icon
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        print(f"CoinGecko 圖示無法取得，改用 Pillow 金屬幣徽：{type(exc).__name__}")

    icon = make_coin_badge(coin_code)
    icon.save("topic_icon.png")
    return icon


def build_prompts(coin_code, market, trigger, articles):
    metadata = SUPPORTED_COINS[coin_code]
    context = article_context(articles)
    report = market_report(coin_code, market)
    target_length = (
        "測試模式：約 200 至 220 字繁體中文，本段只保留 3 至 4 句重點對話。"
        if IS_TEST
        else "正式模式：本段嚴格寫 2,400 至 2,600 字繁體中文，兩段合計 4,800 至 5,200 字。"
    )
    host_persona_guidance = """女聲「以太喵」（cmn-TW-Neural2-A）自稱「本喵」或「以太喵」：冷靜精明、略帶傲嬌吐槽，自然穿插「他喵的」「喵頭不對」等貓語梗；她是忠實的以太坊信仰者，對ETH長期極度看好，熟悉技術升級、Layer 2、DeFi與RWA，對長期潛力抱持如同Tom Lee公開看好以太坊般的信心，但不得冒充或模仿任何外部人士。即使長期看多ETH，也要客觀分析數據、風險、技術瓶頸，不盲目吹捧。
男聲「比特汪」（cmn-TW-Neural2-B）自稱「本汪」或「比特汪」：熱情直率、容易激動，自然使用「汪嗚」「本汪」「別汪想」等狗語梗；他是BTC長期持有者，深信比特幣未來數十年必將成為全球公認的數位黃金（這是角色信念，不是價格或結果保證），自2025年10月25日起每月25日定額NT$10,000累積BTC，另長線配置少量ETH、SOL、XRP、ADA。這是節目角色設定中的模擬帳戶，不是真實成交紀錄；本專題沒有提供帳本數據時，不要自行報帳或插入DCA環節。即使看好BTC，也必須客觀看待回檔、過熱與技術風險，反對頻繁短線進出。
兩人分別代表「數位黃金派」與「世界計算機生態派」，互相尊重、立場互補，自然互相吐槽、穿插幣圈冷笑話和台灣網路梗；以太喵常以數據幫激動的比特汪踩煞車。口播禁止使用HODL、HODLer等縮寫，一律自然說「長期持有」或「長期持有者」。"""
    p1_prompt = f"""你是【幣看深潛｜單一幣種深度專題】的財經播客主編，今天的主角是{metadata['name']}（{coin_code}）。
【篇幅】{target_length}
【市場數據】{report}
【導火線主新聞】{trigger['title']}
【新聞摘要】{trigger['summary']}
【相關前後文報導】
{context}
【雙人角色與互動】{host_persona_guidance}
【結構】第一行輸出「標題：」及 32 字內繁體中文標題；之後逐字稿每行只用「女：」或「男：」開頭。前半段聚焦①導火線新聞與{metadata['name']}幣種概述、②技術特性與過去發展脈絡，串聯可查證的前後文報導。開場後由以太喵以一句約 50 至 65 字口語提醒：節目為公開資訊分享、不構成投資建議、加密貨幣波動大，請獨立判斷並自負盈虧。
【事實規範】只以提供的新聞與行情為依據；分清已發生事實、分析與推測；沒有資料就明說，不虛構升級、機構採用或價格預測。禁止朗讀媒體名稱、網址、Markdown、括號舞台指示與時間問候。不得用「休息一下」「下一段」等分段字眼。"""
    p2_prompt = f"""請接續【幣看深潛】專題逐字稿，主角仍是{metadata['name']}（{coin_code}）。
【篇幅】{"測試模式：約 200 至 220 字繁體中文，3 至 4 句重點對話。" if IS_TEST else "正式模式：本段嚴格寫 2,400 至 2,600 字繁體中文，兩段合計 4,800 至 5,200 字。"}
【前段逐字稿】{{part1}}
【市場數據】{report}
【相關前後文報導】
{context}
【雙人角色與互動】{host_persona_guidance}
【內容】完整涵蓋③當前生態版圖與即時幣價、100/200 日均線、14 日 RSI、恐慌指數的技術面體檢；④多空論據與理性折價、未來展望，以及三個可驗證的關鍵觀察條件。只報提供的數據，指出均線或指標資料不足之處；不可虛構生態項目或保證價格走勢。
逐字稿每行只用「女：」或「男：」開頭。結尾前額外輸出恰好三行圖卡摘要，格式分別為「SLIDE_CONDITION1: ...」「SLIDE_CONDITION2: ...」「SLIDE_CONDITION3: ...」，摘要需對應口播條件且不可含媒體名稱；這三行不是口播內容。不得重複前段新聞與開場，不說分段字眼或時間問候。"""
    return p1_prompt, p2_prompt


def extract_conditions(raw, defaults):
    conditions = []
    for index, fallback in enumerate(defaults, 1):
        match = re.search(
            rf"^SLIDE_CONDITION{index}:\s*(.+)$",
            raw,
            re.M,
        )
        conditions.append(clean_txt(match.group(1)) if match else fallback)
    cleaned = re.sub(r"^SLIDE_CONDITION\d+:.*(?:\r?\n|$)", "", raw, flags=re.M)
    return conditions, cleaned.rstrip()


def default_conditions(coin_code, market):
    sma100 = format_price(market["sma_100"])
    sma200 = format_price(market["sma_200"])
    return [
        f"均線守位｜{coin_code} 價格能否守穩 100 日均線 {sma100}，並觀察 200 日均線 {sma200} 的方向。",
        "量價確認｜突破或跌破近端區間時，成交量是否同步擴大，回測能否守住關鍵位置。",
        f"情緒風險｜恐慌與貪婪指數 {market['fear_greed'].get('value', '未知')} 是否轉向，並與價格及 RSI 訊號互相驗證。",
    ]


def make_dialogues(part1, part2):
    title_match = re.search(r"^標題[：:]\s*(.+)$", part1, re.M)
    title = clean_txt(title_match.group(1)) if title_match else "單一幣種深度專題"
    part1 = re.sub(r"^標題[：:].*(?:\r?\n|$)", "", part1, count=1, flags=re.M)
    full_text = part1.rstrip() + "\n" + part2
    dialogues = []
    for line in full_text.splitlines():
        match = re.match(r"^(女|男)[：:]\s*(.+)$", line.strip())
        if match:
            spoken = clean_txt(match.group(2))
            if spoken:
                dialogues.append((match.group(1), spoken))
    if not dialogues:
        raise RuntimeError("Gemini 沒有輸出可供語音合成的對話。")
    return title, dialogues


def _dialogue_count(dialogues):
    return sum(len(text) for _, text in dialogues)


def _expand_dialogues(dialogues):
    script = "\n".join(f"{role}：{text}" for role, text in dialogues)
    expanded = call_gemini(
        "以下逐字稿偏短。請深度擴寫補強，補充更多白話例子、歷史細節與風險分析。"
        "保持「男：」「女：」開頭、繁體中文、整數美元，禁止台幣、小數金額、承諾獲利與槓桿。"
        "只輸出擴寫後的逐字稿，不要標題。\n\n"
        + script
    )
    _, expanded_dialogues = make_dialogues("標題：擴寫\n" + expanded, "")
    return expanded_dialogues or dialogues


def validate_dialogue_length(dialogues):
    """字數不足只擴寫一次，達 3200 字就放行，不再用 ValueError 中斷成片。"""
    if IS_TEST:
        print(f"測試稿 {_dialogue_count(dialogues)} 字，放行。")
        return dialogues
    character_count = _dialogue_count(dialogues)
    if character_count < 4800:
        print(f"逐字稿 {character_count} 字，低於 4800，改做一次深度擴寫。")
        dialogues = _expand_dialogues(dialogues)
        character_count = _dialogue_count(dialogues)
    if character_count >= 4800:
        print(f"逐字稿 {character_count} 字，進入配音。")
    elif character_count >= 3200:
        print(f"警告：擴寫後 {character_count} 字，低於 4800 但已達 3200，放行進入配音與渲染。")
    else:
        print(f"警告：擴寫後仍只有 {character_count} 字，低於 3200，仍放行以免整集中斷。")
    return dialogues


def run_ffprobe_duration(filename):
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            filename,
        ]
    )
    return float(output.decode().strip())


def synthesize_one(item):
    index, (role, text) = item
    voice_name = "cmn-TW-Neural2-B" if role == "男" else "cmn-TW-Neural2-A"
    fallback_voice = "cmn-TW-Wavenet-B" if role == "男" else "cmn-TW-Wavenet-A"
    pitch = -1.5 if role == "男" else 1.5

    def request_tts(name):
        payload = json.dumps(
            {
                "input": {"text": text},
                "voice": {"languageCode": "cmn-TW", "name": name},
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "pitch": pitch,
                    "speakingRate": 1.2,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://texttospeech.googleapis.com/v1/text:synthesize?key={os.environ['GOOGLE_TTS_KEY'].strip()}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return json.loads(urllib.request.urlopen(request, timeout=45).read().decode("utf-8"))

    try:
        response = request_tts(voice_name)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        lower_detail = detail.lower()
        unsupported = exc.code in (400, 404) and "voice" in lower_detail and any(
            phrase in lower_detail
            for phrase in ("not supported", "unsupported", "not available", "does not exist", "invalid")
        )
        if not unsupported:
            raise RuntimeError(f"Google TTS API 回應 HTTP {exc.code}: {detail[:300]}") from None
        print(f"Neural2 聲線不可用，改用 {fallback_voice}。")
        response = request_tts(fallback_voice)

    filename = f"topic_seg_{index:04d}.mp3"
    with open(filename, "wb") as audio_file:
        audio_file.write(base64.b64decode(response["audioContent"], validate=True))
    return index, filename, text, run_ffprobe_duration(filename)


def synthesize_dialogues(dialogues):
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(synthesize_one, enumerate(dialogues)))
    results.sort(key=lambda result: result[0])
    return results


def make_intro_frame(coin_code):
    image = Image.new("RGB", (1280, 720), "#0b0b24")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 1260, 700), outline="#d6b66b", width=4)
    draw.rectangle((404, 49, 876, 521), outline="#38d9e6", width=3)
    draw.text((640, 558), "幣看深潛｜單一幣種深度專題", fill="#f8fafc", font=load_font(42, True), anchor="mm")
    draw.text(
        (640, 615),
        f"{SUPPORTED_COINS[coin_code]['name']}・{TODAY_STR}",
        fill="#d6b66b",
        font=load_font(34),
        anchor="mm",
    )
    image.save("intro_frame.png")


def make_intro_video(coin_code):
    if not (os.path.exists("info.mp3") and os.path.exists("info.mp4")):
        if os.path.exists("info.mp3") or os.path.exists("info.mp4"):
            print("info.mp3 與 info.mp4 必須同時存在，才會加入片頭；本次略過不完整片頭。")
        return 0.0, False

    make_intro_frame(coin_code)
    source_duration = run_ffprobe_duration("info.mp3")
    fade_duration = min(1.5, source_duration)
    fade_start = max(0.0, min(13.5, source_duration - fade_duration))
    audio_filter = (
        f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f},"
        "apad=whole_dur=15"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", "info.mp3", "-t", "15",
            "-af", audio_filter, "-ar", "24000", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "64k", "intro_cut.mp3",
        ],
        check=True,
    )
    intro_duration = run_ffprobe_duration("intro_cut.mp3")
    frame_count = int(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-count_frames", "-show_entries", "stream=nb_read_frames",
                "-of", "default=noprint_wrappers=1:nokey=1", "info.mp4",
            ]
        ).decode().strip()
    )
    if frame_count <= 0:
        raise RuntimeError("info.mp4 沒有可解碼的影片影格。")
    intro_filter = (
        "[1:v]setpts=PTS-STARTPTS,scale=440:440:force_original_aspect_ratio=decrease,"
        "pad=440:440:(ow-iw)/2:(oh-ih)/2,setsar=1,split=2[forward][backward];"
        "[backward]reverse,setpts=PTS-STARTPTS[reversed];"
        f"[forward][reversed]concat=n=2:v=1:a=0,loop=loop=-1:size={frame_count * 2}:start=0,"
        "fps=24,trim=duration=15,setpts=PTS-STARTPTS[animation];"
        "[0:v][animation]overlay=420:65:shortest=1,fade=t=out:st=14:d=1,"
        "format=yuv420p[output]"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-framerate", "24",
            "-i", "intro_frame.png", "-i", "info.mp4",
            "-filter_complex", intro_filter, "-map", "[output]", "-an",
            "-t", "15", "-r", "24", "-c:v", "libx264",
            "-tune", "stillimage", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "intro.mp4",
        ],
        check=True,
    )
    return intro_duration, True


def create_outro_frame():
    image = Image.new("RGB", (1280, 720), "#0b0b24")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 1260, 700), outline="#d6b66b", width=4)
    avatar_path = os.path.join(BASE_DIR, "avatar.png")
    if os.path.exists(avatar_path):
        with Image.open(avatar_path) as avatar:
            image.paste(
                avatar.convert("RGB").resize((260, 260), Image.Resampling.LANCZOS),
                (510, 30),
            )
    draw = ImageDraw.Draw(image)
    title = "感謝收聽【幣看趨勢】｜別忘了按讚、訂閱、開啟小鈴鐺！"
    title_font = load_font(40, True)
    for index, line in enumerate(wrap_by_width(draw, title, title_font, 1160)):
        bounds = draw.textbbox((0, 0), line, font=title_font)
        draw.text(
            ((1280 - (bounds[2] - bounds[0])) // 2, 315 + index * 50),
            line,
            fill="#f8fafc",
            font=title_font,
        )
    subtitle = "留言告訴以太喵與比特汪，下一集你想看哪一顆幣？"
    subtitle_font = load_font(28)
    for index, line in enumerate(wrap_by_width(draw, subtitle, subtitle_font, 1160)):
        bounds = draw.textbbox((0, 0), line, font=subtitle_font)
        draw.text(
            ((1280 - (bounds[2] - bounds[0])) // 2, 420 + index * 38),
            line,
            fill="#d6b66b",
            font=subtitle_font,
        )

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((60, 510, 600, 680), fill=(5, 6, 24, 185), outline=(214, 182, 107, 210), width=3)
    overlay_draw.rectangle((680, 510, 1220, 680), fill=(5, 6, 24, 185), outline=(56, 217, 230, 210), width=3)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    image.save("outro_frame.png")


def make_outro_assets():
    create_outro_frame()
    outro_source = next(
        (path for path in ("outro.mp3", "info.mp3") if os.path.exists(path)),
        None,
    )
    if outro_source:
        audio_filter = (
            f"volume=0.35,apad=whole_dur={OUTRO_DURATION:g},"
            "afade=t=in:st=0:d=1,"
            f"afade=t=out:st={OUTRO_DURATION - 2.5:g}:d=2.5"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", outro_source, "-t", f"{OUTRO_DURATION:g}",
                "-af", audio_filter, "-ar", "24000", "-ac", "1",
                "-c:a", "libmp3lame", "-b:a", "64k", "outro_music.mp3",
            ],
            check=True,
        )
    else:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", f"{OUTRO_DURATION:g}", "-ar", "24000", "-ac", "1",
                "-c:a", "libmp3lame", "-b:a", "64k", "outro_music.mp3",
            ],
            check=True,
        )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", f"{OUTRO_PAUSE:g}", "-ar", "24000", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "64k", "outro_pause.mp3",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-framerate", "24",
            "-i", "outro_frame.png", "-t", f"{OUTRO_DURATION:g}",
            "-vf", "fade=t=in:st=0:d=1,fade=t=out:st=10:d=2,format=yuv420p",
            "-an", "-r", "24", "-c:v", "libx264",
            "-tune", "stillimage", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "outro.mp4",
        ],
        check=True,
    )


def fmt_ass_time(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{int(hours)}:{int(minutes):02d}:{remaining_seconds:05.2f}"


def write_audio_and_subtitles(results, intro_duration):
    with open("audio_list.txt", "w", encoding="utf-8") as file:
        if intro_duration > 0:
            file.write("file 'intro_cut.mp3'\n")
        for _, filename, _, _ in results:
            file.write(f"file '{filename}'\n")
        file.write("file 'outro_pause.mp3'\n")
        file.write("file 'outro_music.mp3'\n")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "audio_list.txt", "-c", "copy", "podcast_topic.mp3",
        ],
        check=True,
    )

    style = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Noto Sans CJK TC,44,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,"
        "0,0,0,0,100,100,0,0,3,3,0,2,27,27,42,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    converter = OpenCC("s2twp")
    events = []
    current_time = intro_duration
    for _, _, spoken_text, duration in results:
        text = converter.convert(clean_txt(spoken_text))
        chunks = [
            chunk.strip()
            for chunk in re.split(r"[，。！？、；：…!?;\n]+", text)
            if chunk.strip()
        ]
        segments = []
        for chunk in chunks:
            segments.extend(
                chunk[index:index + 20]
                for index in range(0, len(chunk), 20)
                if chunk[index:index + 20].strip()
            )
        character_count = sum(len(segment) for segment in segments)
        if not character_count:
            current_time += duration
            continue
        segment_time = current_time
        for index, segment in enumerate(segments):
            end_time = (
                current_time + duration
                if index == len(segments) - 1
                else segment_time + duration * len(segment) / character_count
            )
            clean_segment = re.sub(r"[，。！？、；：「」『』（）【】《》—…!?;:\(\)\[\]]+", " ", segment)
            clean_segment = re.sub(r"\s+", " ", clean_segment).strip()
            highlighted = re.sub(
                r"(\$[\d,.]+|\b\d+(?:,\d+)*(?:\.\d+)?%?\b|比特幣|以太幣|以太坊|BTC|ETH|SOL|XRP|ADA|ZEC|RSI|均線|風險提醒|投資建議)",
                r"{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H00FFFFFF&}",
                clean_segment,
            )
            events.append(
                f"Dialogue: 0,{fmt_ass_time(segment_time)},{fmt_ass_time(end_time)},"
                f"Default,,0,0,0,,{highlighted}"
            )
            segment_time = end_time
        current_time += duration
    with open("subs_topic.ass", "w", encoding="utf-8") as file:
        file.write(style + "\n".join(events))
    return current_time


def make_topic_chart(coin_code, market):
    metadata = SUPPORTED_COINS[coin_code]
    closes = market["closes"]
    shown = closes[-100:]
    first_index = len(closes) - len(shown)
    font_path = next((path for path in FONTS if os.path.exists(path)), None)
    chart_font = FontProperties(fname=font_path) if font_path else None
    rolling_20 = [
        sum(closes[max(0, index - 19):index + 1]) / len(closes[max(0, index - 19):index + 1])
        for index in range(first_index, len(closes))
    ]
    fig, axis = plt.subplots(figsize=(10.8, 3.0), dpi=100)
    fig.patch.set_facecolor("#11102c")
    axis.set_facecolor("#17172f")
    x_values = list(range(1, len(shown) + 1))
    axis.plot(x_values, shown, color=metadata["color"], linewidth=2.2, label=f"{coin_code} 收盤價")
    axis.plot(x_values, rolling_20, color="#38d9e6", linewidth=1.4, linestyle="--", label="20 日均線")
    if market["sma_100"] is not None:
        axis.axhline(market["sma_100"], color="#d6b66b", linewidth=1.5, label="100 日均線")
    axis.set_title(
        f"{coin_code} 近 100 日走勢與均線",
        color="#f8fafc",
        fontsize=13,
        fontproperties=chart_font,
    )
    axis.tick_params(colors="#cbd5e1", labelsize=8)
    axis.grid(color="#30304c", linewidth=0.6)
    axis.legend(
        facecolor="#17172f",
        edgecolor="none",
        labelcolor="#f8fafc",
        fontsize=8,
        prop=chart_font,
    )
    for spine in ("top", "right"):
        axis.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        axis.spines[spine].set_color("#55556d")
    plt.tight_layout()
    plt.savefig("topic_chart.png", facecolor="#11102c")
    plt.close(fig)


def wrap_by_width(draw, text, font, max_width):
    wrapped = []
    for paragraph in text.split("\n"):
        line = ""
        for character in paragraph:
            candidate = line + character
            bounds = draw.textbbox((0, 0), candidate, font=font)
            if line and bounds[2] - bounds[0] > max_width:
                wrapped.append(line)
                line = character
            else:
                line = candidate
        wrapped.append(line)
    return wrapped


def create_slides(coin_code, market, trigger, articles, conditions):
    metadata = SUPPORTED_COINS[coin_code]
    fng = market["fear_greed"]
    story_titles = [
        f"{datetime.fromtimestamp(article['published'], TW_TZ).strftime('%m/%d')}｜{article['title']}"
        if article["published"]
        else article["title"]
        for article in articles[:4]
    ]
    while len(story_titles) < 4:
        story_titles.append("相關報導資料不足，以下不推定未經證實的事件")
    timeline = "\n".join(f"• {title}" for title in story_titles)
    trend_text = (
        f"價格 {format_price(market['price'])}｜24H {market['change_24h']:+.2f}%\n"
        f"100 日線 {format_price(market['sma_100'])}｜200 日線 {format_price(market['sma_200'])}"
    )
    rsi_display = "資料不足" if market["rsi_14"] is None else f"{market['rsi_14']:.2f}"
    technical_items = articles[:4]
    technical_summary = "\n".join(
        f"• {article['title']}｜{article['summary'][:90]}"
        for article in technical_items
    ) or "目前沒有足夠的技術報導可供核實，不推定未經證實的設計細節。"
    ecology_items = articles[3:7] or articles[1:5]
    ecology = "\n".join(
        f"• {article['title']}｜{article['summary'][:90]}"
        for article in ecology_items
    ) or "目前沒有足夠的生態報導可供核實，不推定未經證實的採用或合作。"
    slides = [
        ("導火線｜今日專題", f"{coin_code}｜{trigger['title']}\n{trigger['summary'][:180]}", None),
        ("幣種檔案｜核心定位", f"{metadata['name']}（{coin_code}）\n導火線摘要：{trigger['summary'][:170]}\n定位與用途應以報導及官方文件核實，不將市場敘事當成已驗證事實。", None),
        ("發展脈絡｜近期報導時間線", timeline, None),
        ("技術特性｜報導與設計取捨", technical_summary, None),
        ("生態版圖｜應用與動態", ecology, None),
        ("即時體檢｜價格與指標", f"{trend_text}\n14 日 RSI：{rsi_display}\n恐慌與貪婪：{fng.get('value', '未知')}（{fng.get('value_classification', '未知')}）", None),
        (f"{coin_code}｜100 日走勢與均線", f"日線收盤價、20 日均線與 100 日均線；均線不足時不以短期資料冒充長期均線。\n資料區間：近 {min(100, len(market['closes']))} 個日線收盤", "topic_chart.png"),
        ("多空打折｜避免單邊敘事", "多方：觀察技術進展、實際使用與生態留存是否轉化為可驗證需求。\n空方：評估供給、競爭、治理、安全及市場流動性風險；利多不等於價格必然上漲。", None),
        ("未來展望｜用條件取代預言", f"{metadata['name']}的後續走勢仍取決於生態落地與市場風險偏好。先列出能驗證的條件，再依資料更新觀點；任何情境都不保證獲利。", None),
        ("觀察條件 1｜均線結構", conditions[0], None),
        ("觀察條件 2｜量價確認", conditions[1], None),
        ("觀察條件 3｜情緒與風險", conditions[2], None),
    ]

    header_font = load_font(24)
    initial_title_size = 34
    initial_body_size = 28
    for index, (title, body, chart_path) in enumerate(slides, 1):
        image = Image.new("RGB", (1280, 720), "#0b0b24")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1280, 82), fill="#17172f")
        draw.text(
            (40, 22),
            f"幣看深潛｜{metadata['name']} 深度專題・{TODAY_STR}",
            fill="#f8fafc",
            font=header_font,
        )
        draw.rectangle((60, 115, 1220, 610), fill="#111126", outline="#d6b66b", width=4)

        title_font = load_font(initial_title_size, True)
        title_lines = wrap_by_width(draw, title, title_font, 1080)
        title_y = 135
        while title_y + len(title_lines) * 44 + 10 > (280 if chart_path else 590) - 35 and title_font.size > 22:
            title_font = load_font(title_font.size - 2, True)
            title_lines = wrap_by_width(draw, title, title_font, 1080)
        if title_y + len(title_lines) * 44 + 10 > (280 if chart_path else 590) - 35:
            raise ValueError(f"第 {index} 張圖卡標題超出可用高度。")
        for line in title_lines:
            draw.text((100, title_y), line, fill=metadata["color"], font=title_font)
            title_y += 44

        body_y = title_y + 10
        body_font = load_font(initial_body_size)
        body_lines = wrap_by_width(draw, body, body_font, 1080)
        line_height = min(40, body_font.size + 12)
        body_limit = 280 if chart_path else 590
        while body_y + len(body_lines) * line_height > body_limit and body_font.size > 16:
            body_font = load_font(body_font.size - 2)
            body_lines = wrap_by_width(draw, body, body_font, 1080)
            line_height = min(40, body_font.size + 12)
        if body_y + len(body_lines) * line_height > body_limit:
            raise ValueError(f"第 {index} 張圖卡內文超出可用高度。")
        for line in body_lines:
            draw.text((100, body_y), line, fill="#f8fafc", font=body_font)
            body_y += line_height

        if chart_path:
            chart_y = max(body_y + 12, 300)
            if chart_y + 285 > 595:
                raise ValueError(f"第 {index} 張圖卡走勢圖超出框線。")
            with Image.open(chart_path) as chart:
                image.paste(chart.convert("RGB").resize((1080, 285), Image.Resampling.LANCZOS), (100, chart_y))
        image.save(f"topic_slide{index}.png")
    return slides


def split_title(text, draw, font, max_width, max_lines=3):
    lines = []
    current = ""
    for character in re.sub(r"\s+", "", text):
        candidate = current + character
        bounds = draw.textbbox((0, 0), candidate, font=font)
        if current and bounds[2] - bounds[0] > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last_line = lines[-1]
        while last_line:
            bounds = draw.textbbox((0, 0), last_line + "…", font=font)
            if bounds[2] - bounds[0] <= max_width:
                break
            last_line = last_line[:-1]
        lines[-1] = last_line + "…"
    return lines


def create_thumbnail(coin_code, topic_title, market, topic_icon):
    metadata = SUPPORTED_COINS[coin_code]
    rsi_display = "N/A" if market["rsi_14"] is None else f"{market['rsi_14']:.1f}"
    image = Image.new("RGB", (1280, 720), "#0b0b24")
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((34, 429, 316, 711), fill=(56, 217, 230, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(22))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 1270, 710), outline="#d6b66b", width=8)
    draw.text((55, 42), "幣看深潛｜單一幣種深度專題", fill="#38d9e6", font=load_font(30, True))

    title_font = load_font(58, True)
    title_lines = split_title(topic_title, draw, title_font, 700, max_lines=10)
    while len(title_lines) > 3 and title_font.size > 42:
        title_font = load_font(title_font.size - 2, True)
        title_lines = split_title(topic_title, draw, title_font, 700, max_lines=10)
    if len(title_lines) > 3:
        title_lines = split_title(topic_title, draw, title_font, 700, max_lines=3)
    for index, line in enumerate(title_lines):
        draw.text((55, 105 + index * 82), line, fill="#f8fafc", font=title_font)

    draw.ellipse((43, 438, 307, 702), outline="#d6b66b", width=5)
    icon = ImageOps.fit(topic_icon.convert("RGB"), (230, 230), method=Image.Resampling.LANCZOS)
    image.paste(icon, (60, 455))
    draw.text((325, 470), f"{coin_code} 即時價格", fill="#38d9e6", font=load_font(27, True))
    draw.text((325, 515), format_price(market["price"]).replace(" USDT", ""), fill="#f8fafc", font=load_font(32, True))
    draw.text((325, 568), f"24H {market['change_24h']:+.2f}%", fill=metadata["color"], font=load_font(28, True))
    draw.text((325, 620), f"RSI 14：{rsi_display}", fill="#d6b66b", font=load_font(25))

    avatar_path = os.path.join(BASE_DIR, "avatar.png")
    if os.path.exists(avatar_path):
        with Image.open(avatar_path) as avatar:
            image.paste(avatar.convert("RGB").resize((430, 430), Image.Resampling.LANCZOS), (790, 150))
    else:
        print("找不到 avatar.png，封面將保留右側角色留白。")
    image.save("thumb_topic.jpg", "JPEG", quality=92)


def upload_video(coin_code, title, dialogue_end, intro_duration, has_intro, slides, market):
    credentials = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"].strip(),
        client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
    )
    youtube = build("youtube", "v3", credentials=credentials)
    chapters = []
    slide_duration = (dialogue_end - intro_duration) / len(slides)
    offset = 15.0 if has_intro else 0.0
    if offset:
        chapters.append("00:00 主題動畫片頭")
    for index, (slide_title, _, _) in enumerate(slides):
        seconds = int(offset + slide_duration * index)
        chapters.append(f"{seconds // 60:02d}:{seconds % 60:02d} {slide_title}")
    outro_start = int(offset + slide_duration * len(slides) + OUTRO_PAUSE)
    chapters.append(f"{outro_start // 60:02d}:{outro_start % 60:02d} 片尾互動與推薦影片")
    description = (
        f"本集【幣看深潛】聚焦 {SUPPORTED_COINS[coin_code]['name']}（{coin_code}），"
        f"整理公開報導、技術脈絡、生態動態與即時行情。\n\n"
        f"⏱️ 專題章節（含 12 秒動態片尾）\n{chr(10).join(chapters)}\n\n"
        f"即時價格：{format_price(market['price'])}\n"
        f"24 小時漲跌：{market['change_24h']:+.2f}%\n"
        "本節目僅作公開資訊與市場數據整理，不構成投資建議。加密資產波動劇烈，請自行研究並承擔風險。\n\n"
        f"#幣看深潛 #{coin_code} #加密貨幣 #區塊鏈"
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title[:95],
                "description": description,
                "tags": ["幣看深潛", coin_code, "加密貨幣", "區塊鏈", "技術分析"],
                "categoryId": "27",
            },
            "status": {"privacyStatus": "unlisted"},
        },
        media_body=MediaFileUpload(
            "output_topic.mp4",
            chunksize=1024 * 1024 * 5,
            resumable=True,
        ),
    )
    result = None
    while result is None:
        _, result = request.next_chunk()
    video_id = result["id"]
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload("thumb_topic.jpg"),
    ).execute()
    print(f"專題已上傳並設定縮圖，YouTube Video ID：{video_id}")


def run_coin_special():
    required = (
        "GEMINI_API_KEY",
        "GOOGLE_TTS_KEY",
        "YT_CLIENT_ID",
        "YT_CLIENT_SECRET",
        "YT_REFRESH_TOKEN",
    )
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        raise RuntimeError(f"缺少必要環境變數：{', '.join(missing)}")

    recent_news = fetch_recent_news()
    requested_coin = os.environ.get("TARGET_COIN", "AUTO")
    coin_code = select_coin(requested_coin, recent_news)
    metadata = SUPPORTED_COINS[coin_code]
    articles = fetch_topic_articles(coin_code, recent_news)
    trigger = select_trigger_article(coin_code, recent_news, articles)
    market = load_market_data(coin_code)
    print(f"本集主角：{metadata['name']}（{coin_code}）；導火線：{trigger['title']}")
    if len(articles) < 6:
        print(f"僅取得 {len(articles)} 篇可核實的相關前後文報導，不會以虛構資料補足。")

    topic_summary = f"{trigger['title']}。{trigger['summary']}"
    topic_icon = generate_topic_icon(topic_summary, coin_code)
    prompt1, prompt2_template = build_prompts(coin_code, market, trigger, articles)
    part1 = call_gemini(prompt1)
    part2 = call_gemini(prompt2_template.replace("{part1}", part1[-6000:]))
    defaults = default_conditions(coin_code, market)
    conditions, part2 = extract_conditions(part2, defaults)
    title_text, dialogues = make_dialogues(part1, part2)
    dialogues = validate_dialogue_length(dialogues)
    title = f"【幣看深潛】{metadata['name']}｜{title_text}（{TODAY_STR}）"
    results = synthesize_dialogues(dialogues)
    intro_duration, has_intro = make_intro_video(coin_code)
    make_outro_assets()
    dialogue_end = write_audio_and_subtitles(results, intro_duration)

    make_topic_chart(coin_code, market)
    slides = create_slides(coin_code, market, trigger, articles, conditions)
    slide_duration = max(0.1, (dialogue_end - intro_duration) / len(slides))
    with open("topic_list.txt", "w", encoding="utf-8") as video_list:
        if has_intro:
            video_list.write("file 'intro.mp4'\n")
        for index in range(1, len(slides) + 1):
            duration = slide_duration + (OUTRO_PAUSE if index == len(slides) else 0)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loop", "1", "-framerate", "24",
                    "-t", f"{duration:.2f}", "-i", f"topic_slide{index}.png",
                    "-c:v", "libx264", "-tune", "stillimage", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-r", "24", f"topic_p{index}.mp4",
                ],
                check=True,
            )
            video_list.write(f"file 'topic_p{index}.mp4'\n")
        video_list.write("file 'outro.mp4'\n")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "topic_list.txt", "-c", "copy", "topic_base.mp4",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", "topic_base.mp4", "-i", "podcast_topic.mp3",
            "-vf", "ass=subs_topic.ass", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "26", "-c:a", "aac",
            "-b:a", "128k", "-shortest", "output_topic.mp4",
        ],
        check=True,
    )
    create_thumbnail(coin_code, title, market, topic_icon)
    upload_video(coin_code, title, dialogue_end, intro_duration, has_intro, slides, market)


def _load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return fallback


def select_evergreen_lesson():
    curriculum = _load_json(
        os.path.join("knowledge_base", "evergreen_curriculum.json"),
        {"lessons": []},
    )
    lessons = curriculum.get("lessons") or []
    custom = os.environ.get("CUSTOM_TOPIC", "").strip()
    if custom:
        return {
            "ep_id": "CUSTOM",
            "series": "加更",
            "title": custom,
            "core_concepts": [custom, "用結構解釋，不用單日新聞"],
            "common_misconceptions": ["把一次敘事當成必然結果", "把示意圖當成即時普查"],
            "chart_type": "concept_bars",
            "status": "pending",
        }
    target = os.environ.get("TARGET_EP_ID", "").strip().upper()
    if target:
        for lesson in lessons:
            if str(lesson.get("ep_id", "")).upper() == target:
                return lesson
        raise RuntimeError(f"課表找不到 {target}")
    progress = _load_json("evergreen_progress.json", {"completed": []})
    done = {item.get("ep_id") for item in progress.get("completed") or []}
    for lesson in lessons:
        if lesson.get("ep_id") not in done and lesson.get("status") != "done":
            return lesson
    if lessons:
        print("課表都已完成，改從 EP01 再講一輪。")
        return lessons[0]
    raise RuntimeError("evergreen_curriculum.json 沒有課程")


def mark_evergreen_complete(lesson):
    ep_id = lesson.get("ep_id")
    if not ep_id or ep_id == "CUSTOM":
        return
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    progress = _load_json("evergreen_progress.json", {"completed": []})
    completed = [item for item in progress.get("completed") or [] if item.get("ep_id") != ep_id]
    completed.append({"ep_id": ep_id, "date": today, "title": lesson.get("title")})
    remaining = []
    curriculum_path = os.path.join("knowledge_base", "evergreen_curriculum.json")
    curriculum = _load_json(curriculum_path, {"lessons": []})
    for item in curriculum.get("lessons") or []:
        if item.get("ep_id") == ep_id:
            item["status"] = "done"
        elif item.get("status") != "done":
            remaining.append(item.get("ep_id"))
    progress["completed"] = completed
    progress["next_ep_id"] = remaining[0] if remaining else None
    progress["updated_at"] = today
    for path, payload in (("evergreen_progress.json", progress), (curriculum_path, curriculum)):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(f"已更新教學進度：{ep_id}，下一堂 {progress['next_ep_id']}")


def main():
    """預設走常青教學。只有 MODE=coin_special 才走舊的單一幣種新聞。"""
    if os.environ.get("MODE", "").strip().lower() == "coin_special":
        print("MODE=coin_special，改走舊的單一幣種新聞路線。")
        run_coin_special()
        return
    print("預設進入常青深度百科教學線，不跑單一幣種新聞。")
    import chart_maker
    import media_engine
    import script_writer
    import youtube_uploader

    is_test = os.environ.get("TEST_MODE", "").strip().lower() == "true"
    lesson = select_evergreen_lesson()
    print(f"本集教學：{lesson.get('ep_id')} {lesson.get('title')}")
    reference = (
        "沒有另抓即時報價。禁止編造今日成交價。"
        "圖上的 $1、$100、$10,000、$30,000、$90,000 都是早期週期或成本帶的區間示意，不是即時普查。"
    )
    chart_path = chart_maker.generate_tutorial_chart(lesson)
    tutorial = script_writer.generate_tutorial_script(
        lesson, reference, {"is_test": is_test}
    )
    rendered = media_engine.render_tutorial_episode(
        tutorial, chart_path, intro_seconds=15, outro_seconds=12, outro_pause=1.5,
    )
    youtube = youtube_uploader.build_youtube_client()
    video_id = youtube_uploader.upload_tutorial_video(youtube, rendered, tutorial, is_test)
    if not is_test and video_id and not str(video_id).startswith(("TEST", "UPLOAD")):
        mark_evergreen_complete(lesson)
    else:
        print("測試模式或上傳未成功，不更新 evergreen_progress.json。")


if __name__ == "__main__":
    main()
