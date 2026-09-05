# 跨源一致性对账：数据质量 Doctor + 可追溯 — 设计文档

> 子项目 D（数据质量 Doctor + 跨源校准）设计。范围在 brainstorm 阶段已与用户确认：
> **只做跨源一致性对账（检测 → 落库 → Web 视图 → 去重告警）**，不做自动修正数据、不改
> 选源优先级逻辑、不做重取编排。

## 1. 背景与现状

数据提供层（`data_provider/base.py`，3877 行）为每个市场装配多个 fetcher，按
`REALTIME_SOURCE_PRIORITY`（实时盘）与 fetcher capability 过滤（日 K）排序。已有能力：

- **优先级链 + 单源熔断**：`get_realtime_quote` / `get_daily_data` 按优先序取第一个成功源；
  单源连续失败超过 `DATA_SOURCE_QUARANTINE_THRESHOLD` 进熔断并告警一次（A 子项目已做）。
- **新鲜度标记**：`_enrich_realtime_quote`（`base.py:1743`）按 `provider_timestamp` 算
  `is_stale`/`stale_seconds`。
- **源间字段补齐**：`_supplement_quote`（`base.py:2144`）已存在「用次选源补主源字段」的既有模式。

**真实缺口（本子项目只补这个）**：

> 取数管理器对每个标的只采用**优先链里选中的第一个成功源**的返回值，**其余源的值被直接丢弃、
> 从不对比**。因此一个「错误但成功」的源——两个源给出矛盾价格、错日数据、或某个关键字段缺失——
> 会**静默进入下游分析与报告**。全仓 `grep cross_source / source_disagree / price_discrep` **零命中**，
> 跨源对账确认为全新能力。`docs/data-source-stability.md:183` 也把「数据源 Doctor 页」列为未做未来项。

## 2. 变更范围

> 只补「选源成功后的跨源可比对」这一处。告警复用 `system_alert.send_system_alert`，落库与 Web 复用
> C 子项目建立的 repo→API→Web 镜像模式。不重建取数、熔断、补全逻辑。

### 变更 1：对账核心逻辑（后端数据提供层）

在 `data_provider/base.py` 新增对账方法，并接上两条取数路径的成功返回点：

- **新增 `_reconcile_cross_source(primary, cross, market, stock_code)`**：比对三者
  - **价差**：仅当 `primary.price` 为正数时计算
    `abs(primary.price - cross.price) / primary.price * 100 > data_quality_price_diff_threshold_pct`
    → `price_discrepancy`；`primary.price <= 0`（无价或无效）时**跳过价差比对**（避免除零、也归入字段缺失判断）。
  - **错日/时间错位**：`primary.provider_timestamp` 与 `cross.provider_timestamp` 相差超过
    `data_quality_date_mismatch_tolerance_seconds`（默认 3600s）→ `date_mismatch`。
  - **字段缺失**：关键字段（`price`/`provider_timestamp`/`open_price`/`high`/`low`/`pre_close`）
    在 primary 或 cross 其一为 `None`/缺省 → `field_missing`。
  - 命中任一 → 落库 + 告警（见变更 2、3）；全部通过 → 仅 debug 日志。
- **接入实时盘**：
  - **A 股**：在主循环 `else` 分支（`base.py:1984`，`quote` 现已是次选源返回值、零额外网络开销），
    `break` 前用 `primary_quote` 与 `quote` 比对。A 股循环是**唯一零额外开销**的接入点。
    注意：A 股「主源单源即满足」的早退路径（`base.py:1975-1980`，字段齐全且不陈旧）**未取次源**，
    该路径**不做对账**（主源数据完整可信、价差对账的成本收益不划算），仅「主源需补齐、走完循环取得
    次源」的路径执行对账。
  - **美股/港股**：在双源路由（`base.py:1866`）`return` 前，用现成 `secondary_src`
    （`base.py:1841/1847`）调 `_try_fetcher_quote(stock_code, secondary_src)` 做交叉验校。
- **接入日 K**：`get_daily_data` 成功返回前（`base.py:1419/1499`），用 `fallback_to` 或
  `fetchers[attempt]` 再取一次该源 `get_daily_data`，对 `df.iloc[-1]['close']` 与 `df.index[-1]` 做
  价差 / 交易日比对（DataFrame 分支单独处理）。
- **门控与健壮性**：
  - 整个对账过程包 try/except + warning，**绝不让对账失败反向影响主取数结果或抛错**；
    次选源取数失败（None）时**静默跳过**，不视为异常（单个源失败本来就在优先级链语义内）。
  - 由 `data_quality_reconciliation_enabled`（默认 `True`）门控；关闭时不执行任何额外调用/比对。

