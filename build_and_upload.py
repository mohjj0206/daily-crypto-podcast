import os, re, json, time, base64, subprocess, urllib.request
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

def clean_txt(s):
    s = re.sub(r'〈.*?〉這篇文章最早發佈於.*', '', s or '')
    return re.sub(r'動區動趨|動區|BlockTempo', '市場消息', s, flags=re.I).strip()

rss_url = "".join(["https", "://", "www.blocktempo.", "com", "/feed/"])
feed = feedparser.parse(rss_url)
entries = [e for e in feed.entries if not hasattr(e, "published_parsed") or time.mktime(e.published_parsed) >= two_days_ago]
if len(entries) < 3: entries = feed.entries[:6]
else: entries = entries[:10]

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
cur_p = f"{btc_cl[-1]:.2f}"
ma100d, ma200d = calc_sma(btc_cl, 100), calc_sma(btc_cl, 200)
ma100w, ma200w = calc_sma(btc_w, 100), calc_sma(btc_w, 200)

coins_lines = []
for name, (cl, pct, rsi) in cdata.items():
    sgn = "+" if pct >= 0 else ""
    r_txt = f" | RSI {rsi}" if rsi else ""
    coins_lines.append(f"{name}: ${cl[-1]} ({sgn}{pct:.2f}%{r_txt})")
coins_summary = "\n".join(coins_lines)

full_report = f"【今日日期】：{today_str}\n【比特幣核心技術指標】\n- BTC 即時價格：${cur_p} USD (24H: {'+' if btc_pct>=0 else ''}{btc_pct:.2f}%)\n- 短中期均線：100日 ${ma100d} | 200日 ${ma200d}\n- 長期週線：100週 ${ma100w} | 200週 ${ma200w}\n- 市場恐慌與貪婪指數：今日 {today_fng['value']} ({today_fng['value_classification']}) / 昨日 {yest_fng['value']} ({yest_fng['value_classification']})\n\n【主流幣行情與RSI掃描】\n{coins_summary}\n\n【近兩日網路公開重點議題】\n{news_list}"

