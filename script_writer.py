import os
import re
import json
import time
import market_data
import urllib.request
from datetime import datetime
from itertools import permutations
from zoneinfo import ZoneInfo
from opencc import OpenCC

from presentation import (
    EXCLUDED_PLATFORMS,
    build_tutorial_cards,
    fill_story_slide_bodies,
    fit_complete_phrase,
    format_usd,
    plan_daily_slides,
    sanitize_text,
    strip_bracket_tags,
)


# ── 文字清理與格式工具 ──────────────────────────────────────────

INCOMPLETE_METRIC = re.compile(
    r"抓不到|無法取得|資料缺失|數據缺失|連線失敗|API\s*異常|"
    r"未取得|資料不足|數據不足|略過此項|略過，不推測|略過超賣|"
    r"請略過|不得推測|無法做情緒|無法區分|所以不預測"
)
LEAK_SENTENCE = re.compile(
    r"抓不到|無法取得|資料缺失|數據缺失|連線失敗|API\s*異常|"
    r"未取得|資料不足|數據不足|所以不預測|接口異常|系統缺漏"
)


def present_metric(text):
    """資料摘要只留下完整數值。缺項整句拿掉，不把缺漏原因送進模型。"""
    kept = []
    for piece in re.split(r"(?<=[；。])", str(text or "")):
        if not piece.strip() or INCOMPLETE_METRIC.search(piece):
            continue
        kept.append(piece)
    return "".join(kept).strip()


def silence_gap_leaks(text):
    """口播、標題與兩欄摘要若說出系統缺漏，刪掉該句。"""
    lines = []
    for raw in str(text or "").splitlines():
        pieces = re.split(r"(?<=[。！？；])", raw)
        kept = [piece for piece in pieces if piece.strip() and not LEAK_SENTENCE.search(piece)]
        line = "".join(kept).strip()
        if re.fullmatch(r"(?:男|女)[：:]\s*(?:\[[^\]]+\])?\s*", line):
            continue
        if line:
            lines.append(line)
    return "\n".join(lines)


def _metric_bullet(label, value):
    text = present_metric(value)
    if not text:
        return None
    return f"- {label}：{text}"


def _custom_reference_block(references):
    lines = []
    for item in references or []:
        if not isinstance(item, dict):
            continue
        title = present_metric(item.get("title") or "")
        excerpt = present_metric(item.get("excerpt") or "")
        if not title and not excerpt:
            continue
        lines.append(f"{len(lines) + 1}. {title}\n{excerpt[:2200]}")
    if not lines:
        return ""
    return (
        "【外部背景參考】以下內容只供消化成自己的分析。"
        "禁止唸出網站、媒體、作者或網址。\n"
        + "\n\n".join(lines)
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


def normalize_long_term_terms(text):
    text = re.sub(r"\bHODLers?\b", "長期持有者", str(text), flags=re.I)
    text = re.sub(r"\bHODL\b", "長期持有", text, flags=re.I)
    # 男主播唯一名稱為「比特汪」，強制封殺「位元汪／位元犬」錯誤譯名。
    text = re.sub(r"位元[汪犬]", "比特汪", text)
    return text


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


# ── Gemini API 調用 ──────────────────────────────────────────

SCRIPT_CACHE_PATH = "cache_last_script.json"
PRODUCTION_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
TEST_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
]


def reuse_script_enabled():
    return os.environ.get("REUSE_SCRIPT", "").strip().lower() == "true"


def _call_openrouter_fallback(prompt_txt, max_output_tokens=8192, timeout=240):
    """Gemini 全線失敗時改用 OpenRouter；只接受非空文字，暫時性錯誤會退避重試。"""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("[OpenRouter] 未設定 OPENROUTER_API_KEY，略過備援。")
        return None

    tc_rule = (
        "請全程使用道地台灣繁體中文（台灣財經習慣用語），嚴禁簡體字；"
        "若要求 JSON 格式請直接輸出純 JSON。"
    )
    full_prompt = f"{prompt_txt}\n\n【格式要求】{tc_rule}"
    openrouter_models = [
        "baidu/ernie-5.5-flash-preview:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "openrouter/free",
    ]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://is.gd/2JOYTF",
        "X-Title": "DailyCryptoPodcast",
    }
    last_err = None
    transient_http_codes = (408, 425, 429, 500, 502, 503, 504)

    for or_model in openrouter_models:
        body = json.dumps({
            "model": or_model,
            "messages": [
                {"role": "system", "content": tc_rule},
                {"role": "user", "content": full_prompt},
            ],
            "max_tokens": max_output_tokens,
        }).encode("utf-8")

        for attempt in range(3):
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")
                res = json.loads(raw)
                choices = res.get("choices") or []
                message = choices[0].get("message") if choices else None
                content = message.get("content") if isinstance(message, dict) else None

                # 部分供應商可能回傳 content=null/空字串；不能把它誤判為備援成功。
                if isinstance(content, list):
                    content = "".join(
                        item.get("text", "") for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("OpenRouter 回應缺少有效的 message.content")

                print(f"[OpenRouter 備援成功] 模型：{or_model}")
                return content.strip()

            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="ignore")
                print(f"OpenRouter API Error ({e.code}) for model '{or_model}': {err_msg}")
                last_err = e
                if e.code in transient_http_codes and attempt < 2:
                    wait_seconds = min(2 ** (attempt + 1), 8)
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    if retry_after:
                        try:
                            wait_seconds = min(max(float(retry_after), 1), 30)
                        except (TypeError, ValueError):
                            pass
                    print(f"[OpenRouter] 暫時性錯誤，{wait_seconds:.0f} 秒後重試 ({attempt + 1}/2)。")
                    time.sleep(wait_seconds)
                    continue
                break
            except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
                print(f"OpenRouter model '{or_model}' connection error: {e}")
                last_err = e
                if attempt < 2:
                    wait_seconds = min(2 ** (attempt + 1), 8)
                    print(f"[OpenRouter] {wait_seconds} 秒後重試 ({attempt + 1}/2)。")
                    time.sleep(wait_seconds)
                    continue
                break
            except (ValueError, KeyError, IndexError, json.JSONDecodeError) as e:
                print(f"OpenRouter model '{or_model}' 回應格式無效：{e}")
                last_err = e
                break
            except Exception as e:
                print(f"Error calling OpenRouter model '{or_model}': {e}")
                last_err = e
                break

    if last_err:
        raise RuntimeError(f"Gemini 與 OpenRouter 備援模型皆未能產生有效文字；最後錯誤：{last_err}") from last_err
    raise RuntimeError("Gemini 與 OpenRouter 備援模型皆未能產生有效文字。")


