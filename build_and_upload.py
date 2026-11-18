import os, json, base64, subprocess, urllib.request
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

def highlight_subs(srt_text):
    kw_pat = r'(\$[\d,.]+|\b\d+(?:,\d+)*(?:\.\d+)?%?\b|比特幣|以太幣|以太坊|BTC|ETH|SOL|XRP|ADA|ZEC|RSI|均線|恐慌與貪婪指數|超賣|超買|支撐|壓力|風險提醒|投資建議)'
    out_lines = []
    for line in srt_text.splitlines():
        if "-->" in line or line.strip().isdigit() or not line.strip():
            out_lines.append(line)
        else:
            hl = re.sub(kw_pat, r'{\\b1\\c&H0024BFFF&}\1{\\b0\\c&H00FFFFFF&}', line)
            out_lines.append(hl)
    return "\n".join(out_lines)

with open("subs.srt", "w", encoding="utf-8") as f:
    f.write(highlight_subs(clean_srt))


probe = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", "podcast.mp3"])
total_sec = float(probe.decode().strip())
seg = total_sec / 4.0

k_url = "".join(["https", "://", "data-api.binance.", "vision", "/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=100"])
klines = json.loads(urllib.request.urlopen(k_url).read().decode("utf-8"))
closes = [float(k[4]) for k in klines]
ma20 = [sum(closes[max(0, i-19):i+1])/len(closes[max(0, i-19):i+1]) for i in range(len(closes))]

fig, ax = plt.subplots(figsize=(9.2, 3.0), dpi=100)
fig.patch.set_facecolor("#111827")
ax.set_facecolor("#0f172a")
ax.plot(closes, color="#38bdf8", linewidth=2.5, label="BTC Close")
ax.plot(ma20, color="#fbbf24", linewidth=1.8, linestyle="--", label="20D MA")
ax.tick_params(colors="#94a3b8", labelsize=9)
ax.grid(color="#1e293b", linestyle="-", linewidth=0.8)
ax.legend(facecolor="#1e293b", edgecolor="none", labelcolor="white", fontsize=9)
for s in ["top", "right"]: ax.spines[s].set_visible(False)
for s in ["bottom", "left"]: ax.spines[s].set_color("#334155")
plt.tight_layout()
plt.savefig("chart.png", facecolor="#111827")
plt.close()

fp = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
if not os.path.exists(fp): fp = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
fh, fs, ft, fb, fn = ImageFont.truetype(fp, 40), ImageFont.truetype(fp, 24), ImageFont.truetype(fp, 36), ImageFont.truetype(fp, 30), ImageFont.truetype(fp, 22)

s1 = cc.convert(payload.get("slide1_data", ""))
s2 = "\n\n".join(cc.convert(payload.get("slide2_data", "")).split("\n"))
s3 = cc.convert(payload.get("slide3_data", ""))
s4 = "【多頭進攻觀察重點】\n・關注 BTC 能否放量站回 200週與 200日均線\n・留意 ETH 能否守穩關鍵整數關卡並帶動資金回流\n\n【空頭防守與風險控管】\n・觀察 14日 RSI 極度超賣後是否出現背離修復\n・短線波動劇烈，嚴控槓桿與單一幣種倉位比例"

cards = [("第一段｜市場體檢與情緒溫度計", "#38bdf8", s1, False), ("第二段｜近期三大市場焦點深度解析", "#fbbf24", s2, False), ("第三段｜主流幣行情與 BTC 100日均線走勢", "#34d399", s3, True), ("第四段｜BTC 與 ETH 多空關鍵觀察策略", "#f472b6", s4, False)]

for idx, (title, color, body, add_chart) in enumerate(cards, 1):
    img = Image.new("RGB", (1920, 1080), "#090d16")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1920, 105), fill="#1e293b")
    d.text((60, 28), f"幣看趨勢｜比特幣與加密貨幣情報  ({payload.get('today_str', '')})", fill="#ffffff", font=fh)
    d.rectangle((60, 135, 760, 825), fill="#111827", outline="#38bdf8", width=3)
    d.text((195, 165), "幣看趨勢・雙人財經播客", fill="#38bdf8", font=ft)
    if os.path.exists("avatar.png"):
        av = Image.open("avatar.png").resize((320, 320))
        img.paste(av, (250, 225))
    d.rectangle((800, 135, 1860, 825), fill="#111827", outline=color, width=4)
    d.text((845, 175), title, fill=color, font=ft)
    d.line(((845, 235), (1815, 235)), fill="#334155", width=2)
    d.multiline_text((845, 265), body, fill="#f8fafc", font=fb, spacing=20)
    if add_chart: img.paste(Image.open("chart.png"), (845, 495))
    d.text((60, 1035), "※ 本節目所有內容皆為網路公開資訊與市場數據整理分享，不構成任何投資建議，投資請獨立思考並自負風險。", fill="#64748b", font=fn)
    img.save(f"slide{idx}.png")

with open("slides.txt", "w", encoding="utf-8") as f:
    for i in range(1, 5): f.write(f"file 'slide{i}.png'\nduration {seg:.2f}\n")
    f.write("file 'slide4.png'\n")

fc = "[1:a]showwaves=s=620x220:mode=cline:colors=#38bdf8:rate=15[w];[0:v][w]overlay=100:560,fps=15,subtitles=subs.srt:force_style='FontSize=28,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,BorderStyle=3,Outline=1,MarginV=42'[out]"
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "slides.txt", "-i", "podcast.mp3", "-filter_complex", fc, "-map", "[out]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-shortest", "output.mp4"], check=True)

creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(), token_uri="".join(["https", "://", "oauth2.", "googleapis.", "com", "/token"]), client_id=os.environ["YT_CLIENT_ID"].strip(), client_secret=os.environ["YT_CLIENT_SECRET"].strip())
youtube = build("youtube", "v3", credentials=creds)
req = youtube.videos().insert(part="snippet,status", body={"snippet": {"title": payload["video_title"][:95], "description": payload["video_desc"], "tags": ["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"], "categoryId": "27"}, "status": {"privacyStatus": "unlisted"}}, media_body=MediaFileUpload("output.mp4", chunksize=1024*1024*5, resumable=True))
res = None
while res is None: _, res = req.next_chunk()
print("Uploaded Video ID:", res["id"])
