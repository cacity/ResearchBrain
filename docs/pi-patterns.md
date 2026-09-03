# ResearchBrain 对 Pi 模式的采用范围

ResearchBrain 不直接嵌入 `@earendil-works/pi-agent-core`。后端是 Python，数据访问受文库边界和
用户授权约束；直接引入 coding-agent 的 Shell、任意文件访问和 TUI 不会提高文献证据质量，反而会
扩大桌面应用的权限面。这里采用的是 Pi Agent Core 的运行模式，而不是复制其产品形态。

| Pi 能力                       | ResearchBrain 状态 | 对应实现                                                        |
| ----------------------------- | ------------------ | --------------------------------------------------------------- |
| Stateful agent loop           | 已改造采用         | `ResearchOrchestrator`、`ResearchStateMachine`、持久化 run/step |
| Structured/custom messages    | 已改造采用         | SSE research events 与持久化步骤事件                            |
| Tool schema validation        | 已采用             | `ResearchToolRegistry` + Pydantic 参数模型                      |
| Parallel/sequential tools     | 已采用             | 注册工具执行模式及有序结果归并                                  |
| Tool lifecycle events         | 已采用             | `tool_execution_start/end`，UI 显示调用、完成和失败数           |
| before/after tool hooks       | 已预留并测试       | registry hooks；写工具仍必须走 approval API                     |
| Abort signal                  | 已采用             | `CancellationSignal` 贯穿模型、工具和运行 API                   |
| Context transform             | 已改造采用         | 历史裁剪；旧回答只作为检索假设，证据权重固定为零                |
| Steering                      | 已采用             | 阶段边界消费用户补充约束                                        |
| Follow-up queue               | 尚未独立实现       | 当前通过下一条持久化对话运行实现，不宣称等价于 Pi 队列          |
| Continue from exact tool turn | 部分采用           | 同一 run 可诊断/重试；重试会重放只读阶段，写任务依靠幂等键去重  |
| Unified multi-provider API    | 接口已抽象         | 当前内置生成后端为 DeepSeek；不是 Pi provider registry          |
| Coding tools, Shell, TUI      | 不适用             | 不向文献问答模型开放任意系统权限                                |

科研问答额外增加了 Pi 本身不负责的约束：证据等级、同主题准入、DOI/PDF 写入审批、引用 ID
账本、引用语义审查，以及成文后的主题排除门禁。这些约束比通用 coding-agent loop 更直接决定
ResearchBrain 的回答可靠性。