def call_gemini(prompt_txt):
    models = ["gemini-3.8-flash"]
    custom_model = os.environ.get("GEMINI_MODEL", "").strip()
    if custom_model:
        models = [custom_model] + [m for m in models if m != custom_model]

    last_err = None
    for model in models:
        g_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.environ['GEMINI_API_KEY'].strip()}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt_txt}]}]}).encode("utf-8")
        for attempt in range(3):
            req = urllib.request.Request(g_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                res = json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))
                return res["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="ignore")
                print(f"Gemini API Error ({e.code}) for model '{model}': {err_msg}")
                last_err = e
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(15 * (attempt + 1))
                    continue
                break
            except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
                print(f"Gemini network timeout/error for model '{model}' (attempt {attempt + 1}/3): {e}")
                last_err = e
                if attempt < 2:
                    time.sleep(15 * (attempt + 1))
                    continue
                break
            except Exception as e:
                print(f"Error calling Gemini model '{model}': {e}")
                last_err = e
                break

    if last_err:
        raise last_err
    raise RuntimeError("No Gemini models available.")

p1_prompt = f"""你是財經播客【幣看趨勢】主編。根據數據寫前半段逐字稿（約2200字繁中）。\n{full_report}\n【角色】「男：」熱情直率柴犬；「女：」冷靜傲嬌橘貓。互動自然融入台灣貓語狗語諧音梗（如他喵的、喵頭不對、本喵、汪嗚、本汪、別汪想），報數字保持清晰。禁提媒體名稱、禁提上下半場分段字眼、禁加動作括號與Markdown。\n【格式】第一行必須單獨輸出：標題：（結合今日數據的反直覺懸念爆款標題，32字內無引號）\n第二行起只能用「男：」「女：」開頭。內容含：反直覺開場、女聲宣讀完整風險提醒（非投資建議、波動劇烈請獨立思考風險自負）、BTC均線與恐慌指數体檢、三大焦點正反理性打折解析。結尾自然過渡到看具體幣價（不說再見）。"""
part1_raw = call_gemini(p1_prompt)
t_match = re.search(r'^標題[：:]\s*(.+)', part1_raw, re.M)
ai_title = t_match.group(1).strip().replace('「', '').replace('」', '') if t_match else f"BTC ${cur_p}｜主流幣均線攻防與恐慌指數全解析"
video_title = f"【幣看趨勢】{ai_title} ({today_str})"

p2_prompt = f"""你是【幣看趨勢】主編。緊接以下對話寫後半段（約2200字繁中），保持一貓一狗性格與諧音梗，無縫接軌禁提分段字眼與媒體名稱，只用「男：」「女：」開頭。\n【前段結尾】{part1_raw[-350:]}\n【數據】{full_report}\n【內容】1.點評六大主流幣即時行情、24H漲跌與14日RSI超賣掃描。2.只針對BTC與ETH做多空策略分析，提出未來一週必須盯緊的三個具體觀察條件。結尾提醒控管倉位與風險並道別。"""
part2_raw = call_gemini(p2_prompt)

full_script = re.sub(r'^標題[：:].*\n?', '', part1_raw, flags=re.M) + "\n" + part2_raw
dialogues = []
for line in full_script.splitlines():
    m = re.match(r'^(男|女)[：:]\s*(.+)', line.strip())
    if m: dialogues.append((m.group(1), m.group(2).strip()))

tts_host = "".join(["https", "://", "texttospeech.", "googleapis.", "com"])
tts_url = f"{tts_host}/v1/text:synthesize?key={os.environ['GOOGLE_TTS_KEY'].strip()}"

def synth_one(item):
    idx, (role, txt) = item
    v_name = "cmn-TW-Standard-B" if role == "男" else "cmn-TW-Wavenet-A"
    clean_say = re.sub(r'[*#\[\]()]', '', txt)
    payload_tts = json.dumps({"input": {"text": clean_say}, "voice": {"languageCode": "cmn-TW", "name": v_name}, "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.05}}).encode("utf-8")
    req = urllib.request.Request(tts_url, data=payload_tts, headers={"Content-Type": "application/json"}, method="POST")
    res = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    fn = f"seg_{idx:04d}.mp3"
    with open(fn, "wb") as f: f.write(base64.b64decode(res["audioContent"]))
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", fn]).decode().strip())
    return idx, fn, txt, dur

with ThreadPoolExecutor(max_workers=5) as ex:
    results = list(ex.map(synth_one, enumerate(dialogues)))
results.sort(key=lambda x: x[0])

with open("audio_list.txt", "w", encoding="utf-8") as f:
    for _, fn, _, _ in results: f.write(f"file '{fn}'\n")
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "audio_list.txt", "-c", "copy", "podcast.mp3"], check=True)
cc = OpenCC("s2twp")
def fmt_ass_t(sec):
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"

ass_head = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Noto Sans CJK TC,22,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,3,1,0,2,27,27,24,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
kw_pat = r'(\$[\d,.]+|\b\d+(?:,\d+)*(?:\.\d+)?%?\b|比特幣|以太幣|以太坊|BTC|ETH|SOL|XRP|ADA|ZEC|RSI|均線|恐慌與貪婪指數|超賣|超買|支撐|壓力|風險提醒|投資建議)'
punct_pat = r'[，。！？、；：「」『』（）【】《》—…!?;:\(\)\[\]]+|(?<!\d)[,\.](?!\d)'
cur_t, evs = 0.0, []
for _, _, txt, dur in results:
    tw_txt = cc.convert(txt)
    no_p = re.sub(r'\s+', ' ', re.sub(punct_pat, ' ', tw_txt)).strip()
    hl = re.sub(kw_pat, r'{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H00FFFFFF&}', no_p)
    evs.append(f"Dialogue: 0,{fmt_ass_t(cur_t)},{fmt_ass_t(cur_t+dur)},Default,,0,0,0,,{hl}")
    cur_t += dur
with open("subs.ass", "w", encoding="utf-8") as f:
    f.write(ass_head + "\n".join(evs))

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 2.7), dpi=100)
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

