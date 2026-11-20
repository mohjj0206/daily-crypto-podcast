import os, re, json, time, calendar, base64, html, io, tempfile, subprocess, urllib.request, math, unicodedata
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import feedparser
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from opencc import OpenCC
from urllib.parse import quote
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from playwright.sync_api import sync_playwright

tw_tz = ZoneInfo("Asia/Taipei")
now_tw = datetime.now(tw_tz)
today_str = now_tw.strftime("%Y年%m月%d日")
is_test = os.environ.get("TEST_MODE", "").lower() == "true"
show_dca = (now_tw.weekday() == 4) or (now_tw.day == 26)
OUTRO_DURATION = 12.0
OUTRO_PAUSE = 1.5
SLIDE_COUNT = 15 if show_dca else 14


def cache_busted_url(url, parameter="_t"):
    if parameter is None:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{parameter}={int(time.time() * 1000)}"


def fetch_bytes(url, timeout=15, headers=None, cache_bust_param="_t"):
    request_headers = {
        "User-Agent": "Mozilla/5.0",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        cache_busted_url(url, cache_bust_param),
        headers=request_headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url, timeout=15, headers=None, cache_bust_param="_t"):
    return json.loads(
        fetch_bytes(url, timeout, headers, cache_bust_param).decode("utf-8")
    )


def clean_txt(s):
    s = re.sub(r'(?:〈.*?〉)?這篇文章最早(?:發佈|出現)於.*', '', s or '')
    s = re.sub(r'(?:延伸閱讀|版權所有|copyright|©).*', '', s, flags=re.I)
    return re.sub(
        r'動區動趨|動區|BlockTempo|區塊客|blockcast\.it|Blockcast',
        '市場消息',
        s,
        flags=re.I,
    ).strip()


AI_PURE_TITLE_KEYWORDS = re.compile(
    r"ChatGPT|OpenAI|Claude|Anthropic|DeepSeek|Sora|Midjourney|Copilot|"
    r"Perplexity|Grok|xAI|Mistral|黃仁勳|英偉達|輝達|Nvidia|"
    r"(?:Google\s+)?Gemini(?!\s*[）\)]?\s*(?:交易所|Trust|Earn|雙子星))|"
    r"大語言模型|大模型|生成式\s*AI|人工智慧|人工智能",
    re.IGNORECASE,
)

AI_CONTENT_KEYWORDS = re.compile(
    r"ChatGPT|OpenAI|Claude|Anthropic|DeepSeek|Sora|Midjourney|Copilot|"
    r"Perplexity|Grok|xAI|Mistral|LLM|大語言模型|大模型|生成式\s*AI|"
    r"人工智慧|人工智能|機器學習|算力|GPU|黃仁勳|英偉達|輝達|Nvidia|"
    r"(?:Google\s+)?Gemini(?!\s*[）\)]?\s*(?:交易所|Trust|Earn|雙子星))",
    re.IGNORECASE,
)

CRYPTO_SPECIFIC_KEYWORDS = re.compile(
    r"比特幣|比特币|以太坊|以太幣|以太币|加密貨幣|加密货币|加密資產|加密市場|加密市场|"
    r"幣圈|币圈|虛擬貨幣|虚拟货币|區塊鏈|区块链|Web3|DeFi|NFT|DAO|鏈上|链上|"
    r"跨鏈|跨链|公鏈|公链|去中心化|智能合約|智能合约|錢包|钱包|私鑰|助記詞|"
    r"質押|质押|挖礦|挖矿|空投|airdrop|銘文|符文|迷因幣|MEME幣|山寨幣|穩定幣|稳定币|"
    r"交易所|CEX|DEX|"
    r"(?<![A-Za-z0-9])(?:BTC|ETH|USDT|USDC|USDE|SOL|XRP|ADA|DOGE|WLD|FET|RENDER|RNDR|TAO)(?![A-Za-z0-9])|"
    r"幣安|Binance|OKX|Coinbase|Bybit|火幣|HTX|Bitget|Kraken|Uniswap|Vitalik|V神|"
    r"Worldcoin|Bittensor|合約清算|爆倉",
    re.IGNORECASE,
)

LLM_TOKEN_PATTERN = re.compile(
    r"(?:\d+\s*[萬kKmM]?\s*(?:個)?代[幣币]|每(?:百萬|千)?代[幣币]|代[幣币](?:費用|價格|消耗|成本|上限)|"
    r"上下文(?:長度)?.*?代[幣币]|token\s*代[幣币]|輸入代[幣币]|輸出代[幣币])",
    re.IGNORECASE,
)


def is_pure_ai_news(entry):
    title = str(entry.get("title", ""))
    summary = str(entry.get("summary", ""))
    description = str(entry.get("description", ""))
    full_text = f"{title} {summary} {description}"

    if not (AI_PURE_TITLE_KEYWORDS.search(title) or AI_CONTENT_KEYWORDS.search(full_text)):
        return False

    title_has_crypto = bool(CRYPTO_SPECIFIC_KEYWORDS.search(title))
    text_without_llm_tokens = LLM_TOKEN_PATTERN.sub("", full_text)
    crypto_matches_in_body = set(CRYPTO_SPECIFIC_KEYWORDS.findall(text_without_llm_tokens))

    if AI_PURE_TITLE_KEYWORDS.search(title) and not title_has_crypto:
        if len(crypto_matches_in_body) < 2:
            return True

    if len(crypto_matches_in_body) == 0 and not title_has_crypto:
        return True

    return False


CORE_NEWS_KEYWORDS = re.compile(
    r"比特幣|比特币|以太坊|以太幣|以太币|加密貨幣|加密货币|區塊鏈|区块链|"
    r"穩定幣|稳定币|交易所|巨鯨|巨鲸|鏈上|链上|代幣|代币|監管|监管|"
    r"聯準會|联准会|美聯儲|美联储|升息|降息|利率|通膨|通胀|總體經濟|"
    r"宏觀經濟|宏观经济|金融監管|金融监管|金融市場|金融市场|貨幣政策|货币政策|"
    r"經濟數據|经济数据|央行|美債|美债|國債|国债|數位資產|数字资产|"
    r"(?<![A-Z0-9])(?:BTC|ETH|USDT|USDC|USDE|SOL|XRP|ADA|ETF|SEC|NFT|CPI|PCE|GDP)(?![A-Z0-9])|"
    r"bitcoin|ethereum|cryptocurrency|crypto|blockchain|web3|defi|stablecoin|"
    r"solana|ripple|tokenization|digital assets?|federal reserve|interest rates?|"
    r"(?<![A-Z0-9])Fed(?![A-Z0-9])|rate hikes?|rate cuts?|monetary policy|"
    r"inflation|macroeconomics|economic data|financial regulation|"
    r"exchange|whale|on[- ]chain",
    re.IGNORECASE,
)


def is_core_crypto_news(entry):
    if is_pure_ai_news(entry):
        return False
    content = " ".join(
        str(entry.get(field, ""))
        for field in ("title", "summary", "description")
    )
    return bool(CORE_NEWS_KEYWORDS.search(content))


def remove_publish_time(text):
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"[\[\(【（]\s*(?:發布時間|來源時間)[:：].*?[\]\)】）]", "", s, flags=re.I)
    s = re.sub(r"(?:發布時間|來源時間)[:：]\s*\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:\s*\d{1,2}:\d{2})?(?:\s*(?:CST|UTC))?", "", s, flags=re.I)
    s = re.sub(r"[\[\(【（]\s*\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:\s*\d{1,2}:\d{2})?(?:\s*(?:CST|UTC))?\s*[\]\)】）]", "", s, flags=re.I)
    return s.strip()


def clean_slide_text(text):
    return remove_publish_time(text)


def clean_slide_title(title):
    cleaned = remove_publish_time(title)
    for _ in range(3):
        cleaned = re.sub(r"^\d+[\.、\s]+", "", cleaned)
        cleaned = re.sub(r"^[(（]\d+[)）]\s*", "", cleaned)
        cleaned = re.sub(r"^市場焦點\s*[一二三四五六七八九十\d]*\s*[｜|:：\-]\s*", "", cleaned)
        cleaned = re.sub(r"^焦點\s*[一二三四五六七八九十\d]*\s*[｜|:：\-]\s*", "", cleaned)
        cleaned = re.sub(r"^【(.*)】$", r"\1", cleaned).strip()
        cleaned = re.sub(r"^「(.*)」$", r"\1", cleaned).strip()
        cleaned = re.sub(r'^"(.*)"$', r"\1", cleaned).strip()
        cleaned = re.sub(r"^'(.*)'$", r"\1", cleaned).strip()
        cleaned = re.sub(r"^(?:動區動趨|動區|BlockTempo|區塊客|blockcast\.it|Blockcast|快訊)[｜|:：\-]\s*", "", cleaned, flags=re.I)
        cleaned = remove_publish_time(cleaned)
    return cleaned.strip()


rss_urls = (
    "".join(["https", "://", "www.blocktempo.", "com", "/feed/"]),
    "".join(["https", "://", "blockcast.", "it", "/feed/"]),
)
source_entries = []
for rss_url in rss_urls:
    try:
        feed = feedparser.parse(fetch_bytes(rss_url, timeout=20))
        if feed.bozo:
            print(f"RSS feed error for '{rss_url}': {feed.bozo_exception}")
        source_entries.append(feed.entries)
    except Exception as e:
        print(f"Error fetching RSS feed '{rss_url}': {e}")
        source_entries.append([])

def published_datetime(entry):
    published = entry.get("published_parsed")
    if not published:
        return None
    try:
        timestamp = calendar.timegm(published)
        if timestamp <= 0:
            return None
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(tw_tz)
    except (OverflowError, TypeError, ValueError):
        return None


def filter_recent_entries(source_entries, now_utc):
    cutoff = now_utc - timedelta(hours=36)
    valid_entries = []
    discarded = {"invalid": 0, "future": 0, "stale": 0, "pure_ai": 0, "off_topic": 0}
    for source in source_entries:
        for entry in source:
            published_at = published_datetime(entry)
            if published_at is None:
                discarded["invalid"] += 1
                continue
            published_utc = published_at.astimezone(timezone.utc)
            if published_utc > now_utc:
                discarded["future"] += 1
                continue
            if published_utc < cutoff:
                discarded["stale"] += 1
                continue
            if is_pure_ai_news(entry):
                discarded["pure_ai"] += 1
                continue
            if not is_core_crypto_news(entry):
                discarded["off_topic"] += 1
                continue
            valid_entries.append((published_at, entry))
    return valid_entries, discarded


valid_entries, discarded_news = filter_recent_entries(
    source_entries, datetime.now(timezone.utc)
)
valid_entries.sort(key=lambda item: item[0], reverse=True)
entries = valid_entries[:10]
print(
    "RSS freshness filter: "
    f"{len(entries)} accepted; discarded {discarded_news['invalid']} invalid, "
    f"{discarded_news['future']} future, {discarded_news['stale']} older than 36h, "
    f"{discarded_news['pure_ai']} pure AI, "
    f"{discarded_news['off_topic']} off-topic."
)
if len(entries) < 3:
    raise RuntimeError(
        "Fewer than 3 RSS articles passed the 36-hour freshness, crypto/macro "
        "topic, and non-pure-AI filters; refusing to build an episode from stale, "
        "off-topic, or unverifiable news."
    )

news_list = "\n\n".join(
    f"{index + 1}. 【{clean_slide_title(clean_txt(entry.get('title')))}】\n"
    f"發布時間：{published_at.strftime('%Y-%m-%d %H:%M')} CST\n"
    f"摘要：{clean_txt(entry.get('summary', '')).replace(chr(10), ' ')[:160]}..."
    for index, (published_at, entry) in enumerate(entries)
)
def parse_source_datetime(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return datetime.fromtimestamp(int(stripped), timezone.utc)
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    raise ValueError("Missing or invalid source timestamp.")


def normalize_fng_response(response, source_name):
    items = response.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError(f"{source_name} returned no fear-and-greed data.")
    normalized = []
    now_utc = datetime.now(timezone.utc)
    for item in items:
        try:
            value = int(item["value"])
            classification = str(item["value_classification"]).strip()
            timestamp = parse_source_datetime(item["timestamp"])
            age = now_utc - timestamp
            if not 0 <= value <= 100 or not classification:
                raise ValueError("Invalid fear-and-greed value or classification.")
            if age < timedelta(0) or age > timedelta(hours=36):
                raise ValueError("Fear-and-greed data is future-dated or older than 36h.")
            normalized.append({
                "value": str(value),
                "value_classification": classification,
                "timestamp": timestamp,
            })
        except (KeyError, OSError, TypeError, ValueError, OverflowError) as error:
            print(f"Discarding invalid {source_name} fear-and-greed record: {error}")
    if not normalized:
        raise ValueError(f"{source_name} returned no fresh valid fear-and-greed records.")
    normalized.sort(key=lambda item: item["timestamp"], reverse=True)
    return normalized[0], normalized[1] if len(normalized) > 1 else None


def fetch_fear_and_greed():
    alternative_url = "".join(["https", "://", "api.alternative.", "me", "/fng/?limit=2"])
    try:
        return normalize_fng_response(fetch_json(alternative_url), "Alternative.me")
    except Exception as error:
        print(f"Primary fear-and-greed API failed validation: {error}")

    cmc_api_key = os.environ.get("COINMARKETCAP_API_KEY", "").strip()
    if not cmc_api_key:
        print(
            "Fear-and-greed backup unavailable: COINMARKETCAP_API_KEY is not configured; "
            "the indicator will be omitted rather than replaced with a stale value."
        )
        return None, None
    cmc_url = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical?start=1&limit=2"
    try:
        return normalize_fng_response(
            fetch_json(
                cmc_url,
                headers={"X-CMC_PRO_API_KEY": cmc_api_key},
            ),
            "CoinMarketCap",
        )
    except Exception as error:
        print(f"Backup fear-and-greed API failed validation: {error}")
        return None, None


today_fng, yest_fng = fetch_fear_and_greed()
binance_api = "".join(["https", "://", "data-api.binance.", "vision", "/api/v3"])
vb = f"{binance_api}/klines"
YAHOO_SYMBOLS = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD",
    "ADAUSDT": "ADA-USD",
    "ZECUSDT": "ZEC-USD",
}
COINGECKO_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "XRPUSDT": "ripple",
    "ADAUSDT": "cardano",
    "ZECUSDT": "zcash",
}


def validate_market_series(candles, interval, minimum_count, source_name):
    if not isinstance(candles, list) or len(candles) < minimum_count:
        raise ValueError(
            f"{source_name} returned fewer than {minimum_count} {interval} candles."
        )
    closes = []
    for candle in candles:
        try:
            close = float(candle[4])
        except (IndexError, TypeError, ValueError) as error:
            raise ValueError(f"{source_name} returned a malformed candle.") from error
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"{source_name} returned a non-positive or invalid close.")
        closes.append(close)
    try:
        last_open_time = datetime.fromtimestamp(float(candles[-1][0]) / 1000, timezone.utc)
    except (IndexError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{source_name} returned an invalid candle timestamp.") from error
    max_age = timedelta(days=9 if interval == "1w" else 2)
    candle_age = datetime.now(timezone.utc) - last_open_time
    if candle_age < timedelta(minutes=-5) or candle_age > max_age:
        raise ValueError(f"{source_name} returned stale or future-dated {interval} candles.")
    return closes


def fetch_yahoo_market_series(symbol, interval):
    period = "5y" if interval in ("1w", "1wk") else "2y"
    yahoo_interval = "1wk" if interval == "1w" else interval
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?range={period}&interval={yahoo_interval}"
    )
    response = fetch_json(url, timeout=30)
    chart = response.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo Finance returned no {interval} prices for {symbol}.")
    result = results[0]
    timestamps = result.get("timestamp", [])
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    candles = [
        [timestamp * 1000, None, None, None, close]
        for timestamp, close in zip(timestamps, closes)
        if close is not None
    ]
    return candles


def fetch_market_series(symbol, interval, limit):
    minimum_count = min(limit, 200) if limit >= 200 else 2
    try:
        end_time = int(time.time() * 1000)
        candles = fetch_json(
            f"{vb}?symbol={symbol}&interval={interval}&limit={limit}"
            f"&endTime={end_time}",
            cache_bust_param=None,
        )
        return validate_market_series(candles, interval, minimum_count, "Binance")
    except Exception as primary_error:
        print(
            f"Binance {symbol} {interval} data failed validation; "
            f"querying Yahoo Finance in real time: {primary_error}"
        )
        try:
            candles = fetch_yahoo_market_series(YAHOO_SYMBOLS[symbol], interval)
            return validate_market_series(
                candles, interval, minimum_count, "Yahoo Finance"
            )
        except Exception as backup_error:
            raise RuntimeError(
                f"No fresh valid {interval} market series for {symbol}; "
                f"Binance error: {primary_error}; Yahoo Finance error: {backup_error}"
            ) from backup_error


def validate_market_quote(price, change_pct, observed_at, symbol, source_name):
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"{source_name} returned an invalid {symbol} price.")
    if not math.isfinite(change_pct) or abs(change_pct) > 80:
        raise ValueError(f"{source_name} returned an implausible {symbol} 24h change.")
    age = datetime.now(timezone.utc) - observed_at
    if age < timedelta(minutes=-5) or age > timedelta(minutes=20):
        raise ValueError(f"{source_name} returned a stale or future-dated {symbol} quote.")
    return price, change_pct


def fetch_yahoo_quote(symbol):
    yahoo_symbol = YAHOO_SYMBOLS[symbol]
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(yahoo_symbol, safe='')}?range=2d&interval=1m"
    )
    response = fetch_json(url, timeout=30)
    chart = response.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise RuntimeError(f"Yahoo Finance returned no quote for {yahoo_symbol}.")
    result = chart["result"][0]
    meta = result.get("meta", {})
    quote_data = result.get("indicators", {}).get("quote", [{}])[0]
    timestamps = result.get("timestamp", [])
    closes = quote_data.get("close", [])
    valid_quotes = [
        (int(timestamp), float(close))
        for timestamp, close in zip(timestamps, closes)
        if close is not None and math.isfinite(float(close)) and float(close) > 0
    ]
    if not valid_quotes:
        raise ValueError(f"Yahoo Finance returned no valid {yahoo_symbol} prices.")
    latest_timestamp, price = valid_quotes[-1]
    previous_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
    if previous_close <= 0 or not math.isfinite(previous_close):
        raise ValueError(f"Yahoo Finance returned no valid prior close for {yahoo_symbol}.")
    change_pct = (price / previous_close - 1) * 100
    observed_at = datetime.fromtimestamp(latest_timestamp, timezone.utc)
    return validate_market_quote(
        price, change_pct, observed_at, symbol, "Yahoo Finance"
    )


def fetch_coingecko_quotes():
    ids = ",".join(COINGECKO_IDS.values())
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd&include_24hr_change=true"
        "&include_last_updated_at=true"
    )
    return fetch_json(url, timeout=30)


def fetch_market_quote(symbol, primary_quotes):
    try:
        quote_data = primary_quotes[COINGECKO_IDS[symbol]]
        price = float(quote_data["usd"])
        change_pct = float(quote_data["usd_24h_change"])
        observed_at = datetime.fromtimestamp(
            float(quote_data["last_updated_at"]), timezone.utc
        )
        return validate_market_quote(
            price, change_pct, observed_at, symbol, "CoinGecko"
        )
    except Exception as primary_error:
        print(
            f"CoinGecko {symbol} quote failed validation; querying Yahoo Finance "
            f"in real time: {primary_error}"
        )
        try:
            return fetch_yahoo_quote(symbol)
        except Exception as backup_error:
            raise RuntimeError(
                f"No fresh valid quote for {symbol}; CoinGecko error: {primary_error}; "
                f"Yahoo Finance error: {backup_error}"
            ) from backup_error


def calc_sma(arr, p):
    sl = arr[-p:]
    return f"{sum(sl)/len(sl):.2f}" if sl else "0.00"

def calc_rsi(arr, p=14):
    sl = arr[-(p+1):]
    if len(sl) < 2: return "50.00"
    gains = sum(max(0, sl[i]-sl[i-1]) for i in range(1, len(sl)))
    losses = sum(max(0, sl[i-1]-sl[i]) for i in range(1, len(sl)))
    rs = (gains/p) / (1 if losses == 0 else losses/p)
    return f"{100 - (100/(1+rs)):.2f}"


def validate_daily_history(candles, source_name, minimum_count=200):
    if not isinstance(candles, list) or len(candles) < minimum_count:
        raise ValueError(
            f"{source_name} returned fewer than {minimum_count} daily candles."
        )
    history = []
    previous_timestamp = None
    for candle in candles:
        try:
            timestamp = float(candle["timestamp"])
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{source_name} returned a malformed daily candle.") from error
        if (
            not all(math.isfinite(value) for value in (timestamp, high, low, close))
            or min(high, low, close) <= 0
            or high < close
            or low > close
            or (previous_timestamp is not None and timestamp <= previous_timestamp)
        ):
            raise ValueError(f"{source_name} returned an invalid daily candle.")
        observed_at = datetime.fromtimestamp(timestamp, timezone.utc)
        if observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError(f"{source_name} returned a future-dated daily candle.")
        history.append({
            "date": observed_at.date(),
            "timestamp": timestamp,
            "high": high,
            "low": low,
            "close": close,
        })
        previous_timestamp = timestamp
    if datetime.now(timezone.utc) - datetime.fromtimestamp(
        history[-1]["timestamp"], timezone.utc
    ) > timedelta(days=2):
        raise ValueError(f"{source_name} returned stale daily candles.")
    return history


def fetch_yahoo_daily_history(symbol):
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?range=2y&interval=1d"
    )
    response = fetch_json(url, timeout=30)
    chart = response.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo Finance returned no daily prices for {symbol}.")
    result = results[0]
    timestamps = result.get("timestamp", [])
    quote_data = result.get("indicators", {}).get("quote", [{}])[0]
    highs = quote_data.get("high", [])
    lows = quote_data.get("low", [])
    closes = quote_data.get("close", [])
    candles = [
        {
            "timestamp": timestamp,
            "high": high,
            "low": low,
            "close": close,
        }
        for timestamp, high, low, close in zip(timestamps, highs, lows, closes)
        if high is not None and low is not None and close is not None
    ]
    return candles[-365:]


def fetch_daily_history(symbol):
    try:
        end_time = int(time.time() * 1000)
        candles = fetch_json(
            f"{vb}?symbol={symbol}&interval=1d&limit=365&endTime={end_time}",
            timeout=30,
            cache_bust_param=None,
        )
        normalized_candles = [
            {
                "timestamp": float(candle[0]) / 1000,
                "high": candle[2],
                "low": candle[3],
                "close": candle[4],
            }
            for candle in candles
        ]
        return validate_daily_history(normalized_candles, "Binance")
    except Exception as primary_error:
        print(
            f"Binance {symbol} daily history failed validation; "
            f"querying Yahoo Finance in real time: {primary_error}"
        )
        try:
            yahoo_symbol = YAHOO_SYMBOLS[symbol]
            candles = fetch_yahoo_daily_history(yahoo_symbol)
            return validate_daily_history(candles, "Yahoo Finance")
        except Exception as backup_error:
            raise RuntimeError(
                f"No fresh valid daily history for {symbol}; "
                f"Binance error: {primary_error}; Yahoo Finance error: {backup_error}"
            ) from backup_error


