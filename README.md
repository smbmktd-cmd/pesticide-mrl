# 農藥殘留容許量查詢

給保健食品原料的品管與採購用的對照站。輸入作物與農殘、可加上檢出量（ppm），畫面上會給出容許量、合格或超標，以及**每一筆都有的適用依據**（個別標準、類別標準、其他類除外、註五定量極限、免訂、禁用，或第3條不得檢出）。

這是對照工具。殘留是否真的合格，以《農藥殘留容許量標準》和食品藥物管理署公告為準。

## 怎麼用

1. 打開網站首頁，輸入原料名稱。可以用附表五的作物名，也可以用俗稱（洛神花、地瓜、高麗菜）。
2. 若名稱對到一種以上，或附表五沒有這個名字，畫面上會列出候選。**不會自行猜類別。** 附表五每一類都以「等」結尾，沒寫出來的作物不能自動歸進去。
3. 輸入農殘的中文普通名稱或英文國際普通名稱。檢出量可以留空；`ND`、`未檢出`、`<0.01` 也接受。
4. 選新鮮或乾燥。第2條以市售型態計算，**乾燥品不另乘濃縮係數**。沒有個別標準時，乾燥蔬果、香辛與草木本的註五兜底是「其他(茶類)*」。
5. 同一原料有一整份篩檢表時，用「批次報告」貼上或上傳 CSV（欄位：農殘名稱、檢出量），再匯出帶有容許量、依據、判定的 CSV。

查詢順序：

1. 附表三 免訂
2. 該作物的個別列（小黃瓜與「胡瓜(小黃瓜除外)」分開，不合併）
3. 「其他○類(A、B除外)」
4. 子類整列，然後類別整列
5. 其他(蔬果類 / 穀類 / 茶類)*，註五定量極限
6. 附表四 禁用
7. 表中未列，依第3條不得檢出

範例（以目前快照的附表一核對）：苦瓜 + 貝芬替 = 0.5 ppm，依瓜菜類標準；番茄 + 貝芬替 = 1 ppm，依其他果菜類(洛神葵除外)標準；西瓜 + 貝芬替 = 2 ppm，依瓜果類標準。苦瓜在瓜菜類，不在瓜果類。

## 資料來源

