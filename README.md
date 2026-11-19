# daily-crypto-podcast
daily-crypto-podcast

## 單一幣種深度專題

`.github/workflows/special_topic.yml` 每週六台灣時間 08:00 自動製作並上傳【幣看深潛】專題，也可在 GitHub Actions 手動選擇 `AUTO`、`BTC`、`ETH`、`SOL`、`XRP`、`ADA` 或 `ZEC`。手動測試可開啟 `test_mode`。

工作流執行 `build_special_topic.py`，需要設定 `GEMINI_API_KEY`、`GOOGLE_TTS_KEY`、`YT_CLIENT_ID`、`YT_CLIENT_SECRET` 與 `YT_REFRESH_TOKEN` Secrets。專題會使用公開 RSS、Binance 行情與恐慌指數資料；若根目錄同時有 `info.mp3` 和 `info.mp4`，便加入 15 秒片頭。

日常版與單幣專題版都會在最後一句後保留 1.5 秒停頓，再接 12 秒動態片尾；根目錄有 `outro.mp3` 時優先使用，否則使用 `info.mp3`，兩者皆不存在時使用靜音音軌。片尾音樂以 0.35 倍音量播放，淡入 1 秒並於最後 2.5 秒淡出。

## 日常版動態簡報

`.github/workflows/render.yml` 會安裝 Playwright 與 Chromium。`build_and_upload.py` 使用 1280×720 HTML/CSS 圖卡錄製動態進場，再以 FFmpeg 轉成 24fps 影片；超過 6 秒的圖卡會定格延長。封面主題圖優先由 Gemini 圖像模型產生，失敗時依序改用 CoinGecko 幣種圖示或本機繪製的金屬幣徽。

主影片逐字稿以 `[SLIDE_1]` 至 `[SLIDE_15]` 錨點決定圖卡切換，時間取自每段 TTS 音檔實長；章節時間也沿用相同錨點。第 2 張圖卡以歷史月底市值柱狀圖對照累計投入成本線（當月顯示截至今日估值），語音只報當月買進概況與累計成績。簡報每頁最多強調 3 個核心金額、百分比或指標值，日期、編號與一般描述數字不加底線。語音會將英文 HODL 縮寫統一轉成自然中文。頻道以長期持有與紀律 DCA 為核心，不鼓勵盲目短線或高槓桿交易；觀察卡聚焦未來 24 小時（今日至明晨）。

日常版完成主影片與縮圖後，會另生成 720×1280 的直式 Shorts：正式模式逐字稿約 230 字，測試模式影片固定為 15 秒；使用 Neural2 語音、52pt 字幕與動態 HTML 畫面，並在 Shorts 說明欄連回當日主影片。上傳隱私設定沿用主影片的「不公開」狀態。
