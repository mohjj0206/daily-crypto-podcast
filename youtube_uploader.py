import os
import re
import presentation
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def _write_metadata(section, title, description, mode):
    with open("youtube_metadata.txt", mode, encoding="utf-8") as metadata_file:
        metadata_file.write(f"{section}標題：\n{title}\n\n")
        metadata_file.write(f"{section}說明欄與章節時間軸：\n{description}\n\n")


def _print_studio_links(video_id):
    text = str(video_id or "")
    if not text or text.startswith(("TEST", "UPLOAD")):
        return
    print(f"影片連結：https://www.youtube.com/watch?v={text}")
    print(f"後台編輯：https://studio.youtube.com/video/{text}/edit")


def _playlist_permission_skip(error):
    if "insufficientPermissions" in str(error):
        print("播放清單權限不足（insufficientPermissions），略過。")
        return True
    return False


_VIDEO_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")


def is_real_video_id(video_id):
    return bool(_VIDEO_ID.fullmatch(str(video_id or "")))


def _safe_title(title):
    return str(title or "").replace("<", "").replace(">", "").strip()[:95]


def _safe_tags(tags):
    kept = []
    used = 0
    for tag in tags or []:
        text = str(tag).replace("<", "").replace(">", "").strip()
        if not text:
            continue
        extra = len(text) + (1 if kept else 0)
        if used + extra > 450:
            break
        kept.append(text)
        used += extra
    return kept


def _api_error_text(error):
    content = getattr(error, "content", b"") or b""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return f"{type(error).__name__}: {error}\n{content}".strip()


def _record_upload_failure(asset, error):
    with open("UPLOAD_FAILED.flag", "a", encoding="utf-8") as flag_file:
        flag_file.write(f"{asset}：{_api_error_text(error)}\n")

    if "uploadLimitExceeded" in str(error):
        print(f"警告：YouTube 每日上傳數量已達上限，{asset}上傳未完成；成片與中繼資料已保留供下載補發。")
    else:
        print(f"警告：{asset}上傳失敗；成片與中繼資料已保留供下載補發。")


def _fail_upload(asset, error):
    print(f"{asset} 上傳失敗，完整 API 錯誤：\n{_api_error_text(error)}")
    _record_upload_failure(asset, error)
    raise error


def _is_auth_error(error):
    text = f"{type(error).__name__}: {error}"
    if type(error).__name__ == "RefreshError":
        return True
    return any(
        marker in text
        for marker in (
            "invalid_grant",
            "invalid_client",
            "invalid_rapt",
            "unauthorized_client",
            "Invalid Credentials",
            "authError",
        )
    )


def _surface_side_error(asset, error):
    """授權失效必須中止。其餘附帶動作印出完整錯誤，不再假裝不影響主程式。"""
    if _is_auth_error(error):
        _fail_upload(asset, error)
    print(f"{asset}失敗，完整 API 錯誤：\n{_api_error_text(error)}")


def _accepted_upload(response, asset):
    video_id = (response or {}).get("id")
    status = (response or {}).get("status") or {}
    upload_status = str(status.get("uploadStatus") or "")
    if not is_real_video_id(video_id):
        _fail_upload(asset, RuntimeError(f"沒有取得 11 碼 video_id：{response!r}"))
    if upload_status in {"failed", "rejected", "deleted"}:
        reason = status.get("failureReason") or status.get("rejectionReason") or ""
        _fail_upload(
            asset,
            RuntimeError(
                f"YouTube 拒絕上傳：uploadStatus={upload_status} reason={reason} response={response!r}"
            ),
        )
    return video_id, upload_status


