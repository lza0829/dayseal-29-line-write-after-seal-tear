# Dayseal（日封台）

收银流水按营业日归集，日结封账后锁定；长短款可查。

- 前端 http://127.0.0.1:5700
- API http://127.0.0.1:10700

## 解封口径（唯一策略：作废原批重开）

已 sealed 的营业日若需补录流水，只走一条链路：

1. `POST /api/close/reopen`（必须带 `reason`）：把该日现行 `sealed` 批次改为 `voided`，
   记录 `voided_at` / `void_reason`，并写入 `reopen_requests` 审计行；
   原批的 `system_total` / `declared_cash` / `variance` / `line_count` 不改写，历史快照只读。
2. 写锁随之解除：该日流水可新增 / 修改 / 删除。
3. 重新 `POST /api/close/seal`：生成新批号（`CB-xxxx` 单调递增，不复用），
   新批为唯一现行批次；长短款以新批为准。
   旧 `voided` 批通过 `superseded_by_batch_id` 指向新批，不再显示为现行已封。

判定「已封」的唯一依据是 `close_batches.status='sealed'`：写闸、封账预览、
总览计数（现行批次数、现行已封天数、待处置长短款）都取自同一判定，
不会出现「页面已解封但写仍 409」或「批仍 sealed 却能改流水」。

## 启动

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 10700 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5700
```