def calculate_rsi_series(closes, period=14):
    if period < 1:
        raise ValueError("RSI period must be positive.")
    values = [None] * len(closes)
    if len(closes) <= period:
        return values
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    average_gain = sum(max(change, 0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0) for change in changes[:period]) / period

    def rsi_value(gain, loss):
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100 - 100 / (1 + gain / loss)

    values[period] = rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(closes)):
        change = changes[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period
        values[index] = rsi_value(average_gain, average_loss)
    return values


def calculate_sma_series(closes, period):
    values = [None] * len(closes)
    if period < 1:
        raise ValueError("SMA period must be positive.")
    running_total = 0.0
    for index, close in enumerate(closes):
        running_total += close
        if index >= period:
            running_total -= closes[index - period]
        if index >= period - 1:
            values[index] = running_total / period
    return values


def calculate_market_structure(history, spot_price, as_of_date):
    if not history:
        raise ValueError("Daily history is required for market-structure analysis.")
    closes = [row["close"] for row in history]
    last_90_days = [
        row for row in history
        if as_of_date - timedelta(days=90) < row["date"] <= as_of_date
    ]
    last_30_days = [
        row for row in history
        if as_of_date - timedelta(days=30) < row["date"] <= as_of_date
    ]
    if not last_90_days or not last_30_days or len(closes) < 200:
        raise ValueError("Insufficient fresh daily history for technical indicators.")

    swing_high = max(last_90_days, key=lambda row: row["high"])
    if spot_price > swing_high["high"]:
        peak_price = spot_price
        peak_date = as_of_date
    else:
        peak_price = swing_high["high"]
        peak_date = swing_high["date"]

    running_peak = 0.0
    maximum_drawdown = 0.0
    drawdown_peak_date = None
    drawdown_trough_date = None
    for row in last_90_days:
        if row["high"] >= running_peak:
            running_peak = row["high"]
            drawdown_peak_date = row["date"]
        drawdown = row["low"] / running_peak - 1
        if drawdown < maximum_drawdown:
            maximum_drawdown = drawdown
            drawdown_trough_date = row["date"]

    sma_100 = sum(closes[-100:]) / 100
    sma_200 = sum(closes[-200:]) / 200
    return {
        "spot_price": spot_price,
        "swing_high": peak_price,
        "swing_high_date": peak_date,
        "days_since_high": max(0, (as_of_date - peak_date).days),
        "current_drawdown_pct": min(0.0, (spot_price / peak_price - 1) * 100),
        "maximum_drawdown_pct": maximum_drawdown * 100,
        "maximum_drawdown_peak_date": drawdown_peak_date,
        "maximum_drawdown_trough_date": drawdown_trough_date,
        "support": min(row["low"] for row in last_30_days),
        "resistance": max(row["high"] for row in last_30_days),
        "sma_100": sma_100,
        "sma_200": sma_200,
        "sma_100_deviation_pct": (spot_price / sma_100 - 1) * 100,
        "sma_200_deviation_pct": (spot_price / sma_200 - 1) * 100,
        "rsi_series": calculate_rsi_series(closes),
    }


def find_rsi_oversold_events(history, rsi_series, as_of_date, threshold=30):
    cutoff_date = as_of_date - timedelta(days=180)
    events = []
    active_event = None
    for row, rsi_value in zip(history, rsi_series):
        if rsi_value is None:
            continue
        if rsi_value < threshold:
            if active_event is None:
                active_event = {
                    "start_date": row["date"],
                    "low_date": row["date"],
                    "low_rsi": rsi_value,
                }
            elif rsi_value < active_event["low_rsi"]:
                active_event["low_date"] = row["date"]
                active_event["low_rsi"] = rsi_value
        elif active_event is not None:
            active_event["end_date"] = row["date"]
            events.append(active_event)
            active_event = None
    if active_event is not None:
        active_event["end_date"] = history[-1]["date"]
        events.append(active_event)
    return [
        event for event in events
        if cutoff_date <= event["start_date"] <= as_of_date
    ]


def fetch_okx_ratio(endpoint, params, label):
    try:
        query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
        response = fetch_json(
            f"https://www.okx.com/api/v5/rubik/stat/contracts/{endpoint}?{query}",
            timeout=10,
        )
        if not isinstance(response, dict) or response.get("code") != "0":
            raise ValueError(f"OKX returned an unsuccessful {label} response.")
        records = response.get("data")
        if not isinstance(records, list) or not records:
            raise ValueError(f"OKX returned no {label} records.")
        valid_records = []
        for record in records:
            try:
                values = record.get("value") if isinstance(record, dict) else record
                timestamp_ms, ratio = values
                timestamp = datetime.fromtimestamp(
                    float(timestamp_ms) / 1000, timezone.utc
                )
                ratio = float(ratio)
                age = datetime.now(timezone.utc) - timestamp
                if (
                    not math.isfinite(ratio)
                    or ratio <= 0
                    or age < timedelta(minutes=-5)
                    or age > timedelta(hours=36)
                ):
                    continue
                valid_records.append((timestamp, ratio))
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
        if not valid_records:
            raise ValueError(f"OKX returned no fresh valid {label} records.")
        timestamp, ratio = max(valid_records, key=lambda record: record[0])
        return {"source": "OKX", "label": label, "timestamp": timestamp, "ratio": ratio}
    except Exception as error:
        print(f"OKX {label} unavailable; omitting this ratio: {error}")
        return None


def fetch_bybit_account_ratio():
    try:
        response = fetch_json(
            "https://api.bybit.com/v5/market/account-ratio"
            "?category=linear&symbol=BTCUSDT&period=1d&limit=1",
            timeout=10,
        )
        if not isinstance(response, dict) or response.get("retCode") != 0:
            raise ValueError("Bybit returned an unsuccessful account-ratio response.")
        records = response.get("result", {}).get("list", [])
        if not records:
            raise ValueError("Bybit returned no account-ratio records.")
        record = records[0]
        timestamp = datetime.fromtimestamp(
            float(record["timestamp"]) / 1000, timezone.utc
        )
        buy_ratio = float(record["buyRatio"])
        sell_ratio = float(record["sellRatio"])
        age = datetime.now(timezone.utc) - timestamp
        if (
            not math.isfinite(buy_ratio)
            or not math.isfinite(sell_ratio)
            or buy_ratio <= 0
            or sell_ratio <= 0
            or age < timedelta(minutes=-5)
            or age > timedelta(hours=36)
        ):
            raise ValueError("Bybit account-ratio data is invalid or stale.")
        return {
            "source": "Bybit",
            "label": "全體帳戶多空比",
            "timestamp": timestamp,
            "ratio": buy_ratio / sell_ratio,
        }
    except Exception as error:
        print(f"Bybit account ratio unavailable; omitting it: {error}")
        return None


def fetch_trader_position_metrics():
    all_accounts = fetch_okx_ratio(
        "long-short-account-ratio",
        {"ccy": "BTC", "period": "1D"},
        "全體帳戶多空人數比",
    )
    top_accounts = fetch_okx_ratio(
        "long-short-account-ratio-contract-top-trader",
        {"instId": "BTC-USDT-SWAP", "period": "1D"},
        "前列交易者帳戶多空比",
    )
    top_positions = fetch_okx_ratio(
        "long-short-position-ratio-contract-top-trader",
        {"instId": "BTC-USDT-SWAP", "period": "1D"},
        "前列交易者持倉多空比",
    )
    if all_accounts is None:
        all_accounts = fetch_bybit_account_ratio()
    return {
        "all_accounts": all_accounts,
        "top_accounts": top_accounts,
        "top_positions": top_positions,
    }


def fetch_okx_spot_volume_metrics():
    try:
        response = fetch_json(
            "https://www.okx.com/api/v5/market/candles"
            "?instId=BTC-USDT&bar=1D&limit=100",
            timeout=10,
        )
        if not isinstance(response, dict) or response.get("code") != "0":
            raise ValueError("OKX returned an unsuccessful BTC spot-candle response.")
        candles = response.get("data")
        if not isinstance(candles, list):
            raise ValueError("OKX returned malformed BTC spot candles.")
        daily = []
        for candle in candles:
            try:
                timestamp = datetime.fromtimestamp(
                    float(candle[0]) / 1000, timezone.utc
                )
                close = float(candle[4])
                quote_volume = float(candle[7])
                age = datetime.now(timezone.utc) - timestamp
                if (
                    str(candle[8]) != "1"
                    or not math.isfinite(close)
                    or close <= 0
                    or not math.isfinite(quote_volume)
                    or quote_volume <= 0
                    or age < timedelta(0)
                    or age > timedelta(days=65)
                ):
                    continue
                daily.append({
                    "date": timestamp.date(),
                    "close": close,
                    "quote_volume": quote_volume,
                })
            except (IndexError, TypeError, ValueError, OverflowError, OSError):
                continue
        daily.sort(key=lambda candle: candle["date"])
        if len(daily) < 60 or len({candle["date"] for candle in daily}) != len(daily):
            raise ValueError("OKX returned fewer than 60 valid completed daily candles.")
        if (datetime.now(timezone.utc).date() - daily[-1]["date"]).days > 2:
            raise ValueError("OKX BTC spot-candle history is stale.")

        recent = daily[-30:]
        previous = daily[-60:-30]
        recent_volume = sum(candle["quote_volume"] for candle in recent)
        previous_volume = sum(candle["quote_volume"] for candle in previous)
        if previous_volume <= 0:
            raise ValueError("OKX BTC spot volume baseline is non-positive.")
        return {
            "source": "OKX BTC-USDT 現貨",
            "start_date": recent[0]["date"],
            "end_date": recent[-1]["date"],
            "recent_volume_quote": recent_volume,
            "previous_volume_quote": previous_volume,
            "volume_change_pct": (recent_volume / previous_volume - 1) * 100,
            "price_change_pct": (recent[-1]["close"] / recent[0]["close"] - 1) * 100,
        }
    except Exception as error:
        print(f"OKX BTC spot-volume data unavailable; omitting it: {error}")
        return None


def fetch_blockchain_chart_metrics(chart_name, label):
    try:
        response = fetch_json(
            f"https://api.blockchain.info/charts/{chart_name}"
            "?timespan=70days&format=json",
            timeout=10,
        )
        if not isinstance(response, dict) or not isinstance(response.get("values"), list):
            raise ValueError(f"Blockchain.com returned malformed {label} history.")
        daily = {}
        for item in response["values"]:
            try:
                timestamp = datetime.fromtimestamp(
                    float(item["x"]), timezone.utc
                )
                value = float(item["y"])
                if not math.isfinite(value) or value < 0:
                    continue
                daily[timestamp.date()] = value
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
        if len(daily) < 40:
            raise ValueError(f"Blockchain.com returned insufficient {label} history.")
        latest_date = max(daily)
        age = datetime.now(timezone.utc) - datetime.combine(
            latest_date, datetime.min.time(), timezone.utc
        )
        if age < timedelta(0) or age > timedelta(days=3):
            raise ValueError(f"Blockchain.com {label} history is stale.")
        recent_start = latest_date - timedelta(days=29)
        baseline_start = latest_date - timedelta(days=59)
        recent = [value for day, value in daily.items() if recent_start <= day <= latest_date]
        previous = [
            value for day, value in daily.items()
            if baseline_start <= day < recent_start
        ]
        if len(recent) < 20 or len(previous) < 20:
            raise ValueError(f"Blockchain.com returned incomplete 30-day {label} windows.")
        recent_average = sum(recent) / len(recent)
        previous_average = sum(previous) / len(previous)
        if previous_average <= 0:
            raise ValueError(f"Blockchain.com {label} baseline is non-positive.")
        return {
            "source": "Blockchain.com",
            "label": label,
            "start_date": recent_start,
            "end_date": latest_date,
            "recent_average": recent_average,
            "previous_average": previous_average,
            "change_pct": (recent_average / previous_average - 1) * 100,
        }
    except Exception as error:
        print(f"Blockchain.com {label} unavailable; omitting it: {error}")
        return None


def fetch_onchain_activity_metrics():
    return {
        "estimated_transfer_volume": fetch_blockchain_chart_metrics(
            "estimated-transaction-volume-usd", "估算鏈上交易量"
        ),
        "transaction_count": fetch_blockchain_chart_metrics(
            "n-transactions", "鏈上交易筆數"
        ),
    }


def describe_long_short_ratio(metric):
    if metric is None:
        return "未取得通過時效驗證的公開多空比資料，略過，不推測"
    ratio = metric["ratio"]
    direction = "偏多" if ratio > 1 else "偏空" if ratio < 1 else "中性"
    return (
        f"{metric['source']} {metric['label']} {ratio:.3f}:1（{direction}，"
        f"截至 {metric['timestamp'].strftime('%Y-%m-%d %H:%M UTC')}）"
    )


def format_trader_position_report(metrics):
    if not metrics:
        return "未取得通過時效驗證的多空比資料，略過，不推測。"
    reports = [
        describe_long_short_ratio(metrics.get("all_accounts")),
        describe_long_short_ratio(metrics.get("top_accounts")),
        describe_long_short_ratio(metrics.get("top_positions")),
    ]
    return (
        "；".join(reports)
        + "。前列交易者是交易所排名代理，不等同所有鏈上巨鯨；"
        "全體帳戶也不是散戶專屬樣本，多空比不代表現貨買入或吸籌。"
    )


def format_spot_volume_report(metrics):
    if metrics is None:
        return "未取得足夠且新鮮的 OKX BTC 現貨成交量資料，略過，不推測。"
    activity = (
        "放量下跌"
        if metrics["price_change_pct"] < 0 and metrics["volume_change_pct"] >= 20
        else "縮量下跌"
        if metrics["price_change_pct"] < 0 and metrics["volume_change_pct"] <= -20
        else "價格與成交量訊號不一致"
    )
    return (
        f"{metrics['source']}近30日報價成交額 USDT {metrics['recent_volume_quote']:,.0f}，"
        f"較前30日 {metrics['volume_change_pct']:+.2f}%；"
        f"近30日價格變化 {metrics['price_change_pct']:+.2f}%，訊號為「{activity}」。"
    )


def format_onchain_activity_report(metrics):
    reports = []
    for key in ("estimated_transfer_volume", "transaction_count"):
        metric = metrics.get(key) if metrics else None
        if metric is None:
            reports.append("未取得足夠的新鮮鏈上歷史資料，略過，不推測")
            continue
        if key == "estimated_transfer_volume":
            value_text = f"估算鏈上交易量日均 US${metric['recent_average']:,.0f}"
        else:
            value_text = f"鏈上交易筆數日均 {metric['recent_average']:,.0f} 筆"
        reports.append(
            f"{value_text}，較前30日 {metric['change_pct']:+.2f}%"
            f"（截至 {metric['end_date']}）"
        )
    return (
        "；".join(reports)
        + "。這些是全網活動代理，無法識別地址身分或判定交易所淨流入／流出。"
    )


def format_chip_sentiment_report(fng_value, market_structure, spot_volume):
    if fng_value is None:
        return "恐慌貪婪指數未取得，無法做情緒交叉判讀。"
    if market_structure is None:
        return "近期回撤資料未取得，無法做情緒交叉判讀。"
    drawdown_pct = market_structure["current_drawdown_pct"]
    if spot_volume is None:
        return (
            f"恐慌貪婪指數 {fng_value}；目前相對90日高點回撤 {drawdown_pct:.2f}%；"
            "現貨成交量資料不足，無法區分放量恐慌或縮量沉澱。"
        )
    volume_change_pct = spot_volume["volume_change_pct"]
    if fng_value <= 24 and drawdown_pct <= -10 and volume_change_pct >= 20:
        interpretation = (
            "符合「極度恐慌＋回撤至少10%＋成交額放大至少20%」的恐慌換手特徵；"
            "不能據此斷定是散戶割肉或巨鯨吸籌。"
        )
    elif fng_value <= 24 and drawdown_pct < 0 and volume_change_pct <= -20:
        interpretation = (
            "符合「極度恐慌＋回撤＋成交額萎縮至少20%」的縮量沉澱特徵；"
            "不能據此證明長期持有者惜售或巨鯨吸籌。"
        )
    else:
        interpretation = (
            "未同時符合上述放量投降或縮量沉澱門檻，籌碼方向訊號混合，"
            "不能判定散戶割肉或巨鯨吸籌。"
        )
    return (
        f"恐慌貪婪指數 {fng_value}；目前相對90日高點回撤 {drawdown_pct:.2f}%；"
        f"OKX現貨近30日成交額變化 {volume_change_pct:+.2f}%。"
        f"{interpretation}"
    )


def fetch_stablecoin_market_cap_changes():
    url = "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1"
    try:
        response = fetch_json(url, timeout=15)
        if not isinstance(response, list) or not response:
            raise ValueError("DefiLlama returned no stablecoin history.")
        daily_totals = {}
        for item in response:
            try:
                timestamp = float(item["date"])
                components = item["totalCirculatingUSD"]
                if not isinstance(components, dict) or not components:
                    continue
                amounts = [float(amount) for amount in components.values()]
                if not all(math.isfinite(amount) and amount >= 0 for amount in amounts):
                    continue
                observed_at = datetime.fromtimestamp(timestamp, timezone.utc)
                daily_totals[observed_at.date()] = sum(amounts)
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
        if not daily_totals:
            raise ValueError("DefiLlama returned no valid stablecoin market-cap records.")
        latest_date = max(daily_totals)
        latest_timestamp = datetime.combine(
            latest_date, datetime.min.time(), timezone.utc
        )
        age = datetime.now(timezone.utc) - latest_timestamp
        if age < timedelta(0) or age > timedelta(hours=36):
            raise ValueError("DefiLlama stablecoin market-cap data is stale.")
        latest_total = daily_totals[latest_date]
        if latest_total <= 0:
            raise ValueError("DefiLlama returned a non-positive stablecoin market cap.")

        changes = {}
        for days in (7, 30):
            target_date = latest_date - timedelta(days=days)
            if target_date not in daily_totals:
                continue
            previous_date = target_date
            previous_total = daily_totals[previous_date]
            if previous_total <= 0:
                continue
            amount = latest_total - previous_total
            changes[days] = {
                "start_date": previous_date,
                "end_date": latest_date,
                "start_total": previous_total,
                "end_total": latest_total,
                "change_amount": amount,
                "change_pct": amount / previous_total * 100,
            }
        return changes
    except Exception as error:
        print(f"Stablecoin market-cap data unavailable; omitting it: {error}")
        return None


def format_market_structure_report(name, metrics, spot_price):
    drawdown_dates = (
        f"（{metrics['maximum_drawdown_peak_date']} 至 "
        f"{metrics['maximum_drawdown_trough_date']}）"
        if metrics["maximum_drawdown_peak_date"] is not None
        else "（近90日未觀察到回撤）"
    )
    return (
        f"{name}：現價 ${spot_price:,.2f}；90日高點 ${metrics['swing_high']:,.2f}"
        f"（{metrics['swing_high_date']}），距該高點 {metrics['days_since_high']} 天，"
        f"目前回撤 {metrics['current_drawdown_pct']:.2f}%；"
        f"90日最大回撤 {metrics['maximum_drawdown_pct']:.2f}%"
        f"{drawdown_dates}；"
        f"30日支撐 ${metrics['support']:,.2f}、壓力 ${metrics['resistance']:,.2f}；"
        f"現價相對100日線 {metrics['sma_100_deviation_pct']:+.2f}%、"
        f"相對200日線 {metrics['sma_200_deviation_pct']:+.2f}%"
    )


def format_stablecoin_market_cap_report(changes):
    if changes is None:
        return "未取得通過時效與資料驗證的穩定幣市值資料，略過，不推測。"
    reports = []
    for days in (7, 30):
        change = changes.get(days)
        if change is None:
            reports.append(f"近{days}日資料不足，略過")
            continue
        direction = "增加" if change["change_amount"] >= 0 else "減少"
        reports.append(
            f"近{days}日{direction} US${abs(change['change_amount']):,.0f}"
            f"（{change['change_pct']:+.2f}%）"
        )
    return (
        f"截至 {max(item['end_date'] for item in changes.values())}："
        + "；".join(reports)
        + "。市值變化不等同於實際流入加密資產。"
        if changes else "未取得足夠的穩定幣歷史資料，略過，不推測。"
    )


def format_oversold_report(events, rsi_series):
    latest_rsi = next(
        (value for value in reversed(rsi_series) if value is not None), None
    )
    if latest_rsi is None:
        return "14日 RSI 資料不足，略過超賣判讀。"
    event_dates = "、".join(
        f"{event['start_date']}（低點 {event['low_date']}，RSI {event['low_rsi']:.1f}）"
        for event in events
    ) or "無"
    state = "目前低於30，屬超賣區" if latest_rsi < 30 else "目前未低於30"
    return (
        f"目前14日 RSI {latest_rsi:.2f}，{state}；近180日 RSI 跌破30共"
        f"{len(events)}個區段，起始日期與區段低點：{event_dates}。"
        "超賣僅是動能狀態描述，不代表價格已見底。"
    )


def get_chart_font():
    font_paths = (
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\mingliu.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    )
    font_path = next((path for path in font_paths if os.path.isfile(path)), None)
    if font_path is None:
        raise RuntimeError("No supported Traditional Chinese chart font is installed.")
    return FontProperties(fname=font_path)


def generate_technical_charts(btc_history, btc_metrics, oversold_events):
    chart_dir = tempfile.TemporaryDirectory(prefix="crypto_technical_charts_")
    try:
        font = get_chart_font()
        closes = [row["close"] for row in btc_history]
        sma_100 = calculate_sma_series(closes, 100)
        sma_200 = calculate_sma_series(closes, 200)
        rsi_series = btc_metrics["rsi_series"]
        chart_color = "#0B0F19"
        panel_color = "#101827"
        grid_color = "#263244"
        price_path = os.path.join(chart_dir.name, "btc_price_structure.png")
        rsi_path = os.path.join(chart_dir.name, "btc_rsi_oversold.png")

        visible_start = max(0, len(btc_history) - 90)
        visible = btc_history[visible_start:]
        dates = [row["date"] for row in visible]
        fig, ax = plt.subplots(figsize=(13, 7.5), dpi=220)
        fig.patch.set_facecolor(chart_color)
        ax.set_facecolor(panel_color)
        ax.plot(
            dates, closes[visible_start:], color="#22D3EE", linewidth=2.2,
            label="BTC 收盤價",
        )
        ax.plot(
            dates, sma_100[visible_start:], color="#FBBF24", linewidth=1.8,
            label="100 日線",
        )
        ax.plot(
            dates, sma_200[visible_start:], color="#A78BFA", linewidth=1.8,
            label="200 日線",
        )
        ax.axhline(
            btc_metrics["resistance"], color="#F87171", linestyle="--",
            linewidth=1.3, label=f"30 日壓力 ${btc_metrics['resistance']:,.0f}",
        )
        ax.axhline(
            btc_metrics["support"], color="#34D399", linestyle="--",
            linewidth=1.3, label=f"30 日支撐 ${btc_metrics['support']:,.0f}",
        )
        ax.set_title(
            "BTC 價格結構與關鍵多空攻防圖", color="#F8FAFC",
            fontsize=19, fontweight="bold", fontproperties=font, pad=16,
        )
        ax.text(
            0.015, 0.97,
            f"現價 ${btc_metrics['spot_price']:,.0f}"
            f"  |  距 90 日高點 {btc_metrics['days_since_high']} 天"
            f"  |  回撤 {btc_metrics['current_drawdown_pct']:.2f}%"
            f"  |  最大回撤 {btc_metrics['maximum_drawdown_pct']:.2f}%",
            transform=ax.transAxes, va="top", color="#F8FAFC", fontsize=10,
            fontproperties=font,
        )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda value, _: f"${value:,.0f}")
        )
        ax.tick_params(colors="#CBD5E1", labelsize=9)
        ax.grid(color=grid_color, linewidth=0.65)
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.legend(
            loc="upper left", bbox_to_anchor=(0, 0.90), ncol=2,
            facecolor=panel_color, edgecolor="none", labelcolor="#E2E8F0",
            prop=font, fontsize=9,
        )
        fig.tight_layout()
        fig.savefig(price_path, facecolor=chart_color, dpi=220)
        plt.close(fig)

        visible_start = max(0, len(btc_history) - 180)
        visible_history = btc_history[visible_start:]
        visible_rsi = rsi_series[visible_start:]
        rsi_dates = [
            row["date"] for row, value in zip(visible_history, visible_rsi)
            if value is not None
        ]
        rsi_values = [value for value in visible_rsi if value is not None]
        fig, ax = plt.subplots(figsize=(13, 7.5), dpi=220)
        fig.patch.set_facecolor(chart_color)
        ax.set_facecolor(panel_color)
        ax.axhspan(70, 100, color="#EF4444", alpha=0.14)
        ax.axhspan(0, 30, color="#22C55E", alpha=0.14)
        ax.plot(rsi_dates, rsi_values, color="#22D3EE", linewidth=2.0, label="14 日 RSI")
        ax.axhline(70, color="#F87171", linestyle="--", linewidth=1.2, label="70 超買")
        ax.axhline(30, color="#34D399", linestyle="--", linewidth=1.2, label="30 超賣")
        for index, event in enumerate(oversold_events):
            ax.scatter(
                event["low_date"], event["low_rsi"], s=62,
                color="#FBBF24", edgecolors="#0B0F19", linewidths=1.0,
                zorder=5, label="歷史超賣區段低點" if index == 0 else None,
            )
        latest_rsi = next(
            (value for value in reversed(rsi_series) if value is not None), None
        )
        if latest_rsi is not None:
            ax.scatter(
                btc_history[-1]["date"], latest_rsi, s=84, marker="D",
                color="#FB7185", edgecolors="#F8FAFC", linewidths=0.8,
                zorder=6, label=f"最新 RSI {latest_rsi:.1f}",
            )
        ax.set_ylim(0, 100)
        ax.set_title(
            "BTC 14 日 RSI 歷史超賣與底部對比圖", color="#F8FAFC",
            fontsize=19, fontweight="bold", fontproperties=font, pad=16,
        )
        ax.text(
            0.015, 0.97,
            f"近 180 日超賣區段 {len(oversold_events)} 次；"
            "歷史低點不代表未來價格底部",
            transform=ax.transAxes, va="top", color="#F8FAFC", fontsize=10,
            fontproperties=font,
        )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.tick_params(colors="#CBD5E1", labelsize=9)
        ax.grid(color=grid_color, linewidth=0.65)
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.legend(
            loc="upper right", facecolor=panel_color, edgecolor="none",
            labelcolor="#E2E8F0", prop=font, fontsize=9,
        )
        fig.tight_layout()
        fig.savefig(rsi_path, facecolor=chart_color, dpi=220)
        plt.close(fig)
        return {"price": price_path, "rsi": rsi_path}, chart_dir
    except Exception:
        chart_dir.cleanup()
        raise


syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "ZECUSDT"]
cdata = {}
spot_prices = {}
daily_histories = {}
try:
    primary_quotes = fetch_coingecko_quotes()
except Exception as error:
    print(f"CoinGecko batch quotes unavailable; each coin will use the backup API: {error}")
    primary_quotes = {}
for s in syms:
    name = s.replace("USDT", "")
    if s in ("BTCUSDT", "ETHUSDT"):
        daily_histories[name] = fetch_daily_history(s)
        cl = [row["close"] for row in daily_histories[name]]
        rsi_series = calculate_rsi_series(cl)
        rsi = f"{rsi_series[-1]:.2f}" if rsi_series[-1] is not None else ""
    else:
        lim = 210 if s in ["SOLUSDT", "XRPUSDT"] else 5
        cl = fetch_market_series(s, "1d", lim)
        rsi = calc_rsi(cl, 14) if lim == 210 else ""
    spot_prices[s.replace("USDT", "")], pct = fetch_market_quote(s, primary_quotes)
    cdata[name] = (cl, pct, rsi)

btc_w = fetch_market_series("BTCUSDT", "1w", 210)
btc_cl, btc_pct, btc_rsi = cdata["BTC"]
eth_cl, eth_pct, eth_rsi = cdata["ETH"]
cur_p = f"{spot_prices['BTC']:.2f}"
data_fetched_at = datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M 台灣時間")
btc_metrics = calculate_market_structure(
    daily_histories["BTC"], spot_prices["BTC"], now_tw.date()
)
eth_metrics = calculate_market_structure(
    daily_histories["ETH"], spot_prices["ETH"], now_tw.date()
)
btc_oversold_events = find_rsi_oversold_events(
    daily_histories["BTC"], btc_metrics["rsi_series"], now_tw.date()
)
ma100d = f"{btc_metrics['sma_100']:.2f}"
ma200d = f"{btc_metrics['sma_200']:.2f}"
ma100w, ma200w = calc_sma(btc_w, 100), calc_sma(btc_w, 200)
eth_ma100d = f"{eth_metrics['sma_100']:.2f}"
eth_ma200d = f"{eth_metrics['sma_200']:.2f}"
stablecoin_changes = fetch_stablecoin_market_cap_changes()
technical_chart_paths, technical_chart_dir = generate_technical_charts(
    daily_histories["BTC"], btc_metrics, btc_oversold_events
)
btc_technical_report = format_market_structure_report(
    "BTC", btc_metrics, spot_prices["BTC"]
)
eth_technical_report = format_market_structure_report(
    "ETH", eth_metrics, spot_prices["ETH"]
)
btc_oversold_report = format_oversold_report(
    btc_oversold_events, btc_metrics["rsi_series"]
)
stablecoin_report = format_stablecoin_market_cap_report(stablecoin_changes)
trader_position_metrics = fetch_trader_position_metrics()
okx_spot_volume_metrics = fetch_okx_spot_volume_metrics()
onchain_activity_metrics = fetch_onchain_activity_metrics()
trader_position_report = format_trader_position_report(trader_position_metrics)
spot_volume_report = format_spot_volume_report(okx_spot_volume_metrics)
onchain_activity_report = format_onchain_activity_report(onchain_activity_metrics)
chip_sentiment_report = format_chip_sentiment_report(
    int(today_fng["value"]) if today_fng else None,
    btc_metrics,
    okx_spot_volume_metrics,
)

def fetch_yahoo_daily_closes(symbol, start_date, end_date):
    start_ts = calendar.timegm(
        datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).timetuple()
    )
    end_date_exclusive = end_date + timedelta(days=1)
    end_ts = calendar.timegm(
        datetime(
            end_date_exclusive.year,
            end_date_exclusive.month,
            end_date_exclusive.day,
            tzinfo=timezone.utc,
        ).timetuple()
    )
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?period1={start_ts}&period2={end_ts}&interval=1d"
    )
    response = fetch_json(url, timeout=30)
    chart = response.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo Finance returned no daily prices for {symbol}.")
    result = results[0]
    exchange_timezone = ZoneInfo(result.get("meta", {}).get("exchangeTimezoneName", "UTC"))
    closes = result["indicators"]["quote"][0]["close"]
    return {
        datetime.fromtimestamp(timestamp, timezone.utc)
        .astimezone(exchange_timezone)
        .date(): float(close)
        for timestamp, close in zip(result.get("timestamp", []), closes)
        if close is not None
    }


def latest_close_on_or_before(closes, target_date, max_age_days=7):
    available_dates = [date for date in closes if date <= target_date]
    if not available_dates:
        return None
    latest_date = max(available_dates)
    if (target_date - latest_date).days > max_age_days:
        return None
    return closes[latest_date]


def scheduled_dca_dates(start_date, end_date):
    dates = []
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        for day in (25,):
            try:
                scheduled_date = datetime(year, month, day).date()
            except ValueError:
                continue
            if start_date <= scheduled_date <= end_date:
                dates.append(scheduled_date)
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return dates


