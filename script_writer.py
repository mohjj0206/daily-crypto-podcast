import os
import re
import json
import time
import market_data
import urllib.request
from itertools import permutations
from opencc import OpenCC


# ── 文字清理與格式工具 ──────────────────────────────────────────

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
    return re.sub(r"\bHODL\b", "長期持有", text, flags=re.I)


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

def call_gemini(prompt_txt):
    models = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
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


# ── DCA 績效指導語 ──────────────────────────────────────────

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


# ── 主流程：生成完整 AI 腳本 ──────────────────────────────────────────

def generate_script(mdata, dca_data, config):
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

    # 組裝新聞字串
    news_list = "\n\n".join(
        f"{index + 1}. 【{clean_slide_title(clean_txt(entry.get('title')))}】\n"
        f"發布時間：{published_at.strftime('%Y-%m-%d %H:%M')} CST\n"
        f"摘要：{clean_txt(entry.get('summary', '')).replace(chr(10), ' ')[:160]}..."
        for index, (published_at, entry) in enumerate(entries)
    )

    dca_report = dca_data.get("dca_report", "")
    dca_slide_body = dca_data.get("dca_slide_body", "")

    full_report = (
        f"【今日日期】：{today_str}\n【數據抓取時間】：{mdata['data_fetched_at']}\n"
        "【比特幣核心技術指標】\n"
        f"- BTC 即時報價：${cur_p} USD (24H: {'+' if btc_pct >= 0 else ''}{btc_pct:.2f}%)\n"
        f"- 短中期均線：100日 ${ma100d} | 200日 ${ma200d}\n"
        f"- 長期週線：100週 ${ma100w} | 200週 ${ma200w}\n"
        f"- 波段結構：{mdata['btc_technical_report']}\n"
        f"- ETH 波段結構：{mdata['eth_technical_report']}\n"
        f"- BTC RSI 歷史：{mdata['btc_oversold_report']}\n"
        f"- 全網穩定幣總市值變化：{mdata['stablecoin_report']}\n"
        f"- 大戶與全體帳戶持倉代理：{mdata['trader_position_report']}\n"
        f"- 近30日現貨成交量：{mdata['spot_volume_report']}\n"
        f"- 鏈上轉帳活躍度代理：{mdata['onchain_activity_report']}\n"
        f"- 恐慌、回撤與成交量交叉判讀：{mdata['chip_sentiment_report']}\n"
        f"- 市場恐慌與貪婪指數：{mdata['fear_report']}\n\n"
        f"{dca_report}\n\n【主流幣即時報價與RSI掃描】\n{mdata['coins_summary']}\n\n"
        f"【近36小時通過時效驗證的新聞】\n{news_list}"
    )

    dca_performance_state = ""
    dca_prompt_guidance = ""
    dca_slide_caption = ""
    if show_dca and dca_data:
        dca_portfolio = dca_data["portfolio"]
        dca_performance_state, dca_prompt_guidance, dca_slide_caption = (
            dca_performance_guidance(float(dca_portfolio["unrealized_pnl_twd"]))
        )

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

    part1_raw = normalize_long_term_terms(call_gemini(p1_prompt))
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
        f"均線守位｜BTC ${cur_p}、ETH ${spot_prices['ETH']:.2f} 能否守穩各自100日均線 ${ma100d}、${market_data.calc_sma(cdata['ETH'][0], 100)}。",
        "美股開盤連動｜觀察今日美股開盤後，BTC與ETH是否跟隨納指方向並伴隨成交量變化。",
        "短線清算風險｜留意急漲急跌時合約清算與資金費率是否擴大，避免槓桿部位遭短線掃損。",
    ]
    slide_24h_conditions, part2_raw = extract_slide_notes(
        part2_raw, "SLIDE_WEEK", default_24h_conditions
    )

    full_script = re.sub(r'^標題[：:].*\n?', '', part1_raw, flags=re.M) + "\n" + part2_raw

    return {
        "part1_raw": part1_raw,
        "part2_raw": part2_raw,
        "full_script": full_script,
        "ai_title": ai_title,
        "video_title": video_title,
        "slide_news_details": slide_news_details,
        "slide_news": slide_news,
        "slide_24h_conditions": slide_24h_conditions,
        "dca_slide_caption": dca_slide_caption,
        "dca_slide_body": dca_slide_body,
    }
