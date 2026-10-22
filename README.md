# district-news-data

「商圈觀測」網站 AI 精選頁（/curated）的資料來源。

- `data/latest.json`：網站讀取的資料（近 14 天，schema_version 1）
- `data/archive/`：每日整理紀錄
- `inputs/`：GitHub Actions 於工作日 05:23–16:23 每小時自動抓取的網站候選清單
- `reference/priority-districts-115.json`：115 年 289 處重點商圈名單
- `CURATION.md`：每日整理的作業程序（收錄與排除規則）
- `scripts/merge.py`、`scripts/validate.py`：合併與格式驗證

資料由 Claude 依 CURATION.md 於工作日自動整理；內容為公開新聞標題、AI 摘要與原文連結，請以原媒體報導為準。
