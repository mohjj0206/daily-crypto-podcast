# daily-crypto-podcast
daily-crypto-podcast

## 單一幣種深度專題

`.github/workflows/special_topic.yml` 每週六台灣時間 08:00 自動製作並上傳【幣看深潛】專題，也可在 GitHub Actions 手動選擇 `AUTO`、`BTC`、`ETH`、`SOL`、`XRP`、`ADA` 或 `ZEC`。手動測試可開啟 `test_mode`。

工作流執行 `build_special_topic.py`，需要設定 `GEMINI_API_KEY`、`GOOGLE_TTS_KEY`、`YT_CLIENT_ID`、`YT_CLIENT_SECRET` 與 `YT_REFRESH_TOKEN` Secrets。專題會使用公開 RSS、Binance 行情與恐慌指數資料；若根目錄同時有 `info.mp3` 和 `info.mp4`，便加入 15 秒片頭。
