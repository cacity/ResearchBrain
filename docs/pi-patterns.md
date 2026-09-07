# ResearchBrain 对 Pi 模式的采用范围

ResearchBrain 不直接嵌入 `@earendil-works/pi-agent-core`。后端是 Python，数据访问受文库边界和
用户授权约束；直接引入 coding-agent 的 Shell、任意文件访问和 TUI 不会提高文献证据质量，反而会
扩大桌面应用的权限面。这里采用的是 Pi Agent Core 的运行模式，而不是复制其产品形态。

| Pi 能力                       | ResearchBrain 状态 | 对应实现                                                                    |
| ----------------------------- | ------------------ | --------------------------------------------------------------------------- |
| Stateful agent loop           | 已改造采用         | 模型返回 `AgentAction`，状态机和预算验证后执行工具、综合、审查或结束        |
| Structured/custom messages    | 已改造采用         | `AgentAction`、`ToolResultMessage`、持久化事件与 SSE                        |
| Tool schema validation        | 已采用             | `ResearchToolRegistry` + Pydantic 参数模型                                  |
| Parallel/sequential tools     | 已采用             | 注册工具执行模式、并发上限及有序结果归并                                    |
| Tool lifecycle events         | 已采用             | `tool_execution_start`、`tool_result`、耗时、错误和 UI 时间线               |
| before/after tool hooks       | 已采用             | 编排器实际安装策略链；写工具还必须通过一次性审批和幂等检查                  |
| Abort signal                  | 已采用             | 取消贯穿模型和工具；即时 Steering 可中止当前可取消工具并在同一运行重新规划  |
| Context transform/compaction  | 已改造采用         | token 预算、结构化 checkpoint；旧回答只作检索假设，证据权重固定为零         |
| Steering                      | 已采用             | constraint/correction/clarification 在阶段边界重校验意图、查询、证据和覆盖  |
| Follow-up queue               | 已采用             | 持久化队列支持目标分支、排序、删除，并在当前运行结束后顺序启动              |
| Continue from safe checkpoint | 已采用             | 保存计划、证据账本、覆盖、计数器、草稿和审查；恢复时跳过已完成检索          |
| Subagent loop                 | 已改造采用         | 只读子任务有独立预算和允许工具，工具结果进入下一模型轮，父 Agent 校验后合并 |
| shouldStopAfterTurn           | 已采用             | 统一检查覆盖、预算、无收益轮次、审批、澄清、审查阻塞和取消                  |
| Unified multi-provider API    | 接口已抽象         | 当前内置生成后端为 DeepSeek；尚未实现 Pi 式 provider registry               |
| Coding tools, Shell, TUI      | 不适用             | 不向文献问答模型开放任意系统权限                                            |

科研问答额外增加了 Pi 本身不负责的约束：证据等级、同主题准入、DOI/PDF 写入审批、引用 ID
账本、引用语义审查，以及成文后的主题排除门禁。这些约束比通用 coding-agent loop 更直接决定
ResearchBrain 的回答可靠性。

与 Pi 不同，ResearchBrain 的模型动作不是无限自由循环。工具名、参数、文库作用域、联网域名、并发、
步数、超时和写入权限均由宿主验证；模型不能伪造 `tool_result`，也不能绕过审批直接写入文库。这是面向
本地文献和可追溯证据的有意约束。
