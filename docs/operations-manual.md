# DSA 操作手册：装 → 配 → 跑 → 看 → 排障

本文是一条**操作路径**，按「装上 → 配好 → 跑起来 → 看结果 → 排障」的顺序走一遍。

它刻意不重复细节：字段契约、渠道差异、`Actions` 映射、部署参数这类内容以对应专题文档为准（见第 6 节文档地图）。这里只回答「下一步敲什么、敲完在哪看」。

---

## 0. 先选一条路

| 你想要的 | 用什么 | 从哪开始 |
| --- | --- | --- |
| 不想碰代码，只想每天收报告 | 桌面客户端 | [小白客户端安装与配置](beginner-client-setup.md) |
| 在自己电脑上跑、随时改配置 | 本地 Python 运行 | 本文第 1–3 节 |
| 挂在服务器 / NAS 上长期运行 | Docker Compose | 本文 3.5 节、[部署指南](DEPLOY.md) |
| 不想维护机器，让 GitHub 帮你跑 | GitHub Actions | 本文 3.6 节、[完整配置与部署指南](full-guide.md) |
| 只用浏览器界面 | Web UI（后端自带） | 本文 4.4 节 |

五条路径共用同一套 `.env` 配置和同一条分析链路，差别只在触发方式和运行环境。

---

## 1. 装

### 1.1 前置条件

| 需要 | 版本 | 什么时候需要 |
| --- | --- | --- |
| Python | 3.11 / 3.12 | 本地运行时必须。CI 用 3.11 与 3.12 两档，Docker 基础镜像是 `python:3.11-slim-bookworm` |
| Node.js | ≥ 20.19，< 27 | 只在你要自己构建前端（`apps/dsa-web`）或桌面端（`apps/dsa-desktop`）时需要 |
| 一个 AI 模型渠道 API Key | — | 必须，至少一个（见 2.2） |

如果只想要现成的桌面客户端，不需要装 Python 和 Node，直接看 [小白客户端安装与配置](beginner-client-setup.md)。

### 1.2 本地 Python 环境

Python 环境用 `uv` 管理，避免污染系统 `python3`：

```bash
uv venv .venv
uv pip install -r requirements.txt
```

装完先冒烟一次——`--dry-run` 只取数据、不调模型，不消耗模型额度：

```bash
uv run python main.py --dry-run
```

### 1.3 造配置文件

```bash
cp .env.example .env
```

`.env` 是本地唯一的配置入口，已被 `.gitignore` 忽略，**不要提交**。

`.env.example` 约 1000 行，按注释 banner 分段，顺序大致是：自选股列表 → Futu OpenD（持仓导入）→ Anspire → 数据源配置（Tushare / TickFlow）→ 选股 → 美股 / 港股数据源 → AI 模型配置 → 搜索引擎 → 社交情绪情报（仅美股）→ 新闻时效与分析筛选 → Agent 策略对话 → 通知渠道配置（含钉钉）→ 单股推送 → 分析间隔 → 回测配置 → 定时任务配置 → 管线自愈 / 失败告警 → 代理配置 → WebUI 配置 → Web 登录认证 → 图片识别股票代码 → 数据源优先级配置 → Portfolio 配置。

每一段开头都有注释说明用途和取值，**照着要用的那一段读即可**，不用通读全文。新增配置项时同步更新 `.env.example`，这是仓库的硬规则。

### 1.4 装完先自检

```bash
uv run python scripts/check_env.py           # 全部检查
uv run python scripts/check_env.py --db      # 只看数据库
uv run python scripts/check_env.py --llm     # 只测 LLM 调用
uv run python scripts/check_env.py --fetch   # 只测数据获取
uv run python scripts/check_env.py --notify  # 只测通知推送
```

这一步能覆盖绝大多数「装完跑不起来」：配置是否加载、数据库能否打开、模型 Key 是否可用、数据源能否取到、Webhook 是否通。

---

## 2. 配