fig, ax = plt.subplots(figsize=(9.4, 2.7), dpi=100)
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

s1 = f"BTC 即時價格：${cur_p} USD\n恐慌與貪婪指數：{today_fng['value']} ({today_fng['value_classification']})\n短中期均線：100日 ${ma100d} / 200日 ${ma200d}\n長期週線：100週 ${ma100w} / 200週 ${ma200w}\n14日 RSI 動能指標：{btc_rsi}"
t3 = [x.strip() for x in cc.convert(top3_headlines).split("\n") if x.strip()]
fng_num = int(re.search(r'\d+', str(today_fng['value'])).group()) if re.search(r'\d+', str(today_fng['value'])) else 15
s4 = "1. 拋壓與槓桿清算動能是否出現枯竭訊號\n2. 關鍵均線與整數心理關卡能否帶量站穩\n3. 市場恐慌情緒與宏觀資金面何時迎來拐點"

slides_cfg = [
    ("開場｜盤勢觀察與投資風險提醒", "#fb7185", "・本節目所有內容皆為網路公開資訊與數據整理\n・純屬個人討論交流，絕對不構成任何投資建議\n・加密貨幣波動劇烈，請務必獨立思考風險自負", "gauge"),
    ("第一段｜BTC 技術體檢與情緒溫度計", "#38bdf8", s1, "gauge"),
    ("第二段｜市場焦點深度解析（議題一）", "#fbbf24", f"{t3[0] if len(t3)>0 else '焦點一'}\n\n【多頭利多邏輯 vs 冷靜理性打折】\n・檢視資金敘事背後的真實基本面支撐\n・評估宏觀利率與監管環境的潛在風險", None),
    ("第二段｜市場焦點深度解析（議題二）", "#fbbf24", f"{t3[1] if len(t3)>1 else '焦點二'}\n\n【多頭利多邏輯 vs 冷靜理性打折】\n・區分短期情緒炒作與長期結構性改變\n・留意市場流動性是否足以支撐高估值", None),
    ("第二段｜市場焦點深度解析（議題三）", "#fbbf24", f"{t3[2] if len(t3)>2 else '焦點三'}\n\n【多頭利多邏輯 vs 冷靜理性打折】\n・追蹤鏈上真實資金流向與籌碼換手訊號\n・避免在消息面利多出盡時盲目追高", None),
    ("第三段｜六大主流幣 24H 漲跌與 RSI 掃描", "#34d399", cc.convert(coins_summary), "bars"),
    ("第三段｜BTC 與 ETH 雙龍頭 100日均線對比", "#a78bfa", "・比較 BTC 與 ETH 近期價格與 20日均線乖離\n・觀察龍頭幣種何時出現止跌築底與量能回溫", "dual"),
    ("第四段｜BTC 與 ETH 必須盯緊的三個條件", "#f472b6", s4, None)
]

