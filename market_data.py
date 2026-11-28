import html
import os
import re
import json
import time
import calendar
import math
import statistics
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote
import feedparser

import presentation

# ── 正則關鍵字與常數 ──────────────────────────────────────────

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

binance_api = "".join(["https", "://", "data-api.binance.", "vision", "/api/v3"])
vb = f"{binance_api}/klines"


# ── 網路請求與快取工具 ──────────────────────────────────────────

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


# ── 新聞過濾邏輯 ──────────────────────────────────────────

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


def is_core_crypto_news(entry):
    if is_pure_ai_news(entry):
        return False
    content = " ".join(
        str(entry.get(field, ""))
        for field in ("title", "summary", "description")
    )
    return bool(CORE_NEWS_KEYWORDS.search(content))


def published_datetime(entry, tw_tz):
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


def filter_recent_entries(source_entries, now_utc, tw_tz):
    cutoff = now_utc - timedelta(hours=36)
    valid_entries = []
    discarded = {"invalid": 0, "future": 0, "stale": 0, "pure_ai": 0, "off_topic": 0}
    for source in source_entries:
        for entry in source:
            published_at = published_datetime(entry, tw_tz)
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


def fetch_rss_news(tw_tz):
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

    valid_entries, discarded_news = filter_recent_entries(
        source_entries, datetime.now(timezone.utc), tw_tz
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
        print(
            f"新聞來源軟性降級：本次只有 {len(entries)} 則通過時效與主題驗證的新聞；"
            "不以舊聞或未驗證內容補足，改由有效市場數據支撐本集。"
        )
    return entries


_NEWS_BODY_PATTERNS = (
    r"(?is)<article\b[^>]*>(.*?)</article>",
    r'(?is)<(?:div|section|article|main)\b[^>]*class=["\'][^"\']*article[^"\']*["\'][^>]*>(.*?)</(?:div|section|article|main)>',
    r'(?is)<(?:div|section|article|main)\b[^>]*class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</(?:div|section|article|main)>',
    r'(?is)<(?:div|section|article|main)\b[^>]*class=["\'][^"\']*post[^"\']*["\'][^>]*>(.*?)</(?:div|section|article|main)>',
    r"(?is)<main\b[^>]*>(.*?)</main>",
)

_NEWS_PARAGRAPH_BANS = (
    "延伸閱讀",
    "這篇文章最早發佈於",
    "版權所有",
    "All rights reserved",
    "追蹤動區",
    "加入動區",
    "TG 頻道",
    "Line 社群",
)


def _news_body_candidates(html_cleaned):
    candidates = []
    for pattern in _NEWS_BODY_PATTERNS:
        match = re.search(pattern, html_cleaned)
        if match:
            candidates.append(match.group(1))
    candidates.append(html_cleaned)
    return candidates


def _paragraphs_from_html(container):
    paragraphs = []
    for p_html in re.findall(r"(?is)<p[^>]*>(.*?)</p>", container):
        clean_p = re.sub(r"(?is)<[^>]+>", "", p_html)
        clean_p = html.unescape(clean_p).strip()
        if len(clean_p) < 15:
            continue
        if any(banned in clean_p for banned in _NEWS_PARAGRAPH_BANS):
            continue
        paragraphs.append(clean_p)
    return paragraphs


def enrich_top_news_with_full_text(news_list, max_articles=4):
    """
    對篩選出的前 max_articles 則核心新聞發送 HTTP 請求（含完整 User-Agent 與 10 秒逾時防呆），
    依序從 <article>、class 含 article/content/post 的容器，以及 <main> 內的 <p> 提取正文
    （每篇 1,200～2,500 字真實細節、人物引述、機構數據與事件脈絡）。
    若某網頁無法抓取則靜默保留原摘要，絕不報錯中斷。
    """
    if not news_list:
        return news_list

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    count = min(len(news_list), max_articles)
    for i in range(count):
        item = news_list[i]
        entry = item[1] if isinstance(item, tuple) and len(item) == 2 else item
        if not isinstance(entry, dict):
            continue

        url = entry.get("link") or entry.get("url") or ""
        if not url:
            continue

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_bytes = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                html_text = raw_bytes.decode(charset, errors="replace")

            # 清理干擾標籤
            html_cleaned = re.sub(
                r"(?is)<(script|style|noscript|header|footer|nav|aside|form)[^>]*>.*?</\1>",
                " ",
                html_text,
            )

            full_text = ""
            for container in _news_body_candidates(html_cleaned):
                paragraphs = _paragraphs_from_html(container)
                candidate = "\n".join(paragraphs).strip()
                if len(candidate) < 150:
                    candidate = _html_to_text(container)
                candidate = re.sub(r"\s+", " ", candidate).strip()
                if len(candidate) > len(full_text):
                    full_text = candidate
                if len(full_text) >= 150:
                    break
            # 限制長度在 1,200～2,500 字以內，保留核心論據
            if len(full_text) > 2500:
                full_text = full_text[:2500]

            if len(full_text) >= 150:
                entry["full_text"] = full_text
                print(f"[News FullText] 成功抓取第 {i+1} 則全文 ({len(full_text)} 字)：{entry.get('title', '')[:30]}")
            else:
                print(f"[News FullText] 提取文字過短，保留原摘要：{entry.get('title', '')[:30]}")
        except Exception as err:
            # 靜默保留原摘要，絕不報錯中斷
            print(f"[News FullText] 抓取略過 ({url[:45]}...): {err}")

    return news_list


# ── 恐慌與貪婪指數 ──────────────────────────────────────────

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


# ── 行情報價與 K 線歷史 ──────────────────────────────────────────

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
    return str(presentation.round_usd(sum(sl) / len(sl))) if sl else "0"


def calc_rsi(arr, p=14):
    sl = arr[-(p+1):]
    if len(sl) < 2:
        return "50.00"
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


# ── 大戶散戶籌碼動向與鏈上數據 ──────────────────────────────────────────

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
        return ""
    ratio = metric["ratio"]
    direction = "偏多" if ratio > 1 else "偏空" if ratio < 1 else "中性"
    return (
        f"{metric['source']} {metric['label']} {ratio:.3f}:1（{direction}，"
        f"截至 {metric['timestamp'].strftime('%Y-%m-%d %H:%M UTC')}）"
    )


def format_trader_position_report(metrics):
    if not metrics:
        return ""
    reports = [
        text for text in (
            describe_long_short_ratio(metrics.get("all_accounts")),
            describe_long_short_ratio(metrics.get("top_accounts")),
            describe_long_short_ratio(metrics.get("top_positions")),
        )
        if text
    ]
    if not reports:
        return ""
    return (
        "；".join(reports)
        + "。前列交易者是交易所排名代理，不等同所有鏈上巨鯨；"
        "全體帳戶也不是散戶專屬樣本，多空比不代表現貨買入或吸籌。"
    )


def format_spot_volume_report(metrics):
    if metrics is None:
        return ""
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
            continue
        if key == "estimated_transfer_volume":
            value_text = f"估算鏈上交易量日均 US${metric['recent_average']:,.0f}"
        else:
            value_text = f"鏈上交易筆數日均 {metric['recent_average']:,.0f} 筆"
        reports.append(
            f"{value_text}，較前30日 {metric['change_pct']:+.2f}%"
            f"（截至 {metric['end_date']}）"
        )
    if not reports:
        return ""
    return (
        "；".join(reports)
        + "。這些是全網活動代理，無法識別地址身分或判定交易所淨流入／流出。"
    )


def format_chip_sentiment_report(fng_value, market_structure, spot_volume):
    if fng_value is None or market_structure is None:
        return ""
    drawdown_pct = market_structure["current_drawdown_pct"]
    if spot_volume is None:
        return (
            f"恐慌貪婪指數 {fng_value}；目前相對90日高點回撤 {drawdown_pct:.2f}%。"
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


# ── 穩定幣總市值 ──────────────────────────────────────────

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
        f"{name}：現價 {presentation.format_usd(spot_price)}；90日高點 {presentation.format_usd(metrics['swing_high'])}"
        f"（{metrics['swing_high_date']}），距該高點 {metrics['days_since_high']} 天，"
        f"目前回撤 {metrics['current_drawdown_pct']:.2f}%；"
        f"90日最大回撤 {metrics['maximum_drawdown_pct']:.2f}%"
        f"{drawdown_dates}；"
        f"30日支撐 {presentation.format_usd(metrics['support'])}、壓力 {presentation.format_usd(metrics['resistance'])}；"
        f"現價相對100日線 {metrics['sma_100_deviation_pct']:+.2f}%、"
        f"相對200日線 {metrics['sma_200_deviation_pct']:+.2f}%"
    )


def format_stablecoin_market_cap_report(changes):
    if not changes:
        return ""
    reports = []
    for days in (7, 30):
        change = changes.get(days)
        if change is None:
            continue
        direction = "增加" if change["change_amount"] >= 0 else "減少"
        reports.append(
            f"近{days}日{direction} US${abs(change['change_amount']):,.0f}"
            f"（{change['change_pct']:+.2f}%）"
        )
    if not reports:
        return ""
    return (
        f"截至 {max(item['end_date'] for item in changes.values() if item)}："
        + "；".join(reports)
        + "。市值變化不等同於實際流入加密資產。"
    )


def format_oversold_report(events, rsi_series):
    latest_rsi = next(
        (value for value in reversed(rsi_series) if value is not None), None
    )
    if latest_rsi is None:
        return ""
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


# ── DCA 歷史資料與帳本 ──────────────────────────────────────────

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


def _positive_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _yahoo_nearest_btc_close(target_date):
    closes = fetch_yahoo_daily_closes(
        "BTC-USD",
        target_date - timedelta(days=3),
        target_date + timedelta(days=3),
    )
    exact = _positive_price(closes.get(target_date))
    if exact is not None:
        return exact
    ranked = []
    for day, close in closes.items():
        price = _positive_price(close)
        if price is None:
            continue
        distance = abs((day - target_date).days)
        if distance <= 3:
            ranked.append((distance, day, price))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def _binance_btc_daily_close(target_date):
    start = datetime(
        target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc
    )
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000) - 1
    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol=BTCUSDT&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=5"
    )
    candles = fetch_json(url, timeout=20, cache_bust_param=None)
    if not isinstance(candles, list):
        return None
    best_price = None
    best_distance = None
    for candle in candles:
        try:
            open_day = datetime.fromtimestamp(int(candle[0]) / 1000, timezone.utc).date()
        except (IndexError, TypeError, ValueError, OverflowError, OSError):
            continue
        price = _positive_price(candle[4] if len(candle) > 4 else None)
        distance = abs((open_day - target_date).days)
        if price is None or distance > 1:
            continue
        if best_price is None or distance < best_distance:
            best_price = price
            best_distance = distance
    return best_price