### 2.1 最少要配两件事

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `STOCK_LIST` | 自选股，逗号分隔；决定实际分析范围 | `STOCK_LIST=600519,300750,002594` |
| 模型 Key | 至少配一个 | 见 2.2 |

配置校验的严重级别分三档，默认级别是 `warn`（不阻断启动）：

- **`error`，唯一硬性必填是 `STOCK_LIST`**：为空时直接报「未配置 STOCK_LIST」并停止。其余 `error` 都是「取值非法」而不是「没配」，例如 `GENERATION_BACKEND` 写了一个不支持的取值。
- **`info`，缺省即降级**：例如没配 `TUSHARE_TOKEN` 会提示「将使用其他数据源」。
- **`warning`**：例如 `STOCK_GROUP_N` 里出现了 `STOCK_LIST` 之外的股票（只影响邮件分组路由）。

所以「没配模型 Key」不会卡在启动校验上，但 LLM 分析这一步一定跑不出结论——等于白跑一次。想要「不配置也能跑，配置后增强能力」的设计只对数据源、通知这类可选能力成立，模型 Key 和 `STOCK_LIST` 属于真正要用就得填的两项。

### 2.2 配模型渠道

| 变量 | 说明 |
| --- | --- |
| `ANSPIRE_API_KEYS` | 一 Key 同时启用大模型和中文优化联网搜索 |
| `AIHUBMIX_KEY` | 一 Key 切换全系模型 |
| `GEMINI_API_KEY` | Google AI Studio |
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `OPENAI_MODEL` | 任意 OpenAI 兼容服务（DeepSeek、通义千问等） |

多 Key、多渠道、三层配置的完整说明：[LLM 配置指南](LLM_CONFIG_GUIDE.md)；服务商预设与错误分类：[LLM 服务商配置指南](llm-providers.md)。

### 2.3 配通知（可选，可同时开多个）

企业微信、飞书、钉钉、Telegram、Discord、Slack、邮件、PushPlus、Server酱³、ntfy、Gotify、AstrBot、自定义 Webhook 都支持按需填写。

- 全部渠道清单、minimal / advanced 分层、Actions 映射、图片报告模板：[通知能力基线](notifications.md)
- 只想确认「配好没有、通不通」而不真的推送：

```bash
uv run python main.py --check-notify
```

注意：**单一通知渠道失败不会拖垮整个分析流程**，会被隔离在那一渠道内。所以「报告有、推送没有」通常只影响某一个渠道，不影响别的。

### 2.4 常用可选开关

默认值取自 `.env.example`：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `MAX_WORKERS` | 3 | 并发线程数 |
| `WEBUI_ENABLED` | false | 设为 true 时启动即拉起 Web / API 服务 |
| `WEBUI_HOST` / `WEBUI_PORT` | 127.0.0.1 / 8000 | 服务监听地址与端口 |
| `SCHEDULE_ENABLED` | false | 定时任务开关 |
| `SCHEDULE_TIME` | 18:00 | 每日执行时间，`HH:MM`；`SCHEDULE_TIMES` 可填多个 |
| `SCHEDULE_RUN_IMMEDIATELY` | true | 定时模式启动时先立刻跑一次 |
| `MARKET_REVIEW_ENABLED` | true | 是否跑大盘复盘 |
| `BACKTEST_ENABLED` | true | 是否启用回测 |
| `DATABASE_PATH` | `./data/stock_analysis.db` | 数据库路径 |
| `USE_PROXY` / `PROXY_HOST` / `PROXY_PORT` | false | 本地代理；GitHub Actions 下恒不启用 |
| `LOG_LEVEL` | INFO | 日志级别 |

配好一项就重跑一次 1.4 的自检，比一次性配完再排查便宜得多。

---

## 3. 跑

### 3.1 五种运行模式

模式由参数决定，判定顺序**从上到下、命中即返回**（`main.py` 的 dispatch）：