def update_dca_portfolio(current_btc_usd):
    current_btc_usd = float(current_btc_usd)
    if not math.isfinite(current_btc_usd) or current_btc_usd <= 0:
        raise ValueError("current_btc_usd must be a positive finite price.")
    today = now_tw.date()
    start_date = datetime(2025, 10, 25).date()
    schedule_dates = scheduled_dca_dates(start_date, today)
    ledger_path = "dca_portfolio.json"

    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as ledger_file:
            portfolio = json.load(ledger_file)
        if not isinstance(portfolio, dict) or not isinstance(portfolio.get("records"), list):
            raise ValueError(f"{ledger_path} must contain a JSON object with a records list.")
    else:
        portfolio = {"records": []}

    records = portfolio["records"]
    if any(not isinstance(record, dict) or not record.get("date") for record in records):
        raise ValueError(f"{ledger_path} contains a record without a date.")
    record_dates = []
    for record in records:
        try:
            record_dates.append(datetime.fromisoformat(record["date"]).date())
        except (TypeError, ValueError) as error:
            raise ValueError(f"{ledger_path} contains an invalid DCA date.") from error
    expected_record_fields = {
        "date", "twd_invested", "btc_price_usd", "btc_price_twd",
        "twd_rate", "btc_bought",
    }
    if records and (
        portfolio.get("start_date") != start_date.isoformat()
        or portfolio.get("schedule_days") != [25]
        or any(
            date.day != 25 or date < start_date or date not in schedule_dates
            or record["date"] != date.isoformat()
            for record, date in zip(records, record_dates)
        )
        or any(not expected_record_fields.issubset(record) for record in records)
        or any(record.get("twd_invested") != 10000 for record in records)
    ):
        print("Resetting legacy DCA records to the monthly 25th-only schedule.")
        portfolio = {"records": []}
        records = portfolio["records"]
    existing_dates = {record["date"] for record in records}
    if len(existing_dates) != len(records):
        raise ValueError(f"{ledger_path} contains duplicate DCA dates.")
    missing_dates = [date for date in schedule_dates if date.isoformat() not in existing_dates]
    historical_fx = {}
    historical_fx_failed = False
    if missing_dates:
        first_missing = min(missing_dates)
        try:
            historical_fx = fetch_yahoo_daily_closes(
                "USDTWD=X", first_missing - timedelta(days=7), today
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
            historical_fx_failed = True
            print(f"Historical USDTWD rate lookup failed; using 32.5 where unavailable: {error}")

        historical_btc = fetch_yahoo_daily_closes("BTC-USD", min(missing_dates), today)
        for deposit_date in missing_dates:
            btc_price = historical_btc.get(deposit_date)
            if btc_price is None or btc_price <= 0:
                raise RuntimeError(
                    f"Missing valid BTC-USD historical close for {deposit_date.isoformat()}."
                )
            twd_rate = latest_close_on_or_before(historical_fx, deposit_date)
            if twd_rate is None or twd_rate <= 0:
                if not historical_fx_failed:
                    print(
                        f"No recent USDTWD close for {deposit_date.isoformat()}; "
                        "using the fallback rate 32.5."
                    )
                twd_rate = 32.5
            usd_invested = 10000 / twd_rate
            btc_bought = round(usd_invested / btc_price, 8)
            records.append({
                "date": deposit_date.isoformat(),
                "twd_invested": 10000,
                "btc_price_usd": round(btc_price, 2),
                "btc_price_twd": round(btc_price * twd_rate, 2),
                "twd_rate": round(twd_rate, 4),
                "btc_bought": btc_bought,
            })

    records.sort(key=lambda record: record["date"])
    current_fx = {}
    try:
        current_fx = fetch_yahoo_daily_closes(
            "USDTWD=X", today - timedelta(days=7), today
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise RuntimeError(
            f"Unable to obtain a fresh USDTWD rate for current DCA valuation: {error}"
        ) from error
    current_twd_rate = latest_close_on_or_before(
        current_fx, today, max_age_days=4
    )
    if current_twd_rate is None or current_twd_rate <= 0:
        raise RuntimeError(
            "No USDTWD close from the last 4 days; refusing stale DCA valuation."
        )

    monthly_totals = {}
    total_twd = 0
    total_usd_invested = 0.0
    total_btc = 0.0
    for record in records:
        record_month = record["date"][:7]
        month = monthly_totals.setdefault(
            record_month, {"twd_invested": 0, "btc_bought": 0.0}
        )
        twd_invested = float(record["twd_invested"])
        twd_rate = float(record["twd_rate"])
        btc_bought = float(record["btc_bought"])
        month["twd_invested"] += twd_invested
        month["btc_bought"] += btc_bought
        total_twd += twd_invested
        total_usd_invested += twd_invested / twd_rate
        total_btc += btc_bought

    if total_btc <= 0:
        raise RuntimeError("The DCA portfolio has no valid accumulated BTC.")
    current_market_value_usd = total_btc * float(current_btc_usd)
    current_market_value_twd = current_market_value_usd * current_twd_rate
    unrealized_pnl_usd = current_market_value_usd - total_usd_invested
    unrealized_pnl_twd = current_market_value_twd - total_twd
    monthly_summary = {
        month: {
            "twd_invested": totals["twd_invested"],
            "btc_bought": f"{totals['btc_bought']:.8f}",
        }
        for month, totals in sorted(monthly_totals.items())
    }
    portfolio.update({
        "start_date": start_date.isoformat(),
        "monthly_contribution_twd": 10000,
        "schedule_days": [25],
        "records": records,
        "monthly_summary": monthly_summary,
        "total_twd": total_twd,
        "total_btc": f"{total_btc:.8f}",
        "avg_buy_price_twd": round(total_twd / total_btc, 2),
        "avg_buy_price_usd": round(total_usd_invested / total_btc, 2),
        "current_btc_usd": round(float(current_btc_usd), 2),
        "current_twd_rate": round(current_twd_rate, 4),
        "current_market_value_usd": round(current_market_value_usd, 2),
        "current_market_value_twd": round(current_market_value_twd, 2),
        "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
        "unrealized_pnl_twd": round(unrealized_pnl_twd, 2),
        "total_return_pct": round(
            (current_market_value_twd / total_twd - 1) * 100, 2
        ),
    })
    with open(ledger_path, "w", encoding="utf-8") as ledger_file:
        json.dump(portfolio, ledger_file, ensure_ascii=False, indent=2)
        ledger_file.write("\n")
    return portfolio


dca_portfolio = update_dca_portfolio(spot_prices["BTC"])
dca_today = now_tw.date()
dca_latest_record = dca_portfolio["records"][-1]
dca_latest_month = dca_latest_record["date"][:7]
dca_latest_month_data = dca_portfolio["monthly_summary"].get(
    dca_latest_month, {"twd_invested": 0, "btc_bought": "0.00000000"}
)
dca_report = (
    "【BTC 長期定投（DCA）節目模擬帳本】\n"
    "比特汪在節目設定中自2025年10月起，每月25日模擬投入NT$10,000；此帳本不是實際成交紀錄。\n"
    f"最近一次買進（{dca_latest_record['date']}）：BTC US${dca_latest_record['btc_price_usd']:,.2f}，"
    f"約NT${dca_latest_record['btc_price_twd']:,.2f}；"
    f"該月累計投入NT${dca_latest_month_data['twd_invested']:,}，"
    f"買入{dca_latest_month_data['btc_bought']} BTC。\n"
    f"總投入：NT${dca_portfolio['total_twd']:,}；"
    f"累計持有：{dca_portfolio['total_btc']} BTC。\n"
    f"平均成本：NT${dca_portfolio['avg_buy_price_twd']:,.2f} / "
    f"US${dca_portfolio['avg_buy_price_usd']:,.2f} 每BTC。\n"
    f"目前總價值：NT${dca_portfolio['current_market_value_twd']:,.2f}；"
    f"帳面賺賠：NT${dca_portfolio['unrealized_pnl_twd']:+,.2f} "
    f"（{dca_portfolio['total_return_pct']:+.2f}%）。"
)
recent_dca_records = dca_portfolio["records"][-3:]
lowest_dca_record = min(
    dca_portfolio["records"], key=lambda record: float(record["btc_price_usd"])
)
highest_dca_record = max(
    dca_portfolio["records"], key=lambda record: float(record["btc_price_usd"])
)


def dca_performance_guidance(unrealized_pnl_twd):
    if unrealized_pnl_twd < 0:
        return (
            "浮虧",
            "目前帳本是未實現浮虧。比特汪要冷靜看待當期損益：固定投入相同台幣，"
            "若BTC折合台幣價格較低，該期可累積較多BTC；只有買進價低於既有均價時，"
            "新增投入才會拉低平均成本。以太喵可解釋低檔持續投入、未來若價格回升時"
            "資產可能受益的情境，但不得說成必然的微笑曲線或保證翻正。可溫和提醒，"
            "恐慌時匆忙賣出可能鎖定損失，但尊重不同人的風險承受度；BTC仍可能續跌，"
            "甚至長期無法回到買入成本。",
            "浮虧期間，低於均價的買入才可能拉低平均成本；未來價格與報酬仍不確定。",
        )
    if unrealized_pnl_twd > 0:
        return (
            "帳面獲利",
            "目前帳本為帳面獲利。比特汪與以太喵可指出，這是截至今日市價高於累積成本"
            "的結果，先前定期投入讓持倉參與了這段價格變化；不得把它說成時間複利必然"
            "或未來報酬保證。提醒觀眾勿因短期浮盈追高、急著加碼或使用槓桿；是否繼續"
            "定投應依原有計畫、預算與風險承受度檢視。",
            "目前帳面獲利仍會隨價格波動；避免因短期上漲追高或加槓桿。",
        )
    return (
        "接近損益兩平",
        "目前帳本接近損益兩平。兩位主持人應平實說明當期結果，不把短期持平解讀成"
        "未來必漲或必跌；定期投入的成本與結果會隨價格變化，無法保證翻正。",
        "帳面接近損益兩平；單期結果不代表未來，定投仍須符合個人預算與風險。",
    )


dca_performance_state, dca_prompt_guidance, dca_slide_caption = (
    dca_performance_guidance(float(dca_portfolio["unrealized_pnl_twd"]))
)
dca_slide_body = "\n".join(
    [
        f"總投入 NT${dca_portfolio['total_twd']:,}｜累計 {dca_portfolio['total_btc']} BTC",
        f"平均成本 TWD {dca_portfolio['avg_buy_price_twd']:,.0f} / USD {dca_portfolio['avg_buy_price_usd']:,.0f}",
        f"總市值 NT${dca_portfolio['current_market_value_twd']:,.0f}｜損益 NT${dca_portfolio['unrealized_pnl_twd']:+,.0f}（{dca_portfolio['total_return_pct']:+.2f}%）",
        "近三次買進：" + "；".join(
            f"{record['date']} ${record['btc_price_usd']:,.0f}/NT${record['btc_price_twd']:,.0f}"
            for record in recent_dca_records
        ),
        f"歷史低／高：${lowest_dca_record['btc_price_usd']:,.0f}（{lowest_dca_record['date']}）／${highest_dca_record['btc_price_usd']:,.0f}（{highest_dca_record['date']}）",
    ]
)


def generate_dca_chart(portfolio):
    today = datetime.now(tw_tz).date()
    start_date = datetime.fromisoformat(portfolio["start_date"]).date()
    chart_font_path = next(
        (
            path for path in (
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "C:\\Windows\\Fonts\\msyhbd.ttc",
                "C:\\Windows\\Fonts\\msyh.ttc",
            )
            if os.path.exists(path)
        ),
        None,
    )
    chart_font = (
        FontProperties(fname=chart_font_path, size=14, weight="bold")
        if chart_font_path
        else FontProperties(size=14, weight="bold")
    )
    btc_history = fetch_yahoo_daily_closes("BTC-USD", start_date, today)
    try:
        fx_history = fetch_yahoo_daily_closes(
            "USDTWD=X", start_date - timedelta(days=7), today
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"DCA chart FX history unavailable; using 32.5 where needed: {error}")
        fx_history = {}

    month_ends = []
    year, month = start_date.year, start_date.month
    while (year, month) <= (today.year, today.month):
        month_end = datetime(
            year, month, calendar.monthrange(year, month)[1]
        ).date()
        month_ends.append((f"{year}-{month:02d}", min(month_end, today)))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1

    records = portfolio["records"]
    invested_values = []
    market_values = []
    fallback_logged = False
    for month_key, valuation_date in month_ends:
        cumulative_records = [
            record for record in records if record["date"] <= valuation_date.isoformat()
        ]
        invested = sum(float(record["twd_invested"]) for record in cumulative_records)
        accumulated_btc = sum(float(record["btc_bought"]) for record in cumulative_records)
        if month_key == today.strftime("%Y-%m"):
            btc_price = float(portfolio["current_btc_usd"])
            twd_rate = float(portfolio["current_twd_rate"])
        else:
            btc_price = latest_close_on_or_before(btc_history, valuation_date)
            if btc_price is None or btc_price <= 0:
                raise RuntimeError(
                    f"Missing BTC-USD month-end price for {valuation_date.isoformat()}."
                )
            twd_rate = latest_close_on_or_before(fx_history, valuation_date)
            if twd_rate is None or twd_rate <= 0:
                if not fallback_logged:
                    print("DCA chart has no recent month-end USDTWD close; using 32.5.")
                    fallback_logged = True
                twd_rate = 32.5
        invested_values.append(invested)
        market_values.append(accumulated_btc * btc_price * twd_rate)

    months = [month for month, _ in month_ends]
    fig, axis = plt.subplots(figsize=(7.2, 5.2), dpi=160)
    fig.patch.set_facecolor("#111827")
    axis.set_facecolor("#0f172a")
    bar_colors = [
        "#f87171" if value >= cost else "#4ade80"
        for value, cost in zip(market_values, invested_values)
    ]
    axis.bar(months, market_values, color=bar_colors, alpha=0.58, label="月底資產總市值")
    axis.plot(
        months, invested_values, color="#fbbf24", marker="o",
        linewidth=2.2, markersize=4, label="累計投入台幣成本",
    )
    axis.set_title(
        "每月投入成本與資產市值",
        color="white", fontsize=18, fontweight="bold",
        fontproperties=chart_font, pad=12,
    )
    axis.set_ylabel(
        "TWD", color="#cbd5e1", fontsize=14, fontweight="bold",
        fontproperties=chart_font,
    )
    axis.tick_params(axis="x", colors="#cbd5e1", labelrotation=45, labelsize=14, pad=6)
    axis.tick_params(axis="y", colors="#cbd5e1", labelsize=14)
    for tick_label in axis.get_xticklabels() + axis.get_yticklabels():
        tick_label.set_fontproperties(chart_font)
    axis.grid(axis="y", color="#334155", linewidth=0.6, alpha=0.8)
    for spine in ("top", "right"):
        axis.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        axis.spines[spine].set_color("#475569")
    axis.legend(
        loc="upper left", facecolor="#1e293b", edgecolor="none",
        labelcolor="white", ncol=2, prop=chart_font,
    )
    fig.tight_layout()
    fig.savefig("chart_dca.png", facecolor="#111827")
    plt.close(fig)
    return "chart_dca.png"


chart_dca_path = generate_dca_chart(dca_portfolio) if show_dca else None

coins_lines = []
for name, (cl, pct, rsi) in cdata.items():
    sgn = "+" if pct >= 0 else ""
    r_txt = f" | RSI {rsi}" if rsi else ""
    coins_lines.append(
        f"{name}: ${spot_prices[name]:,.2f} ({sgn}{pct:.2f}%{r_txt})"
    )
coins_summary = "\n".join(coins_lines)

fear_report = (
    f"今日 {today_fng['value']} ({today_fng['value_classification']})"
    + (
        f" / 昨日 {yest_fng['value']} ({yest_fng['value_classification']})"
        if yest_fng else ""
    )
    if today_fng else "本次未取得通過新鮮度驗證的指數資料，請略過此項，不得推測數值"
)
full_report = (
    f"【今日日期】：{today_str}\n【數據抓取時間】：{data_fetched_at}\n"
    "【比特幣核心技術指標】\n"
    f"- BTC 即時報價：${cur_p} USD (24H: {'+' if btc_pct >= 0 else ''}{btc_pct:.2f}%)\n"
    f"- 短中期均線：100日 ${ma100d} | 200日 ${ma200d}\n"
    f"- 長期週線：100週 ${ma100w} | 200週 ${ma200w}\n"
    f"- 波段結構：{btc_technical_report}\n"
    f"- ETH 波段結構：{eth_technical_report}\n"
    f"- BTC RSI 歷史：{btc_oversold_report}\n"
    f"- 全網穩定幣總市值變化：{stablecoin_report}\n"
    f"- 大戶與全體帳戶持倉代理：{trader_position_report}\n"
    f"- 近30日現貨成交量：{spot_volume_report}\n"
    f"- 鏈上轉帳活躍度代理：{onchain_activity_report}\n"
    f"- 恐慌、回撤與成交量交叉判讀：{chip_sentiment_report}\n"
    f"- 市場恐慌與貪婪指數：{fear_report}\n\n"
    f"{dca_report}\n\n【主流幣即時報價與RSI掃描】\n{coins_summary}\n\n"
    f"【近36小時通過時效驗證的新聞】\n{news_list}"
)

def call_gemini(prompt_txt):
    models = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    custom_model = os.environ.get("GEMINI_MODEL", "").strip()
    if custom_model:
        models = [custom_model] + [m for m in models if m != custom_model]

    last_err = None
    for model in models:
        g_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.environ['GEMINI_API_KEY'].strip()}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt_txt}]}]}).encode("utf-8")
        retried_503 = False
        retried_timeout = False
        while True:
            req = urllib.request.Request(g_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                res = json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))
                return res["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="ignore")
                print(f"Gemini API Error ({e.code}) for model '{model}': {err_msg}")
                last_err = e
                if e.code == 503 and not retried_503:
                    retried_503 = True
                    time.sleep(10)
                    continue
                break
            except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
                print(f"Gemini network timeout/error for model '{model}': {e}")
                last_err = e
                if not retried_timeout:
                    retried_timeout = True
                    time.sleep(10)
                    continue
                break
            except Exception as e:
                print(f"Error calling Gemini model '{model}': {e}")
                last_err = e
                break

    if last_err:
        raise last_err
    raise RuntimeError("No Gemini models available.")

target_words = "約200字繁中（精簡測試版，各圖卡保留一句重點對話）" if is_test else "嚴格要求 2,600 至 2,900 字繁中（兩段合計約 5,200 字以上，確保 1.2 倍速下整集片長仍達 15 至 18 分鐘）"
if show_dca:
    p1_part_slide_count = 8
    news_slide_numbers = (6, 7, 8)
    p1_slide_anchors = (
        "[SLIDE_1]開場與風險提醒；[SLIDE_2]BTC長期定投（DCA）實戰帳本；"
        "[SLIDE_3]恐慌與貪婪指數；[SLIDE_4]BTC四大均線、RSI與歷史超賣圖；"
        "[SLIDE_5]ETH日線均線與RSI；[SLIDE_6]第一則市場焦點；"
        "[SLIDE_7]第二則市場焦點；[SLIDE_8]第三則市場焦點。"
    )
    p2_slide_anchors = (
        "[SLIDE_9]六大主流幣報價與指標總表；[SLIDE_10]六大主流幣24H漲跌；"
        "[SLIDE_11]BTC價格結構與關鍵多空攻防圖；[SLIDE_12]關鍵價位攻防；"
        "[SLIDE_13]美股開盤連動；[SLIDE_14]短線清算風險；"
        "[SLIDE_15]總結行動策略。"
    )
    dca_prompt_section = (
        "風險提醒後由比特汪以第一人稱播報「比特汪的 BTC 定期定額模擬帳本」，"
        "清楚說明自2025年10月25日起，按節目設定每月25日模擬投入NT$10,000，"
        "並非真實成交。只用一句話交代這項模擬規則，再報最近一次買進日期及BTC美金／"
        "台幣價位、最新當月投入與買進數量、總投入、累計BTC（8位小數）、平均成本、"
        "目前總市值、未實現賺賠金額及百分比；不可遺漏或改寫帳本數字。\n"
        f"目前績效狀態：{dca_performance_state}。\n"
        f"依狀態調整雙人互動：{dca_prompt_guidance}\n"
        "合規要求：強調這是長期分批建倉的模擬紀律示範，不構成投資建議；"
        "不鼓勵借貸、高槓桿或盲目追高殺低。不得保證獲利、回本或週期必然翻正。\n"
        "帳本數字如下：\n"
        f"{dca_report}"
    )
    p1_content_guidance = (
        "先以[SLIDE_1]開場並完成風險提醒，下一段由比特汪親自以第一人稱播報[SLIDE_2]節目模擬DCA帳本；"
        "接續依序以[SLIDE_3]分析恐慌貪婪指數、[SLIDE_4]分析BTC均線與RSI歷史超賣圖、"
        "[SLIDE_5]分析ETH，再以[SLIDE_6]至[SLIDE_8]逐一深入解析三大市場焦點。"
    )
    p1_formal_length = (
        "正式版本段必須達2,600至2,900字：開場風險提醒50至65字；DCA帳本播報約220至250字；"
        "BTC均線、RSI歷史超賣、恐慌貪婪指數與ETH均線RSI合計700至800字；三大市場焦點各450至500字，"
        "逐一說明正面邏輯、風險折價與可驗證訊號；段落銜接約100字。"
    )
else:
    p1_part_slide_count = 7
    news_slide_numbers = (5, 6, 7)
    p1_slide_anchors = (
        "[SLIDE_1]開場與風險提醒；[SLIDE_2]恐慌與貪婪指數；"
        "[SLIDE_3]BTC四大均線、RSI與歷史超賣圖；[SLIDE_4]ETH日線均線與RSI；"
        "[SLIDE_5]第一則市場焦點；[SLIDE_6]第二則市場焦點；"
        "[SLIDE_7]第三則市場焦點。"
    )
    p2_slide_anchors = (
        "[SLIDE_8]六大主流幣報價與指標總表；[SLIDE_9]六大主流幣24H漲跌；"
        "[SLIDE_10]BTC價格結構與關鍵多空攻防圖；[SLIDE_11]關鍵價位攻防；"
        "[SLIDE_12]美股開盤連動；[SLIDE_13]短線清算風險；"
        "[SLIDE_14]總結行動策略。"
    )
    dca_prompt_section = (
        "今日不是DCA帳本播報日，逐字稿不得提及帳本數據或插入獨立的DCA播報段落；"
        "仍可自然維持長期持有與紀律投資的頻道精神。"
    )
    p1_content_guidance = (
        "以[SLIDE_1]開場並完成風險提醒，接續依序以[SLIDE_2]分析恐慌貪婪指數、"
        "[SLIDE_3]分析BTC均線與RSI歷史超賣圖、[SLIDE_4]分析ETH，"
        "再以[SLIDE_5]至[SLIDE_7]逐一深入解析三大市場焦點。"
    )
    p1_formal_length = (
        "正式版本段必須達2,600至2,900字：開場風險提醒50至65字；"
        "BTC均線、RSI歷史超賣、恐慌貪婪指數與ETH均線RSI合計900至1,000字；"
        "三大市場焦點各500至550字，逐一說明正面邏輯、風險折價與可驗證訊號；段落銜接約100字。"
    )
p1_length_guidance = (
    f"測試模式仍須為前半段{p1_part_slide_count}個投影片錨點各輸出一段極短口播，"
    "總長約200字以內，不套用正式版字數配額。"
    if is_test else
    f"{p1_formal_length}兩段合計至少5,200字，目標5,200至5,600字，禁止灌水或重複。"
)
p2_length_guidance = (
    "測試模式仍須為後半段7個投影片錨點各輸出一段極短口播，總長約200字以內，不套用正式版字數配額。"
    if is_test else
    "正式版本段必須達2,600至2,900字：六大主流幣報價、24H漲跌與14日RSI逐幣分析1,050至1,150字；BTC近90日價格結構與支撐壓力圖解讀100至150字；未來24小時（今日至明晨）三個觀察條件各450至500字，涵蓋關鍵價位、美股開盤連動與短線清算風險；總結與風險提醒100字。兩段合計至少5,200字，目標5,200至5,600字，禁止灌水或重複。"
)
host_persona_guidance = """女聲「以太喵」（cmn-TW-Wavenet-A）自稱「本喵」或「以太喵」：冷靜精明、略帶傲嬌吐槽，自然穿插「他喵的」「喵頭不對」等貓語梗；熟悉以太坊技術升級、Layer 2、DeFi與RWA。她負責精準拆解價格結構圖與RSI圖，報出回撤天數與幅度、30日支撐壓力、相對100日及200日線乖離、歷史超賣區段，以及穩定幣總市值變化；籌碼面則客觀對照 OKX 全體帳戶、前列交易者帳戶與持倉多空比、OKX 近30日現貨成交額、Blockchain.com 估算鏈上交易量及筆數。說明多空比是平台倉位代理、鏈上統計不能辨識地址身分或交易所淨流向；只依本次數據判讀短線成交量與情緒，不將「散戶割肉」或「巨鯨吸籌」說成已證實事實。市場劇本須分別說明站穩壓力或跌破支撐後可驗證的條件，不把指標當成必然預測，並替激動的比特汪踩煞車。
男聲「比特汪」（cmn-TW-Wavenet-B）自稱「本汪」或「比特汪」：熱情直率、容易激動，自然使用「汪嗚」「本汪」「別汪想」等狗語梗；重視BTC長期週期與紀律定投。接住以太喵的數據，用白話說明短線交易者可能退場、長期參與者可能較不活躍，但不得宣稱巨鯨必然吸籌或敦促觀眾把便宜籌碼交給特定族群。以不猜最低點、不追短線、不碰高槓桿為原則，提醒按個人預算與風險承受能力維持或調整分批計畫；不得保證獲利、回本或週期必然翻正。節目角色設定中的每月定投帳本是模擬紀錄，並非真實成交。
兩人分別代表「數位黃金派」與「世界計算機生態派」，互相尊重、立場互補，自然吐槽、穿插幣圈冷笑話與台灣網路梗；以太喵以數據幫比特汪踩煞車。口播禁止使用HODL、HODLer等縮寫，一律自然說「長期持有」或「長期持有者」。若今日不是DCA播報日，勿主動插入帳本環節；只有在指定DCA環節才可報帳本數字。"""
strategy_script_guidance = """【分析順序與雙人分工】全節目以「數據拆解 → 條件式策略劇本 → 長期定投心法」推進。以太喵先依本次報告讀出BTC與ETH結構數據，並說明RSI超賣區段只是歷史動能訊號；穩定幣市值增加或減少不等於資金已買入或賣出BTC。籌碼面須報出可用的全體帳戶與前列交易者多空比、近30日現貨成交量變化及鏈上活躍度變化；分清「放量下跌」與「縮量下跌」，並依恐慌貪婪值、90日回撤及成交額變化交叉描述恐慌換手或縮量沉澱。若某項未取得便明說略過，不補值。這些指標都是代理資料，不能據此確定散戶正在割肉、巨鯨正在吸籌，或長期持有者惜售。接著以太喵提出兩種條件式劇本：若價格站穩30日壓力並有後續價格確認，描述可能改善的結構；若跌破30日支撐，說明回撤風險仍在及需觀察的後續訊號。不可捏造支撐、壓力或確認條件。比特汪以白話總結短線交易者可能退場、長期參與者可能較少換手，但必須標明這不是對巨鯨吸籌的證明；提醒觀眾勿在恐慌中衝動改變計畫，從長期週期談紀律定投、不猜最低點。超賣不保證反彈，歷史表現不保證未來；定期定額須符合個人預算與風險承受度，不可催促觀眾買進。"""
p1_prompt = f"""你是財經播客【幣看趨勢】主編。請只寫前半段逐字稿，總長度{target_words}，{"測試模式合計約1分鐘內。" if is_test else "正式版整集兩段合計至少5,200字、以1.2倍速達15至18分鐘。"}
【資料依循最高優先級】所有價格、漲跌幅、均線、RSI、多空比、現貨成交量、鏈上活躍度、恐慌貪婪指數與新聞事件，只能依據下方本次資料及其抓取時間播報；嚴禁使用預訓練記憶補入價格、事件日期或新聞。任何欄位若標示未取得或不存在，直接略過，不猜測、不補值、不編造。新聞只可引用有發布時間標記的素材。
{full_report}
【雙人角色與互動】{host_persona_guidance}
{strategy_script_guidance}
【頻道核心精神】兩位主持人分別代表BTC數位黃金與ETH世界計算機生態兩種長期視角。分析以數據與可驗證事實為準，反對盲目短線追高殺低與高槓桿賭博；長期看好不代表忽視短線回檔、過熱與生態風險。
【DCA播報規則】{dca_prompt_section}
【硬性禁令】禁止早安、午安、晚安、大家好等任何時間問候語；禁止「休息一下」「下一段」「後半段」「上半場」「下半場」「先到這裡」「接著看」「接下來」等分段或轉場字眼；禁止提及媒體名稱、禁止動作括號與Markdown；禁止重複同一個數字結論或同一句論點。
【隱藏投影片錨點】每個對應段落的第一句，必須在「男：」或「女：」後立刻放入對應標記，標記後才是要口播的文字；每個標記恰好出現一次，嚴格依序，不可念出標記。前半段標記對應：{p1_slide_anchors}
【篇幅配額】{p1_length_guidance}
【新聞選題與投影片嚴格一致性（最高優先級）】
1. 純幣圈選題禁令：只可從「近36小時新聞」清單中挑選三則不同新聞，且必須是加密貨幣、區塊鏈、Web3 或總體經濟相關新聞；嚴格禁止挑選無關幣圈的純 AI 科技新聞（如 ChatGPT、Gemini、OpenAI、Claude、AI 晶片等純 AI 領域話題）。
2. 口播、投影片大標題、中間內容三位一體完全一致：
   - 第一則口播（錨點 [SLIDE_{news_slide_numbers[0]}]）、SLIDE_NEWS1 的 slide_title、positive、risk 必須【全部來自同一則新聞事件】！
   - 第二則口播（錨點 [SLIDE_{news_slide_numbers[1]}]）、SLIDE_NEWS2 的 slide_title、positive、risk 必須【全部來自同一則新聞事件】！
   - 第三則口播（錨點 [SLIDE_{news_slide_numbers[2]}]）、SLIDE_NEWS3 的 slide_title、positive、risk 必須【全部來自同一則新聞事件】！
   每個新聞錨點後的第一句話，主持人必須清楚點出該新聞的主題事件。SLIDE_NEWS1/2/3 必須嚴格按照口播出場順序排列，不可依照清單原本序號排序。
3. 標題與畫面時間標籤絕對禁令：
   - slide_title 必須是精煉的新聞核心主標題（25字內），中間內容 positive（利多）與 risk（以太喵理性打折與隱藏風險）必須完全呼應此標題。
   - 投影片畫面與逐字稿中【絕對嚴禁出現】「[發布時間:...]」、「來源時間」、「發布時間」、「CST」、「UTC」或任何時間戳記標籤！不可把發布時間印在標題或內容上。
每則摘要行格式：SLIDE_NEWS1:{{"source_news_number": <序號>, "slide_title": "<精簡大標題>", "positive": "<正面利多敘事>", "risk": "<以太喵理性打折與風險>"}}。三則摘要行不是口播內容，禁止 Markdown 與媒體名稱。
【格式】第一行必須單獨輸出：標題：（結合今日數據的反直覺懸念爆款標題，32字內無引號）；其後逐字稿只能用「男：」「女：」開頭。逐字稿結束後，依上述格式輸出恰好三行新聞圖卡 JSON 摘要。
【內容】{p1_content_guidance}結尾銜接具體幣價，不說再見、不使用分段結尾。"""
def normalize_long_term_terms(text):
    text = re.sub(r"\bHODLers?\b", "長期持有者", str(text), flags=re.I)
    return re.sub(r"\bHODL\b", "長期持有", text, flags=re.I)


part1_raw = normalize_long_term_terms(call_gemini(p1_prompt))
def extract_slide_notes(raw, prefix, fallback_notes):
    notes = []
    for index, fallback in enumerate(fallback_notes, 1):
        match = re.search(rf'^{re.escape(prefix)}{index}:\s*(.+)$', raw, re.M)
        notes.append(match.group(1).strip() if match else fallback)
    raw = re.sub(rf'^{re.escape(prefix)}\d+:.*(?:\r?\n|$)', '', raw, flags=re.M)
    return notes, raw.rstrip()


def extract_news_anchor_dialogues(script, target_slide_numbers):
    anchor_pattern = re.compile(r"\[SLIDE_(\d+)\]")
    sections = {}
    current_slide = None
    current_lines = []

    for line in script.splitlines():
        match = re.match(r"^(?:男|女)[：:]\s*(.+)", line.strip())
        if not match:
            continue
        text = match.group(1).strip()
        marker = anchor_pattern.search(text)
        if marker:
            slide_num = int(marker.group(1))
            if current_slide is not None:
                sections[current_slide] = " ".join(current_lines)
            current_slide = slide_num
            current_lines = [anchor_pattern.sub("", text).strip()]
        else:
            if current_slide is not None:
                current_lines.append(text)

    if current_slide is not None:
        sections[current_slide] = " ".join(current_lines)

    return [sections.get(num, "") for num in target_slide_numbers]


def score_text_overlap(text1, text2):
    words1 = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z0-9]{2,}", str(text1).lower()))
    words2 = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z0-9]{2,}", str(text2).lower()))
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    return len(intersection) / (len(words1) ** 0.5 * len(words2) ** 0.5)


def align_slide_news_with_dialogue(details, script, target_slide_numbers, available_entries):
    spoken_dialogues = extract_news_anchor_dialogues(script, target_slide_numbers)
    if len(details) != 3 or len(spoken_dialogues) != 3:
        return details

    detail_texts = []
    for item in details:
        news_idx = item["source_news_number"] - 1
        entry_title = ""
        entry_summary = ""
        if 0 <= news_idx < len(available_entries):
            entry = available_entries[news_idx][1]
            entry_title = str(entry.get("title", ""))
            entry_summary = str(entry.get("summary", ""))
        combined = f"{item.get('slide_title', '')} {item.get('positive', '')} {item.get('risk', '')} {entry_title} {entry_summary}"
        detail_texts.append(combined)

    from itertools import permutations
    best_score = -1.0
    best_perm = (0, 1, 2)
    for perm in permutations(range(3)):
        total_score = sum(score_text_overlap(spoken_dialogues[i], detail_texts[perm[i]]) for i in range(3))
        if total_score > best_score:
            best_score = total_score
            best_perm = perm

    if best_perm != (0, 1, 2):
        print(f"Re-aligned news slides to match spoken dialogue order: {best_perm} (score: {best_score:.3f})")
    return [details[best_perm[i]] for i in range(3)]


def extract_slide_news_details(raw, available_entries):
    marker_lines = re.findall(
        r"^[ \t]*SLIDE_NEWS(\d+):[ \t]*(\{.*\})[ \t]*$", raw, flags=re.M
    )
    if [int(index) for index, _ in marker_lines] != [1, 2, 3]:
        raise RuntimeError(
            "Gemini must return exactly three ordered SLIDE_NEWS JSON records."
        )

    details = []
    selected_news_numbers = set()
    for index, payload in marker_lines:
        try:
            record = json.loads(payload)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Gemini returned invalid JSON for SLIDE_NEWS{index}."
            ) from error
        if not isinstance(record, dict):
            raise RuntimeError(f"SLIDE_NEWS{index} must be a JSON object.")
        news_number = record.get("source_news_number")
        if (
            isinstance(news_number, bool)
            or not isinstance(news_number, int)
            or not 1 <= news_number <= len(available_entries)
            or news_number in selected_news_numbers
        ):
            raise RuntimeError(
                f"SLIDE_NEWS{index} has a missing, repeated, or invalid source_news_number."
            )
        entry = available_entries[news_number - 1][1]
        source_title = clean_txt(entry.get("title", ""))

        slide_title = clean_slide_title(record.get("slide_title"))
        if not slide_title or len(slide_title) < 4 or slide_title in {"今日市場焦點", "市場焦點"}:
            slide_title = clean_slide_title(source_title)[:32]

        positive = remove_publish_time(str(record.get("positive") or "")).strip()
        risk = remove_publish_time(str(record.get("risk") or "")).strip()
        if not slide_title or not positive or not risk:
            raise RuntimeError(
                f"SLIDE_NEWS{index} must include slide_title, positive, and risk."
            )
        selected_news_numbers.add(news_number)
        details.append({
            "source_news_number": news_number,
            "slide_title": slide_title,
            "positive": positive,
            "risk": risk,
        })

    raw = re.sub(
        r"^[ \t]*SLIDE_NEWS\d+:.*(?:\r?\n|$)", "", raw, flags=re.M
    )
    return details, raw.rstrip()


slide_news_details, part1_raw = extract_slide_news_details(part1_raw, entries)
slide_news_details = align_slide_news_with_dialogue(
    slide_news_details, part1_raw, news_slide_numbers, entries
)
slide_news = [
    f"利多：{item['positive']}；風險：{item['risk']}"
    for item in slide_news_details
]
t_match = re.search(r'^標題[：:]\s*(.+)', part1_raw, re.M)
ai_title = t_match.group(1).strip().replace('「', '').replace('」', '') if t_match else f"BTC ${cur_p}｜主流幣均線攻防與恐慌指數全解析"
video_title = f"【幣看趨勢】{ai_title} ({today_str})"

