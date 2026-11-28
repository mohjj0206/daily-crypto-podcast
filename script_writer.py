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
    assign_broll_query,
    assign_daily_layout,
    build_tutorial_cards,
    fill_story_slide_bodies,
    fill_slide_bodies_from_narration,
    fit_complete_phrase,
    format_usd,
    plan_daily_slides,
    sanitize_text,
    strip_bracket_tags,
    visual_style_for_format,
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
    conflicts = "；".join(data.get("recent_core_conflicts") or []) or "無"
    hooks = "；".join(data.get("recent_opening_hooks") or []) or "無"
    titles = "；".join(data.get("recent_slide_titles") or []) or "無"
    return (
        f"今日避開這些切入角度：{avoid}。"
        f"近 7 日已經用過的主軸，今天禁止再用相似視角或句型：{leads}。"
        f"近 7 日核心矛盾，今天必須換一個真正不同的市場問題：{conflicts}。"
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
    """10 種節目形式輪替，避開最近 5 集用過的模式。"""
    path = os.path.join("knowledge_base", "show_formats.json")
    with open(path, encoding="utf-8") as handle:
        formats = json.load(handle)["formats"]
    used = []
    try:
        with open("learning_insights.json", encoding="utf-8") as handle:
            used = [str(item) for item in (json.load(handle).get("recent_format_ids") or []) if str(item).strip()]
    except (OSError, ValueError, json.JSONDecodeError):
        used = []
    # 10 種節目形式，至少隔 5 集才允許回到相同敘事形式。
    # 從所有合格形式中依台灣日期輪換，避免永遠拿 JSON 第一筆當預設。
    blocked = set(used[-5:])
    candidates = [item for item in formats if item.get("id") not in blocked]
    if not candidates:
        candidates = formats
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    return candidates[today.toordinal() % len(candidates)]


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
        "script_structure_version", "part1_raw", "part2_raw", "full_script", "ai_title", "video_title",
        "slide_news_details", "slide_news", "slide_24h_conditions",
        "dca_slide_caption", "dca_slide_body",
    )
    if not isinstance(cached, dict) or cached.get("script_structure_version") != 2 or any(key not in cached for key in required):
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
    """只保留有來源的新聞卡；不為了湊固定張數虛構新聞。"""
    cleaned = []
    if isinstance(news, list):
        for item in news:
            if isinstance(item, dict) and (item.get("slide_title") or item.get("positive")):
                cleaned.append(item)
            if len(cleaned) == 3:
                break
        return cleaned

    for index, (title, summary) in enumerate(_reference_news(mdata)[:3], 1):
        cleaned.append({
            "source_news_number": index,
            "slide_title": title,
            "positive": summary,
            "risk": "",
        })
    return cleaned


def _pad_slide_week(week, mdata):
    """只保留編輯判斷認為有證據支持的情境；不固定補滿三條預測。"""
    if not isinstance(week, list):
        return []
    return [str(item).strip() for item in week if str(item).strip()][:3]


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

    title = str(payload.get("title") or "").strip() or str(payload.get("editorial_lead") or "").strip()[:32] or "加密市場焦點調查"
    editorial_lead = silence_gap_leaks(sanitize_text(payload.get("editorial_lead") or "")).strip() or "多空博弈白熱化，從結構看清表面雜訊"
    core_conflict = silence_gap_leaks(sanitize_text(payload.get("core_conflict") or "")).strip()
    smart_money_intent = silence_gap_leaks(sanitize_text(payload.get("smart_money_intent") or "")).strip()
    bull_case = silence_gap_leaks(sanitize_text(payload.get("bull_case") or "")).strip()
    bear_case = silence_gap_leaks(sanitize_text(payload.get("bear_case") or "")).strip()
    opening_hook = silence_gap_leaks(sanitize_text(payload.get("opening_hook") or "")).strip()
    narrative_flow = str(payload.get("narrative_flow") or "").strip()

    slide_news = _pad_slide_news(payload.get("slide_news"), mdata)
    slide_week = _pad_slide_week(payload.get("slide_week"), mdata)
    raw_titles = payload.get("slide_titles") if isinstance(payload.get("slide_titles"), list) else []
    slide_titles = [str(item).strip() for item in raw_titles if str(item).strip()]
    slide_plan = payload.get("slide_plan") if isinstance(payload.get("slide_plan"), list) else []

    return {
        "title": title,
        "editorial_lead": editorial_lead,
        "core_conflict": core_conflict,
        "smart_money_intent": smart_money_intent,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "opening_hook": opening_hook,
        "narrative_flow": narrative_flow,
        "slide_titles": slide_titles,
        "slide_plan": slide_plan,
        "slide_news": slide_news,
        "slide_week": slide_week,
    }