| 顺序 | 模式 | 命令 | 行为 |
| --- | --- | --- | --- |
| 1 | 回测 | `uv run python main.py --backtest` | 对历史分析结果做后验评估，不跑新分析 |
| 2 | 仅大盘复盘 | `uv run python main.py --market-review` | 只跑大盘、不跑个股；非交易日跳过 |
| 3 | 定时 | `uv run python main.py --schedule` | 常驻，按 `SCHEDULE_TIME` 触发 |
| 4 | 服务 | `uv run python main.py --serve` | 起 FastAPI + Web UI，同时执行分析任务 |
| 5 | 一次性分析 | `uv run python main.py` | 立即分析自选股一次后退出（默认） |

四条容易踩的补充事实：

- `--webui` 是 `--serve` 的别名，`--webui-only` 是 `--serve-only` 的别名（`main.py:1396-1399`）。
- `.env` 里 `WEBUI_ENABLED=true` 时，不带 `--serve*` 也会启动服务。
- CI 环境（`GITHUB_ACTIONS=true`）不启动服务，只做一次性分析。
- `--schedule --serve` 组合由 Web/API runtime scheduler 接管定时任务，在 Web 设置页改调度设置会作用于当前进程。

### 3.2 全部命令行参数

| 参数 | 说明 |
| --- | --- |
| `--debug` | 启用调试模式，输出详细日志 |
| `--dry-run` | 仅获取数据，不进行 AI 分析 |
| `--stocks` | 指定要分析的股票代码，逗号分隔（覆盖配置文件），如 `600519,hk00700,AAPL` |
| `--portfolio` | 使用券商真实持仓作为股票列表；当前支持 `futu`，并覆盖 `--stocks` / `STOCK_LIST` |
| `--no-notify` | 不发送推送通知 |
| `--check-notify` | 只读检查通知渠道配置，不发送通知 |
| `--single-notify` | 单股推送模式：每分析完一只股票立即推送，而不是汇总推送 |
| `--workers` | 并发线程数（默认使用配置值 `MAX_WORKERS`） |
| `--schedule` | 启用定时任务模式，每日定时执行 |
| `--no-run-immediately` | 定时任务启动时不立即执行一次 |
| `--market-review` | 仅运行大盘复盘分析 |
| `--no-market-review` | 跳过大盘复盘分析 |
| `--force-run` | 跳过交易日检查，强制执行全量分析 |
| `--webui` | 启动 Web 管理界面（`--serve` 别名） |
| `--webui-only` | 仅启动 Web 服务，不执行自动分析（`--serve-only` 别名） |
| `--serve` | 启动 FastAPI 后端服务（同时执行分析任务） |
| `--serve-only` | 仅启动 FastAPI 后端服务，不自动执行分析 |
| `--port` | FastAPI 服务端口（默认取 `WEBUI_PORT`，未配置时 8000） |
| `--host` | FastAPI 服务监听地址（默认取 `WEBUI_HOST`，未配置时 127.0.0.1） |
| `--no-context-snapshot` | 不保存分析上下文快照 |
| `--backtest` | 运行回测（对历史分析结果进行评估） |
| `--backtest-code` | 仅回测指定股票代码 |
| `--backtest-days` | 回测评估窗口（交易日数，默认使用配置） |
| `--backtest-force` | 强制回测（即使已有回测结果也重新计算） |

### 3.3 常见组合

```bash
# 只分析几只股票，不打扰群里
uv run python main.py --stocks 600519,hk00700 --no-notify

# 非交易日也想跑一次完整分析
uv run python main.py --force-run

# 调试某只股票，日志拉满
uv run python main.py --stocks 600519 --debug --workers 1

# 起服务 + 定时，长期挂着
uv run python main.py --schedule --serve
```

代码前缀的语义：裸 6 位码按 A 股处理，`hk` / 美股各按对应市场识别，`fund:` 前缀（如 `fund:003095`）进入**场外基金净值体检**链路、不做 K 线买卖点分析。完整市场边界见 [市场支持与边界](market-support.md)。

### 3.4 停掉正在跑的任务

用 `Ctrl+C`。另外要知道 `data/` 下有两个跨进程文件锁：

