import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def _write_metadata(section, title, description, mode):
    with open("youtube_metadata.txt", mode, encoding="utf-8") as metadata_file:
        metadata_file.write(f"{section}標題：\n{title}\n\n")
        metadata_file.write(f"{section}說明欄與章節時間軸：\n{description}\n\n")


def _record_upload_failure(asset, error):
    with open("UPLOAD_FAILED.flag", "a", encoding="utf-8") as flag_file:
        flag_file.write(f"{asset}：{type(error).__name__}: {error}\n")

    if "uploadLimitExceeded" in str(error):
        print(f"警告：YouTube 每日上傳數量已達上限，{asset}上傳未完成；成片與中繼資料已保留供下載補發。")
    else:
        print(f"警告：{asset}上傳失敗（{type(error).__name__}: {error}）；成片與中繼資料已保留供下載補發。")


def build_youtube_client():
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
            f"🐾 今天 BTC 站在 ${float(cur_p):,f}，DCA 帳本繼續紀律執行中！\n"
            "你這個月選擇「繼續定期定額、不管短線波動」還是「暫停一個月先觀望」？\n"
            "留言告訴比特汪與以太喵，讓我們一起追蹤你的策略！👇"
        )
    direction = "強勢上漲" if btc_pct >= 1.5 else ("明顯回落" if btc_pct <= -1.5 else "橫盤震盪")
    return (
        f"🐾 今天 BTC {direction}（{btc_pct:+.2f}%），恐慌貪婪指數 "
        f"{fng_num if fng_num is not None else '資料未取得'}！\n"
        "你現在的策略是「繼續紀律定投、逢低加碼」還是「空手觀望、等待更明確訊號」？\n"
        "留言告訴比特汪與以太喵！👇 #幣看趨勢"
    )


def _resolve_playlist_id(youtube):
    """優先讀環境變數；若未設定則查詢頻道內含「幣看趨勢」的播放清單。"""
    env_id = os.environ.get("YOUTUBE_PLAYLIST_ID", "").strip()
    if env_id:
        return env_id
    try:
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
    except Exception as _pl_lookup_err:
        print(f"查詢播放清單時發生錯誤（不影響主程式）：{_pl_lookup_err}")
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

    try:
        req = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title[:95],
                    "description": description,
                    "tags": tags or ["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"],
                    "categoryId": "27",
                },
                "status": {"privacyStatus": "unlisted"},
            },
            media_body=MediaFileUpload(video_path, chunksize=1024*1024*5, resumable=True),
        )
        res = None
        while res is None:
            _, res = req.next_chunk()
        video_id = res["id"]
        print("Uploaded Video ID:", video_id)
    except Exception as err:
        _record_upload_failure("主片", err)
        if "uploadLimitExceeded" in str(err):
            return "UPLOAD_LIMIT_EXCEEDED"
        return "UPLOAD_FAILED_MAIN_VIDEO"

    # 上傳封面圖
    if thumb_path and os.path.exists(thumb_path):
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb_path)).execute()
            print("Custom thumbnail uploaded successfully!")
        except Exception as e:
            print(f"Failed to upload custom thumbnail: {e}")

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
        print(f"互動留言發佈失敗（不影響主程式）：{_comment_err}")

    # 自動加入播放清單
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
        print(f"加入播放清單失敗（不影響主程式）：{_pl_err}")

    return video_id


def upload_short_video(youtube, short_path, main_video_id, cur_p, fng_num, today_str):
    main_video_url = f"https://www.youtube.com/watch?v={main_video_id}"
    short_title = (
        f"BTC ${cur_p}｜恐慌指數 {fng_num} 點，這個訊號別忽略！ #Shorts"
        if fng_num is not None else
        f"BTC ${cur_p}｜幣圈最新市場焦點與風險解析 #Shorts"
    )
    short_description = (
        f"{today_str} 幣圈市場 50 秒快訊。\n\n"
        f"▶️ 完整 15 分鐘市場解析：{main_video_url}\n\n"
        "本內容為公開資訊整理，不構成投資建議。加密貨幣波動劇烈，請審慎評估風險。\n"
        "#Shorts #比特幣 #BTC #以太幣 #ETH #幣看趨勢"
    )

    _write_metadata("Shorts", short_title, short_description, "a")

    if (
        os.environ.get("TEST_MODE", "").lower() == "true"
        or youtube is None
        or main_video_id == "TEST_MAIN_VIDEO_ID"
    ):
        print("\n" + "=" * 50)
        print(f"[TEST_MODE] Shorts 短片已成功渲染完成：{short_path}")
        print(f"[TEST_MODE] 預計 Shorts 標題：{short_title[:95]}")
        print(f"[TEST_MODE] 預計 Shorts 說明欄：\n{short_description}")
        print("=" * 50 + "\n")
        return "TEST_SHORT_VIDEO_ID"

    if main_video_id in ("UPLOAD_LIMIT_EXCEEDED", "UPLOAD_FAILED_MAIN_VIDEO"):
        print(f"警告：主片未成功上傳，略過 Shorts 上傳；Shorts 成片與中繼資料已保留：{short_path}")
        return "UPLOAD_SKIPPED_MAIN_VIDEO_FAILED"

    try:
        short_request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": short_title[:95],
                    "description": short_description,
                    "tags": ["Shorts", "比特幣", "BTC", "以太幣", "ETH", "幣看趨勢"],
                    "categoryId": "27",
                },
                "status": {"privacyStatus": "unlisted"},
            },
            media_body=MediaFileUpload(
                short_path, chunksize=1024 * 1024 * 5, resumable=True
            ),
        )
        short_response = None
        while short_response is None:
            _, short_response = short_request.next_chunk()
        short_id = short_response["id"]
        print("Uploaded YouTube Short:", short_id)
        return short_id
    except Exception as err:
        _record_upload_failure("Shorts", err)
        if "uploadLimitExceeded" in str(err):
            return "UPLOAD_LIMIT_EXCEEDED"
        return "UPLOAD_FAILED_SHORT_VIDEO"