p2_prompt = f"""你是【幣看趨勢】主編。請緊接前半段續寫後半段逐字稿，{target_words}。
【資料依循最高優先級】所有價格、漲跌幅、均線、RSI、多空比、現貨成交量、鏈上活躍度、恐慌貪婪指數與新聞事件，只能依據下方本次資料及其抓取時間播報；嚴禁使用預訓練記憶補入價格、事件日期或新聞。任何欄位若標示未取得或不存在，直接略過，不猜測、不補值、不編造。新聞只可引用有發布時間標記的素材。
【前段逐字稿】{part1_raw}
【前段結尾】{part1_raw[-350:]}
【數據】{full_report}
【雙人角色與互動】{host_persona_guidance}
{strategy_script_guidance}
【頻道核心精神】兩人以BTC數位黃金與ETH世界計算機生態兩種長期視角互補；所有技術分析與未來24小時觀察條件都要依數據客觀說明，既不盲目吹捧BTC或ETH，也不把長期信念包裝成短線買賣指令。反對高槓桿賭博與頻繁短線交易，結尾提醒以長期週期和風險管理看待市場。
【硬性禁令】禁止早安、午安、晚安、大家好等任何時間問候語；禁止「休息一下」「下一段」「後半段」「上半場」「下半場」「先到這裡」「接著看」「接下來」等分段或轉場字眼；禁止提及媒體名稱、禁止動作括號與Markdown；禁止任何與前半段重複的開場、風險提醒、BTC均線結論、恐慌指數結論或市場焦點分析。
【隱藏投影片錨點】每個對應段落的第一句，必須在「男：」或「女：」後立刻放入對應標記，標記後才是要口播的文字；每個標記恰好出現一次，嚴格依序，不可念出標記。後半段標記對應：{p2_slide_anchors}
【篇幅配額】{p2_length_guidance}
【去重規則】以完整前段逐字稿核對；同一數據只報一次，不復述前段已解讀的多空觀點，不重複前段例子與結論。
【內容】依錨點順序：六大主流幣報價與14日RSI總覽、六大幣24H漲跌強弱比較、BTC近90日價格結構與支撐壓力圖解讀（不重複前段均線數值）；再提出未來24小時（今日至明晨）必須盯緊的三個BTC/ETH觀察條件，分別聚焦關鍵價位攻防、美股開盤連動、短線清算風險。觀察條件須說明可驗證訊號與多空意義，不可虛構即時數據，且必須從長期持有與DCA風險管理角度解讀，不可給短線下單指令。接著總結行動策略。逐字稿後額外輸出恰好三行簡短圖卡摘要，分別以「SLIDE_WEEK1:」「SLIDE_WEEK2:」「SLIDE_WEEK3:」開頭，與三條24小時條件一一對應；這些標記行不是口播內容。結尾自然提醒控管倉位、長期週期與紀律DCA並道別，不使用分段字眼。"""
part2_raw = normalize_long_term_terms(call_gemini(p2_prompt))
default_24h_conditions = [
    f"均線守位｜BTC ${cur_p}、ETH ${spot_prices['ETH']:.2f} 能否守穩各自100日均線 ${ma100d}、${calc_sma(cdata['ETH'][0], 100)}。",
    "美股開盤連動｜觀察今日美股開盤後，BTC與ETH是否跟隨納指方向並伴隨成交量變化。",
    "短線清算風險｜留意急漲急跌時合約清算與資金費率是否擴大，避免槓桿部位遭短線掃損。",
]
slide_24h_conditions, part2_raw = extract_slide_notes(
    part2_raw, "SLIDE_WEEK", default_24h_conditions
)

def extract_dialogues_and_slide_anchors(script):
    dialogues = []
    first_anchor_dialogue_indexes = {}
    slide_anchor_pattern = re.compile(r"\[SLIDE_(\d+)\]")
    for line in script.splitlines():
        match = re.match(r"^(男|女)[：:]\s*(.+)", line.strip())
        if not match:
            continue
        raw_dialogue_text = match.group(2).strip()
        marker_matches = list(slide_anchor_pattern.finditer(raw_dialogue_text))
        dialogue_text = slide_anchor_pattern.sub("", raw_dialogue_text).strip()
        dialogue_text = normalize_long_term_terms(dialogue_text)
        if not dialogue_text:
            continue

        for marker_match in marker_matches:
            slide_number = int(marker_match.group(1))
            if 1 <= slide_number <= SLIDE_COUNT:
                first_anchor_dialogue_indexes.setdefault(
                    slide_number, len(dialogues)
                )
                break
        dialogues.append((match.group(1), dialogue_text))

    ordered_anchor_points = []
    last_dialogue_index = -1
    for slide_number in range(1, SLIDE_COUNT + 1):
        dialogue_index = first_anchor_dialogue_indexes.get(slide_number)
        if dialogue_index is not None and dialogue_index > last_dialogue_index:
            ordered_anchor_points.append((slide_number, float(dialogue_index)))
            last_dialogue_index = dialogue_index

    if not dialogues:
        return dialogues, {}

    anchor_points = {1: 0.0, SLIDE_COUNT: float(len(dialogues))}
    anchor_points.update(ordered_anchor_points)
    known_slides = sorted(anchor_points)
    positions = {}
    for slide_number in range(1, SLIDE_COUNT + 1):
        if slide_number in anchor_points:
            positions[slide_number] = anchor_points[slide_number]
            continue

        previous_slide = max(
            known for known in known_slides if known < slide_number
        )
        next_slide = min(
            known for known in known_slides if known > slide_number
        )
        previous_index = anchor_points[previous_slide]
        next_index = anchor_points[next_slide]
        fraction = (slide_number - previous_slide) / (next_slide - previous_slide)
        positions[slide_number] = previous_index + fraction * (
            next_index - previous_index
        )

    min_gap = len(dialogues) / (SLIDE_COUNT * 1000)
    ordered_positions = [
        min(max(positions[slide_number], 0.0), float(len(dialogues)) - min_gap)
        for slide_number in range(1, SLIDE_COUNT + 1)
    ]
    for index in range(1, SLIDE_COUNT):
        ordered_positions[index] = max(
            ordered_positions[index], ordered_positions[index - 1] + min_gap
        )
    for index in range(SLIDE_COUNT - 2, -1, -1):
        ordered_positions[index] = min(
            ordered_positions[index], ordered_positions[index + 1] - min_gap
        )

    slide_anchor_positions = {
        slide_number: ordered_positions[slide_number - 1]
        for slide_number in range(1, SLIDE_COUNT + 1)
    }
    return dialogues, slide_anchor_positions


def calculate_slide_durations(slide_start_times, speech_duration, video_intro_duration):
    expected_slides = set(range(1, SLIDE_COUNT + 1))
    if set(slide_start_times) != expected_slides:
        raise RuntimeError(
            "Slide timing requires one start time for every slide; received "
            f"{sorted(slide_start_times)}."
        )

    durations = []
    for slide_number in range(1, SLIDE_COUNT + 1):
        start_time = slide_start_times[slide_number]
        end_time = (
            slide_start_times[slide_number + 1]
            if slide_number < SLIDE_COUNT
            else video_intro_duration + speech_duration + OUTRO_PAUSE
        )
        duration = end_time - start_time
        if duration <= 0:
            raise RuntimeError(
                f"Slide {slide_number} has invalid anchor timing: "
                f"{start_time:.3f}s to {end_time:.3f}s."
            )
        durations.append(duration)
    return durations


full_script = re.sub(r'^標題[：:].*\n?', '', part1_raw, flags=re.M) + "\n" + part2_raw
dialogues, slide_anchor_positions = extract_dialogues_and_slide_anchors(full_script)

tts_host = "".join(["https", "://", "texttospeech.", "googleapis.", "com"])
tts_url = f"{tts_host}/v1/text:synthesize?key={os.environ['GOOGLE_TTS_KEY'].strip()}"

def synth_audio_segment(item, file_prefix):
    idx, (role, txt) = item
    txt = normalize_long_term_terms(txt)
    v_name = "cmn-TW-Wavenet-B" if role == "男" else "cmn-TW-Wavenet-A"
    fallback_voice = "cmn-TW-Standard-B" if role == "男" else "cmn-TW-Standard-A"
    pitch = -1.5 if role == "男" else 1.5
    clean_say = re.sub(r'[*#\[\]()]', '', txt)
    ssml_say = html.escape(clean_say, quote=False)
    ssml_say = re.sub(r"([，、,])", r'\1<break time="120ms"/>', ssml_say)
    ssml_say = re.sub(r"([。！？!?])", r'\1<break time="280ms"/>', ssml_say)
    for emphasized_phrase in ("請注意", "關鍵是", "風險提醒"):
        ssml_say = ssml_say.replace(
            emphasized_phrase,
            f'<emphasis level="moderate">{emphasized_phrase}</emphasis>',
        )

    def request_tts(voice_name):
        payload_tts = json.dumps({
            "input": {"ssml": f"<speak>{ssml_say}</speak>"},
            "voice": {"languageCode": "cmn-TW", "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3", "pitch": pitch, "speakingRate": 1.2},
        }).encode("utf-8")
        req = urllib.request.Request(
            tts_url,
            data=payload_tts,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))

    try:
        res = request_tts(v_name)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            error_message = json.loads(error_body).get("error", {}).get("message", error_body)
        except json.JSONDecodeError:
            error_message = error_body
        error_text = str(error_message).lower()
        unsupported_voice = e.code in (400, 404) and "voice" in error_text and any(
            phrase in error_text
            for phrase in ("not supported", "unsupported", "not available", "unavailable", "does not exist", "not valid", "invalid")
        )
        if not unsupported_voice:
            print(f"Google TTS API error ({e.code}) for voice '{v_name}': {error_message}")
            raise
        print(f"Google TTS voice '{v_name}' is unavailable; retrying with '{fallback_voice}'.")
        res = request_tts(fallback_voice)

    fn = f"{file_prefix}_{idx:04d}.mp3"
    with open(fn, "wb") as f: f.write(base64.b64decode(res["audioContent"]))
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", fn]).decode().strip())
    return idx, fn, txt, dur

def synth_one(item):
    return synth_audio_segment(item, "seg")

with ThreadPoolExecutor(max_workers=5) as ex:
    results = list(ex.map(synth_one, enumerate(dialogues)))
results.sort(key=lambda x: x[0])

def create_outro_audio():
    outro_source = next(
        (path for path in ("outro.mp3", "info.mp3") if os.path.exists(path)),
        None,
    )
    if outro_source:
        audio_filter = (
            f"volume=0.35,apad=whole_dur={OUTRO_DURATION:g},"
            f"afade=t=in:st=0:d=1,"
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


has_intro_video = os.path.exists("info.mp4") and os.path.exists("info.mp3")
intro_video_duration = 15.0 if has_intro_video else 0.0
intro_dur = 0.0
if has_intro_video:
    intro_source_duration = float(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", "info.mp3",
            ]
        ).decode().strip()
    )
    fade_duration = min(1.5, intro_source_duration) if intro_source_duration > 0 else 1.5
    fade_start = max(0.0, min(13.5, intro_source_duration - fade_duration))
    intro_audio_filter = (
        f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f},"
        "apad=whole_dur=15"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", "info.mp3", "-t", "15",
            "-af", intro_audio_filter,
            "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
            "intro_cut.mp3",
        ],
        check=True,
    )
    intro_dur = float(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", "intro_cut.mp3",
            ]
        ).decode().strip()
    )

create_outro_audio()
with open("audio_list.txt", "w", encoding="utf-8") as f:
    if intro_dur > 0:
        f.write("file 'intro_cut.mp3'\n")
    for _, fn, _, _ in results: f.write(f"file '{fn}'\n")
    f.write("file 'outro_pause.mp3'\n")
    f.write("file 'outro_music.mp3'\n")
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "audio_list.txt", "-c", "copy", "podcast.mp3"], check=True)
cc = OpenCC("s2twp")
def fmt_ass_t(sec):
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


MAIN_VIDEO_WIDTH = 1920
MAIN_VIDEO_HEIGHT = 1080
MAIN_DESIGN_WIDTH = 1280
MAIN_DESIGN_HEIGHT = 720
MAIN_OUTPUT_SCALE = 1.5
SHORT_VIDEO_WIDTH = 1080
SHORT_VIDEO_HEIGHT = 1920
SHORT_DESIGN_WIDTH = 720
SHORT_DESIGN_HEIGHT = 1280
SHORT_OUTPUT_SCALE = 1.5
SHORT_SUBTITLE_MAX_WIDTH = 12.5
SHORT_SUBTITLE_SHRINK_LIMIT = 15.5
SHORT_SUBTITLE_SHRINK_PERCENT = 88
SHORT_SUBTITLE_SENTENCE_END = re.compile(r"[。！？!?；;\n]+")
SHORT_SUBTITLE_BREAKS = re.compile(r"[，、,]")
SHORT_SUBTITLE_CONNECTORS = re.compile(
    r"但是|不過|然而|因此|所以|同時|而且|此外|另外|接著|反而|因為|如果|"
    r"雖然|並且|可是|但|而|且|或|則"
)
SHORT_SUBTITLE_EDGE_PUNCTUATION = re.compile(
    r"^[，、,。！？!?；;：:]+|[，、,。！？!?；;：:]+$"
)


def short_subtitle_visual_width(text):
    return sum(
        1.0
        if unicodedata.east_asian_width(char) in ("F", "W")
        else 0.5
        for char in text
        if char not in "\r\n"
    )


def split_short_subtitle_text(text):
    def trim_edges(value):
        return SHORT_SUBTITLE_EDGE_PUNCTUATION.sub("", value).strip()

    def split_at_midpoint(value):
        candidates = []
        for match in SHORT_SUBTITLE_BREAKS.finditer(value):
            left = trim_edges(value[:match.start()])
            right = trim_edges(value[match.end():])
            if left and right:
                candidates.append(
                    (
                        abs(
                            short_subtitle_visual_width(left)
                            - short_subtitle_visual_width(right)
                        ),
                        0,
                        left,
                        right,
                    )
                )

        for match in SHORT_SUBTITLE_CONNECTORS.finditer(value):
            for boundary in (match.start(), match.end()):
                left = trim_edges(value[:boundary])
                right = trim_edges(value[boundary:])
                if left and right:
                    candidates.append(
                        (
                            abs(
                                short_subtitle_visual_width(left)
                                - short_subtitle_visual_width(right)
                            ),
                            1,
                            left,
                            right,
                        )
                    )

        if candidates:
            _, _, left, right = min(candidates, key=lambda item: item[:2])
            return left, right

        boundaries = []
        for boundary in range(1, len(value)):
            left = trim_edges(value[:boundary])
            right = trim_edges(value[boundary:])
            if left and right:
                boundaries.append(
                    (
                        abs(
                            short_subtitle_visual_width(left)
                            - short_subtitle_visual_width(right)
                        ),
                        left,
                        right,
                    )
                )
        if not boundaries:
            return value, ""
        _, left, right = min(boundaries, key=lambda item: item[0])
        return left, right

    def split_clause(clause):
        clause = re.sub(r"\s+", " ", trim_edges(clause))
        width = short_subtitle_visual_width(clause)
        if not clause:
            return []
        if width <= SHORT_SUBTITLE_SHRINK_LIMIT:
            scale = (
                SHORT_SUBTITLE_SHRINK_PERCENT
                if width > SHORT_SUBTITLE_MAX_WIDTH
                else 100
            )
            return [(clause, scale)]

        left, right = split_at_midpoint(clause)
        if not right:
            return [(clause, SHORT_SUBTITLE_SHRINK_PERCENT)]
        return split_clause(left) + split_clause(right)

    chunks = []
    for sentence in SHORT_SUBTITLE_SENTENCE_END.split(text):
        chunks.extend(split_clause(sentence))
    return chunks


ass_head = (
    f"[Script Info]\nScriptType: v4.00+\n"
    f"PlayResX: {MAIN_VIDEO_WIDTH}\nPlayResY: {MAIN_VIDEO_HEIGHT}\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Noto Sans CJK TC,66,&H00FFFFFF,&H000000FF,&H00000000,"
    "&H99000000,0,0,0,0,100,100,0,0,3,3,0,2,315,315,60,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
    "MarginV, Effect, Text\n"
)
kw_pat = r'(\$[\d,.]+|\b\d+(?:,\d+)*(?:\.\d+)?%?\b|比特幣|以太幣|以太坊|BTC|ETH|SOL|XRP|ADA|ZEC|RSI|均線|恐慌與貪婪指數|超賣|超買|支撐|壓力|風險提醒|投資建議)'
punct_pat = r'[，。！？、；：「」『』（）【】《》—…!?;:\(\)\[\]]+|(?<!\d)[,\.](?!\d)'
cur_t, evs = intro_dur, []
speech_elapsed = 0.0
slide_start_times = {}
speaker_intervals = {"F": [], "M": []}
for idx, _, txt, dur in results:
    role = dialogues[idx][0]
    if role in ("女", "F"):
        speaker = "F"
    elif role in ("男", "M"):
        speaker = "M"
    else:
        raise RuntimeError(f"Unknown dialogue speaker at index {idx}: {role!r}")
    speaker_start = cur_t
    speaker_end = speaker_start + dur
    intervals = speaker_intervals[speaker]
    if intervals and abs(intervals[-1][1] - speaker_start) < 1e-6:
        intervals[-1] = (intervals[-1][0], speaker_end)
    else:
        intervals.append((speaker_start, speaker_end))

    for slide_number, anchor_position in slide_anchor_positions.items():
        anchor_dialogue_index = int(anchor_position)
        if idx == anchor_dialogue_index:
            fractional_offset = anchor_position - anchor_dialogue_index
            slide_start_times[slide_number] = (
                intro_video_duration + speech_elapsed + dur * fractional_offset
            )
    tw_txt = cc.convert(txt)

    split_txt = [part.strip() for part in re.split(r'[，。！？、；：…!?;\n]+', tw_txt) if part.strip()]
    sub_segments = []
    for segment in split_txt:
        if len(segment) > 24:
            sub_segments.extend([segment[i:i + 20].strip() for i in range(0, len(segment), 20) if segment[i:i + 20].strip()])
        else:
            sub_segments.append(segment)

    sub_segments = [re.sub(r'\s+', ' ', segment).strip() for segment in sub_segments if segment.strip()]
    total_chars = sum(len(segment) for segment in sub_segments)
    if total_chars == 0:
        cur_t += dur
        speech_elapsed += dur
        continue

    segment_cur = cur_t
    for index, segment in enumerate(sub_segments):
        sub_txt = re.sub(r'\s+', ' ', re.sub(punct_pat, ' ', segment)).strip()
        if not sub_txt:
            continue

        if index == len(sub_segments) - 1:
            sub_end = cur_t + dur
        else:
            sub_end = segment_cur + dur * (len(segment) / total_chars)

        hl = re.sub(kw_pat, r'{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H00FFFFFF&}', sub_txt)
        evs.append(f"Dialogue: 0,{fmt_ass_t(segment_cur)},{fmt_ass_t(sub_end)},Default,,315,315,0,,{hl}")
        segment_cur = sub_end

    cur_t += dur
    speech_elapsed += dur
speech_end_t = cur_t
if set(slide_start_times) != set(range(1, SLIDE_COUNT + 1)):
    raise RuntimeError(
        "Not all slide anchors were mapped to synthesized dialogue: "
        f"{sorted(slide_start_times)}."
    )
with open("subs.ass", "w", encoding="utf-8") as f:
    f.write(ass_head + "\n".join(evs))

plot_tick_font = FontProperties(size=14, weight="bold")
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.4, 5.0), dpi=160)
fig.patch.set_facecolor("#111827")
for ax, name, col in [(ax1, "BTC", "#38bdf8"), (ax2, "ETH", "#a78bfa")]:
    cl = cdata[name][0][-100:]
    ma = [sum(cl[max(0, i-19):i+1])/len(cl[max(0, i-19):i+1]) for i in range(len(cl))]
    ax.set_facecolor("#0f172a")
    ax.plot(cl, color=col, linewidth=2.2, label=f"{name} Close")
    ax.plot(ma, color="#fbbf24", linewidth=1.4, linestyle="--", label="20D MA")
    ax.set_title(
        f"{name} 100D Trend", color="white", fontsize=18,
        fontweight="bold",
    )
    ax.tick_params(colors="#94a3b8", labelsize=14)
    for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
        tick_label.set_fontproperties(plot_tick_font)
    ax.grid(color="#1e293b", linewidth=0.6)
    ax.legend(
        facecolor="#1e293b", edgecolor="none", labelcolor="white",
        prop=plot_tick_font,
    )
    for sp in ["top", "right"]: ax.spines[sp].set_visible(False)
    for sp in ["bottom", "left"]: ax.spines[sp].set_color("#334155")
plt.tight_layout()
plt.savefig("chart_dual.png", facecolor="#111827")
plt.close()

fig, ax = plt.subplots(figsize=(8.4, 5.0), dpi=160)
fig.patch.set_facecolor("#111827")
ax.set_facecolor("#0f172a")
names = list(cdata.keys())
pcts = [cdata[n][1] for n in names]
bars = ax.barh(
    names[::-1], pcts[::-1],
    color=["#34d399" if p >= 0 else "#f43f5e" for p in pcts][::-1],
    height=0.55,
)
for bar, pct in zip(bars, pcts[::-1]):
    ax.annotate(
        f"{pct:+.2f}%",
        xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
        xytext=(6 if pct >= 0 else -6, 0),
        textcoords="offset points",
        ha="left" if pct >= 0 else "right",
        va="center",
        color="white",
        fontsize=14,
        fontweight="bold",
    )
spread = max(max(pcts) - min(pcts), 1)
ax.set_xlim(min(0, min(pcts)) - spread * 0.2, max(0, max(pcts)) + spread * 0.2)
ax.axvline(0, color="#64748b", linewidth=1)
ax.set_title(
    "24H Price Change (%)", color="white", fontsize=18,
    fontweight="bold",
)
ax.tick_params(colors="white", labelsize=14)
for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
    tick_label.set_fontproperties(plot_tick_font)
ax.grid(axis="x", color="#1e293b", linewidth=0.6)
for sp in ["top", "right"]: ax.spines[sp].set_visible(False)
for sp in ["bottom", "left"]: ax.spines[sp].set_color("#334155")
plt.tight_layout()
plt.savefig("chart_bars.png", facecolor="#111827")
plt.close()
fp = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
if not os.path.exists(fp): fp = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
fh, fs, ft, fb, fn = ImageFont.truetype(fp, 60), ImageFont.truetype(fp, 36), ImageFont.truetype(fp, 51), ImageFont.truetype(fp, 42), ImageFont.truetype(fp, 33)

if has_intro_video:
    intro_frame = Image.new(
        "RGB", (MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT), "#090d16"
    )
    intro_draw = ImageDraw.Draw(intro_frame)
    intro_draw.rectangle((30, 30, 1890, 1050), outline="#fbbf24", width=6)
    intro_draw.rectangle((606, 74, 1314, 782), outline="#fbbf24", width=6)
    intro_draw.line((60, 60, 270, 60), fill="#38bdf8", width=3)
    intro_draw.line((1650, 1020, 1860, 1020), fill="#38bdf8", width=3)
    intro_draw.text(
        (960, 837),
        "幣看趨勢｜比特幣與加密貨幣情報",
        fill="#f8fafc",
        font=fh,
        anchor="mm",
    )
    intro_draw.text(
        (960, 918),
        f"以太喵 ＆ 比特汪・每日市場深度解析（{today_str}）",
        fill="#fbbf24",
        font=ft,
        anchor="mm",
    )
    intro_frame.save("intro_frame.png")

