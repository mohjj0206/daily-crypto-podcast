import json
import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import market_data
import chart_maker
import presentation
import script_writer
import media_engine
import broll_engine
import youtube_uploader
import daily_learner


def _validate_video_output(path, label):
    """上傳前確認檔案存在，且同時包含可解碼的影音串流。"""
    if not path or not os.path.isfile(path):
        raise RuntimeError(f"{label} 渲染失敗：找不到輸出檔案 {path!r}")
    size_bytes = os.path.getsize(path)
    if size_bytes <= 0:
        raise RuntimeError(f"{label} 渲染失敗：輸出檔案是空檔。")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type",
            "-of", "json", path,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    metadata = json.loads(probe.stdout or "{}")
    stream_types = {
        str(stream.get("codec_type") or "")
        for stream in metadata.get("streams") or []
    }
    try:
        duration = float((metadata.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 or not {"video", "audio"}.issubset(stream_types):
        raise RuntimeError(
            f"{label} 渲染檔不完整：duration={duration:.3f}s，"
            f"streams={sorted(stream_types)}。拒絕上傳不完整影片。"
        )
    result = {
        "path": os.path.basename(path),
        "duration_seconds": round(duration, 3),
        "size_bytes": size_bytes,
    }
    print(
        f"[Render QA] {label} 通過：{result['duration_seconds']:.2f}s，"
        f"{size_bytes:,} bytes，影音串流完整。"
    )
    return result


def _broll_attribution(manifest):
    """將實際採用的 Pixabay 素材整理成可直接公開的來源標註。"""
    lines = []
    seen = set()
    for item in manifest or []:
        source_url = str(item.get("source_url") or "").strip()
        creator = str(item.get("creator") or "").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        key = source_url or asset_id
        if not key or key in seen:
            continue
        seen.add(key)
        title = str(item.get("title") or "Pixabay video").strip()
        if source_url:
            lines.append(f"- {title}｜{creator or 'Pixabay contributor'}｜{source_url}")
        else:
            lines.append(f"- {title}｜{creator or 'Pixabay contributor'}｜Pixabay asset ID: {asset_id}")
    if not lines:
        return ""
    return (
        "🎬 影片素材來源與授權\n"
        + "\n".join(lines)
        + "\n素材依 Pixabay Content License 使用；Pixabay 原則上不強制署名，"
        "此處仍列出來源供觀眾查核。個別素材仍須遵守肖像、商標及第三方權利限制。"
        + "\n授權說明：https://pixabay.com/service/license/\n\n"
    )


def main():
    # ── 基本時間與模式設定 ──────────────────────────────────────
    tw_tz = ZoneInfo("Asia/Taipei")
    now_tw = datetime.now(tw_tz)
    today_str = now_tw.strftime("%Y年%m月%d日")
    is_test = os.environ.get("TEST_MODE", "").lower() == "true"
    reuse_script = os.environ.get("REUSE_SCRIPT", "").strip().lower() == "true"
    show_dca = (now_tw.weekday() == 4) or (now_tw.day == 26)
    OUTRO_DURATION = 12.0
    OUTRO_PAUSE = 1.5
    SLIDE_COUNT = 15 if show_dca else 14

    config = {
        "is_test": is_test,
        "show_dca": show_dca,
        "today_str": today_str,
        "OUTRO_DURATION": OUTRO_DURATION,
        "OUTRO_PAUSE": OUTRO_PAUSE,
        "SLIDE_COUNT": SLIDE_COUNT,
        "reuse_script": reuse_script,
    }

    print(f"=== 幣看趨勢啟動 [{today_str}] (測試模式: {is_test}, DCA帳本: {show_dca}, 重用劇本: {reuse_script}) ===")
    if reuse_script:
        print("[REUSE_SCRIPT] 快取存在時跳過 Gemini，只跑語音、簡報與渲染。")

    # ── 1. 抓取所有市場數據與 DCA 帳本 ─────────────────────────
    print("[1/5] 抓取即時市場數據、鏈上指標與最新新聞...")
    mdata = market_data.fetch_all_market_data(now_tw, is_test)
    dca_data = market_data.fetch_dca_data(mdata["spot_prices"]["BTC"], now_tw) if show_dca else {}
    daily_learner.prepare_learning_insights()

    # ── 2. 生成各類數據視覺化圖表 ─────────────────────────────
    print("[2/5] 生成技術分析與走勢圖表...")
    tech_charts, tech_chart_dir = chart_maker.generate_technical_charts(
        mdata["daily_histories"]["BTC"],
        mdata["btc_metrics"],
        mdata["btc_oversold_events"],
    )
    chart_paths = {
        "price": tech_charts["price"],
        "rsi": tech_charts["rsi"],
        "dual": chart_maker.generate_dual_chart(mdata["cdata"]),
        "bars": chart_maker.generate_bars_chart(mdata["cdata"]),
    }
    if show_dca and dca_data:
        chart_paths["dca"] = chart_maker.generate_dca_chart(dca_data["portfolio"], now_tw)

    # ── 3. 呼叫 Gemini AI 產製逐字稿與對話腳本 ────────────────
    print("[3/5] 呼叫 Gemini 生成雙人對話腳本與摘要...")
    sdata = script_writer.generate_script(mdata, dca_data, config)
    if sdata.get("slide_count"):
        config["SLIDE_COUNT"] = int(sdata["slide_count"])

    # ── 4. 語音合成、簡報渲染與 FFmpeg 影音多工合成 ───────────
    print("[4/5] 渲染 1080p 主片與 9:16 Shorts 短片...")
    try:
        main_render = media_engine.render_main_video(
            sdata, mdata, dca_data, chart_paths, config
        )
        main_broll_manifest = broll_engine.get_episode_manifest()
        short_video_path = media_engine.render_short_video(
            sdata, mdata, config
        )
        short_broll_manifest = broll_engine.get_episode_manifest()
    finally:
        tech_chart_dir.cleanup()

    # 將本集實際採用的素材來源保存成稽核檔，並同步放入 YouTube 說明欄。
    broll_manifest = main_broll_manifest + short_broll_manifest
    with open("daily_broll_manifest.json", "w", encoding="utf-8") as handle:
        json.dump(broll_manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    broll_credits = _broll_attribution(broll_manifest)

    # 上傳前先確認主片與 Shorts 的影音檔案完整性；技術檔案不合格時禁止上傳。
    render_validation = {
        "main": _validate_video_output(main_render.get("output_path"), "每日主片"),
        "short": _validate_video_output(short_video_path, "每日 Shorts"),
    }
    audit = daily_learner.audit_episode(sdata.get("full_script", ""), "subs.ass", is_test, sdata)
    # 內容品質警告與渲染完整性分開記錄；編輯警告不會阻擋已完整的影片產製。
    audit["render_status"] = "passed"
    audit["rendered_assets"] = render_validation

    # ── 5. 上傳 YouTube、發布留言與播放清單歸類 ───────────────
    yt_title = sdata.get("yt_title") or main_render["video_title"]
    yt_highlights = sdata.get("yt_highlights") or []
    yt_chapters = sdata.get("yt_chapters") or []
    highlights_block = "\n".join(yt_highlights) if yt_highlights else ""
    chapters_block = "\n".join(yt_chapters) if yt_chapters else ""
    yt_description = (
        (f"{highlights_block}\n\n" if highlights_block else "")
        + f"📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://is.gd/2JOYTF\n\n"
        + (f"⏱️ 章節\n{chapters_block}\n\n" if chapters_block else "")
        + broll_credits
        + f"{main_render['v_desc']}"
    )
    short_title = youtube_uploader.build_daily_short_title(sdata)
    # 正式製片預設上傳；TEST_MODE 永遠只產生預覽，不觸發 YouTube OAuth 或上傳。
    enable_yt = (not is_test) and os.environ.get("ENABLE_YOUTUBE_UPLOAD", "true").strip().lower() == "true"
    if enable_yt:
        print("[5/5] 上傳至 YouTube 並發布頻道互動...")
        youtube = youtube_uploader.build_youtube_client()
        main_video_id = youtube_uploader.upload_main_video(
            youtube,
            video_path=main_render["output_path"],
            thumb_path=main_render["thumb_path"],
            title=yt_title,
            description=yt_description,
            tags=["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"],
            show_dca=show_dca,
            cur_p=mdata["cur_p"],
            btc_pct=mdata["btc_pct"],
            fng_num=mdata["fng_num"],
            is_test=is_test,
        )

        try:
            youtube_uploader.upload_short_video(
                youtube,
                short_path=short_video_path,
                main_video_id=main_video_id,
                cur_p=mdata["cur_p"],
                fng_num=mdata["fng_num"],
                today_str=today_str,
                short_title=short_title,
            )
        except Exception as error:
            # 主片已成功上傳後，Shorts API 失敗不應讓整個工作流程回報全數失敗；
            # 本機 daily_short.mp4 與發佈清單仍會保留，供後續補發。
            print(f"警告：主片已上傳，但 Shorts 上傳失敗；保留短片檔供補發：{error}")
    else:
        print("[5/5] ENABLE_YOUTUBE_UPLOAD!=true，完全跳過 YouTube 授權與上傳，保留成片供 GitHub Releases 預覽。")
        main_video_id = "PREVIEW_NO_UPLOAD"
        youtube_uploader._write_metadata("主片", yt_title, yt_description, "w")

    # 輸出標準化發佈清單，供雲端一鍵發佈腳本（publish_preview_to_youtube.py）使用。
    main_video_url = f"https://www.youtube.com/watch?v={main_video_id}"
    short_description = (
        f"{today_str} 幣圈市場 50 秒快訊。\n\n"
        "📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://is.gd/2JOYTF\n\n"
        f"▶️ 完整市場解析：{main_video_url}\n\n"
        + broll_credits
        + "本內容為公開資訊整理，不構成投資建議。加密貨幣波動劇烈，請審慎評估風險。\n"
        "#Shorts #比特幣 #BTC #以太幣 #ETH #幣看趨勢"
    )
    youtube_uploader.write_publish_bundle(
        pipeline_type="daily",
        main_video_path="daily_main.mp4",
        short_video_path="daily_short.mp4",
        thumb_path="daily_thumb.jpg",
        main_title=yt_title,
        main_description=yt_description,
        short_title=short_title,
        short_description=short_description,
        tags=["比特幣", "加密貨幣", "BTC", "ETH", "SOL", "技術分析", "均線", "RSI", "幣看趨勢"],
        first_comment=youtube_uploader._build_engagement_comment(
            mdata["cur_p"], mdata["btc_pct"], mdata["fng_num"], show_dca
        ),
    )

    # 確保產出標準預覽檔名供 GitHub Releases 發布
    import shutil
    if os.path.exists(main_render["output_path"]):
        shutil.copyfile(main_render["output_path"], "daily_main.mp4")
    if os.path.exists(short_video_path):
        shutil.copyfile(short_video_path, "daily_short.mp4")
    if os.path.exists(main_render["thumb_path"]):
        shutil.copyfile(main_render["thumb_path"], "daily_thumb.jpg")

    daily_learner.archive_episode(
        is_test=is_test,
        upload_id=main_video_id,
        mdata=mdata,
        sdata=sdata,
        audit=audit,
    )
    print("=== 全流程執行完畢！ ===")


if __name__ == "__main__":
    main()
