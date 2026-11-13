# 配置别名收敛与迁移方案

本文记录设置页里历史配置别名的处理策略，以及后续如果要真正下线旧 key，需要怎么迁移。它不是用户手册，而是维护者做配置迁移时的检查清单。

## 当前策略

设置页默认只展示一个可编辑入口，但运行时继续兼容旧 key：

| 概念 | 规范 key | 兼容 key | 运行时优先级 |
| --- | --- | --- | --- |
| DeepSeek Key | `DEEPSEEK_API_KEYS` | `DEEPSEEK_API_KEY` | 多 Key > 单 Key |
| Gemini Key | `GEMINI_API_KEYS` | `GEMINI_API_KEY` | 多 Key > 单 Key |
| OpenAI 兼容 Key | `OPENAI_API_KEYS` | `OPENAI_API_KEY` | 多 Key > 单 Key |
| Anthropic Key | `ANTHROPIC_API_KEYS` | `ANTHROPIC_API_KEY` | 多 Key > 单 Key |
| 定时时间 | `SCHEDULE_TIMES` | `SCHEDULE_TIME` | `SCHEDULE_TIMES` 为空时回退 |
| 启动后立即运行 | `SCHEDULE_RUN_IMMEDIATELY` | `RUN_IMMEDIATELY` | 由配置解析逻辑继承 |
| Agent 生成后端 | `GENERATION_BACKEND` + `AGENT_BACKEND` | `AGENT_GENERATION_BACKEND` | 新字段优先，旧字段仅兼容 |

设置页的行为：

- 模型 Key：多 Key 字段优先；只有旧版单 Key 已配置、且多 Key 为空时才显示单 Key；两者都为空时显示多 Key。
- 调度字段：由“定时任务”卡片统一维护，不在通用字段区重复展示。
- 历史生成后端别名：保留读取兼容，不再平铺展示。

## 为什么不能直接删旧 key

这些 key 可能存在于：

- 用户本地 `.env`；
- Docker Compose `env_file` / `docker run --env-file`；
- GitHub Actions repository secrets；
- 桌面端用户目录下的配置文件；
- 第三方部署模板或脚本。

直接删除会导致升级后配置“看起来还在、实际未生效”，比保留别名更危险。

## 推荐迁移步骤

### Phase 1：UI 收敛（已完成）

- 设置页只展示规范字段；
- 旧字段保留读取，不删除；
- 文档说明优先级。

### Phase 2：检测与提示（建议）

新增只读诊断，在设置页或 Agent 体检里提示：

- 检测到 `*_API_KEY` 非空、`*_API_KEYS` 为空；
- 检测到 `SCHEDULE_TIME` 非空、`SCHEDULE_TIMES` 为空；
- 检测到 `RUN_IMMEDIATELY` 与 `SCHEDULE_RUN_IMMEDIATELY` 不一致；
- 只提示，不自动改。

### Phase 3：迁移脚本

已提供 `scripts/migrate_legacy_config.py`：

```bash
# 默认 dry-run，只打印将写入的规范 key，不打印密钥值
uv run python scripts/migrate_legacy_config.py --env-file .env

# 确认后写入，并在同目录生成 .env.bak.<timestamp>
uv run python scripts/migrate_legacy_config.py --env-file .env --apply
```

脚本要求：

- `--dry-run` 为默认行为，只输出将写入的 key，不输出密钥值；
- `--apply` 才写 `.env`；
- 写入前备份原 `.env`；
- 只做“单 Key → 多 Key”“旧时间 → 新时间”这类可逆映射；
- 不删除旧 key，保留一个版本周期；
- 不迁移 GitHub Actions / Docker secrets，文档提示用户自行同步；
- 失败时不留下半写入状态。

### Phase 4：下线旧 key（至少一个发布周期后）

- 发布说明明确列出移除的 key；
- 启动时对仍存在的旧 key 打 warning，并提示迁移命令；
- 删除兼容读取前，先确认没有代码路径只读旧 key；
- 回滚方案：恢复旧 key 读取代码并重新发布，不依赖数据库迁移。

## 验证要求

- 配置解析单测覆盖“只有旧 key”“只有新 key”“两者都有”“两者都空”；
- 设置页测试覆盖只显示一个字段；
- 迁移脚本测试覆盖 dry-run、备份、幂等和失败回滚；
- 文档同步 `.env.example`、`docs/full-guide.md`、`docs/settings-help.md`。

## 不做的事

- 不在 UI 层自动把密钥从旧字段复制到新字段；
- 不在没有备份的情况下改写用户 `.env`；
- 不把 GitHub Actions / Docker secrets 当作可自动迁移的文件；
- 不为了“看起来干净”删除仍被运行时读取的兼容 key。