> 对账是「观测 + 告警」，不自动改价格、不自动改选源、不重取——即使用户配置了多个源，选源逻辑仍按
> 现有 `REALTIME_SOURCE_PRIORITY` 优先序执行。

### 变更 2：对账记录落库（后端数据层）

- **表 `data_quality_discrepancies`**：新增 `DataQualityDiscrepancyRecord`（`src/storage.py`）：
  `id`，`market: String(16), index`，`stock_code: String(32), index`，
  `issue_type: String(32), index`（`price_discrepancy`/`date_mismatch`/`field_missing`），
  `primary_source: String(32)`，`secondary_source: String(32)`，
  `primary_price: Float`，`secondary_price: Float`，`price_diff_pct: Float`，
  `primary_ts: String(32)`，`secondary_ts: String(32)`，`detail: Text`，
  `created_at: DateTime, default now, index`。
  复合索引 `(market, stock_code, issue_type, created_at)`。
- **仓储 `DataQualityDiscrepancyRepository`**（`src/repositories/data_quality_discrepancy_repo.py`，
  镜像 `NotificationDeliveryRepository`）：
  - `record_discrepancy(fields: Dict[str, Any]) -> DataQualityDiscrepancyRecord`（插入+commit+refresh）。
  - `list_discrepancies(*, market=None, stock_code=None, issue_type=None, page=1, page_size=20)
    -> Tuple[List[DataQualityDiscrepancyRecord], int]`（条件筛选，按 `created_at desc, id desc` 排序，分页）。

### 变更 3：对账异常告警（复用 system_error 路由）

- 命中异常时调用 **现成** `system_alert.send_system_alert(content, dedup_key=..., enabled=True)`：
  - `dedup_key=f"data-quality:{market}:{stock_code}:{issue_type}"`，复用 `_msg_dedup_keys` 进程内当日去重
    （同类型同标的当日一次，跟 B 的一致）。
  - 发送失败只记日志（`send_system_alert` 本身绝不抛、绝不触发自身告警兜死环），**落库仍保留**——
    告警渠道故障不丢对账记录。
- 不开新通知渠道配置；直接用 `notification_system_error_channels`（未配置自动回退报告/主渠道）。

### 变更 4：对账记录 API（后端展示）

- **`api/v1/schemas/data_quality.py`（新）**：`DataQualityDiscrepancyItem`（id, market, stockCode,
  issueType, primarySource, secondarySource, primaryPrice, secondaryPrice, priceDiffPct, primaryTs,
  secondaryTs, detail, createdAt，snake→camel 序列化）、`DataQualityDiscrepancyListResponse`
  （items, total, page, pageSize）。
- **`api/v1/endpoints/data_quality.py`（新）**：`GET /data-quality/discrepancies`，参数
  `market?/stock_code?/issue_type?/page/page_size`，沿用 `is_auth_enabled()` + `_internal_error` 惯例，
  镜像 `alerts.list_notifications`（读 `DataQualityDiscrepancyRepository().list_discrepancies(...)`）。
- **挂载**：`api/v1/router.py` 新增 `data_quality` import + `include_router(data_quality.router, prefix="/data-quality", tags=["DataQuality"])`。

### 变更 5：Web 数据质量视图（前端落地）

在 `apps/dsa-web` **设置页**新增「数据质量异常」区块，与 C 子项目的「通知投递」区块同名模式：

- `src/types/dataQuality.ts`（新）：`DataQualityDiscrepancyItem`、`DataQualityDiscrepancyListResponse`、
  `DataQualityDiscrepancyListQuery`（与后端 camelCase 对齐）。
- `src/api/dataQuality.ts`（新）：`dataQualityApi.getDiscrepancies(query)` 调
  `GET /api/v1/data-quality/discrepancies`，`toCamelCase` 归一。
- `src/pages/SettingsPage.tsx`：新增「数据质量异常」卡片（`DataQualityCard`），提供
  market / issue_type 过滤 + 分页 + 刷新，镜像 `NotificationDeliveryCard`。
- `src/i18n/uiText.ts`：新增 zh + en 文案 key（含 issue_type 中文映射、市场名映射）。

### 变更 6：配置与文档校对

- `src/config.py`：新增字段（`bias_threshold` 附近的阈值区）：
  - `data_quality_reconciliation_enabled: bool = True`
  - `data_quality_price_diff_threshold_pct: float = 1.0`
  - `data_quality_date_mismatch_tolerance_seconds: int = 3600`
  env parse：`DATA_QUALITY_RECONCILIATION_ENABLED` / `DATA_QUALITY_PRICE_DIFF_THRESHOLD_PCT` /
  `DATA_QUALITY_DATE_MISMATCH_TOLERANCE_SECONDS`（`parse_env_bool`/`parse_env_float`/`parse_env_int` 风格）。