s1 = f"BTC 即時價格：${cur_p} USD\n日線均線：100日 ${ma100d} / 200日 ${ma200d}\n週線均線：100週 ${ma100w} / 200週 ${ma200w}\n14日 RSI：{btc_rsi}"
eth_slide = f"ETH 即時價格：${spot_prices['ETH']:,.2f} USD ({eth_pct:+.2f}% 24H)\n日線均線：100日 ${eth_ma100d} / 200日 ${eth_ma200d}\n14日 RSI：{eth_rsi}"
if today_fng:
    fng_num = int(today_fng["value"])
    if yest_fng:
        fng_yesterday = int(yest_fng["value"])
        fng_change = fng_num - fng_yesterday
        fng_direction = "升高" if fng_change > 0 else "降低" if fng_change < 0 else "持平"
        sentiment_slide = (
            f"今日：{fng_num}（{today_fng['value_classification']}）\n"
            f"昨日：{fng_yesterday}，{fng_direction} {abs(fng_change)} 點"
        )
    else:
        sentiment_slide = (
            f"今日：{fng_num}（{today_fng['value_classification']}）\n"
            "昨日資料未取得，略過日變化比較。"
        )
else:
    fng_num = None
    sentiment_slide = "本次未取得通過新鮮度驗證的指數資料，略過數值與方向判讀。"
conditions_24h = slide_24h_conditions


def split_news_summary(summary):
    cleaned = re.sub(r"^\s*多空打折摘要\s*", "", str(summary)).strip()
    cleaned = remove_publish_time(cleaned)
    match = re.search(
        r"利多[：:]\s*(.*?)\s*[；;]\s*(?:風險|理性折價)[：:]\s*(.*)",
        cleaned,
        flags=re.DOTALL,
    )
    if match:
        return remove_publish_time(match.group(1).strip()), remove_publish_time(match.group(2).strip())

    return (
        remove_publish_time(cleaned),
        "利多敘事仍需由後續價格、量能與實際進展驗證，並留意預期落空風險。",
    )


news_slides = [
    (
        clean_slide_title(slide_news_details[index]["slide_title"]),
        "#fbbf24",
        slide_news[index],
        "compare",
    )
    for index in range(3)
]
condition_slides = [
    (f"未來 24 小時（今日至明晨）觀察條件 {index + 1}", "#f472b6", condition, "focus")
    for index, condition in enumerate(conditions_24h)
]
slides_cfg = [
    ("開場｜盤勢觀察與投資風險提醒", "#fb7185", "本節目內容為公開資訊整理，不構成投資建議。\n加密貨幣波動劇烈，請獨立思考並自負風險。", None),
]
if show_dca:
    slides_cfg.append(
        ("BTC 長期定投（DCA）模擬帳本成績單", "#fbbf24", dca_slide_body, "dca")
    )
slides_cfg += [
    ("市場情緒｜恐慌與貪婪指數", "#38bdf8", sentiment_slide, "gauge"),
    (
        "BTC 動態｜均線、RSI 與歷史超賣",
        "#38bdf8",
        f"{s1}\n{btc_oversold_report}",
        "btc_rsi",
    ),
    ("ETH 動態｜日線均線與 RSI", "#a78bfa", eth_slide, "dual"),
] + news_slides + [
    ("六大主流幣｜報價與指標總表", "#34d399", cc.convert(coins_summary), None),
    ("六大主流幣｜24H 漲跌幅", "#34d399", "即時報價、漲跌幅與 RSI 對照", "bars"),
    (
        "BTC 價格結構與關鍵攻防",
        "#a78bfa",
        f"{btc_technical_report}\n{eth_technical_report}",
        "btc_structure",
    ),
] + condition_slides + [
    ("24小時策略｜先驗證、再行動", "#fbbf24", "核對價格與均線、量能結構及市場情緒。\n依可驗證訊號控管部位，避免追高殺低。", "focus"),
]

def wrap_by_width(draw, text, font, max_width):
    wrapped_lines = []
    for paragraph in text.split("\n"):
        line = ""
        for ch in paragraph:
            candidate = line + ch
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if line and bbox[2] - bbox[0] > max_width:
                wrapped_lines.append(line)
                line = ch
            else:
                line = candidate
        wrapped_lines.append(line)
    return wrapped_lines


_slide_browser = None


def highlight_key_numbers(text, state=None):
    escaped_text = html.escape(str(text))
    highlight_state = state if state is not None else {"remaining": 3}
    important_number_pattern = re.compile(
        r"(?P<money>[-+]?(?:NT\$|US\$|USD\$|\$)\s?[-+]?\d+(?:,\d{3})*(?:\.\d+)?)"
        r"|(?P<metric_label>(?:恐慌(?:與貪婪)?指數|市場情緒指數|RSI)\s*[:：]?\s*)"
        r"(?P<metric_value>[+-]?\d+(?:\.\d+)?(?:\s*/\s*100)?%?)"
        r"|(?P<percent>[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s?%)"
    )

    def highlight(match):
        if highlight_state.get("remaining", 0) <= 0:
            return match.group(0)
        if match.group("money"):
            result = f'<span class="hl">{match.group("money")}</span>'
        elif match.group("metric_label"):
            result = (
                f'{match.group("metric_label")}<span class="hl">'
                f'{match.group("metric_value")}</span>'
            )
        else:
            result = f'<span class="hl">{match.group("percent")}</span>'
        highlight_state["remaining"] -= 1
        return result

    return important_number_pattern.sub(highlight, escaped_text)


def render_animated_slide(idx, slide_data, dur_sec, out_mp4):
    if _slide_browser is None:
        raise RuntimeError("Playwright Chromium must be started before rendering slides.")
    if dur_sec <= 0:
        raise ValueError(f"Slide {idx} duration must be positive.")

    highlights = slide_data.get("highlights", [])
    if isinstance(highlights, str):
        highlights = highlights.splitlines()
    highlight_state = {"remaining": 3}
    title_markup = highlight_key_numbers(
        clean_slide_title(slide_data.get("title", "")), highlight_state
    )
    chart_markup = ""
    chart_path = slide_data.get("chart_path")
    if chart_path:
        with open(chart_path, "rb") as chart_file:
            chart_data = base64.b64encode(chart_file.read()).decode("ascii")
        chart_markup = (
            '<div class="visual chart"><img alt="" '
            f'src="data:image/png;base64,{chart_data}"></div>'
        )
    elif slide_data.get("chart_type") == "gauge":
        raw_gauge_value = slide_data.get("gauge_value")
        if raw_gauge_value is None:
            chart_markup = (
                '<div class="visual gauge">'
                '<div class="gauge-title">市場情緒指數</div>'
                '<div class="gauge-value">本次未取得通過時效驗證的資料</div>'
                '</div>'
            )
        else:
            gauge_value = min(100, max(0, int(raw_gauge_value)))
            chart_markup = (
                '<div class="visual gauge">'
                '<div class="gauge-title">市場情緒指數</div>'
                '<div class="gauge-track">'
                '<span class="fear"></span><span class="caution"></span>'
                '<span class="neutral"></span><span class="greed"></span>'
                '<span class="extreme-greed"></span></div>'
                f'<div class="gauge-marker" style="left:{gauge_value}%"></div>'
                f'<div class="gauge-value">{gauge_value}<small> / 100</small></div>'
                '</div>'
            )

    layout = slide_data.get("layout")
    if layout not in {"chart", "compare", "hero"}:
        layout = "chart" if chart_markup else "hero"
    if layout == "chart" and not chart_markup:
        layout = "hero"
    if layout == "chart":
        highlights = highlights[:5]

    list_markup = ""
    if layout != "compare":
        list_markup = "".join(
            f"<li>{highlight_key_numbers(clean_slide_text(item), highlight_state)}</li>"
            for item in highlights
            if str(item).strip()
        )
    slide_caption = clean_slide_text(str(slide_data.get("caption", ""))).strip()
    chart_caption_markup = (
        f'<li class="chart-caption">{html.escape(slide_caption)}</li>'
        if slide_caption and layout == "chart"
        else ""
    )

    if layout == "compare":
        positive_markup = highlight_key_numbers(
            clean_slide_text(slide_data.get("compare_positive", "")), highlight_state
        )
        risk_markup = highlight_key_numbers(
            clean_slide_text(slide_data.get("compare_risk", "")), highlight_state
        )
        content_markup = (
            '<section class="content compare-layout">'
            '<article class="compare-panel positive">'
            '<h2>市場焦點與利多敘事</h2>'
            f'<ul><li>{positive_markup}</li></ul></article>'
            '<article class="compare-panel risk">'
            '<h2>以太喵理性打折與隱藏風險</h2>'
            f'<ul><li>{risk_markup}</li></ul></article>'
            '</section>'
        )
    elif layout == "chart":
        content_markup = (
            '<section class="content chart-layout">'
            f'{chart_markup}<ul>{list_markup}{chart_caption_markup}</ul></section>'
        )
    else:
        content_markup = (
            f'<section class="content hero-layout"><ul>{list_markup}</ul></section>'
        )

    frame_class = "frame compact" if slide_data.get("compact") else "frame"
    label_markup = html.escape(str(slide_data.get("label", "")))
    accent = html.escape(str(slide_data.get("accent", "#fbbf24")))
    ticker_markup = "".join(
        '<div class="ticker-item">'
        f'<span class="ticker-label">{html.escape(str(item.get("label", "")))}</span>'
        f'<strong class="{html.escape(str(item.get("tone", "neutral")))}">'
        f'{html.escape(str(item.get("value", "")))}</strong>'
        f'<span class="ticker-detail {html.escape(str(item.get("tone", "neutral")))}">'
        f'{html.escape(str(item.get("detail", "")))}</span></div>'
        for item in slide_data.get("ticker_items", [])
    )
    record_seconds = min(float(dur_sec), 6.0)
    remaining = max(0.0, float(dur_sec) - record_seconds)
    tpad_filter = (
        f",tpad=stop_mode=clone:stop_duration={remaining:.6f}"
        if remaining > 0 else ""
    )
    html_doc = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={MAIN_VIDEO_WIDTH}, initial-scale=1">