def _ensure_slide_anchors(script, slide_count):
    """只驗證逐字稿明確產生的分鏡錨點；絕不把缺失錨點平均塞進錯誤段落。"""
    anchors = [
        int(value)
        for value in re.findall(r"\[SLIDE_(\d+)\]", str(script or ""))
    ]
    expected = list(range(1, slide_count + 1))
    if anchors != expected:
        raise RuntimeError(
            "逐字稿分鏡錨點缺失、重複或順序錯誤，拒絕產生與口播不一致的簡報。"
            f" expected={expected}, actual={anchors}"
        )
    return script


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
    """依本集核心主軸與實際分鏡生成說明欄看點，不強制塞入行情或預測。"""
    highlights = []
    core_conflict = editorial_brief.get("core_conflict", "")
    editorial_lead = editorial_brief.get("editorial_lead", "")
    if core_conflict:
        highlights.append(f"核心問題：{core_conflict[:60]}")
    elif editorial_lead:
        highlights.append(f"本集主軸：{editorial_lead[:60]}")

    for title in slide_titles or []:
        if title and not any(title[:18] in item for item in highlights):
            highlights.append(f"本集拆解：{title[:50]}")
        if len(highlights) == 3:
            break

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
        ("MVRV Z-Score", (mdata.get("mvrv") or {}).get("report")),
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
    visual_style = visual_style_for_format(show_format.get("id"))
    print(f"[節目視覺模板] {show_format.get('title')}｜format={show_format.get('id')}｜style={visual_style}")
    visual_candidates = plan_daily_slides(mdata, show_dca, show_format, dca_slide_body)
    candidate_count = len(visual_candidates)
    dca_candidate_slot = next(
        (index for index, slide in enumerate(visual_candidates, 1) if slide.get("role") == "dca"),
        None,
    )

    # 候選卡只是素材池；實際張數、主題取捨與順序由本集總編輯重新決定。
    slide_slots = "；".join(
        f"{index}.{slide.get('role')}｜{slide['title']}｜圖表={slide.get('chart') or 'none'}"
        for index, slide in enumerate(visual_candidates, 1)
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
    banned_terms = "、".join(EXCLUDED_PLATFORMS)
    mvrv_line = present_metric((mdata.get("mvrv") or {}).get("report"))
    mvrv_prompt = (
        f"【MVRV Z-Score】：{mvrv_line}。這是以 CoinMetrics 歷史市值與已實現市值、歷史市值標準差計算的 Z 分數；必須完整說出 Z-Score 名稱、實際數值、資料日期與區間判讀，不得稱為 MVRV 比率，也不得用回撤代理值替代。它不是買賣指令。\n"
        if mvrv_line else ""
    )

    common_rules = f"""【寫作鐵律】：
1. 【嚴禁報表流水帳】：嚴禁連續兩句以上只報價格、漲跌幅或指標數字。每一個關鍵數字後面都必須接白話生活比喻、反直覺質問、或對投資人行為心理的深度剖析。
2. 【嚴禁萬用廢話】：嚴禁出現「謹慎觀望」「注意風險」「投資需謹慎」「請自行評估」「市場瞬息萬變」等任何形式的套話廢話！必須用可驗證的條件、支撐壓力與籌碼結構說話。
3. 【嚴禁露餡台詞】：逐字稿禁止出現「抓不到資料」「無法取得數據」「API 異常」「資料缺失」「連線失敗」「所以不預測」這類暴露系統缺漏的句子。資料區沒有的指標直接當作不存在，禁止猜測或補值。
4. 【嚴禁分段與時間問候語】：禁止早安、午安、晚安、大家好等問候；禁止「休息一下」「下一段」「前半段」「後半段」「先到這裡」「接著看」等轉場字眼；禁止提及任何媒體或網站名稱。
5. 【金額規範】：所有價格、市值、成交量一律使用美元（USD），嚴禁台幣（NT$、TWD）。幣價精度固定為 BTC、ETH、SOL、ZEC 取整數位；XRP、ADA 保留小數點後四位。其他美元金額、市值與成交量四捨五入到整數並加千分位（例如 $95,421、-$836）。百分比、MVRV Z-Score、RSI 維持原有小數。
6. 【情緒標記與格式】：每句發言以「男：」或「女：」開頭，角色名後可接標籤 [excited]、[serious]、[analytical] 或 [debate]，不可念出標籤。只輸出對話純文字，不要 Markdown，不要包裹 JSON。
7. 【主播角色語氣】：
   女聲「以太喵」（冷靜精明、略帶傲嬌吐槽，自然穿插「他喵的」「喵頭不對」等貓語梗，熟悉技術與數據，負責精準拆解價格結構、均線乖離與可取得的鏈上指標；若本集有有效 MVRV Z-Score 才分析該指標，沒有數據就完全不提）。
   男聲「比特汪」（熱情直率、容易激動，自然使用「汪嗚」「本汪」「別汪想」等狗語梗，重視 BTC 長期週期與紀律定投）。男主播唯一名稱就是「比特汪」，嚴禁寫成「位元汪」「位元犬」或任何把 Bitcoin 譯成「位元」的寫法。
   兩人唇槍舌劍、自然吐槽，口播禁止使用 HODL 縮寫，一律自然說「長期持有」或「長期持有者」。
   避開禁忌詞：{banned_terms}。
8. 【數字顯示】：所有年份、價格、百分比、小數、比率與數量一律使用阿拉伯數字，例如 0.587、27.53%、2026 年；禁止寫成「零點五八七」等中文數字形式。
9. 【避免重複】：同一觀點只完整解釋一次；後續提及必須增加新數據、因果或不同角度，禁止換句話重述。避免重複相同轉折句型。
10. 【口頭禪節制】：每位主持人全集最多使用 1 次招牌口頭禪，不得重複；不要為了塞入角色梗犧牲資訊密度。
11. 【避免固定節目感】：不得預設每集都要有開場破題、行情快篩、多空辯論、鏈上分析、情境推演或定投結尾。由今日最重要的事件與證據決定內容、順序、比重和收束方式；沒有必要的主題可以完全不講。
12. 【簡報與口播逐頁同步（最高優先級）】：每個 [SLIDE_n] 都代表一張獨立簡報，標記後的整段口播直到下一個標記，只能講解該頁指定標題與同一主題的證據。必須先明確講出該頁標題所指的事件／指標／新聞，再依序說明事件細節、關鍵數據、因果與影響；不可把其他新聞、其他幣種或無關指標塞進同一頁。新聞人物、機構、事件日期、數字與後續影響必須放在對應新聞標題的頁面，不可只在口播提到、卻讓畫面顯示另一個主題。若該頁資料不足，寧可只呈現有證據的內容，不可用通用敘述填滿。下一個 [SLIDE_n] 出現時，必須立即切換到下一頁主題。"""

    target_length = (
        "測試模式約 200 字，保留全部投影片錨點，每頁至少一句有效對話"
        if is_test else
        "全篇約 5,500 至 6,500 字，依主題重要性自由分配篇幅；不可平均分配各頁，也不可為達字數重複灌水"
    )

    # ── 步驟 A：總編輯建立本集專屬主題地圖 ───────────────────────────
    print("[腳本步驟 A] 總編輯建立本集專屬主題地圖，依事件決定敘事順序與比重...")
    brief_prompt = f"""你是頂級加密貨幣財經播客【幣看深潛】總編輯。
請針對今日完整市場數據與焦點新聞正文，建立本集專屬的編輯判斷與主題地圖。每集都必須重新判斷，不可套用固定節目流程。

【今日切入視角】{angle_title}，只是分析透鏡，不是必須硬套的題目；若今日新聞或數據出現更強烈、可驗證的異常，優先以真實事件決定主軸。近 7 日已用視角（今日必須避免重複）：{recent_angles}。
【今日節目模式】{show_format.get("title")}（{show_format.get("id")}）。{show_format.get("instruction")}
【近 7 日內容去重規則】{_learning_prompt_line()}
【可自然帶入的術語】{vocab_line}
【雙主播口頭禪】{catchphrase_line}

{full_report}

請完成：
1. 找出今日最值得觀眾花時間的核心事件、數據異常或市場背離；若沒有重大事件，選擇最值得追查的一個問題，不得捏造。
2. 判斷需要哪些證據才能解釋核心問題，優先採用至少 2 組不同來源交叉驗證。
3. 決定敘事應從哪個問題或事件開始、如何推進、在哪裡收束；不必包含傳統開場、行情快篩、多空辯論、預測或投資心法。
4. 從以下 {candidate_count} 個候選視覺素材中，依本集敘事需要挑選 6 到 {candidate_count} 張並重新排序；不要為了湊張數選不相關素材。每張必須回傳 source_slot（候選編號）與 title（具體標題），source_slot 不可重複。候選素材：{slide_slots}。若本集啟用 DCA，必須保留候選編號 {dca_candidate_slot} 的模擬帳本圖卡。
5. narrative_flow 用自然語言描述本集實際敘事順序、主題間因果關係、哪些主題應深入、哪些只需帶過；不得用第一幕、第二幕、第三幕或任何固定段落數。
6. slide_news：最多 3 筆真正適合做新聞圖卡的新聞摘要；沒有足夠新聞就回傳較少筆，不要硬湊。
7. slide_week：只有當今日資料足以支持未來 24 小時條件推演時才提供，最多 3 條；不適用時回傳空陣列。
8. 每日選題必須差異化，不可因固定數據欄位存在就每天照同一順序講恐慌指數、均線、六幣、新聞、多空和預測。
9. 投影片文案極簡，每句不超過 15 字，主內容充分利用畫面中央可用區域，不得只放標題或單行文字造成大面積空白。

只輸出合法 JSON：
{{
  "title": "32字內新聞級標題",
  "editorial_lead": "一句話核心主軸",
  "core_conflict": "最重要的矛盾或調查問題",
  "smart_money_intent": "有證據才提出資金意圖，否則留空",
  "bull_case": "有證據才提出多方論點，否則留空",
  "bear_case": "有證據才提出空方論點，否則留空",
  "opening_hook": "若適合才提供開場鉤子，不適合可留空",
  "narrative_flow": "本集動態敘事流程與篇幅分配",
  "slide_plan": [{"source_slot": 1, "title": "具體標題"}],
  "slide_news": [],
  "slide_week": []
}}"""

    brief_raw = call_gemini(brief_prompt, max_output_tokens=8192, timeout=120, json_mode=True)
    editorial_brief = _parse_editorial_brief(brief_raw, mdata, candidate_count)

    selected_plan = []
    used_slots = set()
    for item in editorial_brief.get("slide_plan") or []:
        if not isinstance(item, dict):
            continue
        try:
            source_slot = int(item.get("source_slot"))
        except (TypeError, ValueError):
            continue
        if source_slot < 1 or source_slot > candidate_count or source_slot in used_slots:
            continue
        slide = dict(visual_candidates[source_slot - 1])
        title = clean_slide_title(str(item.get("title") or slide.get("title") or ""))
        if title:
            slide["title"] = title
        selected_plan.append(slide)
        used_slots.add(source_slot)

    # 舊版或格式不完整的編輯 JSON 不會造成空簡報；回退到候選素材原順序。
    if len(selected_plan) < 6:
        selected_plan = [dict(slide) for slide in visual_candidates]
        used_slots = set(range(1, candidate_count + 1))
        print("總編輯 slide_plan 不完整，回退候選素材；仍保留動態逐字稿敘事。")

    # 定投帳本是週期性固定資訊，只在該集有啟用時保留；不讓模型誤刪。
    if dca_candidate_slot and dca_candidate_slot not in used_slots:
        selected_plan.append(dict(visual_candidates[dca_candidate_slot - 1]))
        print("已將本集必要的 DCA 模擬帳本圖卡加入動態簡報。")

    visual_plan = selected_plan
    slide_count = len(visual_plan)
    for index, slide in enumerate(visual_plan):
        slide["layout"] = assign_daily_layout(slide, index)
        slide["broll_query"] = assign_broll_query(slide)
    editorial_brief["slide_titles"] = [slide["title"] for slide in visual_plan]
    slide_anchors = "；".join(
        f"[SLIDE_{index}]{slide['title']}" for index, slide in enumerate(visual_plan, 1)
    )
    print(f"[總編輯定調完成] 標題：{editorial_brief['title']} ｜ 核心主軸：{editorial_brief['editorial_lead']}")
    print(f"[核心矛盾] {editorial_brief['core_conflict']}")
    print(f"[動態敘事地圖] {editorial_brief.get('narrative_flow') or '依投影片主題順序自由展開'}")

    # ── 步驟 B：依動態主題地圖一次生成完整逐字稿 ───────────────────────
    print(f"[腳本步驟 B] 依本集主題地圖生成完整口播（{slide_count} 個同步錨點；{target_length}）...")
    script_prompt = f"""你是財經播客【幣看深潛】主編。請依本集專屬主題地圖，寫出完整、自然、有深度的雙人財經播客逐字稿。
篇幅要求：{target_length}。

【本集核心主軸】{editorial_brief.get('editorial_lead')}
【核心矛盾／調查問題】{editorial_brief.get('core_conflict')}
【編輯敘事地圖】{editorial_brief.get('narrative_flow')}
【開場鉤子】{editorial_brief.get('opening_hook')}
【雙主播口頭禪】{catchphrase_line}
【可自然帶入術語】{vocab_line}
{mvrv_prompt}
{dca_prompt_section}

【本集必須對應的投影片主題，依序逐頁處理】：
{slide_anchors}

每個錨點必須且只能出現一次，嚴格依序。錨點要放在該頁第一句發言開頭（角色名與情緒標籤之後），例如「男：[analytical][SLIDE_1]...」。每個錨點後的口播只談該頁主題，直到下一個錨點才切換。每頁依資料重要性分配篇幅，不可平均分配；同一頁若證據豐富可深入拆解，若證據有限就精簡，不可用空話填滿。不可新增沒有對應投影片的新聞或指標段落。

【敘事自由度】不得將內容切成固定三幕或任何固定段落數。不得為了符合既定節目流程而硬塞行情快篩、多空辯論、鏈上分析、未來 24 小時預測、定投心法或固定收尾。某項內容只有在今日證據與主題確實需要時才可出現。可依本集題材採用調查報導、事件追蹤、數據拆解、歷史比較、機制教學、觀點辯論、風險情境或其他最合適的敘事方式；也可以混合使用。開場與結尾形式由內容決定。

{common_rules}

【數據與新聞資料庫】：
{full_report}

只輸出完整雙人逐字稿，不要說明、不要標題、不要 Markdown。"""

    full_script = str(call_gemini(script_prompt, max_output_tokens=16384, timeout=360)).strip()
    expected_anchors = list(range(1, slide_count + 1))
    actual_anchors = [int(value) for value in re.findall(r"\[SLIDE_(\d+)\]", full_script)]
    if actual_anchors != expected_anchors:
        print(
            f"完整逐字稿錨點驗收未通過：expected={expected_anchors}, actual={actual_anchors}；"
            "重新生成完整稿一次，不自動補塞錨點。"
        )
        retry_prompt = f"""{script_prompt}

【上次生成未通過分鏡驗收】
必須且只能依序出現以下錨點：{ "、".join(f"[SLIDE_{n}]" for n in expected_anchors) }。
上次實際錨點：{actual_anchors}。
請重新生成完整逐字稿，逐頁對應每一個錨點，不可省略、重複或改序。不要把稿件拆成固定幕數。"""
        full_script = str(call_gemini(retry_prompt, max_output_tokens=16384, timeout=360)).strip()
        actual_anchors = [int(value) for value in re.findall(r"\[SLIDE_(\d+)\]", full_script)]
    if actual_anchors != expected_anchors:
        raise RuntimeError(
            "完整逐字稿分鏡錨點缺失、重複或順序錯誤，停止製作以避免口播與簡報錯位。"
            f" expected={expected_anchors}, actual={actual_anchors}"
        )

    total_chars = _spoken_chars(full_script)
    if not is_test and total_chars < 4800:
        print(f"完整逐字稿目前 {total_chars} 字，低於 4,800，重新完整擴寫一次；不在最後一張投影片後追加，避免分鏡失衡。")
        expansion_prompt = f"""{script_prompt}

【完整稿長度驗收未通過】
上次完整逐字稿只有 {total_chars} 字，低於 4,800 字。
請重新撰寫整份完整逐字稿，目標 5,500 至 6,500 字；必須保留全部 {slide_count} 個投影片錨點，且每個錨點恰好一次、順序不變。
依各主題證據量擴寫，不可平均灌水、不可重複觀點、不可新增主題，也不可把補充內容全部塞進最後一張投影片。
只輸出完整逐字稿，不要解釋。"""
        full_script = str(call_gemini(expansion_prompt, max_output_tokens=16384, timeout=360)).strip()
        actual_anchors = [int(value) for value in re.findall(r"\[SLIDE_(\d+)\]", full_script)]
        if actual_anchors != expected_anchors:
            raise RuntimeError(
                "完整逐字稿擴寫後分鏡錨點缺失、重複或順序錯誤，停止製作。"
                f" expected={expected_anchors}, actual={actual_anchors}"
            )
        total_chars = _spoken_chars(full_script)
        print(f"完整逐字稿重新擴寫後：{total_chars} 字。")
        if total_chars < 3200:
            raise RuntimeError(f"完整逐字稿只有 {total_chars} 字，低於 3,200 字最低門檻，停止製作。")

    full_script = silence_gap_leaks(normalize_long_term_terms(full_script))
    _ensure_slide_anchors(full_script, slide_count)

    # 舊欄位保留給既有快取與外部流程相容；僅按字元位置切分，不代表節目段落或固定結構。
    split_anchor = max(1, (slide_count + 1) // 2)
    split_match = re.search(rf"\[SLIDE_{split_anchor}\]", full_script)
    split_at = split_match.start() if split_match else len(full_script) // 2
    part1_raw = full_script[:split_at].strip()
    part2_raw = full_script[split_at:].strip()
    part3_raw = ""
    print(f"=== 動態主題逐字稿完成！總字數：{_spoken_chars(full_script)} 繁中字 ===")
    if not is_test:
        _log_length_gate(_spoken_chars(full_script), "每日動態主題逐字稿")

    # ── 後處理與整合：沿用上方已完成錨點驗收的完整逐字稿 ────────────────
    slide_news_details = _pad_slide_news(editorial_brief.get("slide_news"), mdata)
    for item in slide_news_details:
        item["slide_title"] = silence_gap_leaks(item.get("slide_title"))
        item["positive"] = silence_gap_leaks(item.get("positive"))
        item["risk"] = silence_gap_leaks(item.get("risk"))
    slide_news = [
        f"利多：{item['positive']}；風險：{item['risk']}"
        for item in slide_news_details
    ]

    editorial_lead = editorial_brief.get("editorial_lead") or suggest_editorial_lead(mdata)
    if any(banned in editorial_lead for banned in BANNED_SLIDE_TITLES):
        editorial_lead = suggest_editorial_lead(mdata)

    ai_title = silence_gap_leaks(sanitize_text(editorial_brief.get("title") or editorial_lead or "加密市場焦點調查"))
    if not ai_title:
        ai_title = editorial_lead or "加密市場焦點調查"
    video_title = f"【幣看趨勢】{ai_title} ({today_str})"

    slide_24h_conditions = _pad_slide_week(editorial_brief.get("slide_week"), mdata)
    cleaned_week = []
    for item in slide_24h_conditions:
        cleaned = silence_gap_leaks(item)
        if cleaned:
            cleaned_week.append(cleaned)
    slide_24h_conditions = cleaned_week or list(slide_24h_conditions)

    slide_titles = align_slide_titles(
        editorial_brief.get("slide_titles"),
        [slide["title"] for slide in visual_plan],
    )
    for slide, title in zip(visual_plan, slide_titles):
        slide["title"] = title
    fill_story_slide_bodies(visual_plan, editorial_lead, slide_news_details, slide_24h_conditions)
    fill_slide_bodies_from_narration(visual_plan, full_script)
    opening_hook = editorial_brief.get("opening_hook") or _opening_hook(full_script)

    result = {
        "script_structure_version": 2,
        "part1_raw": part1_raw,
        "part2_raw": part2_raw,
        "part3_raw": part3_raw,
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
        "core_conflict": editorial_brief.get("core_conflict") or "",
        "slide_titles": slide_titles,
        "visual_slides": visual_plan,
        "slide_count": len(visual_plan),
        "format_id": show_format.get("id") or "",
        "format_title": show_format.get("title") or "",
        "visual_style": visual_style,

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
            "myth": strip_bracket_tags(str(item.get("myth") or "先分清概念與實際數據")),
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
    raw_sections = [item for item in raw_sections if isinstance(item, dict)][:8]
    fallback_titles = [str(item).strip() for item in (lesson.get("core_concepts") or []) if str(item).strip()]
    if not fallback_titles:
        fallback_titles = [str(lesson.get("title") or "核心概念")]
    if not raw_sections:
        raw_sections.append({"title": fallback_titles[0], "narration": "", "bullets": []})
    sections = []
    for index, item in enumerate(raw_sections):
        title = str(item.get("title") or fallback_titles[index % len(fallback_titles)]).strip()
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
            "bullets": bullets[:4] or [title],
            "narration": narration,
            "lines": lines,
        })
    short_lines = _dialogue_lines(payload.get("short_script"))
    short_script = str(payload.get("short_script") or "")
    if len(short_lines) < 2:
        short_script = (
            "男：這集把原理講完了，完整版在頻道。\n"
            "女：真正重要的是把這集的核心概念用在判讀資訊，而不是照單全收。"
        )
        short_lines = _dialogue_lines(short_script)
    return {
        "ep_id": lesson.get("ep_id") or "EP",
        "series": lesson.get("series") or "",
        "lesson_title": lesson.get("title") or "",
        "title": sanitize_text(payload.get("title") or lesson.get("title") or "系統教學"),
        "structure_version": 2,
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
    if not isinstance(cached, dict) or cached.get("structure_version") != 2 or not 3 <= len(cached.get("sections") or []) <= 8:
        return None
    print("[REUSE_SCRIPT] 使用 cache_last_tutorial_script.json，教學稿不呼叫 Gemini。")
    return cached


def generate_tutorial_script(lesson, reference_note, config):
    """依題目複雜度動態規劃段落的常青教學稿。快取命中時不呼叫 Gemini。"""
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
        "測試模式：段落數完全依題目需要決定，可為 1 到 8 段；每段簡短呈現，總長約 200 字。"
        if is_test else
        "正式版口播合計 5,800 到 6,500 字，段落數由題目複雜度決定。Shorts 口播 45 到 55 秒，約 180 到 230 字。"
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
請先依題目知識範圍與複雜度自行決定必要的段落數（1 到 8 段皆可，沒有必要就不要湊數），段落標題、順序、深度與篇幅皆由內容決定；不得套用固定段落順序，也不得要求每段相同句數。另外獨立輸出 18 到 30 張圖卡，每張 2 到 4 句口播，張數依知識點數量決定。
每一張圖卡必須自帶完整欄位，禁止拿口播半句當標題：card_title 是 8 到 16 字的完整知識標題；hero_stat 是本頁核心數據或 4 到 10 字金句，例如「170 萬顆遠古沉睡幣」「UTXO 帳本機制」；bullet_points 是 2 到 3 條、每條 4 到 15 字的極簡短詞（1 個視覺符號 ＋ 4～8 字短詞 ＋ 1 個核心數字或對比箭頭，如 15 分鐘 ➔ 6 秒），禁止超過 15 字。三個欄位都要是完整語意，不能停在「也就是說」這類半截句。
開頭可依題目特性選擇問題、案例或反直覺事實，不強制使用鉤子。免責聲明「本集是公開資訊整理，不構成投資建議」自然放在適當位置。
【知識密度】禁止空泛重複。依各段主題選擇最適合的技術機制、歷史證據、數據比較或雙人交鋒，不要求每段都塞入相同種類的素材；資料不足的面向不可以硬湊。
鐵律：繁體中文。角色只有比特汪與以太喵，男主播唯一名稱就是「比特汪」，嚴禁寫成「位元汪」「位元犬」或把 Bitcoin 譯成「位元」；台詞以「男：」或「女：」開頭。美元金額一律整數加千分位，例如 $1、$35,000、$95,000。禁止小數金額、台幣、NT$、TWD。禁止 HODL、媒體名、交易所名，以及把 AI 當主題或字眼。可在角色名後加 [excited]、[serious]、[analytical]、[debate]，標籤只供配音，不要出現在 card_title、hero_stat、bullet_points。不承諾獲利，不鼓勵槓桿或借貸。禁止編造今日即時價格；價格帶若出現，必須說是早期週期區間示意或結構示意，不是即時普查。禁止說抓不到資料、無法取得數據、資料缺失或連線失敗。
只輸出一個 JSON，不要 Markdown。格式：
{{"title":"32字內標題","sections":[{{"title":"段名","bullets":["重點1","重點2","重點3"],"narration":"男：[analytical]...\\n女：[serious]..."}}],"cards":[{{"card_title":"完整標題","hero_stat":"核心金句","bullet_points":["完整要點一","完整要點二"],"lines":[{{"role":"男","text":"不含半截句的口播"}}]}}],"short_script":"男：...\\n女：..."}}
sections 必須 1 到 8 段，cards 依知識點數量決定，至少 8 張、最多 30 張。short_script 要自然總結本集核心問題並引導觀眾觀看完整教學；只有課題本身涉及定期定額時，才可提及模擬帳本。"""
    raw = call_gemini(
        prompt, max_output_tokens=65536, timeout=300, json_mode=True, models=PRODUCTION_MODELS,
    )
    try:
        tutorial = _normalize_tutorial(_json_object(raw), lesson)
    except (ValueError, json.JSONDecodeError) as error:
        print(f"教學稿需要補一次合法 JSON：{error}")
        raw = call_gemini(
            prompt + "\n上次輸出不是合法 JSON。這次只回傳合法 JSON，sections 必須 1 到 8 段，cards 依知識點數量決定，至少 8 張、最多 30 張。",
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
                "以下教學 JSON 偏短。請依動態段落加深 narration 與 18 到 30 張 cards："
                "補上與本集課題直接相關的技術機制、可靠歷史證據、數據對比與雙人追問；不要加入與課題無關的固定素材。"
                "card_title、hero_stat、bullet_points 必須是完整短語，不能是口播半句，也不能含括號標籤。"
                "維持原有 1 到 8 段、依題目決定的動態結構、short_script，以及整數美元。不要承諾獲利。只回傳 JSON。\n\n"
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