- `.env.example`：新增三项说明（含默认值与含义）。
- `docs/data-source-stability.md`：更新「未来项」段（`:183`）——注明跨源对账/数据质量 Doctor 已落地，
  无跨源对账不再是缺口。
- `docs/CHANGELOG.md` `[Unreleased]`：追加**扁平**条目：
  - `- [新功能] 跨源一致性对账：选源成功后用次选源比对价差/交易日/字段缺失，命中记录数据质量异常并可按 system_error 路由告警`
  - `- [新功能] 数据质量异常列表 API（/api/v1/data-quality/discrepancies）与设置页数据质量视图`

## 3. 配置项（新增）

| 字段 | 类型 | 默认 | env | 说明 |
| --- | --- | --- | --- | --- |
| `data_quality_reconciliation_enabled` | `bool` | `True` | `DATA_QUALITY_RECONCILIATION_ENABLED` | 是否对选源结果做跨源对账（关闭则零调用零比对） |
| `data_quality_price_diff_threshold_pct` | `float` | `1.0` | `DATA_QUALITY_PRICE_DIFF_THRESHOLD_PCT` | 两源价差超过该百分比判定为价差异常 |
| `data_quality_date_mismatch_tolerance_seconds` | `int` | `3600` | `DATA_QUALITY_DATE_MISMATCH_TOLERANCE_SECONDS` | 两源行情时间相差超过该秒数判定为错日/时间错位 |

## 4. 验证矩阵

- **Python 后端**：`./scripts/ci_gate.sh`；改动文件 `python -m py_compile`。新增测试：
  - `tests/test_data_quality_discrepancy_repo.py`：`record_discrepancy` 落库、`list_discrepancies`
    条件筛选 + 分页排序。
  - `tests/test_data_quality_reconciliation.py`：
    - 价差超阈值 → `price_discrepancy`；时间错位 → `date_mismatch`；字段缺失 → `field_missing`；
    - `data_quality_reconciliation_enabled=False` → 不比对不落库；
    - 次选源取数失败（None）→ 静默跳过不抛错；
    - 对账逻辑异常 → 主取数结果仍返回、不拖垮调用方；
    - 命中 → 调用 `send_system_alert`（该处 mock）且落库保留。
  - `tests/test_data_quality_discrepancies_api.py`：`GET /data-quality/discrepancies` 正常返回 + 筛选 + 鉴权。
- **Web**：`cd apps/dsa-web && npm ci && npm run lint && npm run build`。设置页 Web UI 变更按
  `AGENTS.md` 需在 PR 描述附截图（若暂不截图需说明原因）。
- **文档/治理**：无 `AGENTS.md`/`.claude/skills` 改动，不触发 `check_ai_assets.py`。

## 5. 风险与回滚

- **风险（额外网络调用）**：美/港与日 K 路径的对账会再用一次次选源取数。为此：
  - A 股实时盘**零额外开销**（复用主循环已取的次选 quote）；
  - 其余路径由 `data_quality_reconciliation_enabled` 门控，且次选源失败/超时按现有 `_try_fetcher_quote`
    语义静默跳过，绝不让对账拖慢或反向拖垮主取数；
  - 为稳妥，设计默认 `True`，但对账整体是 try/except 包住 + warning 降级。
- **风险（告警环路）**：对账告警复用 `send_system_alert`，该函数本身绝不抛、绝不再触发告警，无环路。
- **风险（表语义）**：新表 `data_quality_discrepancies` 与 `alert_notifications` / `notification_deliveries`
  语义不同（数据质量异常 vs 渠道投递回执），三者互补，无重复、无平行功能。
- **回滚**：全部为新增方法/表/端点/区块，独立可回退；将 `DATA_QUALITY_RECONCILIATION_ENABLED=false`
  即停用对账（既有选源、熔断、投递回执不受影响）。

## 6. 不在本子项目范围（明确不做）

- 不做自动修正数据（不偷偷改成次选源值、不回填）。
- 不改选源优先级逻辑（选源仍按现有 `REALTIME_SOURCE_PRIORITY` / fetcher 优先序执行）。
- 不做重取编排 / 自动刷新源。
- 不新建数据源 Doctor 健康面板（每源最近成功/失败/熔断/恢复的完整看板留作后续；A 已提供熔断告警，
  本子项目只补「跨源比对」这一处对账缺口）。
- 不新增除实时盘/日 K 外的其他数据路径对账（行情快照、基本面等留作后续）。
