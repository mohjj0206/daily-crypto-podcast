import os
from datetime import datetime
from zoneinfo import ZoneInfo

import market_data
import chart_maker
import script_writer
import media_engine
import youtube_uploader


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

    # ── 5. 上傳 YouTube、發布留言與播放清單歸類 ───────────────
    print("[5/5] 上傳至 YouTube 並發布頻道互動...")
    youtube = youtube_uploader.build_youtube_client()
    main_video_id = youtube_uploader.upload_main_video(
        youtube,
        video_path=main_render["output_path"],
        thumb_path=main_render["thumb_path"],
        title=main_render["video_title"],
        description=f"📊 比特汪 BTC 定期定額公開帳本（即時追蹤）：https://mohjj0206.github.io/daily-crypto-podcast/\n\n{main_render['v_desc']}",
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

    print("=== 全流程執行完畢！ ===")


if __name__ == "__main__":
    main()
