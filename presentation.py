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

# 全片強制深邃暗底：注入到所有投影片 HTML 最頂端，避免網頁載入空檔露出
# 瀏覽器預設白底造成「整片反白」。從第 0 秒到最後一秒都維持護眼暗底。
# 僅在「該頁沒有 B-Roll 實景底片」時使用，作為防白屏保護。
GLOBAL_DARK_CSS = (
    "html, body, .slide-container { background-color: #070B14 !important; "
    "color: #F8FAFC !important; }"
)

# 當該頁有 B-Roll 實景動態影片時，HTML 最外層必須完全透明，讓底層已壓暗的
# Pixabay 1080p 實景影片清晰透出、在文字與卡片後方流動；前景文字與卡片仍保有
# 半透明深色毛玻璃底框（rgba(7, 11, 20, 0.72)）維持高對比可讀。
GLOBAL_TRANSPARENT_CSS = (
    "html, body, .slide-container { background: transparent !important; "
    "color: #F8FAFC !important; }"
)


def dark_css(has_broll=False):
    """依該頁是否有 B-Roll 底片，回傳透明或實心暗底的頂層 CSS。"""
    return GLOBAL_TRANSPARENT_CSS if has_broll else GLOBAL_DARK_CSS

# 男主播唯一名稱為「比特汪」。任何「位元汪／位元犬」等錯誤譯名一律強制修正。
_WRONG_DOG_NAME = re.compile(r"位元[汪犬]")