def _confirm_upload_status(youtube, video_id, asset):
    try:
        listed = youtube.videos().list(
            part="status,processingDetails",
            id=video_id,
        ).execute()
    except Exception as error:
        _fail_upload(asset, error)
    items = (listed or {}).get("items") or []
    if not items:
        _fail_upload(
            asset,
            RuntimeError(f"videos.list 查無 {video_id}，無法確認上傳狀態：{listed!r}"),
        )
    status = items[0].get("status") or {}
    processing = items[0].get("processingDetails") or {}
    upload_status = str(status.get("uploadStatus") or "")
    processing_status = str(processing.get("processingStatus") or "")
    failed = upload_status in {"failed", "rejected", "deleted"} or processing_status in {
        "failed",
        "terminated",
    }
    if failed or upload_status not in {"uploaded", "processed"}:
        reason = (
            status.get("failureReason")
            or status.get("rejectionReason")
            or processing.get("processingFailureReason")
            or ""
        )
        _fail_upload(
            asset,
            RuntimeError(
                f"YouTube 上傳狀態無法確認：{video_id} uploadStatus={upload_status or 'missing'} "
                f"processingStatus={processing_status or 'missing'} reason={reason} status={status!r}"
            ),
        )
    print(
        f"{asset} 上傳狀態已確認：{video_id} "
        f"uploadStatus={upload_status} processingStatus={processing_status or 'pending'}"
    )
    return video_id


def build_youtube_client():
    if os.environ.get("ENABLE_YOUTUBE_UPLOAD", "false").strip().lower() != "true":
        print("[ENABLE_YOUTUBE_UPLOAD=false] YouTube 上傳已停用：跳過 YouTube OAuth 驗證與影片上傳。")
        return None
    if os.environ.get("TEST_MODE", "").lower() == "true":
        print("[TEST_MODE] 測試模式已啟用：跳過 YouTube OAuth 連線與影片上傳（保留每日上傳配額）。")
        return None
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
        token_uri="".join(["https", "://", "oauth2.", "googleapis.", "com", "/token"]),
        client_id=os.environ["YT_CLIENT_ID"].strip(),
        client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
    )
    return build("youtube", "v3", credentials=creds)


def _build_engagement_comment(cur_p, btc_pct, fng_num, show_dca):
    """根據當日行情產生一則互動二選一留言文字。"""
    if show_dca:
        return (
            f"🐾 今天 BTC 站在 {presentation.format_usd(cur_p)}，DCA 帳本繼續紀律執行中！\n"
            "📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://mohjj0206.github.io/daily-crypto-podcast/\n\n"
            "你這個月選擇「繼續定期定額、不管短線波動」還是「暫停一個月先觀望」？\n"
            "留言告訴比特汪與以太喵，讓我們一起追蹤你的策略！👇"
        )
    direction = "強勢上漲" if btc_pct >= 1.5 else ("明顯回落" if btc_pct <= -1.5 else "橫盤震盪")
    fear_clause = f"，恐慌貪婪指數 {fng_num}" if fng_num is not None else ""
    return (
        f"🐾 今天 BTC {direction}（{btc_pct:+.2f}%）{fear_clause}！\n"
        "你現在的策略是「繼續紀律定投、逢低加碼」還是「空手觀望、等待更明確訊號」？\n"
        "留言告訴比特汪與以太喵！👇 #幣看趨勢"
    )


def _resolve_playlist_id(youtube):
    """優先讀環境變數；若未設定則查詢頻道內含「幣看趨勢」的播放清單。查詢失敗直接拋出。"""
    env_id = os.environ.get("YOUTUBE_PLAYLIST_ID", "").strip()
    if env_id:
        return env_id
    channel_resp = youtube.channels().list(part="id", mine=True).execute()
    channel_id = (channel_resp.get("items") or [{}])[0].get("id", "")
    if not channel_id:
        return None
    pl_resp = youtube.playlists().list(
        part="snippet",
        channelId=channel_id,
        maxResults=50,
    ).execute()
    for item in pl_resp.get("items", []):
        if "幣看趨勢" in item["snippet"].get("title", ""):
            return item["id"]
    return None


