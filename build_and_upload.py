import os, re, json, time, calendar, base64, subprocess, urllib.request
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import feedparser
from PIL import Image, ImageDraw, ImageFont
from opencc import OpenCC
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

tw_tz = timezone(timedelta(hours=8))
today_str = datetime.now(tw_tz).strftime("%Y年%m月%d日")
two_days_ago = time.time() - 48 * 3600
is_test = os.environ.get("TEST_MODE", "").lower() == "true"
OUTRO_DURATION = 12.0
OUTRO_PAUSE = 1.5

def clean_txt(s):
    s = re.sub(r'(?:〈.*?〉)?這篇文章最早(?:發佈|出現)於.*', '', s or '')
    s = re.sub(r'(?:延伸閱讀|版權所有|copyright|©).*', '', s, flags=re.I)
    return re.sub(
        r'動區動趨|動區|BlockTempo|區塊客|blockcast\.it|Blockcast',
        '市場消息',
        s,
        flags=re.I,
    ).strip()

rss_urls = (
    "".join(["https", "://", "www.blocktempo.", "com", "/feed/"]),
    "".join(["https", "://", "blockcast.", "it", "/feed/"]),
)
source_entries = []
for rss_url in rss_urls:
    try:
        feed = feedparser.parse(rss_url)
        if feed.bozo:
            print(f"RSS feed error for '{rss_url}': {feed.bozo_exception}")
        source_entries.append(feed.entries)
    except Exception as e:
        print(f"Error fetching RSS feed '{rss_url}': {e}")
        source_entries.append([])

def published_timestamp(entry):
    published = entry.get("published_parsed")
    return calendar.timegm(published) if published else 0

recent_entries = [
    entry
    for entries_from_source in source_entries
    for entry in entries_from_source
    if not entry.get("published_parsed") or published_timestamp(entry) >= two_days_ago
]
entries = (
    [entry for entries_from_source in source_entries for entry in entries_from_source[:4]]
    if len(recent_entries) < 3
    else recent_entries
)
entries.sort(key=published_timestamp, reverse=True)
entries = entries[:10]

news_list = "\n\n".join([f"{i+1}. 【{clean_txt(e.get('title'))}】\n摘要：{clean_txt(e.get('summary', '')).replace(chr(10), ' ')[:160]}..." for i, e in enumerate(entries)])
top3_headlines = "\n".join([f"{i+1}. {clean_txt(e.get('title'))}" for i, e in enumerate(entries[:3])])

def fetch_json(u):
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))

fng_res = fetch_json("".join(["https", "://", "api.alternative.", "me", "/fng/?limit=2"]))
fng_list = fng_res.get("data", [])
today_fng = fng_list[0] if len(fng_list) > 0 else {"value": "13", "value_classification": "Extreme Fear"}
yest_fng = fng_list[1] if len(fng_list) > 1 else today_fng

vb = "".join(["https", "://", "data-api.binance.", "vision", "/api/v3/klines"])
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
syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "ZECUSDT"]
cdata = {}
for s in syms:
    lim = 210 if s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"] else 5
    raw = fetch_json(f"{vb}?symbol={s}&interval=1d&limit={lim}")
    cl = [float(k[4]) for k in raw]
    pct = ((cl[-1] - cl[-2]) / cl[-2]) * 100 if len(cl) >= 2 else 0.0
    cdata[s.replace("USDT", "")] = (cl, pct, calc_rsi(cl, 14) if lim == 210 else "")

btc_w_raw = fetch_json(f"{vb}?symbol=BTCUSDT&interval=1w&limit=210")
btc_w = [float(k[4]) for k in btc_w_raw]
btc_cl, btc_pct, btc_rsi = cdata["BTC"]
eth_cl, eth_pct, eth_rsi = cdata["ETH"]
cur_p = f"{btc_cl[-1]:.2f}"
ma100d, ma200d = calc_sma(btc_cl, 100), calc_sma(btc_cl, 200)
ma100w, ma200w = calc_sma(btc_w, 100), calc_sma(btc_w, 200)
eth_ma100d, eth_ma200d = calc_sma(eth_cl, 100), calc_sma(eth_cl, 200)

coins_lines = []
for name, (cl, pct, rsi) in cdata.items():
    sgn = "+" if pct >= 0 else ""
    r_txt = f" | RSI {rsi}" if rsi else ""
    coins_lines.append(f"{name}: ${cl[-1]:,.2f} ({sgn}{pct:.2f}%{r_txt})")
