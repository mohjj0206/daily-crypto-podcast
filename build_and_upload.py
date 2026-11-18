import os, re, json, base64, subprocess, urllib.request
from PIL import Image, ImageDraw, ImageFont
from opencc import OpenCC
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

with open("payload.json", "r", encoding="utf-8") as f:
    payload = json.load(f)

raw_audio = payload["audio_url"]
audio_url = next(iter(raw_audio)) if isinstance(raw_audio, list) else raw_audio
urllib.request.urlretrieve(audio_url, "podcast.mp3")

cc = OpenCC("s2twp")
clean_srt = cc.convert(base64.b64decode(payload.get("srt_base64", "")).decode("utf-8"))

def build_ass_subs(srt_text):
    header = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Noto Sans CJK TC,30,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,0,0,0,0,100,100,0,0,3,1,0,2,40,40,36,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    kw_pat = r'(\$[\d,.]+|\b\d+(?:,\d+)*(?:\.\d+)?%?\b|比特幣|以太幣|以太坊|BTC|ETH|SOL|XRP|ADA|ZEC|RSI|均線|恐慌與貪婪指數|超賣|超買|支撐|壓力|風險提醒|投資建議)'
    punct_pat = r'[，。！？、；：「」『』（）【】《》—…!?;:\(\)\[\]]+|(?<!\d)[,\.](?!\d)'
    events = []
    for block in srt_text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 3 and "-->" in lines:
            tp = lines.replace(",", ".").split(" --> ")
            no_p = re.sub(r'\s+', ' ', re.sub(punct_pat, ' ', " ".join(lines[2:]))).strip()
            hl = re.sub(kw_pat, r'{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H00FFFFFF&}', no_p)
            events.append(f"Dialogue: 0,{tp.strip()[1:-1]},{tp.strip()[1:-1]},Default,,0,0,0,,{hl}")
    return header + "\n".join(events)

with open("subs.ass", "w", encoding="utf-8") as f:
    f.write(build_ass_subs(clean_srt))
probe = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", "podcast.mp3"])
seg = float(probe.decode().strip()) / 8.0

vb = "".join(["https", "://", "data-api.binance.", "vision", "/api/v3/klines"])
syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "ZECUSDT"]
cdata = {}
for s in syms:
    raw = json.loads(urllib.request.urlopen(f"{vb}?symbol={s}&interval=1d&limit=100").read().decode("utf-8"))
    cl = [float(k.at(4)) if hasattr(k, "at") else float(k) for k in raw]
    pct = ((cl[-1] - cl[-2]) / cl[-2]) * 100 if len(cl) >= 2 else 0.0
    cdata[s.replace("USDT", "")] = (cl, pct)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 2.7), dpi=100)
fig.patch.set_facecolor("#111827")
for ax, name, col in [(ax1, "BTC", "#38bdf8"), (ax2, "ETH", "#a78bfa")]:
    cl = cdata[name]
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
pcts = [cdata[n] for n in names]
cols = ["#34d399" if p >= 0 else "#f43f5e" for p in pcts]
bars = ax.barh(names[::-1], pcts[::-1], color=cols[::-1], height=0.55)
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

s1 = cc.convert(payload.get("slide1_data", ""))
t3 = [x.strip() for x in cc.convert(payload.get("slide2_data", "")).split("\n") if x.strip()]
s2_a = t3[0] if len(t3) > 0 else "近期市場焦點深度解析（一）"
s2_b = t3[1] if len(t3) > 1 else "近期市場焦點深度解析（二）"
s2_c = t3[2] if len(t3) > 2 else "近期市場焦點深度解析（三）"
s3 = cc.convert(payload.get("slide3_data", ""))
fng_num = int(re.search(r'\d+', s1.split('\n')[1]).group()) if len(s1.split('\n')) > 1 and re.search(r'\d+', s1.split('\n')[1]) else 15