def _coingecko_btc_history_close(target_date):
    date_param = target_date.strftime("%d-%m-%Y")
    url = (
        "https://api.coingecko.com/api/v3/coins/bitcoin/history"
        f"?date={quote(date_param)}&localization=false"
    )
    payload = fetch_json(url, timeout=20, cache_bust_param=None)
    if not isinstance(payload, dict):
        return None
    market = payload.get("market_data")
    if not isinstance(market, dict):
        return None
    current_price = market.get("current_price")
    if not isinstance(current_price, dict):
        return None
    return _positive_price(current_price.get("usd"))


def fetch_historical_btc_close(date_str, fallback_current_price):
    """依 Yahoo、Binance、CoinGecko、近 3 日即時價取得 BTC 收盤價，不拋 RuntimeError。"""
    target_date = datetime.fromisoformat(str(date_str)).date()
    sources = (
        ("Yahoo Finance", lambda: _yahoo_nearest_btc_close(target_date)),
        ("Binance", lambda: _binance_btc_daily_close(target_date)),
        ("CoinGecko", lambda: _coingecko_btc_history_close(target_date)),
    )
    for name, getter in sources:
        try:
            price = getter()
        except Exception as error:
            print(f"[DCA] {name} 歷史收盤價失敗 ({date_str}): {error}")
            continue
        if price is not None:
            print(f"[DCA] {name} 取得 {date_str} BTC 收盤價 {price}")
            return price

    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    age_days = abs((today - target_date).days)
    fallback = _positive_price(fallback_current_price)
    if age_days <= 3 and fallback is not None:
        print(
            f"[DCA] 歷史收盤價來源暫時無回應，{date_str} 距離現在 {age_days} 天，"
            f"改用即時 BTC 價格 {fallback} 作為該筆每月 25 日定投成交價。"
        )
        return fallback
    print(
        f"[DCA] 無法取得 {date_str} 的 BTC 歷史收盤價（距離 {age_days} 天），"
        "略過該筆以免中斷製片。"
    )
    return None


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