coins_summary = "\n".join(coins_lines)

full_report = f"【今日日期】：{today_str}\n【比特幣核心技術指標】\n- BTC 即時價格：${cur_p} USD (24H: {'+' if btc_pct>=0 else ''}{btc_pct:.2f}%)\n- 短中期均線：100日 ${ma100d} | 200日 ${ma200d}\n- 長期週線：100週 ${ma100w} | 200週 ${ma200w}\n- 市場恐慌與貪婪指數：今日 {today_fng['value']} ({today_fng['value_classification']}) / 昨日 {yest_fng['value']} ({yest_fng['value_classification']})\n\n【主流幣行情與RSI掃描】\n{coins_summary}\n\n【近兩日網路公開重點議題】\n{news_list}"

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

target_words = "約200字繁中（精簡測試版，僅保留3至4句重點對話）" if is_test else "嚴格要求 2,600 至 2,900 字繁中（兩段合計約 5,200 字以上，確保 1.2 倍速下整集片長仍達 15 至 18 分鐘）"
p1_length_guidance = (
    "測試模式只保留3至4句重點對話，不套用正式版各段字數配額。"
    if is_test else
    "正式版本段必須達2,600至2,900字：開場含一則50至65字風險提醒共200至250字；BTC四大均線（100日、200日、100週、200週）與恐慌貪婪指數800至900字；三大市場焦點各500至550字，逐一說明正面邏輯、風險折價與可驗證訊號；段落銜接100字。兩段合計至少5,200字，目標5,200至5,600字，禁止灌水或重複。"
)
p2_length_guidance = (
    "測試模式只保留3至4句重點對話，不套用正式版各段字數配額。"
    if is_test else
    "正式版本段必須達2,600至2,900字：六大主流幣報價、24H漲跌與14日RSI逐幣分析共1,050至1,200字；未來一週三個BTC/ETH觀察條件各450至500字，說明驗證價位、量能或情緒指標與多空含義；銜接與風險結尾200字。兩段合計至少5,200字，目標5,200至5,600字，禁止灌水或重複。"
)
p1_prompt = f"""你是財經播客【幣看趨勢】主編。請只寫前半段逐字稿，總長度{target_words}，{"測試模式合計約1分鐘內。" if is_test else "正式版整集兩段合計至少5,200字、以1.2倍速達15至18分鐘。"}
{full_report}
【角色】男聲為「比特汪」，熱情直率的柴犬主持人，自稱「本汪」；女聲為「以太喵」，冷靜傲嬌的橘貓主持人，自稱「本喵」。對話中必須互相稱呼對方名字「以太喵」與「比特汪」，禁止直接叫對方「狗狗」或「貓貓」。每談完一個數據或新聞，自然穿插兩位主持人互相吐槽及幣圈／財經冷笑話，並融入台灣網路梗，例如「這波操作真是謝囉」「歸剛欸」「我太難了」「這不是演習」「涼涼」；笑點要貼合內容、不生硬、不重複，報數字保持清晰。
【硬性禁令】禁止早安、午安、晚安、大家好等任何時間問候語；禁止「休息一下」「下一段」「後半段」「上半場」「下半場」「先到這裡」「接著看」「接下來」等分段或轉場字眼；禁止提及媒體名稱、禁止動作括號與Markdown；禁止重複同一個數字結論或同一句論點。
【篇幅配額】{p1_length_guidance}
【格式】第一行必須單獨輸出：標題：（結合今日數據的反直覺懸念爆款標題，32字內無引號）；其後逐字稿只能用「男：」「女：」開頭。逐字稿結束後，額外輸出恰好三行簡短圖卡摘要，每行分別使用「SLIDE_NEWS1:」「SLIDE_NEWS2:」「SLIDE_NEWS3:」開頭，每行濃縮一個焦點的利多與理性折價，不可包含媒體名稱。
【內容】自然開場後，女聲以太喵在開場白後用一句約50至65字、口語化且精簡有力的提醒，於12至15秒內說明節目僅分享公開資訊、不構成投資建議及加密貨幣高波動、請獨立思考並自負盈虧；不要長篇唸條文。接著分析BTC四大均線與恐慌貪婪指數，再逐一深入解析近兩日三大市場焦點。結尾銜接具體幣價，不說再見、不使用分段結尾。"""
part1_raw = call_gemini(p1_prompt)
def extract_slide_notes(raw, prefix, fallback_notes):
    notes = []
    for index, fallback in enumerate(fallback_notes, 1):
        match = re.search(rf'^{re.escape(prefix)}{index}:\s*(.+)$', raw, re.M)
        notes.append(match.group(1).strip() if match else fallback)
    raw = re.sub(rf'^{re.escape(prefix)}\d+:.*(?:\r?\n|$)', '', raw, flags=re.M)
    return notes, raw.rstrip()

