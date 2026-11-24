"""播報層共用格式、禁忌詞與 SSML。畫面與口播只走這裡，不在各檔各寫一套。"""
import html
import re
from decimal import Decimal, ROUND_HALF_UP

# 增刪平台、媒體與禁忌詞只改這一份。較長的詞要排在前面，避免短詞先切掉長詞。
EXCLUDED_PLATFORMS = [
    "動區動趨",
    "BlockTempo",
    "Cointelegraph",
    "CoinDesk",
    "The Block",
    "Blockcast",
    "Decrypt",
    "Bloomberg",
    "Reuters",
    "區塊客",
    "鏈新聞",
    "彭博",
    "路透",
    "動區",
    "幣安",
    "Binance",
    "Coinbase",
    "Bybit",
    "Bitget",
    "Kraken",
    "Uniswap",
    "火幣",
    "HTX",
    "OKX",
    "HODLer",
    "HODL",
    "AI",
]

EXCLUDED_TERMS = list(EXCLUDED_PLATFORMS)

_EMOTION = {
    "excited": {"rate": "108%", "pitch": "+1.5st", "break": "180ms"},
    "serious": {"rate": "92%", "pitch": "-1.5st", "break": "320ms"},
    "analytical": {"rate": "100%", "pitch": "+0st", "break": "140ms"},
    "debate": {"rate": "106%", "pitch": "+0.5st", "break": "80ms"},
}
_EMOTION_TAG = re.compile(r"\[(excited|serious|analytical|debate)\]")
_NUMBER = re.compile(r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\$?\d+(?:\.\d+)?%?")


def round_usd(value):
    """金額用四捨五入，不用銀行家捨入。"""
    cleaned = str(value).replace(",", "").replace("$", "").strip()
    return int(Decimal(cleaned).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tutorial_badge(ep_id):
    """教學片標題列：系列徽章與課號。"""
    return f"【幣看深潛｜系統教學】{ep_id}"


TUTORIAL_LAYOUTS = ("impact", "compare", "steps", "chart")

DAILY_STORY_BEATS = {
    "debate": [
        ("多頭先把話說完", "focus"),
        ("以太喵逐條反駁", "focus"),
        ("分歧落在這個價", "btc_structure"),
        ("誰的條件先被驗證", "focus"),
        ("空頭看到的風險", "compare"),
        ("多頭還缺哪一個確認", "focus"),
        ("未來一天只看三點", "focus"),
        ("交鋒之後留下的紀律", "focus"),
    ],
    "history_mirror": [
        ("今天的結構長得像哪一段", "btc_structure"),
        ("對上哪一個歷史階段", "focus"),
        ("當時籌碼怎麼換手", "focus"),
        ("相似結構裡的陷阱", "compare"),
        ("這次明顯不同的地方", "focus"),
        ("價格要怎麼確認", "focus"),
        ("未來一天只看三點", "focus"),
        ("鏡像之後的紀律", "focus"),
    ],
    "deep_story": [
        ("事件表面先看懂", "compare"),
        ("背後的資金動機", "focus"),
        ("連鎖效應會碰到誰", "focus"),
        ("數據有沒有對上故事", "btc_structure"),
        ("反例與被忽略的風險", "focus"),
        ("安靜的幣不用展開", "bars"),
        ("未來一天只看三點", "focus"),
        ("調查收到哪裡", "focus"),
    ],
    "psychology_flow": [
        ("追高的人在想什麼", "focus"),
        ("恐慌賣出的人在想什麼", "focus"),
        ("觀望資金站在哪裡", "compare"),
        ("換手可能發生在這個價", "btc_structure"),
        ("情緒和價格為什麼分開", "focus"),
        ("這個行為陷阱", "focus"),
        ("未來一天只看三點", "focus"),
        ("看懂心理之後的紀律", "focus"),
    ],
}


def plan_daily_slides(mdata, show_dca, show_format, dca_body=""):
    """開頭三頁快篩，其餘八頁留給當日模式。週五或 26 日再加一頁帳本。"""
    mdata = mdata or {}
    show_format = show_format or {}
    beats = DAILY_STORY_BEATS.get(show_format.get("id")) or DAILY_STORY_BEATS["debate"]
    price = format_usd(mdata.get("cur_p")) if mdata.get("cur_p") not in (None, "") else "比特幣"
    pct = mdata.get("btc_pct")
    move = f"{float(pct):+.2f}%" if isinstance(pct, (int, float)) else ""
    fear = mdata.get("fng_num")
    fear_name = ""
    today_fng = mdata.get("today_fng") or {}
    if isinstance(today_fng, dict):
        fear_name = str(today_fng.get("value_classification") or "")
    fear_line = f"恐慌指數 {int(fear)} {fear_name}".strip() if isinstance(fear, (int, float)) else "情緒溫度"
    slides = [
        {
            "role": "thermometer",
            "title": f"{fear_line}，{price} 有跟著怕嗎？",
            "accent": "#38bdf8",
            "body": f"{fear_line}\n比特幣 {price} {move}\n本節目內容為公開資訊整理，不構成投資建議。",
            "chart": "gauge",
        },
        {
            "role": "thermometer",
            "title": f"均線快篩：{price} 與以太坊同一頁",
            "accent": "#a78bfa",
            "body": f"{mdata.get('s1') or ''}\n{mdata.get('eth_slide') or ''}".strip(),
            "chart": "dual",
        },
        {
            "role": "thermometer",
            "title": "六幣快篩：只點名今天真的在動的",
            "accent": "#34d399",
            "body": mdata.get("coins_summary") or "安靜的幣種一筆帶過。",
            "chart": "bars",
        },
    ]
    if show_dca:
        slides.append({
            "role": "dca",
            "title": f"模擬帳本對上 {price}",
            "accent": "#fbbf24",
            "body": dca_body or "模擬定期定額帳本。",
            "chart": "dca",
        })
    accents = ["#fbbf24", "#fb7185", "#a78bfa", "#38bdf8", "#f472b6", "#34d399", "#f59e0b", "#e2e8f0"]
    for index, (title, chart) in enumerate(beats):
        slides.append({
            "role": "story",
            "title": title,
            "accent": accents[index % len(accents)],
            "body": show_format.get("title") or title,
            "chart": chart,
        })
    return slides


def fill_story_slide_bodies(slides, editorial, news_details, week_lines):
    """深度頁放主軸、一則新聞與三個觀察條件，不再拆成七頁報表。"""
    news = [item for item in (news_details or []) if isinstance(item, dict)]
    weeks = [str(item).strip() for item in (week_lines or []) if str(item).strip()]
    lead = strip_bracket_tags(editorial) or "今天的主軸"
    news_body = ""
    if news:
        news_body = f"利多：{news[0].get('positive') or lead}；風險：{news[0].get('risk') or '單則消息不是買賣指令。'}"
    week_body = "\n".join(weeks[:3]) or lead
    for slide in slides or []:
        if slide.get("role") != "story":
            continue
        title = str(slide.get("title") or "")
        if slide.get("chart") == "compare" and news_body:
            slide["body"] = news_body
        elif "三點" in title or "一天" in title:
            slide["body"] = week_body
        else:
            slide["body"] = f"{lead}\n{title}"
    return slides


def build_tutorial_cards(sections, lesson=None):
    """把五段口播切成約 25 到 35 張圖卡，每張 2 到 4 句。"""
    spoken = []
    for section_index, section in enumerate(sections or []):
        for line in section.get("lines") or []:
            spoken.append({
                "section_index": section_index,
                "role": line.get("role") or "男",
                "text": line.get("text") or "",
                "title": section.get("title") or "",
                "bullets": list(section.get("bullets") or []),
            })
    total = len(spoken)
    if total == 0:
        return []
    size = 3
    count = (total + size - 1) // size
    if count > 35:
        size = 4
    elif count < 25 and total >= 50:
        size = 2
    if (total + size - 1) // size > 35:
        size = max(4, (total + 34) // 35)
    lesson = lesson or {}
    myths = list(lesson.get("common_misconceptions") or ["把示意區間當成今天的買點"])
    concepts = list(lesson.get("core_concepts") or ["先看結構，再談價格"])
    cards = []
    for start in range(0, total, size):
        chunk = spoken[start:start + size]
        layout = TUTORIAL_LAYOUTS[len(cards) % 4]
        if len(cards) == 12:
            layout = "chart"
        myth = strip_bracket_tags(myths[len(cards) % len(myths)]) or "不要把示意當成買點"
        truth = fit_complete_phrase(
            concepts[len(cards) % len(concepts)], 4, 16, "先看結構再談價格"
        )
        steps = []
        for source in list(chunk[0]["bullets"] or []) + concepts:
            phrase = fit_complete_phrase(source, 8, 22, "")
            if phrase and phrase not in steps:
                steps.append(phrase)
            if len(steps) == 3:
                break
        while len(steps) < 2:
            steps.append(truth)
        cards.append({
            "layout": layout,
            "title": fit_complete_phrase(chunk[0]["title"], 4, 16, truth),
            "headline": truth,
            "myth": myth,
            "truth": truth,
            "steps": steps[:3],
            "lines": [{"role": item["role"], "text": item["text"]} for item in chunk],
        })
    return cards


def format_usd(value):
    """具體美元金額四捨五入到整數，並加上千分位。負號放在錢字符號前面。"""
    rounded = round_usd(value)
    sign = "-" if rounded < 0 else ""
    return f"{sign}${abs(rounded):,}"


def format_metric(value, kind):
    """統一幣價、百分比、比值與市值／成交量的顯示精度。"""
    number = float(value)
    if kind == "price":
        return format_usd(number)
    if kind == "percent":
        return f"{number:+.2f}%"
    if kind == "ratio":
        return f"{number:.2f}"
    if kind == "btc":
        return f"{number:.8f}"
    if kind == "compact":
        sign = "-" if number < 0 else ""
        magnitude = abs(number)
        if magnitude >= 1_000_000_000_000:
            return f"{sign}${magnitude / 1_000_000_000_000:.2f}T"
        if magnitude >= 1_000_000_000:
            return f"{sign}${magnitude / 1_000_000_000:.2f}B"
        if magnitude >= 1_000_000:
            return f"{sign}${magnitude / 1_000_000:.2f}M"
        return format_usd(number)
    raise ValueError(f"unknown metric kind: {kind}")


def classify_mvrv(value):
    ratio = float(value)
    if ratio < 1.0:
        zone = "歷史底部低估區"
    elif ratio < 2.4:
        zone = "健康多頭推進區"
    elif ratio <= 3.5:
        zone = "市場過熱警戒區"
    else:
        zone = "週期極端泡沫區"
    return zone


def _as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def refine_market_zone(zone, mvrv_value, is_estimate, fear_value, price, sma200):
    """大跌或跌破年線時，不用「健康多頭」形容低位或估算 MVRV。"""
    ratio = _as_float(mvrv_value)
    fear = _as_float(fear_value)
    spot = _as_float(price)
    average = _as_float(sma200)
    low_mvrv = ratio is not None and ratio < 2.4
    extreme_fear = fear is not None and fear < 25
    below_sma = spot is not None and average is not None and spot < average
    if (extreme_fear or below_sma) and (bool(is_estimate) or low_mvrv):
        if extreme_fear:
            return "極度恐懼觀望區"
        return "長線低估蓄勢區"
    return zone or "—"


def strip_bracket_tags(text):
    """畫面、字幕與縮圖不得留下 [serious] 這類控制標籤。"""
    return re.sub(r"\[[^\]]*\]", "", str(text or "")).strip()


def fit_complete_phrase(text, low, high, fallback):
    """只留下完整短語。不夠或過長時改用備援，不從句子中間硬切。"""
    cleaned = strip_bracket_tags(sanitize_text(text)).strip(" ，、；：。！？")
    pieces = []
    for sentence in re.split(r"[。！？\n]", cleaned):
        sentence = sentence.strip(" ，、；：")
        if not sentence:
            continue
        pieces.append(sentence)
        for clause in re.split(r"[，、；：]", sentence):
            clause = clause.strip()
            if clause and clause not in pieces:
                pieces.append(clause)
    for piece in pieces:
        if low <= len(piece) <= high:
            return piece
    backup = strip_bracket_tags(sanitize_text(fallback)).strip(" ，、；：。！？")
    if backup and len(backup) <= high:
        return backup
    if backup:
        return backup[:high]
    return "先看結構"


def sanitize_text(text):
    """腳本、字幕、簡報共用的後處理：角色名、禁忌詞、台幣字樣。"""
    cleaned = strip_bracket_tags(text)
    for wrong, right in (
        ("比特王", "比特汪"),
        ("比特旺", "比特汪"),
        ("比特網", "比特汪"),
        ("以太貓", "以太喵"),
        ("以太猫", "以太喵"),
    ):
        cleaned = cleaned.replace(wrong, right)
    cleaned = re.sub(r"\bHODLers?\b", "長期持有者", cleaned, flags=re.I)
    cleaned = re.sub(r"\bHODL\b", "長期持有", cleaned, flags=re.I)
    for term in sorted(EXCLUDED_PLATFORMS, key=len, reverse=True):
        if term.lower() in {"hodl", "hodler", "ai"}:
            continue
        cleaned = re.sub(re.escape(term), "公開資訊", cleaned, flags=re.I)
    cleaned = re.sub(r"(?<![A-Za-z])AI(?![A-Za-z])", "自動化", cleaned)
    cleaned = re.sub(r"(?:新台幣|台幣)", "美元", cleaned)
    cleaned = re.sub(r"NT\$\s*", "$", cleaned)
    cleaned = re.sub(r"\bTWD\b", "USD", cleaned, flags=re.I)
    cleaned = re.sub(r"(公開資訊\s*){2,}", "公開資訊", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def display_text(text):
    return sanitize_text(text)


def infer_emotion(raw):
    """沒有手寫標籤時，依漲跌用詞決定語氣。"""
    found = _EMOTION_TAG.findall(raw)
    if found:
        return found[0]
    if re.search(r"下跌|暴跌|回撤|跌破|下殺|風險|謹慎|浮虧|恐慌|走弱", raw):
        return "serious"
    if re.search(r"上漲|大漲|上攻|突破|站上|強勢|反彈|走強", raw):
        return "excited"
    return "analytical"


def build_ssml_inner(text):
    """把情緒標籤轉成 prosody，關鍵數字整段連讀並加重。"""
    raw = str(text or "")
    emotion = infer_emotion(raw)
    spoken = sanitize_text(raw)
    spoken = _NUMBER.sub(lambda match: match.group(0).replace(",", "").replace("$", ""), spoken)
    spoken = html.escape(spoken, quote=False)
    spoken = re.sub(
        r"(\d{3,}(?:\.\d+)?|\d+\.\d+)",
        r'<break time="160ms"/><emphasis level="moderate"><say-as interpret-as="cardinal">\1</say-as></emphasis>',
        spoken,
    )
    spoken = re.sub(r"([，、])", r'\1<break time="120ms"/>', spoken)
    spoken = re.sub(r"([。！？!?])", r'\1<break time="280ms"/>', spoken)
    style = _EMOTION[emotion]
    return (
        f'<prosody rate="{style["rate"]}" pitch="{style["pitch"]}">'
        f'<break time="{style["break"]}"/>{spoken}</prosody>'
    )


def split_keeping_numbers(segment, limit=20):
    """字幕斷行不得切斷完整數字。"""
    tokens = re.findall(
        r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\$?\d+(?:\.\d+)?%?|[A-Za-z]{2,10}|\s+|\S",
        segment,
    )
    lines = []
    current = ""
    for token in tokens:
        if len(token) > limit:
            if current.strip():
                lines.append(current.strip())
            lines.append(token.strip())
            current = ""
            continue
        if current and len(current) + len(token) > limit:
            lines.append(current.strip())
            current = token.lstrip()
        else:
            current += token
    if current.strip():
        lines.append(current.strip())
    return lines or [segment.strip()]