def update_dca_portfolio(current_btc_usd, now_tw):
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

        historical_btc = {}
        try:
            historical_btc = fetch_yahoo_daily_closes("BTC-USD", min(missing_dates), today)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
            print(f"[DCA] Yahoo 批次 BTC 歷史價失敗，改走逐日備援：{error}")
        for deposit_date in missing_dates:
            btc_price = historical_btc.get(deposit_date)
            if btc_price is None or btc_price <= 0:
                btc_price = fetch_historical_btc_close(
                    deposit_date.isoformat(), current_btc_usd
                )
            if btc_price is None or btc_price <= 0:
                print(f"[DCA] 略過 {deposit_date.isoformat()}，沒有可用的 BTC 成交價。")
                continue
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
                "usd_invested": round(usd_invested, 2),
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
        usd_invested = round(twd_invested / twd_rate, 2)
        record["usd_invested"] = usd_invested
        total_twd += twd_invested
        total_usd_invested += usd_invested
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
        "contribution_note": (
            f"自 {start_date.isoformat()} 起，每月 25 日固定投入 NT$10,000"
            "（依當日 USD/TWD 匯率換算為美元買進 BTC）。"
        ),
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
        "total_invested_usd": round(total_usd_invested, 2),
        "current_value_usd": round(current_market_value_usd, 2),
        "roi_pct": round((current_market_value_usd / total_usd_invested - 1) * 100, 2),
        "total_return_pct": round(
            (current_market_value_twd / total_twd - 1) * 100, 2
        ),
    })
    with open(ledger_path, "w", encoding="utf-8") as ledger_file:
        json.dump(portfolio, ledger_file, ensure_ascii=False, indent=2)
        ledger_file.write("\n")
    return portfolio


