# 翰林版六年級分數除法線上診斷測驗

這份專案由原本的單一 HTML 測驗改成可部署的線上測驗服務：

- 學生掃 QR code 進入 `/quiz` 作答
- 每次開始、作答、換題與完成都會記錄作答歷程
- 交卷結果寫入 SQLite 資料庫
- 教師可用密碼登入後台查閱全班資料與弱點分析
- 可部署到 Render，並用 Persistent Disk 保存資料庫

## 本機啟動

```bash
python server.py
```

開啟：

```text
http://127.0.0.1:8765/
```

預設教師密碼：

```text
teacher123
```

正式部署請務必設定 `ADMIN_PASSWORD`。

## Render 部署

1. 將整個 `hanlin-fraction-division-online` 資料夾上傳到 GitHub。
2. 到 Render 建立 Web Service，連接該 repository。
3. 如果 repository 裡有多個資料夾，Root Directory 設為：

```text
hanlin-fraction-division-online
```

4. 設定環境變數：

```text
ADMIN_PASSWORD=請自行設定教師密碼
DB_PATH=/var/data/quiz_records.sqlite3
```

5. 使用 Persistent Disk，掛載路徑：

```text
/var/data
```

部署完成後，首頁會顯示學生作答 QR code；教師後台可由首頁或 `/teacher` 進入。
