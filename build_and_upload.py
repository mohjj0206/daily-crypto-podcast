import os, sys, subprocess, urllib.request
from faster_whisper import WhisperModel
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

audio_url = os.environ["AUDIO_URL"]
video_title = os.environ["VIDEO_TITLE"]
video_desc = os.environ["VIDEO_DESC"]

# 1. 下載音檔
urllib.request.urlretrieve(audio_url, "podcast.mp3")

# 2. 免費 AI 自動生成繁體中文字幕 (SRT)
model = WhisperModel("base", device="cpu", compute_type="int8")
segments, _ = model.transcribe("podcast.mp3", language="zh", initial_prompt="以下是繁體中文加密貨幣播客對談，包含比特幣、均線、RSI、恐慌指數。")

def fmt_time(t):
    h, r = divmod(int(t), 3600)
    m, s = divmod(r, 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

with open("subs.srt", "w", encoding="utf-8") as f:
    for i, seg in enumerate(segments, 1):
        f.write(f"{i}\n{fmt_time(seg.start)} --> {fmt_time(seg.end)}\n{seg.text.strip()}\n\n")

# 3. FFmpeg 合成 1080p 深色背景 + 動態聲波 + 繁體中文字幕
filter_complex = (
    "[0:v]drawtext=text='幣看趨勢｜比特幣與加密貨幣情報':fontcolor=white:fontsize=46:x=(w-text_w)/2:y=140,"
    "drawtext=text='※ 本節目僅供數據與新聞分享，不構成任何投資建議':fontcolor=#94a3b8:fontsize=26:x=(w-text_w)/2:y=210[bg];"
    "[1:a]showwaves=s=1400x160:mode=cline:colors=#38bdf8:rate=25[waves];"
    "[bg][waves]overlay=(W-w)/2:440[vid];"
    "[vid]subtitles=subs.srt:force_style='FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,MarginV=70'[out]"
)

cmd = [
    "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#0b1120:s=1920x1080:r=25",
    "-i", "podcast.mp3", "-filter_complex", filter_complex,
    "-map", "[out]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
    "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-shortest", "output.mp4"
]
subprocess.run(cmd, check=True)

# 4. 自動上傳至 YouTube (分段拼組 token_uri 避免瀏覽器複製吃字)
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
        "tags": ["比特幣", "加密貨幣", "BTC", "技術分析", "動區動趨", "幣看趨勢"],
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
