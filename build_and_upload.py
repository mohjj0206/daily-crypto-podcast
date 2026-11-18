import os, json, base64, subprocess, urllib.request
from PIL import Image, ImageDraw, ImageFont
from opencc import OpenCC
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

with open("payload.json", "r", encoding="utf-8") as f:
    payload = json.load(f)

raw_audio = payload["audio_url"]
audio_url = raw_audio if isinstance(raw_audio, list) else raw_audio
video_title = payload["video_title"]
video_desc = payload["video_desc"]
srt_base64 = payload.get("srt_base64", "")
slide1_data = payload.get("slide1_data", "")
slide2_data = payload.get("slide2_data", "")
slide3_data = payload.get("slide3_data", "")
today_str = payload.get("today_str", "")

urllib.request.urlretrieve(audio_url, "podcast.mp3")
cc = OpenCC('s2twp')
raw_srt = base64.b64decode(srt_base64).decode("utf-8")
clean_srt = cc.convert(raw_srt)
with open("subs.srt", "w", encoding="utf-8") as f:
    f.write(clean_srt)

font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
if not os.path.exists(font_path):
    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

f_header = ImageFont.truetype(font_path, 42)
f_sub = ImageFont.truetype(font_path, 26)
f_card_title = ImageFont.truetype(font_path, 38)
f_body = ImageFont.truetype(font_path, 32)
f_note = ImageFont.truetype(font_path, 22)

slides_info = [
    ("第一段｜市場體檢與情緒溫度計", "#38bdf8", cc.convert(slide1_data)),
    ("第二段｜近期三大市場焦點深度解析", "#fbbf24", cc.convert(slide2_data)),
    ("第三段｜前20大市值主流幣即時行情與均線攻防", "#34d399", cc.convert(slide3_data)),
    ("第四段｜BTC 與 ETH 多空關鍵觀察策略", "#f472b6", "聚焦兩大龍頭關鍵支撐與壓力結構\n・觀察 BTC 能否重新站回 200週與 200日均線\n・留意 14日 RSI 超賣後的動能修復訊號\n・嚴控部位風險，避免盲目追高殺低")
]

for idx, (sec_title, accent_color, body_text) in enumerate(slides_info, 1):
    img = Image.new("RGB", (1920, 1080), "#090d16")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 1920, 110), fill="#1e293b")
    draw.text((60, 30), f"幣看趨勢｜比特幣與加密貨幣情報  ({today_str})", fill="#ffffff", font=f_header)
    draw.rectangle((60, 140, 760, 820), fill="#111827", outline="#38bdf8", width=3)
    draw.text((165, 190), "雙人深度財經播客對談", fill="#38bdf8", font=f_card_title)
    draw.text((140, 260), "每日精選網路公開市場數據與焦點議題", fill="#94a3b8", font=f_sub)
    draw.rectangle((800, 140, 1860, 820), fill="#111827", outline=accent_color, width=4)
    draw.text((850, 185), sec_title, fill=accent_color, font=f_card_title)
    draw.line(((850, 250), (1810, 250)), fill="#334155", width=2)
    draw.multiline_text((850, 290), body_text, fill="#f8fafc", font=f_body, spacing=24)
    draw.text((60, 1035), "※ 本節目所有內容皆為網路公開資訊與市場數據整理分享，不構成任何投資建議，投資請獨立思考並自負風險。", fill="#64748b", font=f_note)
    img.save(f"slide{idx}.png")

with open("slides.txt", "w", encoding="utf-8") as f:
    f.write("file 'slide1.png'\nduration 240\n")
    f.write("file 'slide2.png'\nduration 270\n")
    f.write("file 'slide3.png'\nduration 300\n")
    f.write("file 'slide4.png'\nduration 600\n")
    f.write("file 'slide4.png'\n")

filter_complex = (
    "[1:a]showwaves=s=620x260:mode=cline:colors=#38bdf8:rate=25[waves];"
    "[0:v][waves]overlay=100:420[vid];"
    "[vid]subtitles=subs.srt:force_style='FontSize=26,PrimaryColour=&H00FFFFFF,BackColour=&H80000000,BorderStyle=3,Outline=1,MarginV=85'[out]"
)

cmd = [
    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "slides.txt",
    "-i", "podcast.mp3", "-filter_complex", filter_complex,
    "-map", "[out]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
    "-crf", "25", "-c:a", "aac", "-b:a", "128k", "-shortest", "output.mp4"
]
subprocess.run(cmd, check=True)

token_url = "".join(["https", "://", "oauth2.", "googleapis.", "com", "/token"])
creds = Credentials(
    None,
    refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
    token_uri=token_url,
    client_id=os.environ["YT_CLIENT_ID"].strip(),
    client_secret=os.environ["YT_CLIENT_SECRET"].strip()
)
youtube = build("youtube", "v3", credentials=creds)

body = {
    "snippet": {
        "title": video_title[:95],
        "description": video_desc,
        "tags": ["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"],
        "categoryId": "27"
    },
    "status": {"privacyStatus": "unlisted"}
}
media = MediaFileUpload("output.mp4", chunksize=1024*1024*5, resumable=True)
req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
res = None
while res is None:
    _, res = req.next_chunk()
print("Uploaded Video ID:", res["id"])
