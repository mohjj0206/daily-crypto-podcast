"""一鍵把 GitHub Releases（latest-preview）下載的預覽成片發佈至 YouTube。

讀取同目錄下的 publish_bundle.json 與對應的 .mp4／.jpg 檔，依序：
  1. 上傳主片並套用標題、說明、標籤與隱私狀態；
  2. 上傳自訂封面縮圖；
  3. 加入播放清單（若頻道內有「幣看趨勢」清單或設定 YOUTUBE_PLAYLIST_ID）；
  4. 發佈置頂第一則留言（含帳本短網址 https://is.gd/2JOYTF）；
  5. 若存在 Shorts 成片，上傳並在說明欄回填主片連結 https://youtu.be/<id>。

授權沿用 youtube_uploader 的 OAuth（YT_CLIENT_ID／YT_CLIENT_SECRET／YT_REFRESH_TOKEN）。
隱私狀態由環境變數 YT_PRIVACY_STATUS 控制（public／unlisted／private，預設 unlisted）。
"""

import json
import os

import youtube_uploader
from googleapiclient.http import MediaFileUpload

LEDGER_URL = "https://is.gd/2JOYTF"
VALID_PRIVACY = ("public", "unlisted", "private")


def _load_bundle(path="publish_bundle.json"):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _privacy_status():
    value = (os.environ.get("YT_PRIVACY_STATUS") or "unlisted").strip().lower()
    return value if value in VALID_PRIVACY else "unlisted"


def _upload_shorts_enabled():
    return (os.environ.get("UPLOAD_SHORTS", "true").strip().lower() == "true")


def _require_file(path, label):
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"{label}不存在：{path!r}")
    return path


def _insert_video(youtube, path, title, description, tags, privacy, asset):
    """上傳影片並套用指定隱私狀態，回傳 11 碼 video_id。"""
    safe_title = youtube_uploader._safe_title(title)
    safe_tags = youtube_uploader._safe_tags(tags)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": safe_title,
                "description": description,
                "tags": safe_tags,
                "categoryId": "27",
            },
            "status": {"privacyStatus": privacy},
        },
        media_body=MediaFileUpload(path, chunksize=1024 * 1024 * 5, resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id, _upload_status = youtube_uploader._accepted_upload(response, asset)
    return youtube_uploader._confirm_upload_status(youtube, video_id, asset)


def _set_thumbnail(youtube, video_id, thumb_path):
    if not thumb_path or not os.path.exists(thumb_path):
        print(f"封面縮圖不存在，略過：{thumb_path!r}")
        return
    try:
        youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(thumb_path)
        ).execute()
        print("Custom thumbnail uploaded successfully!")
    except Exception as error:  # noqa: BLE001
        youtube_uploader._surface_side_error("封面縮圖", error)


def _add_to_playlist(youtube, video_id):
    try:
        playlist_id = youtube_uploader._resolve_playlist_id(youtube)
        if playlist_id:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            print(f"影片已成功加入播放清單：{playlist_id}")
        else:
            print("未設定 YOUTUBE_PLAYLIST_ID 且頻道內找不到含「幣看趨勢」的播放清單，略過自動歸類。")
    except Exception as error:  # noqa: BLE001
        if not youtube_uploader._playlist_permission_skip(error):
            youtube_uploader._surface_side_error("播放清單", error)


def _publish_comment(youtube, video_id, first_comment):
    text = (first_comment or "").strip()
    if not text:
        print("未提供 first_comment，略過留言。")
        return
    if LEDGER_URL not in text:
        text = f"{text}\n\n📊 公開帳本：{LEDGER_URL}"
    try:
        youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {"snippet": {"textOriginal": text}},
                }
            },
        ).execute()
        print(
            "互動留言已發佈成功！\n"
            "💡 提示：YouTube Data API v3 不支援程式自動置頂留言，"
            "請至 YouTube Studio 後台手動點選「置頂」。"
        )
    except Exception as error:  # noqa: BLE001
        youtube_uploader._surface_side_error("互動留言", error)


def _upload_short(youtube, bundle, main_video_id, privacy):
    short_path = bundle.get("short_video_path")
    if not short_path or not os.path.exists(short_path):
        print(f"沒有 Shorts 成片，略過：{short_path!r}")
        return None
    main_url = f"https://youtu.be/{main_video_id}"
    short_description = bundle.get("short_description") or ""
    if main_url not in short_description:
        short_description = f"{short_description}\n\n▶️ 完整主片：{main_url}".strip()
    short_id = _insert_video(
        youtube,
        short_path,
        bundle.get("short_title") or "精華 Shorts #Shorts",
        short_description,
        bundle.get("tags") or ["Shorts", "比特幣", "幣看趨勢"],
        privacy,
        "Shorts",
    )
    print("Uploaded YouTube Short:", short_id)
    youtube_uploader._print_studio_links(short_id)
    return short_id


def main():
    bundle = _load_bundle()
    privacy = _privacy_status()
    print(f"=== 一鍵發佈預覽成片至 YouTube（隱私：{privacy}）===")

    youtube = youtube_uploader.build_youtube_client()
    if youtube is None:
        raise RuntimeError(
            "YouTube 用戶端建立失敗：請確認 ENABLE_YOUTUBE_UPLOAD=true 且已設定 "
            "YT_CLIENT_ID／YT_CLIENT_SECRET／YT_REFRESH_TOKEN。"
        )

    main_path = _require_file(bundle.get("main_video_path"), "主片")
    video_id = _insert_video(
        youtube,
        main_path,
        bundle.get("main_title") or "加密貨幣分析",
        bundle.get("main_description") or "",
        bundle.get("tags") or [],
        privacy,
        "主片",
    )
    print("Uploaded Main Video ID:", video_id)
    youtube_uploader._print_studio_links(video_id)

    _set_thumbnail(youtube, video_id, bundle.get("thumb_path"))
    _add_to_playlist(youtube, video_id)
    _publish_comment(youtube, video_id, bundle.get("first_comment"))

    if bundle.get("short_video_path") and _upload_shorts_enabled():
        _upload_short(youtube, bundle, video_id, privacy)

    print("=== 發佈完成 ===")
    print(f"主片網址：https://www.youtube.com/watch?v={video_id}")


if __name__ == "__main__":
    main()