def dca_usd_view(portfolio, current_btc_usd):
    """歷史 JSON 仍保留台幣入帳欄位；播報只使用這裡換算後的美元視圖。

    週末或非 25 日沒有新成交時，沿用 records 裡最近有效部位，
    再用當下 BTC 美元現價重算市值與報酬，不因平假日把報酬打成 0。
    """
    records = [row for row in portfolio.get("records", []) if isinstance(row, dict)]
    if not records:
        return None
    total_usd = 0.0
    total_btc = 0.0
    for record in records:
        stored_usd = record.get("usd_invested")
        if stored_usd not in (None, ""):
            total_usd += float(stored_usd)
        else:
            rate = float(record.get("twd_rate") or 0)
            invested_twd = float(record.get("twd_invested") or 0)
            if rate <= 0:
                continue
            total_usd += invested_twd / rate
        total_btc += float(record.get("btc_bought") or 0)
    price = float(current_btc_usd)
    if total_btc <= 0 or total_usd <= 0 or price <= 0:
        return None
    current_value = total_btc * price
    roi_pct = (current_value / total_usd - 1) * 100
    latest = max(records, key=lambda row: str(row.get("date") or ""))
    return {
        "total_invested": round(total_usd, 2),
        "current_value": round(current_value, 2),
        "total_btc": f"{total_btc:.8f}",
        "roi_pct": round(roi_pct, 2),
        "avg_buy_price_usd": round(total_usd / total_btc, 2),
        "latest_date": latest.get("date"),
        "latest_price_usd": float(latest.get("btc_price_usd") or 0),
        "monthly_usd": round(float(latest.get("twd_invested") or 0) / float(latest.get("twd_rate") or 1), 2),
    }


def load_dca_usd_snapshot(current_btc_usd):
    ledger_path = "dca_portfolio.json"
    if not os.path.exists(ledger_path):
        return None
    with open(ledger_path, encoding="utf-8") as ledger_file:
        portfolio = json.load(ledger_file)
    if not isinstance(portfolio, dict):
        return None
    return dca_usd_view(portfolio, current_btc_usd)