news_fallbacks = [clean_txt(e.get("title", "今日市場焦點")) for e in entries[:3]]
while len(news_fallbacks) < 3:
    news_fallbacks.append("今日市場焦點")
slide_news, part1_raw = extract_slide_notes(part1_raw, "SLIDE_NEWS", news_fallbacks)
t_match = re.search(r'^標題[：:]\s*(.+)', part1_raw, re.M)
ai_title = t_match.group(1).strip().replace('「', '').replace('」', '') if t_match else f"BTC ${cur_p}｜主流幣均線攻防與恐慌指數全解析"
video_title = f"【幣看趨勢】{ai_title} ({today_str})"

p2_prompt = f"""你是【幣看趨勢】主編。請緊接前半段續寫後半段逐字稿，{target_words}。
【前段逐字稿】{part1_raw}
【前段結尾】{part1_raw[-350:]}
【數據】{full_report}
【角色】男聲為「比特汪」，自稱「本汪」；女聲為「以太喵」，自稱「本喵」。兩人對話必須互相稱呼對方名字「以太喵」與「比特汪」，禁止直接叫對方「狗狗」或「貓貓」。每談完一個數據或觀察條件，自然穿插互相吐槽、幣圈／財經冷笑話及台灣網路梗，例如「這波操作真是謝囉」「歸剛欸」「我太難了」「這不是演習」「涼涼」；保持自然、貼合情境、不生硬不重複，只用「男：」「女：」開頭，報數字清晰。
【硬性禁令】禁止早安、午安、晚安、大家好等任何時間問候語；禁止「休息一下」「下一段」「後半段」「上半場」「下半場」「先到這裡」「接著看」「接下來」等分段或轉場字眼；禁止提及媒體名稱、禁止動作括號與Markdown；禁止任何與前半段重複的開場、風險提醒、BTC均線結論、恐慌指數結論或市場焦點分析。
【篇幅配額】{p2_length_guidance}
【去重規則】以完整前段逐字稿核對；同一數據只報一次，不復述前段已解讀的多空觀點，不重複前段例子與結論。
【內容】1.點評六大主流幣即時行情、24H漲跌與14日RSI，不重複前段分析；2.提出未來一週三個具體BTC/ETH觀察條件。逐字稿後額外輸出恰好三行簡短圖卡摘要，分別以「SLIDE_WEEK1:」「SLIDE_WEEK2:」「SLIDE_WEEK3:」開頭，必須與逐字稿中的三個條件一一對應；這些標記行不是口播內容。結尾自然提醒控管倉位與風險並道別，不使用分段字眼。"""
part2_raw = call_gemini(p2_prompt)
default_weekly_conditions = [
    f"均線守位｜BTC ${cur_p}、ETH ${cdata['ETH'][0][-1]:.2f} 能否守穩各自100日均線 ${ma100d}、${calc_sma(cdata['ETH'][0], 100)}。",
    "量價確認｜突破關鍵價位時成交量是否同步放大，回測時能否守住支撐。",
    f"情緒風險｜恐慌貪婪指數由 {today_fng['value']} 變化時，是否出現情緒回暖或過熱訊號。",
]
slide_week, part2_raw = extract_slide_notes(part2_raw, "SLIDE_WEEK", default_weekly_conditions)

full_script = re.sub(r'^標題[：:].*\n?', '', part1_raw, flags=re.M) + "\n" + part2_raw
dialogues = []
for line in full_script.splitlines():
    m = re.match(r'^(男|女)[：:]\s*(.+)', line.strip())
    if m: dialogues.append((m.group(1), m.group(2).strip()))

tts_host = "".join(["https", "://", "texttospeech.", "googleapis.", "com"])
tts_url = f"{tts_host}/v1/text:synthesize?key={os.environ['GOOGLE_TTS_KEY'].strip()}"

