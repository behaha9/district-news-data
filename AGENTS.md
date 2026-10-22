# 給 AI 代理的說明（Codex / Claude 共用）

這個倉庫只存放「商圈觀測」AI 精選頁的資料，沒有網站程式碼。

- 每次被要求「整理商圈新聞」時，完整依照 `CURATION.md` 執行。
- `inputs/` 由 GitHub Actions 自動抓取網站候選清單，只能讀、不得修改。
- 只能修改 `data/` 資料夾；不得修改 `CURATION.md`、`AGENTS.md`、`scripts/`、`reference/`、`README.md`（規則調整須由使用者確認）。
- 上傳前必須執行 `python3 scripts/merge.py new_items.json` 與 `python3 scripts/validate.py`，驗證通過才可提交。
- 直接提交並推送到 `main`，不要開分支或 PR（網站只讀 main 的 `data/latest.json`）。
- 每則新聞與 PTT 文章的網址都必須是實際查到的原始網址，不得捏造；確認不了發布日期就不收。
- 全程使用臺灣繁體中文。