slides_cfg = [
    ("開場｜盤勢觀察與投資風險提醒", "#fb7185", "・本節目所有內容皆為網路公開資訊與數據整理\n・純屬個人討論交流，絕對不構成任何投資建議\n・加密貨幣波動劇烈，請務必獨立思考風險自負", "gauge"),
    ("第一段｜BTC 技術體檢與情緒溫度計", "#38bdf8", s1, "gauge"),
    ("第二段｜市場焦點深度解析（議題一）", "#fbbf24", f"{s2_a}\n\n【多頭利多邏輯 vs 冷靜理性打折】\n・檢視資金敘事背後的真實基本面支撐\n・評估宏觀利率與監管環境的潛在風險", None),
    ("第二段｜市場焦點深度解析（議題二）", "#fbbf24", f"{s2_b}\n\n【多頭利多邏輯 vs 冷靜理性打折】\n・區分短期情緒炒作與長期結構性改變\n・留意市場流動性是否足以支撐高估值", None),
    ("第二段｜市場焦點深度解析（議題三）", "#fbbf24", f"{s2_c}\n\n【多頭利多邏輯 vs 冷靜理性打折】\n・追蹤鏈上真實資金流向與籌碼換手訊號\n・避免在消息面利多出盡時盲目追高", None),
    ("第三段｜六大主流幣 24H 漲跌與 RSI 掃描", "#34d399", s3, "bars"),
    ("第三段｜BTC 與 ETH 雙龍頭 100日均線對比", "#a78bfa", "・比較 BTC 與 ETH 近期價格與 20日均線乖離\n・觀察龍頭幣種何時出現止跌築底與量能回溫", "dual"),
    ("第四段｜BTC 與 ETH 必須盯緊的三個條件", "#f472b6", "1. 拋壓與槓桿清算動能是否出現枯竭訊號\n2. 關鍵均線與整數心理關卡能否帶量站穩\n3. 市場恐慌情緒與宏觀資金面何時迎來拐點", None)
]
for idx, (title, color, body, chart_type) in enumerate(slides_cfg, 1):
    img = Image.new("RGB", (1920, 1080), "#090d16")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1920, 105), fill="#1e293b")
    d.text((60, 28), f"幣看趨勢｜比特幣與加密貨幣情報  ({payload.get('today_str', '')})", fill="#ffffff", font=fh)
    d.rectangle((60, 135, 760, 825), fill="#111827", outline="#38bdf8", width=3)
    d.text((195, 165), "幣看趨勢・雙人財經播客", fill="#38bdf8", font=ft)
    if os.path.exists("avatar.png"):
        img.paste(Image.open("avatar.png").resize((480, 480)), (170, 260))
    d.rectangle((800, 135, 1860, 825), fill="#111827", outline=color, width=4)
    d.text((845, 175), title, fill=color, font=ft)
    d.line(((845, 235), (1815, 235)), fill="#334155", width=2)
    d.multiline_text((845, 260), body, fill="#f8fafc", font=fb, spacing=18)
    if chart_type == "gauge":
        d.text((845, 580), f"市場恐懼與貪婪指數溫度計：{fng_num} / 100", fill="#fbbf24", font=fb)
        for gx, gc in enumerate(["#ef4444", "#f97316", "#eab308", "#84cc16", "#22c55e"]):
            d.rectangle((845 + gx * 190, 635, 845 + (gx + 1) * 190 - 4, 675), fill=gc)
        px = 845 + int(min(100, max(0, fng_num)) * 9.48)
        d.polygon([(px, 680), (px - 16, 712), (px + 16, 712)], fill="#ffffff")
    elif chart_type == "bars":
        img.paste(Image.open("chart_bars.png").convert("RGB"), (845, 520))
    elif chart_type == "dual":
        img.paste(Image.open("chart_dual.png").convert("RGB"), (845, 520))
    d.text((60, 1035), "※ 本節目所有內容皆為網路公開資訊與市場數據整理分享，不構成任何投資建議，投資請獨立思考並自負風險。", fill="#64748b", font=fn)
    img.save(f"slide{idx}.png")

with open("list.txt", "w", encoding="utf-8") as f:
    for i in range(1, 9):
        dur = seg if i < 8 else (seg + 30)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-framerate", "10", "-t", f"{dur:.2f}", "-i", f"slide{i}.png", "-c:v", "libx264", "-tune", "stillimage", "-preset", "ultrafast", "-pix_fmt", "yuv420p", f"p{i}.mp4"], check=True)
        f.write(f"file 'p{i}.mp4'\n")

subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "base.mp4"], check=True)
subprocess.run(["ffmpeg", "-y", "-i", "base.mp4", "-i", "podcast.mp3", "-vf", "ass=subs.ass", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-shortest", "output.mp4"], check=True)

creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(), token_uri="".join(["https", "://", "oauth2.", "googleapis.", "com", "/token"]), client_id=os.environ["YT_CLIENT_ID"].strip(), client_secret=os.environ["YT_CLIENT_SECRET"].strip())
youtube = build("youtube", "v3", credentials=creds)
req = youtube.videos().insert(part="snippet,status", body={"snippet": {"title": payload["video_title"][:95], "description": payload["video_desc"], "tags": ["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"], "categoryId": "27"}, "status": {"privacyStatus": "unlisted"}}, media_body=MediaFileUpload("output.mp4", chunksize=1024*1024*5, resumable=True))
res = None
while res is None: _, res = req.next_chunk()
print("Uploaded Video ID:", res["id"])
# 自動生成 1280x720 爆款紅黃大字報封面縮圖 (結合橘貓柴犬 IP + 今日爆款標題)
t_img = Image.new("RGB", (1280, 720), "#090d16")
td = ImageDraw.Draw(t_img)
td.rectangle((0, 0, 1280, 720), outline="#fbbf24", width=12)
td.rectangle((45, 45, 520, 110), fill="#ef4444")
td.text((68, 56), "幣看趨勢｜每日深度財經情報", fill="#ffffff", font=ImageFont.truetype(fp, 32))

raw_t = re.sub(r'^【幣看趨勢】|\([^)]+\)$', '', payload["video_title"]).strip()
mid = len(raw_t) // 2
line1, line2 = raw_t[:mid], raw_t[mid:]
f_big = ImageFont.truetype(fp, 66)
td.text((50, 175), line1, fill="#fbbf24", font=f_big)
td.rectangle((45, 295, 760, 405), fill="#dc2626")
td.text((60, 312), line2, fill="#ffffff", font=f_big)
td.text((50, 480), "・BTC / ETH / SOL / XRP 多空均線與 RSI 掃描", fill="#38bdf8", font=ImageFont.truetype(fp, 30))
td.text((50, 540), "・拒絕盲目跟風，理性拆解市場數據與風險", fill="#cbd5e1", font=ImageFont.truetype(fp, 30))

if os.path.exists("avatar.png"):
    t_img.paste(Image.open("avatar.png").resize((430, 430)), (800, 145))

t_img.save("thumb.jpg", "JPEG", quality=92)
youtube.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload("thumb.jpg")).execute()
print("Custom thumbnail uploaded successfully!")