| 锁文件 | 谁在用 |
| --- | --- |
| `data/market_review.lock` | 大盘复盘；检测到过期锁会自动清理后重试 |
| `data/stock_analysis.db.analysis.lock` | runtime scheduler 的分析任务 |

**同一台机器上重复启动同类任务会被锁挡住**，表现就是「启动后什么都没发生」。这不是 bug，先停掉那个已经在跑的进程。

### 3.5 Docker 跑

`docker/docker-compose.yml` 里定义了两个服务：`analyzer`（定时任务）和 `server`（FastAPI）。

```bash
# 定时模式
docker compose -f ./docker/docker-compose.yml up -d

# FastAPI 模式
docker compose -f ./docker/docker-compose.yml up -d server

# 同时启动
docker compose -f ./docker/docker-compose.yml up -d analyzer server
```

要点：

- 配置从宿主机 `../.env` 以 `env_file` 载入；**不要**把 `.env` 作为单文件 volume 挂到 `/app/.env`，否则会挡住配置保存时的原子替换。
- 已挂载 `data/`、`logs/`、`reports/`、`strategies/`（只读）到容器内对应路径，产物落在宿主机。
- 容器内 `WEBUI_HOST=0.0.0.0`；对外端口由 compose 的 `${API_PORT:-8000}` 决定，即默认 8000，可用 `.env` 里的 `API_PORT` 覆盖。
- 资源限制默认 1G（完整分析建议 1G+；512M 只适合轻量 Web / 单股低并发，且建议 `MAX_WORKERS=1`）。
- 重建后确认是否生效、访问不通时的排查项见 [云端 WebUI 部署](deploy-webui-cloud.md)。

### 3.6 交给 GitHub Actions

| 工作流 | 触发 | 说明 |
| --- | --- | --- |
| `00-daily-analysis.yml` | cron `0 10 * * 1-5`（UTC，≈ 北京时间 18:00，周一至周五）；也支持手动触发 | 手动触发可选 `full` / `market-only` / `stocks-only`，并可勾选强制运行 |
| `docker-publish.yml` | 推送 `v*.*.*` tag | 发布镜像 |
| `auto-tag.yml` | 推送到 `main`（忽略 `docs/**`、`**.md` 等路径） | 自动版本号 |

- 自动 tag 是 **opt-in**：只有 commit title 含 `#patch`、`#minor`、`#major` 才触发版本号更新。
- Secrets / Variables 的完整清单（模型 Key、通知渠道、股票列表等）：[完整配置与部署指南](full-guide.md)。

---

## 4. 看

### 4.1 报告文件

| 文件 | 内容 |
| --- | --- |
| `reports/report_YYYYMMDD.md` | 当天个股日报（正文），通知里「详细报告见 …」指向的就是它 |
| `reports/market_review_YYYYMMDD.md` | 当天大盘复盘 |

### 4.2 日志

| 文件 | 内容 |
| --- | --- |
| `logs/stock_analysis_YYYYMMDD.log` | 分析主流程日志 |
| `logs/stock_analysis_debug_YYYYMMDD.log` | `--debug` 时的详细日志 |
| `logs/api_server_YYYYMMDD.log` | 服务端日志（`--serve` / `--serve-only`） |
| `logs/api_server_debug_YYYYMMDD.log` | 服务端调试日志 |

排查时优先看带日期的当天文件；日志级别由 `LOG_LEVEL` 控制。

### 4.3 数据库

`data/stock_analysis.db`（可用 `DATABASE_PATH` 改）。分析结论**在生成报告之前就已落库**，所以报告文件丢了历史也还在。

### 4.4 Web UI 与 API

```bash
uv run python main.py --serve-only
```

- Web 界面：`http://127.0.0.1:8000`（前端静态资源由仓库根目录的 `static/` 提供，`apps/dsa-web` 构建产物就输出到这里）
- API 文档：`http://127.0.0.1:8000/docs`
- 触发分析：`POST /api/v1/analysis/analyze`
- 登录认证：`.env` 里开启后，首次访问在网页设置初始密码，之后可在「系统设置 > 修改密码」修改