for idx, (title, color, body, ctype) in enumerate(slides_cfg, 1):
    img = Image.new("RGB", (1920, 1080), "#090d16")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1920, 105), fill="#1e293b")
    d.text((60, 28), f"幣看趨勢｜比特幣與加密貨幣情報  ({today_str})", fill="#ffffff", font=fh)
    d.rectangle((60, 135, 760, 825), fill="#111827", outline="#38bdf8", width=3)
    d.text((195, 165), "幣看趨勢・雙人財經播客", fill="#38bdf8", font=ft)
    if os.path.exists("avatar.png"): img.paste(Image.open("avatar.png").resize((480, 480)), (170, 260))
    d.rectangle((800, 135, 1860, 825), fill="#111827", outline=color, width=4)
    d.text((845, 175), title, fill=color, font=ft)
    d.line(((845, 235), (1815, 235)), fill="#334155", width=2)
    d.multiline_text((845, 260), body, fill="#f8fafc", font=fb, spacing=18)
    if ctype == "gauge":
        d.text((845, 580), f"市場恐懼與貪婪指數溫度計：{fng_num} / 100", fill="#fbbf24", font=fb)
        for gx, gc in enumerate(["#ef4444", "#f97316", "#eab308", "#84cc16", "#22c55e"]): d.rectangle((845 + gx * 190, 635, 845 + (gx + 1) * 190 - 4, 675), fill=gc)
        px = 845 + int(min(100, max(0, fng_num)) * 9.48)
        d.polygon([(px, 680), (px - 16, 712), (px + 16, 712)], fill="#ffffff")
    elif ctype == "bars": img.paste(Image.open("chart_bars.png").convert("RGB"), (845, 520))
    elif ctype == "dual": img.paste(Image.open("chart_dual.png").convert("RGB"), (845, 520))
    d.text((60, 1035), "※ 本節目所有內容皆為網路公開資訊與市場數據整理分享，不構成任何投資建議，投資請獨立思考並自負風險。", fill="#64748b", font=fn)
    img = img.resize((1280, 720), Image.LANCZOS)
    img.save(f"slide{idx}.png")
seg = cur_t / 8.0
with open("list.txt", "w", encoding="utf-8") as f:
    for i in range(1, 9):
        dur = seg if i < 8 else (seg + 30)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-framerate", "2", "-t", f"{dur:.2f}", "-i", f"slide{i}.png", "-c:v", "libx264", "-tune", "stillimage", "-preset", "ultrafast", "-pix_fmt", "yuv420p", f"p{i}.mp4"], check=True)
        f.write(f"file 'p{i}.mp4'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "base.mp4"], check=True)
subprocess.run(["ffmpeg", "-y", "-i", "base.mp4", "-i", "podcast.mp3", "-vf", "ass=subs.ass", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-shortest", "output.mp4"], check=True)

v_desc = f"歡迎收聽【幣看趨勢｜比特幣與加密貨幣情報】！本集由 AI 數據引擎整理 {today_str} 最新市場指標與網路公開重點議題，帶你透過「多空理性打折」視角掌握幣圈脈動！\n\n⏱️ 本集節目章節（點擊時間軸快速跳轉）：\n00:00 反直覺盤勢開場與投資風險提醒\n01:30 市場體檢：BTC 均線攻防與恐慌貪婪指數溫度計\n05:30 近兩日三大市場焦點深度解析與理性打折\n11:30 六大主流幣（BTC/ETH/SOL/XRP/ADA/ZEC）行情與 RSI 掃描\n17:30 BTC 與 ETH 多空觀察策略：必須盯緊的三個條件\n\n📊 本集核心數據：\n{s1}\n\n⚠️ 免責聲明：\n本頻道所有內容均為網路公開資訊與市場數據整理分享，不構成任何形式之投資建議或買賣邀約。加密貨幣市場波動劇烈，投資前請務必自行研究並審慎評估風險。\n\n#比特幣 #加密貨幣 #區塊鏈 #BTC #ETH #SOL #技術分析 #均線 #RSI #恐慌指數 #幣看趨勢"

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
mid = len(raw_t) // 2
f_big = ImageFont.truetype(fp, 66)
td.text((50, 175), raw_t[:mid], fill="#fbbf24", font=f_big)
td.rectangle((45, 295, 760, 405), fill="#dc2626")
td.text((60, 312), raw_t[mid:], fill="#ffffff", font=f_big)
td.text((50, 480), "・BTC / ETH / SOL / XRP 多空均線與 RSI 掃描", fill="#38bdf8", font=ImageFont.truetype(fp, 30))
td.text((50, 540), "・拒絕盲目跟風，理性拆解市場數據與風險", fill="#cbd5e1", font=ImageFont.truetype(fp, 30))
if os.path.exists("avatar.png"): t_img.paste(Image.open("avatar.png").resize((430, 430)), (800, 145))
t_img.save("thumb.jpg", "JPEG", quality=92)
youtube.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload("thumb.jpg")).execute()
print("Custom thumbnail uploaded successfully!")