def synth_one(item):
    idx, (role, txt) = item
    v_name = "cmn-TW-Neural2-B" if role == "男" else "cmn-TW-Neural2-A"
    fallback_voice = "cmn-TW-Wavenet-B" if role == "男" else "cmn-TW-Wavenet-A"
    pitch = -1.5 if role == "男" else 1.5
    clean_say = re.sub(r'[*#\[\]()]', '', txt)

    def request_tts(voice_name):
        payload_tts = json.dumps({
            "input": {"text": clean_say},
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

    fn = f"seg_{idx:04d}.mp3"
    with open(fn, "wb") as f: f.write(base64.b64decode(res["audioContent"]))
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", fn]).decode().strip())
    return idx, fn, txt, dur

with ThreadPoolExecutor(max_workers=5) as ex:
    results = list(ex.map(synth_one, enumerate(dialogues)))
results.sort(key=lambda x: x[0])

def create_outro_audio():
    if os.path.exists("info.mp3"):
        audio_filter = (
            f"volume=0.35,afade=t=in:st=0:d=1.5,"
            f"afade=t=out:st={OUTRO_DURATION - 2.5:g}:d=2.5,apad=whole_dur={OUTRO_DURATION:g}"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", "info.mp3", "-t", f"{OUTRO_DURATION:g}",
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
intro_dur = 0.0
if os.path.exists("info.mp3"):
    intro_audio_filter = "afade=t=out:st=13.5:d=1.5"
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

ass_head = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Noto Sans CJK TC,44,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,3,3,0,2,27,27,42,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
kw_pat = r'(\$[\d,.]+|\b\d+(?:,\d+)*(?:\.\d+)?%?\b|比特幣|以太幣|以太坊|BTC|ETH|SOL|XRP|ADA|ZEC|RSI|均線|恐慌與貪婪指數|超賣|超買|支撐|壓力|風險提醒|投資建議)'
punct_pat = r'[，。！？、；：「」『』（）【】《》—…!?;:\(\)\[\]]+|(?<!\d)[,\.](?!\d)'
cur_t, evs = intro_dur, []
for _, _, txt, dur in results:
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
        evs.append(f"Dialogue: 0,{fmt_ass_t(segment_cur)},{fmt_ass_t(sub_end)},Default,,0,0,0,,{hl}")
        segment_cur = sub_end

    cur_t += dur
with open("subs.ass", "w", encoding="utf-8") as f:
    f.write(ass_head + "\n".join(evs))

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.8, 2.2), dpi=100)
fig.patch.set_facecolor("#111827")
for ax, name, col in [(ax1, "BTC", "#38bdf8"), (ax2, "ETH", "#a78bfa")]:
    cl = cdata[name][0][-100:]
    ma = [sum(cl[max(0, i-19):i+1])/len(cl[max(0, i-19):i+1]) for i in range(len(cl))]
    ax.set_facecolor("#0f172a")
    ax.plot(cl, color=col, linewidth=2.2, label=f"{name} Close")
    ax.plot(ma, color="#fbbf24", linewidth=1.4, linestyle="--", label="20D MA")
    ax.set_title(f"{name} 100D Trend", color="white", fontsize=10)
    ax.tick_params(colors="#94a3b8", labelsize=8)
    ax.grid(color="#1e293b", linewidth=0.6)
    ax.legend(facecolor="#1e293b", edgecolor="none", labelcolor="white", fontsize=8)
    for sp in ["top", "right"]: ax.spines[sp].set_visible(False)
    for sp in ["bottom", "left"]: ax.spines[sp].set_color("#334155")
plt.tight_layout()
plt.savefig("chart_dual.png", facecolor="#111827")
plt.close()

fig, ax = plt.subplots(figsize=(10.8, 2.2), dpi=100)
fig.patch.set_facecolor("#111827")
ax.set_facecolor("#0f172a")
names = list(cdata.keys())
pcts = [cdata[n][1] for n in names]
ax.barh(names[::-1], pcts[::-1], color=["#34d399" if p >= 0 else "#f43f5e" for p in pcts][::-1], height=0.55)
ax.axvline(0, color="#64748b", linewidth=1)
ax.set_title("24H Price Change (%)", color="white", fontsize=10)
ax.tick_params(colors="white", labelsize=9)
ax.grid(axis="x", color="#1e293b", linewidth=0.6)
for sp in ["top", "right"]: ax.spines[sp].set_visible(False)
for sp in ["bottom", "left"]: ax.spines[sp].set_color("#334155")
plt.tight_layout()
plt.savefig("chart_bars.png", facecolor="#111827")
plt.close()
fp = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
if not os.path.exists(fp): fp = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
fh, fs, ft, fb, fn = ImageFont.truetype(fp, 40), ImageFont.truetype(fp, 24), ImageFont.truetype(fp, 34), ImageFont.truetype(fp, 28), ImageFont.truetype(fp, 22)

if has_intro_video:
    intro_frame = Image.new("RGB", (1280, 720), "#090d16")
    intro_draw = ImageDraw.Draw(intro_frame)
    intro_draw.rectangle((20, 20, 1260, 700), outline="#fbbf24", width=4)
    intro_draw.rectangle((404, 49, 876, 521), outline="#fbbf24", width=4)
    intro_draw.line((40, 40, 180, 40), fill="#38bdf8", width=2)
    intro_draw.line((1100, 680, 1240, 680), fill="#38bdf8", width=2)
    intro_draw.text(
        (640, 558),
        "幣看趨勢｜比特幣與加密貨幣情報",
        fill="#f8fafc",
        font=fh,
        anchor="mm",
    )
    intro_draw.text(
        (640, 612),
        f"以太喵 ＆ 比特汪・每日市場深度解析（{today_str}）",
        fill="#fbbf24",
        font=ft,
        anchor="mm",
    )
    intro_frame.save("intro_frame.png")

s1 = f"BTC 即時價格：${cur_p} USD\n日線均線：100日 ${ma100d} / 200日 ${ma200d}\n週線均線：100週 ${ma100w} / 200週 ${ma200w}\n14日 RSI：{btc_rsi}"
eth_slide = f"ETH 即時價格：${eth_cl[-1]:,.2f} USD ({eth_pct:+.2f}% 24H)\n日線均線：100日 ${eth_ma100d} / 200日 ${eth_ma200d}\n14日 RSI：{eth_rsi}"
t3 = [x.strip() for x in cc.convert(top3_headlines).split("\n") if x.strip()]
while len(t3) < 3:
    t3.append(f"市場焦點 {len(t3) + 1}")
fng_num = int(re.search(r'\d+', str(today_fng['value'])).group()) if re.search(r'\d+', str(today_fng['value'])) else 15
fng_yesterday = int(re.search(r'\d+', str(yest_fng['value'])).group()) if re.search(r'\d+', str(yest_fng['value'])) else fng_num
fng_change = fng_num - fng_yesterday
fng_direction = "升高" if fng_change > 0 else "降低" if fng_change < 0 else "持平"
sentiment_slide = f"今日：{fng_num}（{today_fng['value_classification']}）\n昨日：{fng_yesterday}，{fng_direction} {abs(fng_change)} 點"
weekly_conditions = slide_week

news_slides = [
    (
        f"市場焦點 {index + 1}｜{t3[index]}",
        "#fbbf24",
        f"多空打折摘要\n{slide_news[index]}",
        None,
    )
    for index in range(3)
]
condition_slides = [
    (f"未來一週觀察條件 {index + 1}", "#f472b6", condition, "focus")
    for index, condition in enumerate(weekly_conditions)
]
slides_cfg = [
    ("開場｜盤勢觀察與投資風險提醒", "#fb7185", "本節目內容為公開資訊整理，不構成投資建議。\n加密貨幣波動劇烈，請獨立思考並自負風險。", None),
    ("市場情緒｜恐慌與貪婪指數", "#38bdf8", sentiment_slide, "gauge"),
    ("BTC 動態｜四大均線與 RSI", "#38bdf8", s1, "dual"),
    ("ETH 動態｜日線均線與 RSI", "#a78bfa", eth_slide, "dual"),
] + news_slides + [
    ("BTC 與 ETH｜百日走勢對照", "#a78bfa", "價格走勢與20日均線動態比較", "dual"),
    ("六大主流幣｜報價與指標總表", "#34d399", cc.convert(coins_summary), None),
    ("六大主流幣｜24H 漲跌幅", "#34d399", "即時報價、漲跌幅與 RSI 對照", "bars"),
] + condition_slides + [
    ("本週策略｜先驗證、再行動", "#fbbf24", "核對價格與均線、量能結構及市場情緒。\n依可驗證訊號控管部位，避免追高殺低。", "focus"),
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

def create_outro_frame():
    image = Image.new("RGB", (1280, 720), "#090d16")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 1260, 700), outline="#fbbf24", width=4)
    if os.path.exists("avatar.png"):
        with Image.open("avatar.png") as avatar:
            image.paste(avatar.convert("RGB").resize((260, 260), Image.LANCZOS), (510, 30))

    draw = ImageDraw.Draw(image)
    title = "感謝收聽【幣看趨勢】｜別忘了按讚、訂閱、開啟小鈴鐺！"
    title_font = ImageFont.truetype(fp, 40)
    title_lines = wrap_by_width(draw, title, title_font, 1160)
    for line_index, line in enumerate(title_lines):
        bounds = draw.textbbox((0, 0), line, font=title_font)
        draw.text(
            ((1280 - (bounds[2] - bounds[0])) // 2, 315 + line_index * 50),
            line,
            fill="#f8fafc",
            font=title_font,
        )
    subtitle = "留言告訴以太喵與比特汪，下一集你想看哪一顆幣？"
    subtitle_font = ImageFont.truetype(fp, 28)
    subtitle_lines = wrap_by_width(draw, subtitle, subtitle_font, 1160)
    for line_index, line in enumerate(subtitle_lines):
        bounds = draw.textbbox((0, 0), line, font=subtitle_font)
        draw.text(
            ((1280 - (bounds[2] - bounds[0])) // 2, 420 + line_index * 38),
            line,
            fill="#fbbf24",
            font=subtitle_font,
        )

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((60, 510, 600, 680), fill=(5, 8, 18, 185), outline=(251, 191, 36, 210), width=3)
    overlay_draw.rectangle((680, 510, 1220, 680), fill=(5, 8, 18, 185), outline=(56, 189, 248, 210), width=3)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    image.save("outro_frame.png")


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

for idx, (title, color, body, ctype) in enumerate(slides_cfg, 1):
    img = Image.new("RGB", (1280, 720), "#090d16")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1280, 82), fill="#1e293b")
    d.text((40, 22), f"幣看趨勢｜比特幣與加密貨幣情報  ({today_str})", fill="#ffffff", font=fs)
    d.rectangle((60, 115, 1220, 610), fill="#111827", outline="#fbbf24", width=4)

    content_bottom = 350 if ctype in ("dual", "bars") else 395 if ctype == "gauge" else 590
    title_font = ft
    title_lines = wrap_by_width(d, title, title_font, 1080)
    title_y = 135
    while title_y + len(title_lines) * 44 + 10 > content_bottom - 40 and title_font.size > 22:
        title_font = ImageFont.truetype(fp, title_font.size - 2)
        title_lines = wrap_by_width(d, title, title_font, 1080)
    if title_y + len(title_lines) * 44 + 10 > content_bottom - 40:
        raise ValueError(f"Slide {idx} title does not fit within the slide frame.")
    for line in title_lines:
        d.text((100, title_y), line, fill=color, font=title_font)
        title_y += 44

    body_y = title_y + 10
    body_font = ft if ctype == "focus" else fb
    body_lines = []
    for paragraph in body.split("\n"):
        body_lines.extend(wrap_by_width(d, paragraph, body_font, 1080))
    body_line_height = min(40, body_font.size + 12)
    while body_y + len(body_lines) * body_line_height > content_bottom and body_font.size > 14:
        body_font = ImageFont.truetype(fp, body_font.size - 2)
        body_lines = []
        for paragraph in body.split("\n"):
            body_lines.extend(wrap_by_width(d, paragraph, body_font, 1080))
        body_line_height = min(40, body_font.size + 12)
    if body_y + len(body_lines) * body_line_height > content_bottom:
        raise ValueError(f"Slide {idx} text does not fit within the slide frame.")
    for line in body_lines:
        d.text((100, body_y), line, fill="#f8fafc", font=body_font)
        body_y += body_line_height

    if ctype == "gauge":
        gauge_y = max(body_y + 46, 430)
        d.text((100, gauge_y - 34), f"市場恐懼與貪婪指數溫度計：{fng_num} / 100", fill="#fbbf24", font=fb)
        for gx, gc in enumerate(["#ef4444", "#f97316", "#eab308", "#84cc16", "#22c55e"]):
            d.rectangle((100 + gx * 216, gauge_y, 100 + (gx + 1) * 216 - 4, gauge_y + 36), fill=gc)
        px = 100 + int(min(100, max(0, fng_num)) * 10.4)
        d.polygon([(px, gauge_y + 41), (px - 12, gauge_y + 65), (px + 12, gauge_y + 65)], fill="#ffffff")
    elif ctype in ("bars", "dual"):
        chart_y = max(body_y + 8, 360)
        if chart_y + 220 > 595:
            raise ValueError(f"Slide {idx} chart does not fit within the slide frame.")
        chart_file = "chart_bars.png" if ctype == "bars" else "chart_dual.png"
        chart = Image.open(chart_file).convert("RGB").resize((1080, 220), Image.LANCZOS)
        img.paste(chart, (100, chart_y))

    footer = "※ 本節目所有內容皆為網路公開資訊與市場數據整理分享，不構成任何投資建議，投資請獨立思考並自負風險。"
    footer_font = ImageFont.truetype(fp, 14)
    footer_lines = wrap_by_width(d, footer, footer_font, 1200)
    for footer_index, line in enumerate(footer_lines):
        d.text((40, 684 + footer_index * 16), line, fill="#64748b", font=footer_font)
    img.save(f"slide{idx}.png")

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
        "scale=440:440:force_original_aspect_ratio=decrease,"
        "pad=440:440:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        "split=2[forward][backward];"
        "[backward]reverse,setpts=PTS-STARTPTS[reversed];"
        f"[forward][reversed]concat=n=2:v=1:a=0,"
        f"loop=loop=-1:size={boomerang_frames}:start=0,"
        "fps=24,trim=duration=15,setpts=PTS-STARTPTS[animation];"
        "[0:v][animation]overlay=420:65:shortest=1,"
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
slide_count = len(slides_cfg)
slideshow_duration = cur_t - intro_dur if has_intro_video else cur_t
intro_video_duration = 15.0 if has_intro_video else 0.0
seg = slideshow_duration / slide_count
with open("list.txt", "w", encoding="utf-8") as f:
    if has_intro_video:
        f.write("file 'intro.mp4'\n")
    for i in range(1, slide_count + 1):
        dur = seg + (OUTRO_PAUSE if i == slide_count else 0)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-framerate", "24", "-t", f"{dur:.2f}", "-i", f"slide{i}.png", "-c:v", "libx264", "-tune", "stillimage", "-preset", "ultrafast", "-pix_fmt", "yuv420p", f"p{i}.mp4"], check=True)
        f.write(f"file 'p{i}.mp4'\n")
    f.write("file 'outro.mp4'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "base.mp4"], check=True)
subprocess.run(["ffmpeg", "-y", "-i", "base.mp4", "-i", "podcast.mp3", "-vf", "ass=subs.ass", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-shortest", "output.mp4"], check=True)

actual_video_duration = intro_video_duration + slideshow_duration + OUTRO_PAUSE + OUTRO_DURATION
def fmt_chapter(sec):
    sec = max(0, min(int(round(sec)), int(round(actual_video_duration))))
    minutes, seconds = divmod(sec, 60)
    return f"{minutes:02d}:{seconds:02d}"

chapter_indexes = [0]
chapter_entries = [(0.0, "主題動畫片頭")] if has_intro_video else []
chapter_offset = intro_video_duration
for index in range(1, slide_count):
    if round(seg * index) - round(seg * chapter_indexes[-1]) >= 10:
        chapter_indexes.append(index)
for index in chapter_indexes:
    chapter_entries.append((chapter_offset + seg * index, slides_cfg[index][0]))
chapter_entries.append(
    (intro_video_duration + slideshow_duration + OUTRO_PAUSE, "片尾互動與推薦影片")
)
chapter_times = [fmt_chapter(start) for start, _ in chapter_entries]
chapter_lines = "\n".join(
    f"{chapter_times[position]} {title}"
    for position, (_, title) in enumerate(chapter_entries)
)

v_desc = f"歡迎收聽【幣看趨勢｜比特幣與加密貨幣情報】！本集為您彙整 {today_str} 最新市場指標與網路公開重點議題，由「以太喵」與「比特汪」帶你透過多空理性打折視角掌握幣圈脈動！\n\n⏱️ 本集簡報章節（依14張圖卡時間對齊，含12秒動態片尾）：\n{chapter_lines}\n\n📊 BTC 核心數據：\n{s1}\n\n📊 ETH 核心數據：\n{eth_slide}\n\n⚠️ 免責聲明：\n本頻道所有內容均為網路公開資訊與市場數據整理分享，不構成任何形式之投資建議或買賣邀約。加密貨幣市場波動劇烈，投資前請務必自行研究並審慎評估風險。\n\n#比特幣 #加密貨幣 #區塊鏈 #BTC #ETH #SOL #技術分析 #均線 #RSI #恐慌指數 #幣看趨勢"

creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(), token_uri="".join(["https", "://", "oauth2.", "googleapis.", "com", "/token"]), client_id=os.environ["YT_CLIENT_ID"].strip(), client_secret=os.environ["YT_CLIENT_SECRET"].strip())
youtube = build("youtube", "v3", credentials=creds)
req = youtube.videos().insert(part="snippet,status", body={"snippet": {"title": video_title[:95], "description": v_desc, "tags": ["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"], "categoryId": "27"}, "status": {"privacyStatus": "unlisted"}}, media_body=MediaFileUpload("output.mp4", chunksize=1024*1024*5, resumable=True))
res = None
while res is None: _, res = req.next_chunk()
print("Uploaded Video ID:", res["id"])

t_img = Image.new("RGB", (1280, 720), "#090d16")
td = ImageDraw.Draw(t_img)
td.rectangle((0, 0, 1280, 720), outline="#fbbf24", width=12)
td.rectangle((45, 45, 520, 110), fill="#ef4444")
td.text((68, 56), "幣看趨勢｜每日深度財經情報", fill="#ffffff", font=ImageFont.truetype(fp, 32))
raw_t = re.sub(r'^【幣看趨勢】|\([^)]+\)$', '', video_title).strip()

# 封面主標題：依實際像素寬度自動縮放並換行，避免超出安全區或遮住右側角色。
def split_thumb_title(text, font, draw, max_width, max_lines=3):
    text = re.sub(r'\s+', '', text).strip()
    if not text:
        return []

    preferred_breaks = ('！', '？', '：', '｜', '｜', '，', '、')
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

    if len(lines) > max_lines:
        merged = ' '.join(lines)
        lines = []
        current = ''
        for char in merged.replace(' ', ''):
            trial = current + char
            if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
                current = trial
            elif current:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
        while len(lines) > max_lines:
            lines[max_lines - 1] += lines.pop()

    return lines[:max_lines]

thumb_font = None
thumb_lines = []
thumb_max_width = 720
for thumb_size in range(58, 43, -1):
    candidate_font = ImageFont.truetype(fp, thumb_size)
    candidate_lines = split_thumb_title(raw_t, candidate_font, td, thumb_max_width)
    widest = max((td.textbbox((0, 0), line, font=candidate_font)[2] for line in candidate_lines), default=0)
    if len(candidate_lines) <= 3 and widest <= thumb_max_width:
        thumb_font = candidate_font
        thumb_lines = candidate_lines
        break
if thumb_font is None:
    thumb_font = ImageFont.truetype(fp, 44)
    thumb_lines = split_thumb_title(raw_t, thumb_font, td, thumb_max_width)

line_count = max(1, len(thumb_lines))
line_height = 85 if line_count >= 3 else 78
red_y = 155 if line_count == 3 else (175 if line_count == 2 else 205)
red_height = 110 if line_count == 1 else (line_count * 85 + 15)
red_bottom = min(435, red_y + red_height)
text_block_height = line_count * line_height
text_y = red_y + max(15, (red_height - text_block_height) // 2)

td.rectangle((45, red_y - 10, 760, red_bottom), fill="#dc2626")
for line_index, line in enumerate(thumb_lines):
    line_width = td.textbbox((0, 0), line, font=thumb_font)[2]
    line_x = max(50, min(770 - line_width, 50 + (720 - line_width) // 2))
    td.text((line_x, text_y + line_index * line_height), line, fill="#ffffff", font=thumb_font)

td.text((50, 480), "・BTC / ETH / SOL / XRP 多空均線與 RSI 掃描", fill="#38bdf8", font=ImageFont.truetype(fp, 30))
td.text((50, 540), "・拒絕盲目跟風，理性拆解市場數據與風險", fill="#cbd5e1", font=ImageFont.truetype(fp, 30))
if os.path.exists("avatar.png"): t_img.paste(Image.open("avatar.png").resize((430, 430)), (800, 145))

t_img.save("thumb.jpg", "JPEG", quality=92)
youtube.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload("thumb.jpg")).execute()
print("Custom thumbnail uploaded successfully!")
