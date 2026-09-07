# ResearchBrain 0.4.0 测试版说明

发布日期：2026-09-04

## 本版重点

- 主研究流程改为模型选择、宿主约束的 `AgentAction -> tool_result -> next turn` 循环。
- 本地与在线查询使用模型实际选择的工具参数，同时保留 Schema、文库、域名、预算和审批门禁。
- Phase checkpoint 保存计划、证据账本、覆盖、计数器、工具观察、草稿和 Reviewer 状态；恢复时不再
  重复已完成的检索。
- Subagent 使用独立只读工具循环，工具结果进入下一轮模型上下文，再由父 Agent 校验证据 ID。
- 完成 ResearchIntent、中英文来源检索式、逐主张 Reviewer、Steering/Abort、Context Compaction、
  会话树、Follow-up 队列和诊断时间线。
- 修正设置页滚动和配置持久化相关界面回归测试，并更新在线研究、文献状态和移动端视觉检查。

## 安装产物

| 文件                                               |          大小 | SHA-256                                                            |
| -------------------------------------------------- | ------------: | ------------------------------------------------------------------ |
| `ResearchBrain_0.4.0_x64-setup.exe`                | 157,976,936 B | `11588feb04a9a30f378d648cd3dd69d1a445467f5efec39397d0afe423b5a143` |
| `ResearchBrain_0.4.0_x64_en-US.msi`                | 159,059,968 B | `5bb4e63aea77be2c28edc8471dba7f314beb1f5320744bfe719e470663becca0` |
| `researchbrain-sidecar-x86_64-pc-windows-msvc.exe` | 155,942,008 B | `bb79d3c12833cc50404bc68b4a51f85d431950554d35820871d4202c9370e631` |

## 已完成验证

- Python 全量测试、Ruff format/check。
- TypeScript typecheck、Vite production build、Prettier。
- Markdown 对话、在线研究、文献状态、设置滚动和移动端 Playwright 回归。
- Rust fmt/check/clippy 和 Tauri release build。
- 源码模式与打包 sidecar 的 MCP 工具列举/调用烟雾测试。
- 公开仓库文件审计。

## 尚需发布前实测

- 使用真实 DeepSeek 配置运行本地、混合、在线、无证据和澄清用例。
- 使用真实 MiniMax 配置验证摘要、全文块和查询向量一致性。
- 在另一台干净 Windows 机器验证安装、升级、卸载及用户数据保留。
- 对固定质量集执行人工盲评；这些结果不能由单元测试替代。