def fetch_btc_mvrv(drawdown_pct=None):
    """以 CoinMetrics 歷史市值與實現市值計算真實 MVRV Z-Score，不以回撤代理值冒充。"""
    cache_path = os.path.join("cache", "mvrv_zscore_cache.json")
    coinmetrics_url = (
        "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        "?assets=btc&metrics=CapMrktCurUSD,CapRealUSD&frequency=1d"
        "&start_time=2010-01-01&page_size=10000"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 DailyCryptoPodcast/1.0",
        "Accept": "application/json",
    }

    def as_utc(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def pack(value, source, market_cap, realized_cap, stddev, as_of):
        number = float(value)
        if not math.isfinite(number) or number < -10 or number > 30:
            raise ValueError(f"CoinMetrics produced implausible MVRV Z-Score: {number}")
        zone = presentation.classify_mvrv_zscore(number)
        return {
            "value": round(number, 2),
            "mvrv_type": "z_score",
            "display_name": "MVRV Z-Score",
            "zone": zone,
            "source": source,
            "is_estimate": False,
            "market_cap": market_cap,
            "realized_cap": realized_cap,
            "market_cap_stddev": stddev,
            "as_of": as_of,
            "bar_pct": max(0, min(100, int((number + 1) / 8.0 * 100))),
            "report": (
                f"MVRV Z-Score {presentation.format_mvrv(number, 'z_score')}，"
                f"判讀為{zone}（{source}，截至 {as_of[:10]}；"
                "以歷史市值標準差計算）"
            ),
        }

    try:
        payload = fetch_json(
            coinmetrics_url, timeout=30, headers=headers, cache_bust_param=None
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("CoinMetrics returned no historical BTC asset-metric rows.")

        valid_rows = []
        market_cap_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                market_cap = float(row.get("CapMrktCurUSD"))
                observed = as_utc(row.get("time"))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(market_cap) or market_cap <= 0:
                continue
            # 先保留完整市值序列，稍後截到最新有效 realized-cap 日期，避免分母使用未來資料。
            market_cap_rows.append((observed, market_cap))
            try:
                realized_cap = float(row.get("CapRealUSD"))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(realized_cap) or realized_cap <= 0:
                continue
            valid_rows.append((observed, market_cap, realized_cap))

        if len(valid_rows) < 365:
            raise ValueError(
                f"CoinMetrics history too short for a defensible Z-Score: {len(valid_rows)} days."
            )
        valid_rows.sort(key=lambda item: item[0])
        observed, current_market_cap, current_realized_cap = valid_rows[-1]
        market_caps = [
            market_cap for market_date, market_cap in market_cap_rows
            if market_date <= observed
        ]
        age = datetime.now(timezone.utc) - observed
        if age < timedelta(minutes=-5) or age > timedelta(days=3):
            raise ValueError(f"CoinMetrics MVRV source is stale: {observed.isoformat()}")
        market_cap_stddev = statistics.pstdev(market_caps)
        if not math.isfinite(market_cap_stddev) or market_cap_stddev <= 0:
            raise ValueError("CoinMetrics market-cap standard deviation is invalid.")

        # 標準 MVRV Z-Score = (Market Cap - Realized Cap) / 歷史 Market Cap 標準差。
        z_score = (current_market_cap - current_realized_cap) / market_cap_stddev
        result = pack(
            z_score,
            "CoinMetrics historical CapMrktCurUSD / CapRealUSD",
            current_market_cap,
            current_realized_cap,
            market_cap_stddev,
            observed.isoformat(),
        )
        os.makedirs("cache", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as cache_file:
            json.dump(result, cache_file, ensure_ascii=False, indent=2)
        print(
            f"[MVRV Z-Score] 即時計算完成：{result['value']:.2f}｜"
            f"有效歷史樣本 {len(valid_rows)} 天｜來源 CoinMetrics｜{observed.date()}"
        )
        return result
    except Exception as error:
        print(f"[MVRV Z-Score] CoinMetrics 即時計算失敗：{error}")

    # 備援來源：MacroMicro 公開的 Bitcoin MVRV Z-Score 序列頁（不需 API Key）。
    # 只讀取「Latest Stats」區塊中明確標示的最新日期與數值，不使用圖表刻度或其他指標。
    macromicro_url = "https://en.macromicro.me/series/8365/bitcoin-mvrv-zscore"
    try:
        raw = fetch_bytes(
            macromicro_url,
            timeout=25,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            cache_bust_param=None,
        )
        page_html = raw.decode("utf-8", errors="replace")
        # 優先定位 Latest Stats，避免把圖表上的歷史極值或刻度誤認為最新值。
        latest_match = re.search(
            r"(?is)Latest\s+Stats(.*?)(?:Specs|Related\s+Charts|<footer|$)",
            page_html,
        )
        if not latest_match:
            raise ValueError("MacroMicro page has no readable Latest Stats section.")
        latest_text = html.unescape(
            re.sub(r"(?is)<[^>]+>", " ", latest_match.group(1))
        )
        latest_text = re.sub(r"\s+", " ", latest_text).strip()
        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", latest_text)
        if not date_match:
            raise ValueError("MacroMicro Latest Stats has no observation date.")
        date_text = date_match.group(1)
        date_value = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        after_date = latest_text[date_match.end():]
        number_match = re.search(
            r"(?<![A-Za-z0-9])(-?\d+(?:,\d{3})*(?:\.\d+)?)(?![A-Za-z0-9])",
            after_date,
        )
        if not number_match:
            raise ValueError("MacroMicro Latest Stats has no numeric Z-Score.")
        macro_value = float(number_match.group(1).replace(",", ""))
        age = datetime.now(timezone.utc) - date_value
        if age < timedelta(minutes=-5) or age > timedelta(days=3):
            raise ValueError(f"MacroMicro MVRV Z-Score is stale: {date_text}")
        if not math.isfinite(macro_value) or macro_value < -10 or macro_value > 30:
            raise ValueError(f"MacroMicro returned an implausible Z-Score: {macro_value}")
        macro_zone = presentation.classify_mvrv_zscore(macro_value)
        macro_result = {
            "value": round(macro_value, 2),
            "mvrv_type": "z_score",
            "display_name": "MVRV Z-Score",
            "zone": macro_zone,
            "source": "MacroMicro public series (8365)",
            "is_estimate": False,
            "as_of": date_value.isoformat(),
            "bar_pct": max(0, min(100, int((macro_value + 1) / 8.0 * 100))),
            "report": (
                f"MVRV Z-Score {presentation.format_mvrv(macro_value, 'z_score')}，"
                f"判讀為{macro_zone}（MacroMicro，截至 {date_text}）"
            ),
        }
        os.makedirs("cache", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as cache_file:
            json.dump(macro_result, cache_file, ensure_ascii=False, indent=2)
        print(
            f"[MVRV Z-Score] MacroMicro 公開序列備援成功："
            f"{macro_result['value']:.2f}｜{date_text}"
        )
        return macro_result
    except Exception as error:
        print(f"[MVRV Z-Score] MacroMicro 公開序列備援不可用：{error}")

    # 僅允許沿用 48 小時內、且明確標記為 Z-Score 的快取；舊 MVRV Ratio 不可冒充。
    try:
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as cache_file:
                cached = json.load(cache_file)
            cached_time = as_utc(cached.get("as_of"))
            cache_age = datetime.now(timezone.utc) - cached_time
            if (
                cached.get("mvrv_type") == "z_score"
                and cached.get("value") is not None
                and timedelta(0) <= cache_age <= timedelta(hours=48)
            ):
                cached_source = str(cached.get("source") or "MVRV Z-Score provider")
                cached["source"] = f"{cached_source} (48h cache)"
                cached["display_name"] = "MVRV Z-Score"
                cached["is_estimate"] = False
                cached["zone"] = presentation.classify_mvrv_zscore(cached["value"])
                cached["report"] = (
                    f"MVRV Z-Score {presentation.format_mvrv(cached['value'], 'z_score')}，"
                    f"判讀為{cached['zone']}（沿用 48 小時內 {cached_source} 快取）"
                )
                print(f"[MVRV Z-Score] 即時來源暫不可用，沿用有效快取 {cached['as_of']}")
                return cached
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"[MVRV Z-Score] 快取不可用：{error}")

    print("[MVRV Z-Score] 即時來源與有效快取皆不可用；本集略過此指標，繼續製片。")
    return None


def fetch_mvrv(drawdown_pct=None):
    return fetch_btc_mvrv(drawdown_pct)


def fetch_dca_data(current_btc_usd, now_tw):
    portfolio = update_dca_portfolio(current_btc_usd, now_tw)
    latest_record = portfolio["records"][-1]
    latest_month = latest_record["date"][:7]
    latest_month_data = portfolio["monthly_summary"].get(
        latest_month, {"twd_invested": 0, "btc_bought": "0.00000000"}
    )
    usd_view = dca_usd_view(portfolio, current_btc_usd)
    if not usd_view:
        raise RuntimeError("Unable to build a USD view from the latest DCA records.")
    dca_report = (
        "【BTC 長期定投（DCA）節目模擬帳本，金額一律為美元】\n"
        "比特汪在節目設定中自2025年10月起，每月25日模擬投入一筆固定部位；"
        "歷史檔以當期匯率換算成美元後播報，此帳本不是實際成交紀錄。\n"
        f"最近一次買進（{latest_record['date']}）："
        f"BTC {presentation.format_metric(latest_record['btc_price_usd'], 'price')}；"
        f"該期美元投入 {presentation.format_metric(usd_view['monthly_usd'], 'price')}，"
        f"買入 {latest_month_data['btc_bought']} BTC。\n"
        f"累計投入：{presentation.format_metric(usd_view['total_invested'], 'price')}；"
        f"累計持有：{usd_view['total_btc']} BTC。\n"
        f"平均成本：{presentation.format_metric(usd_view['avg_buy_price_usd'], 'price')} 每 BTC。\n"
        f"目前總價值：{presentation.format_metric(usd_view['current_value'], 'price')}；"
        f"帳面賺賠：{presentation.format_metric(usd_view['current_value'] - usd_view['total_invested'], 'price')} "
        f"（{presentation.format_metric(usd_view['roi_pct'], 'percent')}）。"
    )
    recent_records = portfolio["records"][-3:]
    lowest_record = min(
        portfolio["records"], key=lambda record: float(record["btc_price_usd"])
    )
    highest_record = max(
        portfolio["records"], key=lambda record: float(record["btc_price_usd"])
    )
    dca_slide_body = "\n".join(
        [
            f"累計投入 {presentation.format_metric(usd_view['total_invested'], 'price')}｜累計 {usd_view['total_btc']} BTC",
            f"平均成本 {presentation.format_metric(usd_view['avg_buy_price_usd'], 'price')}",
            (
                f"總市值 {presentation.format_metric(usd_view['current_value'], 'price')}｜"
                f"損益 {presentation.format_metric(usd_view['roi_pct'], 'percent')}"
            ),
            "近三次買進：" + "；".join(
                f"{record['date']} {presentation.format_metric(record['btc_price_usd'], 'price')}"
                for record in recent_records
            ),
            (
                f"歷史低／高：{presentation.format_metric(lowest_record['btc_price_usd'], 'price')}"
                f"（{lowest_record['date']}）／"
                f"{presentation.format_metric(highest_record['btc_price_usd'], 'price')}"
                f"（{highest_record['date']}）"
            ),
        ]
    )
    return {
        "portfolio": portfolio,
        "dca_report": dca_report,
        "dca_slide_body": dca_slide_body,
        "recent_records": recent_records,
        "lowest_record": lowest_record,
        "highest_record": highest_record,
        "usd_view": usd_view,
    }


CUSTOM_SOURCES_PATH = os.path.join("knowledge_base", "custom_sources.json")


def _html_to_text(raw):
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _load_enabled_custom_sources():
    try:
        with open(CUSTOM_SOURCES_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"custom_sources.json 未讀入，外部參考留空：{error}")
        return []
    items = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        items = payload.get("sources") or []
    enabled = []
    for item in items:
        if not isinstance(item, dict) or item.get("enabled") is not True:
            continue
        if not str(item.get("url") or "").strip():
            continue
        enabled.append(item)
    enabled.sort(key=lambda item: int(item.get("priority") or 100))
    return enabled


def _custom_rss_items(source, tw_tz, now_utc):
    url = str(source["url"])
    feed = feedparser.parse(fetch_bytes(url, timeout=20, cache_bust_param=None))
    cutoff = now_utc - timedelta(hours=48)
    found = []
    for entry in feed.entries:
        published_at = published_datetime(entry, tw_tz)
        if published_at is None:
            continue
        published_utc = published_at.astimezone(timezone.utc)
        if published_utc > now_utc or published_utc < cutoff:
            continue
        if is_pure_ai_news(entry) or not is_core_crypto_news(entry):
            continue
        title = _html_to_text(str(entry.get("title") or ""))
        summary = _html_to_text(
            str(entry.get("summary") or entry.get("description") or "")
        )
        name = str(source.get("name") or "")
        if name:
            title = re.sub(re.escape(name), "", title, flags=re.I).strip()
            summary = re.sub(re.escape(name), "", summary, flags=re.I).strip()
        excerpt = summary[:2500].strip()
        if len(title) < 4 and len(excerpt) < 40:
            continue
        found.append({
            "title": title[:120],
            "excerpt": excerpt or title,
        })
        if len(found) >= 3:
            break
    return found


def _custom_webpage(source):
    raw = fetch_bytes(str(source["url"]), timeout=20, cache_bust_param=None)
    text = _html_to_text(raw.decode("utf-8", errors="ignore"))
    name = str(source.get("name") or "")
    if name:
        text = re.sub(re.escape(name), "", text, flags=re.I).strip()
    excerpt = text[:2500].strip()
    if len(excerpt) < 80:
        return None
    if len(excerpt) > 2500:
        excerpt = excerpt[:2500]
    title = str(source.get("note") or "研究頁正文").strip()
    title = re.sub(re.escape(name), "", title, flags=re.I).strip() if name else title
    return {"title": title[:80] or "研究頁正文", "excerpt": excerpt}


def fetch_custom_sources(tw_tz=None):
    """讀取 enabled 的自訂 RSS 與網頁，整理成不含站名的背景參考。失敗的來源直接跳過。"""
    if tw_tz is None:
        tw_tz = ZoneInfo("Asia/Taipei")
    now_utc = datetime.now(timezone.utc)
    references = []
    for source in _load_enabled_custom_sources():
        kind = str(source.get("type") or "").strip().lower()
        source_id = source.get("id") or source.get("url")
        try:
            if kind == "rss":
                references.extend(_custom_rss_items(source, tw_tz, now_utc))
            elif kind == "webpage":
                page = _custom_webpage(source)
                if page:
                    references.append(page)
            else:
                print(f"custom source {source_id} 類型不支援，已跳過。")
        except urllib.error.HTTPError as error:
            if error.code in (403, 429):
                continue
            print(f"custom source {source_id} 已跳過：{error}")
        except Exception as error:
            message = str(error)
            if "HTTP Error 403" in message or "HTTP Error 429" in message:
                continue
            print(f"custom source {source_id} 已跳過：{error}")
    print(f"custom sources：啟用來源整理出 {len(references)} 則背景參考。")
    return references[:8]


# ── 主流程：彙總所有市場數據 ──────────────────────────────────────────

def fetch_all_market_data(now_tw, is_test):
    tw_tz = now_tw.tzinfo
    today_str = now_tw.strftime("%Y年%m月%d日")
    data_fetched_at = now_tw.strftime("%Y-%m-%d %H:%M 台灣時間")

    # 1. 抓取 RSS 新聞並提取焦點新聞完整全文
    entries = fetch_rss_news(tw_tz)
    entries = enrich_top_news_with_full_text(entries, max_articles=4)

    # 2. 恐慌貪婪指數
    today_fng, yest_fng = fetch_fear_and_greed()
    fng_num = int(today_fng["value"]) if today_fng else None

    # 3. 各大幣行情與技術指標
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
        spot_prices[name], pct = fetch_market_quote(s, primary_quotes)
        cdata[name] = (cl, pct, rsi)

    btc_w = fetch_market_series("BTCUSDT", "1w", 210)
    btc_cl, btc_pct, btc_rsi = cdata["BTC"]
    eth_cl, eth_pct, eth_rsi = cdata["ETH"]
    cur_p = str(presentation.round_usd(spot_prices["BTC"]))

    btc_metrics = calculate_market_structure(
        daily_histories["BTC"], spot_prices["BTC"], now_tw.date()
    )
    eth_metrics = calculate_market_structure(
        daily_histories["ETH"], spot_prices["ETH"], now_tw.date()
    )
    btc_oversold_events = find_rsi_oversold_events(
        daily_histories["BTC"], btc_metrics["rsi_series"], now_tw.date()
    )
    ma100d = str(presentation.round_usd(btc_metrics["sma_100"]))
    ma200d = str(presentation.round_usd(btc_metrics["sma_200"]))
    ma100w, ma200w = calc_sma(btc_w, 100), calc_sma(btc_w, 200)
    eth_ma100d = str(presentation.round_usd(eth_metrics["sma_100"]))
    eth_ma200d = str(presentation.round_usd(eth_metrics["sma_200"]))

    btc_technical_report = format_market_structure_report(
        "BTC", btc_metrics, spot_prices["BTC"]
    )
    eth_technical_report = format_market_structure_report(
        "ETH", eth_metrics, spot_prices["ETH"]
    )
    btc_oversold_report = format_oversold_report(
        btc_oversold_events, btc_metrics["rsi_series"]
    )

    # 4. 籌碼與鏈上數據
    stablecoin_changes = fetch_stablecoin_market_cap_changes()
    stablecoin_report = format_stablecoin_market_cap_report(stablecoin_changes)
    trader_position_metrics = fetch_trader_position_metrics()
    okx_spot_volume_metrics = fetch_okx_spot_volume_metrics()
    onchain_activity_metrics = fetch_onchain_activity_metrics()
    trader_position_report = format_trader_position_report(trader_position_metrics)
    spot_volume_report = format_spot_volume_report(okx_spot_volume_metrics)
    onchain_activity_report = format_onchain_activity_report(onchain_activity_metrics)
    chip_sentiment_report = format_chip_sentiment_report(
        fng_num,
        btc_metrics,
        okx_spot_volume_metrics,
    )

    mvrv = fetch_btc_mvrv()
    if mvrv:
        # Z-Score 必須沿用自身的標準差區間，不得再被恐慌指數／均線覆寫判讀。
        mvrv_zone = mvrv["zone"]
        mvrv["report"] = (
            f"MVRV Z-Score {presentation.format_mvrv(mvrv['value'], 'z_score')}，"
            f"判讀為{mvrv_zone}（{mvrv['source']}）"
        )
    else:
        mvrv = {}
    dca_snapshot = load_dca_usd_snapshot(spot_prices["BTC"])
    custom_references = fetch_custom_sources(tw_tz)

    coins_lines = []
    for name, (cl, pct, rsi) in cdata.items():
        sgn = "+" if pct >= 0 else ""
        r_txt = f" | RSI {rsi}" if rsi else ""
        coins_lines.append(
            f"{name}: {presentation.format_coin_price(name, spot_prices[name])} ({sgn}{pct:.2f}%{r_txt})"
        )
    coins_summary = "\n".join(coins_lines)

    fear_report = (
        f"今日 {today_fng['value']} ({today_fng['value_classification']})"
        + (
            f" / 昨日 {yest_fng['value']} ({yest_fng['value_classification']})"
            if yest_fng else ""
        )
        if today_fng else ""
    )

    s1 = (
        f"BTC 即時價格：{presentation.format_usd(cur_p)} USD\n"
        f"日線均線：100日 {presentation.format_usd(ma100d)} / 200日 {presentation.format_usd(ma200d)}\n"
        f"週線均線：100週 {presentation.format_usd(ma100w)} / 200週 {presentation.format_usd(ma200w)}\n"
        f"14日 RSI：{btc_rsi}"
    )
    eth_slide = (
        f"ETH 即時價格：{presentation.format_usd(spot_prices['ETH'])} USD ({eth_pct:+.2f}% 24H)\n"
        f"日線均線：100日 {presentation.format_usd(eth_ma100d)} / 200日 {presentation.format_usd(eth_ma200d)}\n"
        f"14日 RSI：{eth_rsi}"
    )

    return {
        "entries": entries,
        "today_fng": today_fng,
        "yest_fng": yest_fng,
        "fng_num": fng_num,
        "cdata": cdata,
        "spot_prices": spot_prices,
        "daily_histories": daily_histories,
        "cur_p": cur_p,
        "btc_pct": btc_pct,
        "btc_rsi": btc_rsi,
        "eth_pct": eth_pct,
        "eth_rsi": eth_rsi,
        "ma100d": ma100d,
        "ma200d": ma200d,
        "ma100w": ma100w,
        "ma200w": ma200w,
        "eth_ma100d": eth_ma100d,
        "eth_ma200d": eth_ma200d,
        "btc_metrics": btc_metrics,
        "eth_metrics": eth_metrics,
        "btc_oversold_events": btc_oversold_events,
        "stablecoin_changes": stablecoin_changes,
        "stablecoin_report": stablecoin_report,
        "trader_position_metrics": trader_position_metrics,
        "okx_spot_volume_metrics": okx_spot_volume_metrics,
        "onchain_activity_metrics": onchain_activity_metrics,
        "trader_position_report": trader_position_report,
        "spot_volume_report": spot_volume_report,
        "onchain_activity_report": onchain_activity_report,
        "chip_sentiment_report": chip_sentiment_report,
        "btc_technical_report": btc_technical_report,
        "eth_technical_report": eth_technical_report,
        "btc_oversold_report": btc_oversold_report,
        "coins_summary": coins_summary,
        "fear_report": fear_report,
        "data_fetched_at": data_fetched_at,
        "s1": s1 + (f"\n{mvrv['report']}" if mvrv.get("report") else ""),
        "eth_slide": eth_slide,
        "mvrv": mvrv,
        "dca_snapshot": dca_snapshot,
        "custom_references": custom_references,
    }
