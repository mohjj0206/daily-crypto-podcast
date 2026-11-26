import os
from datetime import datetime
from zoneinfo import ZoneInfo

import market_data
import chart_maker
import presentation
import script_writer
import media_engine
import youtube_uploader
import daily_learner


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
        short_video_path = media_engine.render_short_video(
            sdata, mdata, config
        )
    finally:
        tech_chart_dir.cleanup()
    audit = daily_learner.audit_episode(sdata.get("full_script", ""), "subs.ass", is_test)

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
        + f"{main_render['v_desc']}"
    )
    enable_yt = os.environ.get("ENABLE_YOUTUBE_UPLOAD", "false").strip().lower() == "true"
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

        youtube_uploader.upload_short_video(
            youtube,
            short_path=short_video_path,
            main_video_id=main_video_id,
            cur_p=mdata["cur_p"],
            fng_num=mdata["fng_num"],
            today_str=today_str,
        )
    else:
        print("[5/5] ENABLE_YOUTUBE_UPLOAD!=true，完全跳過 YouTube 授權與上傳，保留成片供 GitHub Releases 預覽。")
        main_video_id = "PREVIEW_NO_UPLOAD"
        youtube_uploader._write_metadata("主片", yt_title, yt_description, "w")

    # 輸出標準化發佈清單，供雲端一鍵發佈腳本（publish_preview_to_youtube.py）使用。
    main_video_url = f"https://www.youtube.com/watch?v={main_video_id}"
    short_title = (
        f"BTC {presentation.format_usd(mdata['cur_p'])}｜恐慌指數 {mdata['fng_num']} 點，這個訊號別忽略！ #Shorts"
        if mdata.get("fng_num") is not None else
        f"BTC {presentation.format_usd(mdata['cur_p'])}｜幣圈最新市場焦點與風險解析 #Shorts"
    )
    short_description = (
        f"{today_str} 幣圈市場 50 秒快訊。\n\n"
        "📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://is.gd/2JOYTF\n\n"
        f"▶️ 完整市場解析：{main_video_url}\n\n"
        "本內容為公開資訊整理，不構成投資建議。加密貨幣波動劇烈，請審慎評估風險。\n"
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
        is_test=is_test or not enable_yt,
        upload_id=main_video_id,
        mdata=mdata,
        sdata=sdata,
        audit=audit,
    )
    print("=== 全流程執行完畢！ ===")


if __name__ == "__main__":
    main()