def upload_main_video(youtube, video_path, thumb_path, title, description, tags, show_dca, cur_p, btc_pct, fng_num, is_test):
    try:
        os.remove("UPLOAD_FAILED.flag")
    except FileNotFoundError:
        pass
    _write_metadata("主片", title, description, "w")

    if is_test or youtube is None:
        print("\n" + "=" * 50)
        print(f"[TEST_MODE] 主片已成功渲染完成：{video_path}")
        print(f"[TEST_MODE] 封面縮圖已就緒：{thumb_path}")
        print(f"[TEST_MODE] 預計主片標題：{title[:95]}")
        print(f"[TEST_MODE] 預計說明欄與章節：\n{description}")
        print(f"[TEST_MODE] 互動留言預覽：\n{_build_engagement_comment(cur_p, btc_pct, fng_num, show_dca)}")
        print("=" * 50 + "\n")
        return "TEST_MAIN_VIDEO_ID"

    video_id = _insert_video(
        youtube,
        video_path,
        title,
        description,
        tags or ["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"],
        "主片",
    )
    print("Uploaded Video ID:", video_id)
    _print_studio_links(video_id)

    # 上傳封面圖
    if thumb_path and os.path.exists(thumb_path):
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_path)).execute()
            print("Custom thumbnail uploaded successfully!")
        except Exception as e:
            _surface_side_error("封面縮圖", e)

    # 自動發布互動留言
    try:
        comment_body = _build_engagement_comment(cur_p, btc_pct, fng_num, show_dca)
        youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": comment_body}
                    },
                }
            },
        ).execute()
        print(
            "互動留言已發佈成功！\n"
            "💡 提示：YouTube Data API v3 不支援程式自動置頂留言，"
            "請至 YouTube Studio 後台手動點選「置頂」。"
        )
    except Exception as _comment_err:
        _surface_side_error("互動留言", _comment_err)

    # 自動加入播放清單。查詢失敗不得誤報成「找不到清單」。
    try:
        playlist_id = _resolve_playlist_id(youtube)
        if playlist_id:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    }
                },
            ).execute()
            print(f"影片已成功加入播放清單：{playlist_id}")
        else:
            print("未設定 YOUTUBE_PLAYLIST_ID 且頻道內找不到含「幣看趨勢」的播放清單，略過自動歸類。")
    except Exception as _pl_err:
        if not _playlist_permission_skip(_pl_err):
            _surface_side_error("播放清單", _pl_err)

    return video_id


def upload_short_video(youtube, short_path, main_video_id, cur_p, fng_num, today_str):
    main_video_url = f"https://www.youtube.com/watch?v={main_video_id}"
    short_title = (
        f"BTC {presentation.format_usd(cur_p)}｜恐慌指數 {fng_num} 點，這個訊號別忽略！ #Shorts"
        if fng_num is not None else
        f"BTC {presentation.format_usd(cur_p)}｜幣圈最新市場焦點與風險解析 #Shorts"
    )
    short_description = (
        f"{today_str} 幣圈市場 50 秒快訊。\n\n"
        "📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://mohjj0206.github.io/daily-crypto-podcast/\n\n"
        f"▶️ 完整 15 分鐘市場解析：{main_video_url}\n\n"
        "本內容為公開資訊整理，不構成投資建議。加密貨幣波動劇烈，請審慎評估風險。\n"
        "#Shorts #比特幣 #BTC #以太幣 #ETH #幣看趨勢"
    )

    _write_metadata("Shorts", short_title, short_description, "a")

    if (
        os.environ.get("ENABLE_YOUTUBE_UPLOAD", "false").strip().lower() != "true"
        or os.environ.get("TEST_MODE", "").lower() == "true"
        or youtube is None
        or str(main_video_id or "").startswith(("TEST", "PREVIEW"))
    ):
        print("\n" + "=" * 50)
        print(f"[TEST_MODE] Shorts 短片已成功渲染完成：{short_path}")
        print(f"[TEST_MODE] 預計 Shorts 標題：{short_title[:95]}")
        print(f"[TEST_MODE] 預計 Shorts 說明欄：\n{short_description}")
        print("=" * 50 + "\n")
        return "TEST_SHORT_VIDEO_ID"

    if not is_real_video_id(main_video_id):
        _fail_upload(
            "Shorts",
            RuntimeError(f"主片沒有有效 video_id，無法上傳 Shorts：{main_video_id!r}，成片已保留：{short_path}"),
        )

    short_id = _insert_video(
        youtube,
        short_path,
        short_title,
        short_description,
        ["Shorts", "比特幣", "BTC", "以太幣", "ETH", "幣看趨勢"],
        "Shorts",
    )
    print("Uploaded YouTube Short:", short_id)
    _print_studio_links(short_id)
    return short_id