<style>
* {{ box-sizing: border-box; }}
html, body {{ width: {MAIN_VIDEO_WIDTH}px; height: {MAIN_VIDEO_HEIGHT}px; margin: 0; overflow: hidden; }}
body {{
  position: relative; width: {MAIN_VIDEO_WIDTH}px; height: {MAIN_VIDEO_HEIGHT}px;
  color: #f8fafc; background: #090d16;
  font-family: 'Noto Sans CJK TC', sans-serif;
}}
.slide-stage {{
  position: relative; width: {MAIN_DESIGN_WIDTH}px; height: {MAIN_DESIGN_HEIGHT}px;
  overflow: hidden; background: #090d16;
  transform: scale({MAIN_OUTPUT_SCALE}); transform-origin: top left;
}}
.slide-stage::before {{
  position: absolute; inset: -20%; content: ""; opacity: .46;
  background:
    radial-gradient(ellipse at 18% 22%, rgba(56,189,248,.18), transparent 34%),
    radial-gradient(ellipse at 82% 55%, rgba(167,139,250,.15), transparent 38%),
    linear-gradient(rgba(56,189,248,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56,189,248,.045) 1px, transparent 1px);
  background-size: auto, auto, 48px 48px, 48px 48px;
  animation: gridFlow 18s linear infinite;
}}
@keyframes gridFlow {{
  from {{ transform: translate3d(0, 0, 0); }}
  to {{ transform: translate3d(48px, 24px, 0); }}
}}
.ticker {{
  position: absolute; inset: 0 0 auto; z-index: 2; height: 46px;
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: center; padding: 0 26px;
  background: rgba(15, 23, 42, .92);
  border-bottom: 1px solid rgba(251, 191, 36, .72);
  box-shadow: 0 2px 14px rgba(251, 191, 36, .12);
  backdrop-filter: blur(12px);
}}
.ticker-item {{
  min-width: 0; display: flex; justify-content: center; align-items: baseline;
  gap: 8px; padding: 0 10px; white-space: nowrap; overflow: hidden;
}}
.ticker-item + .ticker-item {{ border-left: 1px solid rgba(148, 163, 184, .22); }}
.ticker-label {{ color: #94a3b8; font-size: 12px; }}
.ticker-item strong {{ color: #f8fafc; font-size: 15px; font-variant-numeric: tabular-nums; }}
.ticker-item strong.positive {{ color: #4ade80; }}
.ticker-item strong.negative {{ color: #fb7185; }}
.ticker-item strong.neutral {{ color: #f8fafc; }}
.ticker-detail {{ font-size: 12px; overflow: hidden; text-overflow: ellipsis; }}
.ticker-detail.positive {{ color: #4ade80; }}
.ticker-detail.negative {{ color: #fb7185; }}
.ticker-detail.neutral {{ color: #cbd5e1; }}
.sr-only {{
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}}
.frame {{
  position: absolute; left: 60px; right: 60px; top: 62px; bottom: 118px;
  padding: 28px 40px; border: 2px solid rgba(251,191,36,.78);
  border-radius: 18px; background: rgba(15,23,42,.86);
  box-shadow: 0 0 34px rgba(56,189,248,.12), inset 0 0 34px rgba(251,191,36,.035);
  display: flex; flex-direction: column; overflow: hidden;
}}
h1 {{
  position: relative; flex: 0 0 auto; max-width: 100%; margin: 0 0 20px;
  color: var(--accent); font-size: 39px; line-height: 1.2; font-weight: 800;
  animation: titleIn .8s cubic-bezier(.2,.8,.2,1) both;
}}
@keyframes titleIn {{
  from {{ opacity: 0; transform: translateX(-58px); }}
  to {{ opacity: 1; transform: translateX(0); }}
}}
.content {{
  min-height: 0; flex: 1 1 auto; display: grid;
  grid-template-columns: minmax(0, 1fr); align-items: center;
}}
.chart-layout {{ grid-template-columns: minmax(0, 54fr) minmax(0, 46fr); gap: 18px; }}
.compare-layout {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; align-items: stretch; }}
ul {{ list-style: none; margin: 0; padding: 0; min-width: 0; }}
li {{
  position: relative; margin: 0 0 12px; padding-left: 26px;
  color: #e2e8f0; font-size: 26px; line-height: 1.36; overflow-wrap: anywhere;
  animation: fadeInUp .65s ease-out both;
}}
li::before {{
  position: absolute; top: .56em; left: 0; width: 9px; height: 9px;
  border-radius: 50%; background: var(--accent); content: "";
  box-shadow: 0 0 12px var(--accent);
}}
li:nth-child(1) {{ animation-delay: .35s; }}
li:nth-child(2) {{ animation-delay: .95s; }}
li:nth-child(3) {{ animation-delay: 1.55s; }}
li:nth-child(4) {{ animation-delay: 2.15s; }}
li:nth-child(5) {{ animation-delay: 2.75s; }}
li:nth-child(n+6) {{ animation-delay: 3.35s; }}
.frame.compact h1 {{ font-size: 32px; margin-bottom: 12px; }}
.frame.compact li {{ margin-bottom: 5px; padding-left: 22px; font-size: 20px; line-height: 1.2; }}
.frame.compact li::before {{ width: 7px; height: 7px; }}
.chart-layout > ul {{
  display: grid; grid-template-columns: 1fr; align-content: center;
  gap: 12px; text-align: left;
}}
.frame .chart-layout > ul li {{
  margin: 0; font-size: 23px; line-height: 1.45;
}}
.chart-layout > ul li.chart-caption {{
  margin-top: 2px; padding: 10px 12px; border: 1px solid rgba(251,191,36,.42);
  border-radius: 10px; color: #fde68a; background: rgba(120,83,9,.18);
  font-size: 17px; line-height: 1.35;
}}
.chart-layout > ul li.chart-caption::before {{ display: none; }}
.content.dense-grid ul {{
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start; gap: 8px 12px; text-align: left;
}}
.content.dense-grid li {{ margin: 0; padding: 8px 12px; font-size: 22px; }}
.frame.fit-compact {{ padding: 18px 24px; }}
.frame.fit-compact h1 {{ margin-bottom: 12px; font-size: 30px; }}
.frame.fit-compact .content {{ row-gap: 10px; column-gap: 12px; }}
.frame.fit-tight {{ padding: 10px 14px; }}
.frame.fit-tight h1 {{ margin-bottom: 6px; font-size: 26px; }}
.frame.fit-tight .chart-layout, .frame.fit-tight .compare-layout {{ gap: 8px; }}
.frame.fit-tight .compare-panel {{ padding: 12px; }}
.frame.fit-tight .compare-panel h2 {{ margin-bottom: 8px; font-size: 18px; }}
.frame.fit-tight .content.dense-grid ul {{ gap: 8px; }}
.frame.fit-tight .content.dense-grid li {{ padding: 8px 10px; }}
.frame.fit-tight .chart-layout > ul li.chart-caption {{ padding: 7px 9px; font-size: 15px; }}
.frame.fit-min {{ padding: 4px 8px; }}
.frame.fit-min h1 {{ margin-bottom: 3px; font-size: 20px; }}
.frame.fit-min .chart-layout, .frame.fit-min .compare-layout {{ gap: 4px; }}
.frame.fit-min .compare-panel {{ padding: 6px; }}
.frame.fit-min .compare-panel h2 {{ margin-bottom: 4px; font-size: 15px; }}
.frame.fit-min li {{ margin-bottom: 1px; padding-left: 10px; font-size: 18px; line-height: 1.05; }}
.frame.fit-min .content.dense-grid ul {{ gap: 4px; }}
.frame.fit-min .content.dense-grid li {{ padding: 4px 6px; }}
.frame.fit-min .chart-layout > ul li.chart-caption {{ padding: 4px 6px; font-size: 13px; }}
@keyframes fadeInUp {{
  from {{ opacity: 0; transform: translateY(24px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
.hl {{ position: relative; z-index: 0; color: #fff7cc; white-space: nowrap; }}
.hl::after {{
  position: absolute; left: 0; bottom: -.08em; z-index: -1; width: 100%; height: .34em;
  background: linear-gradient(90deg, rgba(251,191,36,.82), rgba(251,191,36,.18));
  content: ""; transform: scaleX(0); transform-origin: left center;
  animation: highlightIn .65s ease-out .45s forwards;
}}
@keyframes highlightIn {{ to {{ transform: scaleX(1); }} }}
.visual {{
  position: relative; width: 100%; min-width: 0; height: 100%; max-height: 300px;
  display: flex; align-items: center; justify-content: center;
  animation: chartIn .9s cubic-bezier(.2,.8,.2,1) .45s both, breathe 5s ease-in-out 1.4s infinite;
}}
.chart-layout .visual {{ min-height: 300px; max-height: none; }}
.chart img {{ display: block; width: 100%; height: 100%; max-height: 410px; object-fit: contain; }}
@keyframes chartIn {{
  from {{ opacity: 0; transform: scale(.88); }}
  to {{ opacity: 1; transform: scale(1); }}
}}
@keyframes breathe {{ 0%, 100% {{ filter: drop-shadow(0 0 8px rgba(56,189,248,.16)); }} 50% {{ filter: drop-shadow(0 0 18px rgba(56,189,248,.34)); }} }}
.gauge {{ flex-direction: column; gap: 22px; }}
.gauge-title {{ color: #cbd5e1; font-size: 30px; font-weight: 700; }}
.gauge-track {{ width: 100%; height: 28px; display: flex; overflow: hidden; border-radius: 16px; }}
.gauge-track span {{ flex: 1; }}
.fear {{ background: #ef4444; }} .caution {{ background: #f97316; }}
.neutral {{ background: #eab308; }} .greed {{ background: #84cc16; }}
.extreme-greed {{ background: #22c55e; }}
.gauge-marker {{ position: absolute; top: 43%; width: 5px; height: 42px; background: white; box-shadow: 0 0 12px white; }}
.gauge-value {{ color: #fbbf24; font-size: 64px; font-weight: 800; }}
.gauge-value small {{ color: #94a3b8; font-size: 24px; font-weight: 700; }}
.compare-panel {{
  min-width: 0; padding: 22px 24px; border: 2px solid; border-radius: 16px;
  background: rgba(8, 15, 28, .72); overflow: hidden;
}}
.compare-panel.positive {{ border-color: rgba(56, 189, 248, .82); animation: compareLeft .8s cubic-bezier(.2,.8,.2,1) .25s both; }}
.compare-panel.risk {{ border-color: rgba(251, 191, 36, .82); animation: compareRight .8s cubic-bezier(.2,.8,.2,1) .65s both; }}
.compare-panel h2 {{ margin: 0 0 20px; font-size: 22px; color: #7dd3fc; }}
.compare-panel.risk h2 {{ color: #fbbf24; }}
.compare-panel li {{ margin: 0; padding-left: 22px; font-size: 25px; }}
.compare-panel li::before {{ width: 7px; height: 7px; }}
@keyframes compareLeft {{ from {{ opacity: 0; transform: translateX(-36px); }} to {{ opacity: 1; transform: translateX(0); }} }}
@keyframes compareRight {{ from {{ opacity: 0; transform: translateX(36px); }} to {{ opacity: 1; transform: translateX(0); }} }}
.hero-layout ul {{ width: 100%; display: flex; flex-direction: column; align-items: center; gap: 16px; text-align: center; }}
.hero-layout li {{
  max-width: 100%; margin: 0; padding: 16px 28px; border: 1px solid rgba(251,191,36,.38);
  border-radius: 14px; background: rgba(15,23,42,.72);
  box-shadow: 0 0 24px rgba(251,191,36,.08);
  font-size: 42px; font-weight: 750; line-height: 1.28;
  animation: heroPop .75s cubic-bezier(.16,1,.3,1) both;
}}
.hero-layout li::before {{ display: none; }}
.hero-layout li:nth-child(1) {{ animation-delay: .35s; }}
.hero-layout li:nth-child(2) {{ animation-delay: .6s; }}
.hero-layout li:nth-child(3) {{ animation-delay: .85s; }}
@keyframes heroPop {{ from {{ opacity: 0; transform: scale(.86) translateY(20px); }} to {{ opacity: 1; transform: scale(1) translateY(0); }} }}
</style>
</head>
<body>
<div class="slide-stage">
<header class="ticker" aria-label="即時行情狀態列">{ticker_markup}</header>
<main class="{frame_class}" style="--accent:{accent}" aria-label="{label_markup}">
  <h1>{title_markup}</h1>
  {content_markup}
</main>
</div>
  <script>
  window.fitSlideLayout = () => {{
    const frame = document.querySelector(".frame");
    const title = document.querySelector("h1");
    const content = document.querySelector(".content");
    const items = [...document.querySelectorAll("li")];
    const overflows = () => {{
      const contentBox = content.getBoundingClientRect();
      return content.scrollHeight > content.clientHeight + 1 ||
        items.some(item => item.scrollWidth > item.clientWidth + 1 ||
          item.getBoundingClientRect().top < contentBox.top - 1 ||
          item.getBoundingClientRect().bottom > contentBox.bottom + 1) ||
        title.scrollWidth > title.clientWidth + 1 ||
        title.getBoundingClientRect().bottom > contentBox.top - 8 ||
        frame.scrollHeight > frame.clientHeight + 1;
    }};
    if (items.length >= 6 && !content.classList.contains("chart-layout")) {{
      content.classList.add("dense-grid");
    }}
    for (let attempt = 0; attempt < 12 && overflows(); attempt++) {{
      if (attempt === 0) frame.classList.add("fit-compact");
      if (attempt === 3) frame.classList.add("fit-tight");
      if (attempt === 6) frame.classList.add("fit-min");
      const titleSize = parseFloat(getComputedStyle(title).fontSize);
      if (titleSize > 20) title.style.fontSize = `${{Math.max(20, titleSize - 2)}}px`;
      const minBodySize = content.classList.contains("chart-layout") ? 22 : 18;
      for (const item of items) {{
        const bodySize = parseFloat(getComputedStyle(item).fontSize);
        if (bodySize > minBodySize) {{
          item.style.fontSize = `${{Math.max(minBodySize, bodySize - 2)}}px`;
        }}
      }}
    }}
    if (!overflows()) return true;

    document.querySelectorAll(".compare-panel").forEach(panel => {{
      panel.style.overflow = "visible";
    }});
    frame.style.overflow = "visible";
    const frameBox = frame.getBoundingClientRect();
    const contentBox = content.getBoundingClientRect();
    const itemRects = items.map(item => item.getBoundingClientRect());
    const minLeft = Math.min(
      0, ...itemRects.map(rect => (rect.left - frameBox.left) / {MAIN_OUTPUT_SCALE})
    );
    const minTop = Math.min(
      0, ...itemRects.map(rect => (rect.top - frameBox.top) / {MAIN_OUTPUT_SCALE})
    );
    const maxRight = Math.max(
      frame.clientWidth,
      (contentBox.right - frameBox.left) / {MAIN_OUTPUT_SCALE},
      (contentBox.left - frameBox.left) / {MAIN_OUTPUT_SCALE} + content.scrollWidth,
      ...itemRects.map((rect, index) =>
        (rect.right - frameBox.left) / {MAIN_OUTPUT_SCALE} +
          Math.max(0, items[index].scrollWidth - items[index].clientWidth)
      )
    );
    const maxBottom = Math.max(
      frame.clientHeight,
      (contentBox.bottom - frameBox.top) / {MAIN_OUTPUT_SCALE},
      (contentBox.top - frameBox.top) / {MAIN_OUTPUT_SCALE} + content.scrollHeight,
      ...itemRects.map(rect => (rect.bottom - frameBox.top) / {MAIN_OUTPUT_SCALE})
    );
    const scale = Math.min(
      1,
      frame.clientWidth / Math.max(1, maxRight - minLeft),
      frame.clientHeight / Math.max(1, maxBottom - minTop)
    );
    frame.style.transformOrigin = "top left";
    frame.style.transform = `translate(${{-minLeft * scale}}px, ${{-minTop * scale}}px) scale(${{scale}})`;
    return false;
  }};
  </script>
</body>
</html>"""

    with tempfile.TemporaryDirectory(prefix=f"slide_{idx}_") as record_dir:
        context = _slide_browser.new_context(
            viewport={"width": MAIN_VIDEO_WIDTH, "height": MAIN_VIDEO_HEIGHT},
            device_scale_factor=1,
            record_video_dir=record_dir,
            record_video_size={"width": MAIN_VIDEO_WIDTH, "height": MAIN_VIDEO_HEIGHT},
        )
        page = context.new_page()
        video = page.video
        try:
            page.set_content(html_doc, wait_until="load")
            page.evaluate("() => document.fonts.ready.then(() => true)")
            if not page.evaluate("() => window.fitSlideLayout()"):
                print(f"Slide {idx} required final auto-fit scaling to stay inside its safe area.")
            page.wait_for_timeout(max(1, round(record_seconds * 1000)))
        finally:
            context.close()
        recording_path = video.path()
        filters = (
            f"fps=24,scale={MAIN_VIDEO_WIDTH}:{MAIN_VIDEO_HEIGHT}:flags=lanczos,"
            f"trim=duration={record_seconds:.6f},setpts=PTS-STARTPTS"
            f"{tpad_filter},format=yuv420p"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", recording_path,
                "-vf", filters, "-t", f"{dur_sec:.6f}",
                "-an", "-r", "24", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", out_mp4,
            ],
            check=True,
        )


def generate_topic_icon(topic_summary, coin_code):
    icon_path = "topic_icon.png"
    coin_ids = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "XRP": "ripple", "ADA": "cardano", "ZEC": "zcash",
    }
    coin_code = str(coin_code or "BTC").upper()
    image_prompt = (
        f"Create a polished, cinematic 3D finance illustration inspired by {coin_code} "
        f"and this daily market topic: {topic_summary}. No letters, no words, no logos, "
        "square composition, luminous metallic gold and deep midnight blue, premium "
        "financial editorial art."
    )

    try:
        image_prompt = call_gemini(
            "Rewrite this description as one concise English image-generation prompt. "
            "Require a square 3D financial editorial illustration with no text, letters, "
            "or logos. Return only the prompt:\n" + image_prompt
        ).strip()
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps({
                "model": "gemini-nano-banana-2.1",
                "input": image_prompt,
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip(),
                "Api-Revision": "2026-05-20",
            },
            method="POST",
        )
        response = json.loads(
            urllib.request.urlopen(request, timeout=180).read().decode("utf-8")
        )
        image_bytes = base64.b64decode(response["output_image"]["data"])
        with Image.open(io.BytesIO(image_bytes)) as generated:
            generated.convert("RGB").resize((512, 512), Image.LANCZOS).save(icon_path)
        print("Generated daily topic icon with Gemini image generation.")
        return icon_path
    except Exception as e:
        print(f"Topic image generation failed; trying the official coin icon: {e}")

    try:
        coin_id = coin_ids.get(coin_code, "bitcoin")
        coin_data = fetch_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            "?localization=false&tickers=false&market_data=false&community_data=false"
            "&developer_data=false&sparkline=false"
        )
        image_url = coin_data["image"]["large"]
        image_request = urllib.request.Request(
            image_url, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(image_request, timeout=20) as response:
            with Image.open(io.BytesIO(response.read())) as coin_image:
                coin_image.convert("RGBA").resize(
                    (512, 512), Image.LANCZOS
                ).save(icon_path)
        print(f"Using the official CoinGecko {coin_code} icon.")
        return icon_path
    except Exception as e:
        print(f"CoinGecko icon unavailable; drawing a local coin emblem: {e}")

    emblem = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(emblem)
    for radius, alpha in ((224, 18), (204, 32), (182, 52)):
        draw.ellipse(
            (256 - radius, 256 - radius, 256 + radius, 256 + radius),
            fill=(251, 191, 36, alpha),
        )
    draw.ellipse((70, 70, 442, 442), fill="#102238", outline="#fbbf24", width=16)
    draw.ellipse((92, 92, 420, 420), outline="#fde68a", width=5)
    emblem_font = ImageFont.truetype(fp, 158)
    emblem_draw = ImageDraw.Draw(emblem)
    symbol = coin_code[:4]
    bounds = emblem_draw.textbbox((0, 0), symbol, font=emblem_font)
    emblem_draw.text(
        ((512 - (bounds[2] - bounds[0])) // 2, (512 - (bounds[3] - bounds[1])) // 2 - bounds[1]),
        symbol,
        fill="#fbbf24",
        font=emblem_font,
    )
    emblem.save(icon_path)
    print("Using a locally rendered metallic topic emblem.")
    return icon_path


def create_outro_frame():
    image = Image.new("RGB", (MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT), "#090d16")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 1890, 1050), outline="#fbbf24", width=6)
    if os.path.exists("avatar.png"):
        with Image.open("avatar.png") as avatar:
            image.paste(
                avatar.convert("RGB").resize((390, 390), Image.LANCZOS),
                (765, 45),
            )

    draw = ImageDraw.Draw(image)
    title = "感謝收聽【幣看趨勢】｜別忘了按讚、訂閱、開啟小鈴鐺！"
    title_font = ImageFont.truetype(fp, 60)
    title_lines = wrap_by_width(draw, title, title_font, 1740)
    for line_index, line in enumerate(title_lines):
        bounds = draw.textbbox((0, 0), line, font=title_font)
        draw.text(
            (
                (MAIN_VIDEO_WIDTH - (bounds[2] - bounds[0])) // 2,
                472 + line_index * 75,
            ),
            line,
            fill="#f8fafc",
            font=title_font,
        )
    subtitle = "留言告訴以太喵與比特汪，下一集你想看哪一顆幣？"
    subtitle_font = ImageFont.truetype(fp, 42)
    subtitle_lines = wrap_by_width(draw, subtitle, subtitle_font, 1740)
    for line_index, line in enumerate(subtitle_lines):
        bounds = draw.textbbox((0, 0), line, font=subtitle_font)
        draw.text(
            (
                (MAIN_VIDEO_WIDTH - (bounds[2] - bounds[0])) // 2,
                630 + line_index * 57,
            ),
            line,
            fill="#fbbf24",
            font=subtitle_font,
        )

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        (90, 765, 900, 1020),
        fill=(5, 8, 18, 185),
        outline=(251, 191, 36, 210),
        width=5,
    )
    overlay_draw.rectangle(
        (1020, 765, 1830, 1020),
        fill=(5, 8, 18, 185),
        outline=(56, 189, 248, 210),
        width=5,
    )
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    image.save("outro_frame.png")


def create_speaker_avatars(talk_videos):
    def square_crop(image, top_bias=0.5):
        width, height = image.size
        crop_size = min(width, height)
        left = (width - crop_size) // 2
        top = int((height - crop_size) * top_bias)
        return image.crop((left, top, left + crop_size, top + crop_size))

    def load_portrait(speaker):
        single_image_paths = (
            (f"{speaker}_single.png", f"{speaker}_single.png.png")
        )
        for image_path in single_image_paths:
            if os.path.exists(image_path):
                with Image.open(image_path) as source:
                    return square_crop(source.convert("RGB")).copy()

        video_path = talk_videos.get(speaker)
        if video_path:
            try:
                frame_bytes = subprocess.check_output(
                    [
                        "ffmpeg", "-v", "error", "-i", video_path,
                        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
                    ],
                    stderr=subprocess.PIPE,
                )
                with Image.open(io.BytesIO(frame_bytes)) as source:
                    source.load()
                    return square_crop(source.convert("RGB")).copy()
            except (subprocess.CalledProcessError, OSError) as error:
                details = getattr(error, "stderr", None)
                if isinstance(details, bytes):
                    details = details.decode("utf-8", errors="replace")
                print(
                    f"Could not capture the first frame from {video_path}; "
                    f"trying avatar.png instead. {details or error}"
                )

        if os.path.exists("avatar.png"):
            with Image.open("avatar.png") as source:
                avatar = source.convert("RGB")
                midpoint = avatar.width // 2
                bounds = (
                    (0, 0, midpoint, avatar.height)
                    if speaker == "cat"
                    else (midpoint, 0, avatar.width, avatar.height)
                )
                return square_crop(avatar.crop(bounds), top_bias=0.25).copy()

        raise FileNotFoundError(
            f"No still image, VTuber video frame, or avatar.png is available for {speaker}."
        )

    portraits = {
        speaker: load_portrait(speaker).resize((225, 225), Image.LANCZOS)
        for speaker in ("cat", "dog")
    }
    mask_large = Image.new("L", (1008, 1008), 0)
    ImageDraw.Draw(mask_large).ellipse((3, 3, 1005, 1005), fill=255)
    face_mask = mask_large.resize((225, 225), Image.LANCZOS)
    circle_mask = Image.new("L", (252, 252), 0)
    ImageDraw.Draw(circle_mask).ellipse((0, 0, 251, 251), fill=255)
    circle_mask_path = "speaker_circle_mask.png"
    circle_mask.save(circle_mask_path)

    assets = {"circle_mask": circle_mask_path}
    for speaker, color in (("cat", (129, 140, 248)), ("dog", (251, 191, 36))):
        portrait = portraits[speaker]
        listening_face = ImageEnhance.Brightness(portrait).enhance(0.72).convert("RGBA")
        listening = Image.new("RGBA", (225, 225), (0, 0, 0, 0))
        listening.paste(listening_face, (0, 0), face_mask)
        listening_path = f"{speaker}_avatar.png"
        listening.save(listening_path)
        assets[f"{speaker}_idle"] = listening_path

        ring_glow = Image.new("RGBA", (252, 252), (0, 0, 0, 0))
        ImageDraw.Draw(ring_glow).ellipse(
            (6, 6, 246, 246), outline=(*color, 220), width=15
        )
        ring = ring_glow.filter(ImageFilter.GaussianBlur(14))
        ring_draw = ImageDraw.Draw(ring)
        ring_draw.ellipse((6, 6, 246, 246), outline=(*color, 255), width=9)
        ring_draw.ellipse((15, 15, 237, 237), outline=(255, 255, 255, 180), width=2)
        ring_path = f"{speaker}_avatar_ring.png"
        ring.save(ring_path)
        assets[f"{speaker}_ring"] = ring_path

        speaking_face = ImageEnhance.Brightness(portrait).enhance(1.18).convert("RGBA")
        speaking = Image.new("RGBA", (252, 252), (0, 0, 0, 0))
        speaking.paste(speaking_face, (14, 14), face_mask)
        speaking.alpha_composite(ring)
        speaking_path = f"{speaker}_avatar_speaking.png"
        speaking.save(speaking_path)
        assets[f"{speaker}_speaking"] = speaking_path

    return assets


def create_outro_video():
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

slide_count = len(slides_cfg)
if slide_count != SLIDE_COUNT:
    raise RuntimeError(f"Expected {SLIDE_COUNT} slides, found {slide_count}.")
slideshow_duration = speech_elapsed
if slideshow_duration <= 0:
    raise RuntimeError("No dialogue audio is available for the animated slides.")
slide_durations = calculate_slide_durations(
    slide_start_times, slideshow_duration, intro_video_duration
)
dca_return_pct = float(dca_portfolio.get("total_return_pct", 0.0))
fng_tone = (
    "negative" if fng_num <= 40 else "positive" if fng_num >= 60 else "neutral"
) if fng_num is not None else "neutral"
ticker_items = [
    {
        "label": "BTC",
        "value": f"${float(cur_p):,.2f}",
        "detail": f"{btc_pct:+.2f}% 24H",
        "tone": "positive" if btc_pct >= 0 else "negative",
    },
    {
        "label": "ETH",
        "value": f"${spot_prices['ETH']:,.2f}",
        "detail": f"{eth_pct:+.2f}% 24H",
        "tone": "positive" if eth_pct >= 0 else "negative",
    },
    {
        "label": "恐慌貪婪指數",
        "value": str(fng_num) if fng_num is not None else "未取得",
        "detail": (
            str(today_fng["value_classification"])
            if today_fng else "資料未通過時效驗證"
        ),
        "tone": fng_tone,
    },
    {
        "label": "DCA 實戰帳本",
        "value": f"{dca_return_pct:+.2f}%",
        "detail": "累計總報酬",
        "tone": "positive" if dca_return_pct >= 0 else "negative",
    },
]

with sync_playwright() as playwright:
    _slide_browser = playwright.chromium.launch()
    try:
        for idx, (title, color, body, ctype) in enumerate(slides_cfg, 1):
            chart_path = (
                chart_dca_path if ctype == "dca"
                else "chart_bars.png" if ctype == "bars"
                else "chart_dual.png" if ctype == "dual"
                else technical_chart_paths["price"] if ctype == "btc_structure"
                else technical_chart_paths["rsi"] if ctype == "btc_rsi"
                else None
            )
            layout = (
                "compare" if ctype == "compare"
                else "chart" if chart_path or ctype == "gauge"
                else "hero"
            )
            compare_positive, compare_risk = (
                split_news_summary(body) if layout == "compare" else ("", "")
            )
            slide_data = {
                "label": f"幣看趨勢｜比特幣與加密貨幣情報　{today_str}",
                "title": title,
                "highlights": (
                    [] if layout == "compare"
                    else [line for line in body.splitlines() if line.strip()]
                ),
                "chart_path": chart_path,
                "chart_type": ctype,
                "gauge_value": fng_num,
                "caption": dca_slide_caption if ctype == "dca" else "",
                "accent": color,
                "compact": ctype == "dca",
                "layout": layout,
                "compare_positive": compare_positive,
                "compare_risk": compare_risk,
                "ticker_items": ticker_items,
            }
            render_animated_slide(
                idx, slide_data, slide_durations[idx - 1], f"p{idx}.mp4"
            )
    finally:
        try:
            _slide_browser.close()
        finally:
            _slide_browser = None
            technical_chart_dir.cleanup()

create_outro_frame()
if has_intro_video:
    frame_count_output = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=nb_read_frames",
            "-of", "default=noprint_wrappers=1:nokey=1", "info.mp4",
        ]
    ).decode().strip()
    source_frame_count = int(frame_count_output)
    if source_frame_count <= 0:
        raise RuntimeError("info.mp4 does not contain decodable video frames.")
    boomerang_frames = source_frame_count * 2
    intro_filter = (
        "[1:v]setpts=PTS-STARTPTS,"
        "scale=660:660:force_original_aspect_ratio=decrease,"
        "pad=660:660:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        "split=2[forward][backward];"
        "[backward]reverse,setpts=PTS-STARTPTS[reversed];"
        f"[forward][reversed]concat=n=2:v=1:a=0,"
        f"loop=loop=-1:size={boomerang_frames}:start=0,"
        "fps=24,trim=duration=15,setpts=PTS-STARTPTS[animation];"
        "[0:v][animation]overlay=630:98:shortest=1,"
        "fade=t=out:st=14:d=1,format=yuv420p[output]"
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

create_outro_video()
with open("list.txt", "w", encoding="utf-8") as f:
    if has_intro_video:
        f.write("file 'intro.mp4'\n")
    for i in range(1, slide_count + 1):
        f.write(f"file 'p{i}.mp4'\n")
    f.write("file 'outro.mp4'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "base.mp4"], check=True)
talk_videos = {
    "cat": "cat_vtober.mp4" if os.path.exists("cat_vtober.mp4") else None,
    "dog": "dor_vtober.mp4" if os.path.exists("dor_vtober.mp4") else None,
}
speaker_avatar_paths = create_speaker_avatars(talk_videos)
cat_speaking = "+".join(
    f"between(t,{start:.6f},{end:.6f})"
    for start, end in speaker_intervals["F"]
) or "0"
dog_speaking = "+".join(
    f"between(t,{start:.6f},{end:.6f})"
    for start, end in speaker_intervals["M"]
) or "0"
speech_window = f"between(t,{intro_dur:.6f},{speech_end_t:.6f})"
mux_command = ["ffmpeg", "-y", "-i", "base.mp4", "-i", "podcast.mp3"]
input_indexes = {}


def add_avatar_input(name, path, is_video=False):
    input_index = len(input_indexes) + 2
    if is_video:
        mux_command.extend(["-stream_loop", "-1", "-i", path])
    else:
        mux_command.extend(["-loop", "1", "-framerate", "24", "-i", path])
    input_indexes[name] = input_index
    return input_index


add_avatar_input("cat_idle", speaker_avatar_paths["cat_idle"])
add_avatar_input(
    "cat_talk",
    talk_videos["cat"] or speaker_avatar_paths["cat_speaking"],
    is_video=bool(talk_videos["cat"]),
)
add_avatar_input("dog_idle", speaker_avatar_paths["dog_idle"])
add_avatar_input(
    "dog_talk",
    talk_videos["dog"] or speaker_avatar_paths["dog_speaking"],
    is_video=bool(talk_videos["dog"]),
)
if any(talk_videos.values()):
    add_avatar_input("circle_mask", speaker_avatar_paths["circle_mask"])
for speaker in ("cat", "dog"):
    if talk_videos[speaker]:
        add_avatar_input(f"{speaker}_ring", speaker_avatar_paths[f"{speaker}_ring"])

avatar_filters = ["[0:v]ass=subs.ass[v_sub]"]
active_speakers = [speaker for speaker in ("cat", "dog") if talk_videos[speaker]]
if len(active_speakers) == 2:
    avatar_filters.append(
        f"[{input_indexes['circle_mask']}:v]format=gray,split=2"
        "[cat_mask][dog_mask]"
    )
elif active_speakers:
    speaker = active_speakers[0]
    avatar_filters.append(
        f"[{input_indexes['circle_mask']}:v]format=gray[{speaker}_mask]"
    )
for speaker in active_speakers:
    input_index = input_indexes[f"{speaker}_talk"]
    avatar_filters.extend(
        [
            f"[{input_index}:v]fps=24,"
            "scale=252:252:force_original_aspect_ratio=increase,"
            f"crop=252:252,setsar=1,format=rgba[{speaker}_square]",
            f"[{speaker}_square][{speaker}_mask]alphamerge[{speaker}_talk_circle]",
        ]
    )

cat_talk_input = (
    "[cat_talk_circle]" if talk_videos["cat"]
    else f"[{input_indexes['cat_talk']}:v]"
)
dog_talk_input = (
    "[dog_talk_circle]" if talk_videos["dog"]
    else f"[{input_indexes['dog_talk']}:v]"
)
avatar_filters.extend(
    [
        f"[v_sub][{input_indexes['cat_idle']}:v]overlay=x=42:y=817.5:"
        f"enable='{speech_window}*not({cat_speaking})'[v_cat_idle]",
        f"[v_cat_idle]{cat_talk_input}"
        f"overlay=x=42:y='804+6*sin(t*14)':eval=frame:"
        f"enable='{cat_speaking}'[v_cat_talk]",
    ]
)
cat_stage = "v_cat_talk"
if talk_videos["cat"]:
    avatar_filters.append(
        f"[{cat_stage}][{input_indexes['cat_ring']}:v]"
        f"overlay=x=42:y='804+6*sin(t*14)':eval=frame:"
        f"enable='{cat_speaking}'[v_cat_speaking]"
    )
    cat_stage = "v_cat_speaking"
avatar_filters.append(
    f"[{cat_stage}][{input_indexes['dog_idle']}:v]overlay=x=1653:y=817.5:"
    f"enable='{speech_window}*not({dog_speaking})'[v_dog_idle]"
)
avatar_filters.append(
    f"[v_dog_idle]{dog_talk_input}"
    f"overlay=x=1653:y='804+6*sin(t*16)':eval=frame:"
    f"enable='{dog_speaking}'[v_dog_talk]"
)
dog_stage = "v_dog_talk"
if talk_videos["dog"]:
    avatar_filters.append(
        f"[{dog_stage}][{input_indexes['dog_ring']}:v]"
        f"overlay=x=1653:y='804+6*sin(t*16)':eval=frame:"
        f"enable='{dog_speaking}'[v_dog_speaking]"
    )
    dog_stage = "v_dog_speaking"
avatar_filters.append(f"[{dog_stage}]null[v_out]")
avatar_filter = ";".join(avatar_filters)
mux_command.extend(
    [
        "-filter_complex", avatar_filter,
        "-map", "[v_out]", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-threads", "4", "-r", "24", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "output.mp4",
    ]
)
subprocess.run(mux_command, check=True)

actual_video_duration = intro_video_duration + slideshow_duration + OUTRO_PAUSE + OUTRO_DURATION
def fmt_chapter(sec):
    sec = max(0, min(int(round(sec)), int(round(actual_video_duration))))
    minutes, seconds = divmod(sec, 60)
    return f"{minutes:02d}:{seconds:02d}"

chapter_indexes = [1]
chapter_entries = [(0.0, "主題動畫片頭")] if has_intro_video else []
for slide_number in range(2, slide_count + 1):
    if (
        round(slide_start_times[slide_number])
        - round(slide_start_times[chapter_indexes[-1]]) >= 10
    ):
        chapter_indexes.append(slide_number)
for slide_number in chapter_indexes:
    chapter_entries.append(
        (slide_start_times[slide_number], slides_cfg[slide_number - 1][0])
    )
chapter_entries.append(
    (intro_video_duration + slideshow_duration + OUTRO_PAUSE, "片尾互動與推薦影片")
)
chapter_times = [fmt_chapter(start) for start, _ in chapter_entries]
chapter_lines = "\n".join(
    f"{chapter_times[position]} {title}"
    for position, (_, title) in enumerate(chapter_entries)
)

dca_description_section = (
    f"\n\n📊 BTC 長期定投（DCA）實戰帳本：\n{dca_report}"
    if show_dca else ""
)
v_desc = f"歡迎收聽【幣看趨勢｜比特幣與加密貨幣情報】！本集為您彙整 {today_str} 最新市場指標與網路公開重點議題，由「以太喵」與「比特汪」帶你透過多空理性打折視角掌握幣圈脈動！頻道以長期持有與紀律DCA為核心，不鼓勵盲目短線或高槓桿交易。\n\n⏱️ 本集簡報章節（依{slide_count}張圖卡時間對齊，含12秒動態片尾）：\n{chapter_lines}\n\n📊 BTC 核心數據：\n{s1}{dca_description_section}\n\n📊 ETH 核心數據：\n{eth_slide}\n\n⚠️ 免責聲明：\n本頻道所有內容均為網路公開資訊與市場數據整理分享，不構成任何形式之投資建議或買賣邀約。加密貨幣市場波動劇烈，投資前請務必自行研究並審慎評估風險。\n\n#比特幣 #加密貨幣 #區塊鏈 #BTC #ETH #SOL #技術分析 #均線 #RSI #恐慌指數 #幣看趨勢 #DCA"

topic_icon_path = generate_topic_icon(slide_news[0], "BTC")
creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(), token_uri="".join(["https", "://", "oauth2.", "googleapis.", "com", "/token"]), client_id=os.environ["YT_CLIENT_ID"].strip(), client_secret=os.environ["YT_CLIENT_SECRET"].strip())
youtube = build("youtube", "v3", credentials=creds)
req = youtube.videos().insert(part="snippet,status", body={"snippet": {"title": video_title[:95], "description": v_desc, "tags": ["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"], "categoryId": "27"}, "status": {"privacyStatus": "unlisted"}}, media_body=MediaFileUpload("output.mp4", chunksize=1024*1024*5, resumable=True))
res = None
while res is None: _, res = req.next_chunk()
print("Uploaded Video ID:", res["id"])

t_img = Image.new("RGB", (MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT), "#090d16")
td = ImageDraw.Draw(t_img)
td.rectangle((21, 21, 1899, 1059), outline="#fbbf24", width=6)
td.line((75, 135, 1102, 135), fill="#38bdf8", width=5)
td.text(
    (82, 63),
    "幣看趨勢｜每日深度財經情報",
    fill="#fbbf24",
    font=ImageFont.truetype(fp, 45),
)
raw_t = re.sub(r'^【幣看趨勢】|\([^)]+\)$', '', video_title).strip()

def split_thumb_title(text, font, draw, max_width, max_lines=3):
    text = re.sub(r'\s+', '', text).strip()
    if not text:
        return []

    preferred_breaks = ('！', '？', '：', '｜', '，', '、')
    lines = []
    current = ''
    for char in text:
        trial = current + char
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = char
        if len(current) >= 11 and char in preferred_breaks:
            lines.append(current)
            current = ''
        elif len(current) >= 12:
            lines.append(current)
            current = ''

    if current:
        lines.append(current)

    return lines

thumb_font = None
thumb_lines = []
thumb_max_width = 1050
for thumb_size in range(58, 27, -2):
    candidate_font = ImageFont.truetype(
        fp, round(thumb_size * MAIN_OUTPUT_SCALE)
    )
    candidate_lines = split_thumb_title(raw_t, candidate_font, td, thumb_max_width)
    widest = max((td.textbbox((0, 0), line, font=candidate_font)[2] for line in candidate_lines), default=0)
    if len(candidate_lines) <= 3 and widest <= thumb_max_width:
        thumb_font = candidate_font
        thumb_lines = candidate_lines
        break
if thumb_font is None:
    thumb_font = ImageFont.truetype(
        fp, round(26 * MAIN_OUTPUT_SCALE)
    )
    thumb_lines = split_thumb_title(raw_t, thumb_font, td, thumb_max_width)

line_height = thumb_font.size + 27
text_y = 174
for line_index, line in enumerate(thumb_lines):
    td.text(
        (82, text_y + line_index * line_height),
        line,
        fill="#ffffff",
        font=thumb_font,
    )

glow = Image.new(
    "RGBA", (MAIN_VIDEO_WIDTH, MAIN_VIDEO_HEIGHT), (0, 0, 0, 0)
)
glow_draw = ImageDraw.Draw(glow)
for radius, alpha in ((198, 22), (177, 34), (153, 48)):
    glow_draw.ellipse(
        (262 - radius, 855 - radius, 262 + radius, 855 + radius),
        fill=(251, 191, 36, alpha),
    )
t_img = Image.alpha_composite(t_img.convert("RGBA"), glow).convert("RGB")
with Image.open(topic_icon_path) as icon:
    icon = icon.convert("RGBA").resize((345, 345), Image.LANCZOS)
    icon_mask = Image.new("L", (345, 345), 0)
    ImageDraw.Draw(icon_mask).ellipse((0, 0, 344, 344), fill=255)
    icon.putalpha(icon_mask)
    t_img.paste(icon, (90, 682), icon)
td = ImageDraw.Draw(t_img)
td.ellipse((87, 680, 438, 1031), outline="#fbbf24", width=6)
metric_font = ImageFont.truetype(fp, round(27 * MAIN_OUTPUT_SCALE))
td.text((480, 711), f"BTC 即時價格　${cur_p}", fill="#fbbf24", font=metric_font)
td.text((480, 798), f"24H 漲跌　{'+' if btc_pct >= 0 else ''}{btc_pct:.2f}%", fill="#38bdf8", font=metric_font)
td.text(
    (480, 885),
    f"恐慌貪婪指數　{fng_num if fng_num is not None else '未取得'}"
    f"{' / 100' if fng_num is not None else ''}",
    fill="#cbd5e1",
    font=metric_font,
)
if os.path.exists("avatar.png"):
    with Image.open("avatar.png") as avatar:
        avatar = avatar.convert("RGBA").resize((645, 645), Image.LANCZOS)
        t_img.paste(avatar, (1200, 218), avatar)
    td.rounded_rectangle(
        (1194, 212, 1851, 869),
        radius=30,
        outline="#fbbf24",
        width=6,
    )

t_img.save("thumb.jpg", "JPEG", quality=92)
youtube.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload("thumb.jpg")).execute()
print("Custom thumbnail uploaded successfully!")


def build_and_upload_short():
    short_target_chars = 45 if is_test else 230
    short_duration_limit = 15.0 if is_test else 60.0
    headline_for_short = clean_slide_title(slide_news_details[0]["slide_title"]) if slide_news_details else "今日市場焦點"
    short_news = (
        f"資料抓取時間：{data_fetched_at}\n"
        f"頭條：{headline_for_short}\n"
        f"多空打折摘要：{slide_news[0]}\n"
        f"BTC：${cur_p}，24H {'+' if btc_pct >= 0 else ''}{btc_pct:.2f}%，"
        f"100日均線 ${ma100d}，200日均線 ${ma200d}，RSI {btc_rsi}\n"
        f"ETH：${spot_prices['ETH']:,.2f}，24H {'+' if eth_pct >= 0 else ''}{eth_pct:.2f}%，"
        f"RSI {eth_rsi}\n"
        + (
            f"恐慌與貪婪指數：{today_fng['value']}（{today_fng['value_classification']}）"
            if today_fng else ""
        )
    )
    short_prompt = f"""你是【幣看趨勢】短影音編劇，請使用下方當日頭條與市場數據，寫一段約{short_target_chars}字的繁體中文雙人快節奏短影音逐字稿。
【資料依循最高優先級】只能依據下列本次資料及抓取時間，不可使用預訓練記憶補入價格、事件日期或新聞；缺少的指標直接略過，不猜測或編造。
資料：
{short_news}

規格：
1. 正式模式總長約230字，適合1.2倍速配音約45至50秒；測試模式總長約45字，控制在15秒內。
2. 第一位主持人在開頭3秒內用反直覺問題或數據落差當鉤子。
3. 中間只講資料中的核心新聞、BTC/ETH數據與一個理性風險折價，不可編造資料。
4. 結尾明確引導觀眾點擊頻道觀看完整15分鐘解析。
5. 以「男：」與「女：」交替對話，至少各說一次；只輸出逐字稿，不要標題、註解或Markdown。"""
    raw_short_script = call_gemini(short_prompt)
    short_dialogues = []
    for line in raw_short_script.splitlines():
        match = re.match(r"^(男|女)[：:]\s*(.+)", line.strip())
        if match:
            short_dialogues.append(
                (match.group(1), cc.convert(match.group(2).strip()))
            )
    if not short_dialogues or {role for role, _ in short_dialogues} != {"男", "女"}:
        raise RuntimeError("Gemini did not return valid dialogue for both short-video hosts.")

    with ThreadPoolExecutor(max_workers=5) as executor:
        short_segments = list(
            executor.map(
                lambda item: synth_audio_segment(item, "short_seg"),
                enumerate(short_dialogues),
            )
        )
    short_segments.sort(key=lambda segment: segment[0])
    short_audio_duration = sum(segment[3] for segment in short_segments)
    if short_audio_duration <= 0 or short_audio_duration > short_duration_limit:
        raise RuntimeError(
            f"Short-video speech duration {short_audio_duration:.2f}s exceeds "
            f"the {short_duration_limit:g}s limit; refusing to cut off speech."
        )

    with open("short_audio_list.txt", "w", encoding="utf-8") as audio_list:
        for _, filename, _, _ in short_segments:
            audio_list.write(f"file '{filename}'\n")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "short_audio_list.txt", "-c", "copy", "short_audio.mp3",
        ],
        check=True,
    )

    short_ass_header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\n"
        f"PlayResX: {SHORT_VIDEO_WIDTH}\nPlayResY: {SHORT_VIDEO_HEIGHT}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Noto Sans CJK TC,68,&H0000D7FF,&H000000FF,&H00101010,"
        "&HAA000000,1,0,0,0,100,100,0,0,1,4,2,2,60,60,285,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    short_subtitle_events = []
    subtitle_time = 0.0
    for _, _, spoken_text, segment_duration in short_segments:
        clean_text = re.sub(r"[{}\\]", "", spoken_text).strip()
        subtitle_chunks = split_short_subtitle_text(clean_text)
        total_width = sum(
            short_subtitle_visual_width(chunk)
            for chunk, _ in subtitle_chunks
        )
        segment_time = subtitle_time
        for index, (chunk, scale_percent) in enumerate(subtitle_chunks):
            chunk_width = short_subtitle_visual_width(chunk)
            subtitle_end = (
                subtitle_time + segment_duration
                if index == len(subtitle_chunks) - 1
                else segment_time + segment_duration * chunk_width / total_width
            )
            highlighted = re.sub(
                kw_pat,
                r"{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H0000D7FF&}",
                chunk,
            )
            if scale_percent != 100:
                highlighted = (
                    f"{{\\fscx{scale_percent}\\fscy{scale_percent}}}{highlighted}"
                )
            short_subtitle_events.append(
                f"Dialogue: 0,{fmt_ass_t(segment_time)},{fmt_ass_t(subtitle_end)},"
                f"Default,,60,60,0,,{highlighted}"
            )
            segment_time = subtitle_end
        subtitle_time += segment_duration
    with open("subs_short.ass", "w", encoding="utf-8") as subtitle_file:
        subtitle_file.write(short_ass_header + "\n".join(short_subtitle_events))

    avatar_markup = (
        '<div class="avatar-placeholder">喵・汪</div>'
    )
    if os.path.exists("avatar.png"):
        with Image.open("avatar.png") as avatar:
            avatar_buffer = io.BytesIO()
            avatar.convert("RGBA").resize(
                (240, 240), Image.LANCZOS
            ).save(avatar_buffer, format="PNG")
        avatar_encoded = base64.b64encode(avatar_buffer.getvalue()).decode("ascii")
        avatar_markup = (
            '<img class="avatar" alt="以太喵與比特汪" '
            f'src="data:image/png;base64,{avatar_encoded}">'
        )

    short_highlight_state = {"remaining": 3}
    short_highlights = [
        highlight_key_numbers(headline_for_short[:34], short_highlight_state),
        highlight_key_numbers(clean_slide_text(slide_news[0])[:42], short_highlight_state),
        "完整15分鐘市場解析，點擊頻道觀看",
    ]
    highlights_markup = "".join(
        f'<li style="--delay:{index * 0.6:.1f}s">{highlight}</li>'
        for index, highlight in enumerate(short_highlights)
    )
    fear_value = html.escape(str(today_fng["value"])) if today_fng else "未取得"
    fear_classification = (
        html.escape(str(today_fng["value_classification"]))
        if today_fng else "資料未通過時效驗證"
    )
    short_html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width={SHORT_VIDEO_WIDTH}, initial-scale=1">
<style>
* {{ box-sizing: border-box; }}
html, body {{ width:{SHORT_VIDEO_WIDTH}px; height:{SHORT_VIDEO_HEIGHT}px; margin:0; overflow:hidden; }}
body {{
  position:relative; width:{SHORT_VIDEO_WIDTH}px; height:{SHORT_VIDEO_HEIGHT}px;
  color:#f8fafc; background:#090d16;
  font-family:'Noto Sans CJK TC',sans-serif;
}}
.short-stage {{
  position:relative; width:{SHORT_DESIGN_WIDTH}px; height:{SHORT_DESIGN_HEIGHT}px;
  overflow:hidden; background:#090d16;
  transform:scale({SHORT_OUTPUT_SCALE}); transform-origin:top left;
}}
.short-stage::before {{
  position:absolute; inset:-8%; content:""; opacity:.5;
  background:
    radial-gradient(ellipse at 50% 38%,rgba(56,189,248,.2),transparent 40%),
    radial-gradient(ellipse at 20% 75%,rgba(167,139,250,.16),transparent 35%),
    linear-gradient(rgba(56,189,248,.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(56,189,248,.05) 1px,transparent 1px);
  background-size:auto,auto,42px 42px,42px 42px;
  animation:gridFlow 12s linear infinite;
}}
@keyframes gridFlow {{ to {{ transform:translate3d(42px,21px,0); }} }}
.brand {{
  position:absolute; top:24px; width:100%; text-align:center;
  color:#fbbf24; font-size:29px; font-weight:800; letter-spacing:2px;
}}
.brand::after {{
  display:block; width:210px; height:3px; margin:10px auto 0;
  background:linear-gradient(90deg,transparent,#38bdf8,transparent); content:"";
}}
.avatar {{
  position:absolute; top:78px; left:240px; width:240px; height:240px;
  object-fit:contain; border:3px solid #fbbf24; border-radius:50%;
  box-shadow:0 0 34px rgba(251,191,36,.38); animation:avatarIn .8s ease-out both;
}}
.avatar-placeholder {{
  position:absolute; top:78px; left:240px; width:240px; height:240px;
  display:grid; place-items:center; border:3px solid #fbbf24; border-radius:50%;
  color:#fbbf24; background:#111827; font-size:52px;
}}
@keyframes avatarIn {{
  from {{ opacity:0; transform:scale(.75); }}
  to {{ opacity:1; transform:scale(1); }}
}}
.panel {{
  position:absolute; top:348px; left:34px; right:34px; bottom:310px;
  padding:28px 26px; border:2px solid rgba(251,191,36,.78); border-radius:24px;
  background:rgba(15,23,42,.9); box-shadow:0 0 35px rgba(56,189,248,.14);
  overflow:hidden; text-align:center;
}}
.eyebrow {{ color:#94a3b8; font-size:22px; letter-spacing:4px; }}
.symbol {{ margin-top:6px; color:#38bdf8; font-size:32px; font-weight:800; }}
.price {{
  margin:2px 0 12px; color:#fbbf24; font-size:68px; font-weight:900;
  text-shadow:0 0 26px rgba(251,191,36,.38);
  animation:numberIn .8s cubic-bezier(.2,.8,.2,1) both,numberPulse 3.5s ease-in-out 1s infinite;
}}
@keyframes numberIn {{ from {{ opacity:0; transform:translateY(25px) scale(.92); }} to {{ opacity:1; transform:translateY(0) scale(1); }} }}
@keyframes numberPulse {{ 0%,100% {{ text-shadow:0 0 18px rgba(251,191,36,.25); }} 50% {{ text-shadow:0 0 34px rgba(251,191,36,.62); }} }}
.stats {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:12px 0 20px; }}
.stat {{
  padding:12px 6px; border:1px solid rgba(56,189,248,.35); border-radius:14px;
  background:rgba(8,15,28,.8); animation:fadeInUp .6s ease-out .55s both;
}}
.stat strong {{ display:block; color:#fbbf24; font-size:29px; }}
.stat small {{ color:#94a3b8; font-size:17px; }}
.points {{ list-style:none; margin:0; padding:0; text-align:left; }}
.points li {{
  position:relative; margin:13px 0; padding:0 4px 0 28px;
  color:#e2e8f0; font-size:22px; line-height:1.35; overflow-wrap:anywhere;
  animation:fadeInUp .6s ease-out var(--delay) both;
}}
.points li::before {{
  position:absolute; top:.48em; left:2px; width:9px; height:9px;
  border-radius:50%; background:#38bdf8; box-shadow:0 0 12px #38bdf8; content:"";
}}
.hl {{ position:relative; z-index:0; color:#fff2b3; }}
.hl::after {{
  position:absolute; z-index:-1; left:0; bottom:-.06em; width:100%; height:.32em;
  background:linear-gradient(90deg,rgba(251,191,36,.8),rgba(251,191,36,.14));
  content:""; transform:scaleX(0); transform-origin:left;
  animation:highlight .65s ease-out .6s forwards;
}}
@keyframes highlight {{ to {{ transform:scaleX(1); }} }}
@keyframes fadeInUp {{ from {{ opacity:0; transform:translateY(20px); }} to {{ opacity:1; transform:translateY(0); }} }}
</style></head>
<body>
<div class="short-stage">
<header class="brand">【幣看趨勢】｜每日市場快訊</header>
{avatar_markup}
<main class="panel">
  <div class="eyebrow">BTC 即時價格</div>
  <div class="symbol">BITCOIN　BTC</div>
  <div class="price">${cur_p}</div>
  <section class="stats">
    <div class="stat"><strong>{btc_pct:+.2f}%</strong><small>BTC 24H</small></div>
    <div class="stat"><strong>${spot_prices['ETH']:,.2f}</strong><small>ETH 現價</small></div>
    <div class="stat"><strong>{eth_pct:+.2f}%</strong><small>ETH 24H</small></div>
    <div class="stat"><strong>{fear_value}</strong><small>恐懼貪婪・{fear_classification}</small></div>
  </section>
  <ul class="points">{highlights_markup}</ul>
</main>
</div>
<script>
window.fitShortLayout = () => {{
  const panel = document.querySelector(".panel");
  const points = [...document.querySelectorAll(".points li")];
  for (let attempt = 0; attempt < 12 && panel.scrollHeight > panel.clientHeight + 1; attempt++) {{
    for (const point of points) {{
      const size = parseFloat(getComputedStyle(point).fontSize);
      if (size > 17) point.style.fontSize = `${{size - 1}}px`;
    }}
  }}
  return document.documentElement.scrollWidth <= {SHORT_VIDEO_WIDTH} &&
    document.documentElement.scrollHeight <= {SHORT_VIDEO_HEIGHT} &&
    panel.scrollHeight <= panel.clientHeight + 1;
}};
</script>
</body></html>"""

    render_duration = 15.0 if is_test else short_audio_duration
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with tempfile.TemporaryDirectory(prefix="short_render_") as video_dir:
                context = browser.new_context(
                    viewport={"width": SHORT_VIDEO_WIDTH, "height": SHORT_VIDEO_HEIGHT},
                    device_scale_factor=1,
                    record_video_dir=video_dir,
                    record_video_size={
                        "width": SHORT_VIDEO_WIDTH,
                        "height": SHORT_VIDEO_HEIGHT,
                    },
                )
                page = context.new_page()
                video = page.video
                try:
                    page.set_content(short_html, wait_until="load")
                    page.evaluate("() => document.fonts.ready.then(() => true)")
                    if not page.evaluate("() => window.fitShortLayout()"):
                        raise ValueError("Short-video HTML exceeds its safe area.")
                    page.wait_for_timeout(round(render_duration * 1000))
                finally:
                    context.close()
                recording_path = video.path()
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", recording_path,
                        "-vf", (
                            f"fps=24,scale={SHORT_VIDEO_WIDTH}:{SHORT_VIDEO_HEIGHT}:"
                            "flags=lanczos,format=yuv420p"
                        ),
                        "-t", f"{render_duration:.6f}", "-an", "-r", "24",
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "short_visual.mp4",
                    ],
                    check=True,
                )
        finally:
            browser.close()

    mux_command = [
        "ffmpeg", "-y", "-i", "short_visual.mp4", "-i", "short_audio.mp3",
        "-vf", "ass=subs_short.ass",
    ]
    if is_test:
        mux_command.extend(["-af", "apad=whole_dur=15", "-t", "15"])
    else:
        mux_command.extend(["-t", f"{short_audio_duration:.6f}"])
    mux_command.extend(
        [
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
            "-r", "24", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "short.mp4",
        ]
    )
    subprocess.run(mux_command, check=True)

    main_video_url = f"https://www.youtube.com/watch?v={res['id']}"
    short_title = (
        f"BTC ${cur_p}｜恐慌指數 {fng_num} 點，這個訊號別忽略！ #Shorts"
        if fng_num is not None else
        f"BTC ${cur_p}｜幣圈最新市場焦點與風險解析 #Shorts"
    )
    short_description = (
        f"{today_str} 幣圈市場 50 秒快訊。\n\n"
        f"▶️ 完整 15 分鐘市場解析：{main_video_url}\n\n"
        "本內容為公開資訊整理，不構成投資建議。加密貨幣波動劇烈，請審慎評估風險。\n"
        "#Shorts #比特幣 #BTC #以太幣 #ETH #幣看趨勢"
    )
    short_request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": short_title[:95],
                "description": short_description,
                "tags": ["Shorts", "比特幣", "BTC", "以太幣", "ETH", "幣看趨勢"],
                "categoryId": "27",
            },
            "status": {"privacyStatus": "unlisted"},
        },
        media_body=MediaFileUpload(
            "short.mp4", chunksize=1024 * 1024 * 5, resumable=True
        ),
    )
    short_response = None
    while short_response is None:
        _, short_response = short_request.next_chunk()
    print("Uploaded YouTube Short:", short_response["id"])


build_and_upload_short()
