# daily-crypto-podcast
daily-crypto-podcast

## 單一幣種深度專題

`.github/workflows/special_topic.yml` 每週六台灣時間 08:00 自動製作並上傳【幣看深潛】專題，也可在 GitHub Actions 手動選擇 `AUTO`、`BTC`、`ETH`、`SOL`、`XRP`、`ADA` 或 `ZEC`。手動測試可開啟 `test_mode`。

工作流執行 `build_special_topic.py`，需要設定 `GEMINI_API_KEY`、`GOOGLE_TTS_KEY`、`YT_CLIENT_ID`、`YT_CLIENT_SECRET` 與 `YT_REFRESH_TOKEN` Secrets。專題會使用公開 RSS、Binance 行情與恐慌指數資料；若根目錄同時有 `info.mp3` 和 `info.mp4`，便加入 15 秒片頭。

日常版與單幣專題版都會在最後一句後保留 1.5 秒停頓，再接 12 秒動態片尾；根目錄有 `outro.mp3` 時優先使用，否則使用 `info.mp3`，兩者皆不存在時使用靜音音軌。片尾音樂以 0.35 倍音量播放，淡入 1 秒並於最後 2.5 秒淡出。

## 日常版動態簡報

`.github/workflows/render.yml` 會安裝 Playwright 與 Chromium。`build_and_upload.py` 使用 1280×720 HTML/CSS 圖卡錄製動態進場，再以 FFmpeg 轉成 24fps 影片；超過 6 秒的圖卡會定格延長。簡報固定顯示 BTC、ETH、恐慌貪婪指數與 DCA 報酬狀態列，內容依頁面使用圖表、利多／風險對照或重點字卡版型。封面主題圖優先由 Gemini 圖像模型產生，失敗時依序改用 CoinGecko 幣種圖示或本機繪製的金屬幣徽。

正片聆聽頭像優先取自 `cat_single.png`／`dog_single.png`，缺少時再使用 VTuber 影片首幀或 `avatar.png`。若有 `cat_vtober.mp4`／`dor_vtober.mp4`，對應角色說話時會播放循環張嘴動畫、圓形裁切與霓虹外框；否則使用靜態發話頭像。角色只在正片對話時間顯示，字幕左右邊距為 210px。

Matplotlib 圖表使用至少 18pt 粗體標題、14pt 粗體刻度與圖例；圖表簡報採 54:46 左右欄，右側重點維持單欄且最多五項。

主影片逐字稿以投影片錨點決定圖卡切換，時間取自每段 TTS 音檔實長；缺漏或亂序錨點會插值補齊。BTC 模擬 DCA 自 2025-10-25 起每月 25 日投入 NT$10,000；僅週五或每月 26 日加入由比特汪播報的模擬帳本與專屬圖卡，列出近期買價及歷史高低價。加入 DCA 圖卡時共 15 張，其他日常版為 14 張。以太喵主打 ETH 生態與客觀風險分析，比特汪主打 BTC 長期持有；節目角色帳本屬模擬紀錄，並非真實成交。簡報每頁最多強調 3 個核心金額、百分比或指標值，日期、編號與一般描述數字不加底線。語音會將英文 HODL 縮寫統一轉成自然中文。頻道不鼓勵盲目短線或高槓桿交易；觀察卡聚焦未來 24 小時（今日至明晨）。

日常版完成主影片與縮圖後，會另生成 720×1280 的直式 Shorts：正式模式逐字稿約 230 字，測試模式影片固定為 15 秒；使用 Neural2 語音、52pt 字幕與動態 HTML 畫面，並在 Shorts 說明欄連回當日主影片。上傳隱私設定沿用主影片的「不公開」狀態。