查詢用的附表一至附表四以[食品藥物消費者專區](https://consumer.fda.gov.tw/Law/PesticideList.aspx?nodeID=520&tk=1)為準（匯出 Excel，失敗才改逐頁 `p=`）。開放資料 CSV 只拿來交叉比對，差異寫在頁首和更新歷程，不會蓋過專區的數值。附表五作物分類仍用開放資料與法規頁上的最新版。

| 資料 | 查詢採用 | 交叉比對 |
| --- | --- | --- |
| 附表一 容許量 | 消費者專區 `tk=1` | https://data.fda.gov.tw/data/opendata/export/13/csv |
| 附表二 外源性 | 消費者專區 `tk=7` | https://data.fda.gov.tw/data/opendata/export/173/csv |
| 附表三 免訂 | 消費者專區 `tk=2` | https://data.fda.gov.tw/data/opendata/export/14/csv |
| 附表四 禁用 | 消費者專區 `tk=3` | https://data.fda.gov.tw/data/opendata/export/15/csv |
| 附表五 作物分類 | https://data.fda.gov.tw/data/opendata/export/16/csv | 法規附表五 |
| 法規修正日 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=L0040083 | |

連線會換幾種 TLS／HTTP 設定重試（HTTP/1.1、TLS 1.2、較寬的 cipher）。專區這次抓不到時，沿用上一份成功的專區快照，**不會改用 CSV 的數值**，頁首會標明資料是沿用的。若從來沒有成功下載過專區，同步會失敗並保留上一份查詢檔，不會偷偷改成 CSV。

目前倉庫裡的專區快照是 2026-11-26 09:35–09:40（台北）從可連上該站的機器，用匯出 Excel 的 postback 抓下來的：附表一 8,274 列、附表二 1、附表三 44、附表四 65。附表五 22 類來自同站法規 ODT（`GetFile.ashx?type=gfile&id=14380`）。其中 16 列備註寫明已刪除、仍在緩衝期（陶斯松 2、大克蟎 1、巴拉刈 1、甲基益發靈 12）；查詢會把備註和「此標準已刪除，緩衝期內適用」放在限值旁邊。開放資料禁用 67 列是兩筆被斷行拆開，合併後與專區 65 筆相同。

每日同步用同一種 postback（`pipeline/fetch_site_mrl.py`）。GitHub 代管機器若再次在 TLS 交握被切斷，就沿用這份快照，不改採 CSV。

開放資料若有缺字（例如殺蟎劑變成 `殺?劑`），而專區頁本身是完整字，就用專區的字。仍缺的才用法規 PDF 對回。原始字串保留。`0.01*` 的星號代表註五定量極限。

附表五每天和容許量表一起比對。作物被改掛到別的類別時，歷程類型是「分類變更」。

## 每天怎麼更新

`.github/workflows/sync.yml` 以 `0 22 * * *`（UTC）排程，也就是台北時間 06:00，並可手動執行。流程會：

1. 下載消費者專區附表一至附表四（先匯出 Excel，否則逐頁）。連不上就停用上次成功的專區快照，不改採 CSV。
2. 下載五張開放資料 CSV，只用來交叉比對。少列、多列、限值不同都記成警告。
3. 讀法規頁的修正日。附表五仍以開放資料這份分類表為準。
4. 和上一份查詢快照做列級差異。容許量的鍵是正規化後的國際普通名稱 + 原始作物類別；附表五的鍵是類別 + 作物。
5. 有差異才追加到 `data/history.jsonl`（新增、刪除、限值變更、備註變更、分類變更、開放資料差異），每次執行都追加 `data/sync-runs.jsonl`。
6. 寫進 `site/data/`，提交回倉庫，並在 `main` 上發布 GitHub Pages。

用 `GITHUB_TOKEN` 推上 `main` 時，不會再觸發另一條 Pages 工作流程，所以同步工作流程自己會部署一次。

手動在本機重建（不連網，用已下載的 CSV）：

```bash
python3 -m pip install -r requirements.txt
python3 pipeline/sync.py --offline
python3 -m pytest pipeline/tests -q
node pipeline/tests/lookup.test.mjs
python3 -m http.server -d site 8000
```

完整同步（會連網、需要 `pdftotext`，套件名稱 `poppler-utils`）：

```bash
python3 pipeline/sync.py
```

## 請倉庫擁有者打開 Pages

網站不會在第一次推上 GitHub 時自動出現，需要擁有者在網頁上點這幾步：

1. 打開這個倉庫的 **Settings → Pages**。
2. 在 **Build and deployment** 的 **Source** 選擇 **GitHub Actions**（不要選 Deploy from a branch）。
3. 打開 **Settings → Actions → General → Workflow permissions**，選 **Read and write permissions**，讓每日同步可以把新的 JSON 提交回 `main`。若這裡維持唯讀，同步工作流程會在 `git push` 失敗。
4. 若 `main` 有分支保護、不允許 GitHub Actions 直接推送，請允許 `github-actions[bot]` 推送，或改成由人員合併資料更新。沒有保護規則時不必額外設定。
5. 到 **Actions → 每日更新農藥殘留資料 → Run workflow**，分支選 `main`，手動跑一次，確認資料與網站都更新。程式合併到 `main` 時，「部署 GitHub Pages」也會發布目前的 `site/`。若合併當下 Pages 的 Source 還沒改成 GitHub Actions，那一次部署會失敗；改好之後打開該次工作流程按 **Re-run**。
6. 網站網址：**https://smbmktd-cmd.github.io/pesticide-mrl/**

第一次啟用 Pages 後，GitHub 可能還要一兩分鐘才看得到頁面。