def call_gemini(prompt_txt, max_output_tokens=8192, timeout=240, json_mode=False, models=None):
    if models:
        models = list(models)
    else:
        models = list(TEST_MODELS if os.environ.get("TEST_MODE", "").strip().lower() == "true" else PRODUCTION_MODELS)
    custom_model = os.environ.get("GEMINI_MODEL", "").strip()
    if custom_model:
        models = [custom_model] + [m for m in models if m != custom_model]

    last_err = None
    generation_config = {"maxOutputTokens": max_output_tokens}
    if json_mode:
        generation_config = {
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        }
    for model in models:
        g_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.environ['GEMINI_API_KEY'].strip()}"
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt_txt}]}],
            "generationConfig": generation_config,
        }).encode("utf-8")
        while True:
            req = urllib.request.Request(g_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                res = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))
                return res["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="ignore")
                print(f"Gemini API Error ({e.code}) for model '{model}': {err_msg}")
                last_err = e
                if e.code in (404, 429, 503):
                    print(f"Gemini model '{model}' returned {e.code}; switching immediately.")
                    break
                break
            except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
                print(f"Gemini model '{model}' timed out or lost connection; switching immediately: {e}")
                last_err = e
                break
            except Exception as e:
                print(f"Error calling Gemini model '{model}': {e}")
                last_err = e
                break

    # Gemini 全線失敗（503/429/連線異常），自動切換 OpenRouter 免費模型第二備援。
    # 必須明確驗證回傳內容，避免空值或無效結果被誤判為成功，
    # 也避免 OpenRouter 已成功時仍落入下方 raise last_err。
    try:
        fallback = _call_openrouter_fallback(
            prompt_txt,
            max_output_tokens=max_output_tokens,
            timeout=timeout,
        )
    except Exception as fallback_err:
        print(f"[AI 備援失敗] OpenRouter 無法完成請求：{fallback_err}")
        if last_err:
            raise RuntimeError(
                f"Gemini 模型全部失敗，OpenRouter 備援也失敗。"
                f"Gemini 最後錯誤：{last_err}；OpenRouter 錯誤：{fallback_err}"
            ) from fallback_err
        raise

    if isinstance(fallback, str) and fallback.strip():
        print("[AI 備援] 已取得有效文字，回傳 OpenRouter 結果。")
        return fallback.strip()

    if last_err:
        raise RuntimeError(
            f"Gemini 模型全部失敗，OpenRouter 未回傳有效內容。最後 Gemini 錯誤：{last_err}"
        ) from last_err
    raise RuntimeError("No Gemini models available; OpenRouter returned no usable content.")


# ── DCA 績效指導語 ──────────────────────────────────────────

def load_show_angle():
    """從至少 10 種視角裡避開近 7 天用過的切入點，並抽出術語供本次 prompt 使用。"""
    base = "knowledge_base"
    with open(os.path.join(base, "script_angles.json"), encoding="utf-8") as handle:
        angles = json.load(handle)["angles"]
    with open(os.path.join(base, "vocab_db.json"), encoding="utf-8") as handle:
        terms = json.load(handle)["terms"]
    log_path = os.path.join(base, "recent_topics_log.json")
    try:
        with open(log_path, encoding="utf-8") as handle:
            entries = json.load(handle).get("entries", [])
    except (OSError, ValueError, json.JSONDecodeError):
        entries = []
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    fresh = []
    for entry in entries:
        try:
            used = datetime.fromisoformat(entry["date"]).date()
        except (TypeError, ValueError, KeyError):
            continue
        if (today - used).days < 7:
            fresh.append(entry)
    used_ids = {entry.get("angle_id") for entry in fresh}
    try:
        with open("learning_insights.json", encoding="utf-8") as handle:
            avoid_ids = set(json.load(handle).get("avoid_angle_ids") or [])
    except (OSError, ValueError, json.JSONDecodeError):
        avoid_ids = set()
    used_ids |= avoid_ids
    chosen = next((angle for angle in angles if angle["id"] not in used_ids), angles[0])
    fresh.append({"date": today.isoformat(), "angle_id": chosen["id"], "angle": chosen["title"]})
    with open(log_path, "w", encoding="utf-8") as handle:
        json.dump({"entries": fresh[-7:]}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    offset = today.toordinal() % len(terms)
    sample = [terms[(offset + index) % len(terms)] for index in range(6)]
    vocab = "；".join(f"{item['term']}：{item['gloss']}" for item in sample)
    recent = "、".join(entry.get("angle") or entry.get("angle_id", "") for entry in fresh[:-1]) or "無"
    return chosen["title"], vocab, recent, chosen["id"]


def _learning_prompt_line():
    """讀本地 learning_insights.json，不另外呼叫模型。"""
    try:
        with open("learning_insights.json", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return "尚無近 7 日主軸，依今日數據開題即可。禁止使用「開場｜盤勢觀察與投資風險提醒」。"
    avoid = "、".join(data.get("avoid_angles") or []) or "無"
    keywords = "、".join(data.get("recent_title_keywords") or []) or "無"
    warnings = "；".join(data.get("previous_warnings") or []) or "無"
    leads = "；".join(data.get("recent_editorial_leads") or []) or "無"
    hooks = "；".join(data.get("recent_opening_hooks") or []) or "無"
    titles = "；".join(data.get("recent_slide_titles") or []) or "無"
    return (
        f"今日避開這些切入角度：{avoid}。"
        f"近 7 日已經用過的主軸，今天禁止再用相似視角或句型：{leads}。"
        f"近 7 日開場鉤子，禁止沿用同樣句型：{hooks}。"
        f"近 7 日投影片標題，禁止再用相似標題結構：{titles}。"
        f"近 7 日標題已用過的關鍵詞不要再當主標：{keywords}。"
        f"上一集品質警告：{warnings}。"
        "禁止出現「開場｜盤勢觀察與投資風險提醒」。"
    )


BANNED_SLIDE_TITLES = (
    "開場｜盤勢觀察與投資風險提醒",
    "開場與風險提醒",
    "盤勢觀察與投資風險提醒",
)
CATCHPHRASE_CATEGORIES = (
    "opening_reactions",
    "analytical_transitions",
    "skeptical_pushback",
    "closing_encouragement",
    "banter_pairs",
)


def _clean_dynamic_title(title, fallback):
    cleaned = clean_slide_title(silence_gap_leaks(sanitize_text(title)))
    if not cleaned or any(banned in cleaned for banned in BANNED_SLIDE_TITLES):
        cleaned = clean_slide_title(fallback)
    if not cleaned or any(banned in cleaned for banned in BANNED_SLIDE_TITLES):
        cleaned = "今天先看這組衝突數字"
    return cleaned[:36]


def suggest_editorial_lead(mdata):
    """從今日漲跌、恐慌與均線裡挑波動最大的一條當主軸提示。"""
    mdata = mdata or {}
    scored = []
    fear = mdata.get("fng_num")
    if isinstance(fear, (int, float)):
        scored.append((max(0, 50 - fear), f"恐慌指數 {int(fear)} 和價格是否背離"))
    btc_pct = mdata.get("btc_pct")
    eth_pct = mdata.get("eth_pct")
    if isinstance(btc_pct, (int, float)) and isinstance(eth_pct, (int, float)) and abs(btc_pct - eth_pct) >= 2:
        scored.append((abs(btc_pct - eth_pct) + 3, f"比特幣 {btc_pct:+.1f}% 與以太坊 {eth_pct:+.1f}% 不同步"))
    if isinstance(btc_pct, (int, float)) and abs(btc_pct) >= 3:
        scored.append((abs(btc_pct) + 2, f"比特幣 24 小時 {btc_pct:+.1f}%"))
    for name, payload in (mdata.get("cdata") or {}).items():
        if not isinstance(payload, (list, tuple)) or len(payload) < 2:
            continue
        pct = payload[1]
        if isinstance(pct, (int, float)) and abs(pct) >= 3:
            scored.append((abs(pct), f"{name} 24 小時 {pct:+.1f}%"))
    try:
        if float(str(mdata.get("cur_p")).replace(",", "")) < float(str(mdata.get("ma200d")).replace(",", "")):
            scored.append((8, f"比特幣 {format_usd(mdata.get('cur_p'))} 仍在 200 日均線下方"))
    except (TypeError, ValueError):
        pass
    if not scored:
        return "今天已取得數據裡最反常的那一項"
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def fallback_slide_titles(mdata, show_dca, news_details, week_lines):
    """沒有模型標題時，用今日數字組標題，不用固定公版。"""
    mdata = mdata or {}
    price = format_usd(mdata.get("cur_p")) if mdata.get("cur_p") not in (None, "") else "比特幣"
    pct = mdata.get("btc_pct")
    move = f"{float(pct):+.1f}%" if isinstance(pct, (int, float)) else "今天的波動"
    fear = mdata.get("fng_num")
    fear_title = (
        f"恐慌指數 {int(fear)}，價格有跟著怕嗎？"
        if isinstance(fear, (int, float)) else "情緒和價格今天站在哪一邊？"
    )
    titles = [f"比特幣 {price} {move}，盤面先看這組衝突"]
    if show_dca:
        titles.append(f"模擬帳本對上 {price}，成本還守得住嗎？")
    titles.extend([
        fear_title,
        f"均線與 RSI：{price} 是跌深還是半山腰？",
        "以太坊今天是跟著走，還是被甩開？",
    ])
    details = list(news_details or [])
    for index in range(3):
        raw = details[index].get("slide_title") if index < len(details) and isinstance(details[index], dict) else ""
        titles.append(_clean_dynamic_title(raw, f"這則消息，{price} 有沒有跟著走？"))
    titles.extend([
        f"六幣掃描：{move} 裡誰才是今天的主角？",
        "24 小時漲跌，資金站在誰那邊？",
        f"攻防價位：{price} 先測支撐還是壓力？",
    ])
    weeks = [str(item) for item in (week_lines or [])]
    for index in range(3):
        raw = weeks[index] if index < len(weeks) else ""
        titles.append(_clean_dynamic_title(raw, f"未來 24 小時先核對條件 {index + 1}"))
    titles.append(f"收束：{price} 先驗證，再談動作")
    return [_clean_dynamic_title(title, f"今日數字 {index + 1}") for index, title in enumerate(titles)]


def align_slide_titles(raw_titles, mdata, show_dca=None, news_details=None, week_lines=None):
    if isinstance(mdata, list):
        fallback = mdata
    else:
        fallback = fallback_slide_titles(mdata, show_dca, news_details, week_lines)
    raw = list(raw_titles or [])
    return [
        _clean_dynamic_title(raw[index] if index < len(raw) else "", backup)
        for index, backup in enumerate(fallback)
    ]


def _opening_hook(part1):
    for line in str(part1 or "").splitlines():
        match = re.match(r"^(?:男|女)[：:]\s*(.+)", line.strip())
        if not match:
            continue
        text = re.sub(r"^\[(?:excited|serious|analytical|debate)\]\s*", "", match.group(1)).strip()
        if text:
            return text[:80]
    return ""


def sample_host_catchphrases():
    """從 host_catchphrases.json 抽樣，並避開昨天與今天已用過的句子。"""
    path = os.path.join("knowledge_base", "host_catchphrases.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return "口頭禪資料庫暫時讀不到，仍用比特汪與以太喵的既有性格說話。", []
    blocked = set()
    try:
        with open("learning_insights.json", encoding="utf-8") as handle:
            insights = json.load(handle)
        blocked = {str(item) for item in (insights.get("recent_catchphrases") or [])}
    except (OSError, ValueError, json.JSONDecodeError):
        blocked = set()
    hosts = data.get("hosts") if isinstance(data.get("hosts"), dict) else data
    seed = datetime.now(ZoneInfo("Asia/Taipei")).toordinal()
    picked = []
    lines = []
    for host in ("以太喵", "比特汪"):
        block = hosts.get(host) if isinstance(hosts, dict) and isinstance(hosts.get(host), dict) else {}
        for category in CATCHPHRASE_CATEGORIES:
            options = [str(item).strip() for item in (block.get(category) or []) if str(item).strip()]
            if not options:
                continue
            start = seed % len(options)
            ordered = options[start:] + options[:start]
            choice = next((item for item in ordered if item not in blocked and item not in picked), ordered[0])
            picked.append(choice)
            lines.append(f"{host}／{category}：{choice}")
    if not lines:
        return "口頭禪資料庫沒有可用句子，仍用比特汪與以太喵的既有性格說話。", []
    return "本次可自然穿插的口頭禪，不要每句都用，也不要連續重複：\n" + "\n".join(lines), picked


def choose_show_format():
    """連續四集不重複：避開最近三集用過的模式。"""
    path = os.path.join("knowledge_base", "show_formats.json")
    with open(path, encoding="utf-8") as handle:
        formats = json.load(handle)["formats"]
    used = []
    try:
        with open("learning_insights.json", encoding="utf-8") as handle:
            used = [str(item) for item in (json.load(handle).get("recent_format_ids") or []) if str(item).strip()]
    except (OSError, ValueError, json.JSONDecodeError):
        used = []
    blocked = set(used[-3:])
    for item in formats:
        if item.get("id") not in blocked:
            return item
    return formats[0]


def _slide_slot_names(show_dca):
    names = ["開場鉤子，標題必須帶今日最衝突的數字"]
    if show_dca:
        names.append("模擬定投帳本，標題要帶出本期美元結果")
    names.extend([
        "恐慌與貪婪，標題要帶指數",
        "比特幣均線與 RSI，標題要帶價格或 RSI",
        "以太坊相對比特幣",
        "第一則新聞，標題就是該則事件",
        "第二則新聞，標題就是該則事件",
        "第三則新聞，標題就是該則事件",
        "六幣裡今天真正在動的那一個，安靜的幣不要做成標題主角",
        "24 小時漲跌對照",
        "比特幣支撐與壓力",
        "未來 24 小時條件一",
        "未來 24 小時條件二",
        "未來 24 小時條件三",
        "收束，仍要帶一個今日數字",
    ])
    return names


def dca_performance_guidance(unrealized_pnl_usd):
    if unrealized_pnl_usd < 0:
        return (
            "浮虧",
            "目前帳本是以美元計價的未實現浮虧。比特汪要冷靜看待當期損益："
            "只有買進價低於既有美元均價時，新增投入才會拉低平均成本。"
            "以太喵可解釋低檔持續投入、未來若價格回升時資產可能受益的情境，"
            "但不得說成必然的微笑曲線或保證翻正。可溫和提醒，恐慌時匆忙賣出可能鎖定損失，"
            "但尊重不同人的風險承受度；BTC仍可能續跌，甚至長期無法回到買入成本。",
            "浮虧期間，低於均價的買入才可能拉低平均成本；未來價格與報酬仍不確定。",
        )
    if unrealized_pnl_usd > 0:
        return (
            "帳面獲利",
            "目前帳本為美元計價的帳面獲利。比特汪與以太喵可指出，這是截至今日市價高於累積成本"
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


# ── 逐字稿結構提取與對齊 ──────────────────────────────────────────

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


def _news_card_from_entry(entries, number):
    if entries and 1 <= number <= len(entries):
        entry = entries[number - 1][1]
        title = clean_slide_title(clean_txt(entry.get("title", "")))[:32]
        summary = remove_publish_time(clean_txt(entry.get("summary", "") or entry.get("description", "")))[:160]
    else:
        title = ""
        summary = ""
    return {
        "source_news_number": number if entries and 1 <= number <= len(entries) else 1,
        "slide_title": title or "價格與均線",
        "positive": summary or "今天仍可對照已取得的價格、均線與漲跌幅。",
        "risk": "單則消息不能當成買賣指令，仍要看價格有沒有被成交量確認。",
    }


def extract_slide_news_details(raw, available_entries):
    marker_lines = re.findall(
        r"^[ \t]*SLIDE_NEWS(\d+):[ \t]*(\{.*\})[ \t]*$", raw, flags=re.M
    )
    details = []
    selected_news_numbers = set()
    for index, payload in marker_lines:
        try:
            record = json.loads(payload)
        except json.JSONDecodeError:
            print(f"SLIDE_NEWS{index} 不是合法 JSON，改由當日新聞補齊。")
            continue
        if not isinstance(record, dict):
            continue
        news_number = record.get("source_news_number")
        if (
            isinstance(news_number, bool)
            or not isinstance(news_number, int)
            or not available_entries
            or not 1 <= news_number <= len(available_entries)
            or news_number in selected_news_numbers
        ):
            news_number = next(
                (n for n in range(1, max(len(available_entries), 1) + 1) if n not in selected_news_numbers),
                1,
            )
        fallback = _news_card_from_entry(available_entries, news_number if isinstance(news_number, int) else 1)
        source_title = fallback["slide_title"]
        if available_entries and 1 <= fallback["source_news_number"] <= len(available_entries):
            source_title = clean_txt(available_entries[fallback["source_news_number"] - 1][1].get("title", "")) or source_title

        slide_title = clean_slide_title(record.get("slide_title"))
        if not slide_title or len(slide_title) < 4 or slide_title in {"今日市場焦點", "市場焦點"}:
            slide_title = clean_slide_title(source_title)[:32] or fallback["slide_title"]

        positive = remove_publish_time(str(record.get("positive") or "")).strip() or fallback["positive"]
        risk = remove_publish_time(str(record.get("risk") or "")).strip() or fallback["risk"]
        selected_news_numbers.add(fallback["source_news_number"])
        details.append({
            "source_news_number": fallback["source_news_number"],
            "slide_title": slide_title,
            "positive": positive,
            "risk": risk,
        })
        if len(details) == 3:
            break
    next_number = 1
    while len(details) < 3:
        while next_number in selected_news_numbers:
            next_number += 1
        card = _news_card_from_entry(available_entries, next_number)
        selected_news_numbers.add(card["source_news_number"])
        details.append(card)
        next_number += 1
        if next_number > 12:
            break
    while len(details) < 3:
        details.append(_news_card_from_entry(available_entries, len(details) + 1))

    raw = re.sub(
        r"^[ \t]*SLIDE_NEWS\d+:.*(?:\r?\n|$)", "", raw, flags=re.M
    )
    return details, raw.rstrip()


# ── 主流程：生成完整 AI 腳本 ──────────────────────────────────────────

def load_cached_script():
    if not reuse_script_enabled() or not os.path.isfile(SCRIPT_CACHE_PATH):
        return None
    try:
        with open(SCRIPT_CACHE_PATH, encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[REUSE_SCRIPT] 快取讀取失敗，改走正常產稿：{error}")
        return None
    required = (
        "part1_raw", "part2_raw", "full_script", "ai_title", "video_title",
        "slide_news_details", "slide_news", "slide_24h_conditions",
        "dca_slide_caption", "dca_slide_body",
    )
    if not isinstance(cached, dict) or any(key not in cached for key in required):
        print("[REUSE_SCRIPT] 快取欄位不完整，改走正常產稿。")
        return None
    print("[REUSE_SCRIPT] 已載入 cache_last_script.json，本集不呼叫 Gemini。")
    return cached


def save_cached_script(result):
    with open(SCRIPT_CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"[REUSE_SCRIPT] 已備份劇本至 {SCRIPT_CACHE_PATH}。")


def _reference_news(mdata):
    if not isinstance(mdata, dict):
        return []
    raw = mdata.get("news") or mdata.get("entries") or []
    items = []
    for item in raw:
        entry = item[1] if isinstance(item, (list, tuple)) and len(item) == 2 else item
        if not isinstance(entry, dict):
            continue
        title = clean_slide_title(clean_txt(entry.get("title") or ""))[:32]
        summary = remove_publish_time(clean_txt(entry.get("summary") or entry.get("description") or ""))[:160]
        if title or summary:
            items.append((title or "市場焦點", summary or title or "市場焦點"))
    return items


def _pad_slide_news(news, mdata):
    cleaned = []
    if isinstance(news, list):
        for item in news:
            if isinstance(item, dict) and (item.get("slide_title") or item.get("positive")):
                cleaned.append(item)
            if len(cleaned) == 3:
                return cleaned
    references = _reference_news(mdata)
    used = {
        int(item.get("source_news_number"))
        for item in cleaned
        if isinstance(item.get("source_news_number"), int) and not isinstance(item.get("source_news_number"), bool)
    }
    cursor = 0
    while len(cleaned) < 3:
        if cursor < len(references):
            title, summary = references[cursor]
            number = cursor + 1
            cursor += 1
        else:
            title, summary = "價格與均線", "今天仍可對照已取得的價格、均線與漲跌幅。"
            number = len(cleaned) + 1
        while number in used:
            number += 1
        used.add(number)
        cleaned.append({
            "source_news_number": number,
            "slide_title": title,
            "positive": summary,
            "risk": "單則消息不能當成買賣指令，仍要看價格有沒有被成交量確認。",
        })
    if len(cleaned) < 3:
        print(f"slide_news 只有 {len(news) if isinstance(news, list) else 0} 筆，已補齊到 {len(cleaned)} 筆。")
    elif not isinstance(news, list) or len(news) < 3:
        print("slide_news 不足 3 筆，已用當日新聞標題與摘要補齊。")
    return cleaned[:3]


def _pad_slide_week(week, mdata):
    cleaned = []
    if isinstance(week, list):
        for item in week:
            text = str(item).strip()
            if text:
                cleaned.append(text)
            if len(cleaned) == 3:
                return cleaned
    metrics = (mdata or {}).get("btc_metrics") if isinstance(mdata, dict) else {}
    metrics = metrics or {}
    rsi = (mdata or {}).get("btc_rsi") if isinstance(mdata, dict) else ""
    support = metrics.get("support")
    resistance = metrics.get("resistance")
    defaults = [
        f"支撐觀察｜留意 BTC 是否守住 {format_usd(support)}。" if support else "支撐觀察｜留意 BTC 是否守住近期區間下緣。",
        f"壓力觀察｜留意 BTC 是否站穩 {format_usd(resistance)}。" if resistance else "壓力觀察｜留意 BTC 是否站上近期區間上緣。",
        f"RSI 觀察｜14 日 RSI 目前 {rsi}，只當動能參考，不是買賣指令。" if rsi else "RSI 觀察｜留意 14 日 RSI 靠近超買或超賣時，只當動能參考。",
    ]
    for line in defaults:
        if len(cleaned) >= 3:
            break
        if line not in cleaned:
            cleaned.append(line)
    if not isinstance(week, list) or len([item for item in week if str(item).strip()]) < 3:
        print("slide_week 不足 3 條，已用支撐、壓力與 RSI 補齊。")
    return cleaned[:3]


def _parse_editorial_brief(raw_text, mdata=None, slide_title_count=14):
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    payload = {}
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    title = str(payload.get("title") or "").strip() or "主流幣均線攻防與深度籌碼拆解"
    editorial_lead = silence_gap_leaks(sanitize_text(payload.get("editorial_lead") or "")).strip() or "多空博弈白熱化，從結構看清表面雜訊"
    core_conflict = silence_gap_leaks(sanitize_text(payload.get("core_conflict") or "")).strip()
    smart_money_intent = silence_gap_leaks(sanitize_text(payload.get("smart_money_intent") or "")).strip()
    bull_case = silence_gap_leaks(sanitize_text(payload.get("bull_case") or "")).strip()
    bear_case = silence_gap_leaks(sanitize_text(payload.get("bear_case") or "")).strip()
    opening_hook = silence_gap_leaks(sanitize_text(payload.get("opening_hook") or "")).strip()
    act1_outline = str(payload.get("act1_outline") or "").strip()
    act2_outline = str(payload.get("act2_outline") or "").strip()
    act3_outline = str(payload.get("act3_outline") or "").strip()

    slide_news = _pad_slide_news(payload.get("slide_news"), mdata)
    slide_week = _pad_slide_week(payload.get("slide_week"), mdata)
    raw_titles = payload.get("slide_titles") if isinstance(payload.get("slide_titles"), list) else []
    slide_titles = [str(item).strip() for item in raw_titles if str(item).strip()]

    return {
        "title": title,
        "editorial_lead": editorial_lead,
        "core_conflict": core_conflict,
        "smart_money_intent": smart_money_intent,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "opening_hook": opening_hook,
        "act1_outline": act1_outline,
        "act2_outline": act2_outline,
        "act3_outline": act3_outline,
        "slide_titles": slide_titles,
        "slide_news": slide_news,
        "slide_week": slide_week,
    }


def _expand_act_if_short(act_text, act_num, act_name, context_hint, is_test):
    if is_test:
        return act_text
    chars = _spoken_chars(act_text)
    if chars >= 1650:
        return act_text
    print(f"第 {act_num} 幕（{act_name}）目前 {chars} 字，偏短（目標 1,800~2,200 字），進行深度擴寫補強...")
    expand_prompt = f"""以下是【幣看趨勢】播客第 {act_num} 幕（{act_name}）的口播對話。目前字數只有 {chars} 字，明顯偏短。
請進行深入擴寫，字數嚴格要求達到 1,800 至 2,200 繁中字：
1. 補充更多白話生動比喻、歷史牛熊對照、機構行為心理剖析與深層矛盾拆解。
2. 保留原有角色（男：比特汪、女：以太喵）與所有 [SLIDE_x] 投影片錨點標記。
3. 嚴禁連續報數字流水帳，嚴禁「謹慎觀望、注意風險」等萬用廢話套話。
4. 只輸出對話逐字稿，不要 Markdown，不要包裹 JSON。

背景與核心脈絡：
{context_hint}

目前對話稿：
{act_text}"""
    try:
        expanded = call_gemini(expand_prompt, max_output_tokens=16384, timeout=300)
        if _spoken_chars(expanded) > chars:
            print(f"第 {act_num} 幕擴寫成功：由 {chars} 字提升至 {_spoken_chars(expanded)} 字。")
            return expanded
    except Exception as e:
        print(f"第 {act_num} 幕擴寫失敗，保留原稿：{e}")
    return act_text


def _ensure_slide_anchors(script, slide_count):
    lines = script.split("\n")
    found_anchors = set()
    for line in lines:
        for match in re.finditer(r"\[SLIDE_(\d+)\]", line):
            found_anchors.add(int(match.group(1)))

    missing = [i for i in range(1, slide_count + 1) if i not in found_anchors]
    if not missing:
        return script

    print(f"警告：逐字稿缺少投影片錨點 {missing}，進行自動依序補齊...")
    dialogue_indices = [
        idx for idx, line in enumerate(lines)
        if line.strip().startswith(("男：", "女："))
    ]
    if not dialogue_indices:
        return script

    step = max(1, len(dialogue_indices) // slide_count)
    for m in missing:
        target_idx = min(dialogue_indices[-1], dialogue_indices[min(len(dialogue_indices) - 1, (m - 1) * step)])
        line = lines[target_idx]
        if line.startswith("男："):
            lines[target_idx] = f"男：[SLIDE_{m}]" + line[3:]
        elif line.startswith("女："):
            lines[target_idx] = f"女：[SLIDE_{m}]" + line[3:]
        else:
            lines[target_idx] = f"[SLIDE_{m}] {line}"
    return "\n".join(lines)


def _spoken_chars(text):
    return len(re.sub(r"\s+", "", text or ""))


def _log_length_gate(count, label):
    if count >= 4800:
        print(f"{label} {count} 字，進入後續流程。")
    elif count >= 3200:
        print(f"警告：{label} {count} 字，低於 4800，已達 3200，放行。")
    else:
        print(f"警告：{label} {count} 字，低於 3200，仍放行以免整集中斷。")


def _episode_long_enough(part1_raw, part2_raw, is_test):
    if is_test:
        return len(part1_raw) >= 40 and len(part2_raw) >= 40
    return _spoken_chars(part1_raw) + _spoken_chars(part2_raw) >= 3200


# ── 動態 YouTube Metadata 生成 ──────────────────────────────────

def _generate_yt_title(mdata, editorial_brief, full_script, is_test):
    """生成 65–88 字高點擊率動態標題，依當日核心事件與反直覺衝突。"""
    cur_p = mdata.get("cur_p", 0)
    btc_pct = mdata.get("btc_pct", 0)
    fng = mdata.get("fng_num", 50)
    core_conflict = editorial_brief.get("core_conflict", "")
    opening_hook = editorial_brief.get("opening_hook", "")

    # 嘗試用 Gemini 生成高點擊標題
    if not is_test:
        prompt = f"""你是一位專業的加密貨幣 YouTube 頻道標題優化師。
請根據以下當日核心資訊，撰寫一個 65–88 字元（含標點）的高點擊率中文標題。

核心要求：
1. 必須包含好奇心缺口（例如反直覺數據、衝突事件、懸念問題）
2. 禁止使用「加密貨幣晨報」「BTC、ETH」等公版格式
3. 必須具體提到當日最衝突的數字或事件
4. 結尾加上「｜幣看趨勢」
5. 嚴格控制在 65–88 字元

當日數據：
- BTC 現價：{format_usd(cur_p)}，24H 漲跌：{btc_pct:+.2f}%
- 恐慌貪婪指數：{fng}
- 核心矛盾：{core_conflict}
- 開場鉤子：{opening_hook}

只輸出標題文字，不要任何多餘內容。"""
        try:
            title = call_gemini(prompt, max_output_tokens=200, timeout=30).strip()
            title = sanitize_text(title)
            if 65 <= len(title) <= 88 and "幣看趨勢" in title:
                return title
            print(f"Gemini 標題長度 {len(title)} 不在 65-88 範圍，使用備援。")
        except Exception as e:
            print(f"Gemini 標題生成失敗，使用備援：{e}")

    # 備援：依數據動態組裝
    direction = "狂飆" if btc_pct > 3 else ("暴跌" if btc_pct < -3 else "震盪")
    fear_label = "極度恐懼" if fng < 25 else ("貪婪" if fng > 75 else "中性")
    conflict_short = core_conflict[:20] if core_conflict else "多空分歧加劇"
    title = f" BTC {direction} {fng}恐慌？{conflict_short}｜幣看趨勢"
    title = title.strip()
    if len(title) > 88:
        title = title[:85] + "｜幣看趨勢"
    return title


def _generate_yt_highlights(mdata, editorial_brief, slide_titles, is_test):
    """生成 3 個核心懸念看點，作為說明欄前 3 行。"""
    highlights = []
    core_conflict = editorial_brief.get("core_conflict", "")
    if core_conflict:
        highlights.append(f"核心矛盾：{core_conflict[:60]}")
    cur_p = mdata.get("cur_p", 0)
    btc_pct = mdata.get("btc_pct", 0)
    highlights.append(f"BTC {format_usd(cur_p)}（24H {btc_pct:+.2f}%），{'撐住' if btc_pct >= 0 else '失守'}關鍵均線")
    if slide_titles and len(slide_titles) > 3:
        highlights.append(f"本集重點：{slide_titles[3][:50]}")
    elif len(highlights) < 3:
        highlights.append("未來 24 小時關鍵條件推演與風險提示")
    return highlights[:3]


def _generate_yt_chapters(full_script, slide_titles, intro_duration=15.0):
    """依逐字稿錨點與 slide_titles 生成 YouTube 章節時間軸。"""
    import re as _re
    chapters = []
    anchor_pattern = _re.compile(r"\[SLIDE_(\d+)\]")
    lines = full_script.splitlines()

    # 計算每句字數比例，估算每個 slide 的起始秒數
    total_chars = 0
    slide_chars = {}
    current_slide = 1
    for line in lines:
        text = anchor_pattern.sub("", line).strip()
        if not text:
            continue
        m = anchor_pattern.search(line)
        if m:
            current_slide = int(m.group(1))
        slide_chars[current_slide] = slide_chars.get(current_slide, 0) + len(text)
        total_chars += len(text)

    if total_chars == 0:
        return chapters

    # 以 4.5 字/秒估算語速
    chars_per_sec = 4.5
    elapsed = intro_duration
    for slide_num in sorted(slide_chars.keys()):
        duration = slide_chars[slide_num] / chars_per_sec
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        title = slide_titles[slide_num - 1] if slide_num - 1 < len(slide_titles) else f"段落 {slide_num}"
        chapters.append(f"{mins:02d}:{secs:02d} {title}")
        elapsed += duration

    return chapters


def generate_script(mdata, dca_data, config):
    cached = load_cached_script()
    if cached is not None:
        if not config["is_test"]:
            import daily_learner
            daily_learner.remember_catchphrases(cached.get("catchphrases"), is_test=False)
        return cached

    is_test = config["is_test"]
    show_dca = config["show_dca"]
    today_str = config["today_str"]
    entries = mdata["entries"]
    cur_p = mdata["cur_p"]
    btc_pct = mdata["btc_pct"]
    ma100d = mdata["ma100d"]
    ma200d = mdata["ma200d"]
    ma100w = mdata["ma100w"]
    ma200w = mdata["ma200w"]
    spot_prices = mdata["spot_prices"]
    cdata = mdata["cdata"]

    # 組裝新聞字串（優先引用抓到的完整正文 1,200～2,500 字細節）
    news_items = []
    for index, item in enumerate(entries):
        published_at, entry = (
            item if isinstance(item, tuple) and len(item) == 2
            else (datetime.now(), item)
        )
        title = clean_slide_title(clean_txt(entry.get("title")))
        pub_time = (
            published_at.strftime("%Y-%m-%d %H:%M")
            if hasattr(published_at, "strftime")
            else str(published_at)
        )
        full_text = entry.get("full_text")
        if full_text:
            content_desc = (
                f"【完整網頁新聞正文（真實細節、人物引述、機構數據與脈絡）】：\n{full_text}"
            )
        else:
            summary = clean_txt(entry.get("summary", "")).replace(chr(10), " ")[:300]
            content_desc = f"【新聞摘要】：\n{summary}"
        news_items.append(f"{index + 1}. 【{title}】\n發布時間：{pub_time} CST\n{content_desc}")
    news_list = "\n\n".join(news_items)

    dca_report = dca_data.get("dca_report", "")
    dca_slide_body = dca_data.get("dca_slide_body", "")

    metric_lines = [
        f"- BTC 即時報價：{format_usd(cur_p)} USD (24H: {'+' if btc_pct >= 0 else ''}{btc_pct:.2f}%)",
        f"- 短中期均線：100日 {format_usd(ma100d)} | 200日 {format_usd(ma200d)}",
        f"- 長期週線：100週 {format_usd(ma100w)} | 200週 {format_usd(ma200w)}",
    ]
    for label, value in (
        ("波段結構", mdata.get("btc_technical_report")),
        ("ETH 波段結構", mdata.get("eth_technical_report")),
        ("BTC RSI 歷史", mdata.get("btc_oversold_report")),
        ("全網穩定幣總市值變化", mdata.get("stablecoin_report")),
        ("大戶與全體帳戶持倉代理", mdata.get("trader_position_report")),
        ("近30日現貨成交量", mdata.get("spot_volume_report")),
        ("鏈上轉帳活躍度代理", mdata.get("onchain_activity_report")),
        ("恐慌、回撤與成交量交叉判讀", mdata.get("chip_sentiment_report")),
        ("市場恐慌與貪婪指數", mdata.get("fear_report")),
        ("MVRV", (mdata.get("mvrv") or {}).get("report")),
    ):
        bullet = _metric_bullet(label, value)
        if bullet:
            metric_lines.append(bullet)
    reference_block = _custom_reference_block(mdata.get("custom_references"))
    full_report = (
        f"【今日日期】：{today_str}\n【數據抓取時間】：{mdata['data_fetched_at']}\n"
        "【比特幣核心技術指標】\n"
        + "\n".join(metric_lines)
        + f"\n\n{dca_report}\n\n【主流幣即時報價與RSI掃描】\n{mdata['coins_summary']}\n\n"
        f"【近36小時通過時效驗證的焦點新聞（含前4則完整正文）】\n{news_list}"
    )
    if reference_block:
        full_report += "\n\n" + reference_block
    full_report = sanitize_text(full_report)

    dca_performance_state = ""
    dca_prompt_guidance = ""
    dca_slide_caption = ""
    if show_dca and dca_data:
        usd_view = dca_data.get("usd_view") or {}
        pnl_usd = float(usd_view.get("current_value", 0) - usd_view.get("total_invested", 0))
        dca_performance_state, dca_prompt_guidance, dca_slide_caption = (
            dca_performance_guidance(pnl_usd)
        )

    show_format = choose_show_format()
    visual_plan = plan_daily_slides(mdata, show_dca, show_format, dca_slide_body)
    slide_count = len(visual_plan)
    thermo_count = sum(1 for slide in visual_plan if slide["role"] == "thermometer")

    # 三幕投影片分界
    s1 = 5 if slide_count >= 14 else max(1, slide_count // 3)
    s2 = 10 if slide_count >= 14 else max(s1 + 1, 2 * slide_count // 3)
    p1_anchors = "；".join(
        f"[SLIDE_{index}]{slide['title']}" for index, slide in enumerate(visual_plan[:s1], 1)
    )
    p2_anchors = "；".join(
        f"[SLIDE_{index}]{slide['title']}" for index, slide in enumerate(visual_plan[s1:s2], s1 + 1)
    )
    p3_anchors = "；".join(
        f"[SLIDE_{index}]{slide['title']}" for index, slide in enumerate(visual_plan[s2:], s2 + 1)
    )

    if show_dca:
        dca_prompt_section = (
            "【DCA 定投帳本播報要求】：由比特汪以第一人稱播報「比特汪的 BTC 定期定額模擬帳本」，"
            "清楚說明自2025年10月25日起，按節目設定每月25日模擬投入一筆固定美元部位，"
            "並非真實成交。只用一句話交代這項模擬規則，再報最近一次買進日期、BTC 美元價、"
            "該期美元投入、買進數量、累計美元投入、累計BTC（8位小數）、美元平均成本、"
            "目前美元總市值、未實現賺賠金額及百分比；不可遺漏或改寫帳本數字，禁止出現台幣。\n"
            f"目前績效狀態：{dca_performance_state}。\n"
            f"依狀態調整雙人互動：{dca_prompt_guidance}\n"
            "合規要求：強調這是長期分批建倉的模擬紀律示範，不構成投資建議；"
            "不鼓勵借貸、高槓桿或盲目追高殺低。不得保證獲利、回本或週期必然翻正。\n"
            f"帳本數字：{dca_report}\n"
        )
    else:
        dca_prompt_section = ""

    angle_title, vocab_line, recent_angles, angle_id = load_show_angle()
    catchphrase_line, catchphrases = sample_host_catchphrases()
    slide_slots = "；".join(
        f"{index}.{slide['title']}" for index, slide in enumerate(visual_plan, 1)
    )
    banned_terms = "、".join(EXCLUDED_PLATFORMS)
    mvrv_line = present_metric((mdata.get("mvrv") or {}).get("report"))
    mvrv_prompt = (
        f"【MVRV 指標】：{mvrv_line}。必須說出數值與歷史區間判讀，並聲明它不是買賣指令。\n"
        if mvrv_line else ""
    )

    common_rules = f"""【寫作鐵律】：
1. 【嚴禁報表流水帳】：嚴禁連續兩句以上只報價格、漲跌幅或指標數字。每一個關鍵數字後面都必須接白話生活比喻、反直覺質問、或對投資人行為心理的深度剖析。
2. 【嚴禁萬用廢話】：嚴禁出現「謹慎觀望」「注意風險」「投資需謹慎」「請自行評估」「市場瞬息萬變」等任何形式的套話廢話！必須用可驗證的條件、支撐壓力與籌碼結構說話。
3. 【嚴禁露餡台詞】：逐字稿禁止出現「抓不到資料」「無法取得數據」「API 異常」「資料缺失」「連線失敗」「所以不預測」這類暴露系統缺漏的句子。資料區沒有的指標直接當作不存在，禁止猜測或補值。
4. 【嚴禁分段與時間問候語】：禁止早安、午安、晚安、大家好等問候；禁止「休息一下」「下一段」「前半段」「後半段」「先到這裡」「接著看」等轉場字眼；禁止提及任何媒體或網站名稱。
5. 【金額規範】：所有價格、市值、成交量一律使用美元（USD），嚴禁台幣（NT$、TWD）。美元金額一律四捨五入到整數並加千分位（例如 $95,421、-$836），禁止小數美分。百分比、MVRV、RSI 維持原有小數。
6. 【情緒標記與格式】：每句發言以「男：」或「女：」開頭，角色名後可接標籤 [excited]、[serious]、[analytical] 或 [debate]，不可念出標籤。只輸出對話純文字，不要 Markdown，不要包裹 JSON。
7. 【主播角色語氣】：
   女聲「以太喵」（冷靜精明、略帶傲嬌吐槽，自然穿插「他喵的」「喵頭不對」等貓語梗，熟悉技術與數據，負責精準拆解價格結構、均線乖離與 MVRV，幫比特汪踩煞車）。
   男聲「比特汪」（熱情直率、容易激動，自然使用「汪嗚」「本汪」「別汪想」等狗語梗，重視 BTC 長期週期與紀律定投）。男主播唯一名稱就是「比特汪」，嚴禁寫成「位元汪」「位元犬」或任何把 Bitcoin 譯成「位元」的寫法。
   兩人唇槍舌劍、自然吐槽，口播禁止使用 HODL 縮寫，一律自然說「長期持有」或「長期持有者」。
   避開禁忌詞：{banned_terms}。
8. 【數字顯示】：所有年份、價格、百分比、小數、比率與數量一律使用阿拉伯數字，例如 0.587、27.53%、2026 年；禁止寫成「零點五八七」等中文數字形式。
9. 【避免重複】：同一觀點只完整解釋一次；後續提及必須增加新數據、因果或不同角度，禁止換句話重述。避免每一幕都重複相同轉折句型。
10. 【口頭禪節制】：每位主持人全集最多使用 1 次招牌口頭禪，不得重複；不要為了塞入角色梗犧牲資訊密度。"""

    target_act_words = (
        "約150至200字繁中（快速測試模式，各圖卡保留一句重點對話）"
        if is_test
        else "嚴格要求 1,800 至 2,200 字繁中深度長文（兩位主播逐字深入攻防對話，嚴格杜絕簡化與偷工減料）"
    )

    # ── 步驟 A：總編輯深度定調 (Editorial Brief) ───────────────────────────
    print("[腳本步驟 A] 總編輯深度定調 — 解構市場矛盾、背後資金意圖與三幕大綱...")
    brief_prompt = f"""你是頂級加密貨幣財經播客【幣看趨勢】總編輯。
請針對今日完整市場數據與 4 篇焦點新聞完整正文，進行深度解構與總編輯定調，生成一份深度的《今日總編輯核心論點大綱》。

【今日切入視角】{angle_title}。近 7 日已用視角（今日必須避開）：{recent_angles}。
【今日節目模式】{show_format.get("title")}（{show_format.get("id")}）。{show_format.get("instruction")}
【可自然帶入的術語】{vocab_line}
【雙主播口頭禪】{catchphrase_line}

{full_report}

請產出一份深度定調 JSON，要求：
1. 找出今日市場最反常的矛盾或背離（例如價格漲但量能萎縮、散戶恐慌但巨鯨未拋、股幣背離等）。
2. 拆解背後聰明錢、機構資金或主力籌碼的真實意圖。
3. 歸納多方最強硬的論點，以及空方最具殺傷力的論點。
4. 為整集節目擬定黃金前 15 秒開場懸念鉤子（含具體矛盾數字）。
5. 規劃三幕式深度大綱：
   - 第一幕大綱：全局矛盾破題與核心新聞正文深度細節拆解。
   - 第二幕大綱：多空硬核攻防交鋒、均線/MVRV/籌碼拆穿表面雜訊。
   - 第三幕大綱：情境A與情境B實戰推演、未來24小時觀察與紀律定投。
6. 為全部 {slide_count} 頁簡報分別擬定新聞級標題（slide_titles，順序嚴格對應：{slide_slots}），禁止任何每天都能套用的公版標題。
7. slide_news：3 筆焦點新聞圖卡摘要（含 source_news_number, slide_title, positive, risk）。
8. slide_week：3 句未來 24 小時情境推演條件（情境A、情境B、防守底線）。
9. 【極簡圖解文案】所有會出現在投影片上的文字（slide_titles、slide_news 的 positive/risk、slide_week）都必須極簡：每句禁止超過 15 字，且濃縮為「1 個視覺符號 ＋ 4～8 字短詞 ＋ 1 個核心數字或對比箭頭（如 15 分鐘 ➔ 6 秒）」，營造高留白呼吸感。口播逐字稿不受此限，只有投影片文字要短。

只輸出合法 JSON 物件，格式：
{{
  "title": "32字內新聞級標題",
  "editorial_lead": "一句話核心主軸",
  "core_conflict": "市場最反常矛盾背離",
  "smart_money_intent": "資金真實意圖",
  "bull_case": "多方最強硬論點",
  "bear_case": "空方最強硬論點",
  "opening_hook": "黃金前15秒懸念鉤子",
  "act1_outline": "第一幕綱要",
  "act2_outline": "第二幕綱要",
  "act3_outline": "第三幕綱要",
  "slide_titles": ["標題1", "標題{slide_count}"],
  "slide_news": [{{"source_news_number": 1, "slide_title": "標題", "positive": "利多", "risk": "風險"}}],
  "slide_week": ["情境A", "情境B", "防守線"]
}}"""

    brief_raw = call_gemini(brief_prompt, max_output_tokens=8192, timeout=120, json_mode=True)
    editorial_brief = _parse_editorial_brief(brief_raw, mdata, slide_count)
    print(f"[總編輯定調完成] 標題：{editorial_brief['title']} ｜ 核心主軸：{editorial_brief['editorial_lead']}")
    print(f"[核心矛盾] {editorial_brief['core_conflict']}")

    # ── 步驟 B：三幕式分段深度擴寫 (確保 5,500～6,500 字) ───────────────────
    # 第一幕：全局矛盾與盤面真相
    print(f"[腳本步驟 B-1] 正在生成【第一幕：全局矛盾與盤面真相】（目標長度：{target_act_words}）...")
    act1_prompt = f"""你是財經播客【幣看趨勢】主編。現在請為整集節目生成【第一幕：全局矛盾與盤面真相】的深度口播逐字稿。
長度配額：{target_act_words}。

【今日切入視角】{angle_title}
【今日核心主軸】{editorial_brief.get('editorial_lead')}
【總編輯核心矛盾】{editorial_brief.get('core_conflict')}
【第一幕總編輯綱要】{editorial_brief.get('act1_outline')}
【黃金前15秒破題鉤子】{editorial_brief.get('opening_hook')}
【雙主播口頭禪】{catchphrase_line}
【可自然帶入術語】{vocab_line}
{mvrv_prompt}
【第一幕任務與內容指南】：
1. 【黃金前 15 秒震撼破題】：第一句必須直接拋出當日最震撼的數據衝突或懸疑問句（參考上方鉤子），用上具體矛盾數字。前兩句禁止早安、自我介紹、節目報幕。免責宣告（「本節目僅供市場數據整理，不構成投資建議」）在鉤子說完後第 20~30 秒自然帶過，不可刪除。
2. 【深度深挖新聞正文】：深入引用焦點新聞正文（新聞 1-4）中的具體細節、人物引述、機構數據與深層背景脈絡，嚴禁浮於表面，嚴禁講空話。
3. 【市場溫度計速掃】：用約 3 分鐘做完情緒溫度計與主流幣價格快篩，安靜的幣種一句話帶過。
{dca_prompt_section}
4. 【隱藏投影片錨點】：本幕必須依序放入前半段 {s1} 個投影片錨點。每個對應段落的第一句發言開頭（角色名與情緒標籤之後）必須立刻放入對應標記，每個標記恰好出現一次，嚴格依序，不可念出標記：
   {p1_anchors}
5. 第一幕結尾銜接後續盤面多空博弈，不說再見、不使用分段結尾。

{common_rules}

【數據與新聞資料庫】：
{full_report}
"""
    act1_text = call_gemini(act1_prompt, max_output_tokens=16384, timeout=300)
    act1_text = _expand_act_if_short(act1_text, 1, "全局矛盾與盤面真相", editorial_brief.get("core_conflict", ""), is_test)
    print(f"第一幕生成完畢，字數：{_spoken_chars(act1_text)} 字。")

    act1_tail = act1_text.strip()[-500:] if len(act1_text.strip()) > 500 else act1_text.strip()

    # 第二幕：多空硬核交鋒與籌碼拆解
    print(f"[腳本步驟 B-2] 正在生成【第二幕：多空硬核交鋒與籌碼拆解】（目標長度：{target_act_words}）...")
    act2_prompt = f"""你是財經播客【幣看趨勢】主編。現在請延續第一幕的對話，生成【第二幕：多空硬核交鋒與籌碼拆解】的深度口播逐字稿。
長度配額：{target_act_words}。

【今日切入視角】{angle_title}
【今日核心主軸】{editorial_brief.get('editorial_lead')}
【總編輯第二幕綱要】{editorial_brief.get('act2_outline')}
【背後資金意圖】{editorial_brief.get('smart_money_intent')}
【多方最強硬點】{editorial_brief.get('bull_case')}
【空方最強硬點】{editorial_brief.get('bear_case')}
【口頭禪限制】不要刻意加入固定口頭禪；第一幕若已使用任何口頭禪，第二幕不得重複。
【可自然帶入術語】{vocab_line}
{mvrv_prompt}
【承接第一幕結尾】：
上一幕結尾對話為：
「{act1_tail}」
請直接順著這段對話自然展開，絕不重複第一幕已報過的報價或溫度計；若需回顧概念，必須新增一項不同證據或因果推論，絕不打招呼或說轉場廢話！

【第二幕任務與內容指南】：
1. 【多空硬核交鋒與針鋒相對】：以太喵與比特汪針對核心矛盾展開真實攻防硬核辯論！以太喵用客觀數據給比特汪踩煞車，比特汪從宏觀與定投邏輯反擊。
2. 【深度指標拆解】：引用 100日/200日均線、100週/200週均線、MVRV 歷史週期對照、交易所持倉多空比與資金費率、穩定幣市值增減與鏈上轉帳活躍度。
3. 【拆穿表面雜訊】：分辨「假突破與真吸籌」、「縮量陰跌與恐慌換手」，點破市場表象之下的主力底牌。
4. 【隱藏投影片錨點】：本幕必須依序放入中間段 {s2 - s1} 個投影片錨點。每個對應段落的第一句發言開頭立刻放入對應標記，每個標記恰好出現一次，嚴格依序：
   {p2_anchors}

{common_rules}

【數據與新聞資料庫】：
{full_report}
"""
    act2_text = call_gemini(act2_prompt, max_output_tokens=16384, timeout=300)
    act2_text = _expand_act_if_short(act2_text, 2, "多空硬核交鋒與籌碼拆解", editorial_brief.get("smart_money_intent", ""), is_test)
    print(f"第二幕生成完畢，字數：{_spoken_chars(act2_text)} 字。")

    act2_tail = act2_text.strip()[-500:] if len(act2_text.strip()) > 500 else act2_text.strip()

    # 第三幕：實戰推演與未來 24 小時驗證劇本
    print(f"[腳本步驟 B-3] 正在生成【第三幕：實戰推演與未來 24 小時驗證劇本】（目標長度：{target_act_words}）...")
    conditions_guide = editorial_brief.get("slide_week") or []
    act3_prompt = f"""你是財經播客【幣看趨勢】主編。現在請延續第二幕的對話，生成【第三幕：實戰推演與未來 24 小時驗證劇本】的深度口播逐字稿，為本集節目完美收尾。
長度配額：{target_act_words}。

【今日切入視角】{angle_title}
【今日核心主軸】{editorial_brief.get('editorial_lead')}
【總編輯第三幕綱要】{editorial_brief.get('act3_outline')}
【未來24小時情境條件】
1. {conditions_guide[0] if len(conditions_guide) > 0 else '情境A'}
2. {conditions_guide[1] if len(conditions_guide) > 1 else '情境B'}
3. {conditions_guide[2] if len(conditions_guide) > 2 else '關鍵防守線'}
【口頭禪限制】不要刻意加入固定口頭禪；前兩幕若已使用任何口頭禪，第三幕不得重複。
【可自然帶入術語】{vocab_line}

【承接第二幕結尾】：
上一幕結尾對話為：
「{act2_tail}」
請直接順著這段對話自然推進，不重複已討論過的均線與指標定義；只補充新的情境條件、驗證訊號與風險界線！

【第三幕任務與內容指南】：
1. 【未來 24 小時實戰推演】：給出清晰嚴密的條件推演：「如果發生情境 A ➔ 代表什麼、後續如何確認；如果跌破關鍵支撐 B ➔ 代表什麼、防守線在哪裡」。
2. 【投資心法與紀律定投】：比特汪總結抗波動心理學，強調不猜最低點、不盲目追高殺低的紀律定投與倉位管理，客觀說明超賣不保證立刻反彈。
3. 【主播幽默收尾】：兩位主播自然道別，使用本集獨有的具體觀察收束；不要刻意加入或重複口頭禪，絕不講空話。
4. 【隱藏投影片錨點】：本幕必須依序放入後續 {slide_count - s2} 個投影片錨點。每個對應段落的第一句發言開頭立刻放入對應標記，每個標記恰好出現一次，嚴格依序：
   {p3_anchors}

{common_rules}

【數據與新聞資料庫】：
{full_report}
"""
    act3_text = call_gemini(act3_prompt, max_output_tokens=16384, timeout=300)
    act3_text = _expand_act_if_short(act3_text, 3, "實戰推演與未來 24 小時驗證劇本", editorial_brief.get("act3_outline", ""), is_test)
    print(f"第三幕生成完畢，字數：{_spoken_chars(act3_text)} 字。")

    # ── 後處理與整合 ──────────────────────────────────────────────────────
    act1_raw = silence_gap_leaks(normalize_long_term_terms(act1_text))
    act2_raw = silence_gap_leaks(normalize_long_term_terms(act2_text))
    act3_raw = silence_gap_leaks(normalize_long_term_terms(act3_text))

    full_script = f"{act1_raw}\n{act2_raw}\n{act3_raw}"
    full_script = _ensure_slide_anchors(full_script, slide_count)

    total_chars = _spoken_chars(full_script)
    print(f"=== 三幕式深度逐字稿生成完成！總字數：{total_chars} 繁中字 ===")
    print(f"（第一幕：{_spoken_chars(act1_raw)} 字，第二幕：{_spoken_chars(act2_raw)} 字，第三幕：{_spoken_chars(act3_raw)} 字）")
    if not is_test:
        _log_length_gate(total_chars, "每日三幕深度逐字稿")

    slide_news_details = _pad_slide_news(editorial_brief.get("slide_news"), mdata)
    for item in slide_news_details:
        item["slide_title"] = silence_gap_leaks(item.get("slide_title"))
        item["positive"] = silence_gap_leaks(item.get("positive"))
        item["risk"] = silence_gap_leaks(item.get("risk"))
    slide_news = [
        f"利多：{item['positive']}；風險：{item['risk']}"
        for item in slide_news_details
    ]

    ai_title = silence_gap_leaks(sanitize_text(editorial_brief.get("title") or f"BTC {format_usd(cur_p)}｜主流幣均線攻防與深度籌碼拆解"))
    if not ai_title:
        ai_title = f"BTC {format_usd(cur_p)}｜主流幣均線攻防與深度籌碼拆解"
    video_title = f"【幣看趨勢】{ai_title} ({today_str})"

    slide_24h_conditions = _pad_slide_week(editorial_brief.get("slide_week"), mdata)
    cleaned_week = []
    for item in slide_24h_conditions:
        cleaned = silence_gap_leaks(item)
        if cleaned:
            cleaned_week.append(cleaned)
    slide_24h_conditions = cleaned_week or list(slide_24h_conditions)

    editorial_lead = editorial_brief.get("editorial_lead") or suggest_editorial_lead(mdata)
    if any(banned in editorial_lead for banned in BANNED_SLIDE_TITLES):
        editorial_lead = suggest_editorial_lead(mdata)

    slide_titles = align_slide_titles(
        editorial_brief.get("slide_titles"),
        [slide["title"] for slide in visual_plan],
    )
    for slide, title in zip(visual_plan, slide_titles):
        slide["title"] = title
    fill_story_slide_bodies(visual_plan, editorial_lead, slide_news_details, slide_24h_conditions)
    opening_hook = editorial_brief.get("opening_hook") or _opening_hook(act1_raw)

    result = {
        "part1_raw": act1_raw,
        "part2_raw": f"{act2_raw}\n{act3_raw}",
        "part3_raw": act3_raw,
        "full_script": full_script,
        "ai_title": ai_title,
        "video_title": video_title,
        "yt_title": _generate_yt_title(mdata, editorial_brief, full_script, is_test),
        "yt_highlights": _generate_yt_highlights(mdata, editorial_brief, slide_titles, is_test),
        "yt_chapters": _generate_yt_chapters(full_script, slide_titles),
        "slide_news_details": slide_news_details,
        "slide_news": slide_news,
        "slide_24h_conditions": slide_24h_conditions,
        "dca_slide_caption": dca_slide_caption,
        "dca_slide_body": dca_slide_body,
        "angle_id": angle_id,
        "angle_title": angle_title,
        "editorial_lead": editorial_lead,
        "slide_titles": slide_titles,
        "visual_slides": visual_plan,
        "slide_count": len(visual_plan),
        "format_id": show_format.get("id") or "",
        "format_title": show_format.get("title") or "",
        "opening_hook": opening_hook,
        "catchphrases": catchphrases,
    }

    # 輸出完整逐字稿到 transcript.txt 供 Releases 與預覽
    try:
        with open("transcript.txt", "w", encoding="utf-8") as f:
            f.write(f"【節目標題】：{video_title}\n\n")
            f.write(f"【總編輯核心主軸】：{editorial_lead}\n")
            f.write(f"【盤面核心矛盾】：{editorial_brief.get('core_conflict', '')}\n")
            f.write(f"【聰明錢意圖】：{editorial_brief.get('smart_money_intent', '')}\n\n")
            f.write("【逐字稿全文】：\n")
            f.write(full_script)
            f.write("\n")
    except Exception as e:
        print(f"寫入 transcript.txt 略過：{e}")

    save_cached_script(result)
    if not is_test:
        import daily_learner
        daily_learner.remember_catchphrases(catchphrases, is_test=False)
    return result


TUTORIAL_CACHE_PATH = "cache_last_tutorial_script.json"
TUTORIAL_SECTION_TITLES = (
    "好奇提問與痛點鉤子",
    "生活化白話比喻",
    "鏈上原理與籌碼結構拆解",
    "歷史週期換手實證",
    "投資人實戰啟示與三大誤區",
)


def _json_object(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("tutorial response has no JSON object")
    return json.loads(text[start:end + 1])


def _dialogue_lines(narration):
    lines = []
    for raw in str(narration or "").splitlines():
        match = re.match(r"^(男|女)[：:]\s*(.+)", raw.strip())
        if match:
            lines.append({"role": match.group(1), "text": normalize_long_term_terms(match.group(2).strip())})
    return lines


def _tutorial_char_count(sections):
    return sum(len(re.sub(r"\s+", "", section.get("narration", ""))) for section in sections)


def _structured_tutorial_cards(payload, sections, lesson):
    """優先用模型給的完整圖卡。欄位不完整才退回口播分組，且不截半句。"""
    raw_cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
    cards = []
    for item in raw_cards:
        if not isinstance(item, dict):
            continue
        lines = []
        for line in item.get("lines") or []:
            if not isinstance(line, dict):
                continue
            text = normalize_long_term_terms(str(line.get("text") or "").strip())
            if not text:
                continue
            lines.append({"role": "女" if line.get("role") in ("女", "F") else "男", "text": text})
        title = fit_complete_phrase(item.get("card_title"), 8, 16, "")
        hero = fit_complete_phrase(item.get("hero_stat"), 4, 16, "")
        bullets = []
        for bullet in item.get("bullet_points") or []:
            phrase = fit_complete_phrase(bullet, 8, 22, "")
            if phrase and phrase not in bullets:
                bullets.append(phrase)
        if not title or not hero or len(bullets) < 2 or not lines:
            continue
        cards.append({
            "layout": ("impact", "compare", "steps", "chart")[len(cards) % 4],
            "title": title,
            "headline": hero,
            "myth": strip_bracket_tags(str(item.get("myth") or "不要把示意區間當成買點")),
            "truth": hero,
            "steps": bullets[:3],
            "lines": lines,
        })
        if len(cards) == 13:
            cards[-1]["layout"] = "chart"
    if len(cards) >= 8:
        return cards[:30]
    return build_tutorial_cards(sections, lesson)


def _normalize_tutorial(payload, lesson):
    raw_sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    if len(raw_sections) != 5:
        print(f"教學 sections 有 {len(raw_sections)} 段，補齊到 5 段後放行。")
    sections = []
    for index in range(5):
        item = raw_sections[index] if index < len(raw_sections) and isinstance(raw_sections[index], dict) else {}
        title = str(item.get("title") or TUTORIAL_SECTION_TITLES[index]).strip()
        narration = silence_gap_leaks(normalize_long_term_terms(str(item.get("narration") or "")))
        bullets = [
            silence_gap_leaks(str(bullet).strip())
            for bullet in (item.get("bullets") or [])
            if silence_gap_leaks(str(bullet).strip())
        ]
        lines = _dialogue_lines(narration)
        if not lines:
            narration = (
                f"男：[analytical]這一點先看{title}。\n"
                "女：[serious]這是結構說明，不是買賣指令。"
            )
            lines = _dialogue_lines(narration)
        sections.append({
            "title": title,
            "bullets": bullets[:4] or [TUTORIAL_SECTION_TITLES[index]],
            "narration": narration,
            "lines": lines,
        })
    short_lines = _dialogue_lines(payload.get("short_script"))
    short_script = str(payload.get("short_script") or "")
    if len(short_lines) < 2:
        short_script = (
            "男：這集把原理講完了，完整版在頻道。\n"
            "女：公開帳本只是模擬定期定額，不是真實成交。"
        )
        short_lines = _dialogue_lines(short_script)
    return {
        "ep_id": lesson.get("ep_id") or "EP",
        "series": lesson.get("series") or "",
        "lesson_title": lesson.get("title") or "",
        "title": sanitize_text(payload.get("title") or lesson.get("title") or "系統教學"),
        "sections": sections,
        "short_script": short_script,
        "short_lines": short_lines,
        "char_count": _tutorial_char_count(sections),
        "cards": _structured_tutorial_cards(payload, sections, lesson),
        "catchphrases": list(payload.get("catchphrases") or []),
    }


def _tutorial_long_enough(tutorial, is_test):
    if is_test:
        return tutorial["char_count"] >= 80
    return 5800 <= tutorial["char_count"] <= 6500


def _load_tutorial_cache():
    if not reuse_script_enabled() or not os.path.exists(TUTORIAL_CACHE_PATH):
        return None
    try:
        with open(TUTORIAL_CACHE_PATH, encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(cached, dict) or len(cached.get("sections") or []) != 5:
        return None
    print("[REUSE_SCRIPT] 使用 cache_last_tutorial_script.json，教學稿不呼叫 Gemini。")
    return cached


def generate_tutorial_script(lesson, reference_note, config):
    """五段式常青教學稿。快取命中時不呼叫 Gemini。"""
    cached = _load_tutorial_cache()
    if cached is not None:
        if not bool(config.get("is_test")):
            import daily_learner
            daily_learner.remember_catchphrases(cached.get("catchphrases"), is_test=False)
        return cached
    is_test = bool(config.get("is_test"))
    concepts = "；".join(lesson.get("core_concepts") or [])
    myths = "；".join(lesson.get("common_misconceptions") or [])
    length_rule = (
        "測試模式：五段各兩句以內，總長約 200 字。"
        if is_test else
        "正式版五段口播合計 5,800 到 6,500 字，約 16 到 20 分鐘。Shorts 口播 45 到 55 秒，約 180 到 230 字。"
    )
    catchphrase_line, catchphrases = sample_host_catchphrases()
    prompt = f"""你是【幣看深潛｜系統教學】主編。請為比特汪與以太喵寫一集常青教學，不要寫成每日新聞。
【雙主播口頭禪】{catchphrase_line}
課號：{lesson.get("ep_id")}
系列：{lesson.get("series")}
題目：{lesson.get("title")}
核心知識：{concepts}
常見誤區：{myths}
資料邊界：{reference_note}
{length_rule}
五段順序必須是：1 好奇提問與痛點鉤子；2 生活化白話比喻；3 鏈上或指標原理拆解；4 歷史週期實證；5 實戰啟示與三大誤區。
每段寫成很多短對白，正式版每段 16 到 22 句。另外獨立輸出 25 到 30 張圖卡，每張 2 到 4 句口播。
每一張圖卡必須自帶完整欄位，禁止拿口播半句當標題：card_title 是 8 到 16 字的完整知識標題；hero_stat 是本頁核心數據或 4 到 10 字金句，例如「170 萬顆遠古沉睡幣」「UTXO 帳本機制」；bullet_points 是 2 到 3 條、每條 4 到 15 字的極簡短詞（1 個視覺符號 ＋ 4～8 字短詞 ＋ 1 個核心數字或對比箭頭，如 15 分鐘 ➔ 6 秒），禁止超過 15 字。三個欄位都要是完整語意，不能停在「也就是說」這類半截句。
第 1 段第一句就要丟出這集最反直覺的問題，前兩句不要客套，也不要先講免責。一句「本集是公開資訊整理，不構成投資建議」放在鉤子之後、開始拆原理之前。
【知識密度】禁止空泛重複。每一段都要同時有四種內容：具體技術機制，例如 UTXO 的輸入與輸出、公私鑰如何簽名；真實歷史里程碑與年代，例如 2009 年創世區塊、2010 年一萬枚比特幣買披薩、2011 年首度突破 1 美元、歷次減半後的籌碼換手；深度對比，把遺失私鑰的遠古化石幣和現代機構的高成本活躍籌碼分開，說明它們怎麼改變真實流通供給；雙人交鋒，比特汪追問實戰痛點，以太喵用硬核數據回答，並自然用上本次口頭禪。
鐵律：繁體中文。角色只有比特汪與以太喵，男主播唯一名稱就是「比特汪」，嚴禁寫成「位元汪」「位元犬」或把 Bitcoin 譯成「位元」；台詞以「男：」或「女：」開頭。美元金額一律整數加千分位，例如 $1、$35,000、$95,000。禁止小數金額、台幣、NT$、TWD。禁止 HODL、媒體名、交易所名，以及把 AI 當主題或字眼。可在角色名後加 [excited]、[serious]、[analytical]、[debate]，標籤只供配音，不要出現在 card_title、hero_stat、bullet_points。不承諾獲利，不鼓勵槓桿或借貸。禁止編造今日即時價格；價格帶若出現，必須說是早期週期區間示意或結構示意，不是即時普查。禁止說抓不到資料、無法取得數據、資料缺失或連線失敗。
只輸出一個 JSON，不要 Markdown。格式：
{{"title":"32字內標題","sections":[{{"title":"段名","bullets":["重點1","重點2","重點3"],"narration":"男：[analytical]...\\n女：[serious]..."}}],"cards":[{{"card_title":"完整標題","hero_stat":"核心金句","bullet_points":["完整要點一","完整要點二"],"lines":[{{"role":"男","text":"不含半截句的口播"}}]}}],"short_script":"男：...\\n女：..."}}
sections 必須恰好 5 段，cards 必須 25 到 30 張。short_script 要引導觀眾去看完整教學，並提到公開帳本網站可追蹤模擬定期定額，但不要把帳本說成真實成交。"""
    raw = call_gemini(
        prompt, max_output_tokens=65536, timeout=300, json_mode=True, models=PRODUCTION_MODELS,
    )
    try:
        tutorial = _normalize_tutorial(_json_object(raw), lesson)
    except (ValueError, json.JSONDecodeError) as error:
        print(f"教學稿需要補一次合法 JSON：{error}")
        raw = call_gemini(
            prompt + "\n上次輸出不是合法 JSON。這次只回傳合法 JSON，sections 必須恰好 5 段，cards 必須 25 到 30 張。",
            max_output_tokens=65536,
            timeout=300,
            json_mode=True,
            models=PRODUCTION_MODELS,
        )
        try:
            tutorial = _normalize_tutorial(_json_object(raw), lesson)
        except (ValueError, json.JSONDecodeError) as second_error:
            print(f"教學稿仍無法解析，改用課程大綱補齊並放行：{second_error}")
            tutorial = _normalize_tutorial(
                {"title": lesson.get("title") or "系統教學", "sections": [], "short_script": ""},
                lesson,
            )
    if not is_test and tutorial["char_count"] < 4800:
        print(f"教學稿 {tutorial['char_count']} 字，低於 4800，深度擴寫一次。")
        try:
            expanded = call_gemini(
                "以下教學 JSON 偏短。請加深五段 narration 與 25 到 30 張 cards："
                "補上 UTXO 或指標機制、有年代的歷史事件、遠古籌碼與現代活躍籌碼的對比，以及雙人追問。"
                "card_title、hero_stat、bullet_points 必須是完整短語，不能是口播半句，也不能含括號標籤。"
                "維持恰好 5 段、short_script，以及整數美元。不要承諾獲利。只回傳 JSON。\n\n"
                + raw[:12000],
                max_output_tokens=65536,
                timeout=300,
                json_mode=True,
                models=PRODUCTION_MODELS,
            )
            tutorial = _normalize_tutorial(_json_object(expanded), lesson)
        except (ValueError, json.JSONDecodeError) as error:
            print(f"教學擴寫無法解析，沿用上一版並放行：{error}")
    if not is_test:
        _log_length_gate(tutorial["char_count"], "教學逐字稿")
    tutorial["catchphrases"] = catchphrases
    if not is_test:
        import daily_learner
        daily_learner.remember_catchphrases(catchphrases, is_test=False)
    with open(TUTORIAL_CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump(tutorial, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return tutorial

