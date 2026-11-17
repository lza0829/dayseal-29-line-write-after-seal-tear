# Dayseal（日封台）

收银流水按营业日归集，日结封账后锁定；长短款可查。

- 前端 http://127.0.0.1:5700
- API http://127.0.0.1:10700

## 封账写闸口径（唯一策略：禁写最高）

已 sealed 的营业日，**所有**写入入口一律拒写——新增、修改（含支付方式/金额）、
删除、批量导入、冲正、改归属日、解款附属写入，没有按路径区分的例外。
判定「已封」的唯一依据是 `close_batches.status='sealed'`：写闸、封账预览、
总览计数、解款与旁路矩阵都取自同一判定。

- 统一闸口在 `app/write_paths.py::assert_writable`，所有 service 写入口都经过它，
  同一 `BEGIN IMMEDIATE` 事务内完成「查封态 → 写入/拒写 → 审计」，并发安全。
- 被封日拒写的旁路会写一条 `write_conflicts` 审计行（`outcome=rejected`）；
  前端只反映后端结论，不承担任何写闸职责。

## 钉扎指纹与冻结有效集合

封账时除了批头（批号/系统合计/笔数/差额），还会冻结：

- `sealed_line_snapshot`：当次有效流水的完整快照（回滚目标）；
- `line_fingerprint`：快照有效集合的 SHA-256；
- `header_fingerprint`：批头的 SHA-256。

`GET /api/close/integrity` 只读比对「现行有效集合 vs 冻结快照」与批头指纹。
若已有封后写入绕过了闸口（历史数据 / 直接改库 / 并发旧版本），下一次闸口检查
或 `POST /api/close/repair` 会按快照回滚该日流水、恢复批头合计与两个指纹，
并写 `write_conflicts`（`outcome=rolled_back`）。sealed 现行批次的
`system_total / line_count / variance` 因此始终与有效集合一致。

## 解封口径（作废原批重开）

已 sealed 的营业日若需补录流水，只走一条链路：

1. `POST /api/close/reopen`（必须带 `reason`）：先按冻结快照回滚任何封后漂移，
   再把该日现行 `sealed` 批次改为 `voided`，记录 `voided_at` / `void_reason`，
   并写入 `reopen_requests` 审计行；原批合计/明细/快照不改写，历史快照只读。
2. 写锁随之解除：该日流水可新增 / 修改 / 删除 / 导入 / 冲正 / 改日 / 解款。
3. 重新 `POST /api/close/seal`：生成新批号（`CB-xxxx` 单调递增，不复用），
   新批为唯一现行批次；旧 `voided` 批通过 `superseded_by_batch_id` 指向新批，
   不再显示为现行已封。

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
