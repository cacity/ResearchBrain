# ResearchBrain 0.4.2 测试版说明

发布日期：2026-09-05

## 本版重点

- 修正多轮调研在工具调用额度临界点直接失败并显示
  `Research tool-call budget was exhausted` 的问题。
- 当模型提出的检索批次超过剩余额度时，只执行可容纳的调用，跳过其余调用，并基于已筛选证据
  继续综合、审查和输出受限答案。
- 本地或混合调研达到停止条件后不再错误转入联网检索。
- 每个检索式自动补读排名最高的一篇文献，避免全文补读过早耗尽预算；子智能体为主流程保留
  工具与模型额度。
- 调研过程会显示预算收束事件，回答完成后自动清除临时待办提示。

## 安装产物

| 文件                                               |          大小 | SHA-256                                                            |
| -------------------------------------------------- | ------------: | ------------------------------------------------------------------ |
| `ResearchBrain_0.4.2_x64-setup.exe`                | 157,976,415 B | `3f1ffba8f41cff2205420f58435cf24ad59b4003d2c9d39fe9f7998a83c7f7f4` |
| `ResearchBrain_0.4.2_x64_en-US.msi`                | 159,059,968 B | `e014b605657a82ca78d2db6b8f373097822ae55131aa3a577cff4ab48672b639` |
| `researchbrain-sidecar-x86_64-pc-windows-msvc.exe` | 155,944,832 B | `60e5cce94e06d3eb394b28257b9f5d38b81572e07cf5840b5e75a169e6dc38c3` |

## 已完成验证

- 新增“模型提出的工具批次大于剩余预算仍能完成回答”的端到端编排回归。
- Python 全量测试、Ruff format/check。
- TypeScript typecheck、Vite production build、Prettier。
- Markdown、在线调研、文献状态、Skills/Harness 与移动端 Playwright 回归。
- Rust fmt/check/clippy、Tauri release build和打包 sidecar MCP 烟雾测试。

## 尚需实测

- 安装 `0.4.2` 后，在真实文库中重新运行截图所示的球谐分析调研问题。
- 在本地、混合和在线三种模式下观察预算收束提示及最终限制说明。