改了前端源码要重新构建（`cd apps/dsa-web && npm ci && npm run build`）才会反映到 `/` 页面。

### 4.5 桌面端与移动端

桌面端打包与本地开发见 [桌面端打包说明](desktop-package.md)；手机看报告走通知渠道（[通知能力基线](notifications.md)）。

---

## 5. 排障

### 5.1 三步定位

1. `uv run python scripts/check_env.py` —— 配置 / 数据库 / 模型 / 取数 / 通知，哪一层断了先定位。
2. `uv run python main.py --dry-run` —— 只取数不调模型，区分「数据源问题」还是「模型问题」，且不烧额度。
3. 打开当天的 `logs/*.log` 看具体报错栈。

### 5.2 症状 → 处置

| 症状 | 多半是 | 怎么办 |
| --- | --- | --- |
| 启动直接报错退出 | `STOCK_LIST` 为空，或某个配置项取值非法 | 看报错里点名的变量，回到第 2 节 |
| 能启动，但报告里没有分析结论 | 模型 Key / 渠道没配好 | `--check-notify` 查推送，`scripts/check_env.py --llm` 查模型；回到 2.2 |
| 取不到行情，但流程没崩 | 单一数据源失败、已按优先级降级 | 看日志里的 fallback 记录；[数据源稳定性与故障处理图示](data-source-stability.md) |
| 报告生成了，但完全没收到推送 | 渠道配置或路由问题 | `--check-notify`；[通知能力基线](notifications.md) |
| 某个渠道没收到，其他渠道正常 | 该渠道失败被隔离 | 同上，按渠道单独查 |
| 定时模式没跑 | 非交易日跳过 / `SCHEDULE_ENABLED=false` / 锁被占用 | `--force-run` 强制；确认开关；确认没有别的进程在跑 |
| 启动后什么都没发生 | 同机已有同类任务持锁 | 先停掉那个进程（见 3.4） |
| 端口被占用 | 已有服务在跑 | 换 `--port`，或停掉旧进程 |
| 网络请求超时 | 代理未配或配错 | 检查 `USE_PROXY` / `PROXY_HOST` / `PROXY_PORT` |

排查原则：数据源、LLM、通知三类都是**独立兜底**——单点失败不阻断整体。所以看到「某一步降级了」不等于流程坏了，先判断影响面再决定要不要修。

### 5.3 还有没覆盖的

- 常见配置 / 模型 / 通知 / 部署问题汇总：[FAQ](FAQ.md)
- 运行诊断分层记录：[P0](run-diagnostics-p0.md) / [P1](run-diagnostics-p1.md) / [P2](run-diagnostics-p2.md) / [P3](run-diagnostics-p3.md)
- 设置项的行为与重启语义：[设置项帮助](settings-help.md)

---

## 6. 文档地图

完整入口在 [文档中心](INDEX.md)。最常用的几个：

| 想要 | 文档 |
| --- | --- |
| 环境变量完整列表、Actions 详细配置、Docker 部署 | [完整配置与部署指南](full-guide.md) |
| 模型渠道与三层配置 | [LLM 配置指南](LLM_CONFIG_GUIDE.md)、[LLM 服务商配置指南](llm-providers.md) |
| 通知渠道基线 | [通知能力基线](notifications.md) |
| 服务器 / 云平台部署 | [部署指南](DEPLOY.md)、[云端 WebUI 部署](deploy-webui-cloud.md) |
| 桌面端打包 | [桌面端打包说明](desktop-package.md) |
| Bot / IM 接入 | [Bot 命令与接入](bot-command.md) |
| 市场与代码前缀边界 | [市场支持与边界](market-support.md) |
| 版本变化 | [更新日志](CHANGELOG.md) |

---

**维护约定**：本文只写「怎么操作」。字段契约、渠道细节、部署参数以对应专题文档为准；改动 CLI 参数、运行模式、产物路径时同步更新第 3、4 节。
