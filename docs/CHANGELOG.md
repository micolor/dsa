# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> For user-friendly release highlights, see the [GitHub Releases](https://github.com/ZhuLinsen/daily_stock_analysis/releases) page.

## [Unreleased]

- [修复] 回测页初始加载失败时不再显示成「暂无数据」：初始加载的 effect 内联 `init()`，第一句 `await backtestApi.getOverallPerformance()` 既没有 try/catch，effect 里也是裸 `init()` 没有 `.catch`，而该接口只把 404 吞成 null、其它状态码与网络错误一律 rethrow（axios 拦截器只在 401 时跳转，没有全局错误提示）。于是接口报 500 或超时（库未初始化、后端短暂不可用）时，`fetchResults` 永远不会执行、`setPageError` 永远不被调用（全文件只有 `fetchResults` / `fetchPerformance` 会写它），页面呈现为左侧「暂无指标」+ 主区「暂无结果」两个空态——用户会以为历史回测数据是空的，同时留下一个只有控制台可见的未处理 Promise rejection。现把初始加载纳入与 `fetchPerformance` 相同的错误路径（`console.error` + `setPageError`，并按 `perfRequestRef` 判过期、按 `mountedRef` 判卸载），顺带补上 `setIsLoadingPerf(true)`：此前 init 路径从不置位该标志，左侧指标区在请求期间会先闪一下「暂无指标」再变成卡片，这与 `fetchPerformance` 的行为也不一致。新增 2 例回归（初始请求失败时出现 `role="alert"` 且带原始错误信息、初始请求在途时左侧显示「正在加载指标...」），反向验证：恢复 HEAD 版本的 init 后这 2 例转红（前者完全查不到 alert），恢复后 11 例全绿
- [修复] `ENABLE_EASTMONEY_PATCH` 下东方财富的请求会永久改掉数据源模块的 headers：`patched_request` 里 `headers = kwargs.get("headers", {})` 拿的是**调用方传入的同一个 dict 的引用**，随后就地写入 `headers["User-Agent"]` 与 `headers["Cookie"] = f"nid18={nid}"`。efinance 把自己的模块级常量 `headers` 按引用传进来（全仓库 28 处调用点），于是第一次东方财富请求之后，那个常量里的 `User-Agent` 就恒为本次随机出来的值、并被注入一个 `nid18` Cookie：后续所有请求（含非东方财富域名、以及同一进程里复用这个 dict 的其它调用）都带着这份被改写的头，NID 失效后也无法通过「重新取值」恢复，而这段代码的注释写的却是「确保不破坏业务代码传入的 headers」——意图与实现相反。改为 `headers = dict(kwargs.get("headers") or {})` 先复制再改（顺带让显式传入 `headers=None` 不再抛 `TypeError`）。新增 1 例回归（传入一个共享 dict 走完整 `patched_request`，断言调用方 dict 未被改动、而实际发出去的头带上了新 UA 与 `nid18` Cookie），反向验证：换回 `kwargs.get("headers", {})` 后该例转红（`shared == before` 断言失败），恢复后与其余 10 例共 11 例全绿，flake8 无告警

- [修复] `ENABLE_EASTMONEY_PATCH` 下取不到 NID 的失败退避从未生效，每次东方财富数据请求都要先打一次授权接口：`_get_nid` 的缓存快路径是 `if _cache.data and now < _cache.expire_at`，而两个失败分支都把 `_cache.data` 置为 `None` 再写 `expire_at = now + 5 * 60`（注释写的是「设置较长过期时间，可避免频繁请求」）——数据为 None 时那个 `and` 直接短路，`expire_at` 写了也永远判不到，于是**同一个失效窗口内每个请求都会重新拿锁并发出一次完整请求**。锁是 `threading.Lock` 且覆盖整个请求，所以并发抓取线程会串在 30s 超时后面排队；每次数据抓取（`push2` / `push2his` / `fund.eastmoney.com`）额外付出一次授权尝试加 1–4s 随机休眠，一轮筛选/分析里几百次请求就从秒级变成数十分钟，而这段时间数据抓取本来该走的「拿不到 NID 就照常请求、失败了返回 None」降级路径根本没有机会执行。另外响应形状不符合预期时（JSON 根是数组/字符串、`data` 为 null、没有 `nid`）`data['data']['nid']` 抛出的 `TypeError` 不在 `except (KeyError, json.JSONDecodeError)` 覆盖范围内，会一路穿出 `_get_nid` 和没有 try 的 `patched_request` 打断当次数据抓取，与该函数「如果获取失败则返回 None」的契约相悖。现把失败退避独立成 `_cache.retry_after`（与 `data` 无关地判断，取锁后再判一次避免等锁期间的白跑），并抽出 `_record_failure` 统一记录失败与退避、`_extract_nid` 把形状不对一律降级成「没有可用 nid」，两条 except 也收敛到这两个入口。新增 10 例回归（失败后连续 5 次调用只发出 1 次请求、退避窗口过后会重试、成功令牌仍缓存 20s、7 种异常响应形状都返回 None 且只请求 1 次），反向验证：修复前这 6 例（退避 1 例 + 形状 5 例）转红，恢复后全绿；`tests/ -k "eastmoney or patch or fetcher or data_provider"` 757 例通过，flake8 无告警
- [修复] Web 端不认事件类告警，编辑会静默改掉阈值、列表把参数显示成 CCI：事件类告警（`event_dragon_tiger` / `event_capital_flow` / `event_announcement`）由 API 或 agent 创建，`GET /api/v1/alerts/rules` 不加类型过滤、且与其它规则同存于 `AlertRuleRecord` 一张表，所以它们照常出现在 `/alerts` 列表里并可进入编辑表单；但 Web 侧从类型定义到表单都不认识这三个类型，表现为四处独立降级——①阈值被静默改回默认值：`AlertType` 联合类型没有这三个值，表单的类型下拉在 `value` 不在候选里时只剩一个占位符（`Select.tsx` 的占位符分支），`buildParameters()` 对这三个类型也没有任何分支、落到底部的 `return {}` 把 `min_recent_count` / `min_abs_inflow` / `min_count` 整个丢掉，即便值保住了 `toSnakeRulePayload` 这份手写白名单也会把它们映射掉；后端 `normalize_event_alert_parameters` 收到空参数后按 `_DEFAULT_MIN_RECENT_COUNT=1` / `_DEFAULT_MIN_ABS_INFLOW=100_000_000.0` / `_DEFAULT_MIN_COUNT=1` 补默认值，接口返回 200、界面提示「创建成功/已更新」，而用户设的阈值（实测 5 亿主力净流入）已被改成 1 亿。②列表「参数」列把事件类规则显示成「CCI-- 上穿 --」：`formatParameters()` 的收尾是无条件 `return` 的 CCI 那一串，`cci_threshold` 并没有自己的分支，于是任何未被前面 `if` 覆盖的类型都套用了 CCI 的文案，看起来像规则本身配错了。现在补全：类型与中英文案认得这三个类型，列表按各自的阈值渲染（净流入按元分组显示）、CCI 改为显式分支且兜底只回落到类型名（后续新增类型不会再冒充别的类型）；编辑时把当前类型补进下拉候选（只用 `ALERT_TYPE_LABELS` 取标签、不提供新建入口），`buildParameters()` 对事件类原样回传 `editingRule.parameters`（表单没有对应字段，只能透传，返回空对象会让后端套默认值），`toSnakeRulePayload` 补上三个 snake_case 键。**顺带修掉本次改动自己引入的一处回归**：类型清单追加事件类之后，`marketAlertTypes` 原为 `alertTypes.slice(12)`（取到末尾），会把这三项混进市场范围的可选类型里——中文界面看不出来（`AlertRuleForm` 自带硬编码列表），英文界面会多出三项，改为显式收尾 `slice(12, 14)`。新增 4 例回归（表单：编辑 `event_capital_flow` 规则点「保存修改」后参数原样送达、英文界面市场范围不出现事件类；列表：三种事件类的标签与阈值、英文标签；API 层：`updateRule` 请求体带 `min_abs_inflow`——该路径此前无任何测试，顺带补上 `patch` mock），反向验证：分别把 `buildParameters()` 的事件分支关掉、把三个键从白名单删掉、把列表的事件分支删掉、把 `slice(12, 14)` 改回 `slice(12)`，对应用例各自转红，恢复后转绿；全量 122 文件 / 1307 用例全绿，`tsc --noEmit` / `eslint` / `npm run build` / `ui_governance` 均通过。浏览器实测（后端建三条事件类规则、只在停用状态下建并当场删除，避免触发真实通知）：编辑表单「规则类型」显示「主力资金流」而非占位符，保存后 `PATCH /api/v1/alerts/rules/4` 的请求体带 `parameters: {"min_abs_inflow":500000000}`、响应保持 5 亿；列表「参数」列显示「主力净流入 >= 500,000,000 元」；反向确认后端行为——对同一接口直接发 `parameters: {}` 会把阈值改成 1 亿
- [修复] `python main.py` 在分析流程失败时不再退出码 0：单次运行走的是 `_run_analysis_with_runtime_scheduler_lock` → `run_with_global_analysis_lock(task_runner=run_full_analysis, ...)`，而该 helper 的返回值表达的是「有没有拿到那把全局分析锁」，`task_runner` 的结果被直接丢掉；`run_full_analysis` 在异常分支是 `except Exception: logger.exception(...); return False`（定时/调度路径传 `raise_errors=True` 不受影响），于是分析实际失败、一份报告都没生成，`main()` 仍打印「程序执行完成」并 `return 0`。每日分析 workflow（`00-daily-analysis.yml`）与包装脚本正是拿 `python main.py` 的退出码判定当天成败，因此失败在 Actions 上显示为绿色通过。现让 `_run_analysis_with_runtime_scheduler_lock` 用一个本地 wrapper 捕获 `run_full_analysis` 的返回值并返回（不把共享 helper 的「锁是否拿到」语义与 runner 结果混在一个布尔里），`main()` 在「分析失败且本次不是 `--serve` 常驻」时 `logger.error` 并返回 1。**行为变更**：此前被刻意钉住的一例（`test_standalone_futu_downstream_failure_keeps_existing_exit_semantics`，Futu 持仓模式下 `_compute_trading_day_filter` 抛 RuntimeError → 退出码 0）随之下线，改名并在 `docs/CHANGELOG.md` 记录为 1——那次是「重构不改动退出码」的回归护栏，不是产品意图。新增 2 例回归（分析返回 False → 退出码 1 且日志点名、分析返回 True → 退出码 0）与改写后的 Futu 用例；反向验证：临时关掉 `main()` 的失败分支后这 2 例（及 Futu 那条）转红，恢复后转绿
- [修复] `ADMIN_AUTH_ENABLED` 改为进程环境优先，官方 Docker 部署不再静默无鉴权：`is_auth_enabled()` 的唯一数据源是 `_is_auth_enabled_from_env()`，它只做「算出 `.env` 路径 → 文件不存在就 `return False` → 存在则 `dotenv_values()` 读该文件」，全程不查 `os.environ`；而官方 `docker/docker-compose.yml` 用 `env_file: ../.env` 注入容器，`Dockerfile` / `.dockerignore` 又把 `.env` 排除在镜像外（compose 注释本身就写明容器内不会生成 `/app/.env`），于是容器里没有文件可读，**哪怕宿主 `.env` 写了 `ADMIN_AUTH_ENABLED=true`，开关也恒为 False**——`AuthMiddleware.dispatch` 首行直接放行全部 `/api/v1/*`，读写系统配置、触发分析、持仓与自选股全无凭据可及，而 `/api/v1/auth/login` 返回 `auth_disabled`、管理员根本登不进去，界面上看起来只是「没开鉴权」。反方向同样成立：进程环境里显式写 `ADMIN_AUTH_ENABLED=false` 也压不过 `.env` 里的 true，与 `setup_env()`（`load_dotenv(override=False)`，进程环境优先）及所有其它配置项的既有约定相悖。现先取 `os.environ`，为空才回落到 `.env` 文件（`api/v1/endpoints/auth.py` 与 `system_config_service` 在写完 `.env` 后都会 `setup_env(override=True)`，两者因此保持同步）。新增 2 例回归（无 `.env` 文件、只有进程环境 → 开启；`.env` 为 true 而进程环境为 false → 关闭），反向验证：修复前两例转红；同时把三个原本只靠临时 `.env` 表达「已开启鉴权」前置条件的既有用例（两个真实 ASGI 类 + 一处 `.env` 回落用例）改为显式声明进程环境，避免开发机真实 `.env` 泄漏进来的值把前置条件顶掉
- [修复] 组合页三个录入表单（录入交易 / 录入资金 / 公司行为）没有防重复提交，双击「提交」会写入两笔重复记录：三个 handler 都没有 in-flight 状态，成功前不改按钮状态（弹窗是在 `await` 之后才关），提交按钮只按「是否选了具体账户」禁用；而手动录入的 `trade_uid` 在界面上没有任何输入能写它（只在 state 初始化时为 `''`），请求体里恒为 `undefined`，后端的 `_validate_trade_identity` 只认 `trade_uid` / `dedup_hash` 两个键，`POST /portfolio/trades` 的 schema 既不接受也不生成 `dedup_hash`，所以同一个请求发两次就是两条独立交易——持仓数量与现金变动翻倍，快照、集中度、收益曲线全按双倍口径计算，而页面上看起来只是一次成功录入。现给三个表单各自加 `tradeSubmitting` / `cashSubmitting` / `corporateSubmitting`，提交期间置位并在 `finally` 复位，提交按钮按 `disabled` + `isLoading` 锁住（三个独立标志而不是一个，避免关掉弹窗后另一表单被连带禁用）。新增 1 例回归（把 `createTrade` 挂在永不结束的 pending 上，连点两次「提交交易」：只允许发出一次请求），反向验证：修复前该例转红（收到 2 次调用），修复后转绿；浏览器实测（把写接口改为静默成功、不真的落库）：首次点击后按钮即变为 disabled 且显示「处理中…」，随后再点两次，实际只发出 1 个 `POST /api/v1/portfolio/trades`
- [修复] 告警页的「触发历史」或「事件」Tab 会永久卡在加载中：两个 loader 都调 `listTriggers`，却共用同一个 `triggersRequestIdRef` 取号，`finally` 里又只在「自己仍是最新请求」时才关掉 loading。并发时后发的把 ref 推到更大的号，先发的响应回来后被判为过期——既不写列表，也不关自己的 loading；而首次加载由一次性开关（`triggersLoadedRef` / `eventsLoadedRef`）驱动、置真后不再重发，分页入口又只在「非加载中且有数据」时渲染，于是卡死的那个 Tab 页面内没有任何重试入口，只能刷新整页。现给「事件」独立的 `eventsRequestIdRef`，两个 Tab 各自的过期判定互不干扰。新增 1 例回归（把两个请求都挂在 pending 上，先点「触发历史」再点「事件」，随后让两个响应到达并切回「历史」：列表必须渲染出来而不是停在「正在加载触发历史」），反向验证：修复前该例转红（DOM 里只剩两处「正在加载触发历史」），修复后转绿
- [修复] 决策信号页的深链筛选 `?sourceReportId=` 在「重置」后无法清除：`resetFilters()` 用 `getInitialFilters()` 重新求初值，而该函数直接从 `window.location.search` 解析 `sourceReportId`，本页又从不清理 URL，于是每次「重置」（以及每次重新挂载的 `filters` / `appliedFilters` 两处 initializer）都会把它重新注入。只要该字段有值，`toListParams` 就走短路分支——忽略其它筛选并强制 `sourceType: 'analysis'`——所以点完「重置」输入框、chip 与列表范围都还是那份报告，用户会以为已经回到全量列表。现在「重置」改为回到 `DEFAULT_LIST_FILTERS` 并摘掉 URL 上的 `sourceReportId` / `source_report_id`（`clearSourceReportIdParam()` 用 `replaceState` 就地改写，不新增历史记录），chip 上的 × 走同一条路径，离开再返回或刷新也不会复活。新增 1 例回归（深链进入后点「重置」：最后一次列表请求不得再带 `sourceReportId`、URL 不得再含该参数、输入框清空），反向验证：修复前该例转红（最后一次请求仍带 `sourceReportId: 3001`），修复后转绿；全量 122 文件 / 1300 用例全绿。浏览器实测：`/decision-signals?sourceReportId=137` 点「重置」后 URL 变为 `/decision-signals`、输入框清空、chip 消失
- [修复] 问股页在删除撤销窗口内再删另一条会话，会静默丢掉上一条删除，而提示已宣告「会话已删除」：删除被推迟 6 秒以提供撤销，待执行计时器存于 `pendingDeleteRef`；再次发起删除时只 `clearTimeout` 掉上一条，既不发请求也不提示，随后把 ref 与 `deleteToastId` 换成新会话。于是「删除会话 A → 提示『会话已删除 / 如需恢复，请在 6 秒内点击撤销』→ 6 秒内再删会话 B → 等 6 秒」只有 B 被删除，A 仍留在侧栏，界面没有任何说明，而撤销按钮已被新 id 覆盖、A 的撤销窗口也已丢失。现在把「真正提交一次删除」抽成 `commitDeleteSession`，被顶替时立刻补交上一条（不再只清计时器），撤销窗口正常到期时走同一条路径。新增 1 例回归（第二条删除顶掉第一条撤销窗口后，两次 `deleteChatSession` 都必须发出），反向验证：修复前该例转红（只收到 `session-2` 一次调用），修复后转绿；全量 122 文件 / 1299 用例全绿。浏览器实测（把 XHR 的 DELETE 请求改为静默成功、不真的删数据）：第一次点删除后提示按「会话已删除」渲染，再点第二条删除的同一帧就发出了上一条的 DELETE，6 秒后第二条的 DELETE 也发出，提示随之撤下
- [修复] 选股页切换策略后，被清除的旧结果会从 localStorage 复活并被当作当前策略的候选：选股结果在 `applyScreenResult` 里写入 `localStorage['dsa.screening.lastResult.v1']`，而「策略 / 返回数量」是另一条独立持久化的 `dsa.screening.formPrefs.v1`，两条线之间没有任何一致性校验，且清除路径只重置内存里的 `candidates` / `screenMeta` / `expandedCode`（全仓库对该 key 只有 `setItem` 与 `getItem`，没有 `removeItem`）。于是「跑选股（策略 A）→ 切到策略 B（结果清空）→ 刷新页面或离开再返回」会把 A 的候选名单重新灌回结果区，而表单显示的是 B；结果区标题只写「选股结果 / N 条候选」、不标注产出它的策略，那一轮的重排元信息还会连带把 A 的「智能重排未完成…继续使用确定性因子评分」告警一起还原，用户可以直接点候选上的「分析」把它交给 DSA。现在恢复副本时核对归属（`readScreenResult(scope)`：副本里的 `strategy` / `market` 与当前表单不一致就丢弃，老载荷缺这两个字段时不拦，免得历史数据整块失效），切换策略 / 返回数量触发的 `clearScreeningResults` 同时删除该副本。新增 2 例回归（切换策略后副本被删除、属于其它策略的副本不被恢复），反向验证：把归属校验临时去掉后「不被恢复」一例转红，恢复后转绿；全量 122 文件 / 1298 用例全绿。浏览器实测：把表单设为「资金热度」、副本设为「双低选股」后刷新，旧候选与旧告警均不再出现；切到「双低选股」后副本 key 被删除
- [改进] 全站页面级提示统一走右上角 Toast：问股页每次发消息弹出的那条引导提示渲染在 `absolute inset-x-0 top-3` 的**水平居中**容器里（不在任何 toast 区域），这就是「提醒出现在页面中间」的来源；进一步清点发现全站约 64 处页面级提示散落在页顶、输入框上方、卡片底部、`fixed bottom-5 right-5` 等处，其中已在右上角的 9 处又分属 5 个各自条件挂载的 `<ToastViewport>`（问股 / 告警 / 持仓 / 设置 / 模拟盘各一个）——切换路由提示就消失、多页无法共存、每个页面都要自己再搭一遍定位样式。现抽出全站唯一的 `ToastHostProvider` + `ToastPortal`（`src/contexts/ToastHostContext.tsx`），挂在 `App.tsx` 的 `UiLanguageProvider` 内、`Router` 外，因此 `/login`、`isLoading`、`loadError` 这些早返回分支也共用同一个 host（`Shell.tsx` 里的 `GlobalTaskCenter` 覆盖不到它们，这是它能做「全站」挂载点的原因）。页面仍各自持有 state 与 JSX，只把原来内联的那段包一层 `<ToastPortal>`、由它 `createPortal` 到 host；**不**改成命令式 `notify()` + `useEffect` —— 那要重写所有状态编排，diff 巨大且容易引入回归。**兜底契约**：没有 Provider 时就地渲染，与 `useUiLanguage` 不抛错、退化成中文默认值是同一套约定，因此裸渲染单个页面的既有测试无需改动即保持通过（代价是「忘了挂 host」在测试里不会暴露，故 `App.test.tsx` 断言 host 存在、并另加 6 例宿主回归）。host 容器**不带** `role="alert"` / `role="status"`，否则全站 33 处 `getByRole('alert')` 单数查询会变成「命中多个元素」。**自动消失语义沿用现状**：需要自动消失的站点自己包 `AutoDismissToast`（悬停暂停），不包就是持续显示——持续型与带操作按钮的提示因此搬过去也不自动消失；对话删除的「6 秒内可撤销」保持手写计时，它的到点动作是真实删除、文案也向用户承诺了固定窗口。**刻意留在原地（共 ~49 处）**：表单字段校验、卡片/面板自身的 fetch 失败占位、`RouteBoundary` / `SettingsPanelErrorBoundary` 的整页兜底、Dialog 内部提示——它们是页面结构的一部分，飘到右上角用户会找不到「这个输入框错在哪」，整页兜底做成浮动 toast 更会让错误页变成空白页。四处按同一判据例外保留、未按原计划搬走：①首页「配置不完整」提示——它是常驻的，而首页右上角正是「大盘复盘 / 分析」两个主操作按钮所在（实测按钮在 `top:76, right:1362/1434`，而 host 是 360px 宽的 `fixed right-5 top-5`），浮动过去会长期盖住主入口；②决策信号页「重估」面板的 5 条提示——`renderReassessPanel()` 同时渲染在页面卡片与 Drawer 两处，只搬其中两条会让同一面板的提示分裂成两地；③设置页「通知测试」面板——它的结果提示与逐渠道 `result.attempts` 明细同在一个 `<div className="space-y-3">` 里，且都是同一个按钮的产物，拆开放置反而让「哪条对应哪个渠道」失去上下文；④选股页结果上方的「当前使用因子排序 / 选股提示」（`StockScreeningPage.tsx:1631`）——它是**本次结果自身的出处说明**（解释下面的候选为何没有走智能重排），紧贴「选股结果」区块，搬走会与它所解释的结果分离，而且它是常驻的：实测 `/screening` 页面在 host 覆盖区（`top:20~160, right:1084~1444`）内有一个真实控件「展开热点题材（12）」（`top:99, left:1198, 201x36`），常驻提示浮过去会长期盖住它，与该页其余三条提示（只在选股未开启/不可用时出现，那时该按钮并不存在）的情形不同。新增 6 例宿主回归 + 8 例 `App.test.tsx` 断言，反向验证：把 `ToastPortal` 改回就地渲染后这 14 例全部转红，恢复后转绿。全量 122 文件 / 1296 用例全绿且**零测试改动**，`tsc --noEmit` / `eslint .` / `npm run build` / `ui_governance` 均通过；浏览器逐路由实测：8 条路由的提示都落在右上角（`top:20, right:1444~1450`），页面内只剩 recharts 自带的 `role="status"` tooltip 与选股卡片自身的降级占位

- [修复] 降级路径的分析摘要不再是英文报告：流水线被超时或预算截断、`decision` 阶段没跑完时，`_resolve_final_output` 会退回 `_fallback_summary`，而这份「摘要」是用户实际读到的正文（实测问股超时后回答气泡里就是 `# Analysis Summary: 600519 ()` + `## technical` + `Signal: sell (confidence: 62%)` + `## Risk Flags` + `- [high] ...`）——栏目名、信号、风险等级、生产者名全是英文，其中生产者名和信号还是**内部标识**（`technical` / `skill_shrink_pullback` / `sell`）。现让它和正式报告同一套口径：按 `ctx.meta["report_language"]`（`normalize_report_language`）取 `_REPORT_LABELS` 栏目名（`summary_heading` / `risk_alerts_label` / `strategy_confidence_label`），信号与风险等级复用既有译名函数 `localize_strategy_signal` / `localize_conflict_severity`（后者已是通知里渲染 severity 的既有做法），每条 opinion 的生产者名走 `stage_display_name`，战法共识（`skill_consensus`，非流水线阶段）另取 `strategy_synthesis_heading`（多策略综合）；顺带修掉股票名为空时拼出的空括号 `600519 ()` 与等级为空时拼出的空方括号 `- []`。**已知缺口**：核心阶段名（技术面/情报面/风险面/决策/持仓/整体分析）在仓库里没有 en/ko 译名，因此 en/ko 报告里这几个标题仍是中文；`op.reasoning` 是子代理 JSON 里的原文，各阶段 system prompt 全英文、未带语言指令，所以这段正文仍可能是英文，属另一处独立问题。新增 4 例回归（中英韩三种语言的栏目名、内部标识不回显、空股票名/空等级不拼空括号），反向验证：换回修复前的 `_fallback_summary` 后 4 例全部转红（另修掉 `tests/test_multi_agent.py` 两条降级用例里对旧英文标题的断言）

- [修复] 问股「思考过程」不再显示内部阶段 id：阶段开始/结束/超时/跳过的文案散在三处，且大多以**内部英文 id** 呈现——后端 `stage_start` 发的是 `Starting technical analysis...` 这类模板，`stage_done` 干脆不带文案，前端只好自己拼 `${stage} completed`，`pipeline_timeout` 同样由前端拼 `${stage} timed out`，`pipeline_budget_skipped` 则是写死的英文整句 `Skipped decision analysis due to insufficient remaining budget`；于是 `technical` / `intel` / `risk` / `decision` / `agent_loop` / `skill_shrink_pullback` 这些标识直接出现在用户界面上（战法阶段更是把 `skill_shrink_pullback` 原样打出来）。现新增 `src/agent/stage_labels.py` 作为阶段文案的单一来源（`stage_display_name` / `stage_start_message` / `stage_done_message` / `stage_timeout_message` / `stage_budget_skipped_message`），编排器与 `run_agent_loop` 的全部阶段事件都改为调用它：核心阶段给出中文名（技术面 / 情报面 / 风险面 / 决策 / 持仓 / 整体分析），`skill_<id>` 复用 `src/report_language.py` 的既有译名表（`localize_strategy_skill`，缩量回踩 / 箱体震荡 …）而不是再维护一份会漂移的副本，认不出的 id 回退为「阶段」而非回显英文 id。前端 `ChatPage.tsx` 的四处兜底（`stage_start` / `stage_done` / `pipeline_timeout` / `pipeline_budget_skipped` 缺 `message` 时）同步改为中文且不再拼接 `step.stage`——正常路径下后端每个阶段事件都带文案，兜底只在事件缺字段时生效。回归：`tests/test_agent_stream_events.py` 的期望值更新为中文文案并覆盖 `stage_done` 新增的 `message` 字段，`ChatPage.test.tsx` 两条走兜底路径的用例改断言中文文案

- [修复] 问股补上缺失的工具中文标签：`get_capital_flow` 与 `get_portfolio_snapshot` 在两张标签表里都没有条目，`propose_alert` 只在其中一张里有，于是「思考过程」和工具进度行会直接露出内部英文 id（如 `get_capital_flow...`）或把它读进「「propose_alert」已完成」这类叙述里。现补全 `runner._THINKING_TOOL_LABELS`（资金流分析 / 持仓快照获取 / 告警提案生成）与 `api.v1.endpoints.agent.TOOL_DISPLAY_NAMES`（分析主力资金流 / 获取持仓快照），新增 `tests/test_agent_tool_label_parity.py`：从真实工具注册表（`get_tool_registry`）取全部工具名，断言两张表都覆盖且标签含中文，避免以后新增工具时再静默漏标；反向验证：删掉任一标签后该守卫点名缺失的工具并转红

- [修复] 问股不再把中间阶段的模型原文推流到回答气泡：`llm_adapter` 只要收到 `progress_callback` 就走流式并把每个文本分片当作 `content_delta` 推给客户端，而编排器给**每一个**阶段（技术面 / 情报 / 风险 / 战法 / 决策）都传了回调，前端又把这些分片实时拼进同一个回答气泡，于是整段分析期间用户看到的是各子代理的中间推理和原始 JSON 拼接（实测一次问股 3783 字节，开头就是 `I'll fetch the data for the requested stock.` 加 `{"signal": "hold", ...}`），直到 `done` 才被最终答复覆盖；若 `done.content` 为空，前端还会拿这堆文本兜底成永久内容。现按阶段过滤：只有产出用户可见答复的 `decision`（`_build_agent_chain` 在 quick/standard/full/specialist 四种模式下都把它排在最后，`_resolve_final_output` 也只取它的载荷）保留文本推流，其余阶段的 `content_delta` 被丢弃，工具调用与阶段进度事件照常上报。实测同一问题修复后 `content_delta` 总量由 3783 字节降到 412 字节且全部来自决策阶段、与 `done.content` 一致。新增 5 例回归（中间阶段不推流、决策阶段照常推流、无回调时仍传 None、四种模式收尾阶段都是 decision、其它事件类型不受影响）

- [改进] 图表不再在每次挂载时往控制台打一条 recharts 的 `width(-1) and height(-1)` 告警：`recharts@3.8.0` 的 `ResponsiveContainer` 把 `initialDimension` 默认成 `{width:-1,height:-1}`，首帧就按这两个 -1 走 `warn`，而真正的测量要等挂载后的 `useEffect` + `ResizeObserver`，因此**每次挂载必然先告警一次**（`warn` 只在 dev 构建生效，生产构建无输出）。现给三处没有声明尺寸的容器补上**布局已知的高度**（净值曲线 `h-64`→256、回撤缩略线 `h-12`→48、持仓详情价格趋势 `h-24`→96），宽度仍传 0 表示「测量前未知」：效果与默认的 -1 相同（测到之前不渲染图表，尺寸不会被猜错），只是不再触发这条告警——`warn` 的判据是「宽高任一大于 0」，给出真实高度即可。浏览器实测 `/paper`：告警由 2 条降到 0 条，净值曲线 SVG 尺寸仍是 1554×256、曲线正常绘制；`/decision-signals` 的时间线图早已写死 `initialDimension={{width:640,height:320}}`，因此它从来没告警，可作为对照。**未验证**：`/portfolio` 的两处图表在本地环境不挂载（无回撤趋势数据、持仓详情价格趋势接口一直 loading），该两处只按同一契约推及，未取得实测
- [修复] 场外基金分享图不再被当成单只股票渲染：`share_image.build_share_image_html` 只用 `_stock_heading_entry` 解析正文首标题，而基金报告的标题形如「中欧医疗创新股票A (006229)」——与个股标题同形，于是基金正文落进个股分支，分享图带着「个股决策卡 · 结论、点位与风险一图读懂」的副标题和「评分 50 /100」（`sentiment_score` 对基金是固定中性值）「置信度 中」（硬编码）这些**基金根本没有的信号**，同时净值、区间收益、最大回撤、波动、夏普、资产配置、前十大重仓、AI 解读**整块丢失**（`metrics` 与 `llm` 一起丢）。现新增基金版式：判据复用 `pipeline.py` / `notification` / `HistoryService` 共用的 `dashboard.report_type == "fund"`，并对通知汇总（`基金体检`）与历史单条（`基金净值体检`）两种标题做正则兜底；**要求正文恰好一条基金标题**，多只基金的汇总正文仍走多股布局，避免把第一只当成整份报告、丢掉后面几支。基金卡的数据只取自结构化载荷（`latest_nav` / `metrics` / `asset_allocation` / `holdings` / `llm`），Markdown 不反向解析——那等于给同一份数据再维护一套会随上游文案漂移的解析器；没有载荷时整段兜底渲染原始 Markdown，信息不丢。口径上：单位净值 4 位小数（与 `HistoryService._fund_nav` 一致）、`return_*` 比率 ×100 保留 1 位小数、`asset_allocation.*_pct` 与 `pct_of_nav` 已是百分比不再换算；风险等级从 summary 的既有结论里取，不在模板里重算 `_risk_grade` 那套阈值（后端、Web 卡片、海报三处各算一遍迟早会出现同一只基金两个等级）；`annual_volatility` 固定中性色，否则「波动大」会按正负被画成「涨得好」。新增 10 例回归（两种正文版式 + 载荷、无载荷兜底、无 LLM 整块不渲染、多基金保持多股布局、名字含「基金」的个股不被劫持、仅凭载荷标记选中基金版式、数据不足如实呈现、英文栏目），反向验证：把基金路由关掉后 8 例转红，剩下 2 例正是必须始终通过的反例守卫
- [修复] `POST /api/v1/auth/change-password` 改密后不再遗留可用会话，且当前密码与 `/login`、`/settings` 共用同一张限流表：原实现改完密码既不轮换会话秘钥、也不清理 `_rate_limit`，于是①改密前签发的 `dsa_session` Cookie 在改密后**依然有效**——密码泄露后「赶紧改密码」这个补救动作拦不住已经拿到 Cookie 的人；②当前密码校验没有任何失败次数上限，可以对着这个接口无限猜。现改密成功后调用 `rotate_session_secret()` 让其它已登录会话立即失效，并在同一响应里重发一个会话（`204` + `Set-Cookie`），避免操作者把自己踢下线；轮换失败返回 `500 internal_error` 并写明「密码已修改，但会话轮换失败」——密码此时已经落盘、没有可回滚的旧哈希，如实说明比假装成功更有用。限流按 `/login` 的既有写法（`get_client_ip` → `check_rate_limit` 429 / `record_login_failure` / `clear_rate_limit`），**失败只按认证失败记账**：`change_password()` 只返回文案，无法区分「当前密码错误」与「新密码不合规 / 写盘失败」，因此先单独 `verify_stored_password(current)` 判断是否计入，否则连续提交一个短密码就能把用户锁进 429（该反例已固化为回归用例）。响应契约不变：`204` 成功、`400 invalid_password` 失败，接口描述与 `docs/architecture/api_spec.json` 同步更新。新增 5 例回归（成功轮换 + 调用者会话仍可用 + 旧会话失效、反复输错当前密码触发 429、新密码不合规不消耗限流、轮换失败如实报错、真实 ASGI 端到端重放旧 Cookie 被 `AuthMiddleware` 拒绝 401），反向验证：把轮换改掉后 3 例转红，把限流去掉后 429 那例转红，把失败记账改成「任何 err 都记」后「新密码不合规」那例转红

- [修复] 每日分析工作流补上 `NOTIFICATION_EVENT_CHANNELS` 映射：`00-daily-analysis.yml`「执行股票分析」步骤的 env 只映射了 `NOTIFICATION_REPORT_CHANNELS` / `NOTIFICATION_ALERT_CHANNELS` / `NOTIFICATION_SYSTEM_ERROR_CHANNELS` 三个路由键，而 `src/notification_routing.py` 的 `NOTIFICATION_ROUTE_CONFIGS` 有四个（`event` 路由负责龙虎榜/资金流/公告这类事件型事实通知，以及模拟盘实时成交推送 `PAPER_NOTIFY_ENABLED`），于是**定时任务里这两个生产者的路由配置根本没法通过 GitHub Variables/Secrets 设置**。这条缺失同时让两处检查变红：`tests/test_daily_analysis_workflow_notification_env.py::test_daily_analysis_maps_p3_notification_route_env_keys`，以及 `test_notification_actions_env_table_matches_generated_output`——后者是因为 `scripts/generate_notification_actions_env_table.py` 会先校验工作流、缺键就直接拒绝生成。现补上映射（同样的 `vars || secrets` 写法）并重跑生成脚本更新 `docs/notifications.md` 的托管表；同时修正该文档「通知路由策略」一节：原文写「P3 新增三类」、表里也只有三行，现补齐 `event` 行并写明与 `NOTIFICATION_ROUTE_CONFIGS` 一一对应。**行为影响**：只有用户显式配置该变量时才生效（未配置时空值解析为零路由，仍是「发送到所有已配置渠道」的旧行为），因此不影响现有部署
- [修复] dsa-web 每个路由页面都设自己的 `document.title`：「选股」(`/screening`) 与「模型用量」(`/usage`) 两个页面此前**完全没有**设置标题，切过去之后浏览器标签页、书签、前进/后退历史里记的仍是上一个页面的名字（实测 `/screening` 显示「AI 建议 - DSA」、`/usage` 显示「模拟盘 - DSA」），页面本身却看不出任何异常；现按其它页面的既有写法用 `t()` 设标题，新增 `screening.pageTitle` / `usage.pageTitle` 两条中英文案（选股页正文仍是中文硬编码、未接入 i18n，标题仍跟随全局语言设置）。顺带修 `ChatPage` 的标题 effect 依赖为空数组：切换界面语言后标题不会更新，现改为 `[t]`，与首页、AI 建议、设置页一致。新增一条 `tests/ui_governance.test.ts` 守卫（从 `App.tsx` 解析「路由 → 组件 → 懒加载模块路径 → 源文件」，逐页断言存在 `document.title`，并先断言确实扫到了路由以免正则失效时空过），反向验证：把任一页面的赋值改掉后守卫点名该路由（`/usage → TokenUsagePage`、`/screening → StockScreeningPage`）；浏览器逐个路由实测中英文标题均正确
- [测试] 修掉 dsa-web 全量测试里按负载随机转红的那条删除用例：`ChatPage` 的「删除对话」走的是 **6 秒真实 `setTimeout`**（到点才调删除接口），组件卸载时**刻意**不清理它——导航离开后删除照旧生效，这是产品语义。而 `ChatPage.test.tsx` 里「给每个会话渲染独立删除按钮」那条用例点了删除就结束，计时器因此活到 6 秒后、在**后续用例**的执行窗口里调用 `deleteChatSession`，正好撞上「撤销窗口内不真正删除」那条 `expect(...).not.toHaveBeenCalled()`。证据：一次性探针（渲染 → 点删除 → 卸载 → 等 6.5 秒）显示删除接口在卸载后不立刻调用、6.5 秒后被调用 1 次；该断言在改动前的干净 HEAD 上 3 次全量全绿、在本工作树上 5 次全量红 3 次，而单跑该文件 4/4 全绿——判据是文件内时序相位（两者耗时区间重叠），删除链路与卸载清理都未被本次改动触碰。现让该用例收尾时点「撤销」拆掉计时器（唯一能确定性取消它的动作）并写明原因；修后 4 次全量里该断言 0 次转红
- [改进] 右上角提示 Toast 在鼠标悬停其上时不再自动消失：此前每个页面各自 `setTimeout` 到点就关，正在读的人把鼠标移上去也照关不误。现抽出共享组件 `AutoDismissToast` 统一持有倒计时与悬停状态，五个页面（模拟盘「估值已更新」3.2 秒、设置页操作结果 3.2 秒、告警测试结果 5 秒、持仓汇率刷新结果 5 秒、会话发送结果 3 秒/失败 5 秒）全部改用它，并删掉各自的计时器与 `useRef` 残留。语义：**悬停即暂停，移开后重新计时**（不是接着走剩余时间，读的人总能拿到完整窗口）；**换了新提示也重新计时**——因此 `active` 传的是提示状态本身而非布尔值，值一变倒计时就重启，这保留了各页面原有的行为。**刻意不改**：会话删除的「6 秒内可撤销」倒计时——它的到点动作是真实删除、文案也向用户承诺了固定窗口，暂停会改变破坏性动作发生的时间，与「读完再消失」不是一回事；页面顶部引导文案与「已复制」这类行内提示也不在 ToastViewport 内。新增 5 例组件回归（到点消失、悬停期间不消失、移开后重新计时、换新提示重新计时、未激活时不消失），去掉悬停判断后其中 2 例转红；浏览器实测模拟盘页：悬停 6 秒仍在，移开后 3.0 秒仍在、3.6 秒已消失
- [改进] 设置页「修改密码」卡片的成功提示纳入同一套悬停语义：该卡片不在 `ToastViewport` 内、此前走的是卡片自己的裸 `setTimeout(4000)`，于是这条提示在鼠标悬停其上时照关不误，是上一条「悬停即暂停」的最后一处漏网。现同样改由 `AutoDismissToast` 计时，删掉裸计时器，卡片内的成功提示与右上角通知行为一致。新增 3 例回归（到点消失、悬停期间不消失、移开后重新计时），未接悬停时后 2 例转红。该卡片只在启用管理员认证且已设置管理员密码时渲染（`passwordChangeable`），本地环境 `authEnabled=false` 因此不可见，未做浏览器实测
- [测试] 补上跨进程写同一个 SQLite 库的真实观察：告警 worker 与 Web 进程写同一个库，此前的回归用例只在单进程里证明「代码调了 `_run_write_transaction`」，证不了「另一个进程正握着写锁时这次写入仍然落地」。新增 `tests/test_sqlite_write_contention.py`，两个用真实多进程（`spawn`）的场景：①三个进程同时 upsert 同一个 `(rule_id, target, severity)`，唯一键下的竞态不得让任一方丢行或抛错；②另一个进程 `BEGIN IMMEDIATE` 握住写锁 1 秒，写入方把 busy timeout 压到 100ms、重试预算约 3.5 秒，必须在锁释放后完成写入。**判别依据是第二例**：把 `alert_repo.py` 退回修复前（裸 `get_session()`）后它以 `OperationalError: (sqlite3.OperationalError) database is locked` 转红——冷静期整行没写进去，而通知此时已经发出；第一例在修复前后都通过（默认 5 秒 busy timeout 已足以串行化那种强度），只作为并发下的长跑观察保留
- [改进] 工具调用超时的「遗弃」语义写进日志、并用真实子进程验证进程能退出：此前的超时日志只说 `Tool 'x' timed out after 2.00s at step 1`，读的人会以为调用被取消了；实际是 Python 无法强杀已阻塞的线程，**调用仍在后台跑完、只是结果被丢弃**——这会解释超时之后才出现的副作用（日志、写库、外部请求）。现两处超时日志改为明确写出 `abandoning the call — it keeps running on a daemon thread and its result is discarded`（批量路径写明遗弃了几个）。同时补一例子进程端到端回归：子进程里挂一个阻塞 120 秒的工具、超时设 0.2 秒，断言该进程在 30 秒内以 0 退出并打印出超时结果——即被遗弃的调用不再阻止解释器退出。把守护标志改回非守护（等价于修复前的 `ThreadPoolExecutor` 工作线程）后该例与另外两例一起转红，子进程被卡住的调用拖到 60 秒超时才被杀
- [改进] 钉钉回调 fail-closed 的代价——配置错一次就每次 403——现在在 **ERROR** 级别可诊断：`DingtalkPlatform` 对两类**配置缺失**导致的拒绝（未配置 `DINGTALK_APP_SECRET`、回调缺少 `timestamp`/`sign` 头）记一条带修复说明的日志，同一原因每个平台实例只记一次；签名不符、时间戳过期属于请求问题，仍是逐条 `WARNING`。之所以限制为「一次」：回调地址是公开的，`verify_request` 在鉴权之前执行，逐条记会让任何知道地址的人刷满 ERROR 日志；而配置错是「一次错、之后每次都错」，一次足够排查。排障时先看 ERROR 级的 `[DingTalk]` 行即可知道要改哪个配置，不必靠翻 `docs/bot-command.md` 反推 403 的原因。新增 3 例回归（两类配置拒绝各只记一次 ERROR 且含 `DINGTALK_APP_SECRET` / 「加签」字样、伪造签名不得占用 ERROR），前两例在把日志降回 `warning` 后转红
- [修复] 钉钉 Webhook 校验不再 fail-open：`DingtalkPlatform.verify_request` 此前在「未配置 `dingtalk_app_secret`」和「请求缺少 `timestamp`/`sign` 头」两种情况下都直接 `return True`，等于**没有密钥就不校验**——任何知道回调地址的人都能驱动机器人，而 `bot/platforms/__init__.py` 的 `ALL_PLATFORMS` 目前只登记了钉钉，这是仓库里唯一在册的 Webhook 平台。现两种情况都拒绝（`handle_webhook` 返回 403），与 `DiscordPlatform` 的既有契约一致；时间戳窗口（1 小时）与非数字时间戳仍按原样拒绝，请求头名改为大小写不敏感匹配（HTTP 规范如此，否则合法的加签请求会被误判成缺少签名而拒绝）。钉钉的 `handle_challenge` 恒返回 `None`，URL 可用性校验不经过 `verify_request`，因此收紧不影响回调地址的首次校验。新增 7 例回归（已签名的请求可被接受并解析出群聊消息、缺密钥拒绝、缺签名头拒绝、签名错误拒绝、时间戳过期拒绝、时间戳非数字拒绝、头名大小写不敏感），其中 2 例在修复前转红
- [文档] 修正 `docs/bot-command.md` 「Webhook 路由」与实现不符的描述：原文称 `/bot/feishu`、`/bot/dingtalk`、`/bot/wecom`、`/bot/telegram` 已「在 `api/v1/router.py` 中注册路由」，但该文件里没有任何 `/bot/*`，`server.py` 也没有，`ALL_PLATFORMS` 只登记了钉钉；现改为与 `docs/bot-command_EN.md` 一致的「尚未挂载」表格（钉钉可用、飞书仅 Stream、企业微信与 Telegram 有 handler 无适配器），并同步补上钉钉回调 fail-closed 的签名校验说明。中英两份文档本次同步更新

- [修复] 工具调用超时不再留下拖住进程退出的线程：`runner._execute_tools` 的单工具与批量两条路径此前都用 `ThreadPoolExecutor` 跑工具、超时后 `shutdown(wait=False, cancel_futures=True)` 就走人——而 `Future.cancel()` 对已经开始执行的调用是空操作，`ThreadPoolExecutor` 的工作线程又**不是**守护线程、会在解释器退出时被 join，于是「超时」只让调用方不再等待，卡在第三方库里的线程照样活着并阻止进程退出；实测 5 次超时留 5 个 `ThreadPoolExecutor-N_0` 线程。Python 无法强杀已经阻塞的线程，因此改为用守护线程承载调用（复用 `source_guard.call_with_timeout` 的既有取舍）：调用方按时放弃等待，卡住的调用再也不能拖住进程；每次调用仍 `copy_context().run` 拷贝上下文，`frozen_target_date` 一类 ContextVar 照旧传递到工具内。批量路径改为完成队列取结果、按剩余预算等待，并发度与「完成顺序 → 结果顺序」「超时的调用排出 `timeout: true` 结果」这些既有语义不变。新增 3 例回归（单工具超时后存活线程必须全是守护线程、批量同理、未超时的调用仍返回真实结果），前两例在修复前转红并指名泄漏的 `ThreadPoolExecutor-*` 线程
- [修复] 告警冷静期不再在写锁竞争下静默丢失：`AlertRepository.upsert_cooldown` 此前直接 `with self.db.get_session()` 写 `alert_cooldown`，没有走其他仓储写入共用的 `DatabaseManager._run_write_transaction`——文件 SQLite 上那条路径会先取写锁（`BEGIN IMMEDIATE`）并在 `database is locked` 上退避重试，而直接开 session 没有这两层保护。告警 worker 与 Web 进程写同一个库，一次瞬时锁竞争就会让冷静期整行写不进去，而**通知此时已经发出**，于是下一轮重新触发、重复推送同一条告警——正是冷静期要防的事。现改为在共享写入路径上完成（`(rule_id, target, severity)` 唯一键下的 `IntegrityError` 仍兜住「两个写者都 INSERT」的竞态，重读幸存行继续写）；`alert_worker._upsert_db_cooldown_safely` 的失败日志从 `warning` 提到 `error`，因为走到那里已经是真正的持久化失败而不是例行告警。新增 3 例回归（写入必须经由 `_run_write_transaction`、锁定首写必须重试且不丢行、失败必须以 ERROR 级留痕并带上目标），三例在修复前均转红

- [修复] 任务列表按状态筛选不再「先截断再筛选」：`GET /api/v1/analysis/tasks` 此前用 `list_all_tasks(limit=limit)` 取最近 N 条、再在端点里按 `status` 过滤，筛选因此只在「最近 N 条」这个窗口内生效——库里有 500 条任务而最近 20 条都是 `pending` 时，按 `completed` 查询返回空列表，尽管 completed 任务确实存在。现筛选下推到队列内部、在 `limit` 之前生效（大小写与空白归一、支持逗号分隔多值），端点不再做二次过滤；响应里的 `total`/`pending`/`processing` 仍是全量统计，语义未变。Web 侧 `refreshActiveTasks` 用 `response.tasks.length === activeTaskCount` 判断快照是否完整以决定能否清理陈旧任务，此前该判据会因窗口截断而恒为 false，现在才真正成立。新增 4 例回归（筛选顺序、无筛选时排序不变、大小写/多值匹配、端点经真实队列验证筛选发生在截断之前），去掉修复后其中 3 例转红
- [修复] 场外基金报告在「数据不足」时不再自相矛盾：`_risk_grade` 在回撤/波动为 `None` 时给「数据不足」，但 `build_fund_report` 的走势与建议分支把缺失值当成 0 参与判断，于是同一份报告里 summary 写着「风险等级:数据不足」，走势却是「震荡」（上行需要 `return_1y > 0`，`None` 时取 0）、建议落到兜底文案「风险较低,走势相对平稳」——两句都不成立，还会一路进通知正文和 Web 卡片。现数据不足单独成支：走势为「数据不足」、建议为「净值数据不足,暂无法给出风险与走势判断」，不再编造结论也不再静默降级成一个看起来正常的取值。新增回归用例覆盖该分支
- [修复] 基金 LLM 解读此前只在 Web 卡片上可见，通知正文与历史 Markdown 都把它丢了：增强层挂在 `dashboard["llm"]`，而 `generate_fund_aggregate`（邮件/飞书/本地文件正文）与 `HistoryService._generate_fund_markdown`（历史报告）都只读 `metrics` / `latest_nav` / `holdings` / `asset_allocation`，于是同一份分析在 Web 上看得到「持仓集中度 / 综合解读 / 申赎建议 / 风险提示 / 情绪分」，在邮件和历史里看不到。现两处出口按与 `FundMetricsCard` 相同的字段与顺序渲染该块，逐字段过滤空值（未产出时整段不出现，不输出空壳）；新增用例断言两处出口都渲染、且缺席时不出现空标题
- [修复] 企业微信分支不再把基金批次喂给股票仪表盘：`pipeline._send_notifications` 的企业微信分支直接调用 `generate_wechat_dashboard` / `generate_brief_report`，绕过了 `generate_aggregate_report` 的基金短路，而基金 dashboard 没有 `core_conclusion` / `battle_plan` / `intelligence` 这些键——净值指标、持仓、资产配置和 LLM 解读在企业微信里整块消失，同一次分析的邮件正文却是完整的。现基金批次（或 `report_type=FUND`）在该分支同样走 `generate_fund_aggregate`，判据复用 `_all_fund_results`，与邮件正文同源；混有股票的批次保持原行为不变
- [修复] `CrossProcessAnalysisLock.acquire()` 抢锁失败时不再泄漏文件描述符：`fd` 在 `flock` 失败的分支已经打开，而 `self._fd` 只在成功时赋值、`release()` 兜不到这条路径，因此每次抢锁失败都会漏一个 fd。现失败分支显式关闭自己打开的 fd。同时修正无 `fcntl` 平台（Windows）的静默降级：此前 `ImportError` 分支不记日志就返回 `True`，等于无声宣称互斥已生效，而两个调用方都不额外持有进程内锁；现该分支记一条 warning 说明跨进程互斥**未**生效并仍返回 `True`——返回 `False` 会让 Windows 上排程整体停摆，代价更大。新增两例回归（抢锁失败必须关掉自己打开的 fd；缺 `fcntl` 必须留痕且不假装持有锁）
- [测试] 全量测试不再往开发者真实数据库写桩数据：`DatabaseManager.get_instance()` 在用例重置单例且未提供 URL 时落回配置的 `DATABASE_PATH`（仓库内 `data/stock_analysis.db`），而 `persist_llm_usage` 设计上「fire-and-forget、never raises」，`test_persist_usage_never_raises` 正好重置单例后调用它；`tests/test_agent_executor.py` 的 20+ 处 `usage={"total_tokens": 10}` 桩同样如此——实测单跑一次该文件就往 `llm_usage` 追加 81 行 `model='openai'`、`total_tokens=10` 的假记录，而 `/usage` 页把它们当真实调用统计展示（871 次调用里 720 次来自这些桩）。`tests/conftest.py` 新增 session 级 autouse fixture，只把**默认**数据库重定向到临时目录；自带 `DATABASE_PATH` / `patch.dict` / 显式 `db_url` 的用例不受影响。反向验证：改动前 `llm_usage` 行数 1114 → 1195（+81），改动后 1195 → 1195
- [文档] 修正 `AGENTS.md` 核心职责里不存在的路径：`src/reports/`（报告生成）自始不存在，报告生成实际位于 `src/notification.py`、`src/services/report_renderer.py`、`src/services/history_service.py`、`src/share_image.py`，现按实际代码列出；全仓库已无 `src/reports` 引用
- [测试] 本机跑全量测试不再被自己启动的后端干扰：`tests/test_runtime_scheduler_service.py`（6 例）与 `tests/test_main_portfolio.py`（1 例）构造的 config 是 `SimpleNamespace`，没有 production 一定有的 `database_path`，于是 `_analysis_lock_path_from_config` 落回仓库真实路径 `data/stock_analysis.db.analysis.lock`；本机后端（或另一个 pytest）持有该 flock 时，`_run_analysis_locked` 按设计「拿不到锁就跳过本次运行」，依赖运行结果的用例于是假失败——生产行为是对的，缺的是用例前提。现两个测试类各加一个 `setUp`，只把「本来会落回仓库路径」的那一类 config 重定向到临时目录，自带 `database_path` 的用例（如 `test_cross_process_busy_records_skip_reason` 用临时库路径验证竞争路径）原样走真实推导；flock、非阻塞、竞争返回 `False` 这套机制仍走真实实现。反向验证：不启动重定向 → 这 7 例全红；启动 → 33 passed。**无生产代码变更**
- [文档] 写明「信号状态」与「模拟盘处置」是两套账本：`decision_signals.status` 描述建议本身是否还有效，系统只自动写入 `expired`（TTL 到期）与 `invalidated`（同 profile 的相反 active 信号出现）两个终态，`closed` / `archived` 是用户动作、不接受自动写入——模拟盘平仓与止损/止盈退出都不回写信号状态。模拟盘侧的结论由 `paper_signals.disposition`（按 `account_id + signal_id` 唯一）记录，`paper_positions.open_signal_id` 与 `paper_trades.signal_id` / `reason` 可供关联还原。**无代码变更**：交易闭环审计曾建议「平仓时自动把开仓信号置为 `closed`」并补 `close_signal_id` 列，评估后判定这是两套账本的分工而非缺陷——自动置 `closed` 会让模拟账户的止损退出静默抹掉用户仍可执行的建议，加列则是为同一事实建第二份存储并需要迁移；取舍与将来若要做时的正确形态记在 `docs/paper-trading-optimization.md` 第 17 节
- [修复] 模拟盘区分「想做但没做成」与「无需动作」：`paper_signal_records.disposition` 此前把两类处境相反的结果混在一个取值里——账户侧被挡住（现金耗尽买不进、按最小交易单位取整后数量为 0、`sell`/`reduce` 但没有可减的持仓）分别被记成 `ignored` 或 `hold`，而「维持」这个词主动断言系统认为无需动作，用户看到它不会去查现金，只会以为策略没给建议（真实场景：现金耗尽后再来一条 buy 信号，列表显示「维持」）。现区分成因，新增 `no_cash` / `lot_too_small` / `no_position` 三个取值，`_handle_signal` 的 `ignored`（hold/watch/avoid/alert 本身无可执行动作）与加仓路径的 `hold`（已在目标权重）保持不变。**只改记录值、不改行为**：这三种结果照旧被消费（不重试、不触发重新估值），也与 `data_unavailable` 的可重试语义正交。**无 schema 变更、无数据迁移**——`disposition` 是 `String(16)` 自由字符串，历史行保持旧值、新旧并存，`api/v1/schemas/paper.py` 的 `disposition: str` 不设白名单故接口形状不变；Web 对未知值原样回退，旧后端配新前端不会崩，新后端配旧前端会显示裸英文串（与引入 `no_fill` 时一致的既有兼容契约）。Web 侧 `PaperRecordsList` 补三个徽章（前两个用 `warning` 表达「账户有约束」而非 `default` 的「无需动作」），`featureText` 补中英 `dispNoCash` / `dispLotTooSmall` / `dispNoPosition`；新增 `tests/test_paper_disposition_labels.py` 从 `paper_service` 的三个产出方法解析可持久化的取值集合（含 `disposition = "..."` 赋值，不只是 `return`）与 Web 的 `dispositionMeta` 做双向集合比对并校验 zh/en 文案，防后端新增取值而前端漏补
- [修复] 选股结果被候选上限压缩时不再静默：`LLM_MAX_CANDIDATES`（默认 12）此前在调用方请求更多条数时直接封顶最终返回条数而结果里不留任何痕迹——`picks` 由进入 LLM 重排的 `df_top` 构建，此后只会被取子集或重排、不会再扩充，所以 Web 默认请求 20 条实际只会拿到 12 条；响应里唯一沾边的 `after_filter_count` 语义是「硬过滤后」、即上限生效**前**的候选池大小，读的人无从区分「策略只产出这么多」和「只重排了这么多」。现当 `top_k < output_count` 时写入 `degradation`（措辞与既有 `Remote post-analysis cap ...` 一致，判据同为 `<上限> < requested output`），经 `_collect_screening_warning_messages` 并入 API 的 `warnings` 抵达前端。Web 侧 `formatScreenMessage` 此前把 `Remote post-analysis cap` 整条抹成空白，于是仅有的两条能说明条数被截的提示在页面上都不可见；现把这两条渲染成「候选数量受…上限限制：请求 N 条，最多返回 M 条。」，纯诊断计数（风险否决数、硬过滤瀑布、上下文行数）仍不回显。**无 schema 变更、无新增配置**，`docs/screening-engine.md` 同步记录该判据与 `after_filter_count` 的真实语义
- [改进] Skill 表现统计补出 `pending` / `unable` 的原因分布：`/api/v1/decision-signals/skill-outcomes/stats` 的每个 bucket 此前只返回 `pending`、`unable` 两个总数，而这两类里混着处境完全相反的行——`insufficient_future_data` 是前向窗口还没走完、会自行消解，`missing_start_bar` 是本地 `stock_daily` 里根本没有该 session 的 bar、需要人去查数据源或改调用方。只有总数时，读的人只能看到「一直有几十条待定」而无法判断系统是否卡住，排查必须手工查库。现按 `decision_signals` 侧既有的 `unable_reasons` 模式补上 `pending_reasons` / `unable_reasons` 两个 `reason → 计数` 映射：纯追加字段、默认空对象，计数口径与样本充足度完全不变，不参与任何 rate 分母。没有原因的行（服务层记录瞬时异常时写入的不带原因 `pending`）归入 `unknown` 而不是丢弃，因此每个映射的计数之和恒等于同 bucket 的 `pending` / `unable`，明细不会漏数；原因按 `(skill_id, horizon, engine_version)` 与其它计数同等隔离，不会跨桶串味。**未改动评估与重试语义**：这两类仍按 `docs/multi-strategy-contract.md` 第 21 行保持可重试 `pending`——`missing_start_bar` 里 `HK00700` 这类只是尚未成功落过一次港股日线（港股日线由 AkShare / Tushare / Yfinance / Longbridge 提供），一旦落库就会自行转成 `evaluated` / `observational`，为让队列「看起来干净」而提前终态化成 `unable` 会把本可恢复的样本永久丢掉。同批修正 `skill-outcomes/stats` 端点描述里遗留的「最小评估样本数（30）」，改为直接插值 `MIN_SKILL_OUTCOME_SAMPLE_SIZE`，避免阈值调整后文案再次漂移
- [文档] `docs/multi-strategy-contract.md` 补记 Outcome 的可重试 `pending` 与原因分布口径：说明 `missing_start_bar` 与 `insufficient_future_data` 的区别、当前 40 条 `missing_start_bar` 的实际来源（`006229` / `001052` 是场外基金代码却被按裸代码当作 A 股分析，股票日线链路不可能产出其净值 bar；`HK00700` 本地日线为空），以及为什么这两类必须在无数据时保持 `pending` 而不是提前转终态
- [修复] 选股的市场校验不再有一层是死代码：`_ensure_supported_market` 读的是 `/screening/status` 返回的 `supported_markets`，而 `_call_screening_status()` 从不返回该键（也不返回它兜底读的 `markets` / `market`），于是这道闸门恒定在「拿不到支持列表就直接放行」处提前返回，从未拦下过任何请求——真正拦下非法市场的只有引擎内的闸门，报错也从设计中的 `422 screening_invalid_market` 降级成了 `400 screening_screen_rejected`；现由 `union_market_scopes` 从各策略 `market_scope` 的并集产出该键，开关恢复生效。同一并集同时供引擎内闸门使用，两处不会再各说各的支持集合；若某个策略读不出 `market_scope`，现在会直接报错而不是静默参与一次「支持集合为空」的判定。`GET /api/v1/screening/status` 也透出 `supported_markets`——`ScreeningService.status()` 是按字段 allowlist 拼载荷的，服务内部有这个键不等于响应里有，客户端此前只能靠试错得知哪些市场可用；引擎探测失败时该键刻意不写（空集合会被读成「没有市场可选」）。**行为变更**：非法 `market` 的错误码由 `400 screening_screen_rejected` 变为 `422 screening_invalid_market`，错误体由字符串 `detail` 变为 `{error, message}` 对象，消息形如「市场 us 不在选股功能支持范围内（支持市场：cn）」；合法市场路径不受影响。覆盖该场景的用例此前把 `_call_screening_status` 替换成 `{"supported_markets": ["hk","us"]}`——一个生产代码永远产不出的形状，因此断言通过而闸门在生产里是死的，现改为只替换策略来源、让状态载荷走真实推导路径，并新增「status 的支持集合必须真实存在且与引擎闸门同源」的守卫
- [文档] 补记 Skill 路由的实际可检测市场状态与 `sector_hot` 断链：`_detect_regime()` 里 `ctx.meta["sector_hot"]` 分支不可达（全仓库没有任何写入方，`meta` 的每一处写入都是逐键字面量赋值），已删除该分支；`AGENT_SKILL_ROUTING` 的帮助文案改为列出真实存在的四种状态（趋势向上/趋势向下/震荡/放量），并说明策略 YAML 里的 `market_regimes: [sector_hot]` 没有任何检测来源——只声明 `sector_hot` 的策略（`dragon_head` / `hot_theme` / `emotion_cycle`）不会被 auto 模式选中，需显式指定或用 manual 模式（中英双语）。`docs/screening-engine.md` 同步补记市场校验的两层结构与「按原样字符串比较、不做大小写归一」的契约
- [修复] 决策信号 `swing` / `long` 两个周期不再永不过期：`_expires_at_from_base` 只对 `1d/3d/5d/10d` 能算出天数，`swing` / `long` 使 `_horizon_days` 返回 `None`、`expires_at` 落成 `NULL`，于是这两类信号永远不进 `expired` 终态，也就永远留在「启用中」并参与同源去重与相反信号失效判定；现新增 `DEFAULT_HORIZON_TTL_DAYS`（1/3/5/10/20/60 天）统一承载各 horizon 的默认有效期并补上 `swing=20` / `long=60`，显式传入 `expires_at` 仍优先。`docs/decision-signals.md` 同步记录该默认值表
- [改进] Skill 表现的最小评估样本数由 30 下调到 5：该阈值原先在真实数据上**结构性不可达**——每个 `(skill, horizon)` 桶的 `total` 上限只有 13，而 `evaluated` 仅占其中约一成（152 条后验里 16 条 `evaluated`），于是 `compute_weights` 恒定返回 1.0，加权结果与「没有复盘」逐位相同；下调后阈值回到样本成熟后确实可以达到的量级。**需说明的是当前数据下仍无 bucket 达标**（桶内 `evaluated` 最大为 2），行为与改前一致，本次只是取消一个永远不会触发的开关，并未让加权真正生效——要它生效仍需等 `pending` 样本的前向窗口走完（这批样本的绝对条数会随每日复盘持续变化，故此处不写死数字）。Web 侧无需改动：「AI 建议」页在无 bucket 达标时本就不渲染那张表，而是显示「样本尚未达标」并列出样本进展
- [改进] 模拟盘补币种口径说明：`paper_*` 全部表都没有币种维度、跨市场按 1:1 记账，而实盘「持仓分析」页是按币种分桶 + 汇率折算的，两者口径不同却没有任何提示，用户容易把模拟盘收益当成跨市场真实收益；现于模拟盘页净值卡片下方增加一行说明（中英双语）。**未改数据模型**——给 paper 表加币种列需要迁移与历史回填，且模拟盘定位是策略跟踪而非真实盈亏核算，`docs/paper-trading-optimization.md` 方向 J 记录了该取舍
- [改进] 「AI 建议」页新增首屏「待处理」区：启用中的信号按「可以动手 / 需要确认 / 观察」三档分组，只有动作明确（buy/add/reduce/sell）且计划完整（plan_quality=complete）的信号才进入「可以动手」，观察档默认收起。该区分档单独拉取最近 100 条启用信号做全貌判断，不受列表分页与列表筛选影响；达到 100 条时用可见文案说明覆盖范围，不静默截断。原有「全部信号」「单股追踪」两块能力一行未改
- [修复] 「AI 建议」页「待处理」区不再继承主列表筛选、收起的分档不再留在 Tab 顺序里、超长内容不再被静默裁掉：此前「去重最新」打开时该区直接复用主列表结果，但主列表会带上当前生效的筛选（状态改回「全部状态」得到 `status=''`，或经 `?sourceReportId=` deep link 进入时 `status` 整个不传），于是「可以动手」可能把已失效的 buy + plan_quality=complete 当成可执行信号展示，也可能漏掉筛选项之外的可执行信号且页面无任何提示；现只在列表无活跃筛选（与 `DEFAULT_LIST_FILTERS` 逐字段比较）且「去重最新」打开时才复用（此时 `DEDUPE_PAGE_SIZE` 与 `QUEUE_PAGE_SIZE` 同为 100，两次查询确实等价），其余情况照常发独立的 `status=active`、`page_size=100` 宽查询。「观察」档此前用 `max-h-0` + `opacity-0` 隐藏，卡片按钮仍在 DOM 与键盘 Tab 顺序里（键盘用户会 Tab 进看不见的卡片、表现为焦点「消失」），现改为受控模式 + 收起时不挂载子内容；`Collapsible` 新增可选 `scrollable` 属性（去掉展开态 2000px 上限并给出真正的滚动容器，默认值不变、既有消费方行为不变），用于「观察」档与底部「统计数据」区这两处可能超过 2000px 的内容
- [新功能] 「AI 建议」页支持「只看我的持仓」：复用后端既有 holding_only 查询参数（此前 API 客户端已接入但页面从未使用），开关同时作用于待处理区与信号列表
- [改进] 「AI 建议」页 Skill 表现表的中文名列：此前直接渲染 skill_id 原始值（如 box_oscillation），现映射为中文名（如「箱体震荡」）并随语言切换；未知 skill id 回退显示原始 id，不显示为占位横杠，因 skill 是开放集合（AGENT_SKILLS 可配置自定义策略）
- [改进] 「AI 建议」页高级筛选表单默认收起（有活跃筛选时自动展开），「信号表现统计」与「Skill 表现」两张卡移入页面底部默认收起的「统计数据」区，且展开时才发起请求——这是一项行为变更：此前两张卡在页面挂载时即请求，现在折叠状态下不挂载也不请求，统计区在首屏的两次请求降为 0 次（同页新增的「待处理」区列表请求是另一笔）
- [测试] 新增 tests/test_skill_label_parity.py：断言后端 _STRATEGY_SKILL_TRANSLATIONS 与 Web skill 映射的 id 集合严格一致，并逐条比对 zh / en 文案本身（只比对 key 存在性无法发现改字），防止后端新增策略或改动译名而前端漏补
- [修复] 「AI 建议」页页头「刷新」不再只刷新一半：该按钮此前只重新拉取信号列表与全局复盘统计，Skill 表现卡、当前股票的最新信号、单股时间线都停在旧数据上，用户点完刷新看到的仍可能是过期内容；现补齐 Skill 表现刷新，并在已应用当前股票（且时间线已查询过）时一并刷新最新信号与时间线——没有股票上下文时不发起 latest / 时间线查询，保持「未选股不查询」的既有契约；按钮的禁用态同步覆盖这五个请求，避免连点重复发起
- [文档] 静态 OpenAPI 规格补齐整体漂移并在专题文档补记缺失接口：`docs/architecture/api_spec.json` 是 `create_app().openapi()` 的生成产物，但一直只用按模块手写的路径 allowlist 兜底，于是 6 条已上线路由（`/api/v1/decision-signals/skill-outcomes/run`、`/skill-outcomes/stats`、`/api/v1/paper/reset`、`/api/v1/notifications/deliveries`、`/api/v1/data-quality/discrepancies`、`/api/v1/portfolio/positions/{symbol}/price-history`）与 12 个 schema 长期缺失，另有 3 条路径 / 3 个 schema 内容已过期；现按运行时契约整体重新生成（纯追加 6 条路径 + 12 个 schema 并校正过期内容，无删除，鉴权定义与 info 不变），`docs/decision-signals.md` 的 API 清单同步补上此前漏记的两个 skill-outcomes 接口及其「与 `decision_signals` 表无关、服务相邻 skill 意见资产」的说明
- [测试] 静态 API 规格整体守卫：`tests/test_api_schema_pydantic.py` 新增 `test_static_api_spec_is_a_faithful_dump_of_the_runtime_contract`，整体比对路径与 schema（含逐项内容），任何新增或改签名的路由都会失败并点名，替代此前「漏了路径也不会红」的手写清单；同文件把 decision-signals 的管理员鉴权断言从手写 allowlist 改为遍历 router 实际挂载的全部 `/api/v1/decision-signals*` 路由，`tests/test_decision_signal_docs.py` 同步要求两个 skill-outcomes 接口出现在专题文档中；已反向验证守卫会点名缺失的路径
- [修复] 大盘复盘「复盘摘要」卡片不再显示报告标题本身，无标题段落的占位标题也不再中英混排：`_summarize_market_review` 此前取报告首行非空文本作摘要，而首行恒为 Markdown 标题（如 `## 2026-09-12 大盘复盘`）——它本来就会被前端当作记录名展示，于是「复盘摘要」卡片显示的就是标题本身，等于什么都没给；现跳过所有 Markdown 标题行，只取第一行正文。同批把报告无标题段落（`overview` / `full_review`）的占位标题按复盘语言本地化（`复盘概览` / `复盘正文`，此前中文界面固定显示英文 `Overview` / `Review`，与报告正文语言不一致），由报告自身 Markdown 标题得来的其余段落标题不受影响；段落 `key`（`overview` / `full_review`）是 API 契约，任何语言下保持不变
- [修复] 中文设置页补齐 17 个字段缺失的中文标题与描述：`SettingsField` 在 zh 下取 `fieldTitleMap[key] || 注册表英文标题`（描述同理），字段一旦没进这两张前端映射表，整行就退化成英文——本次新注册的 `PAPER_NOTIFY_ENABLED` 与 16 个历史字段（DeepSeek / Gemini / Anthropic / OpenAI 的多密钥与模型参数、飞书 `FEISHU_CHAT_ID` / `FEISHU_RECEIVE_ID_TYPE` / `FEISHU_DOMAIN`、`NOTIFICATION_EVENT_CHANNELS`、`AGENT_SKILL_CONCURRENCY`）均命中，中文界面显示 `Paper Trading Fill Notifications` 这类英文标题与说明；现补齐中文文案，只改展示层，注册表字段、env 键、默认值与保存行为均不变
- [修复] 设置页补回「事件通知渠道」控件：`NOTIFICATION_EVENT_CHANNELS`（事件型通知路由，龙虎榜 / 主力资金 / 重要公告以及模拟盘实时成交通知都走它）自 `7eb98998` 引入起在设置页**完全没有控件**——通知分类只渲染 `NOTIFICATION_CHANNEL_FIELDS` 里命中分组的字段，而它是全仓库唯一一个既不属于任何渠道分组、也不在 `general` 清单里的 notification 字段，于是只能改 `.env` 才能配；现加入 `general`（通用 / 报告）分区，与另外三个 `NOTIFICATION_*_CHANNELS` 路由字段并列，同时更正该映射表上方那句与实现不符的注释（原本声称未命中分组会「兜底到 general」，实际是一张手工清单、没有兜底机制）
- [测试] 通知字段可达性守卫：`tests/test_config_registry.py` 新增 `TestNotificationFieldsReachableInWebSettings`，解析 `SettingsPage.tsx` 的分组清单，要求每个 `category="notification"` 的注册字段至少落在一个分组里——字段漏进清单不会掉到 `general`，而是直接从设置页消失，这个守卫把「注册了但界面上不存在」变成测试失败；已反向验证守卫会点名缺失的键
- [测试] 补中文标签守卫：`tests/test_config_registry.py` 新增 `TestWebSettingsChineseLabels`，解析 `systemConfigI18n.ts` 的两张映射表，要求每个注册字段都有中文标题与描述（前端显式隐藏、不渲染成字段行的键列在 `_NOT_RENDERED_KEYS` 中），避免以后新注册字段再在中文界面显示英文；已反向验证守卫会点名缺失的键
- [改进] 模拟盘设置与市场域开关从「系统设置」挪到「基础设置」：设置中心按「配置机制」而非「资产类别」分类，模拟盘属于「自选股 → 信号 → 模拟盘」这条产品主线，是对账户本身的配置而不是运行时 / 调度开关，此前却和 `SCHEDULE_*`、`LOG_*`、`WEBUI_*` 放在一起；现把 `PAPER_NOTIFY_ENABLED` 与「模拟盘」账户卡片（账户摘要 + 重置入口）挪到「基础设置」，并顺带收拢同属市场域、同样落在「系统设置」的交易日历与大盘复盘开关（`TRADING_DAY_CHECK_ENABLED`、`MARKET_REVIEW_ENABLED`、`DAILY_MARKET_CONTEXT_ENABLED`、`MARKET_REVIEW_REGION`、`MARKET_REVIEW_COLOR_SCHEME`）。仅调整注册表 `category` 与对应 `help_key` 前缀（`settings.system.*` → `settings.base.*`）及中英文案，字段名、默认值、校验、API 契约一律不变——保存后行为与迁移前完全一致；「基础设置」区由 2 项增至 8 项，「系统设置」区剩 18 项，全部是调度 / 日志 / WebUI / 认证 / 性能这类真正的运行时开关
- [新功能] 模拟盘实时成交通知：此前模拟盘成交只落库 + Web 展示，任何渠道都收不到推送；现新增 `PAPER_NOTIFY_ENABLED`（默认 false，不配置即维持现状），开启后把**实时**产生的成交推到已配置渠道——信号消费产生的开仓/加仓/减仓/清仓，以及盘后估值触发的止损/止盈；历史回填是重放（一次可能落几百笔），全程静音不推送。复用现有 `event` 通知路由（`NOTIFICATION_EVENT_CHANNELS`），不新增路由类型；消息标题带动作与标的、正文含成交价量、金额、手续费与成交后现金，止损/止盈分别用 warning/success 语气；发送失败只记 warning 日志，绝不抛回交易路径（一条通知发不出去不能影响模拟盘记账）；开关可在设置页「基础设置」区直接切换（注册进配置注册表，保存后热生效、无需重启）；`.env.example` 与 `docs/paper-trading-optimization.md` 方向 I 同步
- [测试] 模拟盘成交通知补用例：新增 `tests/test_paper_notify.py`（消息渲染按止损/止盈/开仓/加仓/减仓/清仓区分语气与文案、未开启时不构造通知服务、开启时走 `event` 路由且带去重键、发送抛异常与返回 `success=False` 都只返回 false 不抛）7 例；`tests/test_paper_service.py` 新增「实时开仓发通知且 `cash_after` 是扣款后的值」「盘后止盈平仓按 closed 发通知」「回填确实落了成交但全程不调用通知」「通知服务整个不可用时成交与已消费标记照常落库」4 例

- [新功能] 模拟盘初始资金可配置、账户可重置：此前初始资金在 `paper_repo.ensure_account` 里硬编码 100 万且只在账户首次创建时写入，账户一旦存在就复用（传入值被静默忽略），也没有任何重置入口；现新增 `PAPER_INITIAL_CAPITAL` 配置项（默认 1000000，仅新建账户时生效，不配置即维持现状）与 `POST /api/v1/paper/reset`——把当前 active 账户置为 `archived`、按新的初始资金开一个新空账户，旧账户的持仓/成交/净值快照按原 `account_id` 全部保留（归档不删数据），请求体可选 `initial_capital` 覆盖本次金额（省略则用配置值），金额须 > 0（否则 422，且校验在任何写入之前，被拒的重置无副作用），并发重置按旧账户加 `_account_lock` 串行；新账户为空、历史信号**不自动重放**，需另行执行「历史回填」；查询侧零改动（`ensure_account` 取 id 最小的 active，所有入口自动认到新账户）；前端在设置页「基础设置」区新增「模拟盘」卡片（账户摘要 + 「重置账户」按钮 + 二次确认弹窗，含可选的初始资金输入，留空走服务端配置；模拟盘页工具栏只留刷新 / 回填这类高频操作，避免误点），`ConfirmDialog` 增加可选 `children` 插槽（纯追加，不影响既有调用方），中英文案同步；`.env.example` 与 `docs/paper-trading-optimization.md` 方向 H 同步
- [测试] 模拟盘初始资金与重置补用例：`tests/test_paper_service.py` 新增「归档后旧账户持仓/成交按旧 `account_id` 原样可查且状态为 `archived`」「新账户为空且金额/净值生效」「新账户成为 `get_or_create_account` 的返回值」「重置后同一信号能在新账户重新开仓（信号消费记录按账户隔离，旧账户的已消费标记不挡新账户）」「省略金额时取 `PAPER_INITIAL_CAPITAL`」「非法金额（0/负数/NaN/inf）抛错且不改动账户」4 例；新增 `tests/test_paper_api.py` 覆盖 `POST /paper/reset` 传金额、省略 body 走配置、金额 ≤ 0 返回 422 且无副作用 3 例；`PaperTradingPage.test.tsx` 补「二次确认后按填入金额调用 reset 并提示成功」「非法金额在本地拦截、不发起请求」2 例（并在 `beforeEach` 补 `vi.clearAllMocks()`，否则「不应被调用」类断言会被上个用例的调用历史污染）
- [文档] 模拟盘前复权覆盖写入的影响确认：`save_daily_data` 按 `(code, date)` UPSERT，每次分析重抓的近 30~240 天 bar 会覆盖成新的前复权序列，而模拟盘的成交价/含费成本价/止损止盈线都是绝对价、从不随复权调整——除权除息后持仓会出现等于分红额（或送转比例）的虚亏，风控线也可能落到当前价错误一侧并误触发；结论与影响写入 `docs/paper-trading-optimization.md` 方向 G，仓库已有 `PortfolioCorporateAction` 可复用但只服务实盘持仓，模拟盘接入属新增能力，未在本次改动范围内
- [改进] 模拟盘同日触及止损与止盈时先用开盘价判定先后：此前一律按止损优先记账（`ambiguous_stop_loss`），方向保守但让所有这类 bar 都被当成「先亏后赚」，明细数字系统性偏低；现开盘 ≤ 止损价按止损、开盘 ≥ 止盈价按止盈，只有开盘夹在两者之间（日内顺序确实无法从 OHLC 判定）才仍按止损优先并记 `ambiguous_stop_loss`；残留偏置收窄但未消除，已在 `docs/paper-trading-optimization.md` 写明
- [新功能] 模拟盘计入交易成本：买卖不再按零成本成交，按市场计佣金、印花税、过户费——A股佣金万2.5（单笔最低 5 元）+ 卖出印花税 0.05% + 过户费 0.001% 双向，港股佣金万2.5 + 印花税 0.1% 双向 + 交易费约 0.0105%，美股无法定费用；佣金率是全局配置（默认万2.5，不区分市场，`PAPER_FEE_COMMISSION_RATE=0` 时美股归零而 A股/港股仍收法定费用）；买入费用摊入成本价，`paper_trades` 新增 `fee` 列（启动时对存量库补列，历史行为 NULL 读回 0）并在成交流水/API/前端透出（仅费用非零时显示）；新增 `PAPER_FEE_ENABLED`（默认开，设为 false 等价旧零成本行为）、`PAPER_FEE_COMMISSION_RATE`（覆盖各市场佣金率）、`PAPER_FEE_SLIPPAGE_BPS`（滑点，默认 0 即不假设执行质量损耗，限价买单不会被滑点推过限价，按全部现金下单也不会因费用透支）
- [测试] 模拟盘交易成本补反例用例：`tests/test_paper_service.py` 新增「A股卖出收印花税而买入不收」「小单触发单笔最低佣金」「费用可关闭后回到零成本数值」「滑点按不利方向移动成交价」「限价买单不被滑点推过限价」「港股印花税双向」「按全部现金下单不透支」「成交流水费用能对平账户现金」「佣金率填 0 时美股归零而 A股仍收法定费用」9 例；`tests/test_paper_repo.py` 新增存量 `paper_trades` 补 `fee` 列的迁移用例
- [修复] 模拟盘成交不再凭空成交：`_signal_trade_date` 此前直接取信号 `created_at`（UTC-naive）的日期，不做市场本地化、也不判断当天有没有日线 bar，于是收盘后/周末产生的信号会在**非交易日**按计划价记账（真实库中 001324 的买入就落在周六 2026-08-08，当天无 bar）；现新增 `trading_calendar.resolve_fill_session`，交易日收盘前取当日、收盘后或非交易日顺延到下一个交易日，未知市场/日历不可用 fail-open 到市场本地自然日。买入价也不再直接取计划价 `entry_high`，而是按限价单处理——当日 `low` 高于限价则不成交（新 disposition `no_fill`，信号已消费、不重放，前端补中英「未成交 / No fill」徽章），否则按 `min(open, entry_high)` 成交，无计划价时仍按收盘价
- [修复] 模拟盘止损止盈按跳空成交价记账：触发后此前一律按止损/止盈价原样成交，跳空开盘越过触发价时等于凭空给出更好的价格（真实库中 2026-09-11 的 bar 为 open 20.99 / high 20.99 / low 19.80 / close 20.29，全天未跌破 19.42，账户却按 19.42 止盈，单笔少记约 16485 元）；现开盘越过触发价时按开盘价成交（止损 `min(trigger, open)`、止盈 `max(trigger, open)`），日内触达仍按触发价
- [修复] 模拟盘平仓日净值虚增与快照滞后一次标记：`_record_snapshot` 求和的是 `_valuate` 开头读到的持仓 records，而 `upsert_position` / `close_position` 只写库、不回写这批对象，于是平仓日把「卖出所得现金」和「已不存在的持仓市值」一起计入净值（真实库 2026-09-11 快照 `cash 1004410 + market_value 194775 = net 1199185`，净值虚增 19.5 个百分点，次日回落至 0.441%），且每个快照都用上一次运行的市值（08-27 的快照用的 08-26 收盘价）；现改为标记/平仓结束后重新读取开放持仓，`_record_snapshot` 的 `positions` 参数随之删除
- [测试] 模拟盘成交语义补反例用例：`tests/test_paper_service.py` 新增「收盘后信号顺延到下一交易日」「开盘价优于限价时按开盘价成交」「当日区间没碰到限价则不成交且不重放」「日内触达止盈仍按目标价」「平仓日快照不再重复计入已平仓市值」5 例，并把 `test_take_profit_exit` / `test_stop_loss_precedence` 原先钉住「按触发价原样成交」的断言改为跳空成交价；`tests/test_trading_calendar.py` 新增 `resolve_fill_session` 单测（收盘前/收盘时/非交易日/盘前/naive 输入/未知市场/日历缺失 7 例）；两个文件 75 passed
- [改进] 报告「操作建议」块补充可执行指引：在短词结论（观望/买入等）之下拼接该报告 dashboard 的「关键点位行（现价/支撑/压力）」与「一句话核心结论（先做什么）」，缺日线/技术数据或多字段缺失时逐级降级，始终保底显示结论；仅读 `details.rawResult.dashboard`，不动数据源、不改 prop 契约，建议有料不再单薄
- [改进] 首页自选卡片透出「一句话操作建议」：每张自选（及今日）卡片在名称下方展示该股最新分析的一句话建议（`operationAdvice`，单行截断 + 悬停 tooltip，按建议 tone 用 success/danger/warning 上色），让自选当天该怎么做一眼可见；无建议时不占位，完整理由仍走点击详情，不改数据契约与 i18n
- [改进] 模拟盘估值改用「最新已收盘交易日」定价：估值默认 `as_of` 由服务端本地今日改为该市场最近已收盘 session（`get_effective_trading_date`），避免盘中/收盘前把当日快照锁死在旧价，改善日线级的现值准确性；调度侧新增「仅出现新已收盘交易日才估值」守卫，消除了原先每 30 分钟一整天反复空转的唤醒，`run_daily_valuation` 幂等、手动 refresh（force）语义不变
- [新功能] 通知投递回执：全路径持久化每次渠道投递状态与耗时并可在设置页追溯
- [新功能] 通知投递列表 API（/api/v1/notifications/deliveries）与设置页投递视图
- [新功能] 跨源一致性对账：选源成功后用次选源比对价差/交易日/字段缺失，命中记录数据质量异常并可按 system_error 路由告警
- [新功能] 数据质量异常列表 API（/api/v1/data-quality/discrepancies）与设置页数据质量视图
- [新功能] 信号后验自动化：每日自动评估决策信号与 skill 意见后验
- [新功能] Skill 表现聚合 API（/decision-signals/skill-outcomes/*）与 Web 面板
- [新功能] 管线自愈与失败告警：调度分析失败时主动推送系统告警（复用 system_error 路由，未配置回退报告主渠道，同类型当日去重一次）、设置中心「系统设置」调度卡片补充展示上次失败时间与连续失败次数、数据源连续失败达到阈值进入短期熔断并通知一次（熔断阈值与恢复冷却可配置为 `DATA_SOURCE_QUARANTINE_THRESHOLD` / `DATA_SOURCE_QUARANTINE_RECOVERY_SECONDS`，默认 3 / 300，不改变现状）、跨发行日自动补跑（`RUNTIME_BACKFILL_ENABLED` / `RUNTIME_BACKFILL_MAX_DAYS`，默认开 / 1）
- [文档] `.env.example` 补管线自愈相关开关说明
- [新功能] 场外基金净值体检分析：`fund:<code>` 前缀显式识别，接入净值日报拉取、健康体检报告（无买卖点/止损，含「不构成投资建议」）、历史存储与 Web 基金指标卡；新增 `FUND_RISK_FREE_RATE` 配置（Sharpe 无风险利率，默认 0.02）
- [新功能] 基金报告补底层数据面：基于东方财富 F10 拉取当期十大重仓股（`jjcc`）与资产配置（`zcpz`），透传到报告、历史 Markdown 与 Web 基金指标卡（重仓股表 + 股票/债券/现金占比 + 净资产），纯信息展示、不作任何买卖判断，不改变默认配置与数据契约
- [新功能] 告警中心新增「事件」页：从既有触发历史接口拉取最新一页（接口 page_size 上限 100）在客户端折叠出 `dragon_tiger` / `capital_flow` / `stock_events` 三类事件驱动记录，以卡片列表展示事件来源标签、标的、观察值/阈值/状态与诊断事实（龙虎榜上榜次数、主力净流入金额自动按万/亿格式化、重要公告条数）；纯前端实现，不改触发历史数据契约、不加 data_source 过滤参数，未配置事件渠道时该页为空态不报错；新增 `EventFactList`/`eventFacts` 组件与中英文案
- [新功能] 事件驱动告警：新增 `event_dragon_tiger`（龙虎榜上榜）、`event_capital_flow`（主力净流入绝对值）、`event_announcement`（重要事件条数）三种个股级事件告警类型，并新增独立 `event` 通知路由（`NOTIFICATION_EVENT_CHANNELS` 配置项 + 配置注册表条目 + `.env.example` 说明）；事实源复用 `DataFetcherManager` 龙虎榜/主力资金流上下文与 `search_stock_events` 事件检索，取数失败静默降级为 `degraded` 不阻断告警管线；告警工单按类型分流——事件告警走 `event` 渠道、非事件告警维持原 `alert` 渠道；未配置 `event` 渠道时跳过该路由不崩；create/dry-run/指标阈值/数据源/默认命名均已接入既有告警服务契约（`SUPPORTED_ALERT_TYPES`/`SYMBOL_ALERT_TYPES`/`normalize_alert_parameters`/`_evaluate_rule`/`_to_runtime_rule`）
- [改进] 持仓加载提速：持仓快照（含 /risk）不再在请求路径同步走实时行情 provider 瀑布，改为优先读持久化的 `position_quote_cache`（每标的最新行情，新鲜窗口内直返），未命中走 `stock_daily` 收盘价快路径即时返回（标记 `price_stale`），实时行情由守护线程后台刷新并写回缓存；快照缓存 TTL 5s→15s 降低重算频率。请求路径耗时从 ~4.5s 降至 <0.1s，盘中价不再因单次网络卡顿阻塞整页
- [改进] 首页 K 线图提速并修复港股加载不出来：`get_history_data` 网络取数前先读 `stock_daily` 最近日线（最新一条 ≤4 个日历日则直接返回，不发网络），取数成功后按 `(code,date)` UPSERT 落库（幂等），下次请求即命中本地缓存；查询侧对 `stock_daily` 裸码/带前缀码不一致做变体匹配（A 股 `SH/SZ`、港股 `HK`）避免 MISS；港股日线源加固——yfinance 对 `YFRateLimitError` 加入指数退避重试、Akshare 对瞬时网络/HTTP 错误纳入 `@retry`；前端 `getStockHistory` 加模块级 3 分钟 TTL 缓存（键 `code|days`，失败条目不缓存），避免切换/重新选中报告反复取数；不改 provider 优先级与返回结构
- [改进] 个股分析报告新增「K 线走势」卡（`StockPriceChart`）：复用现成 `GET /api/v1/stocks/{code}/history?period=daily&days=N` 日 K 数据，recharts 自绘蜡烛实体绘制日 K，下方叠加成交量柱（涨绿跌红语义 token），插在报告概览与策略点位之间；用 ref + ResizeObserver 自测量包装规避 ResponsiveContainer 首帧 null 弹开闪烁，`isAnimationActive={false}`，加载/空况/无代码三种占位态，不改任何汇报 payload 字段或后端契约
- [改进] 首页大盘复盘报告「结构化大盘数据」卡补轻量可视化：涨跌家数加涨跌双段占比条（`BreadthBar`），指数涨跌幅内嵌中心归零迷你条形（负数红、正数绿，组内按最大绝对值归一化，`MiniChangeBar`），板块/概念 Top5 加领涨绿/领跌红横向条组（`SectorBarList`）；纯 CSS 宽度条实现，无 recharts / 无 ResponsiveContainer，规避首帧 null 弹开闪烁，颜色复用涨绿跌红语义 token，不改任何数据契约
- [改进] 首页大盘复盘报告正文正文按章节折叠：七个章节用现成 `Collapsible` 收起（首章默认展开，其余收起），报告滚动区高度实测 4644px 降至 1788px；分享截图走后端 `getShareImage` 不受影响，章节标题语义从 heading 转为折叠按钮（按钮可访问名保留完整标题文本，符合手风琴模式）
- [改进] 首页顶部「大盘复盘 / 分析」入口补用途说明：两个按钮分别加 Tooltip（悬停/聚焦提示功能边界），复用既有 `Tooltip`，不动按钮顺序、视觉权重与数据契约；对应 i18n 文案 `home.marketReviewHint` / `home.analyzeHint` 中英双语
- [改进] 问股（bot `/ask`）多轮 Agent 化：此前每次问股都生成新的 `uuid` 会话（含股票代码），同一用户每次都是全新会话、历史被丢弃，且单股锁定单一技能侧重、多股走一次性 `.run()` 不延续上下文、工具执行与可创建预警不回流；现会话改为按（平台, 用户[, 群聊房间]）持久分面（`:ask` 前缀区别于 `/chat` 的 `:chat`），同一用户多次问股（含切换标的，由 `resolve_stock_scope` 智能切换）串成一个 agent 会话、具备跨轮记忆；单股与多股都不再强制单一技能侧重（`skills=None` 落到默认/配置技能集，18 个工具始终可用，用户点名的策略文字仍作为自由指引留在消息里由 LLM 自主落实）；单股路径接入 `progress_callback`，命中 `propose_alert` 的 `alert_proposal` 事件时在回复末尾追加一行可创建预警提示（`BotResponse` 无流式面故折叠进最终回复，不做实时流式）；Web 端 `POST /chat` 本已多轮 + 全工具 + 流式，无需改动
- [改进] 「AI 建议」页信息架构重组为「全部信号 + 单股追踪」双区并补用途说明：此前刷新→选股→筛选→统计→最新信号→时间线→信号列表单列堆叠，真正的主信号列表被压到页面底部，统计数字无任何释义；现顶部一句用途说明（信号=AI 操作建议，「命中」=方向相符），分割为「全部信号」（筛选 + 去重开关 + 信号列表 + 分页 + 全局统计）与「单股追踪」（选股 + 最新信号 + 时间线）两个带小标题的分区，统计卡下方补常驻图例行（命中/未命中/无法评估/命中率），未选股时时间线卡用一行短提示替代重复大空态
- [改进] 「AI 建议」页信号列表加「只显示每只股票的最新信号」去重开关：此前同一股票信号在列表里出现多条、无区分易误读；现提供开关（默认关）勾选后按股票（market+stockCode）取 `createdAt`（再比 id）最新一条、扩大一次拉取到 100 条并隐藏分页、显示去重说明与去重后股票数；纯前端客户端折叠，不影响后端 / API / 分页默认行为
- [改进] 回测页「运行回测」与「筛选结果」区别常驻说明：此前两个按钮分处两行、无任何提示，用户不清楚「运行」会重新打分、而「筛选」只查询已有结果；现在参数行下方补一行说明（中英双语），避免需悬停才发现
- [改进] 回测页数字参数补单位与含义：此前「评估窗口」「最小天龄」只显示数字输入、无单位无解释；现「评估窗口」加「天」后缀与 tooltip（往后几个交易日验证 1–120，设为 1 即 1 日验证），「最小天龄」加「天」后缀与 tooltip（只评分至少 N 天前的分析，强制重跑归 0）
- [改进] 回测页搜索执行区对齐首页风格：此前头部按「① 评估规则 / ② 追踪范围 / ③ 操作」三区纵向排布，主按钮「运行回测」被挤到最右、搜索框小而分散；现改为首页式布局——顶部一行 = 大搜索框（股票代码，flex-1）+ 紧邻的「运行回测」主按钮，下面一行紧凑参数（评估窗口/1 日验证 · 开始~结束日期 · 阶段 · 最小天龄/强制重跑 · 筛选结果），去掉编号眉题改用内联小标签，整页更简洁、主操作一眼可见
- [修复] 回测结果表「AI 预测」列文本截断失效导致列表错乱：评估窗口改为按需重新打分后，趋势判断 / 操作建议作为 flex item 默认 `min-width:auto` 不收缩，各自按正文宽度膨胀到约 1030px，横向冲出 220px 单元格并覆盖「实际表现 / 准确性 / 结果」列，表格也溢出容器到 1944px；现给文本 span 加 `min-w-0 max-w-[220px]` 恢复单行省略号截断（完整文本仍走悬停 tooltip），表格回到容器宽度 1224px、无单元格溢出
- [改进] 回测页空态加首次使用引导：无结果时除「暂无结果」外新增一行「使用步骤」（① 评估规则选窗口/1日验证 → ② 追踪范围定股票/日期/阶段 → ③ 操作运行回测），让首次用户无需读文档即可知道模块如何用
- [改进] 回测页整体表现卡每个指标加释义：此前「方向准确率/胜率/平均模拟收益/平均个股收益/止损触发率/止盈触发率/平均命中天数/评估数/盈亏中」等指标只显示数字、无一句说明；现每项旁加 info 图标，悬停/聚焦显示对应英文或中文释义（中英双语）
- [改进] 回测页「评估窗口」失焦即套用，修输入/1日验证/列头不同步：此前手动改窗口值后仍停留在旧的「1 日验证」列（窗口改到非 1 时 1 日验证开关已灭但列表仍是次日验证列，需再点「筛选结果」才恢复）；现输入窗口失焦时若与已加载窗口不同立即套用并刷新，输入、1 日验证开关、列头与数据四处保持一致
- [改进] 回测结果表「阶段」列加悬停提示并限宽：长市场阶段摘要此前无提示地被撑宽换行、中间被截断且无法查看完整文本；现限宽 200px 截断，完整文本通过悬停 tooltip 查看
- [修复] 回测结果表「止损/止盈」列内容中英文：此前内容硬编码中文「止损/止盈」，英文界面下仍显示中文；现改用本地化文案（en「Stop/Target」），与表头「Stop loss / Target」一致
- [改进] 回测页头部按「评估规则 / 追踪范围 / 操作」三区重构并新增用途说明：此前「运行回测（重新计算打分）」与「查看结果（查询已有数据）」两类操作共用一行控件，`1 日验证`（即评估窗口=1）作为独立开关被放在右侧执行面板、与窗口输入分离，`最小天龄`/`强制重跑`（仅运行相关）却混在查询输入行，`阶段`/`筛选`（仅查看相关）也同排，且整页无一句话说明模块用途；现改为纵向三区——顶部一句用途说明，① 评估规则（评估窗口 + 相邻「1 日验证」快捷，同一规则一目了然），② 追踪范围（股票代码 / 开始结束日期 / 阶段，运行与查看共用），③ 操作（左侧「筛选结果」仅查看、右侧为最小天龄 + 强制重跑 + 运行回测主按钮）；`1 日验证` 的按下态与窗口输入联动，列切换仍由加载到结果的评估窗口驱动，行为不变，仅调整控件归位与文案可读性
- [新功能] 组合持仓支持场外基金：`fund:<code>` 前缀识别并记入场外基金（份额 × 最新净值估值），快照按最新单位净值展示市值/盈亏与资产占比，风险模块跳过基金避免误判；前端持仓行展示「场外基金」标识与净值口径、禁用仅股票适用的分析/价格历史入口，后端新增轻量 `get_latest_nav`（只拉净值页，不触发持仓/资产配置快照）
- [改进] 非交易日价格类告警按「跳过」记录不再触发：价位（价格上穿/下穿）、涨跌幅规则此前不论是否交易日都按实时报价判断，休市日静态收盘价会重复触发同一条「处于阈值之上/之下」噪音；现复用既有 `trading_day_check_enabled`（默认启用，`TRADING_DAY_CHECK_ENABLED` 可关）与交易日历，规则所在市场休市时按 `skipped`（数据源 `trading_calendar`）记录、不触发，价位/涨跌幅规则在 DB 规则与 legacy env 规则两条入口都生效，dry-run（测试按钮）与生产 worker 行为一致
- [改进] legacy `EventMonitor` 告警回路补非交易日门控：此前 `agent_event_monitor_enabled` 的老式 `_check_price` / `_check_price_change` 回路仍在休市日按静态收盘价触发价位/涨跌幅告警；现与 AlertService 价位告警一致，复用同一 `trading_day_check_enabled` 与交易日历，所属市场休市时该条规则直接跳过（不取实时报价、不触发不通知），与持久化规则路径保持一致
- [改进] 告警中心页面级文案与「通知尝试记录」Tab 接入中英文（i18n）：此前 `AlertsPage` 页面本体与通知尝试记录整节为中文硬编码，而三个子组件（规则表单/列表/触发历史）均已双语，模块内部自相矛盾；现试跑状态、通知渠道/状态、Tab 标题、测试结果统计与警示、通知记录表头与空态全部走 `featureText`，复用既有 `ALERT_*_LABELS` 与新增 `ALERT_PAGE_TEXT`
- [改进] 告警中心「触发历史」与「通知尝试记录」两个 Tab 补齐分页：此前硬编码只取前 20 条（≥21 条被静默截断且无提示），现按 rules 列表同款 `Pagination` 翻页并展示总数
- [改进] 告警中心 Tab 内容按需加载：进入页面只在默认「规则」Tab 拉取规则列表，触发历史 / 通知记录改为首次切到对应 Tab 时才请求，不再页面挂载即三份全量预载
- [改进] 告警后端 `GET /rules` 列表消除 cooldown N+1：每页由每行各一次 `get_rule_cooldown_summary` 单行子查询改为一次 `IN (...)` 批量取回再按行填充，列表页从「20 次额外查询」降到「1 次」
- [改进] 告警 worker 复用 `NotificationService`：此前每条触发规则都新建一次（含配置加载与全部渠道探测），现单个 worker 懒加载一次并在整轮复用，触发越多节省越明显；注入的测试 notifier 仍优先
- [改进] 告警评估 per-rule 超时对齐 dry-run：worker 生产路径的 `_evaluate_one` 补上 `asyncio.wait_for`（沿用 dry-run 的 `DRY_RUN_TARGET_TIMEOUT_SECONDS`），单只股票实时行情/日线查询挂起不再阻塞整轮 `gather`，超时按 skipped 降级
- [改进] 告警 cooldown 并发 upsert 修正：`upsert_cooldown` 改为捕获 `IntegrityError` 后重取幸存行再更新，避免多 worker 并发对同一 `(rule_id, target, severity)` 竞态 INSERT 撞唯一约束、导致 cooldown 静默未写而下周期重复触发/重复通知
- [改进] 前端告警规则参数映射补 `top_weight_pct` / `max_drawdown_pct`：`toSnakeRulePayload` 此前遗漏这两个字段（`types/alerts.ts` 已定义），设置时会静默丢弃，现补齐，与后端参数契约闭合
- [改进] 回测页开始/结束日期改用共享 `DatePicker` 统一控件：此前为原生 `<input type="date">`，各浏览器渲染不一、与站点其余日期控件解剖不一致；现复用组件站统一的 `DatePicker`（`YYYY-MM-DD`、同款高度与样式）
- [改进] 回测结果表「方向匹配」列加 `whitespace-nowrap`：此前方向匹配徽章与文案过长时被折行，破坏列对齐
- [改进] 回测页头部工具条重构为「查询 + 执行」双区布局：左侧为占满可换行的检索字段区（股票代码 / 评估窗口 / 最小天龄 / 阶段 / 开始日期 / 结束日期，开始与结束日期作为一组整体换行不拆开，末尾接「筛选」按钮），右侧为纵向执行面板（1日验证 + 强制重跑一行、运行回测主按钮整宽），消除原先日期拆行、字段间留白、运行控件散落的观感
- [改进] 回测结果表「AI 预测」列改彩色操作徽章呈现：此前动作（买入/观望/卖出等）是三行灰色截断文本叠放、无颜色语义、且趋势与建议常重复堆叠（如「观望 / 震荡 / 观望」）；现动作复用 `Badge` + `getDecisionActionTone` 着色（买入=绿 / 卖出=红 / 观望=黄），趋势与建议作为独立明细行展示并去重（与徽章相同的值不再重复出现），悬停 tooltip 仍可看完整原文
- [改进] 回测结果表「分析日期」列加 `whitespace-nowrap`：此前 `2026-08-11` 等日期在窄列被折成两行，现单行展示
- [修复] 共享 `DatePicker` 补可访问性标签与可编程赋值：此前触发按钮无 `aria-label`，无表单字段，屏幕阅读器不可读、测试无法通过 `change` 设值；现内置一个隐藏的原生 `<input type="date">`（带 `aria-label`、`tabIndex=-1`）承载标签与赋值，视觉仍由 portal 日历按钮呈现
- [改进] 模拟盘成交流水列表的「原因」列改为本地化徽章展示：`reason` 原始枚举（`signal_action`/`stop_loss`/`take_profit`/`ambiguous_stop_loss`）不再原样输出英文，改为按语言映射为「信号触发 / 止损退出 / 止盈退出 / 止损+止盈」，并用徽章颜色区分（信号=中性，止损+止损+止盈=红，止盈=绿），一眼区分「主动跟单」与「被动风控退出」；未知枚举回退为原样文本，不丢信息
- [改进] 模拟盘开放持仓读取合并与回填跳过提示：`PaperService._valuate` 已读取的开放持仓结果直接传给 `_record_snapshot`，去掉一次重复的 `list_open_positions` DB 查询（同一批持仓在一次估值内不再被重复读取）；历史回填完成后若存在因缺行情被跳过的信号，Web 端 toast 额外提示「另有 N 条因缺行情被跳过，可稍后重试」（后端本已返回 `signals_unavailable`，前端此前未展示）
- [改进] 模拟盘页面加载与 Tab 交互优化：首次进入只拉静态数据（账户 / 持仓 / 净值曲线，账户响应已内嵌快照故移除一次独立 snapshot 请求，加载由 6 个降到 3 个请求）；信号 / 成交列表改为切换到对应 Tab 或翻页时才按需拉取，不再每次切 Tab / 翻页重发全部请求；手动刷新 / 回填后仍会同步刷新当前 Tab 列表
- [改进] 模拟盘行情回补缓存复用：`paper_service` 的每只股票日线 bar 缓存由实例级改为进程内共享的模块级缓存（带容量上限与覆盖窗口检查，窗口内只拉一次），后台估值与信号消费共用一个 `PaperService` 产物，拆掉「每 30 分钟估值 / 每信号消费都各自重新实例化、导致缓存从不跨次生效、重拉整段行情」的浪费；空结果不缓存，避免晚到的行情被旧空窗挡住而误判信号不可用；`paper_service` 新增 `clear_bar_cache_for_tests` 供测试隔离
- [新功能] 问股支持生成告警提案：AI 识别到值得监控的价格上穿/下穿、涨跌幅、成交量异动或技术指标（均线/RSI/MACD/KDJ/CCI）信号时，在对话中提出「告警提案」卡片，用户确认后才真正创建告警规则（复用现有 `POST /api/v1/alerts/rules` 校验/权限/通知链路），取消则丢弃
- [修复] 设置中心左侧分类菜单滚动联动：此前左侧菜单被 sticky 固定且无内部滚动，分类较多时底部项被裁出视口，必须把右侧滚动到底才够得着；现左侧菜单限制在视口高度内独立纵向滚动，切换分类时自动把当前选中项滚动进可视区
- [改进] 设置中心「通知」分类新增渠道筛选器：顶部提供渠道下拉（企业微信 / 飞书 / 钉钉 / PushPlus / 自定义 Webhook / Telegram / 邮件 / Discord / Slack / Pushover / ntfy / Gotify / ServerChan3 / AstrBot，不再提供「全部渠道」），选中某渠道只显示该渠道的配置字段；默认自动选中「当前使用」渠道（已配置字段最多的具体渠道，无配置则回退企业微信），避免一进来就平铺 63 个字段；通用与报告选项（`NOTIFICATION_*` 路由 / 去重 / 静默时段、`REPORT_*`、`SINGLE_STOCK_NOTIFY` 等）不属于任何具体渠道，从渠道下拉中拆出，始终在下方独立「通用 / 报告」分区展示，不受渠道筛选影响；此渠道下拉同时驱动「通知测试」面板的测试渠道（共享同一选择），避免两处渠道选择不一致；渠道选项抽到 `notificationChannels.ts` 单一真源，设置页筛选与测试面板下拉的 value、顺序、中英文 label 完全统一；此前 63 个通知字段平铺在一长串里难以定位目标渠道，现按字段前缀归组；纯前端展示过滤，不改保存 / 刷新 / 诊断语义。同时「通知测试」面板改为点「测试」按钮在弹窗中打开（不再内联常驻），由通知分类顶部同一渠道下拉驱动测试渠道
- [改进] 设置页「偏好设置」卡片（主题 / 语言）改为仅在「系统设置」分类下显示：此前该卡片在任何分类下都常驻渲染，与系统设置分类下的其他系统级卡片（鉴权、调度、版本信息）门控方式不一致；现统一为 `activeCategory === 'system'` 时渲染，选中「系统设置」分类即可看到主题与语言切换
- [改进] 告警规则列表「测试」按钮新增试管制图标（`FlaskConical`），与「编辑」「删除」图标保持一致，图标 `aria-hidden` 不影响按钮可访问名
- [修复] 回测 agent 工具默认评估窗口与构建窗口不一致：`get_strategy_backtest_summary` / `get_skill_backtest_summary` / `get_stock_backtest_summary` 及对应 handler 此前硬编码默认 `eval_window_days=30`，而摘要只在回测实际运行所用的窗口下构建（默认 `backtest_eval_window_days=10`），导致 agent 不带窗口调用时几乎总返回「No backtest summary available」；现默认改为从配置 `backtest_eval_window_days` 解析，省略窗口时命中已构建摘要，显式传值不受影响
- [改进] 回测页 eval-window 口径统一：此前页面列头/模式描述随输入框逐键即时变化、而数据仅在点「筛选 / 翻页 / 回测」时才重新拉取，出现列声称某窗口、行却是另一窗口队列的一致性问题；现以「已加载数据实际使用的窗口」（applied window）统一驱动列头、模式描述、结果集标题与分页，筛选时才真正应用输入值；分页/筛选/CSV 导出/「次日证伪」在回测运行期间一并禁用，避免对过期数据操作
- [改进] 回测页日期字段与列表头排版微调：开始/结束日期输入由 `w-40` 收窄至 `w-36`，空态不再留出明显多余空白、与评估窗口/最小天龄等紧凑输入更协调，且填充日期后完整显示不裁剪；结果集各列头加 `whitespace-nowrap`，「止损/止盈」等标题不再折成两行
- [改进] 通用 `Badge` 组件与回测结果集阶段单元格加 `whitespace-nowrap`：状态（已完成/数据不足）、结果（盈利/亏损）、阶段（CN - 盘后/CN - 非交易日）等徽章与文本标签不再被折成两行，列随内容自适应（表已在 `overflow-x-auto` 内可横向滚动）
- [改进] 回测页开始/结束日期改用统一 `DatePicker` 控件：此前是原生 `<input type="date">`，占位「年/月/日」为浏览器本地化默认、弹出的日历也是系统默认浅色样式，与整页深色主题和其它控件风格不一致；现与持仓页/模拟盘共用同一主题化 `DatePicker`（深色日历弹层、统一触发样式，值为 `YYYY-MM-DD` 与后端兼容），高度对齐同级 `h-10` 控件
- [改进] 回测结果集「方向匹配」单元格加 `whitespace-nowrap`：`directionExpected` 文本（持平/上升/下跌）此前在窄列内被竖排成「持/平」两行，现与方向图标保持同一行、不再折行
- [改进] 告警中心「测试结果」提示更明确：试跑状态由原始状态码改为中文标签（已触发/未触发/求值出错），单目标与多目标统一展示「共评估 N 个目标：触发 · 降级 · 跳过」统计；当有目标因缺少实时行情被跳过时，新增警示说明「这不代表未触发，常见于非交易时段、停牌或实时报价缺失」，目标级记录状态（已触发/已跳过/降级/失败）同步用中文呈现
- [改进] 首页上海时区日期格式化复用模块级 `Intl.DateTimeFormat` 缓存：`getShanghaiDateKey` 在自选行 / 今日列表 memo 的逐条循环中被高频调用，此前每次调用都新建格式化器（较重的一次性构造），现复用在模块级常量，行为不变
- [改进] 告警规则列表移除「操作」列的停用/启用切换按钮：启停状态改为在编辑规则对话框内通过「启用」开关调整（编辑/新建表单的开关标签由「创建后立即启用」简化为「启用」），列表仅保留编辑/测试/删除操作；相关 `onToggleEnabled` 回调与后端 `enable`/`disable` 调用随之从列表与页面移除，测试同步更新
- [改进] Toast 弹框改为 iPhone 液态玻璃（Liquid Glass）质感并可读性增强：设置页、对话页与告警中心（创建成功 / 测试结果）的 toast 不再使用半透明色块（`bg-*/10`），改用中性磨砂玻璃表面（70% 不透明 + `backdrop-blur-2xl` + `backdrop-saturate-150`）+ 顶部 specular 高光线 + 表面顶部渐变反光 + 深柔投影，状态由彩色文字表达；关闭按钮由文字字符改为 lucide `X` 小图标（设置页 toast 新增关闭按钮）
- [改进] 界面主题与语言切换从左侧导航菜单移入设置页「偏好设置」分类：侧边栏不再渲染主题/语言入口，改为设置页常驻的「偏好设置」卡片，每项含说明文案；主题改为横向分段 Tab 控件（浅色/深色/跟随系统直接展示，替代原下拉菜单，原 `ThemeToggle` 组件移除），语言保持切换按钮；侧边栏相关测试同步更新
- [修复] 告警中心组合集中度/回撤规则列表不显示阈值参数值：`AlertRuleList` 此前读取 snake_case 键（`top_weight_pct`/`max_drawdown_pct`），前端参数对象实际为 camelCase（`topWeightPct`/`maxDrawdownPct`），导致值缺失只显示规则名；改为读取 camelCase 键并展示阈值百分比
- [改进] 告警中心测试按钮结果改为右上角 toast 展示（5 秒自动消失 + 关闭按钮），替代此前内联在规则列表顶部的大段结果块
- [改进] 告警中心触发历史组件 `AlertTriggerHistory` 全部硬编码中文接入 i18n：表头/状态标签/空态/阶段与数据质量渲染改为语言感知（含英文），`renderPhaseQuality` 不再写死 `zh`，阶段徽章复用语言感知的去前缀辅助函数
- [改进] 告警规则创建表单对「组合集中度 / 组合回撤 / 组合价格状态」三类规则展示配置驱动提示：这三类规则的阈值由风险模块配置控制（非规则参数），表单无需也不能设置参数；此前选中后表单静默为空且提交 `{}`，现明确说明以消除误导
- [修复] 告警中心 `AlertsPage` 触发历史与通知记录加载缺少并发/卸载守卫：`loadTriggers`/`loadNotifications` 与规则列表一致加入 `mountedRef` + 请求 id 守卫，避免 StrictMode 二次挂载后请求返回被静默跳过
- [改进] 任务面板统一不再显示「运行诊断」traceId 折叠块（此前分析任务有、选股任务没有导致不一致），任务条目布局一致，运行流按钮仍保留用于深入排查
- [修复] 选股任务在全局任务图标重复显示：同一选股任务经 SSE 以英文标题混入分析任务列表、又经选股页写入 `screeningTaskStore` 以中文标题展示，运行期间图标出现两条（带 `screen:` 前缀的副本因非真实 taskId，点击提示「不存在 / 已过期」）；现面板聚合时剔除分析列表中的重复条目，仅保留中文标题选股条目并透传真实 taskId，点击可正常进入执行详情
- [改进] 首页审计修复：今日标签刷新按钮现会重新加载今日排行（此前为空操作）；导入/自动分析路径显式传入股票代码作为原始查询，避免提交空 `originalQuery`；大盘复盘提示统一走同步 ref 的更新入口，避免陈旧 ref 漏更新；`useHomeDashboardState` 裁剪 19 个未使用 store 字段与 2 个冗余 Set，减少无关 store 更新引发的整页重渲染；自选增删回调改用 ref 守卫以保住行 memo、提交被跳过时不再清空输入框；`StockAutocomplete` 提交回调上提为 `useCallback` 恢复 memo；今日日期键改 `useMemo`；自选提示文案接入 i18n（新增 `watchlist.addedMessage`/`removedMessage`/`actionFailed`）；大盘复盘轮询魔法数字上提为命名常量；SSE 断线日志附带错误对象便于排查
- [修复] 修复 AI 建议页状态更新/反馈无反应（同类 StrictMode `mountedRef` 缺陷）：`DecisionSignalsPage` 卸载清理仅把 `mountedRef.current` 置 `false` 而未在挂载时重置，开发模式 `<StrictMode>` 二次挂载后该值为 `false`，`handleStatusUpdate`/`handleFeedbackSubmit` 的 `if (!mountedRef.current) return` 守卫在请求成功后静默跳过 `setState`，界面不刷新；改为在 effect 挂载时重置 `mountedRef.current = true`
- [修复] 修复选股页策略下拉无值：`mountedRef` 卸载清理把 `mountedRef.current` 置 `false` 后，React `<StrictMode>` 开发模式二次挂载不会重新初始化为 `true`，导致 `loadStrategies`/`loadHotspots` 的 `if (!mountedRef.current) return` 守卫在请求返回后静默跳过 `setState`，策略列表与热点始终为空（接口 200 但下拉无值）；改为在 effect 挂载时重置 `mountedRef.current = true`（对齐 `HomePage` 既有正确写法）
- [修复] 情报源抓取请求不再复用共享的可变 `proxies` 常量：`requests` 会在请求时对传入的 `proxies` 字典原地追加环境中所有 `*_proxy` 项（`merge_environment_settings` → `proxies.setdefault`），导致模块级 `_DISABLE_REQUEST_PROXIES` 被污染（如本机 `SOCKS_PROXY` 泄漏进每次请求），现改为每次请求传入独立副本，避免共享常量被跨请求污染
- [测试] 测试夹具隔离本地 `.env` 污染：`src.config.setup_env()` 会把仓库根 `.env` 写入进程环境且不复原；此前某用例加载真实 `.env` 后，后续用例会读到开发者本机配置（如 `LITELLM_FALLBACK_MODELS`、`SOCKS_PROXY`）导致结果依赖用例执行顺序；`tests/conftest.py` 新增 autouse fixture 在每个用例前后快照/还原 `os.environ`
- [测试] 修复三处「时间炸弹」测试：yfinance 股息 TTM、东财兜底新闻时效均改为按相对当前时间构造夹具，静态日期会随日期推移掉出时效窗口导致回归
- [测试] 情报源与用量看板 API 契约测试在 `create_app` 下禁用鉴权：本机 `.env` 设 `ADMIN_AUTH_ENABLED=true` 时这些测试返回 401，改为在中间件边界关闭鉴权（对齐 auth 测试既有模式），使契约测试与本机鉴权开关解耦
- [chore] 重新生成静态 OpenAPI spec `docs/architecture/api_spec.json`：此前与 `create_app().openapi()` 存在 90 处缺失路径、127 处缺失 schema 的漂移，重新生成以匹配运行时契约
- [改进] API 错误响应统一到 `{error, message}` 契约并清理内部报错：全局 `HTTPException` / 未捕获异常 handler 复用共享 `error_body()`，`detail` 为空时不回传空值；`alerts` / `paper` 端点错误改走统一 `api_error()`，内部 `str(exc)` 不再泄入响应；`agent` / `stocks` / `history` 若干 500 文案去敏（去掉异常字符串与堆栈），避免向客户端暴露内部细节
- [改进] API 入参与契约加固：`stocks` 报价/历史接口统一 `_validate_and_normalize_stock_code` 规范化股票代码、JSON 文本分析增加 100KB 上限；`history` 日期解析前置并校验非法日期返回 400；`paper` 回填增加起始日期不晚于结束日期的校验；`portfolio` 快照/风险 `cost_method` 收紧为 `Literal["fifo","avg"]`（非法值由 FastAPI 直接 422）、`delete_account` 补齐 `response_model`
- [chore] 移除未注册的 `ErrorHandlerMiddleware` 死代码类及其导出，统一由 `error_handler.py` 的注册式 handler 承载全局异常处理
- [测试] 同步测试到收紧后的契约：`cost_method` 非法值断言由 400 改为 422；litellm 非流式错误断言改为「不泄露内部异常、message 为统一中文文案」
- [改进] hover 位移动效加 `pointer-fine` 门控（Apple 流体界面「减少动效 + 指针门控」原则）：settings-secondary 按钮上浮、hotspot/节点卡上浮、hotspot 卡装饰图标缩放共 4 处位移/缩放动效改为仅在精确指针设备生效，避免触屏首点后残留 hover 位移
- [改进] 全站 `transition-all` 收敛为确切过渡属性（Apple 流体界面「指明确切属性」原则）：输入框/选择器/focus 光晕收敛为 `transition-[border-color,background-color,box-shadow]`；导航项、分页、勾选框、遮罩等纯色变化元素收敛为 `transition-colors`；按钮/节点卡/hotspot 卡保留 `transform`/`filter` 过渡（hover 有位移或亮度变化），进度条收敛为 `transition-[width,background-color]`，折叠面板收敛为 `transition-[max-height,opacity]`；22 处 `transition-all` 全部收敛，行为不变
- [改进] 多维度情报搜索（`search_comprehensive_intel`）由「每维度轮询一个引擎」改为「按优先级逐个尝试多个来源」：某来源无相关结果或异常时自动尝试下一个来源，命中直接个股新闻或满足目标条数则提前结束该维度；东财免费兜底仅在没有任何来源返回原始结果时触发（与个股资讯路径语义一致），避免来源返回结果但被时效过滤掉时误触网络兜底
- [改进] 首页策略菜单按钮动效属性收敛：`transition-all` 改为 `transition-colors`（Apple 流体界面「指明确切属性」原则，按钮 hover 仅变背景/边框色，避免对无变化属性做无意义过渡）
- [修复] 决策信号建议单元移除原生 `title`（治理规则禁止对 div/span 使用原生 title，键盘不可达）：改用可访问 `Tooltip` 承载完整建议文本（操作/期限/风险/观察条件），悬停或聚焦时展开；持仓页持有信号风险摘要测试同步为「悬停建议单元 → 断言 Tooltip 内容」，并在账户切换重建行（key 含 accountId）时每次重新定位触发器，避免拿到已卸载旧行
- [测试] 同步告警模块测试到自定义 `Select` 异步渲染契约：`AlertRuleForm`/`AlertRuleList` 共 18 条测试仍按原生 select 交互而失败；改为「点击触发器 → `await waitFor`/`findByRole('option')` → 点击 `data-value` 选项」，并修正 `waitFor` 从 `@testing-library/react` 导入，消除级联失败，告警测试全部恢复通过
- [修复] 决策信号页并发守卫收尾：切换非空信号时先清空上一信号的 outcomes/feedback，避免 persist-reassess 原地切换 id 时新信号头部下闪一帧旧数据；新增卸载守卫使 reassess/persist 在途请求作废，状态更新与反馈提交叠加 `mounted` 守卫，避免卸载后 setState（对齐页内既有 request-id 范式）
- [测试] 决策信号页测试同步到自定义 `Select` 异步渲染契约：共享 `Select` 选项经 `requestAnimationFrame` 异步渲染后，`selectByValue` 助手与 22 处内联 `getByRole('option')` 仍按同步读取而失败；改为等待选项出现（`await waitFor` / `await findByRole`）再点击或断言 `data-value`，消除随后续用例的级联失败，59 条测试全部恢复通过
- [测试] 修复股票指数加载器测试的模块级缓存污染：`loadStockIndex` 存在按小时缓存的模块级单例，同文件内用例未重置便复用上一用例的成功结果，导致压缩格式/空数组/fallback 等 6 条用例读到泄漏数据（如期望 2 条却得 5 条、fetch 从未被调用）；改为 `beforeEach` 中 `vi.resetModules()` + 动态重新 import 加载器，使每条用例获得干净的模块状态（纯函数仍静态导入，加载器本身行为不变）
- [测试] 同步设置页测试到自定义 `Select`（listbox）交互契约：共享 `Select` 由原生 `<select>` 重构为按钮触发器 + 门户下拉后，`SettingsField`/`LLMChannelEditor`/`NotificationTestPanel` 共 23 条测试仍按原生 select 交互（`getByRole('option')`、`.toHaveValue()`、`fireEvent.change`）而失败；改为「点击触发器打开下拉 → `await findByRole('option')` → 点击选项」，`.toHaveValue` 改断言 `data-value`
- [改进] 设置页调度状态刷新加入并发守卫（`schedulerStatusRequestIdRef`）：手动刷新或调度运行时状态切换时只让最新一次请求生效，避免慢响应覆盖新状态（对齐页内 `setupStatusRequestIdRef` 既有范式）
- [修复] 持仓页快照/风险与事件列表加载加入并发守卫：账户/成本法或事件筛选快速切换时只让最新一次请求生效（`snapshotRequestRef`/`eventsRequestRef`），避免慢响应覆盖新数据或提前清除加载态，并作废卸载后的在途请求
- [改进] 选股候选列表展开交互传稳定回调：`CandidateListItem` 的 `onToggle` 改为按 code 传参、父组件直接传稳定 `handleToggleCandidate`（对齐 `onAnalyze`），此前内联 lambda 每次父重渲染都新建引用、击穿 `memo`，导致展开/收起单个候选时全量重渲染所有候选
- [修复] 回测页并发请求防呆：结果与绩效两个异步加载加入卸载守卫（`mountedRef`）与请求序号守卫（`resultsRequestRef`/`perfRequestRef`），只让最新一次请求生效，避免快速筛选/翻页/运行/切换下慢响应覆盖新数据、初始加载与用户操作竞态，以及卸载后 `setState`
- [改进] 问股 Agent Chat 渲染优化：AI 消息的 Markdown 正文用 `memo` 包裹，任务进度 SSE 事件期间已完成消息不再每次重复解析渲染（消息数组引用不变时跳过重建）；实时进度步骤列表设保留上限（最近 200 条），完整详情仍会在完成时随消息的 `thinkingSteps` 保留，避免长任务（尤其 codex）无界累积内存与渲染
- [修复] 问股 Agent Chat 流式超时统一进入「已超时」终态：后端整体超时事件补上 `error_code='timeout'`，前端据此与 codex 的 timeout 处理对齐，不再显示普通错误
- [修复] 问股 Agent Chat 消息 id 改用 UUID，避免同一毫秒创建多条消息时 React key 碰撞
- [改进] 首页报告区隔离：把右侧报告 / 大盘复盘 / 历史趋势子树抽取为 `memo` 组件 `HomeReportRegion`，其 props 在任务进度期间引用稳定，任务运行时的 SSE progress 更新不再让整份 Markdown 报告随之重建（自选列表行仍按行更新进度，属预期）
- [改进] 首页 `activeTasks` 订阅下移：自选区自行订阅运行中任务并按股票代码推导「代码 → 运行中任务」映射，首页顶层不再订阅 `activeTasks`，任务进度 SSE 更新只触发自选区行重渲染，页头与报告区不再随之整页重建
- [改进] 首页与选股页列表行减少不必要重渲染：自选/今日/历史与选股候选列表项改由 `React.memo` + `useCallback` 稳定回调承载，任务进度更新不再连带整行重建；大盘复盘轮询状态以 ref 守卫避免内容未变时重复 `setState`；两处异步 loader 增加卸载守卫，避免卸载后 `setState`；选股 `maxResults` 提交前 clamp 到 1–100，自选股条件无有效结果时收敛到固定 A 股市场死状态
- [改进] 选股任务运行中时结果区显示「选股任务运行中」进行中占位，替代空表/骨架闪烁
- [改进] 情绪评分与市场阶段徽章抽取为共享组件（`SentimentBadge`/`MarketPhaseBadge`），历史列表、首页自选与选股页复用同一实现，消除多处样式漂移
- [修复] 选股热点详情数据源并发时不再静默丢源：概念股/行业成分源共享的并发槽被占满时改为内联补拉（数据完整），单个 HTTP 读取超时不再吞掉整次调用的共享时间预算（按剩余预算拆分 connect/read 超时）；`include_search` 重复请求同一热点新闻搜索改为按主题缓存（TTL 10 分钟，服务不可用的失败结果不缓存），避免重复点击每次都触发阻塞式网络搜索
- [改进] 基本面诊断卡显示失败原因：当基本面部分可用/失败时，会在该块下以 warning 列出各数据源的实际状态（如 `fundamental_bundle:failed`），区分「接口失败」与「该标的不支持」，不再笼统显示为「缺失」；原始报错字符串不落盘以避免密钥泄露
- [修复] 筹码分布诊断对美股/港股/ETF 等不支持市场不再误报「缺失」：此前「该标的不支持筹码」的标记（`chip_not_supported`）只被读取、从未写入，现已接入分析上下文，这类标的显示为「该类标的不支持筹码」而非误导性的「缺失」
- [改进] A 股基本面在 AkShare 数据源无有效成长/盈利内容时自动回退到 Baostock（免费、稳定、无需 token）：通过 `query_profit_data`/`query_growth_data` 补全 ROE、毛利率、净利率、净利润同比与财报关键指标，`source_chain` 会记录真实数据源（`baostock_profit`/`baostock_growth`），缓解基本面「经常不可用」
- [新功能] 新增模拟盘（Paper Trading）：用 AI 决策信号驱动虚拟账户，跟踪买卖建议的实际表现，产出持仓、净值曲线、成交与信号记录；支持历史信号回填、每日估值与新增 Web `/paper` 页面；通过 `PAPER_TRADING_ENABLED` 控制每日估值后台任务
- [改进] 模拟盘每日估值后台任务默认开启（`PAPER_TRADING_ENABLED` 默认值由 `false` 改为 `true`），长运行进程启动后默认按日幂等估值并生成净值曲线；可在 `.env` 设 `PAPER_TRADING_ENABLED=false` 关闭
- [修复] 模拟盘历史回填不再静默截断：repo 分页上限为 100 条，回填改为按页取全区间信号，保证 `from_date..to_date` 内所有信号都被重放，避免更早信号被丢弃
- [修复] 模拟盘开仓/加仓增加现金护栏与单标的仓位上限：买入金额不超可用现金、加仓不突破目标权重（净值的 20%），避免 `buy`/`add` 信号反复出现时现金为负或单一股票集中度过高
- [改进] 模拟盘「刷新估值」改为强制重估当天：即使当日已有快照也重新按现价估值（含止损/止盈判定），手动刷新真正生效；定时调度仍按日幂等
- [修复] 模拟盘估值 bar 缓存加锁并按日期范围自愈：后台估值线程与分析线程并发读写不再竞态，回填覆盖更早日期时自动重载更宽窗口，避免取不到历史价格
- [改进] 模拟盘仅在真正改变仓位（开/加/减/清/止损止盈退出）时才执行当日估值并写快照；观望/忽略信号不再产生冗余快照，回填大批信号时更高效、净值曲线更干净
- [改进] 模拟盘加仓信号若携带新的止损/目标价，会更新持仓的风控线，不再一直沿用开仓当天的旧值
- [修复] 模拟盘开仓当天不再触发止损/止盈退出：买入以当日最高价为成交价，若同日又用当日最低价判止损会系统性误杀新开仓位；现改为开仓日仅按收盘价估值，从次日起才判定止损/止盈，使回测成交更符合真实逻辑
- [改进] 模拟盘每只持仓的目标仓位可配置：新增 `PAPER_POSITION_WEIGHT`（占总资产比例，默认 `0.20`，范围 0.01~1.0），可调整体仓位激进/保守程度
- [改进] 模拟盘持仓按市场整手规则取整：A 股与港股按 100 股整手近似（港股整手随个股而异，此处取常见默认），美股按 1 股逐股，替代原先只处理 A 股、其余一律按整数股的逻辑
- [chore] 修复前端 `react-hooks/set-state-in-effect` lint 错误使 `npm run lint` 归零：`DatePicker`/`Select` 弹层定位改由 `requestAnimationFrame` 异步触发、`DatePicker` 日历视图与 `FloatingTaskPanel` 自动收起改为渲染期状态调整，消除 CI `web-gate` 的 lint 阻断项
- [修复] 持仓页「录入交易」弹框内，股票自动补全不再吞掉回车：未选中候选时按回车会提交当前表单，避免「怎么不能提交」；选中候选后下拉保持关闭，无需再点第二次关闭（仅对需要该行为的录入交易生效，其余使用自动补全的页面行为不变）
- [改进] 持仓页在「全部账户」视图下置灰持仓明细卡片头部的录入交易 / 录入资金 / 公司行为 / 导入CSV 按钮，防止打开无目标账户的模态框；选中具体账户后自动恢复可用
- [改进] 持仓明细表格显示更合理：数量、成本、现价等数值去掉多余尾零（如 `420.0000`→`420`），避免精度噪音；AI 建议列收紧宽度并支持悬停通过 `title` 展示完整建议文本（风险摘要与观察条件不再被截断隐藏）
- [改进] 持仓页事件记录由页内卡片改为居中弹框（复用共享 Dialog）展示：点击某行持仓明细弹出弹框并聚焦该股票事件（自动切到交易流水、按股票代码过滤、显示可一键清除的「只看 XX」标签）；录入交易 / 录入资金 / 公司行为 / 导入CSV 按钮移至持仓明细卡片头部（「全部账户」视图下禁用），并移除「全部账户」下的写入保护提示 banner；弹框内列表为主、搜索退居次要：默认仅一行精简工具栏（事件类型下拉 + 代码筛选 + 刷新），日期 / 方向等筛选收进一行内紧凑排列，弹框加高（`max-h-[92vh]`）以展示更多行；交易 / 资金 / 公司行为三类流水均新增「账号」列；流水删除按事件 id 执行，在「全部账户」视图下同样可用
- [改进] 首页个股栏的 AI 建议徽章精简：仅对「观望」这类中立建议保留可见标签，其余建议（买入 / 卖出 / 持有等）不再占用徽章空间，完整建议与分数改为悬停整行时通过 tooltip 提示展示，避免每行右侧标签拥挤
- [改进] Web 桌面端左侧导航改为左上角悬浮菜单图标，鼠标悬停即弹出无遮罩的浮动菜单，移出菜单区域后自动收起，点击仍可切换；菜单打开时悬浮按钮置顶显示关闭图标
- [新功能] Agent Chat 按会话持久化 Skill 选择，支持刷新和会话切换恢复，并区分省略 `skills`、显式空列表与非空选择；无持久化状态的历史会话继续使用运行时默认且不会被静默转为显式选择，复用分析 `context` 中残留的 legacy `skills` / `strategies` 也不会覆盖顶层三态或会话状态，非空但全部无效的 Skill 请求不会被当成显式空列表并清空既有选择
- [改进] 后端 CI 在不跳过离线测试的前提下按完整测试文件分成三个独立 runner 并行执行，由单一 `backend-gate` 汇总门禁结果；实测文件耗时和首分片静态检查成本共同参与负载平衡，新测试文件自动纳入，现有 pip 安装和测试参数保持不变，避免 xdist 进程内并发的全局状态竞态。
- [新功能] Agent Chat 正文改为真 token 流式输出：后端新增 `content_delta` SSE 事件，litellm 后端开启 `stream=True` 边生成边推送文本，codex 后端按 `agentMessage` item 帧粒度尽力推送；Web 前端按 ~40ms 节流渐进渲染 Markdown、滚动跟随，`done` 事件仍以完整 `content` 为权威终稿收敛。旧客户端忽略未知事件类型，`done` 契约不变，无破坏。
- [改进] 选股页面视觉与首页统一：分节容器改为玻璃拟态卡片（`glass-card`），顶部状态与标题统一走 `DashboardPanelHeader`，热点与策略徽章复用 `Badge` 组件，选股结果由 10 列密集表格改为可展开的卡片列表（复用 `ListItemRow`，详情保留完整字段），后端筛选逻辑与数据契约不变。
- [改进] 设置页全面统一到 glass-card 视觉语言：分组卡片、分类导航、加载骨架与 AI 模型 / 智能导入 / 后端状态面板的容器与分区标题统一走 `glass-card` + `DashboardPanelHeader`，与首页 / 选股页一致；字段行与内部小徽标保留浅表面，DOM 结构、`data-testid` 与后端 / API 数据契约不变。
- [改进] 全站玻璃卡片材质向 macOS 感收敛：共享 `.glass-card` / `.dashboard-card` 的毛玻璃更通透（背景 0.42→0.32）、模糊更自然（`blur(18px) saturate(190%)`→`blur(20px) saturate(160%)`）、阴影改为更轻更柔的多层弥散（接触影 + 环境影 + 顶部内高光）；设置页、选股页、首页等所有使用玻璃卡的页面观感保持一致，小控件阴影 token 不变。
- [改进] 设置页剩余元素源码级统一到全站共享语言：按钮变体 `settings-primary`/`settings-secondary` 全部替换为共享 `primary`/`secondary`，表面与边框 token（`settings-surface*`/`settings-border*`）替换为 `bg-elevated`/`bg-hover`/`border-border/N`，主色复合类（`settings-accent-text`/`settings-accent-badge`/`settings-nav-item-active`/`settings-drag-active`）改用共享 `hsl(var(--primary))` 任意值表达，设置页源码不再保留两套类系统；`index.css` 的 `--settings-*` 变量与输入框控件保留不动，DOM 结构、`data-testid` 与数据契约不变。
- [改进] 用量页视觉与全站 glass-card 语言统一：统计卡（`StatCard`）、模型用量卡、调用类型分区与最近调用表格容器统一为玻璃拟态卡片，分区标题走 `DashboardPanelHeader`，刷新按钮改用共享 `Button` 次按钮变体；`Card`/`terminal-card` 与 `btn-secondary` 旧表面对齐到共享语言，DOM 结构、`data-testid` 与数据契约不变。
- [改进] 告警中心视觉与全站 glass-card 语言统一：新建规则表单、规则列表、触发历史与通知尝试记录四个分区由 `Card`（`terminal-card`）统一为玻璃拟态卡片，分区标题走 `DashboardPanelHeader`，与设置 / 用量 / 首页一致；表单与列表控件本就复用共享 `Button`/`Input`/`Select`，DOM 结构、`data-testid` 与数据契约不变。
- [改进] 任务执行统一收敛到左侧任务图标：将分析任务与选股任务的进度查看合并到 Shell 全局常驻的悬浮任务图标（`GlobalTaskCenter` + `FloatingTaskPanel`），所有页面共用同一入口实时查看进度；选股页移除页面内联的「选股运行中/选股完成」进度区与运行详情，结果候选列表仍为完成态的唯一输出。
- [测试] 后端 CI 默认覆盖所有非 Web 改动，仅对已证明安全的纯 Web 路径跳过，并将整个 Web public 目录及前端渠道模板、设置帮助视为跨层运行合同；补充纯 Web、共享 Web 资产及 Web/非 Web 混合改动的过滤语义回归，明确 `predicate-quantifier: every` 按单文件匹配全部规则、再以任一匹配文件触发门禁。Docker CI 继续按构建输入过滤。离线测试保留稳定的串行执行与慢用例摘要，并移除重复用例和测试内真实等待。
- [改进] 告警中心「新建规则」由左侧常驻表单改为居中弹框：规则列表头部新增「新建规则」按钮，点击打开居中 Dialog（复用设置帮助弹框模式），表单挂载时打开、提交成功自动关闭并刷新列表，Escape / 背板点击 / 关闭按钮均可关闭，创建错误在弹框内显示；左侧固定列移除、规则列表独占宽度，DOM 结构 / 数据契约不变。
- [改进] 持仓页视觉与全站 glass-card 语言统一：顶部账户/成本法工具栏与新建账户卡、总资产/总市值/总现金/汇率状态统计卡（复用 `StatCard`）、持仓明细表、集中度饼图、回撤/止损/范围/AI 风险四张风险卡、手工录入交易/资金流水/公司行为三张表单卡、券商 CSV 导入与事件记录，全部由旧 `Card`（`terminal-card`/`gradient-border-card`）与 `btn-secondary`/`text-secondary`/`border-white/*` 统一为 `glass-card` + `DashboardPanelHeader` + 共享 `Button`/`Select`/`EmptyState` 语言，按钮与表单控件对齐共享变体；DOM 结构 / 数据契约不变，`text-secondary` 缺价回退类名同步为 `text-secondary-text`。
- [改进] 持仓页整体布局对齐首页：账户/成本法工具栏由滚动内容中整块 glass-card 改为固定在顶部非滚动的 `<header>`，下方统计卡/持仓表/表单在独立 `space-y-4` 滚动容器中滚动，页面高度与首页对齐为 `h-[calc(100vh-4rem)]`；DOM / 数据契约与可访问名不变。
- [改进] 持仓页手工录入（交易 / 资金流水 / 公司行为）与券商 CSV 导入由常开的表单卡改为事件记录卡头部的「录入交易 / 录入资金 / 公司行为 / 导入CSV」按钮触发居中模态框，复用共享 `Dialog` 组件（告警页「新建规则」一并收敛复用）；提交成功后自动关闭弹框并刷新统计 / 持仓 / 事件记录，未选择账户时提交按钮禁用并在弹框内提示，Escape / 背板点击 / 关闭按钮均可关闭。页面对比信息：三张手工录入卡与 CSV 导入卡从页面移除，页面正文显著变短。
- [改进] 持仓页「录入交易」股票代码输入框升级为股票自动补全（支持按代码 / 中文名 / 拼音 / 别名搜索，复用现有 `StockAutocomplete` 组件）；选中某只股票后自动带出标准化代码，并尽力拉取实时行情，把当前价作为成交价默认值预填（可手动修改），行情获取失败时保留空价格不阻塞录入。

- [修复] Web 分享图改为用户点击“分享”后才按需生成，不再在报告加载时自动请求
- [修复] 将 `SCREENING_ENABLED` 及 Web 选股功能开关归入“基础设置”，选股导航入口继续由该开关控制
- [修复] 飞书交互机器人在 `FEISHU_DOMAIN=lark` 时让 Stream 长连接与消息回复统一使用 Lark 国际版 API 域名，避免 SDK 默认连接飞书国内域名并返回 `Incorrect domain name`（fixes #937）。
- [修复] `scripts/ci_gate.sh` 的 `offline_test_suite` 给 `pytest -m "not network"` 加 `--timeout=120 -o timeout_method=thread` 与 `-o faulthandler_timeout=300`：单个测试（含其 teardown）超过 2 分钟直接 fail，单个测试（含其 teardown）超过 5 分钟时 dump 全部线程栈到 stderr。配合 `.github/requirements-ci.txt` 新增 `pytest-timeout>=2.3.0` 依赖。issue #2131 报告过 backend-gate 在 AlphaSift hotspot 用例附近间歇性无 traceback 卡住直到被 GitHub Actions 取消，此次修复让任何未来 CI hang 都会留下可定位的失败信息或 post-mortem 栈，而不是静默消亡。同步修正 `.github/workflows/docker-publish.yml` 的 `Install backend gate dependencies` 与 `setup-python cache-dependency-path` 对齐 `ci.yml` 的 backend-gate 依赖安装方式，避免发布流程跑同一个 `./scripts/ci_gate.sh` 时因缺少 `pytest-timeout` 而直接 fail。

- [修复] 选股策略栏稳定展示完整中文策略列表，并保留自定义策略 ID 入口
- [修复] 选股热点详情统一使用中文业务文案，不再显示内部类名、字段名和原始数据源错误
- [改进] 选中热点后先展示榜单已有摘要和核心股，后台再补充完整详情，并将单次热点源等待上限收紧为 8 秒
- [修复] 刷新热点榜单并保留当前题材时同步绕过详情缓存重拉该题材，避免新榜单继续搭配旧路线与成分股；详情质量可用且字段完整时不再展示底层数据源尝试失败
- [改进] 热点成分股并行获取东方财富与同花顺数据，并按固定数据源优先级合并；AkShare 调用复用 DSA 的可终止子进程 timeout，同花顺 HTTP 调用设置 connect/read timeout，并以进程级并发槽限制活跃任务、在返回前回收 worker；题材详情可按需复用 DSA 原生搜索服务的数据源优先级、时效过滤、缓存与同请求合并补充安全且带链接的近期消息，真实供应商调用使用限流且可终止回收的子进程，并在本地压缩摘要以避免额外 LLM 等待与降级提示；显式搜索增强不写入热点详情共享缓存或 Web 页面缓存
- [修复] 选股尾部轮换以分析器输入顺序为权威并保留并列分候选顺序；热点消息增强分别追加 `route` 与原始 `timeline`，同键搜索只允许缓存 owner 启动供应商链，子进程启动或清理失败也会释放全局容量；热点默认 provider 的正数超时配置覆盖板块、成分股及直接详情 fallback 并传递剩余硬截止，关闭外层预算仍保留单源安全上限；主动新闻搜索以同一绝对 deadline 覆盖缓存等待、重新竞争和 provider 执行，并区分有效空结果与运行失败
- [改进] 精简选股页面的重复说明，将任务标识、快照统计和排序诊断折叠到运行详情
- [新功能] SkillAggregator 基于独立满足 30 条 evaluated 门槛的真实 Skill Outcome bucket，使用 Beta 先验收缩、unable 惩罚和多周期证据加权生成有界运行时权重；缺失、低样本或异常统计保持中性。
- [改进] 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，统一使用 `ScreeningService`、`SCREENING_ENABLED` 和 `/api/v1/screening`，并保留 Apache-2.0 归因与来源版本记录。
- [新功能] 选股结果按 `run_id` 持久化到 DSA 数据库，新增运行历史和数据源历史 API，接入公告事件上下文及其搜索缓存，并支持将候选连同筛选策略映射的 skill 交给单股深度分析。
- [修复] Outcome 候选按上次尝试时间公平调度，避免持续新增的缺失 key 使旧 `pending` outcome 永久得不到重试。
- [新功能] 新增按 skill、horizon 与 outcome engine version 独立聚合的只读 Skill Opinion 表现统计；少于 30 条 evaluated 样本时仅返回观察性计数，不输出表现指标或调整运行时权重。
- [修复] 选股主模型返回空内容、非 JSON 或低覆盖结构时继续尝试备用模型；全部失败时明确展示确定性因子排序状态。最终 JSON 必须在 `content` 或 `output` 块中；`reasoning_content`（链式思考）被视为内部辅助，不作为最终结果。
- [改进] Web 选股使用浏览器匿名种子与运行 ID 在最终评分后的有界近分池中生成每次运行的候选组合；Web Storage 不可用时在页面会话内存中复用同一临时种子；本地评分覆盖完整短名单，远程分析继续遵守数量上限，且只有完成相同后置分析的候选可参与轮换；原 Top-N 前半部分、明显领先候选、硬过滤、风险否决和得分保持不变。
- [改进] 热点榜单刷新与选股长流程解除双向串行等待，热点详情改为选中后按需加载；选股默认复用 5 分钟内且数据源优先级一致的成功全市场快照，同一来源链中的后备源结果也可复用，并在后台任务中展示快照、候选上下文、LLM 重排、最终评分和新闻事件增强阶段。
- [修复] 选股日线增强改用请求级 DSA-first fetcher 注入，不再临时替换进程级函数，避免重叠请求泄漏 wrapper 或重复执行 fallback；多个后置分析器按最新分数逐级重排，远程分析状态跟随实际提交候选，超限候选统一记录为 `skipped`，外部响应也不能改写未提交候选。
- [修复] 统一等价股票代码的本地日线候选与同源窗口解析；冲突沪深交易所代码不再降级匹配裸码，回测仅接受快照或交易日历确认的起点，并在同一起点中优先完整的单一代码窗口。
- [新功能] 新增按 individual SkillAgent 自身 signal、版本化 engine 与本地已存同源日线窗口计算并持久化 `skill_opinion_outcomes` 的核心服务。
- [修复] #1970 关闭认证属于高风险操作，即使携带有效 session cookie 也强制要求再次输入当前管理员密码二次确认；后端 `auth_update_settings` 的 disable 分支统一走 currentPassword 校验，命中 rate limit 时与 enable 路径一致返回 429，前端 `AuthSettingsCard` 在关闭认证时如有缺失当前密码将阻止提交并给出内联提示。
- [改进] 优化首页侧栏任务面板与自选股工作区：支持折叠任务摘要、自选股直接打开最新详情，并压缩头部操作以释放窄侧栏列表空间。
- [修复] 收敛自选股行交互与今日状态语义：详情与移除操作使用独立可访问按钮，详情提示从当前行状态实时派生并区分查找中、查找失败和确认无详情，任何 stock-bar 请求及完成任务后的数据刷新都会在开始时进入待确认状态，旧 stock-bar 或 fallback 报告在重新确认或未知期间不会作为最新详情开放；刷新失败时不再把旧历史记录误标为今日分析，自选股刷新会显式重试列表、stock-bar 与逐股票详情查询，逐股票 fallback 使用固定 worker 并发上限并取消已失效的查询批次。
- [新功能] 新增按 individual SkillAgent 自身 signal、版本化 engine 与本地已存同源日线窗口计算并持久化 `skill_opinion_outcomes` 的核心服务；本阶段不提供管理员 API、表现统计、样本充足度或权重调整。
- [新功能] STOCK_LIST 解析新增 `parse_analysis_target()` 单条目解析契约，支持 sh/sz/bj/hk/us 前缀校验、裸码默认归股票、未命中前缀降级为股票三段语义；保留现有 `split_stock_list()`/`serialize_stock_list()` 行为不变，并对外暴露 `IndexRegistry`、`AnalysisTarget`、`ParseStatus`、`default_index_registry()` 以便上层注入自定义指数白名单（关联 issue #2063 Phase 1）
- [修复] `parse_analysis_target()` 在显式交易所后缀输入被规范化层拒绝时（如 `600519.BJ`、`600000.HK`、`1234567.SH`、`abc.SH`），不再静默改写为 `sh<digits>` 或继续走裸码分类导致误判为 US，而是直接返回 `unsupported` 并携带可定位原因；保留 `000300.SH` / `sh000300.SH` 等已知 INDEX alias 的索引命中路径（关闭 PR #2122 review blocker OR-COR-607f1395 / OR-COR-26596201 / OR-COR-d6afd0d6）
- [修复] `parse_analysis_target()` 收敛显式交易所后缀输入的 3 个新 correctness blocker：畸形混合 alias（如 `sh0x00300.SH`）不再被数字过滤后重建为已注册指数；dotted-prefix 形态（如 `SH.000999`）在 invalid base 时不被 strict-suffix 误拒，亦不静默降级为畸形 canonical stock，统一返回 `unsupported`（白名单交易所的合法 alias 通过 `sz399001.SZ` / `sz399006.SZ` 等命中 INDEX）；外盘半显式 suffix（.T/.KS/.KQ/.TW/.TWO）非法 base 不再静默回退到 US stock，而是返回 `unsupported` 并标记外盘 suffix（关闭 PR #2129 review blocker OR-COR-d83a3580 / OR-COR-b3e32200 / OR-COR-e21e9de5）
- [修复] `parse_analysis_target()` 在 `us` 前缀分支按 Phase 1 contract（issue #2063 maintainer clarification 2026-08-01）统一收紧：`us` 前缀本身大小写不敏感（`us`/`US`/`uS`/`Us` 均可），但 ticker base 必须为 canonical uppercase US symbol 形态（regex `^[A-Z]{1,5}(\.[A-Z]{1,2})?$`，与 `data_provider/us_index_mapping.py` 和 `stock_code_utils._normalize_code_and_exchange` 一致）。`us` 前缀输入只要 base 不 match 该 regex——含任意小写字母（`usfd` / `usm` / `usibm` / `usamd` / `usge` / `usbk` / `usaapl` / `usshop` 全小写、`Usfd` / `USibm` / `Usaapl` / `uSfd` / `USaapl` mixed-case prefix + lowercase base）、含标点（`usbrk.b` / `usshop.us` lowercase base with punctuation）、含数字（`us1` / `us1a` / `us12a` lowercase base with digits、`US1` / `US12345` all-uppercase but invalid US shape）一律直接返回 `unsupported` 并提示用户使用 uppercase base（如 `usAAPL` / `usBRK.B` / `usSHOP.US`）或 bare 大写形态（如 `USFD`）。docstring 同步更新明确「exchange 前缀大小写不敏感，但 `us` 后 base 必须 match canonical US symbol shape」契约。该 contract 一致规则同时关闭 PR #2129 review blocker OR-COR-9c3d2c44（`usfd`/`usm` 全 lower 静默大写）、OR-COR-2f0d1a7e（`usibm`/`usge`/`usbk`/`usaapl` 长度依赖剥前缀分叉）、OR-COR-7b45f5c1（`Usfd`/`USibm`/`Usaapl` mixed-case prefix + lowercase base 绕过 lowercase-only guard）与 OR-COR-us-prefix-nonalpha-guard-gap（`usbrk.b`/`usshop.us`/`us1` lowercase/non-alphabetic base 绕过早期 `raw.isalpha()`-gated guard 走到 `_split_prefix` 异常剥前缀），无需 US ticker 白名单。mixed-case `usBRK` / `UsBRK` / `uSBRK` / `usFD` / `usM`（prefix any case + upper base）与全 upper `USAAPL` 等仍按显式前缀剥前缀；`USFD`/`USM` 等 ≤5 字母全 upper 仍按 bare ticker 保留
- [修复] `_split_prefix()` 对带 `.US` 后缀的 bare 美股代码（`SHOP.US` / `HKD.US` / `BJRI.US` / `USFD.US` / `BRK.B` / `AAPL.US`）应用与裸 1-5 字母代码（`USFD` / `SHAK` / `AAPL`）相同的 short-circuit：只要 token 严格 match canonical US symbol shape regex `^[A-Z]{1,5}(\.[A-Z]{1,2})?$`，就不进入 `_KNOWN_PREFIXES_SORTED` 扫描。此前 short-circuit 只对纯字母 (1-5 ASCII uppercase letters) 生效，导致前两字符碰巧撞上 `sh` / `hk` / `bj` / `us` 已知前缀的 dotted `.US` 代码（`SHOP.US -> sh`+`OP.US`、`HKD.US -> hk`+`D.US`、`BJRI.US -> bj`+`RI.US`、`USFD.US -> us`+`FD.US`）被错误拆分，exchange 与 canonical_id 双双错位。mixed-case 显式前缀代码（`usAAPL` / `hk00700`）仍按原 prefix 路径处理。关闭 PR #2129 round-4 review blocker OR-COR-bare-us-suffix-prefix-collision
<!-- 新条目格式：- [类型] 描述（类型取值：新功能/改进/修复/文档/测试/chore）-->
<!-- 每条独立一行追加到本段末尾，无需分类标题，合并时冲突最小 -->
- [文档] FAQ 补充 macOS 桌面应用被 Gatekeeper quarantine 阻止启动时的受信任安装包临时放行步骤（refs #2113）。
- [新功能] LLM 渠道新增显式 Chat Completions / Responses API Surface，支持 Anspire GPT-5.6 系列等 Responses-only 模型，并统一连接测试、主分析、筛选、图片识别与状态诊断路由；所有运行路径先按同一规则解析协议再校验 Surface，混合 Surface 的同名路由按未知能力保守处理；显式 Anspire 渠道独占共享 Key，非法 Surface 或协议不匹配时不会把该 Key 回退为旧版 Chat 部署，同时保留无关的 Gemini/OpenAI 等 legacy provider；本地 loopback 渠道可在图片识别路径继续无 Key 调用，远端渠道仍要求凭据；禁用渠道不会因残留 Surface 配置阻断其他兼容 fallback；Web 编辑器不会静默改写非法历史值，并允许将 Hermes 非法 Surface 修复为 Chat Completions。
- [修复] 将 Responses 渠道的协议、模型 provider、公开 route alias 与 wire-model 构造收敛为统一路由契约，保存校验、运行时加载、状态诊断、选股入口和 Web 编辑器共同使用当前安装的 LiteLLM provider registry，拒绝 `openai` 协议下显式非 OpenAI provider 的模型、拒绝同一 alias 混用 Chat/Responses，并保留 OpenAI-compatible 网关自有的带斜杠模型 ID。
- [修复] 模拟盘信号消费加入 per-account 串行锁与「数据不可用」可重试语义：同一账户的信号消费/每日估值/历史回填串行执行，避免并发重复建仓或现金竞态；`buy`/`add`/`reduce` 在缺失行情价时返回 `data_unavailable` 且不落信号记录（下次信号可重试补齐），回填响应新增 `signals_unavailable` 计数
- [改进] 市场复盘红涨绿跌配色方案默认值统一为 `green_up`：`market_review_color_scheme` 配置默认、环境变量读取与配置注册表默认值三处由 `red_up` 收敛为 `green_up`（与文档默认一致，避免同一配置在运行时与注册表之间漂移）
- [修复] 前端 API 错误解析补全 FastAPI 422 / 429 分支：`isRecord` 不再把数组当对象（修复 422 校验详情 `detail` 数组被误判为单一对象而整段 JSON 化），422 渲染为逐字段可读错误、429 归为「请求过于频繁」；CSV 导入上传不再手动写死 `multipart/form-data` Content-Type（由浏览器自动携带 boundary），避免上传请求被拒
- [改进] 调度跨进程互斥：API 运行时调度器与 CLI `--schedule` 定时路径共用基于 `fcntl` 文件锁的跨进程互斥（锁文件锚定在共享 SQLite 数据库旁），避免 API 与 CLI 或多个 uvicorn worker 并发跑同一天分析产生重复报告/通知；另一进程已持锁时本进程跳过并记录 `analysis_running_elsewhere`
- [改进] 定时分析失败可观测与限次重试：运行时调度器记录 `last_failed_at` 与连续失败次数并暴露到 `/scheduler/status`，整轮失败不再被静默吞掉；失败后最多重试 3 次（间隔 5 分钟，成功即清零计数），防止单次故障静默丢当日报告
- [改进] Web 前端整体改造为液态玻璃（Liquid Glass）风格：半透明玻璃承载层、背景光斑、顶部镜面高光；暗/亮双主题同步；内容层保持可读
- [修复] 首页实时大盘复盘卡片恢复「分享图片 / 运行流」入口：`HomeReportRegion` 渲染 `MarketReviewReportView` 时未传 `recordId` 与 `onOpenRunFlow`，而分享按钮在 `recordId` 缺失时直接返回 null，导致这两个按钮静默消失；现补传落库记录 ID（复盘任务完成后从最新历史取回）与运行流回调，并把「正文 + 结构化载荷 + 记录 ID」收敛为同一套清理逻辑，避免只清一半再次丢按钮
- [修复] 分享图片失败原因不再被通用文案覆盖：根因是分享接口用 `responseType: 'blob'`，axios 会把**失败响应**也解析成 `Blob`，响应拦截器在 `attachParsedApiError` 时按 Blob 解析、把错误归因缓存成「代理、DNS 或出网配置」并永久固化，服务端真实原因（如未安装 `wkhtmltoimage`）被吞掉；现拦截器在归因前先把 Blob 错误体还原成 JSON / 纯文本，按钮文案保持动作语义（「重试」）、服务端原因追加到悬停提示上；同时修正 502/503 归因——仅当响应体是网关 HTML / 空体时才归因为出网配置，服务端返回结构化错误体时原样透出真实原因
- [修复] K 线走势卡价格轴刻度出现二进制浮点噪声：`domain` 由 `[最低价 - pad, 最高价 + pad]` 计算，pad 为浮点数，未格式化时会渲染成 `21.840999999999998` 这类刻度；现统一收敛到 2 位小数
- [修复] 移动端顶部下拉菜单无法用 Escape 或点击外部关闭：此前仅靠 `mouseleave` 关闭，触屏没有悬停事件、键盘进入菜单项后也退不出；现菜单打开期间支持 Escape（关闭并把焦点交回汉堡按钮）与外部 `pointerdown` 关闭
- [改进] 首页删除单只股票分析记录前增加确认：此前点击删除即刻清空该代码的全部分析历史且不可恢复，现弹确认框说明将删除的范围，取消不发请求；`ConfirmDialog` 补 `role="dialog"` / `aria-modal` 提升可访问性
- [修复] 首页策略菜单与移动端菜单关闭后键盘焦点丢失：菜单项被 Enter/Space 激活后菜单卸载、焦点掉到 body，现把焦点交回触发按钮
- [改进] 「AI 建议」页两个「刷新」按钮可访问名区分：信号列表刷新与 Skill 表现刷新可见文案同为「刷新」，屏幕阅读器与语音控制无法分辨；现给 Skill 表现刷新补 `aria-label`（保留可见文案「刷新」以符合 WCAG 2.5.3 标签一致）
- [改进] 「AI 建议」页 Skill 表现卡改用独立空态文案：此前与复盘统计卡共用「暂无已复盘样本 / 当前已有 AI 建议时…」，两卡相邻时同一句话重复出现、也分不清是哪份样本缺失；现改为「暂无 Skill 表现样本 / Skill 意见已产生时，也可能还没有形成可统计的后验评估结果。」，新键 `decisionSignals.skillStatsEmptyTitle` / `skillStatsEmptyDescription` 中英双语
- [测试] 对齐 dsa-web 前端测试与现有功能：`DecisionSignalsPage` 9 个、`SettingsPage` 2 个用例在干净 HEAD 即失败，均属测试漂移而非功能缺陷；修复三处根因——mock 工厂漏挂 `getSkillOutcomeStats` / `runSkillOutcomes` 导致挂载即渲染常驻错误态、`clearAllMocks` 不清理残留的 `mockResolvedValueOnce` 队列让用例间相互污染、通知投递卡片新增诊断提示后「诊断提示数量」断言未同步；上述 11 个失败已全部转绿（另新增 1 个 Skill 表现空态用例）
- [修复] 首页大盘复盘「复盘摘要」卡显示的是报告标题：`_summarize_market_review` 取正文第一行作摘要，而首行就是 Markdown 标题（如「## 2026-09-12 大盘复盘」），于是摘要与记录名一字不差、零信息量；现摘要跳过所有 Markdown 标题行只取第一段正文，回退文案不变
- [修复] 首页大盘复盘「摘要」区不再渲染三张永远空态的卡片：市场情绪 / 轮动与资金 / 风险与观察对大盘复盘没有数据来源（`sentiment_score` 是常量 50、其余是「查看复盘」「大盘复盘」占位串），移动端四张全宽卡约占 700px 却零信息量；现只保留有真实数据的「复盘摘要」一张卡，删掉 `MarketReviewReportView` 中对应文案键与渲染分支
- [修复] 大盘复盘正文首段标题不再是硬编码英文 `Overview` / `Review`：这两个占位名生成在语言无关的 `_split_report_sections` 里，中文报告的第一个折叠段会显示英文标题；现按报告语言本地化（zh/en/ko），段落 `key`（`overview` / `full_review`）作为 API 契约保持不变
- [修复] 30 秒后台刷新不再把首页打进加载假态：`refreshStockBar()` 无条件置 `isLoadingStockBar`，而生命周期每 30 秒与每次切回前台都会调用它，导致所有自选行的「今日覆盖」瞬间归零、行内出现 spinner、点行只弹「最新详情加载中」而不打开报告（常驻性「点了没反应」）；现后台刷新走 `silent` 分支不置该标志（与同 interval 的 `refreshHistory(true)` 对齐），失败仍置 `stockBarRefreshFailed` 轻提示
- [修复] 自选列表的增删改不再静默吞错：`refreshCodes` 失败无任何反馈（表现为刷新按钮点了没反应）、`onSwitchList` 先改激活项再拉数据失败后停在「新列表名 + 上一个列表的内容」、`onCreateList` 乐观写入后失败仍提示「已创建」；现三条路径都返回成功与否，失败时回滚激活列表与乐观写入的列表项并提示，新建列表改用站内 Dialog 呈现字段级错误（重名、空名）
- [修复] 「新建自选列表」不再用 `window.prompt`：Electron 渲染进程不实现该 API，桌面端点「新建」无反应也无提示，Web 端也脱离设计系统、无重名校验；现改为站内 `Dialog` + `Input`，提交期间禁用、失败在表单内提示
- [修复] 首页错误反馈不再自相矛盾：此前失败反馈有两处反向问题——后台静默刷新（30 秒定时 / 回到前台）失败也会写全局 `error`，用户没做任何操作却在首页顶部看到粘性红条；而成功路径不清 `error`，红条会一直挂到手动关闭。现 `fetchHistory` / `fetchMarketReviewHistory` 仅在非静默失败时写 `error`，并给这两条路径的失败打上来源标记（`errorScope`），同一路径重新取数成功就收回自己留下的红条——包括 30 秒静默刷新的成功，因为「无法连接到本地服务」是对当前状态的描述，后端恢复后不该继续挂着。分析失败 / 任务失败 / 报告详情失败等一次性事件不打标记，不会被别的请求顺手抹掉
- [修复] 个股记录列表请求失败不再伪装成「暂无记录」：历史 tab 此前只消费 `stockBarRefreshFailed` 的部分反馈，无缓存时请求失败会落到空态，用户会以为历史被清空；现缓存为空且上次刷新失败时展示独立错误态（`stockBar.errorTitle` / `stockBar.errorDescription`，中英双语）
- [修复] 报告资讯列表的竞态覆盖：`ReportNews` 只用 `recordId` 闭包、无请求序号守卫，快速从报告 A 切到 B 时 A 的迟到响应会把 B 的资讯覆盖掉（标题已是 B、列表还是 A）；现加记录级请求令牌，响应返回时校验令牌、切换即作废在途请求
- [改进] 首页空态不再同步下载整个 Markdown 渲染栈：`HomeReportRegion` 静态 import `MarketReviewReportView`，一路带出 `ReportMarkdownBody` → react-markdown + remark-gfm（约 148K / gzip 44K），而复盘正文只在真有复盘报告时渲染；现与 `ReportSummary` 一样改 `React.lazy` + `Suspense`，构建产物中已独立成 `MarketReviewReportView` chunk
- [改进] 首页大盘复盘进度与结果卡可跨页面恢复：复盘状态（notice / 正文 / 载荷 / 记录 ID）与轮询此前只存在于触发它的那次交互里，切走页面或刷新浏览器后任务仍在跑、首页却什么都不显示且不再恢复；现 store 保存在途大盘复盘任务 ID（派生字符串，不订阅整个 `activeTasks`），首页挂载时接回轮询
- [改进] 修复「大盘复盘任务」判定用错字段：`refreshHistoryForCompletedTask` 与首页任务完成回调都用 `task.reportType === 'market_review'` 判断，而 `trigger_market_review` 提交任务时未传 `report_type`、实际取值是 `detailed`，判定永远不成立，还会把 `market_review` 当股票代码塞进待补选队列；现统一走 `isMarketReviewTask`（看任务 `stock_code`，并兼容未来显式传 `report_type`）
- [改进] 「名称与代码指向同一标的」时不再重复展示代码：场外基金只回代码做名称（如 001052）、大盘复盘伪标的为 MARKET，卡片标题、meta、分享标题与 `aria-label` 会把同一串念/显示两遍；现统一走 `isStockCodeRedundantWithName` 判定，覆盖个股栏、历史列表、自选/今日卡片、报告概览与 Markdown 面板、任务面板、模拟盘记录、基金指标卡、筛选候选、决策信号、回测导出与问股追问等展示路径，仅当名称确有区分信息时才并列代码
- [改进] 补全展开态与异步反馈的可访问性：`Collapsible` 与 `ReportDetails` 两处折叠按钮只靠箭头旋转表达展开态，现补 `aria-expanded`（与仓库其他折叠组件一致）；批量分析提交结果由裸 `div` 改为 `role="status"`，读屏用户能拿到提交结果
- [改进] 区域选择器可访问名回归可见文案（WCAG 2.5.3 Label in Name）：`MarketReviewRegionSelector` 触发按钮的 `aria-label`（「选择大盘复盘市场」）覆盖了可见文案（「A 股 + 港股」/「服务器默认」），语音控制用户按可见文字无法激活该控件；现移除该 `aria-label`（菜单 `role="dialog"` 的 `aria-label` 保留），与同页策略按钮一致
- [改进] 股票代码归一化收敛为单一实现：`HomePage`、`HomeStockWorkspace`、`stockPoolStore` 各有一份逐字相同的私有 `stockCodeKey`（注释还写着「与 HomePage 保持一致」），任一份改了规则都会让任务匹配与待补齐历史 key 静默错配；现统一到 `utils/stockCode.ts` 的 `stockCodeKey`，选中判定也收敛到 `areStockCodesEquivalent`（此前 `StockBar` 用裸 `===`，带交易所前后缀的代码会漏判高亮）
- [改进] 首页取数去重与可取消：自选历史补齐的 effect 依赖 `canLookupWatchlistHistory`，每次后台刷新该值会抖动一次并 abort 后对全部代码重发一轮请求；现按「刷新版本 + 待补齐签名」去重，只记录**已完成**的那一轮（在途请求被取消时不留标记，下一轮正常重来），并在待补齐集合清空时重置标记；「今日分析」分页查询带 `AbortSignal`（切走标签页即中断在途翻页，此前只拦住 `setState`、请求仍会翻到当日全量），同一日期 60 秒内复用上次结果
- [测试] 同步 dsa-web 前端测试到新契约：`MarketReviewRegionSelector` 4 个用例改按可见文案查询触发按钮（顺带锁住 Label in Name 契约）、`MarketReviewReportView` 改为断言三张已删除的空态卡片不再渲染、`HomePage` 今日榜用例的 `historyApi.getList` 断言补上 `AbortSignal` 参数，并为 `isStockCodeRedundantWithName` 补 4 个用例（场外基金/大盘复盘伪标的、带交易所前后缀、空名称保底）、为「按来源回收红条（静默成功同样生效）/ 别处来源的红条不被顺手抹掉」补 3 个 store 用例；本批次全量 `npx vitest run` 112 文件 / 1198 用例通过（`tsc --noEmit`、`eslint`、`vite build` 均通过）
- [修复] 选股市场闸门不再宣称支持实际没有策略支持的市场：`screen()` 此前硬编码接受 `("cn", "us")`，于是 `market="us"` 先拿到「supported: cn, us」，紧接着又被策略级校验拒掉（内置 10 个策略的 `market_scope` 均为 `[cn]`）；现改为用已加载策略 `market_scope` 的并集校验，报错文案列出真实支持的市场集合，`market="cn"` 行为不变
- [修复] 模拟盘消费决策信号失败从 debug 提到 warning 并补上堆栈：`_try_consume_paper_signal()` 的异常此前只记 `logger.debug`，默认日志级别下完全不可见，与同一文件里抽取失败用 `logger.warning` 的可见性不对等；现升到 `logger.warning` 并加 `exc_info=True`——这条 best-effort 路径只在失败时触发，而 `error=%s` 只给异常文本、看不到调用链，而「为什么这条信号没被消费掉」恰恰需要堆栈；best-effort 语义（消费失败不中断主流程）不变
- [文档] 修正 `docs/screening-engine.md` 关于美股选股能力的表述：原文把「美股」列进「原始数据与选股能力均已纳入」，实际仓库只有美股数据适配器（`snapshot_us.py`）、内置策略 `market_scope` 均为 `[cn]`，默认没有任何策略支持美股选股
- [新功能] 场外基金报告补上 LLM 解读层：`fund:<code>` 此前只有确定性「净值体检」骨架（`src/services/fund_analysis.py` 的模块注释原文即为「无需 LLM 的确定性结论:报告骨架;LLM 层作为增强由下游 Prompt 任务补充」），本次把这层补上。`GeminiAnalyzer.FUND_SYSTEM_PROMPT` + `_get_fund_system_prompt()` 与股票 `_get_analysis_system_prompt` 同构（中文基线 + `Output Language` 段落，`zh`/`en`/`ko` 三语），`run_fund_analysis()` 复用既有 LLM 骨架（模型 fallback、JSON 抽取），`enrich_fund_report_with_llm()` 产出解读并挂到 `dashboard.llm`，Web 端在既有 `FundMetricsCard` 内新增「AI 解读」区块（单一渲染器，不新增视图）。**契约只收解读、不收事实**：`FundReportSchema` 仅 5 个字段（`holdings_concentration` / `analysis_summary` / `operation_advice` / `risk_warning` / `sentiment_score`），净值/收益/回撤/波动/夏普/持仓/配置仍由确定性层持有——`manager`/`scale`/`inception_date` 因 `FundProfile` 不提供而不索取（问了即诱导模型凭记忆编造），`max_drawdown`/`top_holdings` 因事实层已有而不再索取（复述会在同一张卡片上产生第二个可能矛盾的数据源）。字段清单共用 `FUND_REPORT_FIELDS`，prompt 与 schema 无法各自漂移（`tests/test_fund_llm_prompt.py` 守卫），prompt 明确排除涨跌停/龙虎榜/北向资金/筹码等股票概念与买卖档措辞（统一为申赎）。**降级语义明确且有记录**：未配置 LLM、所有模型失败、返回内容违反契约、返回全空字段，一律记 warning 并让增强层整体缺席，确定性报告照常产出（`AGENTS.md` §7）；`AnalysisResult.sentiment_score` 保持确定性 50 不采用模型分数（该字段是下游摘要排序依据，用随调用波动的分数排序会让同一条记录在不同运行间跳动），模型分数仅展示在解读区块。**无新增配置项**（开关即「是否配置 LLM」，避免叠加互斥开关）、**无 schema 变更、无数据迁移**。新增 `tests/test_fund_llm_prompt.py`（14 例）、`tests/test_fund_llm_pipeline.py`（3 例，覆盖增强层挂载与两条降级路径）、`apps/dsa-web/src/components/report/__tests__/FundMetricsCard.test.tsx`（7 例），并扩充 `tests/test_fund_analysis.py`、`tests/test_fund_report_schema.py`、`ReportSummary.test.tsx`
- [文档] 校正 `docs/superpowers/specs/2026-08-26-open-fund-report-design.md`：该设计文档状态为「待实现」，但其正文与实际落地代码在四处相互矛盾——§1 要求 `.FUND`/`.OTC` **后缀**识别而代码实现的是 `fund:` **前缀**、§2 要求新增 `FundDataProvider`(akshare) 而代码落在 `data_provider/fund_fetcher.py`(东财)、§8 要求新增 `FUND_SUPPORT` 开关而代码没有该开关、报告渲染要求新增独立视图而代码扩展的是既有 `FundMetricsCard`。按 `AGENTS.md` §5.6「文档与代码不一致时以实际代码为准」，正文改写为 as-built 描述并标记为已实现，原决策与取代原因集中记录在新增的「与原稿的差异」表中（含 LLM 契约从 14 字段收敛到 5 字段的理由），避免后来者按已废弃的原稿实现；`docs/market-support.md` 的「场外基金（净值体检）」一节补记 LLM 解读层的开关、契约字段、降级语义、排序字段取值理由与三语行为
- [修复] 场外基金净值序列的顺序契约在数据源边界归一化：东财 `lsjz` 按净值日期**倒序**返回，而 `compute_metrics` / `build_fund_report` / `build_fund_llm_user_prompt` 全部按「列表末尾是最新一条」取数，此前从未有代码把顺序摆正，于是基金卡片上的事实是错的——以 `fund:003095` 实测，卡片「单位净值」显示 2.0866（2025-09-09，窗口内**最旧**的一条）而当日实际净值是 1.9645；「近 1 月」显示 +9.2% 而真实最近一月是 **-5.4%**（方向相反）、「近 6 月」显示 +24.8% 而实际 +14.9%、最大回撤在时间倒序的路径上算成 -30.3%（实际 -30.2%）。`FundFetcher._fetch_nav` 现统一返回**按净值日期正序**的序列（先排序再取末尾 `limit` 条，保证截断留下的是最近若干交易日；接口若历史上改为正序返回也不会被倒过来——`get_latest_nav` 的 docstring 本就注明「不依赖接口返回顺序，兼容新旧两种排法」，说明该接口的排法本就不稳定）。顺带修掉一个结构性空值：默认窗口 `history_len=250` 小于 `TRADING_DAYS=252`，而区间收益判据是 `n <= days → None`，因此「近 1 年」在任何默认调用下**永远**是空值、「走势」也因此恒为「震荡」（上行需要 `return_1y > 0` 成立，`None` 时取 0）；默认窗口现改为 `TRADING_DAYS + 1`，`return_1y` 与夏普（依赖 `return_1y`）随之可用。既有用例之所以没发现，是因为它们喂给 `compute_metrics` 的都是自造的正序序列——正好绕开了真实取数路径。新增 4 例守这个边界（倒序载荷归一化、截断保留最近若干条、正序载荷不被倒转、默认窗口可算满一年），并在报告层补 2 例断言 `latest_nav` 与喂给模型的「最新净值」都取末尾那条。**无 schema 变更、无新增配置**；`docs/superpowers/specs/2026-08-26-open-fund-report-design.md` §2 补记该顺序契约
- [改进] 分析基金不再空跑一遍股票数据管道：`process_single_stock` 此前对**所有**代码无条件先执行 `fetch_and_save_stock_data`，而 `analyze_stock` 的基金分支自己用 `FundFetcher` 取净值、根本不消费这步的数据。于是一次 `fund:<code>` 分析会先把各股票数据源（含实时行情/K 线/筹码等分支）的失败路径挨个走一遍再落库，白等几十秒并刷一屏 ERROR 级日志，对报告内容零贡献。现 `is_fund_code(code)` 为真时跳该步骤（进度文案改为「基金净值准备完成」），股票链路逐字不变。新增 2 例（基金不调 `fetch_and_save_stock_data`、股票仍恰好调一次）

## [3.29.0] - 2026-08-02

### 发布亮点

- feat: 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，新增选股运行历史、数据源历史和候选深度分析链路。
- feat: 新增 1080px 个股决策卡与高密度市场复盘分享图，支持 Web 原生分享和下载回退。
- feat: 新增 Skill Opinion Outcome 计算、表现统计与基于真实样本的有界运行时权重。
- improve: 优化选股快照复用、热点按需加载、多源并发和候选轮换，缩短长流程等待并提升结果多样性。
- fix: 加固关闭认证、短凭证诊断脱敏、CI 超时取证和桌面冻结包启动链路。
- fix: 修复 Longbridge 量比、股票代码窗口解析、选股后置重排及分享图交互等稳定性问题。

### 新功能

- SkillAggregator 基于独立满足 30 条 evaluated 门槛的真实 Skill Outcome bucket，使用 Beta 先验收缩、unable 惩罚和多周期证据加权生成有界运行时权重；缺失、低样本或异常统计保持中性。
- 选股结果按 `run_id` 持久化到 DSA 数据库，新增运行历史和数据源历史 API，接入公告事件上下文及其搜索缓存，并支持将候选连同筛选策略映射的 skill 交给单股深度分析。
- 新增按 skill、horizon 与 outcome engine version 独立聚合的只读 Skill Opinion 表现统计；少于 30 条 evaluated 样本时仅返回观察性计数，不输出表现指标或调整运行时权重。
- 新增按 individual SkillAgent 自身 signal、版本化 engine 与本地已存同源日线窗口计算并持久化 `skill_opinion_outcomes` 的核心服务。

### 改进

- 选中热点后先展示榜单已有摘要和核心股，后台再补充完整详情，并将单次热点源等待上限收紧为 8 秒。
- 热点成分股并行获取东方财富与同花顺数据，并按固定数据源优先级合并；真实供应商调用增加限流、可终止 timeout、并发槽和 worker 回收，题材详情可按需复用 DSA 原生搜索服务补充安全且带链接的近期消息。
- 精简选股页面的重复说明，将任务标识、快照统计和排序诊断折叠到运行详情。
- 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，统一使用 `ScreeningService`、`SCREENING_ENABLED` 和 `/api/v1/screening`，并保留 Apache-2.0 归因与来源版本记录。
- Web 选股使用浏览器匿名种子与运行 ID 在最终评分后的有界近分池中生成每次运行的候选组合；本地评分覆盖完整短名单，远程分析继续遵守数量上限，硬过滤、风险否决和得分保持不变。
- 热点榜单刷新与选股长流程解除双向串行等待，热点详情改为选中后按需加载；选股默认复用 5 分钟内且数据源优先级一致的成功全市场快照，并展示快照、候选上下文、LLM 重排、最终评分和新闻事件增强阶段。
- 图片报告改用独立的 1080px 个股决策卡和高密度市场复盘卡，优先从结构化 payload 精确填充数据并保留 Markdown 回退；小红书账号与二维码支持关闭或替换，Web 支持原生分享与下载回退。

### 修复

- Web 分享图在按需生成完成后通过第二次用户点击同步打开系统分享，避免首次异步生成使原生分享退化为下载。
- Web 分享图改为用户点击“分享”后才按需生成，不再在报告加载时自动请求。
- 移除基础设置选股卡片中仍跳转到“数据源”的过期“查看配置项”按钮，并将 `SCREENING_ENABLED` 及 Web 选股功能开关归入“基础设置”。
- `scripts/ci_gate.sh` 的离线测试增加单测 timeout 与 faulthandler 取证，并同步 Docker 发布流程的 CI 依赖，避免无 traceback 卡住或发布门禁缺少 `pytest-timeout`。
- 选股策略栏稳定展示完整中文策略列表，并保留自定义策略 ID 入口。
- 选股热点详情统一使用中文业务文案，不再显示内部类名、字段名和原始数据源错误。
- 刷新热点榜单并保留当前题材时同步绕过详情缓存重拉该题材，避免新榜单继续搭配旧路线与成分股。
- 选股尾部轮换以分析器输入顺序为权威并保留并列分候选顺序；热点消息增强、共享缓存 owner、全局并发容量和新闻搜索 deadline 统一收敛。
- Outcome 候选按上次尝试时间公平调度，避免持续新增的缺失 key 使旧 `pending` outcome 永久得不到重试。
- 选股主模型返回空内容、非 JSON 或低覆盖结构时继续尝试备用模型；全部失败时明确展示确定性因子排序状态，且不把 `reasoning_content` 当作最终结果。
- 选股日线增强改用请求级 DSA-first fetcher 注入，多个后置分析器按最新分数逐级重排，远程分析状态跟随实际提交候选，避免重叠请求泄漏 wrapper 或改写未提交候选。
- 统一等价股票代码的本地日线候选与同源窗口解析；冲突沪深交易所代码不再降级匹配裸码，回测仅接受快照或交易日历确认的起点。
- 关闭认证时强制再次校验当前管理员密码，命中 rate limit 时返回 429，前端在当前密码缺失时阻止提交并显示内联提示（#1970）。
- 本地 CLI 的 `stdout_preview` / `stderr_preview` 按环境变量、JSON、YAML/日志标量与 URL 的独立契约脱敏短凭证，避免 API key、secret 或 token 进入诊断（refs #1784）。
- PyInstaller 冻结包在 NLTK 3.10 导入保护下误判内置 `_internal` 标准库时不再启动失败；Windows/macOS 打包脚本统一接入兼容 runtime hook。
- 分享图按字段合并历史结构化数据与 Markdown，多市场逐区域复用持久化 payload，隐藏不可用市场灯号维度并保留配色方案；中英韩模板跟随报告语言，原生分享失败时自动回退下载。
- 飞书文件报告在写入或上传前清理隐藏的市场 metadata；桌面运行时默认隐藏未随包提供 renderer 的 Web 分享按钮。
- `redact_diagnostic_text()` 的命令替换扫描不再吞掉尾随非敏感诊断字段，并统一 `export FOO=$(...)` 与 `FOO=$(...)` 的脱敏行为。
- Longbridge 量比改用 adaptive keyword args 调用 `history_candlesticks_by_offset`，兼容 0.2.74 与 4.x SDK 参数顺序（fixes #2100）。

## [3.28.0] - 2026-07-26

### 发布亮点

- feat: Multi-Agent 多策略综合支持分层 deliberation、mediator/self-review、revision projection 与 multi-round，并统一最终动作和解释契约。
- feat: AI 建议页新增按决策风格分组的历史表现，specialist opinion 样本可持久化并用于后验评估。
- feat: 新增 `--portfolio futu`，可只读导入 Futu OpenD 真实账户的沪深 A 股、港股和美股 LONG 正股持仓。
- feat: Web 首页与 API 支持按单个或多个市场临时触发大盘复盘，不修改全局配置。
- feat: Tushare 支持通过 `TUSHARE_HTTP_URL` 接入自建网关或兼容镜像。
- fix: 改进港股行情路由与缓存、外股英文新闻匹配、数据源兜底顺序及桌面端发包稳定性。

### 新功能

- Multi-Agent 多策略综合新增受控 deliberation v0、可注入 mediator/self-review v1-v2、只读 revision projection v3 与 multi-round v4；增强层相对上一层 baseline 只能保持或继续 softened，不覆盖权威最终信号。
- `specialist` 模式最多选择 4 个策略专家，并通过 `AGENT_SKILL_CONCURRENCY` 控制 1–4 个 worker 并发；worker 继承主管线冻结的 target date 等上下文，单个 skill 失败不阻断其它策略或最终决策。
- Multi-Agent 报告按八态用户 action 追踪 Pipeline 最终调整，排除非法 Agent 意见；仅在 canonical action 可唯一解析时生成 explanation 与 DecisionSignal，并以同一个 `final_action` 统一最终动作契约。
- specialist 在分析历史保存成功后持久化版本化、低敏且幂等的有效 opinion 样本，为后续后验评估提供真实数据；本阶段不计算 outcome、不统计表现、不调整权重。
- AI 建议页新增决策风格历史表现，按每个分组独立的 30 个已完成样本门槛展示命中、区间涨跌、无法评估和最大不利波动，并保持旧统计接口兼容。
- 新增 `--portfolio futu`，只读导入 Futu OpenD 真实账户的沪深 A 股、港股和美股 LONG 正股持仓作为分析列表。
- Web 首页与 `POST /api/v1/analysis/market-review` 支持用严格校验的 `region` 临时选择单个或多个复盘市场；一次性覆盖不读写全局配置，并贯穿任务提交、状态、SSE、结果与历史记录。
- Tushare 数据源支持通过 `TUSHARE_HTTP_URL` 自定义接入地址；留空时继续使用官方默认地址（fixes #1985）。

### 改进

- 暂停 PR Review 的自动触发，仅保留 `workflow_dispatch` 手动入口，避免辅助评审重复运行及评论权限失败产生误导性红灯；正式 CI 检查保持不变。
- `.env.example` 与每日分析 workflow 同步映射 `TUSHARE_HTTP_URL`，保持本地和云端配置入口一致。

### 修复

- 修复外股代码映射到中文显示名时英文新闻相关性漏判，统一外股代码、英文名和别名解析，并对展开后的检索词去重（fixes #2026）。
- 特权 `pull_request_target` 流程不再检出 fork PR head；敏感步骤仅执行主分支可信脚本，PR 元数据与 diff 通过 GitHub API 读取（fixes #2051）。
- PR Review 事件载荷缺失、不可读或 JSON 非法时输出可定位且不泄露载荷的警告，并保留原有降级行为（fixes #2070）。
- 修复 Windows 上 `mimetypes` 冷启动读取注册表导致进程卡死的问题。
- 统一 `DataFetcherManager`、AkShare 与 Longbridge 对 4–5 位裸港股码的识别，避免 4 位代码被错误路由或静默失败（fixes #2091）。
- AkShare 港股实时行情增加 20 分钟全市场缓存与并发冷启动 single-flight，热缓存命中不再等待网络限速，主接口异常时仍保留新浪备用接口降级（refs #1852）。
- 将 `TencentFetcher` 默认优先级调整为 A 股日 K 数据源的最终兜底，并新增 `TENCENT_PRIORITY` 显式覆盖项（refs #2032）。
- Web 设置页和通知测试入口补齐普通钉钉群机器人配置，支持安全遮罩保存 webhook 与 secret、查看帮助并发送测试通知（refs #1957）。
- Agent Chat 普通与流式接口在请求未指定 `report_language` 时继承全局 `REPORT_LANGUAGE`，显式请求值仍优先。
- WebUI 分开展示发布版本、代码版本与构建时间，并用构建输入摘要避免复用时间戳未变化的旧静态资源（fixes #2093）。
- macOS unsigned 打包显式禁用 Electron 签名与 Hardened Runtime，在冻结后端和 electron-builder 阶段清理残缺签名，并审计原始应用与 DMG 产物；该缓解不替代 Apple Developer 签名与公证（refs #2075）。

### 文档

- 修复文档中的失效相对链接。
- [修复] #2026 外股代码映射到中文显示名时英文新闻相关性判定漏判：新增同源 STOCK_ENGLISH_NAME_MAP 单一真源、canonicalize_foreign_stock_code 规范化入口与 _foreign_english_query_terms 别名解析，使 AAPL/00700/BABA 等 ticker 即使 stock_name 为中文也能在查询构建、相关性打分与多维度情报路径上复用 canonical 英文名，并补齐 .US/.HK suffix / HK 前缀全形式的归类与回归用例；同时在 _score_news_relevance 对 alias 展开 term 做去重，避免 legal alias 展开短名与显式 short alias 重复计分。
- [新功能] Tushare 数据源支持通过 `TUSHARE_HTTP_URL` 环境变量自定义接入地址，便于网络无法直达 `api.tushare.pro` 时切换自建网关或第三方兼容镜像；留空保持官方默认地址不变（fixes #1985）
- [文档] `.env.example` 与 `.github/workflows/00-daily-analysis.yml` 同步映射 `TUSHARE_HTTP_URL`，避免出现"配置项有但 workflow 漏映射"的半修状态
- [修复] #2051 PR Review 的特权 `pull_request_target` 流程不再检出 fork PR head：敏感文件、标签、报告与 AI 审查统一通过 GitHub API 将 PR 元数据和 diff 作为数据读取，只执行主分支可信脚本；Python 语法、Flake8、确定性检查和离线测试继续由无 secrets 的 `pull_request` CI / `backend-gate` 执行，兼容 `actions/checkout` 新增的 fork checkout 安全保护。
- [修复] 修复 Windows 上 mimetypes 冷启动时读取注册表导致的进程卡死

## [3.27.0] - 2026-07-19

### 发布亮点

- feat: 新增 Codex App Server single-agent 问股实验原型，并保持 LiteLLM、Multi Agent、普通报告和定时任务等默认链路不变。
- feat: Web AI 建议页支持保存基于历史报告快照重算的决策风格信号，补齐去重、续期、失效和可审计 guardrail 语义。
- feat: 引入多策略观点结构化输出第一阶段契约，覆盖观点标准化、基础冲突检测、聚合元数据和报告兼容边界。
- improve: 报告页明确展示输入数据状态、来源、异常影响、处理建议和诊断码，并区分页面资讯与本次分析输入。
- fix: 修复 MiniMax 推理内容污染最终 JSON、字符串 `<think>` 包装兼容及多 Agent 风险覆盖后结论未按最终信号收敛的问题。
- fix: 补齐美股实时行情 PE/PB 估值字段、多市场工具描述和 macOS Gatekeeper 安装排障说明。

### 新功能

- 新增 #1743 Phase 6 Codex App Server single-agent 问股实验原型，仅开放三个既有只读 Tool Surface 工具；默认 LiteLLM、Multi Agent、Deep Research、普通报告、定时任务与 Phase 1/2 `codex_cli` 路径保持不变。
- Web AI 建议页支持确认保存基于历史报告快照重算的决策风格信号，以 created/existing/refreshed 区分新建、原样复用和既有记录续期或维度补齐，并复用 profile-aware 去重与失效语义。
- 多策略观点结构化输出第一版新增策略观点标准化、基础冲突检测与聚合 metadata，作为 #1964 的阶段性基础契约；本版本不声明完成并发执行、完整策略调度 MVP 或前端完整多语言展示。

### 改进

- Codex 设置页仅检查配置、命令和所需协议是否允许尝试，用户保存后可直接提问；Chat 以服务端 `accepted` 事件提交问题并按实际 backend 停止。
- Web 报告页输入数据块沿用状态、来源、告警和说明字段，在说明中补充异常影响、处理建议与诊断码，并区分报告页资讯和本次分析输入。
- 更新 Anspire 数据源的项目展示信息，并将 `get_stock_info` 工具说明从 A 股限定修正为覆盖 A 股、港股和美股。

### 修复

- 修复 MiniMax 分析与渠道 JSON 测试把推理内容和最终文本拼接后导致结果无法解析、无法持久化的问题；字符串响应仅剥离开头完整的 `<think>` 包装，并保留 JSON 内容中的同名字面标签。
- 修正多 Agent 内部 runtime facts 的 timeout 归因，并让 risk application 覆盖后的 dashboard 决策字段及一句话核心结论基于 post-risk signal 完成 finalization。
- 收敛多策略综合器语义：正确处理 Signal 枚举、缺失 signal、有效 opinion_count 和 deterministic synthesis，并兼容历史与外部 dashboard 的宽松字段形状。
- Codex 问股只接受 App Server 明确完成的终态回答，并统一整体时限、累计输出、事件、工具预算和进程回收边界。
- `codex_cli` 普通分析显式固定无人值守批准策略与只读沙箱，避免新版 Codex 在非交互任务中因请求人工批准而中断。
- yfinance 美股实时行情补齐 `pe_ratio` 和 `pb_ratio`，供估值分析和下游报告使用。

### 文档

- 补充 macOS 未签名、未公证 DMG 被 Gatekeeper 拦截时的架构选择、安全排查与官方安装包临时放行步骤。

## [3.26.1] - 2026-07-12

### 发布亮点

- feat: Web 首页新增历史、自选与今日工作区，支持批量分析、今日覆盖判断和评分排行。
- feat: 新增 A 股市场结构与题材主线上下文，并贯通报告、Agent、DecisionSignal 与 Web 展示。
- feat: 飞书支持文件形式推送报告，多 Agent 支持子 Agent 独立超时钳位。
- feat: 补齐内部 DSA Tool Surface、DecisionAgent 分歧摘要和 DecisionSignal profile 契约。
- fix: 统一报告动作口径，修复按股票代码批量删除历史记录和通知理由静默截断问题。
- fix: 改进 Web、桌面端、数据源缓存及发行包资源的稳定性。

### 新功能

- 新增 A 股市场结构与题材主线上下文，并在报告、Agent、DecisionSignal 和 Web 市场位置卡中复用。
- 飞书推送新增文件上传能力：`FeishuSender.send_feishu_file(file_path)` 通过 App Bot SDK (`im.v1.file.create`) 上传文件并发送文件消息；Webhook 模式回退为发送文件内容文本；新增 `FEISHU_SEND_AS_FILE=true` 配置开关，开启后飞书以文件形式发送报告而非文字消息。
- 多 Agent 编排 Pipeline 新增子 Agent 独立超时钳位：支持 6 个环境变量为 TechnicalAgent、IntelAgent、RiskAgent、DecisionAgent、PortfolioAgent、SkillAgent 各自配置独立硬上限，互不挤占配额；默认 0 表示关闭钳位。

### 改进

- 为 multi-agent DecisionAgent 增加内部低敏分歧摘要输入管线，作为 #1904 P1 解释输出的前置 plumbing；不改变 public API、dashboard schema 或最终解释字段。
- GitHub Actions 每日分析工作流补齐 TickFlow 数据源环境变量映射，并收敛 README 数据源稳定性说明到完整指南。
- Web 首页个股栏新增历史 / 自选 / 今日切换，保留历史分析默认视图，并支持在自选页一键分析全部或仅分析今日未覆盖股票、在今日页按评分查看当天分析排行；分块提交部分失败时保留已确认计数、停止后续提交并刷新任务列表。
- GitHub Actions 每日分析工作流新增钉钉通知环境变量映射，支持在云端定时任务中直接使用钉钉机器人。
- `STOCK_LIST` 自选股解析支持中文逗号、顿号、分号、空格和换行等常见粘贴分隔符，运行时、定时热刷新、CLI `--stocks`、Web 设置保存和自选 API 统一识别，并在写回时规范为英文逗号。
- 新增 `NEWS_INTEL_AUTO_FETCH_ENABLED` 单开关，开启后个股分析、Agent 分析和大盘复盘会 fail-open 自动初始化并刷新 RSS/Atom/NewsNow 本地资讯池。
- Web AI 建议页新增主股票上下文，复用最近分析和股票索引候选，并改进表现统计零样本说明。
- DecisionSignal 将 `decision_profile` 升级为正式 nullable 字段，统一 same-profile 查询、去重、续期和失效语义，并保持 create metadata `null` 兼容与 SQLite 幂等回填诊断。
- 设置页移动端分类导航改为横向滚动列表并保证设置内容首屏可见，桌面端保留分类说明并收紧字段布局层级与间距。
- 新增 #1743 Phase 6a 内部 DSA Tool Surface 契约，统一工具 schema、stock scope fail-closed guard、结构化错误、审计摘要和脱敏诊断边界，并明确外部 AgentBackend 工具能力仍需 wire-level probe 证明。
- `src/services/analysis_service.py` 在 `report` 详情层新增 `details.raw_result` 回填，补齐与 API/历史详情的报告载荷一致性；不改变 provider、model、Base URL 或配置迁移语义。

### 修复

- 按股票代码删除历史记录时分批清理全部匹配项，并拒绝空白代码，避免超过 10000 条后残留记录或无筛选删除。
- 市场结构概念排行为空或超时时复用本轮负结果，避免批量个股分析重复请求同一概念排行数据源。
- Windows/macOS 桌面后端打包显式收集并校验 AkShare `file_fold/calendar.json`，避免发行包因缺少交易日历 package data 导致热点题材和选股日线增强降级。
- 邮件、Telegram 与报告共享的 DecisionSignal 摘要完整展示已脱敏的理由，避免固定 120 字符在句中无提示截断；Telegram 按最终 Markdown payload 长度安全分片。
- 推送报告、Jinja 报告与历史 Markdown 导出复用 Web/API 的评分-action 口径：高分但旧 `operation_advice` 仍为持有且无降级原因时，建议文案与三类统计展示为买入；有明确 guardrail reason 时继续保留持有/观望。
- WebUI 启动时显式 `--host` / `--port` 不再被 `.env` 中的 `WEBUI_HOST` / `WEBUI_PORT` 覆盖，未传 CLI 参数时统一使用解析后的运行时配置。
- Web 首页今日状态与排行使用带时区偏移的历史时间戳和完整分页数据，在查询失败、跨服务器时区边界或任务完成刷新时保持安全且准确。
- Web 首页 stock bar 刷新序列化：并发或乱序返回时仅最新请求可清除 `stockBarRefreshFailed`，避免旧响应覆盖任务完成后的刷新结果。
- Web 持仓页首屏快照改用 `include_realtime=false` 快速估值，跳过逐票实时行情预取后先展示持仓列表，避免外部实时行情源变慢时长时间空白等待。
- 修复任务状态接口重建报告动作字段时把合法情绪分 `0` 当成空值的问题，确保低分报告能按评分口径纠正为卖出建议。
- 修复 Agent 流式回复在未收到完成事件就断开时被显示为“（无内容）”的问题，改为提示流式响应中断并保留用户消息。
- 修复桌面端 `WEBUI_HOST=*` / `WEBUI_HOST=[::]` 会被原样传给端口探测和后端启动导致无法监听的问题，启动前分别规范化为 `0.0.0.0` / `::`。

### 文档

- 在 README 快速开始中补充行情数据源配置说明（`TUSHARE_TOKEN` / Longbridge），明确未配置时仍可使用 AkShare、Baostock、YFinance 等免费兜底源，并同步中英文完整指南。

## [3.25.0] - 2026-07-03

### 发布亮点

- feat: 新增 `claude_code_cli`、`opencode_cli` generation-only 本地 CLI backend，并补齐生成后端状态诊断、预览、冒烟测试 API 和 Web 状态面板。
- feat: 台股报告完整接入三大法人资料，覆盖报告渲染、LLM prompt、TWD 币别标示、收盘集合竞价识别和 fetcher 韧性加固。
- feat: 新增钉钉群机器人通知、韩语报告输出和 AI 建议决策风格重评估预览。
- feat: Agent `/chat/stream` 标准化 progress event，新增阶段开始/完成、pipeline timeout 和预算跳过语义。
- fix: 修复桌面端 WebUI host/port 绑定、macOS Homebrew CLI PATH 诊断、Discord 长报告分片、AlphaSift 超时、yfinance 分红解析、A 股回测代码归一化等稳定性问题。

### 新功能

- 钉钉群机器人通知支持 `DINGTALK_WEBHOOK_URL` 和 `DINGTALK_SECRET`，并对长文本自动切片以适配 20KB 限制。
- 报告输出语言新增韩语（`REPORT_LANGUAGE=ko`），覆盖个股报告、大盘复盘、Prompt 输出语言、决策护栏、通知模板标签与 Web 报告详情页文案。
- 新增 `claude_code_cli` 与 `opencode_cli` generation-only 本地 CLI backend，保留 LiteLLM 默认路径、Agent 工具调用边界、per-preset extractor、最小 env allowlist 与结构化错误。
- 新增生成后端状态、预览和冒烟测试 API，以及 Web 生成后端状态面板，区分轻量检查与 JSON 冒烟测试，并保持本地 CLI “仅生成、不支持问股工具调用”的边界。
- Agent `/chat/stream` progress event 新增 `stage_start`、`stage_done`、`pipeline_timeout`、`pipeline_budget_skipped`，补齐阶段进度、超时和预算跳过语义。
- 台股个股报告的 institution 区块展示 TWSE T86 / TPEx 三大法人原始买卖超净额，并将三大法人净买卖超表格注入 LLM 分析 prompt 作为台股筹码过滤器。
- 新增 AI 建议决策风格重评估预览接口与页面预览。

### 改进

- 台股三大法人 fetcher 增加并发缓存防击穿、TWSE/TPEx 分市场熔断、TPEx 日期保护和剩余 stage 预算复用，降低限流、端点故障和冷抓取超时带来的降级概率。
- AlphaSift 默认依赖 pin 更新到 `9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf`，接入 wrapper 数据源 caller-side timeout、东财直连限速/抖动、策略目录元数据和防守策略。
- 选股任务状态轮询遇到可恢复超时时提示后台任务仍会自动重试，`.env.example` 补充相关超时调优项。
- 收敛个股分析评分与 DecisionSignal action 口径，统一 80/60/40/20 分段，并在风控降级时记录 raw/adjusted score、final action 与原因。
- Web 设置页左侧分类切换时仅在相关分类展示首次启动检查和 AlphaSift 辅助卡片，减少跨分类残留。

### 修复

- 修复 Windows 桌面端启动后端时固定传入 `--host 127.0.0.1` 导致 `.env` 中 `WEBUI_HOST=0.0.0.0` 不生效、局域网无法访问 WebUI 的问题；桌面端仍默认使用 `127.0.0.1`，仅在显式配置 `WEBUI_HOST` 后按配置绑定。
- 修复桌面端启动时 `.env` 中 `WEBUI_PORT` 与 Electron 自动选择端口不一致，导致窗口继续等待旧端口并连接超时的问题。
- 修复 macOS 桌面端从 Finder/Dock 启动时后端 PATH 看不到 Homebrew Codex CLI 的问题，并明确 Codex CLI 主分析与 Agent LiteLLM 工具调用分流诊断。
- 修复 Discord 长报告推送按 2000 字符上限分片逐段发送，遇到 429 限流会按 `retry_after`/`Retry-After` 有限重试，避免中途失败后只收到前半段报告。
- 修复日股、韩股和台股 `market_phase` 收盘集合竞价识别，避免临近收盘阶段仍被标记为普通 `intraday`。
- 修复 A 股个股分析遇到空 `belong_boards` 占位时不会继续补查所属板块、关联板块模块展示不稳定的问题。
- 修复大盘复盘在 LLM 标题漂移或正文缺少板块段时，Web 与推送报告偶发缺少板块主线的问题。
- 修复 Web 大盘复盘结构化数据成交额、指数点位、涨跌幅和高/低值格式化，避免浮点长尾或缺失值 `0.00` 直接展示。
- 修复 Web 首页个股栏在 stock-bar 摘要字段缺失或动作建议无法归类时隐藏情绪分与建议标识的问题。
- 修复 Web 设置页定时任务“立即执行一次”后台线程未传 `stock_codes` 导致任务崩溃的问题。
- 修复 `opencode_cli` 静态指令，避免全局 JSON-only 约束影响 `generate_text()` 与大盘复盘自由文本输出。
- 修复 yfinance 1.2.x 将 `Ticker.dividends` 返回为单列 DataFrame 时分红解析被丢弃的问题，恢复 TTM 每股分红与分红次数计算。
- 修复台股财务金额币别标示，将 TWD 金额标注为“新台币”，避免在 A 股语境下误读为人民币。
- 修复回测日线补全将 `605066.SH`、`SS605066`、`SS.605066` 等 A 股等价代码误向数据源请求 `SS605066`，导致回测数据不足的问题。

### 文档

- 新增 Agent `/chat/stream` progress event 契约文档，说明新增事件字段语义、Web 兼容边界、验证方式和回滚方式。
- 同步本地 CLI backend 隐私/部署边界，明确 local CLI 不是离线模型，Docker/CI/远端需自行安装登录，DSA 不读取 Claude/OpenCode credential 文件。
- 更新 README 三语入口和市场支持边界，说明台股 `.TW` / `.TWO`、三大法人报告区块、TWD 标注与收盘竞价识别能力边界。

### 测试

- 台股三大法人 fetcher 新增 live-smoke 脚本与 `@pytest.mark.network` 漂移检测测试，用于非阻断 network-smoke 定时任务核对 TWSE T86 / TPEx 核心字段与解析结果。

## [3.24.1] - 2026-06-28

### 修复

- 修正 Longbridge SDK 版本约束为按平台选择可安装版本，避免桌面与 Docker 发布在 `pip install -r requirements.txt` 时因不存在的 `0.2.75` 版本失败。

## [3.24.0] - 2026-06-28

### 发布亮点

- feat: 扩展台股、日股、韩股市场支持，覆盖台股 suffix-only 分析、台股三大法人资料层、JP/KR 大盘复盘和跨服务市场枚举。
- feat: 新增 GenerationBackend 抽象、`codex_cli` 本地 CLI backend、reserved Hermes 本地 HTTP 渠道和 prompt cache capability registry。
- feat: Web/API/Desktop 支持多时间定时推送与 runtime scheduler 热重建，Web 设置页补齐首次启动检查与定时任务面板。
- feat: 报告链路补齐信号归因、单股信号时间线、概念板块排行和通知/报告关联板块展示。
- fix: 修复 Docker/启动探针、静态资源 MIME、回测空结果、组合估值、通知 Markdown、AlphaSift 数据源和测试环境隔离等稳定性问题。

### 新功能

- 新增台股 suffix-only 个股分析 MVP：`.TW`/`.TWO` 代码可走 YFinance 日线与近实时行情，并补齐市场识别、交易日历和 Prompt 能力边界。
- 台股 `tw` 纳入 DecisionSignal、Portfolio、Intelligence 服务层、API 枚举和 Web 筛选，避免台股分析信号被市场归一化静默丢弃。
- 新增台股三大法人资料层 fetcher `TwInstitutionalFetcher`，支持 TWSE/TPEx 来源、日期转换、单日缓存和 fail-open 退化。
- 大盘复盘新增 `jp`/`kr` 市场，支持日经225/TOPIX、KOSPI/KOSDAQ 指数复盘，并扩展 `MARKET_REVIEW_REGION`、交易日过滤和 Web 设置枚举。
- 新增 GenerationBackend Phase 1 抽象和显式 opt-in 的 `codex_cli` 本地 CLI generation backend，提供结构化错误、fallback、stream 降级和 usage unavailable contract。
- 新增 reserved Hermes 本地 HTTP generation 渠道，提供 JSON generation、no-proxy 本地调用和 saved secret endpoint 绑定。
- 新增 Provider Cache Capability Registry，按 provider、API surface、gateway 与 verification status 建模 prompt cache 能力。
- 支持 `SCHEDULE_TIMES` 多时间定时推送，长运行 Web/API/Desktop 进程保存调度配置后可热启停或重建 runtime scheduler。
- 新增信号归因分析和 Web AI 建议页单股信号时间线，并为自动生成与历史回填的 DecisionSignal 写入默认 `decision_profile` metadata。
- 大盘复盘、Web 报告页和通知关联板块补齐概念板块排行与概念信号展示。

### 改进

- TickFlow 扩展为可选 A 股日 K、实时行情、股票列表/名称数据源，并增加 count、完整性校验和批量预取缓存保护。
- 硬化 JP/KR/TW suffix 识别、日韩股票种子索引、YFinance 报价/基本面上下文，以及 JP/KR Portfolio 与 Market Light 边界。
- Web 设置页新增首次启动配置检查卡与定时任务面板，隐藏内部 `SCHEDULE_TIMES` 键，并改善重复任务提示的关闭与自动消失体验。
- Web 历史报告详情不再内嵌 AI 建议卡片，结构化决策信号集中到 AI 建议页，并保留来源报告 ID/URL 参数精确定位。
- `GENERATION_BACKEND=codex_cli` 下普通分析与大盘复盘不再因缺少 LiteLLM API Key 被误判不可用，并改用 `--output-last-message` 文件读取最终响应。
- 本地 CLI backend 对 stdout/stderr 诊断预览和最终响应实行执行期总量上限，并补齐新增 generation backend 数字配置最大值校验。
- AlphaSift 默认依赖 pin 更新到 `0a7b9cd59e81718f851890535241bc105d4ddc64`，并默认走 DSA EastMoney 兜底 provider、暴露 source health 诊断。
- Docker Compose 默认内存建议提升到 1G；每日分析 workflow 兼容误将 `STOCK_LIST` 配到同名 Environment variables 的场景。
- Agent 路径同步 signal attribution prompt，通知报告摘要不再展开 AI 决策信号明细，完整信号保留在个股详情与单股报告。

### 修复

- API 异步批量分析共享概念板块排行缓存，避免同批多股重复拉取全市场概念排行。
- 修复通知 Markdown 表格转换在空单元格后将后续内容错配到错误表头的问题。
- 修复 Market Light 区域归一化拒绝 `jp`/`kr`、日韩历史列表市场阶段摘要误传 `analysis_phase` 和默认通知报告缺少 `dashboard.phase_decision` 的问题。
- 固定 Docker 可安装的 Longbridge SDK 版本为 0.2.75，并修复 Docker 镜像中 efinance 缓存目录属主导致 A 股数据源降级的问题。
- 持仓快照今日估值改为受限并发预取实时价，减少持仓较多时 Web 组合页面刷新超时。
- Web 首页重新分析完成后自动切换到同一股票最新报告，并修复 Windows 环境下 Web/Desktop 静态 JS 资源可能以 `text/plain` 返回导致黑屏的问题。
- 修复 `--serve --schedule` 与 Web/API runtime scheduler 状态脱节、立即执行忙碌状态误提示、重建定时任务重复监听和启动参数语义丢失。
- 修复 `main.py --serve-only` 在低配主机上因惰性 import 应用超出 uvicorn 启动自检窗口而反复重启的问题。
- 修复 Web 回测未传分析日期范围、股票代码未归一化导致成功响应但结果为空的问题，并为空候选、行情不足和非法后缀提供诊断信息。
- 修复 unsupported `GENERATION_BACKEND` 被当成空响应/模板 fallback、`codex_cli` stdout 重复计入输出上限和主分析 JSON schema fallback 语义回退的问题。
- Docker 部署中 Web 设置页保存自定义 Webhook 模板时会转义 `$content_json` 等占位符，并在运行时还原，避免 Compose 重新部署展开为空。

### 文档

- 补齐概念板块排行字段契约、通知报告行业/概念类型列展示和数据源稳定性与故障处理图示。
- 补充 JP/KR/TW suffix-only MVP、`MARKET_REVIEW_REGION` 保存/校验/回退矩阵、Market Light 边界和 PR 提交流程约束。
- 补充本地 CLI backend 隐私边界、非离线模型说明、Docker/CI 登录态限制和 `codex_cli` experimental/limited 状态。
- 补充回测请求链路说明，并同步更新 `docs/full-guide.md` 与 `docs/full-guide_EN.md` 示例。

### 测试

- 新增/更新台股、JP/KR 大盘复盘、GenerationBackend、`codex_cli`、Hermes、本地 CLI、runtime scheduler、回测和概念板块排行相关回归测试。
- 加强 `tests/test_analysis_api_contract.py`、`tests/test_analysis_history.py` 与 `tests/test_backtest_service.py` 的临时 `.env` 隔离，避免本地真实 `.env` 污染系统配置测试。

## [3.23.0] - 2026-06-20

### 发布亮点

- feat: DecisionSignal 贯通报告提取、Web 展示、反馈/后验、告警通知和组合风险，AI 建议信号进入可追踪闭环。
- feat: 新增合规 RSS/Atom 与 NewsNow 资讯源情报池，分析、Agent 和大盘复盘可 fail-open 复用本地资讯 evidence。
- feat: 新增日本/韩国 suffix-only 个股分析 MVP，支持 `.T`、`.KS`、`.KQ` 标的通过 YFinance 获取行情与技术上下文。
- feat: 新增 Token 用量监控看板、legacy LLM usage telemetry 和 message stability audit，增强 LLM 调用可观测性。
- fix: 修复运行流 live 状态、AlphaSift 缓存/字段兼容、发布说明诊断和日韩股票输入/历史展示等稳定性问题。

### 新功能

- 个股分析历史成功保存后会从最终报告 best-effort 提取 `DecisionSignal` 决策信号，复用现有信号去重、计划质量计算和脱敏契约。
- 新增 Web AI 建议页、持仓页 latest active 信号摘要、历史报告信号展示和更完整的信号详情卡片，展示评分、置信度、价格计划、催化、风险与失效条件。
- 新增 DecisionSignal 用户反馈、信号级日线后验评估、统计 API 与 Web 展示，使用 outcome/feedback sidecar 表并保留主信号表契约。
- 将 DecisionSignal 复用到告警、通知和组合风险：告警触发关联 latest active 信号或创建最小 alert 信号，通知追加低敏信号摘要，持仓风险聚合 active sell/reduce/alert 信号并保持 fail-open。
- 新增合规 RSS/Atom 资讯源配置、拉取、去重、入库、查询、retention 与基础安全校验 API，作为个股/市场资讯情报池基线。
- 资讯源新增 `newsnow` 类型、`NEWSNOW_BASE_URL` 配置和 `/api/v1/intelligence/sources/defaults` 默认源初始化接口，内置财联社热门、雪球热门股票、华尔街见闻快讯、金十数据和格隆汇事件等财经热点源。
- 个股分析、Agent 分析和大盘复盘会 fail-open 读取本地资讯/情报池，并把来源链接作为新闻上下文和 evidence 输入。
- 新增日本/韩国 suffix-only 个股分析 MVP：手输 `.T` / `.KS` / `.KQ` 代码可走 YFinance 日线与近实时行情，补充市场识别、交易日历、Prompt 语义、Web/API 类型和能力边界文档。
- 新增 Token 用量监控看板与 `/api/v1/usage/dashboard` 接口，展示 LLM 调用总量、Prompt/Completion 拆分、模型用量、调用类型分布和最近调用明细。

### 改进

- 为 `DecisionSignal` 补齐默认生命周期、同源窄 relaxed 去重、相反 active 信号自动 invalidated、terminal 状态不可 PATCH 复活和低敏 market phase hints 提取。
- 补充 Web decision-signals typed API wrapper 与契约隔离测试，并将历史报告 AI 建议查询收口到精确报告懒提取。
- DSA 数据源链路新增 Tencent 日 K 直连 fetcher、daily source health 短期熔断，并升级 AlphaSift 默认 pin/runtime bridge。
- 默认启用 `DAILY_SOURCE=auto`、Sina snapshot 优先级、候选级 quote context 与 LLM ranking timeout/max tokens 边界。
- 新增 legacy LLM usage provider/cache telemetry、message HMAC 诊断字段和普通个股分析 legacy message stability audit，不改变公开 Usage API、prompt 或 provider 参数。
- 问股页移动端策略选择改为默认收起的按钮入口，展开后仍可多选策略并在发送后自动收起，减少对对话内容的遮挡。

### 修复

- 修复运行流 live SSE 脱敏、后期 LLM/通知卡片重复、数据源聚合卡片过早成功、Web 首页窄侧栏挤压股票信息，以及个股分析自动生成大盘上下文时运行诊断互相串扰的问题。
- 修复 AlphaSift 热点题材 EastMoney 瞬断且无缓存时的空态、桌面更新热点缓存保留，以及 `leader_stocks` / `stocks` 双字段兼容问题。
- 修复 Web AI 建议页筛选/状态更新分页、价格计划单边入场价展示、持仓 latest 信号刷新、详情 JSON 安全渲染和卡片交互语义问题。
- 仅允许历史报告存在明确 `action` 或可解析动作时才触发决策信号懒回填，避免 `decision_type=hold` 等统计口径在建议不明确场景误回填。
- 修复 #1390 P6 DecisionSignal 在组合风险快照语义和默认聚合通知展示中的遗漏。
- 默认禁用 `/api/v1/intelligence/sources/defaults` 新建源，避免公开示例 NewsNow 实例被默认启用，同时统一 500 响应细节仅入日志、响应返回通用错误信息。
- Web 股票自动补全、输入校验、历史/任务展示和筛选补齐日韩 Yahoo 后缀代码、常用日韩股票索引与股票池裸码解析，避免 `000660`、`005930`、`7203.T`、`005930.KS`、`035720.KQ` 等场景崩溃、误入 A 股语义或历史分裂展示。
- 日韩个股分析在本地历史上下文缺失时会用 YFinance 日线兜底构造 K 线与技术指标上下文，避免报告误称日股/韩股核心行情和技术数据不可用。
- 发布说明生成查询 PR 作者失败时保留降级并输出包含 PR 编号和异常类型的 warning，便于排查 token、权限、网络或 GitHub API 异常。

### 文档

- README、完整指南和市场支持文档补充日股/韩股示例（`7203.T`、`005930.KS`），并明确 `.T/.KS/.KQ` 当前为 YFinance-only MVP。
- 新增 DecisionSignal 决策信号专题文档，补齐字段/API/Web/告警通知/组合风险/后验评估、脱敏、迁移与回滚说明，并收口 Web i18n 显示边界。
- 补充 AlphaSift 迁移与回退边界：明确 `ALPHASIFT_INSTALL_SPEC` 显式覆盖语义、`requirements.txt + DEFAULT_ALPHASIFT_INSTALL_SPEC` 与运行时兼容边界。
- 补充资讯源基线文档，说明 `NEWS_INTEL_*` 配置、NewsNow 自建建议、模型/provider/base URL 不变更边界，以及禁用或移除情报源变量的回退路径。

### 测试

- 新增/更新 DecisionSignal 服务、提取、反馈/后验、摘要、文档、通知、告警、持仓风险、Web 展示和 label 的回归覆盖。
- 新增/更新 RSS/Atom / NewsNow 情报源服务、API、安全校验、分析接入和配置兼容测试。
- 新增/更新日韩市场识别、股票索引、YFinance 行情兜底、Web 自动补全和输入校验测试。
- 新增/更新 LLM usage、运行流、AlphaSift、发布说明生成和移动端交互相关回归。


## [3.22.0] - 2026-06-13

### 发布亮点

- feat: 新增 DecisionSignal 独立存储与 API、运行流快照 API 和 Web 运行流视图，补齐建议动作结构化字段与历史/回测展示链路。
- feat: AlphaSift 热点题材链路升级为新版合约，支持热点榜单、题材详情、发酵路线、概念股详情、缓存与兜底数据源。
- feat: 个股分析默认注入当日大盘环境摘要，并在高风险/退潮环境下软化激进买入建议。
- fix: 修复问股历史追问标的上下文、自选股等价代码匹配、低质量新闻过滤、运行流脱敏与 AlphaSift 热点详情展示等稳定性问题。

### 新功能

- 新增独立 `DecisionSignal` 存储、Repository、Service 与 `/api/v1/decision-signals` API，支持来源/市场/股票/动作/期限/阶段去重、查询、续期、状态更新、懒过期、持仓过滤和敏感信息脱敏。
- 新增分析任务与历史报告运行流快照 API，提供 lanes、nodes、edges、events、summary 等统一契约，并从任务队列、运行诊断和 AnalysisContextPack overview 构建脱敏数据流/信息流。
- Web 端为活跃任务、历史报告和大盘复盘报告补充运行流视图入口，支持查看运行摘要、拓扑节点、事件流和基础排障详情。
- 新增 AlphaSift 热点题材链路：后端提供 `/api/v1/alphasift/hotspots` 与 `/api/v1/alphasift/hotspots/{topic}` API，Web 选股页新增热点题材区域并支持发酵路线与概念股查看。

### 改进

- 个股分析新增按当日/市场复用的大盘环境摘要，普通 Pipeline 与 Agent 分析 Prompt 可读取低敏大盘背景；新增默认开启的 `DAILY_MARKET_CONTEXT_ENABLED` 配置，用户仍可显式关闭。
- 个股分析与历史/回测展示新增可选八态 `action` / `action_label` 建议动作字段，保留 `operation_advice` 自由文本和 `decision_type=buy|hold|sell` 统计口径。
- 补充 Web decision-signals typed API wrapper 与契约隔离测试，暂不接入 UI。
- 完善运行时日志上下文，补充 logger name、触发来源、市场统计与实时行情预取链路状态，便于排查调度、API、Bot 和数据源降级路径。
- 持仓管理页新增持仓账户删除入口，复用现有账户软删除接口，误建账户会从默认列表、快照、风险、录入入口和事件列表隐藏且不物理清理历史流水。
- AlphaSift 依赖锁定更新到 `d038c52c468543726fc1fd830b53c27d3f09d6da`，并为新版 last-good snapshot、日线历史、行业/概念 provider cache、hotspot 榜单、题材发酵路线、概念股详情、上次成功热点缓存与 post-analysis 元信息补齐 DSA 运行期和 Web 适配。
- AlphaSift 热点题材读取默认优先使用上次成功缓存，手动刷新才实时拉取并覆盖缓存，实时拉取失败时尽量回退旧缓存。
- AlphaSift 热点题材区域改为默认折叠，展开并选中具体题材后再读取详情；发酵路线改为带时间标记的时间线展示，概念股可点击进入首页并直接启动分析。
- AlphaSift 热点题材数据链路复用同一次东方财富板块异动快照，并从真实涨跌幅、异动次数和高频个股推导趋势分、持续分、阶段与龙头样本。
- AlphaSift 热点题材刷新在合约层返回少量或缺少关键字段时改用 DSA 东方财富板块异动直连榜单，忽略少于 3 条的本地热点缓存，并补齐板块兜底字段。
- AlphaSift 热点题材卡片改为更紧凑的多列布局，概念股列表改为独立“分析”按钮触发个股分析；详情优先合并东方财富成分股、同花顺解析和板块异动龙头兜底并按日聚合发酵时间线。
- AlphaSift 热点题材详情新增 DSA 侧 30 分钟磁盘缓存，重复点开同一题材时复用发酵时间线与概念股详情；题材事件仅展示 AlphaSift 合约时间线、同花顺摘要、已配置新闻搜索或东财板块异动等真实来源。
- AlphaSift 热点题材消息催化改为摘要展示：配置 LLM 时优先压缩为一句题材催化摘要，未配置或调用失败时回退本地短摘要。
- AlphaSift 热点题材列表新增可选 `include_details` 详情预取，Web 默认随热点列表批量带回 Top 题材发酵路线与概念股并复用前端内存缓存；新闻催化在 LLM 不可用时改为本地事件归纳。
- 改造 `main.py --webui-only` 启动行为：若 FastAPI 监听端口已被占用，启动即 fail-fast 抛出明确错误并退出。

### 修复

- 问股从历史报告进入后的追问会持续携带当前标的，切回或重载已有会话时可从历史消息恢复基础当前标的，并由后端阻断未明确切换时的错误股票工具调用、交易所片段和指标缩写误路由。
- 自选股加入和删除按等价股票代码匹配港股及大小写美股变体，避免 `00700`、`HK00700`、`00700.HK` 或 `aapl`、`AAPL` 被误判为不同标的。
- 收紧建议动作 legacy fallback：否定/回避表达、中文金融上下文、`buy or sell`、多 guard 歧义文本以及英文复合词不再误渲染成 action badge；有结构化 `action` 时回测/历史趋势等入口按界面语言显示 action 标签。
- 股票新闻与多维情报搜索在相关度排序后新增域名无关的准入过滤，剔除下载/安装包/应用评分页及成人/招嫖服务垃圾页，并在同批已有有效标的/行业候选时移除 `score=0` 背景填充项。
- 修复历史报告运行流快照在混合时区事件时间戳下返回 500 的问题。
- 修复运行流 live SSE 事件未复用快照层递归脱敏规则的问题，避免本地路径、prompt/raw response、代理头等敏感诊断字段在 refetch 前短暂暴露。
- AlphaSift 热点题材默认加载在无缓存且旧适配层缺少 `alphasift.hotspot` 模块时返回空态，不再一打开选股页就显示 AlphaSift 未就绪；手动刷新仍会提示依赖需更新。
- 为 THS 发酵路线补充列名兜底：当 `stock_board_concept_summary_ths` 返回缺列时仅跳过该来源富化，不影响热点题材详情 API 返回。
- 桌面发布打包改用冻结可执行文件运行时探针校验 `alphasift.dsa_adapter`，避免 macOS PyInstaller 将模块内嵌进可执行文件时被文件系统/zip 扫描误判为缺失。
- AlphaSift 热点题材详情展示改为优先使用后端融合后的 `route`，避免旧 `timeline` 覆盖新闻/LLM 摘要；手动刷新热点榜单时会同步绕过同题材详情缓存。

### 文档

- README 与繁中 README 快速开始入口补充视频教程链接，并将桌面客户端入口文案调整为客户端配置教程。
- 补充 `docs/alphasift-integration.md`：明确 AlphaSift 锁定 commit 来源、Hotspot 契约边界、LLM/LiteLLM 兼容语义与关闭开关下回退路径。
- 补充 #1381 运行时范围、兼容边界、官方语义依据与常规发布回滚说明。

### 测试

- 覆盖 #1381 后端 runtime 与兼容核验：`tests/test_main_schedule_mode.py`、`tests/test_pipeline_daily_market_context.py`、`tests/test_daily_market_context.py`、`tests/test_daily_market_context_guardrail.py`、`tests/test_agent_executor.py`、`tests/test_config_env_compat.py`、`tests/test_config_registry.py` 与 `apps/dsa-web/tests/system_config_i18n.test.ts`。
- 新增/更新 AlphaSift 后端回归：`python -m pytest tests/test_alphasift_api.py -q`、`python -m pytest tests/test_docker_entrypoint.py -q`、`python -m pytest tests/test_main_schedule_mode.py -q -k "start_api_server_fails_before_thread_when_port_is_busy"`。

## [3.21.0] - 2026-06-07

### 发布亮点

- feat: 新增 Web UI 中英文界面语言切换和飞书 App Bot 通知模式，提升多人部署和企业通知场景体验。
- feat: 大盘复盘报告、历史入口和个股栏继续收口到结构化数据与统一 Markdown/GFM 渲染，Web/API 人工触发入口不再被交易日 gate 短路。
- feat: AlphaSift 选股链路改为可恢复后台任务，并完善 DSA LLM runtime bridge、默认适配层预置和兼容回归。
- fix: 修复英文界面残留中文、诊断展示、运行时环境变量展示、健康检查、桌面更新路径、工作流变量读取和多处 Web 窄布局问题。

### 新功能

- WebUI 新增独立界面语言状态与中英文切换入口，覆盖主导航、首页、登录、设置页和通用控件文案；UI 语言与 `report_language` 解耦，不改写报告语言链路。
- 飞书通知新增应用机器人（App Bot）模式，支持通过 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` 配置，无需额外创建自定义机器人。
- Web 大盘复盘报告新增专用展示视图，历史入口和首页即时结果统一使用 Markdown/GFM 渲染并隐藏个股专属模块。
- 大盘复盘新增结构化 `market_review_payload`，Web、历史详情和推送统一基于结构化数据渲染，并保留 Markdown 兼容展示。
- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 明确控制，并保留 `/install` 作为显式修复路径。

### 改进

- Web/API 大盘复盘人工触发入口不再因交易日检查或相关市场休市而短路跳过；定时任务、GitHub Actions 手动运行和 CLI 默认入口仍保持原交易日 gate。
- AlphaSift Web 选股改为后台任务提交与状态轮询，新增可恢复任务状态展示，避免外部快照、行情或 LLM 变慢时浏览器长请求超时。
- AlphaSift 选股 API 与服务层收敛到 `AlphaSiftService`，endpoint 仅做路由参数接收与错误映射。
- AlphaSift 与 DSA 的运行时 LLM 兼容桥接改为调用期注入，保留 `provider/model/base_url/custom headers/fallback` 语义链路，不做持久化迁移。
- Web 首页侧栏不再单独展示大盘复盘历史集合，最新大盘复盘作为 `MARKET` 并入个股栏，按最近分析时间参与排序，并复用个股栏的选择、删除、完整报告与历史趋势查看能力。
- 多股通知报告将市场阶段收敛为总览下方单行 `市场状态`，不再在每只股票摘要下重复展示数据质量和限制详情。
- API 错误响应构造收敛到共享 helper，保持既有错误 envelope 形状并降低 endpoint 重复代码。
- WebUI 绑定公网地址或 CORS 全开放且未启用管理员认证时新增运行时 warning；仅增加可观测性，不阻断启动、不改写配置。
- 数据库初始化新增 `schema_migrations` baseline 标记表与幂等记录，用于后续 schema 演进追踪；不迁移、不清理、不改写既有业务表数据。
- #1386 P6 复用市场阶段与 AnalysisContextPack 公开摘要联动告警、持仓手动分析、历史、回测和通知展示，不新增数据库迁移。

### 修复

- Web 英文界面补齐回测、组合风险与告警规则相关文案本地化，避免英文模式下残留中文筛选器、按钮和枚举标签。
- 综合情报搜索中的机构分析与业绩预期维度改用 180 天 provider 请求窗口，避免默认短新闻窗口漏掉财报、研报等周期性财经材料。
- Web 个股栏和历史卡片在窄布局下不再让市场阶段标签遮挡股票名称。
- 问股自由文本追问不再将 TTM、PE、YOY 等金融缩写误识别为新股票代码。
- [修复] GitHub Actions 每日分析工作流读取 SearXNG 自建实例地址时支持 Variables 优先、Secrets 回退，修复仅配置 Variables 时 URL 不生效的问题。
- Web/桌面端左侧导航选中态改用 border 实现，避免蓝色竖条指示器溢出侧栏边界；侧栏展开宽度 116px -> 136px，新增 rail 紧凑模式。
- Windows 桌面端自动更新安装目录不再预先加引号，避免带空格路径在自动安装时触发“缺少快捷方式 / 找不到 Daily Stock Analysis.exe”的系统弹窗。
- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 修正大盘复盘结构化 `breadth` 的可用性判断：当市场不支持或抓取失败时不下发 `breadth`，前端展示“暂无数据”，避免误导性 0 值。
- 大盘复盘语言行为遵循全局 `report_language`，并在美股中文场景下本地化市场标签与策略蓝图，避免混入英文策略段落。
- Docker Web 设置页读取配置时在活跃 `.env` 文件缺项时回退展示启动注入的同名环境变量，并补清相关挂载边界文档。
- 报告页运行诊断会区分数据源抓取成功与进入 LLM 分析输入，相关新闻区标注为报告页补充/后续检索资讯，避免与输入数据块状态互相误读。
- `/health` 根路径健康检查现在始终返回 JSON，避免静态 Web fallback 吞掉健康探针；`/api/health` 与 `/api/v1/health` 继续保持兼容。
- `ALPHASIFT_ENABLED` 关闭时不触发 `alphasift` 运行时注入；开启后优先复用已配置的 DSA/provider 配置并注入 `LITELLM_*` 与 `LLM_*` 运行时变量。
- 补齐 openai-compatible 场景下 base URL、`extra_headers` 与 `LITELLM_FALLBACK_MODELS` 的兼容路径与回退链验证。
- 桌面/镜像打包链路保持与运行时一致的 AlphaSift 适配层预置，避免 `pip install` 作为线上修复依赖。

### 文档

- 明确 Issue #777 UI 语言切换采用仓内 `UiLanguageContext` + `uiText` 实现，持久化 key 为 `dsa.uiLanguage`，并补充对应可视化验收指引。
- 明确大盘复盘展示链路、结构化 payload、语言行为、交易日 gate 差异和回滚边界。
- 补充 LLM / LiteLLM 兼容键在 Settings 展示与校验上下文中的回退边界，说明不改写、不迁移、不清理用户现有 provider/model/base URL 持久化配置。
- 补齐 #1602 运行诊断口径修复覆盖范围，说明仅统一输入与展示口径，回滚方式为常规发布回滚。
- 明确 AnalysisContextPack P6 文档、迁移与回滚边界，并同步既有 `SAVE_CONTEXT_SNAPSHOT` 到 `.env.example`、配置注册表、Web 设置帮助和完整指南。
- 补齐 #1386 P7 盘前/盘中/盘后分析的入口、迁移、回滚和用户可见说明。
- 为 AlphaSift runtime bridge 增加官方兼容依据落点，明确 provider/model/base_url/extra_headers/fallback 与回退边界。

### 测试

- Web 方向执行 `npm run lint`、`npm run build`、相关 Vitest 和 smoke 命令；未设置 `DSA_WEB_SMOKE_PASSWORD` 时 smoke 用例按设计 skip。
- Web 测试运行时声明 Node `>=20.19.0 <27` 与 npm `>=10`，并补 localStorage 测试兜底以稳定 Vitest。
- 增补 AlphaSift runtime bridge 与打包脚本静态验证，覆盖 `LLM_CHANNELS`、`LITELLM_FALLBACK_MODELS`、`alphasift.dsa_adapter`、`--collect-all alphasift`。

### chore

- 移除随 issue / PR 验收流程误入库的截图资产，并明确一次性截图证据应保留在 PR 描述、评论、附件或 artifact 中，不作为仓库文件合入。

## [3.20.0] - 2026-06-03

### 发布亮点

- feat: 新增 AlphaSift 选股入口、自动安装与稳定适配层，支持 Web 策略执行、LLM 重排展示和默认关闭的可控启用。
- feat: 完善个股历史、自选队列、市场阶段与 AnalysisContextPack 可见性，增强 Web 报告和 API 的结构化上下文能力。
- feat: MiniMax 默认模型升级到 `MiniMax-M3`，并补齐相关价格、预设和测试覆盖。
- fix: 修复健康检查、Windows 桌面更新与首次运行编码、ETF 日线 secid、LLM base_url 校验和 Agent 日线上下文误判等稳定性问题。

### 新功能

- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 开启后经由稳定适配层读取策略并执行选股。
- Web 首页左侧栏改为个股栏，按股票去重展示，大盘复盘置顶，点击个股加载最新报告，支持按代码变体（.SZ/.SH/.SS）归一化去重合并。保留全选、批量删除和删除确认入口；新增按股票代码批量删除 API `DELETE /api/v1/history/by-code/{stock_code}`。
- 报告详情右侧栏新增自选操作入口，支持查看当前股票是否在自选队列、一键加入或移除；大盘复盘报告不显示该操作。
- 问股页面输入区上方新增自选操作按钮，用户发送包含股票代码的消息后自动显示加入自选/从自选删除入口。
- Web 报告页新增同股历史趋势抽屉入口，历史列表摘要补充趋势、摘要、模型和分析时行情字段，支持按当前股票查看历史分析并加载更多。
- AnalysisContextPack P4 低敏 overview 接入历史详情、同步分析响应、completed 任务状态和 Web 报告页，展示数据块状态、来源、缺失原因与降级摘要。
- #1386 P5 为个股分析报告新增 `dashboard.phase_decision` 盘中决策护栏，并在保存历史前按市场阶段与数据质量限制高置信盘中买卖结论。
- #1386 P4a 新增 `analysis_phase=auto|premarket|intraday|postmarket` API 参数，并在异步任务 accepted、内存 status、list、SSE 与分析 pipeline 中透传请求阶段。
- #1386 P4b Web 报告页新增最终市场阶段标签，任务面板展示请求阶段，并复用 AnalysisContextPack 低敏数据质量摘要。
- MiniMax 渠道模型列表升级：新增 `MiniMax-M3` 并作为默认，按官方 OpenAI-compatible 文档支持 1M 输入上下文（项目保守注册为 `<=512K` 价格档：context_window 512K、`max_tokens` 128K，对应 $0.6/M 输入、$2.4/M 输出，>512K 输入价格档未建模），保留 `MiniMax-M2.7` 与 `MiniMax-M2.7-highspeed`，并保留 `MiniMax-M2.5` legacy 价格条目以兼容现有用户配置的成本估算。Web 设置页 MiniMax 预设模型与价格按 M3 刷新。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 市场阶段低敏摘要接入历史详情、同步分析响应和 completed 任务状态的 report metadata。

### 改进

- 首次运行配置校验补充缺失 AI Key、空 STOCK_LIST、Telegram/邮件成对字段和 Webhook URL 前缀诊断。
- AlphaSift 选股入口在 Web 侧边栏中移动到“问股”下方，贴近 Agent/研究辅助工作流。
- Docker 镜像构建阶段预置默认 AlphaSift 适配层，与桌面发布包一样避免运行期额外安装。
- AlphaSift 选股改为依赖 `alphasift.dsa_adapter` 的稳定接口，Web 策略列表由 AlphaSift 动态提供，不再在前端硬编码。
- AlphaSift 选股页补充 Run ID、快照数、过滤后数量、因子和风险详情，展开候选时展示真实明细，并暂时仅开放当前支持的 A 股市场。
- Web 设置页新增 AlphaSift 选股开关卡片，可直接开启或关闭选股页签。
- 开启 AlphaSift 选股时先切换 `ALPHASIFT_ENABLED` 并检查适配层可用性，缺失时自动调用受控安装接口，不再要求用户额外点击安装。
- AlphaSift 已开启但适配层缺失时，策略列表和选股接口会串行化自动安装锁定来源，并强制重装以覆盖旧版 `alphasift` 包。
- AlphaSift 选股页合并重复的快照源 fallback 提示，并保留 AlphaSift 自身的 Tushare 优先快照源逻辑。
- AlphaSift 选股页在 LLM 重排降级时展示 warning/source error/parse error，并避免把本地因子评分误显示为 LLM 判断。
- Web 设置页不再把 `ALPHASIFT_ENABLED` 作为普通数据源配置项重复展示，该值仅作为“开启选股”按钮背后的持久化状态。
- AlphaSift 关闭时隐藏 Web 左侧“选股”导航入口，避免误导未开启用户。
- 补充 AlphaSift 选股自定义策略显示逻辑，避免未匹配预设项时误显示“均衡多因子”。
- 新增 GET /api/v1/history/stocks 端点按 code 分组返回不重复个股列表；新增 GET /api/v1/stocks/watchlist、POST /api/v1/stocks/watchlist/add、POST /api/v1/stocks/watchlist/remove 端点支持自选队列增删查。STOCK_LIST 读写保持原样，不做自动归一化；add/remove 时归一化比较判断等价代码变体。
- 新增 useWatchlist hook 统一管理自选队列前端状态，复用 SystemConfigService 的 STOCK_LIST 配置项实现持久化。
- AnalysisContextPack P5 增加数据质量评分、`fetch_failed` 状态、Prompt 数据限制区块和 Web 低敏质量展示。
- #1386 P2-full 在 AnalysisContextPack Prompt 数据限制中追加市场阶段与降级数据的交叉约束，并修正中文分析 Prompt 的阶段化行情标签。
- 通知报告默认发送路径恢复既有渠道兼容转换与分片逻辑，新增 renderer 能力仅保留为未来扩展基础。
- 关联板块缺少类型数据时改为单行展示板块名称，避免生成整列 `N/A` 的板块表格。
- 优化 Web 报告详情页信息层级，将输入数据块和运行诊断下移为主体内容后的折叠辅助信息。
- 盘中分析补齐实时行情获取时间、provider 时间、stale、fallback 与 partial/estimated 标记，供 AnalysisContextPack 映射输入数据限制。

### 修复

- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 注册 /api/v1/health 路由并加入认证豁免，修复该路径返回 404 以及开启 ADMIN_AUTH_ENABLED 后健康探针收到 401 的问题。
- Windows 本地首次运行环境检查兼容非 UTF-8 控制台输出，并将 `requirements.txt` 注释改为 ASCII 以降低默认代码页下的依赖安装失败概率。
- AlphaSift DSA 适配层默认开启 LLM 重排，后端显式请求 `use_llm=True`，选股页展示 LLM 分数、判断、覆盖率和关注项。
- AlphaSift 嵌入 DSA 时复用 DSA 已解析的 LLM 模型、渠道和密钥配置，避免 Web 已配置 LLM 但选股 LLM 重排仍因缺少 provider key 降级。
- AlphaSift 选股复用 DSA LLM 路由时过滤未声明的托管 provider 备选模型，并把已声明渠道模型补入回退链，避免残留 Gemini fallback 覆盖可用的 DSA 渠道。
- AlphaSift 默认安装来源改为锁定 commit 的受信任 GitHub 地址；桌面模式自动安装不要求管理员会话，非桌面部署要求管理员认证会话，并继续限制安装来源。
- 修复 Web 开启 AlphaSift 时先安装后写配置导致默认关闭状态无法开启的问题。
- AlphaSift 状态与安装接口不再返回 `install_spec` 明文，仅返回 `install_spec_is_default` 等非敏感状态字段。
- AlphaSift 状态探测区分可选依赖缺失与非预期异常，异常场景记录 warning 并返回非敏感诊断信息。
- 调整 AlphaSift 筛选调用兼容：`screen` 以 `max_results` 为主并支持历史 `max_output` 关键词，同时允许策略透传以对齐前端手动策略参数。
- AlphaSift Web 选股请求使用独立长超时，避免开启 LLM 重排后被通用 30 秒 API 超时提前中断。
- 桌面端打包阶段预置 AlphaSift 并收集适配层，避免发布包运行时再要求管理员自动安装。
- AlphaSift 自动安装仅在 `status` 诊断为 `missing_module` 时触发（仅模块缺失场景）；适配层可导入但运行时异常不再自动 `pip install`，而是返回 `424` 并保留诊断，避免把真实运行时故障掩盖为重装。
- 收口 Web 中文界面残留英文文案与设置页 help 缺口，回测页改为中文展示，并让 Web 设置页仅展示已注册且带说明的配置项。
- Windows 桌面端自动更新静默安装时显式复用当前安装目录，避免自定义安装目录场景下卸载旧版本文件失败。
- Windows 安装器重试旧卸载器时对 `_?=` 安装目录参数加引号，修复旧版本安装在带空格路径时返回 2 导致自动更新失败。
- Windows 桌面端自动更新传给 NSIS 的 `/D=` 目录参数在包含空格时自动加引号，避免安装位置注册表被截断。
- 加固 LLM channel base_url 校验，避免解析差异导致 SSRF 绕过。
- 修正 efinance ETF 日线 Eastmoney secid 路由，避免沪市 ETF 被按深市 quote id 查询导致日线为空。

### 文档

- 明确 AlphaSift 与 LiteLLM 兼容边界：仅桥接 DSA 已声明 provider/model/base URL 为调用期注入，不对 `.env` 做 provider/model 路由迁移；回退方式为关闭 AlphaSift 并恢复原有 `LITELLM_*`/`LLM_*` 配置。
- 明确 AlphaSift 仅复用 DSA 现有 LLM/LiteLLM 配置语义，不新增 `LITELLM_MODEL`、`OPENAI_MODEL`、`OPENAI_BASE_URL`、`LLM_TIMEOUT_SEC` 等模型语义迁移；失败提示与回退路径统一沿用既有系统配置链路，仅影响 AlphaSift 选股能力本身。
- 明确 AlphaSift 自动安装来源锁定、`missing_module` 与运行时异常行为边界，以及 LLM/provider/base URL 与自定义通道回退路径，便于问题溯源与回滚到原有 LLM 配置。
- 明确同股历史趋势新增模型字段为历史快照展示元数据，不影响运行时 LLM Provider/Model/Base URL 路由与配置迁移清理；回退方式为按常规发布回滚本变更。
- 明确 #1311 的兼容性边界：渲染层仅消费分析结果 `model_used` 展示字段，未改动 `wechat/slack/feishu/telegram` sender 发送链路，不触发 provider/model/base_url 兼容迁移。
- 明确 AlphaSift 锁定 commit 的 `alphasift.dsa_adapter` 契约依据，以及当前 DSA API/Web 调用结构的兼容边界。
- 明确 Settings 页面对 LLM 配置仅做展示分组与字段归并，不改写或触发 LLM 迁移/回退路径；兼容现有 `LLM` 配置保存与回退语义。
- 新增 AnalysisContextPack P0 上下文盘点。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。

### 测试

- 同步更新 `llmProviderTemplates`、LiteLLM fallback pricing 与 MiniMax 预设相关单测，断言新默认模型。
- 补充 ETF 日线数据源路由、输入变体、fallback 与 MA 字段回归覆盖。

### chore

- 新增通知报告渠道能力画像、PreparedMessage 与结构感知 Markdown 分片基础设施，为 #1311 全渠道渲染适配打底。
- 预置企业微信、飞书、Telegram、钉钉、Slack 平台 renderer 元数据，暂不改变默认推送报告入口和可见版式。

## [3.19.0] - 2026-05-29

### 新功能

- 落地 #1391 Phase 1 运行诊断最小链路：任务/SSE 追加 trace_id，并记录日线与实时行情 ProviderRun 快照。
- 告警中心新增 P7 大盘红绿灯结构化规则，支持 `market_light_status` 与 `market_light_score_drop` 并复用现有 worker、触发历史、通知和冷却链路。
- 落地 #1391 Phase 2 运行诊断摘要：生成用户可读 RunDiagnosticSummary，提供历史报告诊断 API 与脱敏复制文本。
- 落地 #1391 Phase 3 运行诊断可见性：报告详情和任务面板默认折叠展示运行状态、trace 与可复制排障信息；后端通过 `api/v1/history/{record_id}/diagnostics` 与 `context_snapshot.diagnostics` 提供历史链路回填。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 新增 AnalysisContextPack P2 builder，从普通分析 pipeline 已有 artifacts 组装内部上下文包。
- 问股新增默认关闭的可见对话上下文压缩，支持 Web 开关、Agent 高级 preset、滚动摘要和最近轮次原文保护，降低长会话 token 消耗。
- 股票自动补全索引默认支持从 GitHub main 远程刷新并缓存到本地，Web/CLI 分析入口失败时自动降级到内置索引，降低摘帽和更名后旧简称污染分析的概率。
- 普通分析与 Agent 运行时 Prompt 接入 AnalysisContextPack 低敏摘要，保持 history/API/Web 输出兼容。

### 改进

- `scripts/fetch_tushare_stock_list.py` 可对 A 股中带 `XD`/`XR`/`DR`/`N`/`C` 前缀的名称进行回填修正，供自动补全刷新流程默认使用。
- Web 路由页面改为按需加载，降低首包体积并增加路由加载失败恢复提示。
- Web 完整报告 Markdown 抽屉改为按需加载。
- 新增市场阶段推断基线并明确盘前、盘中、午休、临近收盘、盘后和非交易日语义。
- 新增运行态市场阶段上下文构造与降级测试。
- 设置页配置帮助阶段性补齐 Web 设置页实际展示/可配置字段的中英双语文案，覆盖 Agent、回测、报告、通知路由、系统运行时、AI legacy、数据源和通知高级配置。
- P2-min：LLM Prompt 注入市场阶段上下文。

### 修复

- 股票自动补全索引生成缺少 `pypinyin` 时改为直接失败，避免写出缺失拼音字段的降级索引。
- 归一腾讯实时行情成交量为股口径，避免量能变化倍数被放大并误导分析报告。
- Docker 默认部署移除 `.env` 单文件挂载，避免 WebUI 保存配置时因 `os.replace` 更新挂载点触发 `Device or resource busy`。
- 收敛 #1391 Phase 0 A 股代码归属边界：补齐 `SH`/`SZ` 前缀场景的归属一致性，明确 `data_provider/baostock_fetcher.py`、`data_provider/pytdx_fetcher.py`、`data_provider/tushare_fetcher.py` 的本轮修复范围。
- 修复 `STOCK_LIST` 使用裸 A 股代码时 Baostock 等数据源 fallback 的内部格式转换，保持用户配置继续使用 6 位股票编号。
- Windows 桌面端自动更新在用户确认重启安装后改为静默执行安装器，并在停止内置后端后清理进程引用，降低安装器提示“每日股票分析无法关闭”的概率。
- macOS 桌面端将运行时配置迁移到用户数据目录，并在旧 `.app` 包内文件仍可访问时迁移 `.env`、数据库和日志，避免后续替换升级后重新配置。
- 恢复 Agent/历史兼容快照中的关联板块与板块联动字段提取，修复新版首页报告缺少“板块联动”的回归问题。
- 修正 Web 设置帮助中 legacy 告警 JSON 字段名与静默时段投递语义说明。
- 修复 Web 中文设置页在数据源、通知、系统与 Agent 区域的配置标题、说明和关键下拉选项漏翻问题。
- 修复问股会话切换和首页任务重连后可能残留 Agent/分析任务进行中状态的问题。
- 问股 single-agent 新增 provider-aware trace 分轨，跨轮保留 DeepSeek V4 thinking + tool-call 的 `reasoning_content` 与工具协议材料。
- 为 Akshare 新浪/腾讯 A 股历史兜底接口增加调用级超时，并补齐 Tushare `605xxx` 沪市代码路由回归测试，避免定时分析因数据源无响应而挂起。
- 将 `exchange-calendars` 依赖下限提升到 `4.13.0`，避免 pandas 3 环境导入交易日历时因 Timedelta 单位 `T` 失效导致分析失败。
- 交互式命令（钉钉会话、飞书会话、Telegram）触发的分析结果只回到来源会话，不再同时广播到静态通知渠道。
- 适配 Longbridge OAuth 2.0 认证与 token 缓存恢复，避免新后台无 Legacy Access Token 时长桥数据源被误判为未配置。
- Longbridge OAuth 路径在当前 SDK 不支持 `OAuthBuilder` / `Config.from_oauth` 时明确日志降级，避免 Linux/Docker 仅可安装旧 SDK 时构建失败。
- 兼容 YFinance 日线返回未命名日期索引的场景，避免标准化后缺少 `date` 列导致美股日线 fallback 中断。

### 文档

- 新增 #1391 Phase 0 运行诊断契约文档，明确 trace_id、诊断摘要、关键链路范围与脱敏/fail-open/retention 边界。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。
- 说明本次桌面修复仅覆盖 Windows NSIS 更新安装链路与后端进程生命周期清理；未改动设置项保存/模型运行时清理语义。移除此前误入的 `docker/Dockerfile` `npm registry` 变更，恢复部署构建与更新修复的职责隔离。
- 新增 AnalysisContextPack P0 上下文盘点，明确字段质量状态、现有状态映射和首版 pack 边界。
- 明确 #1391 Phase 2 的结构化检测告警为非配置迁移信号：`agent_max_steps`/`agent_orchestrator_timeout_s` 非法值会 fallback 至默认并产生日志告警，新增诊断链路仅新增 `context_snapshot`/`RunDiagnosticSummary` 读写字段，不改写 `litellm_model`、`agent_litellm_model`、`openai_base_url`、LLM channel 路由或配置迁移语义。
- 补充 #1391 Phase 3 兼容性说明：记录后端诊断持久化、历史查询与通知回写链路变更边界与回滚策略，并补齐后端门禁级验证要求。

### 测试

- 收敛 #1391 Phase 3 后端/API 与 Web 回归检查：`./scripts/ci_gate.sh`、`test_pipeline_market_phase_context.py`、`test_analysis_api_contract.py`、`test_analysis_history.py`、`npm run lint`、`npm run build`。
- 执行 `python -c "import exchange_calendars as xcals; xcals.get_calendar('XSHG'); print('ok')"` 通过验证，以覆盖导入与交易日历初始化兼容性。

## [3.18.0] - 2026-05-21

### 发布亮点

- feat: 告警中心扩展到 P2-P6，补齐后台评估、真实通知结果、业务冷却、技术指标规则，以及自选股 / 持仓 / 账户联动规则。
- feat: 个股分析支持策略选择，新增热点题材、事件驱动、成长质量和预期重估策略，并为 HK/US 报告补充基本面、财务摘要、股东回报和关联板块。
- feat: 新增 Finnhub / AlphaVantage 美股数据源适配器，扩展美股日线 failover 链，提升美股行情获取韧性。
- fix: 修复桌面端发布打包、分析状态接口、AlphaVantage 涨跌幅、持仓实时估值、告警历史去重、数据库冷启动和 fallback pricing 注册等稳定性问题。

### What's Changed

- feat: Add alert-center P2-P6, Web strategy selection, HK/US fundamental context, static-report financial sections, and Finnhub / AlphaVantage US-market fallback.
- improve: Refine LiteLLM parameter recovery, yfinance currency/dividend handling, RSI calculation, market-review presentation, stock-news relevance ranking, and report table rendering.
- fix: Harden desktop packaging/update assets, completed analysis-status responses, AlphaVantage pct_chg routing, portfolio realtime snapshots, alert trigger dedupe, DatabaseManager cold start, and fallback pricing registration.
- docs/tests: Add beginner setup and settings-help docs, document compatibility/rollback boundaries, and extend regression coverage for API, alert, packaging, and release paths.

## [3.17.1] - 2026-05-16

### 发布亮点

- fix: 桌面端 Windows / macOS 打包脚本显式关闭 electron-builder 自动发布，避免 tag 构建时因缺少 `GH_TOKEN` 在本地打包完成后失败；Release workflow 继续负责上传和发布产物。

### What's Changed

- fix: Add `--publish never` to the Windows and macOS Electron packaging scripts so tag builds only create local artifacts and GitHub Actions handles release upload/publish.

## [3.17.0] - 2026-05-16

### 发布亮点

- feat: 新增 Alert API MVP，支持告警规则 CRUD、启停、一次性测试以及触发/通知结果查询，首版覆盖 `price_cross` / `price_change_percent` / `volume_spike` 并保持 legacy 配置兼容。
- feat: 通知网关新增 ntfy 与 Gotify 一等渠道，并补齐通知降噪、静态渠道隔离、诊断、Web 测试和 GitHub Actions env 对照校验。
- feat: Windows 桌面安装版接入自动更新安装链路，支持后台下载、确认重启安装、运行时文件备份/恢复和发布产物元数据校验。
- improve: 大盘复盘新增概念排行、人气股、涨停池等底层数据源，支持指数涨跌颜色语义配置，并将复盘结果写入历史记录。
- improve: Web 设置页支持 `.env` 配置备份导入/导出和通知/Agent 区域局部错误兜底；报告新增 `REPORT_SHOW_LLM_MODEL` 开关控制模型信息展示。
- improve: Docker 启动入口自动修复挂载目录权限并在日志目录不可写时降级到控制台，减少普通部署的手动修复步骤。
- fix: 数据源缺凭据或连接失败时更温和降级，Longbridge / Pytdx 加入冷却，资金流缺失时避免输出高置信买入结论。
- fix: 分析与报告链路兼容 OpenAI-compatible `content_blocks` 响应，归一策略价格字段，并修复大盘复盘滚动和历史记录丢失问题。
- docs: 补齐通知、告警中心、桌面打包、README / 指南和 PR title 治理说明，明确多处配置兼容边界与回滚路径。
- test: 增加 Alert API、通知降噪/路由、Docker entrypoint、数据源预取、桌面更新链路和分析历史等回归覆盖。

### What's Changed

- feat: Add an Alert API MVP with rule CRUD, enable/disable, one-shot testing, trigger history, notification results, and legacy config compatibility.
- feat: Promote ntfy and Gotify to first-class notification channels with Web tests, routing, Actions integration, diagnostics, and noise control.
- feat: Add the Windows desktop auto-update install flow with runtime state backup/restore and release artifact metadata verification.
- improve: Extend market review data sources, add configurable index color semantics, and persist market review results into analysis history.
- improve: Add Web `.env` backup import/export, local settings panel error boundaries, and a report model visibility toggle.
- improve: Harden Docker startup by repairing mounted directory permissions and falling back to console logging when mounted logs are not writable.
- fix: Cool down unavailable optional fetchers, reduce noisy Longbridge/Pytdx retries, and downgrade buy advice when capital flow data is missing.
- fix: Handle OpenAI-compatible `content_blocks`, normalize strategy price fields, and recover market review scrolling/history behavior.
- docs/tests: Update notification, alert, desktop packaging, README/guide, and governance docs; add focused regression coverage for the new release paths.

## [3.16.0] - 2026-05-10

### 发布亮点

- feat: Web 首页新增“大盘复盘”触发入口、任务轮询与完成后报告直出；首次启动配置状态可提示缺口并引导到系统设置。
- feat: 新增通知路由策略，支持按 report、alert、system_error 将通知收窄到指定渠道；Web 设置页支持通知渠道一键测试。
- feat: 系统设置页新增配置项帮助入口与多语言帮助文案基础设施，首批覆盖自选股、LLM 主模型、LLM 渠道、飞书 Webhook 与 WebUI 监听地址。
- improve: 大盘复盘 API、CLI、Bot 共用 `build_market_review_runtime` 装配路径，补齐 `litellm_model` / `llm_model_list` 与 legacy key 回退说明。
- improve: 个股报告操作建议结合支撑/压力、量能、筹码与主力资金流校准，减少买入/卖出剧烈切换，并补强 Agent 决策兜底。
- improve: Docker 镜像支持非 root 用户运行，LiteLLM 依赖约束放宽到后续安全 1.x 修复版本。
- fix: 修正 LLM 渠道测试中 `Model disabled`、provider blocked 等错误分类，避免被误报为网络异常。
- fix: 港股日线跳过不支持港股的内置历史数据源；北交所 `BJ` 前缀与 `.BJ` 后缀代码校验保持一致。
- fix: Web 大盘复盘按钮可观测性、Windows fallback 锁进程探测和催化线索展示更稳健。
- docs: 新增文档中心与配置帮助维护说明，清理 README、完整指南与配置指南中的临时 PR/文档同步说明。

### What's Changed

- feat: Add a Web home market-review trigger with task polling and inline report display; setup status now points users to missing configuration.
- feat: Add notification routing by report, alert, and system_error; add one-click notification channel testing in Web settings.
- feat: Add settings field help infrastructure with multilingual help text for the first batch of core configuration fields.
- improve: Share `build_market_review_runtime` across API, CLI, and Bot market review paths; document `litellm_model` / `llm_model_list` and legacy key fallback behavior.
- improve: Calibrate stock advice with support/resistance, volume, chips, and main-force capital flow; strengthen Agent decision fallback behavior.
- improve: Run Docker images as a non-root user and relax LiteLLM constraints to allow safe future 1.x fixes.
- fix: Classify `Model disabled`, provider blocked, and related LLM channel test errors more accurately instead of reporting them as generic network failures.
- fix: Avoid unsupported built-in historical providers for Hong Kong daily data; align Beijing Stock Exchange `BJ` prefix and `.BJ` suffix validation.
- fix: Improve Web market-review observability, Windows fallback lock probing, and market catalyst snippet rendering.
- docs: Add the documentation index and settings-help maintenance guide; remove temporary PR/doc-sync notes from README and user-facing guides.

## [3.15.0] - 2026-05-05

### 发布亮点

- LLM 渠道配置体验继续升级：新增 Anspire OpenAI-compatible 网关接入，并补齐常用服务商预设、官方来源、能力标签、配置注意事项和 GitHub Actions 显式映射。
- Web LLM 配置检测更可诊断：细分错误 reason，并支持用户显式触发 JSON、tools、vision、stream 运行时 smoke。
- LLM 运行时配置清理更稳健：只清理托管 provider 的失效运行时选择，并保留 `cohere/*`、`google/*`、`xai/*` 等直连 provider 兼容语义。
- 通知与 Bot 状态可观测性增强：自定义 Webhook 支持 JSON body 模板，Bot `/status` 展示更完整的 LLM、Agent 与通知渠道状态。
- 大盘复盘、实时告警、Agent weak 兜底和持仓估值继续补强，降低默认值覆盖、缺价污染和配置排障成本。

### 新功能

- 支持 `ANSPIRE_API_KEYS` 默认接入 Anspire OpenAI-compatible 大模型网关，并在 LLM 渠道编辑器补充 Anspire Open 预设。
- 自定义 Webhook 支持 `CUSTOM_WEBHOOK_BODY_TEMPLATE` JSON body 模板，便于适配 AstrBot、NapCat 和自建推送服务。
- 大盘复盘结构化区块新增大盘红绿灯结论，基于盘面温度输出 green/yellow/red、核心原因和操作建议。
- EventMonitor 支持 `price_change_percent` 涨跌幅阈值规则，可按上涨或下跌方向触发实时告警。
- Web LLM 渠道编辑器新增常用服务商配置模板与预设，覆盖 MiniMax、火山方舟、OpenAI、Claude、Gemini、Kimi、Qwen、GLM、豆包等入口。

### 改进

- Web LLM 配置检测补充细分错误分类，并新增显式触发的 JSON/tools/vision/stream 运行时 smoke；默认测试和保存流程不变，检测结果仅作为当前配置的一次 best-effort 诊断。
- Bot `/status` 展示统一 LLM 主模型、Agent 模型、渠道模式、YAML 配置和更多通知渠道状态。
- Web LLM 渠道编辑器展示 provider 能力标签、官方来源链接和配置注意事项提示；这些标签仅用于配置参考，不代表运行时能力已验证通过。
- 抽出 Web LLM provider preset 单一模板数据源，保持现有配置保存语义不变。
- 补齐 LLM provider channel 在 GitHub Actions 中的显式映射，并同步 `.env` 示例与配置文档。

### 修复

- Agent weak 完整性兜底在模型缺少评分、趋势、操作建议或 dashboard 关键块时优先保留本地趋势分析结果，并只补齐真正缺失的仪表盘字段，避免首页评分被默认 50 覆盖。
- 统一持仓快照输出现价、市值、浮盈亏、收益率与价格元信息，避免缺价或 stale 价格污染持仓估值。
- LLM 渠道测试补充结构化诊断与设置页排障提示，便于定位 provider、模型、Base URL 和鉴权配置问题。
- 明确 runtime 清理兼容边界：仅对托管 provider（`gemini`、`vertex_ai`、`anthropic`、`openai`、`deepseek`）触发保存前失效值清理，`cohere/*`、`google/*`、`xai/*` 直连值按 legacy 兼容路径保留，不做无提示迁移或覆写。
- 将 MiniMax 预设调整为官方 OpenAI-compatible Base URL 和当前模型示例，并补充 MiniMax、火山方舟、LiteLLM 兼容来源与回退说明。
- 移除截图识别对 Gemini 3 Vision 模型的过时降级逻辑，默认推断改用当前 Gemini 模型配置。

### 文档

- 完善 LLM provider 配置文档，补充配置方式选择、Actions 变量对照、运行时检测边界、错误 reason 排障和回滚路径（#1180）。
- 补充 LLM 渠道编辑器的官方来源、依赖兼容窗口、保存时的运行时模型清理规则，以及旧配置回退路径说明。
- 为 `cohere/*`、`google/*`、`xai/*` 直连语义补充官方 provider/model 说明、`litellm>=1.80.10,<1.82.7` 兼容依据引用，并明确示例模型名仅为配置保留行为说明而非可用性背书。
- 明确 `price_change_percent` 事件告警仅为配置与运行时规则扩展，未变更模型/provider/base URL/LiteLLM 兼容语义；回退路径为关闭/移除 Event Monitor 配置。
- 同步 README、DEPLOY、full-guide、Anspire、AIHubMix 与 SerpAPI 相关说明，统一外链、配置口径和评审一致性说明。

### 测试

- 补齐 AI 配置页与 `task_queue` 的 LLM 运行时清理/同步回归证据：恢复渠道模型时保留 fallback、编辑模型列表期间不静默清空运行时选择，渠道无可用模型时清理失效 runtime 引用，并覆盖 legacy key 与 `cohere/*`、`google/*`、`xai/*` 直连 provider 保留语义。
- 覆盖 Web LLM 配置检测的细分错误分类，以及 JSON、tools、vision、stream 运行时 smoke 的显式触发路径。

## [3.14.2] - 2026-04-30

### 发布亮点

- 大盘复盘扩展到港股，并让 Bot `/market` 与 CLI/调度入口使用一致的交易日过滤语义。
- 问股与 Agent 链路增强配置缺失、决策 fallback 和多策略选择体验。
- LLM 与分析报告链路提升稳定性：非法 JSON 响应会继续尝试备用模型，LiteLLM DEBUG 日志默认降噪。
- 新增只读首次启动配置状态接口，为后续配置向导和 smoke run 奠定基础。

### 新功能

- 大盘复盘支持港股市场：`MARKET_REVIEW_REGION` 新增 `hk` 选项；`both` 扩展为 A股+港股+美股，并新增港股指数（HSI/HSTECH/HSCEI）复盘链路。
- 新增只读首次启动配置状态接口 `GET /api/v1/system/config/setup/status`，用于识别 LLM、Agent、自选股、通知和本地存储配置缺口；该接口不会重载运行时、写入 `.env` 或创建数据库文件。

### 改进

- 问股页面支持组合选择多个 Agent 策略。

### 修复

- Bot `/market` 命令复用 `get_open_markets_today()` / `compute_effective_region()` 做交易日过滤：结果作为 `override_region` 透传给 `run_market_review`；若结果为空字符串则跳过复盘并推送“今日相关市场休市”，与 CLI/调度入口行为一致。
- 问股 Agent 在未配置可用 LLM 时保留后端真实错误原因并维持 `done.success=false` 失败语义，避免前端把配置缺失误当成成功回答。
- Agent 模式未生成有效决策仪表盘时保留本地趋势分析的评分、趋势和操作建议，并将强买/强卖 fallback 归一到兼容的 `buy`/`sell` 决策类型，避免首页结果被 `50 / 观望 / 未知` 缺省值覆盖。
- 持仓快照现价缺失时不再静默回退为持仓成本；当天快照优先使用历史收盘价，仅在缺失时使用实时价 fallback，缺价持仓不再污染市值与未实现盈亏汇总，并为持仓明细返回价格来源、日期、stale 与缺价状态。
- 分析 Prompt 在注入 `trend_analysis` 前按最终 `trend_status` / `ma_alignment` 清洗互斥理由：空头结构移除看多理由、多头结构移除空头结构风险，并在事件/技术冲突与异常放量（>10 倍）时强制提示“事件先行、技术待确认”与量能降权。
- LLM 返回非 JSON 响应时同样触发备用模型切换：主模型成功返回但无法解析 JSON 时，不再立即降级为纯文本 fallback，而是依次尝试 `LITELLM_FALLBACK_MODELS` 中的备用模型；所有模型均无法返回合法 JSON 时，再降级为文本 fallback。
- LiteLLM 内部 DEBUG 日志默认压低到 WARNING，避免流式生成时 token 级日志污染 `stock_analysis_debug_*.log`；如需排查 LiteLLM 内部细节，可临时设置 `LITELLM_LOG_LEVEL=DEBUG`（Fixes #1156）。

### 文档

- 补充 LLM 配置指南与 FAQ，明确问股 Agent 对 `LITELLM_CONFIG` / `LLM_CHANNELS` / legacy `GEMINI_*` `OPENAI_*` `ANTHROPIC_*` 的兼容优先级、回退路径与“不静默迁移旧配置”的结论。

### 测试

- 新增 `tests/test_bot_market_command.py`，覆盖 `MARKET_REVIEW_REGION=both` + open markets `{"cn","us"}` / `{"cn","hk"}` 的 `override_region` 透传断言，并覆盖全市场休市跳过与关闭交易日检查路径；新增 `tests/test_yfinance_hk_indices.py` 覆盖港股指数符号映射与部分/全部失败降级路径。
- 补齐 `task_queue` 轻量导入 stub 的股票代码规范化函数，恢复 `tests/test_task_queue_config_sync.py` 收集与运行。

## [3.14.1] - 2026-04-26
- [测试] 修正大盘复盘 prompt 测试对“明日交易计划”标题的断言，并同步桌面端版本号，恢复发布 gate。

## [3.14.0] - 2026-04-26

### 发布亮点

- 📊 **大盘复盘升级为盘后工作台式结构** — A 股复盘固定输出盘面温度、指数明细、板块 Top 表、新闻催化、明日交易计划和风险提示，减少纯文字复盘的重复与空泛。
- 🖥️ **桌面端新增 GitHub Release 更新提醒** — Windows/macOS 桌面端启动后自动检测新版本，也可从设置页手动检查并跳转下载页。
- 🤖 **Pipeline Agent 数据加载大幅降噪** — K 线工具改为 DB-first 并预热 240 天历史数据，避免同一只股票重复 HTTP 请求。
- 🐳 **Docker 发布链路整理** — 发布工作流收敛为正式发布与手动补发两条路径，官方 Docker Hub 镜像名统一为 `zhulinsen/daily_stock_analysis`。
- 🔧 **LLM 渠道与 DeepSeek V4 配置补强** — GitHub Actions 定时分析补齐多渠道变量透传，DeepSeek 官方渠道预设与示例同步到 V4。
- 🧩 **桌面端静态资源一致性校验** — 打包链路和运行时都能更早发现静态资源错配，降低 Release 包白屏排查成本。

### 新功能

- 🏠 **Web 首页历史报告区新增重新分析入口** — 支持基于原始 prompt 重做同一只股票同日期的分析。
- 🖥️ **Windows/macOS 桌面端新增 GitHub Release 更新提醒** — 启动后自动检测新版本，并支持从设置页手动检查后跳转下载页。

### 改进

- 📊 **A 股大盘复盘报告改为结构化盘后工作台版式** — 固定输出盘面温度、指数明细、板块 Top 表、新闻催化和明日交易计划。
- 🐳 **Docker 发布工作流收敛** — 更清晰地区分正式发布与手动补发链路，并统一官方 Docker Hub 镜像名为 `zhulinsen/daily_stock_analysis`。
- 🤖 **Agent 日线工具优先复用本地缓存** — 同时持久化新获取的日线与新闻情报，减少重复数据源调用。

### 修复

- 🤖 **Pipeline Agent K 线工具 DB-first 加载** — `get_daily_history` / `analyze_trend` / `calculate_ma` / `get_volume_analysis` / `analyze_pattern` 改为优先读取本地 DB，消除同一只股票 9x5=45 次重复 HTTP 请求（Fixes #1066）。
- 🤖 **Pipeline Agent 执行前按需预热 240 天 K 线历史到 DB** — 正常情况下 K 线工具调用无需重复网络请求。
- 🕒 **冻结 `target_date` 并通过 ContextVar 透传到 Pipeline Agent K 线工具线程** — 消除跨收盘边界时间漂移。
- 🪟 **Windows 桌面端后端日志转抄编码修复** — 转抄 stdout/stderr 时优先使用 UTF-8，并兼容本地代码页回退，避免中文日志乱码。
- ⚙️ **GitHub Actions 每日分析工作流补齐 LLM 渠道变量透传** — 支持 `LLM_CHANNELS`、多 Key 与常用 `LLM_<NAME>_*`，避免本地可用的多模型配置在云端定时任务中失效（Fixes #1063, #872）。
- 📈 **历史报告详情接口修正 `change_pct` 取值** — 使用 `is None` 判断避免把 0.0（平盘）当作缺失值丢弃，移除错误的 `change_60d` 兜底，并在缺失时回退到原始实时行情字段（Fixes #1084）。
- 🔧 **DeepSeek 官方渠道预设与示例配置同步到 V4** — 保留 legacy `deepseek-chat` 默认值并增加废弃提示，同时修正模型发现后旧运行时选择导致保存失败的问题（Fixes #1108, #1109）。
- 🧩 **桌面端打包链路新增静态资源一致性检查** — `scripts/check_static_assets.py` 会在源 `static/` 与 PyInstaller 产物中校验 `index.html` 引用的资源是否真实存在，运行时也会在错配时写入明确日志，避免重现 Release 包打开后白屏（Refs #1064 / #1065 / #1050）。
- 🧩 **后端 `/assets/*` 改为显式路由托管** — 资源缺失时返回与请求扩展名匹配的 `text/javascript` / `text/css` 404，减少默认 JSON 错误响应带来的排查误导（Refs #1064）。
- 🌙 **`kimi-k2.6` 自动使用固定温度** — 主分析、大盘复盘和 Agent 调用该模型时自动使用 `temperature=1.0`，避免模型拒绝默认温度请求（Fixes #1102）。

### 文档

- 🐳 **补充官方 Docker 镜像使用说明** — 增加镜像拉取、`docker run` 用法与 `.env` / 数据目录映射说明，不再只覆盖 Compose 部署路径。
- 📨 **修正飞书自定义机器人 Webhook 示例** — `feishu_sender.py` 中的示例改为 interactive card JSON，并补充飞书自动化 Webhook 触发器配置教程。
- 📚 **优化根 README 结构** — 保留首页级功能特性、技术栈、快速开始、推送效果、Web、Agent、赞助商和新闻源入口，将细配置、交易纪律和基本面语义收口到完整指南，并将 Docker 徽章指向官方镜像页。
- 🌐 **同步英文与繁中 README 的精简入口结构** — 同时补齐完整指南中的 LLM 用量 API 与持仓管理说明。
- 🤝 **调整 AI 协作与 PR 模板中的 README 维护规则** — 明确 README 非必要不更新，细节优先进入专题文档。

### 测试

- 🧪 **稳定市场复盘相关测试的 LiteLLM stub 行为** — 避免本机安装的 LiteLLM 在测试收集顺序变化时影响市场复盘单元测试。
- 🧪 **pytest 默认跳过前端依赖目录** — 本地存在 `apps/dsa-web/node_modules` 时不再被后端测试递归扫描，避免发布前 gate 被无关目录拖慢。

## [3.13.0] - 2026-04-21

### 发布亮点

- 🌉 **长桥 OpenAPI 数据源接入** — 美股/港股行情优先使用 Longbridge，YFinance / AkShare 自动兜底；未配置时行为不变。
- 📈 **Tushare 港股全链路扩展** — 港股日线通过 `hk_daily` 获取；筹码分布对港股返回 `None`；换算单位跟随港股口径，不再套用 A 股手/千元规则。
- 🔍 **Anspire Search 语义搜索接入** — 配置 `ANSPIRE_*` 后即可使用 Anspire Search 获取实时行情及资讯，未配置时完全透明。
- 🚀 **普通分析链路支持 LLM 流式生成** — 首页任务 SSE 新增 `task_progress` 事件，进度更细化；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web 渠道编辑器支持按需拉取可用模型列表** — `/v1/models` 统一模型发现入口，多选写回 `LLM_{CHANNEL}_MODELS`，拉取失败时保留手动输入降级。
- 🛡️ **Agent 稳定性与预算护栏全面补强** — `AGENT_MAX_STEPS` 语义统一、技能降级不中断管线、SSE 异常透传、技能加载 warning 日志补齐。
- 🛠️ **SQLite 写入链路原子化** — 批量原子 upsert + WAL + `busy_timeout` + 有限写入重试，显著降低批量分析并发锁竞争。

### 新功能

- 🌉 **集成 Longbridge OpenAPI 作为美股/港股可选数据源**（fixes #981）— 配置 `LONGBRIDGE_*` 后优先使用长桥获取日线与实时行情，YFinance / AkShare 兜底；未配置时行为与此前一致。联调使用 `tests/longbridge_live_smoke.py`（手动脚本，不参与 pytest 收集）。
- 📈 **Tushare 支持港股日线查询** — 配置 Tushare 凭证后调用 `hk_daily` 接口获取港股数据；权限不足时抛出异常，与原流程一致。
- 🔍 **集成 Anspire Search 可选语义搜索后端** — 配置 `ANSPIRE_*` 可使用 Anspire Search 获取实时行情及新闻资讯；未配置时行为与此前一致。联调使用 `tests/test_anspire_search.py`（手动脚本）。
- 🚀 **普通分析链路支持 LiteLLM 流式生成与更细任务进度** — 股票分析在 LLM 阶段优先尝试 `stream=True` 并在服务端累积 chunk，首页任务 SSE 新增 `task_progress` 事件与更细的 `message/progress` 更新；仅在最终 JSON 解析成功后持久化历史报告；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web AI 模型配置支持按渠道获取可用模型列表** — 渠道编辑器支持调用 `/v1/models` 拉取可用模型，并以多选方式写回 `LLM_{CHANNEL}_MODELS`；拉取失败时保留手动输入作为降级路径。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；仅对极少数高位且摘要不足的结果做延迟补抓，优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案统一为"主模型 / Agent 主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键保持兼容。
- 🧠 **IntelAgent 新增公司公告搜索与主力资金流工具** — 增加上交所/深交所/cninfo 公告搜索维度与 `get_capital_flow` 工具，修复 Agent 模式下公告和资金流数据经常缺失的问题。
- 📦 **后端股票名称解析优先复用 `stocks.index.json`** — 懒加载缓存前端静态索引，纯后端/缺失静态资源场景静默降级回 `STOCK_NAME_MAP` 与原有数据源回退链路。
- 📊 **TushareFetcher 港股单位适配** — `get_chip_distribution` 对港股直接返回 `None`（港股暂不支持筹码分布）；`_normalize_data` 对港股（`hk_daily`）不再做 A 股手→股、千元→元的缩放，与 Tushare 港股字段语义一致。
- ⏱️ **Agent 超步数错误增加 `AGENT_MAX_STEPS` 调整提示** — 帮助用户自助排查步数限制问题。
- ⚙️ **GitHub Actions 分析任务超时支持 `vars` 配置** — `daily_analysis.yml` 任务超时从 repository variables 读取，无需修改代码即可调整运行超时上限（fixes #1014）。

### 修复

- 📣 **大盘复盘链路接入 `REPORT_LANGUAGE`** — `REPORT_LANGUAGE=en` 时，A 股/合并复盘的 Prompt、章节标题、模板兜底文案与通知包装标题统一输出英文，避免英文正文搭配中文标题的混排问题。
- 📈 **EfinanceFetcher 指数开盘价映射兼容**（fixes #1043）— `get_main_indices()` 的开盘价映射改为兼容 `今开 → 开盘 → open`，修复部分 efinance 版本下指数开盘价被读成缺失值的问题。
- 🤖 **AGENT_MAX_STEPS 语义统一**（fixes #1026）— 在 orchestrator 多 Agent 模式下明确为"各子 Agent 步数上限而非硬覆盖"；TechnicalAgent 等高默认值 Agent 会被封顶，低默认值 Agent 保持原值；用户主动调高（>10）时统一覆盖所有子 Agent。修复了用户设置 12 但 TechnicalAgent 仍以默认 6 步运行并报 "Agent exceeded max steps" 的问题。
- 🛡️ **Specialist（Skill）Agent 失败改为优雅降级** — 技能 Agent 失败不再中断整个分析管线，与 intel/risk 保持相同的降级策略。
- 🔧 **MiniMax-M2.7 连接测试修复** — 修复 LLM 通道连接测试在 MiniMax-M2.7 下返回 "Empty response" 的问题；将 `max_tokens` 上限从 8 提升至 256 以容纳思考过程，并添加 `content_blocks` 格式解析逻辑。
- 📊 **移除 `sentiment_score` 范围约束**（fixes #942）— 移除 `HistoryItem` 与 `ReportSummary` 响应 Schema 中 `sentiment_score` 的 `ge=0/le=100` 约束，历史库中存储的超范围值不再触发 Pydantic ValidationError。
- 🖥️ **WebUI 前端资源缺失时发出明确警告** — `webui_frontend.py` 在 `static/index.html` 存在但 `static/assets/` 缺失时发出 warning，避免 CSS/JS 资源缺失导致页面异常变大却无从排查（fixes #944）。
- 🔗 **分析管线可选服务降级初始化** — `StockAnalysisPipeline` 搜索服务与社交舆情服务任一初始化异常时，记录 warning 并以禁用状态继续运行，避免外部依赖抖动阻塞主分析链路。
- 🖥️ **桌面端版本展示统一读取 `package.json`** — 统一读取 `apps/dsa-desktop/package.json`，移除 preload 中硬编码的 `0.1.0`，设置页展示真实桌面端版本；修复版本号显示错误（fixes #1048）。
- 🐋 **港股名称获取失败修复**（fixes #940）— 修复主数据源字段缺失时无法正确回退到备用字段获取港股名称的问题。
- 🔄 **SSE 任务流断开时 `CancelledError` 正确 re-raise**（fixes #967）— 修复 SSE 流中断时异常被静默吞掉导致故障无日志可查的问题。
- 🔄 **Agent SSE 清理阶段后台任务异常正确上报**（fixes #969）— 流结束时后台执行器异常现在正确记录并上报，避免错误无法感知。
- 🔇 **技能加载异常补充 `logger.warning` 日志**（fixes #970）— 在 `ask.py`、`skills/aggregator.py`、`skills/router.py` 的静默 except 块补充日志，确保技能列表为空时有日志可查。
- 🛠️ **SQLite 写入链路原子化**（fixes #878）— `stock_daily(code,date)` 使用批量原子 upsert；文件型 SQLite 连接默认启用 WAL + `busy_timeout` + 有限写入重试；"新增数"改按本次真正插入窗口计算。
- 💰 **多 Agent / 单 Agent 预算护栏语义统一** — 剩余预算低于最小阈值时主动跳过并降级；已完成阶段可构建降级报告时返回 `success=True` 并携带非空内容，否则返回 `success=False`。
- ⚙️ **GitHub Actions `daily_analysis.yml` 补齐 `REPORT_LANGUAGE` 注入**（fixes #1013）— 修复用户在 Secrets/Variables 中配置 `REPORT_LANGUAGE` 后不生效的问题。
- 📊 **任务状态 API 补齐实时价格字段**（fixes #983）— `GET /api/v1/analysis/status/{task_id}` 从数据库回填已完成任务时补齐 `current_price` / `change_pct`，修复首页报告股票名旁不显示实时价格的问题。
- 📅 **非交易日数据返回最近交易日**（fixes #1009）— 修复非交易日（周末/节假日）筹码分布与板块排行返回倒数第二个交易日数据的问题，现在正常返回最近交易日数据。
- 🔍 **A 股资讯搜索恢复中文优先** — `search_stock_news()` 在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面；非美股查询不再默认沿用 Brave 的 `en/US` 区域语言偏好。
- 📨 **飞书群机器人通知支持签名校验** — 飞书通知现在支持 `FEISHU_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_KEYWORD`；Web 设置与文档明确区分 Webhook 推送模式和 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 应用模式，降低误配风险。
- ⚡ **LLM 适配层新增 `RateLimitError` 和 `ContextWindowExceeded` 检测** — 识别并处理速率限制与上下文窗口超出错误，提升分析链路在高负载或长文本场景下的健壮性（fixes #1002）。

### 测试

- 🧪 **TushareFetcher 港股相关单元测试** — 新增 `get_chip_distribution` 筹码分布获取与 `_normalize_data` 港股/A 股/ETF 单位处理的单元测试，覆盖港股特殊路径。

### 文档

- 📘 **DEPLOY.md 补充 UI 元素异常变大排查步骤** — 新增重建 Docker 镜像或手动执行 `npm run build` 的排查指南；`deploy-webui-cloud.md` 同步更新。
- 📨 **飞书 Webhook 配置说明补全** — 强调 `FEISHU_WEBHOOK_URL` 是群通知必填项、签名校验须两端同时启用或关闭、`FEISHU_APP_SECRET` 仅用于应用/Stream Bot 模式；`.env.example` 补充内联注释；同步英文指南。
- 🤝 **FAQ 补充 Ollama 连接失败排障条目（Q12c）** — 覆盖服务未启动、URL 配置错误、模型前缀缺失、模型未下载、远程防火墙等 5 个检查点（fixes #854）。
- 🌉 **README 补充长桥数据源使用说明** — 中/英/繁 README 明确长桥"首选 / 兜底 / 未配置不调用"边界；`docs/` 内相对路径链接修复；`LONGBRIDGE_PRINT_QUOTE_PACKAGES` 配置与代码及 `.env.example` 对齐。
- 🐋 **Docker 安装场景版本说明** — 补充最小化文档，明确 Docker 安装场景下应以 Git tag / 镜像 tag 判断版本（fixes #1091）。

## [3.12.0] - 2026-04-01

### 发布亮点

- 📊 **回测页新增"次日验证"视图** — 可按股票与日期范围查看 AI 预测 vs 次日实际涨跌，复用历史分析与 1 日回测结果，快速验证分析准确率。
- 🔧 **LLM 接入体验简化** — 用户侧文案统一收口为"主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有配置键保持兼容。
- 🐳 **Docker / WebUI 运行时稳态补强** — 修复系统设置保存后配置不生效、启动早期日志缺失、预构建静态资源复用等问题，降低容器化部署的运维摩擦。
- 🔒 **安全与并发稳定性同步增强** — Discord 入站 Webhook 补齐 Ed25519 验签，修复并发执行时共享状态未加锁、单股推送模式通知并发复用等问题。
- 🖥️ **桌面端与定时任务细节打磨** — Windows 安装器支持自选安装目录，内置定时调度器感知运行中 SCHEDULE_TIME 变更，断点续传改按市场时区判断。

### 新功能

- 📊 **回测页新增"次日验证 / 1 日窗口"视图** — 可按股票代码与分析日期范围查看 AI 预测、次日实际涨跌及筛选区间准确率，复用历史分析与 1 日回测结果实现。
- 🏷️ **Web 设置页新增版本信息卡片** — `apps/dsa-web` 现在会在构建时注入前端包版本与构建时间，系统设置页新增只读"版本信息"区块，展示 `WebUI 版本 / 构建标识 / 构建时间`；当 `package.json` 仍为占位版本 `0.0.0` 时，会自动回退为构建标识，方便 Docker 重建后快速确认当前静态资源是否已经生效。
- 🪟 **Windows 桌面安装器支持自选安装目录** — 安装器改为支持在安装向导中自定义安装目录，安装到非默认盘符后仍沿用现有打包态目录逻辑在安装目录旁读写 `.env`、`data/stock_analysis.db` 和 `logs/desktop.log`，同时保留 `win-unpacked` 免安装分发方式。安装器仅支持当前用户安装、已禁用管理员提权（`allowElevation: false`），并通过 NSIS `.onVerifyInstDir` 阻止选择系统保护目录。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；现在仅对极少数高位且摘要明显不足的结果，在更短超时预算内做延迟补抓，并优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案已统一收口为"主模型 / Agent 主模型 / 备选模型 / 模型渠道 / 高级模型路由配置"；Web 设置页、配置元数据、校验提示与中英文文档不再把 LiteLLM 当作普通用户默认必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键仍保持兼容。

### 修复

- 🚀 **启动早期失败时暴露真实根因** — `python main.py` 现在通过 stderr 暴露真实根因，bootstrap 阶段不再向硬编码 `logs/` 目录写入文件日志，文件日志推迟到 `config.log_dir` 可用后创建，避免健康启动在非预期路径残留日志文件。
- 🐳 **Docker WebUI 运行时优先复用预构建静态资源** — `prepare_webui_frontend_assets()` 现在会先检查镜像内已有的 `static/index.html` 是否可直接复用；当容器运行时不包含 `apps/dsa-web` 源码目录且未安装 `npm` 时，也不会误报"未找到前端项目，无法自动构建"，从而恢复 Docker 部署后的 WebUI 打开能力。
- 🐳 **Docker WebUI 系统设置保存后配置生效** — Docker 场景下 WebUI 保存 `STOCK_LIST`、`SCHEDULE_ENABLED`、`SCHEDULE_TIME`、`SCHEDULE_RUN_IMMEDIATELY`、`RUN_IMMEDIATELY` 后，`Config` 会优先读取持久化 `.env` 中的新值，避免被容器创建时注入的旧环境变量覆盖。
- 📈 **市场复盘 LLM max_tokens 提升** — 市场复盘生成链路将 LLM `max_tokens` 从 `2048` 提升到 `8192`，降低长复盘输出因 `MAX_TOKENS` 提前截断导致内容未完成的概率。
- ⏰ **内置定时调度器感知 SCHEDULE_TIME 运行时变更** — 调度器现在会在运行中感知 WebUI 保存后的 `SCHEDULE_TIME` 变化，并在下一轮检查时重绑 daily job。
- 🪟 **Windows Release 渠道编辑器保留 MiniMax 模型前缀** — 渠道模式下填写 `minimax/<模型名>` 时，后端归一化与 Web 设置页运行时模型列表都会保留该值原样，不再误改写成 `openai/minimax/<模型名>`。
- 🤖 **Discord 入站 Webhook 补齐 Ed25519 验签** — `DiscordPlatform` 现在会基于 `X-Signature-Ed25519`、`X-Signature-Timestamp` 和原始请求体校验 Discord Interaction 签名；缺失签名头、公钥格式非法或签名不匹配时直接拒绝请求，同时对 timestamp 做 ±5 分钟时效窗口校验以防御重放攻击。
- ⚙️ **STOCK_GROUP_N / EMAIL_GROUP_N 配置关系明确化** — 明确与 `STOCK_LIST` 的关系，并在配置校验中对超出 `STOCK_LIST` 的邮件分组给出 warning。
- 🗓️ **断点续传改按市场时区和交易日历判断**（fixes #880）— 股票数据存在性检查不再直接使用服务器自然日，而是按 A 股 / 港股 / 美股各自市场时区解析"最新可复用交易日"。
- 📨 **单股推送模式不再并发复用共享通知实例** — `StockAnalysisPipeline.run()` 现在会保留个股分析并发，但把 `SINGLE_STOCK_NOTIFY=true` 下的即时通知挪到结果收集侧串行发送。
- 🔇 **实时行情降级提示收口为单次告警** — 分析主流程获取股票名称时不再提前触发一次实时行情查询，只有在全部数据源都不可用时才提示已降级为历史收盘价继续分析。
- 🔍 **A 股中文资讯搜索恢复中文优先** — `search_stock_news()` 现在会在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面。
- 🔒 **并发执行时共享状态补齐统一加锁** — 修复并发执行时共享状态缺少统一加锁的问题，避免多线程场景下的数据竞争。

### 测试

- 🧪 **补充设置页版本信息回归测试** — 新增 Web 设置页版本信息渲染断言，并覆盖占位版本 `0.0.0` 自动回退为构建标识的逻辑。
- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

## [3.11.0] - 2026-03-27

### 发布亮点

- 🎨 **Web 工作台完成一轮 UI 统一与双主题升级** — 首页、问股、回测、持仓和设置页进一步收口到统一设计 token、输入表面和状态表达；新增完整浅色主题，并支持浅色 / 深色一键切换与持久化保存。
- 🤖 **Bot / Agent 能力重新补回主分支** — 恢复 `/history`、`/strategies`、`/research` 等命令，`/ask` 继续支持多股对比与组合视角；Deep Research、事件监控与 schedule 轮询链路重新接回主线能力。
- 🔒 **安全性与运行稳态同步补强** — 修复 `X-Forwarded-For` 限流绕过风险，恢复 LiteLLM 官方 PyPI 安装路径，Tushare 初始化不再依赖本地 SDK，降低 Docker、桌面打包和环境重建时的脆弱点。
- 🖥️ **日常使用细节继续打磨** — 修复首页港股自动补全提交、登录页首屏主题闪烁、历史长股票名重叠，以及 Telegram Markdown 解析失败时整条通知发送中断等问题。

### 新功能

- 🎨 **全新浅色主题与双主题切换上线** — Web 工作台新增完整浅色主题，并支持在侧边栏中一键切换浅色 / 深色模式；主题选择会持久化保存，刷新页面后仍保持当前偏好。此次升级不是局部配色微调，而是对卡片层级、边界对比、输入表面、状态提示和页面背景做了一整套 light theme 重绘。
- 🤖 **补回主分支缺失的 Agent / Bot 能力** — `#648` / `#649` 已重新补回 `main`：Bot 恢复 `/history`、`/strategies`、`/research`，`/ask` 保留多股对比与组合视角；Deep Research 与 Event Monitor 的配置重新在 Web 设置页可见并可编辑，schedule 模式也重新接入事件告警轮询。

### 改进

- 🖥️ **核心页面统一到同一套工作台视觉语言** — `Home / Chat / Backtest / Portfolio / Settings` 进一步收口到共享设计 token、`input-surface` 输入体系、空态/错误态表达和抽屉遮罩语义，减少页面之间的视觉割裂与局部私有样式漂移。
- 💬 **问股交互可达性与反馈增强** — 问股页补强了会话导出、通知发送、消息复制、历史删除与追问上下文提示；AI 回复操作不再过度依赖 hover，触屏设备和小屏场景下也能直接触达关键按钮。
- 📊 **回测与持仓页表面和状态表达继续标准化** — 回测页筛选控件、布尔状态、结果表格与汇总卡片统一到共享输入/状态原语；持仓页的导入反馈、汇率刷新提示、空态与警示信息进一步归口到共享组件，减少页面级重复实现。
- 🧭 **导航与页面壳层协同优化** — 侧边栏主题切换、问股完成角标、移动端抽屉遮罩和主内容滚动契约进一步统一，首页、问股和回测在桌面端与移动端的切页体验更稳定。

### 测试

- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

### 修复

- 🌗 **Web 首屏默认主题预设为深色** — `apps/dsa-web/index.html` 现在会在 React 挂载前读取本地保存的主题偏好；若没有已保存值，则立即给 `<html>` 预设 `dark` 并同步 `color-scheme`，避免首页和登录页首屏先闪出浅色主题。
- 🔐 **登录页独立主题层收口** — 登录页输入框、标签、切换按钮和按钮文案现在使用独立的 `--login-*` 视觉 token，不再继承全局浅/深主题文字色；即使浏览器缓存了浅色主题，登录页仍保持稳定的深色视觉与青色密码输入表现，避免密码圆点和文案落成黑色。
- 🖥️ **首页港股代码输入修复** — Web 首页分析输入框现在可正确接受港股代码与自动完成选中的港股项，补齐 `00700.HK` / `HK00700` 等格式识别，避免提交时误报“请输入有效的股票代码或股票名称”。

- 🔒 **认证限流 X-Forwarded-For 取值修复（CWE-345）**（#841 / #842）— `get_client_ip()` 从取 `X-Forwarded-For` 最左值改为最右值，防止攻击者通过伪造首部旋转限流桶绕过暴力破解保护；仅影响 `TRUST_X_FORWARDED_FOR=true` 且单层可信反向代理的部署场景，多级代理环境需按部署文档评估配置。
- 📦 **恢复 LiteLLM 官方 PyPI 安装并锁定安全上限** — `requirements.txt` 重新使用 `pip install litellm` 的官方 PyPI 安装路径，并在保留历史最低要求 `>=1.80.10` 的同时增加 `<1.82.7` 的安全上限，避免误装已被移除的 `1.82.7` / `1.82.8` 风险版本；Windows 桌面打包脚本也同步回退到标准 `pip install -r requirements.txt` 链路，减少特殊下载分支带来的维护成本。
- 📨 **Telegram Markdown 解析失败回退纯文本**（fixes #850）— `src/notification_sender/telegram_sender.py` 现在会在 Telegram 返回 `HTTP 400` 且包含 `can't parse entities` / Markdown 解析错误时，自动去掉 `parse_mode` 后重试纯文本发送，避免 `*ST` 等正文内容直接导致整条通知失败。
- 🔢 **A 股同码实时行情保留交易所提示**（fixes #852）— `DataFetcherManager` 与 `TushareFetcher` 现在会保留 `SZ000001` / `000001.SZ` 这类显式沪深提示，旧版 Tushare 实时行情降级分支不再把深市 `000001` 误判成 `sh000001` 上证指数。
- 🎯 **多 Agent 次优买点不再盲目复制理想买点**（fixes #851）— 当多智能体结果缺少独立 `secondary_buy` 时，仪表盘现在优先展示 `N/A` 而不是把 fallback 值硬拷贝成与 `ideal_buy` 完全相同，减少误导性的双买点展示。
- 🧩 **Tushare 初始化不再强依赖本地 SDK 包** — `TushareFetcher` 现在直接使用内置 HTTP client 访问 Tushare Pro，不再在启动阶段先 `import tushare` 才能初始化；修复了 Docker、桌面打包或环境重建后因缺少 `tushare` 包而提前报 `No module named 'tushare'` 的问题，并补充对应回归测试。
- ⚙️ **`daily_analysis` 工作流补齐 `DEEPSEEK_API_KEY` 映射** — GitHub Actions 每日分析工作流现在会正确透传 `DEEPSEEK_API_KEY`，避免云端任务配置了密钥却在运行时拿不到对应环境变量。
- 🖥️ **历史列表过长股票名称截断与悬停展示**（fixes #815）— 历史列表中过长的股票名称, 现在会按字符类型自动截断（英文15/中文8/混合10字符），默认显示截断结果，悬停时展示完整名称；解决 1920x1080 分辨率下股票名称与右侧状态标签文字重叠的问题。新增 `stockName.ts` 工具函数并补充对应测试。

### 文档

- 🧾 **README 捐赠入口更新为小红书二维码** — README 及中英文说明中的赞助入口更新为小红书二维码素材，保持展示口径一致。

## [3.10.1] - 2026-03-24

### 新功能

- 🔔 **Web 端分析推送通知开关**（#808）— 首页分析按钮旁新增「推送通知」复选框，默认勾选；取消勾选时本次分析不发送 Telegram/企业微信等推送。API `POST /api/v1/analysis/analyze` 新增 `notify` 字段（`bool`，默认 `true`），不传时行为与修改前一致，Bot 和定时任务不受影响。

### 改进

- 🖥️ **问股 / 回测页面布局与壳层协同优化** — 统一 Chat / Backtest 页面容器、共享 UI 状态和跟随问答交互路径，移除部分硬编码高度限制，让导航框架内的填充与滚动行为更连贯。
- 🎨 **全局视觉与共享组件继续收敛** — Light theme 引入动态 HSL 阴影体系，统一侧边栏激活态、告警组件对比度和聊天气泡样式，并把部分零散内联样式收口为语义化 CSS 变量，提升一致性与可维护性。

### 修复

- 🖼️ **系统设置智能导入文件选择恢复** — 修复了“系统设置 > 基础设置 > 智能导入”模块中 “选择图片 / 选择文件” 两个按钮点击无响应的问题。
- 🖥️ **移动端滚动与交互层级修复** — 解决主题切换菜单在移动端被主内容遮挡的 z-index 冲突，并恢复首页长报告场景下的正常纵向滚动，不影响其他页面现有滚动行为。
- 🧾 **Markdown 纯文本复制清洗增强** — 改进纯文本导出算法，复制分析报告时会更稳定地清除表格分隔符等 Markdown 痕迹，提升分享和归档内容的纯净度。
- 🧠 **Trading philosophy injection 覆盖 legacy + Agent 全链路**（#810）— `GeminiAnalyzer`、单 Agent 模式和 skill-aware Prompt 现在共享同一套策略注入状态；只有隐式回落到内置默认 `bull_trend` 时才保留旧的趋势型提示，显式策略选择或自定义默认 skill 不再被偷偷叠加 `MA5>MA10>MA20` 多头基线。
- 🛠️ **后端 CI 依赖安装链路稳态化**（#835）— 拆分 backend gate 阶段、为依赖安装增加重试，并把 CI 用的 `litellm` 安装来源调整为更稳定的 GitHub 源，降低依赖解析抖动导致的 backend gate 偶发失败。
- 🪟 **Windows 桌面发版构建恢复 LiteLLM 安装兼容性** — `scripts/build-backend.ps1` 现在会先过滤 `requirements.txt` 中的 LiteLLM GitHub 源包，再下载对应 tag 的 zipball 到本地移除上游可选 `enterprise/` 目录后安装，绕过 Windows runner 上 Poetry 构建 wheel 时把目录误当文件打包导致的失败；同时补上 `pip install` 退出码检查，避免依赖安装失败后只在后续 `python-multipart` 校验阶段才暴露成次生报错。

### 测试

- 🧪 **问股 / 回测 / 智能导入回归覆盖补齐** — 同步更新 E2E 冒烟期望，补充 `DashboardStateBlock`、Chat 页、智能导入文件选择与相关交互回归断言，确保近期 UI 调整后的关键路径仍可稳定通过。

## [3.10.0] - 2026-03-24

### 发布亮点

- 🔎 **自动补全与索引工具扩展到三市场** — 补全索引生成链路现在同时覆盖 A 股、港股、美股，配套新增 Tushare 股票列表抓取工具与更完整的静态索引数据，让首页搜索入口从“能用”走向“更全、更稳”。
- 🖥️ **Dashboard 与报告查看体验继续收口** — 首页 Dashboard 面板、状态边界、字体层级和完整报告表格密度完成一轮统一；报告详情也补齐了 Markdown/纯文本复制与更可靠的按钮交互，减少历史报告查看与分享时的摩擦。
- 🤖 **Agent skill 与市场语义边界更清晰** — skill bundle、默认策略、回测汇总语义和兼容接口进一步收敛；同时分析 Prompt 不再默认写死 A 股上下文，美股和港股分析也能按各自市场规则生成更贴切的内容。
- ⏰ **定时与桌面配置能力更贴近真实使用场景** — 桌面端支持 `.env` 导入导出；`python main.py --schedule --stocks ...` 也不再把启动时股票快照错误带入后续计划执行，定时任务会跟随最新保存的 `STOCK_LIST`。
### 新功能

- 💾 **桌面端 `.env` 备份/恢复入口**（#754）— 桌面模式下的系统设置页新增 `导出 .env` / `导入 .env` 按钮，可直接备份当前已保存配置，或把备份文件中的键值合并恢复到当前桌面端 `.env`；导入沿用现有 `config_version` 冲突保护与运行时重载链路，不改变现有桌面端便携模式路径。
- 📊 **Tushare 股票列表获取工具** — 新增 `scripts/fetch_tushare_stock_list.py`，支持从 Tushare Pro 获取 A股、港股、美股列表信息并保存为 CSV，配有分页读取、智能限流、错误处理和进度提示；新增对应使用文档 `docs/TUSHARE_STOCK_LIST_GUIDE.md`。
- 🔎 **索引生成脚本多市场支持** — `generate_index_from_csv.py` 重构为支持 Tushare 和 AkShare 双数据源，同时覆盖 A股、港股、美股三个市场；新增按市场分类的别名映射（A股、港股常见别名，美股常用股票英文缩写）；添加 `--source` 参数切换数据源、`--test` 参数验证模式；严格过滤美股 DUMMY 记录。
- 🔎 **索引生成脚本增强** — `generate_stock_index.py` 新增 `--test`/`-t` 测试模式和 `--verbose`/`-v` 详细输出模式，添加市场分布统计，优化 JSON 输出格式。
- 📋 **首页完整报告支持双模式复制** — 历史报告详情头部新增“复制 Markdown 源码”和“复制纯文本”工具按钮；前者保留原始 Markdown 结构，后者去除常见 Markdown 格式符号，方便分享、归档和跨报告比对。复制按钮文案会跟随 `REPORT_LANGUAGE` 保持中英文一致，避免英文报告页出现中文固定文案。
- 🧩 **个股分析页补齐关联板块展示**（#669）— A 股分析写路径现在会把 `belong_boards` 一次性写入 `fundamental_context` / `fundamental_snapshot`，结构化报告详情同步新增 `belong_boards` 与 `sector_rankings` 字段，Web 个股分析页首屏可直接展示所属板块及其是否命中当日板块涨跌榜；无数据时保持 fail-open 隐藏，不影响现有分析主流程。

### 改进

- 🖥️ **Dashboard 面板统一化（PR7-2）** — 新增 `DashboardPanelHeader` 和 `DashboardStateBlock` 作为历史、报告、资讯、任务和透明度等面板的通用组件；统一了各面板标题层级、加载/空态/错误态和 CSS 变量 token。
- 🖥️ **HomePage 状态边界收口（PR7-2）** — 引入 `useHomeDashboardState` hook，集中 `stockPoolStore` 状态选取逻辑，移除 `HomePage` 中重复的本地状态派生和回调定义。
- 🧭 **Agent skill 统一到单一配置语义** — Multi-Agent runtime、API、Web chat 和配置元数据统一围绕 `skill` 概念收敛；`/api/v1/agent/skills` 成为主发现入口，`AGENT_SKILL_*` 成为主配置面，内置 skill 元数据也开始声明默认启用、排序优先级、market regime tag 等信息，减少默认策略散落在代码里的隐式耦合。
- 🔎 **自动补全索引数据更新** — 重新生成 `stocks.index.json`，涵盖 A股、港股、美股三个市场，提升自动补全覆盖率。
- 🧾 **Dashboard 字体与完整报告表格密度微调** — 收敛首页侧栏、空状态、历史操作区的字体层级，并将完整 Markdown 报告表格 `th/td` 的内边距调整到更紧凑的 4-6px 区间，让信息密度与现有 Dashboard 视觉节奏更一致。

### 修复

- ⏰ **定时模式不再锁定启动时 CLI 股票快照** — `python main.py --schedule --stocks ...` 现在不会让后续计划执行沿用启动时的旧股票列表；定时任务每次触发前都会重新读取最新保存的 `STOCK_LIST`，确保 WebUI 或 `.env` 更新后的自选股配置能参与后续推送。
- 🌍 **LLM Prompt 按股票市场动态注入上下文** — 分析链路不再把市场规则写死成 A 股；系统 Prompt 会根据股票代码识别 A 股、港股或美股，并注入对应的角色描述与交易规则提示，减少跨市场分析出现口径错位或结论失真的问题。
- 🔎 **美股自动补全复用 ticker 去重** — `generate_index_from_csv.py` 在导入 Tushare `us_basic` CSV 时会先按 `ts_code` 折叠复用的美股 ticker，优先保留更可能仍在使用的记录，避免 `stocks.index.json` 出现重复 `canonicalCode` 后让 Web 自动补全展示历史名称或提交歧义代码。
- 🧾 **Web 报告详情复制交互稳定性修复**（#749）— `ReportDetails` 中“原始分析结果 / 分析快照”的复制按钮补齐可点击层级，避免被下方 JSON 内容覆盖；两个面板的复制提示也改为各自独立，不再出现复制一个后两个按钮同时显示“已复制”的误导反馈。
- 📊 **Agent skill 回测与兼容接口语义收敛** — `get_skill_backtest_summary` 现在要求显式传入 `skill_id`，缺失时返回明确校验提示；仓库尚未持久化真实 skill 级汇总时会返回明确的 unsupported/info 响应，并保留 `normalized` 与 `*_pct` 兼容字段，避免沿用 overall 指标误导 Agent 或用户。
- 🔧 **Skill 默认选择与兼容层行为加固** — `allowed-tools` 会继续仅作为 `SKILL.md` bundle 元数据保留，不再泄露到运行时工具选择；`/api/v1/agent/strategies` 恢复旧 payload 形状；显式传入 `skills: []` 时会清空陈旧上下文；当用户明确选择策略 skill 时不再偷偷叠加默认 bull-trend，而在 `AGENT_SKILLS` 为空时则统一只回落到单一主默认 skill。

### 测试

- 🧪 **Dashboard 组件测试覆盖率扩展（PR7-2）** — 新增 `ReportNews` 和 `TaskPanel` 测试；对 `HistoryList`、`ReportDetails`、`HomePage`、`useDashboardLifecycle` 和 `stockPoolStore` 增强了断言覆盖，包括删除回退、移动端抽屉和任务生命周期等场景。
- 🧪 **多市场索引生成测试补齐** — 新增 `tests/test_generate_index_from_csv.py`，覆盖 Tushare/AkShare 双数据源解析、多市场判断、美股 DUMMY 过滤与重复 ticker 去重等核心路径。
- 🧪 **关联板块写入与 API 契约回归** — 新增 `tests/test_pipeline_related_boards.py`，并补充分析历史与分析接口契约测试，确保 `belong_boards` / `sector_rankings` 只做增量扩展且保持 fail-open。
- 🧪 **定时模式股票列表语义回归测试** — 新增 `tests/test_main_schedule_mode.py`，覆盖定时模式忽略启动时 `--stocks` 快照、单次运行仍保留 CLI 股票覆盖的边界场景。

### 文档

- 📘 **新增 Tushare 股票列表工具文档** — 新增 `docs/TUSHARE_STOCK_LIST_GUIDE.md`，说明股票列表抓取工具的使用方法、数据格式和常见问题。
- 🌍 **补齐定时模式与关联板块的双语说明** — `docs/full-guide.md` / `docs/full-guide_EN.md` 现在明确说明 scheduled mode 会在每次执行前重新读取 `STOCK_LIST`，并同步补充个股关联板块展示能力说明，减少配置预期偏差。
- 🧭 **调整 Agent 术语兼容文案** — README、双语文档、设置页与问股界面继续以“策略”作为用户入口主称呼，同时补充 `skill` 作为内部统一命名，降低迁移期理解成本。

## [3.9.0] - 2026-03-20

### 发布亮点

- 🤖 **模型链路与报告语言更灵活** — Agent 现在可以通过 `AGENT_LITELLM_MODEL` 独立选择模型链路，普通分析与 Agent 报告也可通过 `REPORT_LANGUAGE=zh|en` 输出统一语言，减少“英文内容 + 中文壳子”这类混排问题，并允许团队分别权衡主分析与 Agent 的成本、速度和能力。
- 🔎 **首页分析体验完成一轮闭环优化** — 首页新增 A 股自动补全，支持代码、中文名、拼音和别名检索；同时 Dashboard 状态收口到统一 store，历史、报告、新闻与 Markdown 抽屉的交互更稳定，“Ask AI” 追问也会优先携带当前报告上下文。
- 💬 **通知与检索能力继续外扩** — 新增 Slack 一等通知渠道；SearXNG 在未配置自建实例时可以自动发现公共实例并按受控轮询降级；Tavily 时效新闻链路修复后，严格时效过滤不再错误丢光有效结果。
- 💼 **持仓与市场复盘链路更稳** — A 股 market review 可选接入 TickFlow 强化指数与涨跌统计；持仓账本写入改为串行化以缩小并发超卖窗口；汇率刷新入口和禁用态提示也更加清晰，减少用户误判。

### 新功能

- 🔎 **Web 股票自动补全 MVP** — 首页分析输入框新增本地索引驱动的自动补全，支持股票代码、中文名、拼音和别名匹配；选中候选后会提交 canonical code，并透传 `stock_name`、`original_query`、`selection_source` 到分析请求、任务状态和 SSE 事件；索引加载失败时自动退回旧输入模式，不阻断原有提交流程。同步补充了静态索引加载器、索引生成脚本和前后端契约测试。分阶段进行开发，第一阶段仅支持 A 股。
- 💬 **Slack 一等通知渠道** — 新增 Slack 原生通知支持，同时支持 Bot Token 和 Incoming Webhook 两种接入方式；同时配置时优先使用 Bot API，确保文本与图片发送到同一频道；Bot Token 模式支持图片上传（raw body POST，不使用 multipart）；新增 `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID`、`SLACK_WEBHOOK_URL` 配置项，GitHub Actions 工作流同步补齐对应 Secrets 传递。
- 🌍 **报告输出语言可配置**（Issue #758）— 新增 `REPORT_LANGUAGE=zh|en`，默认 `zh`；语言设置会同步注入普通分析与 Agent Prompt，并覆盖 Markdown/Jinja 模板、通知 fallback、历史/API `report_language` 元数据及 Web 报告页固定文案，避免“英文内容 + 中文壳子”的混合输出。
- 🚀 **Agent 与普通分析模型解耦**（Issue #692）— 新增 `AGENT_LITELLM_MODEL`（留空继承 `LITELLM_MODEL`，无前缀按 `openai/<model>` 归一）；Agent 执行链路与 `/api/v1/agent/models` 的 `is_primary/is_fallback` 标记改为基于 Agent 实际模型链路；系统配置与启动期校验补齐 `AGENT_LITELLM_MODEL` 的 `unknown_model/missing_runtime_source` 检查；Web 设置页新增 Agent 主模型选择并与渠道模式运行时配置同步。
- 🔎 **SearXNG 公共实例自动发现与受控轮询**（#752）— 新增 `SEARXNG_PUBLIC_INSTANCES_ENABLED`，在未配置 `SEARXNG_BASE_URLS` 时默认从 `searx.space` 拉取公共实例列表，并按受控轮询顺序选择实例；同次请求内遇到超时、连接错误、HTTP 非 200 或无效 JSON 会自动切换到下一个实例。已配置自建实例的用户保持原有优先级与语义不变；`daily_analysis` GitHub Actions 工作流也已支持显式透传该开关并在启动日志中展示当前状态。
- 📈 **TickFlow market review enhancement** (#632) — 新增可选 `TICKFLOW_API_KEY`；配置后，A 股大盘复盘的主要指数行情优先尝试 TickFlow；若当前 TickFlow 套餐支持标的池查询，市场涨跌统计也会优先尝试 TickFlow。失败或权限不足时立即回退到现有 `AkShare / Tushare / efinance` 链路；板块涨跌榜回退顺序保持不变。接入层同时适配了真实 SDK 契约：主指数查询按单次请求上限分批拉取，并将 TickFlow 返回的比例型 `change_pct` / `amplitude` 统一转换为项目内部的百分比口径。

### 改进

- **Dashboard state slice and workspace closure** — moved Home / Dashboard state into `stockPoolStore`, consolidated history selection, report loading, task syncing, polling refresh, and markdown drawer handling under a single state slice.
- **Dashboard panel standardization** — kept the current dashboard layout contract stable while unifying history, report, news, and markdown presentation with shared tokens, standardized states, and bounded in-panel scrolling for the history list.
- **Dashboard-to-chat follow-up bridge** — routed “Ask AI” follow-ups through report-context hydration instead of direct cross-page state coupling, while keeping chat sends usable when enriched history context is still loading.
- 💼 **持仓账本并发写入串行化**（#742）— 持仓源事件写入/删除现在会在 SQLite 下先获取串行化写锁，减少并发卖出把超售流水写入账本的窗口；直接持仓写接口在锁竞争时返回 `409 portfolio_busy`，CSV 导入保持逐条提交并把 busy 计入 `failed_count`。
- 💱 **持仓页汇率手动刷新入口补齐**（#748）— Web `/portfolio` 页面现在会在“汇率状态”卡片中展示“刷新汇率”按钮，直接调用现有 `POST /api/v1/portfolio/fx/refresh` 接口；刷新后会仅重载快照与风险数据，并以内联摘要反馈“已更新 / 仍 stale / 刷新失败”的结果，减少用户对 `fxStale` 长时间停留的误解。

### 修复

- 🔎 **Web 自动补全 Enter 提交语义修正** — 股票自动补全在搜索命中候选时不再默认高亮第一项；候选列表展开但用户尚未用方向键或鼠标明确选中时，按 Enter 会继续提交原始输入，避免手动输入被第一条候选静默覆盖。
- 🌍 **补齐 `REPORT_LANGUAGE` 启动解析与历史展示本地化边界** — `Config` 在启动时继续遵循“真实环境变量优先、`.env` 兜底”的既有语义，并在两者冲突时输出显式告警，减少 `REPORT_LANGUAGE` 来源不清带来的误判；同时 `/api/v1/history/{id}` 英文详情响应会同步本地化 `sentiment_label`，历史 Markdown 也会正确识别英文 `bias_status` 的风险等级 emoji，避免出现 `乐观` 或 `🚨Safe` 这类中英混排/误报展示。
- 📰 **Tavily 时效新闻检索发布时间映射修复**（#782）— Tavily 在股票新闻和严格时效的情报维度中现在会显式使用 `topic="news"`，并兼容 `published_date` / `publishedDate` 两种发布时间字段；修复了 Tavily 明明返回结果却在后续硬过滤阶段被全部记为 `drop_unknown` 丢弃的问题，同时将机构分析、业绩预期、行业分析等分析型维度恢复为宽源搜索，不再被统一压缩成新闻模式。
- 💱 **持仓页汇率刷新禁用语义修正**（#772）— 当 `PORTFOLIO_FX_UPDATE_ENABLED=false` 时，`POST /api/v1/portfolio/fx/refresh` 现在会返回显式 `refresh_enabled=false` 与 `disabled_reason`，Web `/portfolio` 页面会明确提示“汇率在线刷新已被禁用”，不再误报“当前范围无可刷新的汇率对”。
- 🤖 **Agent timeout and config hardening** — `AGENT_ORCHESTRATOR_TIMEOUT_S` now also protects the legacy single-agent ReAct loop, parallel tool batches stop waiting once the remaining budget is exhausted, and invalid numeric `.env` values fall back to safe defaults with warnings instead of crashing startup.
- 🌐 **CORS wildcard + credentials compatibility** — `CORS_ALLOW_ALL=true` no longer combines `allow_origins=["*"]` with credentialed requests, avoiding browser-side cross-origin failures in demo/development setups.
- 🧭 **Unavailable Agent settings hidden from Web UI** — Deep Research / Event Monitor controls are now treated as compatibility-only metadata in the current branch and are removed from the Settings page to avoid exposing non-functional toggles.

### 文档

- 新增 Ollama 本地模型配置说明，同步更新 `README.md` 与 `docs/README_EN.md`（Fixes #690）
- 完善 Ollama 配置说明：`docs/full-guide.md` / `docs/full-guide_EN.md` 环境变量表与 Note 补充 `OLLAMA_API_BASE`，避免英文用户误以为 Ollama 不能作为独立配置入口；合并重复的 `OLLAMA_API_BASE` 条目为单一条目
- 明确文档同步治理边界：补充 `README.md`、专题文档、双语文档与交付说明之间的默认同步规则，减少后续文档漂移

## [3.8.0] - 2026-03-17

### 发布亮点

- 🎨 **Web 界面完成一轮骨架升级** — 新的 App Shell、侧边导航、主题能力、登录与系统设置流程已经串成统一体验，桌面端加载背景也完成对齐。
- 📈 **分析上下文继续补强** — 美股新增社交舆情情报，A 股补齐财报与分红结构化上下文，Tushare 新接入筹码分布和行业板块涨跌数据。
- 🔒 **运行稳定性与配置兼容性提升** — 退出登录会立即让旧会话失效，定时启动兼容旧配置，运行中的 `MAX_WORKERS` 调整和新闻时效窗口反馈更清晰。
- 💼 **持仓纠错链路更完整** — 超售会被前置拦截，错误交易/资金流水/公司行为可以直接删除回滚，便于修复脏数据。

### 新功能

- 📱 **美股社交舆情情报** — 新增 Reddit / X / Polymarket 社交媒体情绪数据源，为美股分析提供实时社交热度、情绪评分和提及量等补充指标；完全可选，仅在配置 `SOCIAL_SENTIMENT_API_KEY` 后对美股生效。
- 📊 **A 股财报与分红结构化增强**（Issue #710）— `fundamental_context.earnings.data` 新增 `financial_report` 与 `dividend` 字段；分红统一按“仅现金分红、税前口径”计算，并补充 `ttm_cash_dividend_per_share` 与 `ttm_dividend_yield_pct`；分析/历史 API 的 `details` 追加 `financial_report`、`dividend_metrics` 可选字段，保持 fail-open 与向后兼容。
- 🔍 **接入 Tushare 筹码与行业板块接口** — 新增筹码分布、行业板块涨跌数据获取能力，并统一纳入配置化数据源优先级；默认按上海时间区分盘中/盘后交易日取数，优先使用 Tushare 同花顺接口，必要时降级到东财。
- 🧱 **Web UI 基础骨架升级** — 重建共享设计令牌与通用组件，新增 App Shell、Theme Provider、侧边导航，并同步调整 Electron 加载背景，为 Web / Desktop 的统一体验打底。
- 🔐 **登录与系统设置流程重做** — 重构 Login、Settings 与 Auth 管理流程，补上显式的认证 setup-state 处理，并让 Web 端与运行时认证配置 API 行为对齐。
- 🧪 **前端回归与冒烟覆盖补强** — 新增并扩展登录、首页、聊天、移动端 Shell、设置页、回测入口等关键路径的组件测试与 Playwright smoke coverage。

### 变更

- 🧭 **页面接入新 Shell 布局契约** — Home、Chat、Settings、Backtest 已统一接入新的页面容器、抽屉和滚动约定，降低 UI 迁移期间的页面行为不一致。
- 💾 **设置页状态同步更稳** — 优化草稿保留、直接保存同步与冲突处理，减少模块级保存后前后端配置状态不一致的问题。
- 🎭 **登录页视觉基线回归** — 登录页恢复到既有 `006` 分支的视觉基线，同时保留新的认证状态逻辑和统一表单交互模型。
- 🏛️ **AI 协作治理资产加固** — 收敛并加强 `AGENTS.md`、`CLAUDE.md`、Copilot 指令和校验脚本的一致性约束，降低治理资产长期漂移风险。

### Added

- **Web UI foundation refresh** — rebuilt shared design tokens and common primitives, introduced the app shell, theme provider, sidebar navigation, and Electron loading background alignment for the upgraded desktop/web experience
- **Settings and auth workflow overhaul** — rebuilt the Login, Settings, and Auth management flows, added explicit auth setup-state handling, and aligned the Web UI with the runtime auth configuration APIs
- **UI regression coverage and smoke checks** — expanded targeted frontend tests and added Playwright smoke coverage for login, home, chat, mobile shell, settings, and backtest entry flows

### Changed

- **Shell-driven page integration** — aligned Home, Chat, Settings, and Backtest with the new shell layout contract so routing, drawer behavior, and page-level scrolling are consistent during the UI migration
- **Settings state consistency** — refined draft preservation, direct-save synchronization, and conflict handling so module-level saves no longer leave the page out of sync with backend config state
- **Login visual baseline** — restored the login page visual treatment to the established `006` branch baseline while keeping the newer auth-state logic and unified form interaction model

### 修复

- ⏰ **定时启动立即执行兼容旧配置**（Issue #726）— `SCHEDULE_RUN_IMMEDIATELY` 未设置时会回退读取 `RUN_IMMEDIATELY`，修复升级后旧 `.env` 在定时模式下的兼容性问题；同时澄清 `.env.example` / README 中两个配置项的适用范围，并注明 Outlook / Exchange 强制 OAuth2 暂不支持。
- 🧵 **运行期 `MAX_WORKERS` 配置生效与可解释性增强**（#633）— 修复异步分析队列未按 `MAX_WORKERS` 同步的问题；新增任务队列并发 in-place 同步机制（空闲即时生效、繁忙延后），并在设置保存反馈与运行日志中明确输出 `profile/max/effective`，减少“参数未生效”误解。
- 🔐 **退出登录立即失效现有会话** — `POST /api/v1/auth/logout` 现在会轮换 session secret，避免旧 cookie 在退出后仍可继续访问受保护接口；同浏览器标签页和并发页面会被同步登出。认证开启时，该接口也不再属于匿名白名单，未登录请求会返回 `401`，避免匿名请求触发全局 session 失效。
- 🧮 **Tushare 板块/筹码调用限流与跨日缓存修复** — 新增的 `trade_cal`、行业板块排行、筹码分布链路统一接入 `_check_rate_limit()`；交易日历缓存改为按自然日刷新，避免服务跨天运行后继续沿用旧交易日判断取数日期。
- 💼 **持仓超售拦截与错误流水恢复**（#718）— `POST /api/v1/portfolio/trades` 现在会在写入前校验可卖数量，超售返回 `409 portfolio_oversell`；持仓页新增交易 / 资金流水 / 公司行为删除能力，删除后会同步失效仓位缓存与未来快照，便于从错误流水中直接恢复。
- 📧 **邮件中文发件人名编码**（#708）— 邮件通知现在会对包含中文的 `EMAIL_SENDER_NAME` 自动做 RFC 2047 编码，并在异常路径补充 SMTP 连接清理，修复 GitHub Actions / QQ SMTP 下 `'ascii' codec can't encode characters` 导致的发送失败。
- 🐛 **港股 Agent 实时行情去重与快速路由** — 统一 `HK01810` / `1810.HK` / `01810` 等港股代码归一规则；港股实时行情改为直接走单次 `akshare_hk` 路径，避免按 A 股 source priority 重复触发同一失败接口；Agent 运行期对显式 `retriable=false` 的工具失败增加短路缓存，减少同轮分析中的重复失败调用。
- 📰 **新闻时效硬过滤与策略分窗**（#697）— 新增 `NEWS_STRATEGY_PROFILE`（`ultra_short/short/medium/long`）并与 `NEWS_MAX_AGE_DAYS` 统一计算有效窗口；搜索结果在返回后执行发布时间硬过滤（时间未知剔除、超窗剔除、未来仅容忍 1 天），并在历史 fallback 链路追加相同约束，避免旧闻再次进入“最新动态/风险警报”。

### 文档

- ☁️ **新增云服务器 Web 界面部署与访问教程**（Fixes #686）— 补充从云端部署到外部访问的落地说明，降低远程自托管门槛。
- 🌍 **补齐英文文档索引与协作文档** — 新增英文文档索引、贡献指南、Bot 命令文档，并补充中英双语 issue / PR 模板，方便中英文协作与外部贡献者理解项目入口。
- 🏷️ **本地化 README 补充 Trendshift badge** — 在多语言 README 中同步补上新版能力入口标识，减少中英文说明面不一致。

## [3.7.0] - 2026-03-15

### 新功能

- 💼 **持仓管理 P0 全功能上线**（#677，对应 Issue #627）
  - **核心账本与快照闭环**：新增账户、交易、现金流水、企业行为、持仓缓存、每日快照等核心数据模型与 API 端点；支持 FIFO / AVG 双成本法回放；同日事件顺序固定为 `现金 → 企业行为 → 交易`；持仓快照写入采用原子事务。
  - **券商 CSV 导入**：支持华泰 / 中信 / 招商首批适配，含列名别名兼容；两阶段接口（解析预览 + 确认提交）；`trade_uid` 优先、key-field hash 兜底的幂等去重；前导零股票代码完整保留。
  - **组合风险报告**：集中度风险（Top Positions + A 股板块口径）、历史回撤监控（支持回填缺失快照）、止损接近预警；多币种统一换算 CNY 口径；汲取失败时回退最近成功汇率并标记 stale。
  - **Web 持仓页**（`/portfolio`）：组合总览、持仓明细、集中度饼图、风险摘要、全组合 / 单账户切换；手工录入交易 / 资金流水 / 企业行为；内嵌账户创建入口；CSV 解析 + 提交闭环与券商选择器。
  - **Agent 持仓工具**：新增 `get_portfolio_snapshot` 数据工具，默认紧凑摘要，可选持仓明细与风险数据。
  - **事件查询 API**：新增 `GET /portfolio/trades`、`GET /portfolio/cash-ledger`、`GET /portfolio/corporate-actions`，支持日期过滤与分页。
  - **可扩展 Parser Registry**：应用级共享注册，支持运行时注册新券商；新增 `GET /portfolio/imports/csv/brokers` 发现接口。

- 🎨 **前端设计系统与原子组件库**（#662）
  - 引入渐进式双主题架构（HSL 变量化设计令牌），清理历史 Legacy CSS；重构 Button / Card / Badge / Collapsible / Input / Select 等 20+ 核心组件；新增 `clsx` + `tailwind-merge` 类名合并工具；提升历史记录、LLM 配置等页面可读性。

- ⚡ **分析 API 异步契约与启动优化**（#656）
  - 规范 `POST /api/v1/analysis/analyze` 异步请求的返回契约；优化服务启动辅助逻辑；修复前端报告类型联合定义与后端响应对齐问题。

### 修复

- 🔔 **Discord 环境变量向后兼容**（#659）：运行时新增 `DISCORD_CHANNEL_ID` → `DISCORD_MAIN_CHANNEL_ID` 的 fallback 读取；历史配置用户无需修改即可恢复 Discord Bot 通知；全部相关文档与 `.env.example` 对齐。
- 🔧 **GitHub Actions Node 24 升级**（#665）：将所有 GitHub 官方 actions 升级至 Node 24 兼容版本，消除 CI 日志中的 Node.js 20 deprecation warning（影响 2026-06-02 强制升级窗口）。
- 📅 **持仓页默认日期本地化**：手工录入表单默认日期改用本地时间（`getFullYear/Month/Date`），修复 UTC-N 时区用户在当天晚间出现日期偏移的问题。
- 🔁 **CSV 导入去重逻辑加固**：dedup hash 纳入行序号作为区分因子，确保同字段合法分笔成交不被误折叠；同时在 `trade_uid` 存在时也持久化 hash，防止混合来源重复写入。

### 变更

- `POST /api/v1/portfolio/trades` 在同账户内 `trade_uid` 冲突时返回 `409`。
- 持仓风险响应新增 `sector_concentration` 字段（增量扩展），原有 `concentration` 字段保持不变。
- 分析 API `analyze` 接口异步行为契约文档化；前端报告类型联合更新。

### 测试

- 新增持仓核心服务测试（FIFO / AVG 部分卖出、同日事件顺序、重复 `trade_uid` 返回 409、快照 API 契约）。
- 新增 CSV 导入幂等性、合法分笔成交不误去重、去重边界、风险阈值边界、汇率降级行为测试。
- 新增 Agent `get_portfolio_snapshot` 工具调用测试。
- 新增分析 API 异步契约回归测试。

## [3.6.0] - 2026-03-14

### Added
- 📊 **Web UI Design System** — implemented dual-theme architecture and terminal-inspired atomic UI components
- 📊 **UI Components Refactoring** — integrated `clsx` and `tailwind-merge` for robust class composition across Web UI

- 🗑️ **History batch deletion** — Web UI now supports multi-selection and batch deletion of analysis history; added `POST /api/v1/history/batch-delete` endpoint and `ConfirmDialog` component.
- 🔐 **Auth settings API** — new `POST /api/v1/auth/settings` endpoint to enable or disable Web authentication at runtime and set the initial admin password when needed
- openclaw Skill 集成指南 — 新增 [docs/openclaw-skill-integration.md](openclaw-skill-integration.md)，说明如何通过 openclaw Skill 调用 DSA API
- ⚙️ **LLM channel protocol/test UX** — `.env` and Web settings now share the same channel shape (`LLM_CHANNELS` + `LLM_<NAME>_PROTOCOL/BASE_URL/API_KEY/MODELS/ENABLED`); settings page adds per-channel connection testing, primary/fallback/vision model selection, and protocol-aware model prefixing
- 🤖 **Agent architecture Phase 0+1** — shared protocols (`AgentContext`, `AgentOpinion`, `StageResult`), extracted `run_agent_loop()` runner, `AGENT_ARCH` switch (`single`/`multi`), config registry entries
- 🔍 **Bot NL routing** — two-layer natural-language routing: cheap regex pre-filter (stock codes + finance keywords) → lightweight LLM intent parsing; controlled by `AGENT_NL_ROUTING=true`; supports multi-stock and strategy extraction
- 💬 **`/ask` multi-stock analysis** — comma or `vs` separated codes (max 5), parallel thread execution with 150s timeout (preserves partial results), Markdown comparison summary table at top
- 📋 **`/history` command** — per-user session isolation via `{platform}_{user_id}:{scope}` format (colon delimiter prevents prefix collision); lists both `/chat` and `/ask` sessions; view detail or clear
- 📊 **`/strategies` command** — lists available strategy YAML files grouped by category (趋势/形态/反转/框架) with ✅/⬜ activation status
- 🔧 **Backtest summary tools** — `get_strategy_backtest_summary` and `get_stock_backtest_summary` registered as read-only Agent tools
- ⚙️ **Agent auto-detection** — `is_agent_available()` auto-detects from `LITELLM_MODEL`; explicit `AGENT_MODE=true/false` takes full precedence
- 🏗️ **Multi-Agent orchestrator (Phase 2)** — `AgentOrchestrator` with 4 modes (`quick`/`standard`/`full`/`strategy`); drop-in replacement for `AgentExecutor` via `AGENT_ARCH=multi`; `BaseAgent` ABC with tool subset filtering, cached data injection, and structured `AgentOpinion` output
- 🧩 **Specialised agents (Phase 2-4)** — `TechnicalAgent` (8 tools, trend/MA/MACD/volume/pattern analysis), `IntelAgent` (news & sentiment, risk flag propagation), `DecisionAgent` (synthesis into Decision Dashboard JSON), `RiskAgent` (7 risk categories, two-level severity with soft/hard override)
- 📈 **Strategy system (Phase 3)** — `StrategyAgent` (per-strategy evaluation from YAML skills), `StrategyRouter` (rule-based regime detection → strategy selection), `StrategyAggregator` (weighted consensus with backtest performance factor)
- 🔬 **Deep Research agent (Phase 5)** — `ResearchAgent` with 3-phase approach (decompose → research sub-questions → synthesise report); token budget tracking; new `/research` bot command with aliases (`/深研`, `/deepsearch`)
- 🧠 **Memory & calibration (Phase 6)** — `AgentMemory` with prediction accuracy tracking, confidence calibration (activates after minimum sample threshold), strategy auto-weighting based on historical win rate
- 📊 **Portfolio Agent (Phase 7)** — `PortfolioAgent` for multi-stock portfolio analysis (position sizing, sector concentration, correlation risk, cross-market linkage, rebalance suggestions)
- 🔔 **Event-driven alerts (Phase 7)** — `EventMonitor` with `PriceAlert`, `VolumeAlert`, `SentimentAlert` rules; async checking, callback notifications, serializable persistence
- ⚙️ **New config entries** — `AGENT_ORCHESTRATOR_MODE`, `AGENT_RISK_OVERRIDE`, `AGENT_DEEP_RESEARCH_BUDGET`, `AGENT_MEMORY_ENABLED`, `AGENT_STRATEGY_AUTOWEIGHT`, `AGENT_STRATEGY_ROUTING` — all registered in `config.py` + `config_registry.py` (WebUI-configurable)

### Changed
- 🔐 **Auth password state semantics** — stored password existence is now tracked independently from auth enablement; when auth is disabled, `/api/v1/auth/status` returns `passwordSet=false` while preserving the saved password for future re-enable
- 🔐 **Auth settings re-enable hardening** — re-enabling auth with a stored password now requires `currentPassword`, and failed session creation rolls back the auth toggle to avoid lockout
- ♻️ **AgentExecutor refactored** — `_run_loop` delegates to shared `runner.run_agent_loop()`; removed duplicated serialization/parsing/thinking-label code
- ♻️ **Unified agent switch** — Bot, API, and Pipeline all use `config.is_agent_available()` instead of divergent `config.agent_mode` checks
- 📖 **README.md** — expanded Bot commands section (ask/chat/strategies/history), added NL routing note, updated agent mode description
- 📖 **.env.example** — added `AGENT_ARCH` and `AGENT_NL_ROUTING` configuration documentation
- 🔌 **Analysis API async contract** — `POST /api/v1/analysis/analyze` now documents distinct async `202` payloads for single-stock vs batch requests, and `report_type=full` is treated consistently with the existing full-report behavior

### Fixed
- 🐛 **Analysis API blank-code guardrails** — `POST /api/v1/analysis/analyze` now drops whitespace-only entries before batch enqueue and returns `400` when no valid stock code remains
- 🐛 **Bare `/api` SPA fallback** — unknown API paths now return JSON `404` consistently for both `/api/...` and the exact `/api` path
- 🎮 **Discord channel env compatibility** — runtime now accepts legacy `DISCORD_CHANNEL_ID` as a fallback for `DISCORD_MAIN_CHANNEL_ID`, and the docs/examples now use the same variable name as the actual workflow/config implementation
- 🐛 **Session secret rotation on Windows** — use atomic replace so auth toggles invalidate existing sessions even when `.session_secret` already exists
- 🐛 **Auth toggle atomicity** — persist `ADMIN_AUTH_ENABLED` before rotating session secret; on rotation failure, roll back to the previous auth state
- 🔧 **LLM runtime selection guardrails** — YAML 模式下渠道编辑器不再覆盖 `LITELLM_MODEL` / fallback / Vision；系统配置校验补上全部渠道禁用后的运行时来源检查，并修复 `vertexai/...` 这类协议别名模型被重复加前缀的问题
- 🐛 **Multi-stock `/ask` follow-up regressions** — portfolio overlay now shares the same timeout budget as the per-stock phase and is skipped on timeout instead of blocking the bot reply; `/history` now stores the readable per-stock summary instead of raw dashboard JSON; condensed multi-stock output now renders numeric `sniper_points` values
- 🐛 **Decision dashboard enum compatibility** — multi-agent `DecisionAgent` now keeps `decision_type` within the legacy `buy|hold|sell` contract and normalizes stray `strong_*` outputs before risk override, pipeline conversion, and downstream统计/通知汇总
- 🛟 **Multi-Agent partial-result fallback** — `IntelAgent` now caches parsed intel for downstream reuse, shared JSON parsing tolerates lightly malformed model output, and the orchestrator preserves/synthesizes a minimal dashboard on timeout or mid-pipeline parse failure instead of always collapsing to `50/观望/未知`
- 🐛 **Shared LiteLLM routing restored** — bot NL intent parsing and `ResearchAgent` planning/synthesis now reuse the same LiteLLM adapter / Router / fallback / `api_base` injection path as the main Agent flow, so `LLM_CHANNELS` / `LITELLM_CONFIG` / OpenAI-compatible deployments behave consistently
- 🐛 **Bot chat session backward compatibility** — `/chat` now keeps using the legacy `{platform}_{user_id}` session id when old history already exists, and `/history` can still list / view / clear those pre-migration sessions alongside the new `{platform}_{user_id}:chat` format
- 🐛 **EventMonitor unsupported rule rejection** — config validation/runtime loading now reject or skip alert types the monitor cannot actually evaluate yet, so schedule mode no longer silently accepts permanent no-op rules
- 🐛 **P0 基本面聚合稳定性修复** (#614) — 修复 `get_stock_info` 板块语义回归（新增 `belong_boards` 并保留 `boards` 兼容别名）、引入基本面上下文精简返回以控制 token、为基本面缓存增加最大条目淘汰，并补齐 ETF 总体状态聚合与 NaN 板块字段过滤，保证 fail-open 与最小入侵。
- 🔧 **GitHub Actions 搜索引擎环境变量补充** — 工作流新增 `MINIMAX_API_KEYS`、`BRAVE_API_KEYS`、`SEARXNG_BASE_URLS` 环境变量映射，使 GitHub Actions 用户可配置 MiniMax、Brave、SearXNG 搜索服务（此前 v3.5.0 已添加 provider 实现但缺少工作流配置）
- 🤖 **Multi-Agent runtime consistency** — `AGENT_MAX_STEPS` now propagates to each orchestrated sub-agent; added cooperative `AGENT_ORCHESTRATOR_TIMEOUT_S` budget to stop overlong pipelines before they cascade further
- 🔌 **Multi-Agent feature wiring** — `AGENT_RISK_OVERRIDE` now actively downgrades final dashboards on hard risk findings; `AGENT_MEMORY_ENABLED` now injects recent analysis memory + confidence calibration into specialised agents; multi-stock `/ask` now runs `PortfolioAgent` to add portfolio-level allocation and concentration guidance
- 🔔 **EventMonitor runtime wiring** — schedule mode can now load alert rules from `AGENT_EVENT_ALERT_RULES_JSON`, poll them at `AGENT_EVENT_MONITOR_INTERVAL_MINUTES`, and send triggered alerts through the existing notification service
- 🛠️ **Follow-up stability fixes** — multi-stock `/ask` now falls back to usable text output when dashboard JSON parsing fails; EventMonitor skips semantically invalid rules instead of aborting schedule startup; background alert polling now runs independently of the main scheduled analysis loop
- 🧪 **Multi-Agent regression coverage** — added orchestrator execution tests for `run()`, `chat()`, critical-stage failure, graceful degradation, and timeout handling
- 🧹 **PortfolioAgent cleanup** — `post_process()` now reuses shared JSON parsing and removed stale unused imports
- 🚦 **Bot async dispatch** — `CommandDispatcher` now exposes `dispatch_async()`; NL intent parsing and default command execution are offloaded from the event loop, DingTalk stream awaits async handlers directly, and Feishu stream processing is moved off the SDK callback thread
- 🌐 **Async webhook handler** — new `handle_webhook_async()` function in `bot/handler.py` for use from async contexts (e.g. FastAPI); calls `dispatch_async()` directly without thread bridging
- 🧵 **Feishu stream ThreadPoolExecutor** — replaced unbounded per-message `Thread` spawning with a capped `ThreadPoolExecutor(max_workers=8)` to prevent thread explosion under message bursts
- 🔒 **EventMonitor safety** — `_check_volume()` now safely handles `get_daily_data` returning `None` (no tuple-unpacking crash); `on_trigger` callbacks support both sync and async callables via `asyncio.to_thread`/`await`
- 🧹 **ResearchAgent dedup** — `_filtered_registry()` now delegates to `BaseAgent._filtered_registry()` instead of duplicating the filtering logic
- 🧹 **Bot trailing whitespace cleanup** — removed W291/W293 whitespace issues across `bot/handler.py`, `bot/dispatcher.py`, `bot/commands/base.py`, `bot/platforms/feishu_stream.py`, `bot/platforms/dingtalk_stream.py`
- 🐛 **Dispatcher `_parse_intent_via_llm` safety** — replaced fragile `'raw' in dir()` with `'raw' in locals()` for undefined-variable guard in `JSONDecodeError` handler
- 🐛 **筹码结构 LLM 未填写时兜底补全** (#589) — DeepSeek 等模型未正确填写 `chip_structure` 时，自动用数据源已获取的筹码数据补全，保证各模型展示一致；普通分析与 Agent 模式均生效
- 🐛 **历史报告狙击点位显示原始文本** (#452) — 历史详情页现优先展示 `raw_result.dashboard.battle_plan.sniper_points` 中的原始字符串，避免 `analysis_history` 数值列把区间、说明文字或复杂点位压缩成单个数字；保留原有数值列作为回退
- 🐛 **Session prefix collision** — user ID `123` could see sessions of user `1234` via `startswith`; fixed with colon delimiter in session_id format
- 🐛 **NL pre-filter false positives** — `re.IGNORECASE` caused `[A-Z]{2,5}` to match common English words like "hello"; removed global flag, use inline `(?i:...)` only for English finance keywords
- 🐛 **Dotted ticker in strategy args** — `_get_strategy_args()` didn't recognize `BRK.B` as a stock code, leaving it in strategy text; now accepts `TICKER.CLASS` format
- ⏱️ **efinance 长调用挂起修复** (#660) — 为所有 efinance API 调用引入 `_ef_call_with_timeout()` 包装（默认 30 秒，可通过 `EFINANCE_CALL_TIMEOUT` 配置）；使用 `executor.shutdown(wait=False)` 确保超时后不再阻塞主线程，彻底消除 81 分钟挂起问题
- 🛡️ **类型安全内容完整性检查** (#660) — `check_content_integrity()` 现在将非字符串类型的 `operation_advice` / `analysis_summary` 视为缺失字段，避免下游 `get_emoji()` 因 `dict.strip()` 崩溃
- 📄 **报告保存与通知解耦** (#660) — `_save_local_report()` 不再依赖 `send_notification` 标志触发，`--no-notify` 模式下本地报告照常保存
- 🔄 **operation_advice 字典归一化** (#660) — Pipeline 和 BacktestEngine 现在将 LLM 返回的 `dict` 格式 `operation_advice` 通过 `decision_type`（不区分大小写）映射为标准字符串，防止因模型输出格式变化导致崩溃
- 🛡️ **runner.py usage None 防护** (#660) — `response.usage` 为 `None` 时不再抛出 `AttributeError`，回退为 0 token 计数
- 📋 **orchestrator 静默失败改为日志警告** (#660) — `IntelAgent` / `RiskAgent` 阶段失败现在记录 `WARNING` 而非静默跳过，便于诊断

### Notes
- ⚠️ **Multi-worker auth toggles** — runtime auth updates are process-local; multi-worker deployments must restart/roll workers to keep auth state consistent

## [3.5.0] - 2026-03-12

### Added
- 📊 **Web UI full report drawer** (Fixes #214) — history page adds "Full Report" button to display the complete Markdown analysis report in a side drawer; new `GET /api/v1/history/{record_id}/markdown` endpoint
- 📊 **LLM cost tracking** — all LLM calls (analysis, agent, market review) recorded in `llm_usage` table; new `GET /api/v1/usage/summary?period=today|month|all` endpoint returns aggregated token usage by call type and model
- 🔍 **SearXNG search provider** (Fixes #550) — quota-free self-hosted search fallback; priority: Bocha > Tavily > Brave > SerpAPI > MiniMax > SearXNG
- 🔍 **MiniMax web search provider** — `MiniMaxSearchProvider` with circuit breaker (3 failures → 300s cooldown) and dual time-filtering; configured via `MINIMAX_API_KEYS`
- 🤖 **Agent models discovery API** — `GET /api/v1/agent/models` returns available model deployments (primary/fallback/source/api_base) for Web UI model selector
- 🤖 **Agent chat export & send** (#495) — export conversation to .md file; send to configured notification channels; new `POST /api/v1/agent/chat/send`
- 🤖 **Agent background execution** (#495) — analysis continues when switching pages; badge notification on completion; auto-cancel in-progress stream on session switch
- 📝 **Report Engine P0** — Pydantic schema validation for LLM JSON; Jinja2 templates (markdown/wechat/brief) with legacy fallback; content integrity checks with retry; brief mode (`REPORT_TYPE=brief`); history signal comparison
- 📦 **Smart import** — multi-source import from image/CSV/Excel/clipboard; Vision LLM extracts code+name+confidence; name→code resolver (local map + pinyin + AkShare); confidence-tiered confirmation
- ⚙️ **GitHub Actions LiteLLM config** — workflow supports `LITELLM_CONFIG`/`LITELLM_CONFIG_YAML` for flexible AI provider configuration
- ⚙️ **Config engine refactor & system API** (#602) — unified config registry, validation and API exposure
- 📖 **LLM configuration guide** — new `docs/LLM_CONFIG_GUIDE.md` covering 3-tier config, quick start, Vision/Agent/troubleshooting

### Fixed
- 🐛 **analyze_trend always reports No historical data** (#600) — now fetches from DB/DataFetcher instead of broken `get_analysis_context`
- 🐛 **Chip structure fallback when LLM omits it** (#589) — auto-fills from data source chip data for consistent display across models
- 🐛 **History sniper points show raw text** (#452) — prioritizes original strings over compressed numeric values
- 🐛 **GitHub Actions ENABLE_CHIP_DISTRIBUTION configurable** (#617) — no longer hardcoded, supports vars/secrets override
- 🐛 **`.env` save preserves comments and blank lines** — Web settings no longer destroys `.env` formatting
- 🐛 **Agent model discovery fixes** — legacy mode includes LiteLLM-native providers; source detection aligned with runtime; fallback deployments no longer expanded per-key
- 🐛 **Stooq US stock previous close semantics** — no longer misuses open price as previous close
- 🐛 **Stock name prefetch regression** — prioritizes local `STOCK_NAME_MAP` before remote queries
- 🐛 **AkShare limit-up/down calculation** (#555) — fixed market analysis statistics
- 🐛 **AkShare Tencent source field index & ETF quote mapping** (#579)
- 🐛 **Pytdx stock name cache pagination** (#573) — prevents cache overflow
- 🐛 **PushPlus oversized report chunking** (#489) — auto-segments long content
- 🐛 **Agent chat cancel & switch** (#495) — cancel no longer misreports as failure; fast switch no longer overwrites stream state
- 🐛 **MiniMax search status in `/status` command** (#587)
- 🐛 **config_registry duplicate BOCHA_API_KEYS** — removed duplicate dict entry that silently overwrote config

### Changed
- 🔎 **Fetcher failure observability** — logs record start/success/failure with elapsed time, failover transitions; Efinance/Akshare include upstream endpoint and classified failure categories
- ♻️ **Data source resilience & cleanup** (#602) — fallback chain optimization
- ♻️ **Image extract API response extension** — new `items` field (code/name/confidence); `codes` preserved for backward compatibility
- ♻️ **Import parse error messages** — specific failure reasons for Excel/CSV; improved logging with file type and size

### Docs
- 📖 LLM config guide refactored for clarity (#583)
- 📖 `image-extract-prompt.md` with full prompt documentation
- 📖 AkShare fallback cache TTL documentation
## [3.4.10] - 2026-03-07

### Fixed
- 🐛 **EfinanceFetcher ETF OHLCV data** (#541, #527) — switch `_fetch_etf_data` from `ef.fund.get_quote_history` (NAV-only, no OHLCV, no `beg`/`end` params) to `ef.stock.get_quote_history`; ETFs now return proper open/high/low/close/volume/amount instead of zeros; remove obsolete NAV column mappings from `_normalize_data`
- 🐛 **tiktoken 0.12.0 `Unknown encoding cl100k_base`** (#537) — pin `tiktoken>=0.8.0,<0.12.0` in requirements.txt to avoid plugin-registration regression introduced in 0.12.0
- 🐛 **Web UI API error classification** (#540) — frontend no longer treats every HTTP 400 as the same "server/network" failure; now distinguishes Agent disabled / missing params / model-tool incompatibility / upstream LLM errors / local connection failures
- 🐛 **北交所代码识别失败** (#491, #533) — 8/4/92 开头的 6 位代码现正确识别为北交所；Tushare/Akshare/Yfinance 等数据源支持 .BJ 或 bj 前缀；Baostock/Pytdx 对北交所代码显式切换数据源；避免误判上海 B 股 900xxx
- 🐛 **狙击点位解析错误** (#488, #532) — 理想买入/二次买入等字段在无「元」字时误提取括号内技术指标数字；现先截去第一个括号后内容再提取

### Added
- **Markdown-to-image for dashboard report** (#455, #535) — 个股日报汇总支持 markdown 转图片推送（Telegram、WeChat、Custom、Email），与大盘复盘行为一致
- **markdown-to-file engine** (#455) — `MD2IMG_ENGINE=markdown-to-file` 可选，对 emoji 支持更好，需 `npm i -g markdown-to-file`
- **PREFETCH_REALTIME_QUOTES** (#455) — 设为 `false` 可禁用实时行情预取，避免 efinance/akshare_em 全市场拉取
- **Stock name prefetch** (#455) — 分析前预取股票名称，减少报告中「股票xxxxx」占位符
- 📊 **分析报告模型标记** (#528, #534) — 在分析报告 meta、报告末尾、推送内容中展示 `model_used`（完整 LLM 模型名）；Agent 多轮调用时记录并展示每轮实际使用的模型（支持 fallback 切换）

### Changed
- **Enhanced markdown-to-image failure warning** (#455) — 转图失败时提示具体依赖（wkhtmltopdf 或 m2f）
- **WeChat-only image routing optimization** (#455) — 仅配置企业微信图片时，不再对完整报告做冗余转图，避免误导性失败日志
- **Stock name prefetch lightweight mode** (#455) — 名称预取阶段跳过 realtime quote 查询，减少额外网络开销

## [3.4.9] - 2026-03-06

### Added
- 🧠 **Structured config validation** — `ConfigIssue` dataclass and `validate_structured()` with severity-aware logging; `CONFIG_VALIDATE_MODE=strict` aborts startup on errors
- 🖼️ **Vision model config** — `VISION_MODEL` and `VISION_PROVIDER_PRIORITY` for image stock extraction; provider fallback (Gemini → Anthropic → OpenAI → DeepSeek) when primary fails
- 🚀 **CLI init wizard** — `python -m dsa init` 3-step interactive bootstrap (model → data source → notification), 9 provider presets, incremental merge by default
- 🔧 **Multi-channel LLM support** with visual channel editor (#494)

### Changed
- ♻️ **Vision extraction** — migrated from gemini-3 hardcode to `litellm.completion()` with configurable model and provider fallback; `OPENAI_VISION_MODEL` deprecated in favor of `VISION_MODEL`
- ♻️ **Market analyzer** — uses `Analyzer.generate_text()` for LLM calls; fixes bypass and Anthropic `AttributeError` when using non-Router path
- ♻️ **Config validation refinements** — test_env output format syncs with `validate_structured` (severity-aware ✓/✗/⚠/·); Vision key warning when `VISION_MODEL` set but no provider API key; market_analyzer test covers `generate_market_review` fallback when `generate_text` returns None
- ⚙️ **Auto-tag workflow defaults to NO tag** — only tags when commit message explicitly contains `#patch`, `#minor`, or `#major`
- ♻️ **Formatter and notification refactor** (#516)

### Fixed
- 🐛 **STOCK_LIST not refreshed on scheduled runs** — `.env` or WebUI changes to `STOCK_LIST` now hot-reload before each scheduled analysis (#529)
- 🐛 **WebUI fails to load with MIME type error** — SPA fallback route now resolves correct `Content-Type` for JS/CSS files (#520)
- 🐛 **AstrBot sender docstring misplaced** — `import time` placed before docstring in `_send_astrbot`, causing it to become dead code
- 🐛 **Telegram Markdown link escaping** — `_convert_to_telegram_markdown` escaped `[]()` characters, breaking all Markdown links in reports
- 🐛 **Duplicate `discord_bot_status` field** in Config dataclass — second declaration silently shadowed the first
- 🧹 **Unused imports** — removed `shutil`/`subprocess` from `main.py`
- 🔧 **Config validation and Vision key check** (#525)

### Docs
- 📝 Clarified GitHub Actions non-trading-day manual run controls (`TRADING_DAY_CHECK_ENABLED` + `force_run`) for Issue #461 / PR #466

## [3.4.8] - 2026-03-02

### Fixed
- 🐛 **Desktop exe crashes on startup with `FileNotFoundError`** — PyInstaller build was missing litellm's JSON data files (e.g. `model_prices_and_context_window_backup.json`). Added `--collect-data litellm` to both Windows and macOS build scripts so the files are correctly bundled in the executable.

### CI
- 🔧 Cache Electron binaries on macOS CI runners to prevent intermittent EOF download failures when fetching `electron-vX.Y.Z-darwin-*.zip` from GitHub CDN
- 🔧 Fix macOS DMG `hdiutil Resource busy` error during desktop packaging

### Docs
- 📝 Clarify non-trading-day manual run controls for GitHub Actions (`TRADING_DAY_CHECK_ENABLED` + `force_run`) (#474)

## [3.4.7] - 2026-02-28

### Added
- 🧠 **CN/US Market Strategy Blueprint System** (#395) — market review prompt injects region-specific strategy blueprints with position sizing and risk trigger recommendations

### Fixed
- 🐛 **`TRADING_DAY_CHECK_ENABLED` env var and `--force-run` for GitHub Actions** (#466)
- 🐛 **Agent pipeline preserved resolved stock names** (#464) — placeholder names no longer leak into reports
- 🐛 **Code cleanup** (#462, Fixes #422)
- 🐛 **WebUI auto-build on startup** (#460)
- 🐛 **ARCH_ARGS unbound variable** (#458)
- 🐛 **Time zone inconsistency & right panel flash** (#439)

### Docs
- 📝 Clarify potential ambiguities in code (#343)
- 📝 ENABLE_EASTMONEY_PATCH guidance for Issue #453 (#456)

## [3.4.0] - 2026-02-27

### Added
- 📡 **LiteLLM Direct Integration + Multi API Key Support** (#454, Fixes #421 #428)
  - Removed native SDKs (google-generativeai, google-genai, anthropic); unified through `litellm>=1.80.10`
  - New config: `LITELLM_MODEL`, `LITELLM_FALLBACK_MODELS`, `GEMINI_API_KEYS`, `ANTHROPIC_API_KEYS`, `OPENAI_API_KEYS`
  - Multi-key auto-builds LiteLLM Router (simple-shuffle) with 429 cooldown
  - **Breaking**: `.env` `GEMINI_MODEL` (no prefix) only for fallback; explicit config must include provider prefix

### Changed
- ♻️ **Notification Refactoring** (#435) — extracted 10 sender classes into `src/notification_sender/`

### Fixed
- 🐛 LLM NoneType crash, history API 422, sniper points extraction
- 🐛 Auto-build frontend on WebUI startup — `WEBUI_AUTO_BUILD` env var (default `true`)
- 🐛 Docker explicit project name (#448)
- 🐛 Bocha search SSL retry (#445, #446) — transient errors retry up to 3 times
- 🐛 Gemini google-genai SDK migration (Fixes #440, #444)
- 🐛 Mobile home page scrolling (Fixes #419, #433)
- 🐛 History list scroll reset (#431)
- 🐛 Settings save button false positive (fixes #417, #430)

## [3.3.22] - 2026-02-26

### Added
- 💬 **Chat History Persistence** (Fixes #400, #414) — `/chat` page survives refresh, sidebar session list
- 🎨 Project VI Assets — logo icon set, PSD, vector, banner (#425)
- 🚀 Desktop CI Auto-Release (#426) — Windows + macOS parallel builds

### Fixed
- 🐛 Agent Reasoning 400 & LiteLLM Proxy (fixes #409, #427)
- 🐛 Discord chunked sending (#413) — `DISCORD_MAX_WORDS` config
- 🐛 yfinance shared DataFrame (#412)
- 🐛 sniper_points parsing (#408)
- 🐛 Agent framework category missing (#406)
- 🐛 Date inconsistency & query id (fixes #322, #363)

## [3.3.12] - 2026-02-24

### Added
- 📈 **Intraday Realtime Technical Indicators** (Issue #234, #397) — MA calculated from realtime price, config: `ENABLE_REALTIME_TECHNICAL_INDICATORS`
- 🤖 **Agent Strategy Chat** (#367) — full ReAct pipeline, 11 YAML strategies, SSE streaming, multi-turn chat
- 📢 PushPlus Group Push — `PUSHPLUS_TOPIC` (#402)
- 📅 Trading Day Check (Issue #373, #375) — `TRADING_DAY_CHECK_ENABLED`, `--force-run`

### Fixed
- 🐛 DeepSeek reasoning mode (Issue #379, #386)
- 🐛 Agent news intel persistence (Fixes #396, #405)
- 🐛 Bare except clauses replaced with `except Exception` (#398)
- 🐛 UUID fallback for HTTP non-secure context (fixes #377, #381)
- 🐛 Docker DNS resolution (Fixes #372, #374)
- 🐛 Agent session/strategy bugs — multiple follow-up fixes for #367
- 🐛 yfinance parallel download data filtering

### Changed
- Market review strategy consistency — unified cn/us template
- Agent test assertions updated (`6 -> 11`)


## [3.2.11] - 2026-02-23

### 修复（#patch）
- 🐛 **StockTrendAnalyzer 从未执行** (Issue #357)
  - 根因：`get_analysis_context` 仅返回 2 天数据且无 `raw_data`，pipeline 中 `raw_data in context` 始终为 False
  - 修复：Step 3 直接调用 `get_data_range` 获取 90 日历天（约 60 交易日）历史数据用于趋势分析
  - 改善：趋势分析失败时用 `logger.warning(..., exc_info=True)` 记录完整 traceback

## [3.2.10] - 2026-02-22

### 新增
- ⚙️ 支持 `RUN_IMMEDIATELY` 配置项，设为 `true` 时定时任务触发后立即执行一次分析，无需等待首个定时点

### 修复
- 🐛 修复 Web UI 页面居中问题
- 🐛 修复 Settings 返回 500 错误

## [3.2.9] - 2026-02-22

### 修复
- 🐛 **ETF 分析仅关注指数走势**（Issue #274）
  - 美股/港股 ETF（如 VOO、QQQ）与 A 股 ETF 不再纳入基金公司层面风险（诉讼、声誉等）
  - 搜索维度：ETF/指数专用 risk_check、earnings、industry 查询，避免命中基金管理人新闻
  - AI 提示：指数型标的分析约束，`risk_alerts` 不得出现基金管理人公司经营风险

## [3.2.8] - 2026-02-21

### 修复
- 🐛 **BOT 与 WEB UI 股票代码大小写统一**（Issue #355）
  - BOT `/analyze` 与 WEB UI 触发分析的股票代码统一为大写（如 `aapl` → `AAPL`）
  - 新增 `canonical_stock_code()`，在 BOT、API、Config、CLI、task_queue 入口处规范化
  - 历史记录与任务去重逻辑可正确识别同一股票（大小写不再影响）

## [3.2.7] - 2026-02-20

### 新增
- 🔐 **Web 页面密码验证**（Issue #320, #349）
  - 支持 `ADMIN_AUTH_ENABLED=true` 启用 Web 登录保护
  - 首次访问在网页设置初始密码；支持「系统设置 > 修改密码」和 CLI `python -m src.auth reset_password` 重置

## [3.2.6] - 2026-02-20
### ⚠️ 破坏性变更（Breaking Changes）

- **历史记录 API 变更 (Issue #322)**
  - 路由变更：`GET /api/v1/history/{query_id}` → `GET /api/v1/history/{record_id}`
  - 参数变更：`query_id` (字符串) → `record_id` (整数)
  - 新闻接口变更：`GET /api/v1/history/{query_id}/news` → `GET /api/v1/history/{record_id}/news`
  - 原因：`query_id` 在批量分析时可能重复，无法唯一标识单条历史记录。改用数据库主键 `id` 确保唯一性
  - 影响范围：使用旧版历史详情 API 的所有客户端需同步更新

### 修复
- 修复美股（如 ADBE）技术指标矛盾：akshare 美股复权数据异常，统一美股历史数据源为 YFinance（Issue #311）
- 🐛 **历史记录查询和显示问题 (Issue #322)**
  - 修复历史记录列表查询中日期不一致问题：使用明天作为 endDate，确保包含今天全天的数据
  - 修复服务器 UI 报告选择问题：原因是多条记录共享同一 `query_id`，导致总是显示第一条。现改用 `analysis_history.id` 作为唯一标识
  - 历史详情、新闻接口及前端组件已全面适配 `record_id`
  - 新增后台轮询（每 30s）与页面可见性变更时静默刷新历史列表，确保 CLI 发起的分析完成后前端能及时同步，使用 `silent` 模式避免触发 loading 状态
- 🐛 **美股指数实时行情与日线数据** (Issue #273)
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 ^GSPC）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源
  - 消除重复的美股识别逻辑，统一使用 `is_us_stock_code()` 函数

### 优化
- 🎨 **首页输入栏与 Market Sentiment 布局对齐优化**
  - 股票代码输入框左缘与历史记录 glass-card 框左对齐
  - 分析按钮右缘与 Market Sentiment 外框右对齐
  - Market Sentiment 卡片向下拉伸填满格子，消除与 STRATEGY POINTS 之间的空隙
  - 窄屏时输入栏填满宽度，响应式对齐保持一致

## [3.2.5] - 2026-02-19

### 新增
- 🌍 **大盘复盘可选区域**（Issue #299）
  - 支持 `MARKET_REVIEW_REGION` 环境变量：`cn`（A股）、`us`（美股）、`both`（两者）
  - us 模式使用 SPX/纳斯达克/道指/VIX 等指数；both 模式可同时复盘 A 股与美股
  - 默认 `cn`，保持向后兼容

## [3.2.4] - 2026-02-18

### 修复
- 🐛 **统一美股数据源为 YFinance**（Issue #311）
  - akshare 美股复权数据异常，统一美股历史数据源为 YFinance
  - 修复 ADBE 等美股股票技术指标矛盾问题

## [3.2.3] - 2026-02-18

### 修复
- 🐛 **标普500实时数据缺失**（Issue #273）
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 `^GSPC`）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源

## [3.2.2] - 2026-02-16

### 新增
- 📊 **PE 指标支持**（Issue #296）
  - AI System Prompt 增加 PE 估值关注
- 📰 **新闻时效性筛查**（Issue #296）
  - `NEWS_MAX_AGE_DAYS`：新闻最大时效（天），默认 3，避免使用过时信息
- 📈 **强势趋势股乖离率放宽**（Issue #296）
  - `BIAS_THRESHOLD`：乖离率阈值（%），默认 5.0，可配置
  - 强势趋势股（多头排列且趋势强度 ≥70）自动放宽乖离率到 1.5 倍

## [3.2.1] - 2026-02-16

### 新增
- 🔧 **东财接口补丁可配置开关**
  - 支持 `EFINANCE_PATCH_ENABLED` 环境变量开关东财接口补丁（默认 `true`）
  - 补丁不可用时可降级关闭，避免影响主流程

## [3.2.0] - 2026-02-15

### 新增
- 🔒 **CI 门禁统一（P0）**
  - 新增 `scripts/ci_gate.sh` 作为后端门禁单一入口
  - 主 CI 改为 `backend-gate`、`docker-build`、`web-gate` 三段式
  - CI 触发改为所有 PR，避免 Required Checks 因路径过滤缺失而卡住合并
  - `web-gate` 支持前端路径变更按需触发
  - 新增 `network-smoke` 工作流承载非阻断网络场景回归
- 📦 **发布链路收敛（P0）**
  - `docker-publish` 调整为 tag 主触发，并增加发布前门禁校验
  - 手动发布增加 `release_tag` 输入与 semver/changelog 强校验
  - 发布前新增 Docker smoke（关键模块导入）
- 📝 **PR 模板升级（P0）**
  - 增加背景、范围、验证命令与结果、回滚方案、Issue 关联等必填项
- 🤖 **AI 审查覆盖增强（P0）**
  - `pr-review` 纳入 `.github/workflows/**` 范围
  - 新增 `AI_REVIEW_STRICT` 开关，可选将 AI 审查失败升级为阻断

## [3.1.13] - 2026-02-15

### 新增
- 📊 **仅分析结果摘要**（Issue #262）
  - 支持 `REPORT_SUMMARY_ONLY` 环境变量，设为 `true` 时只推送汇总，不含个股详情
  - 默认 `false`，多股时适合快速浏览

## [3.1.12] - 2026-02-15

### 新增
- 📧 **个股与大盘复盘合并推送**（Issue #190）
  - 支持 `MERGE_EMAIL_NOTIFICATION` 环境变量，设为 `true` 时将个股分析与大盘复盘合并为一次推送
  - 默认 `false`，减少邮件数量、降低被识别为垃圾邮件的风险

## [3.1.11] - 2026-02-15

### 新增
- 🤖 **Anthropic Claude API 支持**（Issue #257）
  - 支持 `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`、`ANTHROPIC_TEMPERATURE`、`ANTHROPIC_MAX_TOKENS`
  - AI 分析优先级：Gemini > Anthropic > OpenAI
- 📷 **从图片识别股票代码**（Issue #257）
  - 上传自选股截图，通过 Vision LLM 自动提取股票代码
  - API: `POST /api/v1/stocks/extract-from-image`；支持 JPEG/PNG/WebP/GIF，最大 5MB
  - 支持 `OPENAI_VISION_MODEL` 单独配置图片识别模型
- ⚙️ **通达信数据源手动配置**（Issue #257）
  - 支持 `PYTDX_HOST`、`PYTDX_PORT` 或 `PYTDX_SERVERS` 配置自建通达信服务器

## [3.1.10] - 2026-02-15

### 新增
- ⚙️ **立即运行配置**（Issue #332）
  - 支持 `RUN_IMMEDIATELY` 环境变量，`true` 时定时任务启动后立即执行一次
- 🐛 修复 Docker 构建问题

## [3.1.9] - 2026-02-14

### 新增
- 🔌 **东财接口补丁机制**
  - 新增 `patch/eastmoney_patch.py` 修复 efinance 上游接口变更
  - 不影响其他数据源的正常运行

## [3.1.8] - 2026-02-14

### 新增
- 🔐 **Webhook 证书校验开关**（Issue #265）
  - 支持 `WEBHOOK_VERIFY_SSL` 环境变量，可关闭 HTTPS 证书校验以支持自签名证书
  - 默认保持校验，关闭存在 MITM 风险，仅建议在可信内网使用

## [3.1.7] - 2026-02-14

### 修复
- 🐛 修复包导入错误（package import error）

## [3.1.6] - 2026-02-13

### 修复
- 🐛 修复 `news_intel` 中 `query_id` 不一致问题

## [3.1.5] - 2026-02-13

### 新增
- 📷 **Markdown 转图片通知**（Issue #289）
  - 支持 `MARKDOWN_TO_IMAGE_CHANNELS` 配置，对 Telegram、企业微信、自定义 Webhook（Discord）、邮件发送图片格式报告
  - 邮件为内联附件，增强对不支持 HTML 客户端的兼容性
  - 需安装 `wkhtmltopdf` 和 `imgkit`

## [3.1.4] - 2026-02-12

### 新增
- 📧 **股票分组发往不同邮箱**（Issue #268）
  - 支持 `STOCK_GROUP_N` + `EMAIL_GROUP_N` 配置，不同股票组报告发送到对应邮箱
  - 大盘复盘发往所有配置的邮箱

## [3.1.3] - 2026-02-12

### 修复
- 🐛 修复 Docker 内运行时通过页面修改配置报错 `[Errno 16] Device or resource busy` 的问题

## [3.1.2] - 2026-02-11

### 修复
- 🐛 修复 Docker 一致性问题，解决关键批次处理与通知 Bug

## [3.1.1] - 2026-02-11

### 变更
- ♻️ `API_HOST` → `WEBUI_HOST`：Docker Compose 配置项统一

## [3.1.0] - 2026-02-11

### 新增
- 📊 **ETF 支持增强与代码规范化**
  - 统一各数据源 ETF 代码处理逻辑
  - 新增 `canonical_stock_code()` 统一代码格式，确保数据源路由正确

## [3.0.5] - 2026-02-08

### 修复
- 🐛 修复信号 emoji 与建议不一致的问题（复合建议如"卖出/观望"未正确映射）
- 🐛 修复 `*ST` 股票名在微信/Dashboard 中 markdown 转义问题
- 🐛 修复 `idx.amount` 为 None 时大盘复盘 TypeError
- 🐛 修复分析 API 返回 `report=None` 及 ReportStrategy 类型不一致问题
- 🐛 修复 Tushare 返回类型错误（dict → UnifiedRealtimeQuote）及 API 端点指向

### 新增
- 📊 大盘复盘报告注入结构化数据（涨跌统计、指数表格、板块排名）
- 🔍 搜索结果 TTL 缓存（500 条上限，FIFO 淘汰）
- 🔧 Tushare Token 存在时自动注入实时行情优先级
- 📰 新闻摘要截断长度 50→200 字

### 优化
- ⚡ 补充行情字段请求限制为最多 1 次，减少无效请求

## [3.0.4] - 2026-02-07

### 新增
- 📈 **回测引擎** (PR #269)
  - 新增基于历史分析记录的回测系统，支持收益率、胜率、最大回撤等指标评估
  - WebUI 集成回测结果展示

## [3.0.3] - 2026-02-07

### 修复
- 🐛 修复狙击点位数据解析错误问题 (PR #271)

## [3.0.2] - 2026-02-06

### 新增
- ✉️ 可配置邮件发送者名称 (PR #272)
- 🌐 外国股票支持英文关键词搜索

## [3.0.1] - 2026-02-06

### 修复
- 🐛 修复 ETF 实时行情获取、市场数据回退、企业微信消息分块问题
- 🔧 CI 流程简化

## [3.0.0] - 2026-02-06

### 移除
- 🗑️ **移除旧版 WebUI**
  - 删除基于 `http.server.ThreadingHTTPServer` 的旧版 WebUI（`web/` 包）
  - 旧版 WebUI 的功能已完全被 FastAPI（`api/`）+ React 前端替代
  - `--webui` / `--webui-only` 命令行参数标记为弃用，自动重定向到 `--serve` / `--serve-only`
  - `WEBUI_ENABLED` / `WEBUI_HOST` / `WEBUI_PORT` 环境变量保持兼容，自动转发到 FastAPI 服务
  - `webui.py` 保留为兼容入口，启动时直接调用 FastAPI 后端
  - Docker Compose 中移除 `webui` 服务定义，统一使用 `server` 服务

### 变更
- ♻️ **服务层重构**
  - 将 `web/services.py` 中的异步任务服务迁移至 `src/services/task_service.py`
  - Bot 分析命令（`bot/commands/analyze.py`）改为使用 `src.services.task_service`
  - Docker 环境变量 `WEBUI_HOST`/`WEBUI_PORT` 更名为 `API_HOST`/`API_PORT`（旧名仍兼容）

## [2.3.0] - 2026-02-01

### 新增
- 🇺🇸 **增强美股支持** (Issue #153)
  - 实现基于 Akshare 的美股历史数据获取 (`ak.stock_us_daily()`)
  - 实现基于 Yfinance 的美股实时行情获取（优先策略）
  - 增加对不支持数据源（Tushare/Baostock/Pytdx/Efinance）的美股代码过滤和快速降级

### 修复
- 🐛 修复 AMD 等美股代码被误识别为 A 股的问题 (Issue #153)

## [2.2.5] - 2026-02-01

### 新增
- 🤖 **AstrBot 消息推送** (PR #217)
  - 新增 AstrBot 通知渠道，支持推送到 QQ 和微信
  - 支持 HMAC SHA256 签名验证，确保通信安全
  - 通过 `ASTRBOT_URL` 和 `ASTRBOT_TOKEN` 配置

## [2.2.4] - 2026-02-01

### 新增
- ⚙️ **可配置数据源优先级** (PR #215)
  - 支持通过环境变量（如 `YFINANCE_PRIORITY=0`）动态调整数据源优先级
  - 无需修改代码即可优先使用特定数据源（如 Yahoo Finance）

## [2.2.3] - 2026-01-31

### 修复
- 📦 更新 requirements.txt，增加 `lxml_html_clean` 依赖以解决兼容性问题

## [2.2.2] - 2026-01-31

### 修复
- 🐛 修复代理配置区分大小写问题 (fixes #211)

## [2.2.1] - 2026-01-31

### 修复
- 🐛 **YFinance 兼容性修复** (PR #210, fixes #209)
  - 修复新版 yfinance 返回 MultiIndex 列名导致的数据解析错误

## [2.2.0] - 2026-01-31

### 新增
- 🔄 **多源回退策略增强**
  - 实现了更健壮的数据获取回退机制 (feat: multi-source fallback strategy)
  - 优化了数据源故障时的自动切换逻辑

### 修复
- 🐛 修复 analyzer 运行后无法通过改 .env 文件的 stock_list 内容调整跟踪的股票

## [2.1.14] - 2026-01-31

### 文档
- 📝 更新 README 和优化 auto-tag 规则

## [2.1.13] - 2026-01-31

### 修复
- 🐛 **Tushare 优先级与实时行情** (Fixed #185)
  - 修复 Tushare 数据源优先级设置问题
  - 修复 Tushare 实时行情获取功能

## [2.1.12] - 2026-01-30

### 修复
- 🌐 修复代理配置在某些情况下的区分大小写问题
- 🌐 修复本地环境禁用代理的逻辑

## [2.1.11] - 2026-01-30

### 优化
- 🚀 **飞书消息流优化** (PR #192)
  - 优化飞书 Stream 模式的消息类型处理
  - 修改 Stream 消息模式默认为关闭，防止配置错误运行时报错

## [2.1.10] - 2026-01-30

### 合并
- 📦 合并 PR #154 贡献

## [2.1.9] - 2026-01-30

### 新增
- 💬 **微信文本消息支持** (PR #137)
  - 新增微信推送的纯文本消息类型支持
  - 添加 `WECHAT_MSG_TYPE` 配置项

## [2.1.8] - 2026-01-30

### 修复
- 🐛 修正日志中 API 提供商显示错误 (PR #197)

## [2.1.7] - 2026-01-30

### 修复
- 🌐 禁用本地环境的代理设置，避免网络连接问题

## [2.1.6] - 2026-01-29

### 新增
- 📡 **Pytdx 数据源 (Priority 2)**
  - 新增通达信数据源，免费无需注册
  - 多服务器自动切换
  - 支持实时行情和历史数据
- 🏷️ **多源股票名称解析**
  - DataFetcherManager 新增 `get_stock_name()` 方法
  - 新增 `batch_get_stock_names()` 批量查询
  - 自动在多数据源间回退
  - Tushare 和 Baostock 新增股票名称/列表方法
- 🔍 **增强搜索回退**
  - 新增 `search_stock_price_fallback()` 用于数据源全部失败时
  - 新增搜索维度：市场分析、行业分析
  - 最大搜索次数从 3 增加到 5
  - 改进搜索结果格式（每维度 4 条结果）

### 改进
- 更新搜索查询模板以提高相关性
- 增强 `format_intel_report()` 输出结构

## [2.1.5] - 2026-01-29

### 新增
- 📡 新增 Pytdx 数据源和多源股票名称解析功能

## [2.1.4] - 2026-01-29

### 文档
- 📝 更新赞助商信息

## [2.1.3] - 2026-01-28

### 文档
- 📝 重构 README 布局
- 🌐 新增繁体中文翻译 (README_CHT.md)

### 修复
- 🐛 修复 WebUI 无法输入美股代码问题
  - 输入框逻辑改成所有字母都转换成大写
  - 支持 `.` 的输入（如 `BRK.B`）

## [2.1.2] - 2026-01-27

### 修复
- 🐛 修复个股分析推送失败和报告路径问题 (fixes #166)
- 🐛 修改 CR 错误，确保微信消息最大字节配置生效

## [2.1.1] - 2026-01-26

### 新增
- 🔧 添加 GitHub Actions auto-tag 工作流
- 📡 添加 yfinance 兜底数据源及数据缺失警告

### 修复
- 🐳 修复 docker-compose 路径和文档命令
- 🐳 Dockerfile 补充 copy src 文件夹 (fixes #145)

## [2.1.0] - 2026-01-25

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 📈 **MACD 和 RSI 技术指标**
  - MACD：趋势确认、金叉死叉信号（零轴上金叉⭐、金叉✅、死叉❌）
  - RSI：超买超卖判断（超卖⭐、强势✅、超买⚠️）
  - 指标信号纳入综合评分系统
- 🎮 **Discord 推送支持** (PR #124, #125, #144)
  - 支持 Discord Webhook 和 Bot API 两种方式
  - 通过 `DISCORD_WEBHOOK_URL` 或 `DISCORD_BOT_TOKEN` + `DISCORD_MAIN_CHANNEL_ID` 配置
- 🤖 **机器人命令交互**
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
- 🌡️ **AI 温度参数可配置** (PR #142)
  - 支持自定义 AI 模型温度参数
- 🐳 **Zeabur 部署支持**
  - 添加 Zeabur 镜像部署工作流
  - 支持 commit hash 和 latest 双标签

### 重构
- 🏗️ **项目结构优化**
  - 核心代码移至 `src/` 目录，根目录更清爽
  - 文档移至 `docs/` 目录
  - Docker 配置移至 `docker/` 目录
  - 修复所有 import 路径，保持向后兼容
- 🔄 **数据源架构升级**
  - 新增数据源熔断机制，单数据源连续失败自动切换
  - 实时行情缓存优化，批量预取减少 API 调用
  - 网络代理智能分流，国内接口自动直连
- 🤖 Discord 机器人重构为平台适配器架构

### 修复
- 🌐 **网络稳定性增强**
  - 自动检测代理配置，对国内行情接口强制直连
  - 修复 EfinanceFetcher 偶发的 `ProtocolError`
  - 增加对底层网络错误的捕获和重试机制
- 📧 **邮件渲染优化**
  - 修复邮件中表格不渲染问题 (#134)
  - 优化邮件排版，更紧凑美观
- 📢 **企业微信推送修复**
  - 修复大盘复盘推送不完整问题
  - 增强消息分割逻辑，支持更多标题格式
  - 增加分批发送间隔，避免限流丢失
- 👷 **CI/CD 修复**
  - 修复 GitHub Actions 中路径引用的错误

## [2.0.0] - 2026-01-24

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 🤖 **机器人命令交互** (PR #113)
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
  - 支持选择精简报告或完整报告
- 🎮 **Discord 推送支持** (PR #124)
  - 支持 Discord Webhook 推送
  - 添加 Discord 环境变量到工作流

### 修复
- 🐳 修复 WebUI 在 Docker 中绑定 0.0.0.0 (fixed #118)
- 🔔 修复飞书长连接通知问题
- 🐛 修复 `analysis_delay` 未定义错误
- 🔧 启动时 config.py 检测通知渠道，修复已配置自定义渠道情况下仍然提示未配置问题

### 改进
- 🔧 优化 Tushare 优先级判断逻辑，提升封装性
- 🔧 修复 Tushare 优先级提升后仍排在 Efinance 之后的问题
- ⚙️ 配置 TUSHARE_TOKEN 时自动提升 Tushare 数据源优先级
- ⚙️ 实现 4 个用户反馈 issue (#112, #128, #38, #119)

## [1.6.0] - 2026-01-19

### 新增
- 🖥️ WebUI 管理界面及 API 支持（PR #72）
  - 全新 Web 架构：分层设计（Server/Router/Handler/Service）
  - 核心 API：支持 `/analysis` (触发分析), `/tasks` (查询进度), `/health` (健康检查)
  - 交互界面：支持页面直接输入代码并触发分析，实时展示进度
  - 运行模式：新增 `--webui-only` 模式，仅启动 Web 服务
  - 解决了 [#70](https://github.com/ZhuLinsen/daily_stock_analysis/issues/70) 的核心需求（提供触发分析的接口）
- ⚙️ GitHub Actions 配置灵活性增强（[#79](https://github.com/ZhuLinsen/daily_stock_analysis/issues/79)）
  - 支持从 Repository Variables 读取非敏感配置（如 STOCK_LIST, GEMINI_MODEL）
  - 保持对 Secrets 的向下兼容

### 修复
- 🐛 修复企业微信/飞书报告截断问题（[#73](https://github.com/ZhuLinsen/daily_stock_analysis/issues/73)）
  - 移除 notification.py 中不必要的长度硬截断逻辑
  - 依赖底层自动分片机制处理长消息
- 🐛 修复 GitHub Workflow 环境变量缺失（[#80](https://github.com/ZhuLinsen/daily_stock_analysis/issues/80)）
  - 修复 `CUSTOM_WEBHOOK_BEARER_TOKEN` 未正确传递到 Runner 的问题

## [1.5.0] - 2026-01-17

### 新增
- 📲 单股推送模式（[#55](https://github.com/ZhuLinsen/daily_stock_analysis/issues/55)）
  - 每分析完一只股票立即推送，不用等全部分析完
  - 命令行参数：`--single-notify`
  - 环境变量：`SINGLE_STOCK_NOTIFY=true`
- 🔐 自定义 Webhook Bearer Token 认证（[#51](https://github.com/ZhuLinsen/daily_stock_analysis/issues/51)）
  - 支持需要 Token 认证的 Webhook 端点
  - 环境变量：`CUSTOM_WEBHOOK_BEARER_TOKEN`

## [1.4.0] - 2026-01-17

### 新增
- 📱 Pushover 推送支持（PR #26）
  - 支持 iOS/Android 跨平台推送
  - 通过 `PUSHOVER_USER_KEY` 和 `PUSHOVER_API_TOKEN` 配置
- 🔍 博查搜索 API 集成（PR #27）
  - 中文搜索优化，支持 AI 摘要
  - 通过 `BOCHA_API_KEYS` 配置
- 📊 Efinance 数据源支持（PR #59）
  - 新增 efinance 作为数据源选项
- 🇭🇰 港股支持（PR #17）
  - 支持 5 位代码或 HK 前缀（如 `hk00700`、`hk1810`）

### 修复
- 🔧 飞书 Markdown 渲染优化（PR #34）
  - 使用交互卡片和格式化器修复渲染问题
- ♻️ 股票列表热重载（PR #42 修复）
  - 分析前自动重载 `STOCK_LIST` 配置
- 🐛 钉钉 Webhook 20KB 限制处理
  - 长消息自动分块发送，避免被截断
- 🔄 AkShare API 重试机制增强
  - 添加失败缓存，避免重复请求失败接口

### 改进
- 📝 README 精简优化
  - 高级配置移至 `docs/full-guide.md`


## [1.3.0] - 2026-01-12

### 新增
- 🔗 自定义 Webhook 支持
  - 支持任意 POST JSON 的 Webhook 端点
  - 自动识别钉钉、Discord、Slack、Bark 等常见服务格式
  - 支持配置多个 Webhook（逗号分隔）
  - 通过 `CUSTOM_WEBHOOK_URLS` 环境变量配置

### 修复
- 📝 企业微信长消息分批发送
  - 解决自选股过多时内容超过 4096 字符限制导致推送失败的问题
  - 智能按股票分析块分割，每批添加分页标记（如 1/3, 2/3）
  - 批次间隔 1 秒，避免触发频率限制

## [1.2.0] - 2026-01-11

### 新增
- 📢 多渠道推送支持
  - 企业微信 Webhook
  - 飞书 Webhook（新增）
  - 邮件 SMTP（新增）
  - 自动识别渠道类型，配置更简单

### 改进
- 统一使用 `NOTIFICATION_URL` 配置，兼容旧的 `WECHAT_WEBHOOK_URL`
- 邮件支持 Markdown 转 HTML 渲染

## [1.1.0] - 2026-01-11

### 新增
- 🤖 OpenAI 兼容 API 支持
  - 支持 DeepSeek、通义千问、Moonshot、智谱 GLM 等
  - Gemini 和 OpenAI 格式二选一
  - 自动降级重试机制

## [1.0.0] - 2026-01-10

### 新增
- 🎯 AI 决策仪表盘分析
  - 一句话核心结论
  - 精确买入/止损/目标点位
  - 检查清单（✅⚠️❌）
  - 分持仓建议（空仓者 vs 持仓者）
- 📊 大盘复盘功能
  - 主要指数行情
  - 涨跌统计
  - 板块涨跌榜
  - AI 生成复盘报告
- 🔍 多数据源支持
  - AkShare（主数据源，免费）
  - Tushare Pro
  - Baostock
  - YFinance
- 📰 新闻搜索服务
  - Tavily API
  - SerpAPI
- 💬 企业微信机器人推送
- ⏰ 定时任务调度
- 🐳 Docker 部署支持
- 🚀 GitHub Actions 零成本部署

### 技术特性
- Gemini AI 模型（gemini-3-flash-preview）
- 429 限流自动重试 + 模型切换
- 请求间延时防封禁
- 多 API Key 负载均衡
- SQLite 本地数据存储

---

[Unreleased]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.29.0...HEAD
[3.29.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.28.0...v3.29.0
[3.28.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.27.0...v3.28.0
[3.27.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.26.1...v3.27.0
[3.26.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.25.0...v3.26.1
[3.25.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.24.1...v3.25.0
[3.24.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.24.0...v3.24.1
[3.24.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.23.0...v3.24.0
[3.23.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.22.0...v3.23.0
[3.22.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.21.1...v3.22.0
[3.21.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.21.0...v3.21.1
[3.21.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.20.0...v3.21.0
[3.20.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.19.0...v3.20.0
[3.19.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.18.0...v3.19.0
[3.18.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.1...v3.18.0
[3.17.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.0...v3.17.1
[3.17.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.16.0...v3.17.0
[3.16.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.15.0...v3.16.0
[3.15.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.2...v3.15.0
[3.14.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.1...v3.14.2
[3.14.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.0...v3.14.1
[3.14.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.13.0...v3.14.0
[3.13.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.12.0...v3.13.0
[3.12.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.10...v3.5.0
[3.4.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.9...v3.4.10
[3.4.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.8...v3.4.9
[3.4.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.7...v3.4.8
[3.4.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.0...v3.4.7
[3.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.22...v3.4.0
[3.3.22]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.12...v3.3.22
[3.3.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.11...v3.3.12
[3.2.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.10...v3.2.11
[2.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.5...v2.3.0
[2.2.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.4...v2.2.5
[2.2.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.3...v2.2.4
[2.2.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.14...v2.2.0
[2.1.14]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.13...v2.1.14
[2.1.13]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.12...v2.1.13
[2.1.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.11...v2.1.12
[2.1.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.10...v2.1.11
[2.1.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.9...v2.1.10
[2.1.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.8...v2.1.9
[2.1.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.7...v2.1.8
[2.1.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.6...v2.1.7
[2.1.6]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.5...v2.1.6
[2.1.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.4...v2.1.5
[2.1.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.2...v2.1.3
[2.1.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.6.0...v2.0.0
[1.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v1.0.0