PLAYLIST_EVERGREEN = "【幣看深潛｜加密貨幣百科】"
LEDGER_URL = "https://mohjj0206.github.io/daily-crypto-podcast/"


def _playlist_by_title(youtube, title):
    channel_resp = youtube.channels().list(part="id", mine=True).execute()
    channel_id = (channel_resp.get("items") or [{}])[0].get("id", "")
    if not channel_id:
        return None
    response = youtube.playlists().list(part="snippet", channelId=channel_id, maxResults=50).execute()
    for item in response.get("items", []):
        if item["snippet"].get("title") == title:
            return item["id"]
    created = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": "幣看深潛常青教學，不隨每日新聞過期。"},
            "status": {"privacyStatus": "unlisted"},
        },
    ).execute()
    return created.get("id")


def _insert_video(youtube, path, title, description, tags, asset="影片"):
    safe_title = _safe_title(title)
    safe_tags = _safe_tags(tags)
    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": safe_title,
                    "description": description,
                    "tags": safe_tags,
                    "categoryId": "27",
                },
                "status": {"privacyStatus": "unlisted"},
            },
            media_body=MediaFileUpload(path, chunksize=1024 * 1024 * 5, resumable=True),
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
    except Exception as error:
        _fail_upload(asset, error)
    video_id, _upload_status = _accepted_upload(response, asset)
    return _confirm_upload_status(youtube, video_id, asset)


def upload_tutorial_video(youtube, rendered, tutorial, is_test):
    """上傳教學長片與精華 Shorts，並放進幣看深潛播放清單。"""
    title = f"{presentation.tutorial_badge(tutorial.get('ep_id', 'EP'))}｜{tutorial.get('title', '系統教學')}"
    description = (
        f"📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：{LEDGER_URL}\n\n"
        f"{presentation.tutorial_badge(tutorial.get('ep_id', 'EP'))}\n"
        f"{tutorial.get('lesson_title') or tutorial.get('title')}\n\n"
        "本集圖表是教學結構示意，不是即時持倉普查，也不構成投資建議。\n"
        "模擬定期定額不是真實成交。加密貨幣波動劇烈，請自行研究。\n\n"
        "⏱️ 章節\n" + "\n".join(rendered.get("chapters") or [])
    )
    _write_metadata("教學長片", title, description, "w")
    if is_test or youtube is None:
        print(f"[TEST_MODE] 教學長片不上傳：{rendered['output_path']}")
        print(f"[TEST_MODE] 精華 Shorts 不上傳：{rendered['short_path']}")
        print(title)
        print(description)
        return "TEST_TUTORIAL_VIDEO_ID"
    video_id = _insert_video(
        youtube, rendered["output_path"], title, description,
        ["幣看深潛", "比特幣", "UTXO", "系統教學", tutorial.get("ep_id", "EP")],
        "教學長片",
    )
    print("Uploaded Tutorial Video ID:", video_id)
    _print_studio_links(video_id)
    if rendered.get("thumb_path") and os.path.exists(rendered["thumb_path"]):
        try:
            youtube.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(rendered["thumb_path"])
            ).execute()
        except Exception as error:
            _surface_side_error("教學縮圖", error)
    short_title = f"{tutorial.get('ep_id', 'EP')} 精華｜{tutorial.get('title', '系統教學')} #Shorts"
    short_description = (
        f"完整教學：https://www.youtube.com/watch?v={video_id}\n"
        f"📊 公開帳本：{LEDGER_URL}\n\n"
        "結構示意，不構成投資建議。\n#Shorts #幣看深潛 #比特幣"
    )
    short_id = _insert_video(
        youtube, rendered["short_path"], short_title, short_description, ["Shorts", "幣看深潛"],
        "教學Shorts",
    )
    print("Uploaded Tutorial Short:", short_id)
    _print_studio_links(short_id)
    try:
        playlist_id = _playlist_by_title(youtube, PLAYLIST_EVERGREEN)
        if playlist_id:
            youtube.playlistItems().insert(
                part="snippet",
                body={"snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }},
            ).execute()
            print(f"已加入播放清單 {PLAYLIST_EVERGREEN}：{playlist_id}")
    except Exception as error:
        if not _playlist_permission_skip(error):
            _surface_side_error("教學播放清單", error)
    return video_id