_EMOTION = {
    "excited": {"rate": "108%", "pitch": "+1.5st", "break": "140ms"},
    "serious": {"rate": "98%", "pitch": "-1.5st", "break": "220ms"},
    "analytical": {"rate": "105%", "pitch": "+0st", "break": "110ms"},
    "debate": {"rate": "108%", "pitch": "+0.5st", "break": "70ms"},
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

# ── B-Roll 實景底圖關鍵字 ──────────────────────────────
# 純技術圖表頁（chart_spotlight）保留圖表專注度，broll_query 設為空字串；
# 新聞故事、宏觀經濟、心理博弈等非純圖表頁自動給 2～3 個英文畫面關鍵字。
BROLL_KEYWORD_POOL = {
    "news": ["stock market", "cryptocurrency", "data center"],
    "macro": ["global finance", "abstract technology", "stock exchange"],
    "psychology": ["trader emotion", "stock market panic", "abstract technology"],
    "debate": ["trading screen", "bitcoin", "financial district"],
    "history": ["stock chart", "data center", "abstract technology"],
    "story": ["cryptocurrency", "server room", "global finance"],
}


def assign_broll_query(slide):
    """依該頁標題與實際播報內文產生 Pixabay 英文搜尋詞；純圖表頁不加底片。"""
    slide = slide or {}
    layout = slide.get("layout") or ""
    title = str(slide.get("title") or "")
    body = str(slide.get("body") or "")
    source = f"{title} {body}".lower()

    # 先依當頁播報的具體事件／名詞選圖，避免每頁都只搜泛用 bitcoin/finance。
    topic_rules = [
        (("sec", "證券交易委員會", "監管", "監管機構", "訴訟", "起訴"), "securities regulator, government building"),
        (("etf", "現貨基金", "資金流入", "資金流出", "基金申購", "基金贖回"), "bitcoin etf, stock exchange trading"),
        (("聯準會", "美聯儲", "fed", "利率", "降息", "升息", "通膨", "通胀"), "federal reserve, central bank, financial market"),
        (("交易所", "binance", "coinbase", "okx", "交易平台"), "cryptocurrency exchange, trading floor"),
        (("駭客", "黑客", "攻擊", "漏洞", "遭竊", "被盜", "資安"), "cybersecurity, server room, computer security"),
        (("挖礦", "礦工", "算力", "礦場"), "bitcoin mining, data center, server racks"),
        (("穩定幣", "usdt", "usdc", "支付", "清算"), "digital payment, fintech, financial technology"),
        (("solana", "sol "), "solana blockchain, validator server, digital network"),
        (("ethereum", "以太坊", "以太幣", "eth "), "ethereum blockchain, digital network, server room"),
        (("比特幣", "bitcoin", "btc "), "bitcoin cryptocurrency, blockchain network, trading screen"),
        (("巨鯨", "鯨魚", "鏈上", "錢包", "地址", "轉帳", "轉賬"), "blockchain data, digital network, data visualization"),
        (("川普", "特朗普", "白宮", "制裁", "戰爭", "地緣政治", "選舉"), "world news, government building, global economy"),
        (("破產", "倒閉", "清算", "債務", "裁員"), "financial district, office building, business news"),
        (("恐慌", "貪婪", "追高", "拋售", "情緒", "散戶"), "stock market trader, trading floor, market volatility"),
        (("宏觀", "經濟", "資金", "流動性", "美元", "債券"), "global finance, stock exchange, financial district"),
    ]
    selected = []
    for terms, query in topic_rules:
        if any(term in source for term in terms):
            for part in query.split(", "):
                if part not in selected:
                    selected.append(part)
            if len(selected) >= 3:
                break

    if len(selected) < 3:
        if layout == "versus_clash" or slide.get("role") == "story":
            fallback = BROLL_KEYWORD_POOL["debate"]
        elif any(kw in source for kw in ("心理", "恐慌", "貪婪", "追高", "觀望", "行為")):
            fallback = BROLL_KEYWORD_POOL["psychology"]
        elif any(kw in source for kw in ("宏觀", "經濟", "利率", "通膨", "資金")):
            fallback = BROLL_KEYWORD_POOL["macro"]
        elif any(kw in source for kw in ("歷史", "鏡像", "結構", "週線")):
            fallback = BROLL_KEYWORD_POOL["history"]
        elif any(kw in source for kw in ("新聞", "事件", "消息", "調查")):
            fallback = BROLL_KEYWORD_POOL["news"]
        else:
            fallback = BROLL_KEYWORD_POOL["story"]
        for part in fallback:
            if part not in selected:
                selected.append(part)
            if len(selected) >= 3:
                break

    return ", ".join(selected[:3])

# 每種節目敘事形式對應不同的整集視覺主題；不只換投影片內容，也換主畫面構圖。
VISUAL_STYLE_BY_FORMAT = {
    "debate": "debate",
    "history_mirror": "archive",
    "deep_story": "editorial",
    "psychology_flow": "psychology",
    "case_file": "investigation",
    "data_detective": "terminal",
    "scenario_lab": "scenario",
    "myth_busting": "editorial",
    "capital_flow": "data",
    "volatility_lab": "terminal",
}


def visual_style_for_format(format_id):
    """將節目形式轉成主影片整集視覺模板，未知模式採用紀錄片風格。"""
    return VISUAL_STYLE_BY_FORMAT.get(str(format_id or ""), "editorial")

DAILY_LAYOUTS = ("impact_quote", "versus_clash", "chart_spotlight", "timeline_steps")

DAILY_STORY_BEATS = {
    "debate": [
        ("多頭論點先過第一關", "focus"), ("空頭反證：量價有沒有跟上", "compare"),
        ("雙方真正分歧的價位", "btc_structure"), ("現貨與槓桿誰在主導", "bars"),
        ("最強反方證據", "focus"), ("多頭還缺哪個確認", "focus"),
        ("兩種走法的觸發條件", "focus"), ("交鋒後留下的結論", "focus"),
    ],
    "history_mirror": [
        ("今天的結構與過去哪裡相似", "btc_structure"), ("當時市場處於哪個階段", "focus"),
        ("歷史上籌碼如何換手", "bars"), ("相似走勢曾出現的陷阱", "compare"),
        ("這次與歷史最大的不同", "focus"), ("哪些條件不能直接類比", "focus"),
        ("歷史劇本的驗證價位", "focus"), ("這次不能忽略的變數", "focus"),
    ],
    "deep_story": [
        ("事件發生：先還原時間線", "compare"), ("誰受影響、誰可能受益", "focus"),
        ("資金流向透露的第二層訊息", "bars"), ("事件如何傳導到 BTC", "btc_structure"),
        ("市場可能忽略的反作用力", "focus"), ("哪些說法仍缺乏證據", "focus"),
        ("事件後續的三個觀察點", "focus"), ("調查結論：目前能確定什麼", "focus"),
    ],
    "psychology_flow": [
        ("追高資金正在承擔什麼", "focus"), ("恐慌賣壓從哪裡來", "bars"),
        ("觀望資金等待哪個訊號", "compare"), ("情緒與價格的落差", "btc_structure"),
        ("散戶最容易誤判的地方", "focus"), ("籌碼可能在哪裡換手", "focus"),
        ("情緒反轉需要什麼條件", "focus"), ("把情緒轉成可執行紀律", "focus"),
    ],
    "case_file": [
        ("案件現場：今天發生了什麼", "compare"), ("第一份證據：價格留下的痕跡", "btc_structure"),
        ("第二份證據：資金去了哪裡", "bars"), ("時間線：事件如何逐步發酵", "focus"),
        ("嫌疑假說：多方解讀", "focus"), ("反證：空方如何拆解", "compare"),
        ("尚未解開的關鍵問題", "focus"), ("目前證據支持哪個結論", "focus"),
    ],
    "data_detective": [
        ("先找出最不合理的數字", "compare"), ("拆開數字背後的分母", "focus"),
        ("價格與成交量是否說同一件事", "bars"), ("鏈上指標能否交叉驗證", "btc_structure"),
        ("資料支持的解釋", "focus"), ("資料尚不能證明的部分", "focus"),
        ("下一個能推翻假說的數據", "focus"), ("數據調查暫時結案", "focus"),
    ],
    "scenario_lab": [
        ("基準情境：市場維持現狀", "btc_structure"), ("上行情境：需要哪些條件", "focus"),
        ("下行情境：風險如何擴散", "compare"), ("三種情境的觸發價位", "focus"),
        ("哪個指標最先發出警報", "bars"), ("情境失效的判斷方式", "focus"),
        ("未來 24 小時決策樹", "focus"), ("不預測方向，只定義應對", "focus"),
    ],
    "myth_busting": [
        ("市場今天最流行的說法", "impact_quote"), ("這個說法忽略了什麼", "compare"),
        ("拿價格數據來驗證", "btc_structure"), ("再用資金數據交叉檢查", "bars"),
        ("哪些部分是真的", "focus"), ("哪些部分被過度延伸", "focus"),
        ("如果看錯，會先出現什麼", "focus"), ("拆完迷思後的實際結論", "focus"),
    ],
    "capital_flow": [
        ("資金入口：新增流動性在哪", "bars"), ("穩定幣供給透露的訊號", "focus"),
        ("現貨買盤是否真的進場", "compare"), ("槓桿資金正在放大什麼", "btc_structure"),
        ("資金從哪個市場流向哪裡", "focus"), ("流入不等於立即上漲", "focus"),
        ("下一個資金流向觀察點", "focus"), ("資金地圖給出的結論", "focus"),
    ],
    "volatility_lab": [
        ("波動突然放大的位置", "btc_structure"), ("哪一側部位最脆弱", "compare"),
        ("清算與成交量的連鎖反應", "bars"), ("支撐失守後的傳導路徑", "focus"),
        ("反向擠壓需要什麼條件", "focus"), ("波動收斂還是準備再擴張", "focus"),
        ("高波動下的三個風險點", "focus"), ("把波動轉成風險界線", "focus"),
    ],
}

def opening_conflict_body(mdata):
    """第 1 頁只放情緒和價格有沒有打架，不放免責大字。"""
    mdata = mdata or {}
    price = format_usd(mdata.get("cur_p")) if mdata.get("cur_p") not in (None, "") else "比特幣"
    pct = mdata.get("btc_pct")
    move = f"{float(pct):+.2f}%" if isinstance(pct, (int, float)) else ""
    fear = mdata.get("fng_num")
    fear_name = ""
    today_fng = mdata.get("today_fng") or {}
    if isinstance(today_fng, dict):
        fear_name = str(today_fng.get("value_classification") or "")
    fear_line = f"恐慌指數 {int(fear)} {fear_name}".strip() if isinstance(fear, (int, float)) else "情緒溫度"
    conflict = f"{fear_line}，比特幣 {price} {move}".strip("， ")
    focus = "先看情緒和價格有沒有走在一起"
    if isinstance(pct, (int, float)) and isinstance(fear, (int, float)):
        price_down = float(pct) < 0
        fear_high = float(fear) >= 55
        fear_low = float(fear) < 40
        if price_down and fear_low:
            focus = "價格先退，恐慌指數還沒跟上"
        elif (not price_down) and fear_high:
            focus = "價格還撐著，情緒已經偏熱"
        else:
            focus = "情緒和價格今天站在同一邊"
    return f"{conflict}\n{focus}"


def assign_daily_layout(slide, index=0):
    """依每頁內容性質與圖表類型，動態切換四大財經紀錄片／雜誌級視覺版型。"""
    slide = slide or {}
    chart = slide.get("chart")
    title = str(slide.get("title") or "")
    role = slide.get("role") or ""

    if chart in ("dual", "bars", "dca", "btc_structure", "btc_rsi", "gauge"):
        return "chart_spotlight"
    if chart == "summary" and role == "thermometer":
        return "impact_quote"
    if any(kw in title for kw in ("三點", "一天", "條件", "驗證", "推演", "連鎖", "劇本", "步驟")):
        return "timeline_steps"
    if chart == "compare" or any(kw in title for kw in ("反駁", "對決", "迷思", "風險", "分歧", "多頭", "空頭", "利多")):
        return "versus_clash"

    # 其餘深度頁依序輪替（避開連續重複）
    non_chart_layouts = ("impact_quote", "versus_clash", "timeline_steps")
    return non_chart_layouts[index % len(non_chart_layouts)]


def plan_daily_slides(mdata, show_dca, show_format, dca_body=""):
    """依當日節目模式重排市場快篩，並套用不同敘事分鏡，避免每日固定同一順序。"""
    mdata = mdata or {}
    show_format = show_format or {}
    format_id = show_format.get("id") or "debate"
    beats = DAILY_STORY_BEATS.get(format_id) or DAILY_STORY_BEATS["debate"]
    # 不同節目形式採用不同篇幅，保留該模式最後的結論幕，避免每日固定相同張數。
    beat_counts = {
        "debate": 7, "history_mirror": 6, "deep_story": 7,
        "psychology_flow": 6, "case_file": 8, "data_detective": 7,
        "scenario_lab": 8, "myth_busting": 6, "capital_flow": 7,
        "volatility_lab": 7,
    }
    beat_limit = beat_counts.get(format_id, len(beats))
    if len(beats) > beat_limit:
        beats = beats[:beat_limit - 1] + [beats[-1]]
    price = format_usd(mdata.get("cur_p")) if mdata.get("cur_p") not in (None, "") else "比特幣"
    pct = mdata.get("btc_pct")
    move = f"{float(pct):+.2f}%" if isinstance(pct, (int, float)) else ""
    fear = mdata.get("fng_num")
    today_fng = mdata.get("today_fng") or {}
    fear_name = str(today_fng.get("value_classification") or "") if isinstance(today_fng, dict) else ""
    fear_line = f"恐慌指數 {int(fear)} {fear_name}".strip() if isinstance(fear, (int, float)) else "情緒溫度"
    modules = {
        "fear": {"role":"thermometer","title":f"{fear_line}：市場真的在怕嗎？","accent":"#38bdf8","body":opening_conflict_body(mdata),"chart":"summary"},
        "dual": {"role":"thermometer","title":f"BTC {price}：與 ETH 的相對強弱","accent":"#a78bfa","body":f"{mdata.get('s1') or ''}\n{mdata.get('eth_slide') or ''}".strip(),"chart":"dual"},
        "rotation": {"role":"thermometer","title":f"資金輪動：BTC {move}，誰真的在動？","accent":"#34d399","body":mdata.get("coins_summary") or "安靜的幣種一筆帶過。","chart":"bars"},
    }
    # 不再每集固定掃過恐慌、BTC/ETH、六幣三張快篩；各節目形式只挑與敘事主軸有關的模組。
    module_orders = {
        "debate": ("fear", "dual", "rotation"),
        "history_mirror": ("dual", "rotation"),
        "deep_story": ("rotation", "fear"),
        "psychology_flow": ("fear", "rotation"),
        "case_file": ("dual", "rotation", "fear"),
        "data_detective": ("rotation", "dual"),
        "scenario_lab": ("dual", "fear"),
        "myth_busting": ("fear", "dual"),
        "capital_flow": ("rotation", "dual"),
        "volatility_lab": ("fear", "rotation"),
    }
    slides = [dict(modules[key]) for key in module_orders.get(format_id, module_orders["debate"])]
    mvrv = mdata.get("mvrv") or {}
    if (
        format_id in {"history_mirror", "data_detective", "scenario_lab"}
        and mvrv.get("mvrv_type") == "z_score"
        and mvrv.get("value") is not None
    ):
        slides.append({
            "role": "story",
            "title": f"MVRV Z-Score {float(mvrv['value']):.2f}：{mvrv.get('zone') or '鏈上估值位置'}",
            "accent": "#f59e0b",
            "body": mvrv.get("report") or "以歷史市值標準差衡量 BTC 估值位置",
            "chart": "focus",
        })
    if show_dca:
        slides.append({"role":"dca","title":f"模擬帳本：價格 {price} 對績效的影響","accent":"#fbbf24","body":dca_body or "模擬定期定額帳本。","chart":"dca"})
    accents = ["#fbbf24","#fb7185","#a78bfa","#38bdf8","#f472b6","#34d399","#f59e0b","#e2e8f0"]
    for index, (title, chart) in enumerate(beats):
        slides.append({"role":"story","title":title,"accent":accents[index % len(accents)],"body":show_format.get("title") or title,"chart":chart})
    for idx, slide in enumerate(slides):
        slide["layout"] = assign_daily_layout(slide, idx)
        if slide["layout"] != "versus_clash":
            slide["body"] = limit_slide_body(slide.get("body"))
        slide["broll_query"] = assign_broll_query(slide)
    return slides

def fill_slide_bodies_from_narration(slides, full_script):
    """簡報短句直接取自口播，並保留每個短句所屬 TTS 對話與句內位置。"""
    slide_lines = {}
    current_slide = None
    dialogue_index = 0
    anchor_pattern = re.compile(r"\[SLIDE_(\d+)\]")
    # 接受「男：[serious]」、「男 [serious]：」及獨立一行的 [SLIDE_n]。
    # 不改動錨點順序，也不替缺失錨點補值；只修正模型輸出格式差異造成的漏解析。
    dialogue_pattern = re.compile(
        r"^(男|女)\s*(?:\[[^\]]+\]\s*)*[：:]\s*(.*)$"
    )
    slide_count = len(slides or [])
    anchors = [int(value) for value in anchor_pattern.findall(str(full_script or ""))]
    expected = list(range(1, slide_count + 1))
    if anchors != expected:
        raise RuntimeError(
            "投影片與口播錨點不一致，停止輸出錯配簡報。"
            f" expected={expected}, actual={anchors}"
        )

    for raw_line in str(full_script or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 模型有時會把錨點獨立輸出，先更新目前頁面，再解析本行或下一行口播。
        anchor_match = anchor_pattern.search(line)
        if anchor_match:
            current_slide = int(anchor_match.group(1))
            line = anchor_pattern.sub("", line).strip()

        match = dialogue_pattern.match(line)
        if not match:
            continue

        spoken = match.group(2).strip()
        # 兼容錨點出現在角色標籤之後、口播文字之前的格式。
        inline_anchor = anchor_pattern.search(spoken)
        if inline_anchor:
            current_slide = int(inline_anchor.group(1))
            spoken = anchor_pattern.sub("", spoken).strip()
        if not spoken:
            continue
        this_dialogue_index = dialogue_index
        dialogue_index += 1
        if current_slide is None:
            continue

        spoken = _EMOTION_TAG.sub("", spoken).strip()
        spoken = sanitize_text(spoken)
        fragments = [
            fragment.strip(" ，、；：。！？!?;")
            for fragment in re.split(r"[，。！？、；：…!?;\n]+", spoken)
        ]
        fragments = [fragment for fragment in fragments if len(fragment) >= 4]
        bucket = slide_lines.setdefault(current_slide, [])
        fragment_count = max(1, len(fragments))
        for fragment_index, fragment in enumerate(fragments):
            condensed = condense_slide_line(fragment)
            if condensed and not any(item["text"] == condensed for item in bucket):
                bucket.append({
                    "text": condensed,
                    "dialogue_index": this_dialogue_index,
                    "fragment_index": fragment_index,
                    "fragment_count": fragment_count,
                })

    for index, slide in enumerate(slides or [], 1):
        candidates = slide_lines.get(index) or []
        if not candidates:
            raise RuntimeError(
                f"第 {index} 張投影片的 [SLIDE_{index}] 沒有對應口播內容，停止輸出。"
            )
        # 標題保留該頁口播開頭；內文則從整頁口播分布取樣，
        # 避免只顯示前幾句，漏掉本頁後段才出現的時事、數據或風險。
        slide["title"] = candidates[0]["text"]
        remaining_candidates = candidates[1:]
        body_limit = 2 if slide.get("layout") == "versus_clash" else 3
        if len(remaining_candidates) <= body_limit:
            body_candidates = remaining_candidates
        elif body_limit == 1:
            body_candidates = [remaining_candidates[len(remaining_candidates) // 2]]
        else:
            sample_indexes = [
                round(position * (len(remaining_candidates) - 1) / (body_limit - 1))
                for position in range(body_limit)
            ]
            body_candidates = [remaining_candidates[position] for position in sample_indexes]
        if slide.get("layout") == "versus_clash" and len(body_candidates) < 2:
            raise RuntimeError(
                f"第 {index} 張 VS 簡報缺少兩個可由口播支持的對照論點，停止輸出，避免補入未播報內容。"
            )
        if not body_candidates:
            body_candidates = candidates[:1]
        slide["body"] = "\n".join(item["text"] for item in body_candidates)
        slide["_body_timeline"] = body_candidates
        slide["body_source"] = "narration_anchor_distributed"
        slide["title_source"] = "narration_anchor_strict"
    return slides



def fill_story_slide_bodies(slides, editorial, news_details, week_lines):
    """依版型填入對應結構化文案：金句幕、VS對決幕、圖表焦點幕、三步驟推演幕。"""
    news = [item for item in (news_details or []) if isinstance(item, dict)]
    weeks = [str(item).strip() for item in (week_lines or []) if str(item).strip()]
    lead = strip_bracket_tags(editorial) or "今天的主軸"
    news_body = ""
    if news:
        news_body = f"利多：{news[0].get('positive') or lead}；風險：{news[0].get('risk') or '單則消息不是買賣指令。'}"
    week_body = "\n".join(weeks[:3]) or lead
    week_cursor = 0
    for idx, slide in enumerate(slides or []):
        if slide.get("role") != "story":
            continue
        title = str(slide.get("title") or "")
        layout = slide.get("layout") or assign_daily_layout(slide, idx)
        slide["layout"] = layout
        if layout == "versus_clash":
            if news_body:
                slide["body"] = news_body
            else:
                slide["body"] = f"利多：{lead}；風險：短線量能若未跟上結構確認，回撤壓力仍在。"
        elif layout == "timeline_steps":
            # 多頁三步驟若都用同一組條件會看起來像空頁；有足夠條件時逐頁輪替。
            if len(weeks) >= 3:
                offset = week_cursor % len(weeks)
                rotated = weeks[offset:] + weeks[:offset]
                slide["body"] = "\n".join(rotated[:3])
            else:
                slide["body"] = week_body
            week_cursor += 1
        else:
            slide["body"] = f"{lead}\n{title}"
    for slide in slides or []:
        if slide.get("layout") != "versus_clash":
            slide["body"] = limit_slide_body(slide.get("body"))
        slide["broll_query"] = assign_broll_query(slide)
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
        truth = condense_slide_line(
            fit_complete_phrase(concepts[len(cards) % len(concepts)], 4, 16, "先看結構再談價格")
        )
        steps = []
        for source in list(chunk[0]["bullets"] or []) + concepts:
            phrase = condense_slide_line(fit_complete_phrase(source, 8, 22, ""))
            if phrase and phrase not in steps:
                steps.append(phrase)
            if len(steps) == 3:
                break
        while len(steps) < 2:
            steps.append(truth)
        cards.append({
            "layout": layout,
            "title": condense_slide_line(fit_complete_phrase(chunk[0]["title"], 4, 16, truth)),
            "headline": truth,
            "myth": myth,
            "truth": truth,
            "steps": steps[:3],
            "lines": [{"role": item["role"], "text": item["text"]} for item in chunk],
        })
    return cards


def format_usd_price(price):
    """分級幣價格式化：≥100 整數、10–100 兩位、<10 四位。"""
    value = float(price)
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 10:
        return f"${value:.2f}"
    return f"${value:.4f}"


def format_usd(value):
    """BTC、ETH、SOL、ZEC 等高價幣報價四捨五入到整數。"""
    rounded = round_usd(value)
    sign = "-" if rounded < 0 else ""
    return f"{sign}$" + f"{abs(rounded):,}"


def format_coin_price(symbol, value):
    """依幣種固定精度：BTC/ETH/SOL/ZEC 整數；XRP/ADA 保留四位小數。"""
    code = str(symbol or "").upper()
    number = float(value)
    sign = "-" if number < 0 else ""
    if code in {"XRP", "ADA"}:
        return f"{sign}$" + f"{abs(number):,.4f}"
    if code in {"BTC", "ETH", "SOL", "ZEC"}:
        return format_usd(number)
    return format_usd_price(number)


def format_mvrv(value, mvrv_type="z_score"):
    """MVRV 無單位鏈上指標，固定兩位小數。"""
    return f"{float(value):.2f}"


def format_metric(value, kind):
    """統一幣價、百分比、比值與市值／成交量的顯示精度。"""
    number = float(value)
    if kind == "price":
        return format_usd_price(number)
    if kind == "percent":
        return f"{number:+.2f}%"
    if kind == "ratio":
        return f"{number:.2f}"
    if kind == "mvrv":
        return format_mvrv(number)
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


def classify_mvrv_ratio(value):
    """MVRV 比率（市值／已實現價值倍數）區間判讀。"""
    ratio = float(value)
    if ratio < 1.0:
        return "全網平均浮虧低估區"
    if ratio < 1.8:
        return "理性累積區"
    if ratio < 2.8:
        return "健康多頭推進區"
    if ratio < 3.5:
        return "市場偏熱警戒區"
    return "極度過熱泡沫風險"


def classify_mvrv_zscore(value):
    """MVRV Z 分數（標準差尺度）區間判讀。"""
    z = float(value)
    if z < 0.1:
        return "極度低估歷史底部"
    if z < 1.5:
        return "低估蓄勢"
    if z < 3.5:
        return "健康多頭推進"
    if z < 6.0:
        return "市場偏熱警戒"
    return "極度過熱泡沫風險"


def classify_mvrv(value, mvrv_type="ratio"):
    """依 mvrv_type 分派到對應的區間判讀函式。"""
    if mvrv_type == "z_score":
        return classify_mvrv_zscore(value)
    return classify_mvrv_ratio(value)


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


# ── 極簡圖解文案：投影片內文每句不得超過 15 字 ──────────────────────
MAX_SLIDE_LINE = 15
_SLIDE_CLAUSE_SPLIT = re.compile(r"[。！？\n]")
# 常見視覺符號，濃縮時優先保留在短詞前，營造高留白呼吸感。
SLIDE_SYMBOLS = ("🔥", "📈", "📉", "⚡", "🎯", "🧭", "💰", "🐋", "⚖️", "🔍", "⏳", "🚀", "⚠️", "✅", "❓")


def condense_slide_line(text):
    """把一句投影片內文濃縮成 ≤15 字短詞：優先「短詞＋核心數字／對比箭頭」。"""
    cleaned = strip_bracket_tags(sanitize_text(text)).strip(" ，、；：。！？")
    if not cleaned:
        return ""
    clause = next((piece.strip() for piece in _SLIDE_CLAUSE_SPLIT.split(cleaned) if piece.strip()), cleaned)
    candidate = clause or cleaned
    if len(candidate) <= MAX_SLIDE_LINE:
        return candidate
    number = _NUMBER.search(candidate)
    if number:
        token = number.group(0)
        if len(token) <= MAX_SLIDE_LINE:
            head = candidate[: candidate.find(token)].strip(" ，、；：")
            budget = max(0, MAX_SLIDE_LINE - len(token))
            if len(head) > budget:
                # head 太長時在自然斷點切，不硬切出半截詞。
                window = head[: budget + 4]
                cut = max(window.rfind(ch) for ch in "，、；：, ")
                head = window[:cut] if cut >= 4 else window
            head = head.strip(" ，、；：, ")
            combined = f"{head} {token}".strip()
            if combined:
                return combined[: MAX_SLIDE_LINE + 2].strip()
    # 太長又沒有數字：在容差範圍內找最近的標點或空格當自然斷點，
    # 找不到就保留完整子句，寧可略長也不硬切出斷頭句。
    window = candidate[: MAX_SLIDE_LINE + 4]
    cut = max(window.rfind(ch) for ch in "，、；：, ")
    if cut >= 6:
        return window[:cut].strip(" ，、；：, ")
    return candidate.strip(" ，、；：")


def limit_slide_body(body):
    """整頁內文逐句濃縮、去重，確保每句不超過 15 字。"""
    condensed = []
    for line in str(body or "").splitlines():
        piece = condense_slide_line(line)
        if piece and piece not in condensed:
            condensed.append(piece)
    return "\n".join(condensed)


_CN_DIGIT_MAP = {
    "零": "0", "〇": "0", "一": "1", "二": "2", "兩": "2", "三": "3",
    "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
}
_CN_NUMBER_CHARS = "零〇一二兩三四五六七八九十百千"


def _chinese_number_to_int(token):
    """將明確的中文數字片段轉為整數；純數字序列保留逐位讀法。"""
    raw = str(token or "")
    if not raw:
        return None
    if all(char in _CN_DIGIT_MAP for char in raw):
        return int("".join(_CN_DIGIT_MAP[char] for char in raw))
    section = 0
    number = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for char in raw:
        if char in _CN_DIGIT_MAP:
            number = int(_CN_DIGIT_MAP[char])
        elif char in units:
            unit = units[char]
            section += (number if number else 1) * unit
            number = 0
        else:
            return None
    return section + number


def normalize_chinese_numerals(text):
    """字幕與簡報中的中文數字轉為阿拉伯數字，避免零點五八七等寫法。"""
    value = str(text or "")
    digit_chars = "零〇一二兩三四五六七八九"

    percent_pattern = re.compile(
        rf"百分之([{_CN_NUMBER_CHARS}]+)(?:點([{digit_chars}]+))?"
    )

    def percent_replacement(match):
        whole = _chinese_number_to_int(match.group(1))
        if whole is None:
            return match.group(0)
        fraction = match.group(2)
        return (
            f"{whole}.{''.join(_CN_DIGIT_MAP[ch] for ch in fraction)}%"
            if fraction else f"{whole}%"
        )

    value = percent_pattern.sub(percent_replacement, value)

    decimal_pattern = re.compile(
        rf"([{_CN_NUMBER_CHARS}]+)點([{digit_chars}]+)"
    )

    def decimal_replacement(match):
        whole = _chinese_number_to_int(match.group(1))
        fraction = "".join(_CN_DIGIT_MAP[ch] for ch in match.group(2))
        return f"{whole}.{fraction}" if whole is not None else match.group(0)

    value = decimal_pattern.sub(decimal_replacement, value)

    unit_pattern = re.compile(
        rf"([{_CN_NUMBER_CHARS}]+)(?=(?:年|月|日|枚|顆|美元|倍|週|周|天|小時|分鐘|秒|個|次|位|項|家|筆|%))"
    )

    def unit_replacement(match):
        number = _chinese_number_to_int(match.group(1))
        return str(number) if number is not None else match.group(0)

    return unit_pattern.sub(unit_replacement, value)


def sanitize_text(text):
    """腳本、字幕、簡報共用的後處理：角色名、禁忌詞、台幣字樣。"""
    cleaned = strip_bracket_tags(text)
    for wrong, right in (
        ("比特王", "比特汪"),
        ("比特旺", "比特汪"),
        ("比特網", "比特汪"),
        ("位元汪", "比特汪"),
        ("位元犬", "比特汪"),
        ("以太貓", "以太喵"),
        ("以太猫", "以太喵"),
    ):
        cleaned = cleaned.replace(wrong, right)
    cleaned = _WRONG_DOG_NAME.sub("比特汪", cleaned)
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
    cleaned = re.sub(r"第\s*\d+\s*則\s*新聞", "最新消息", cleaned)
    cleaned = re.sub(r"新聞\s*\d+[、：:，]?", "最新消息", cleaned)
    cleaned = re.sub(r"(?:本|這|該)?新聞\s*[一二三四五六七八九十\d]+", "最新消息", cleaned)
    cleaned = normalize_chinese_numerals(cleaned)
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


def pronounce_for_tts(text):
    """僅供 Google TTS 發音用：小數點後逐位念，字幕與簡報原文不變。"""
    s = str(text or "")
    if not s:
        return s

    def decimal_spoken(value):
        whole, fraction = value.split(".", 1)
        return f"{whole}點" + " ".join(fraction)

    s = re.sub(
        r"(\d+\.\d+)%", lambda match: "百分之" + decimal_spoken(match.group(1)), s
    )

    def ratio_spoken(match):
        left, right = match.group(1), match.group(2)
        if "." in left:
            left = decimal_spoken(left)
        if "." in right:
            right = decimal_spoken(right)
        return f"{left}比{right}"

    s = re.sub(r"(\d+(?:\.\d+)?)\s*[:：]\s*(\d+(?:\.\d+)?)", ratio_spoken, s)
    s = re.sub(
        r"(?<![\d.])(\d+\.\d+)(?![\d.])",
        lambda match: decimal_spoken(match.group(1)),
        s,
    )
    return s


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
    spoken = re.sub(r"([，、])", r'\1<break time="90ms"/>', spoken)
    spoken = re.sub(r"([。！？!?])", r'\1<break time="200ms"/>', spoken)
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
