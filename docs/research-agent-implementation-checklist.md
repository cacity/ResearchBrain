# ResearchBrain 研究 Agent 实施清单

更新时间：2026-09-04
核对分支：`feature/pi-research-orchestrator`
核对基线：`612bba3` 及当前功能分支工作树

本文档是研究 Agent 后续实施和验收的唯一进度清单。每完成一个小项，就在同一提交中把对应的
`[ ]` 改为 `[x]`，并补充测试或验收记录。只有接口、提示词或局部代码而未接入真实流程时，
不得打勾。

## 状态规则

- `[x]`：代码已进入实际运行路径，自动化测试通过，行为可由 API 或 UI 观察。
- `[ ]`：未实现、部分实现，或尚未完成测试和实际验收。
- 历史回答不是完成证据；当前代码、测试输出和新建会话的运行结果才是依据。
- 全部勾选后仍须通过固定质量集、真实模型测试和 Windows 安装包验收。

## 0. 基线与质量门禁

- [x] 持久化研究运行、步骤、事件、证据账本和会话记忆。
- [x] 支持 `local`、`hybrid`、`online` 三种模式。
- [x] 模型结构化输出经过 Pydantic Schema 校验。
- [x] 建立“球谐分析不得混入多波束声呐”的回归测试。
- [x] 无证据或全被主题门禁拒绝时返回可见结果。
- [x] 记录 0.3.1 基线：102 项 Python 测试、Ruff、TypeScript、Vite、Tauri 构建通过。
- [x] 为下列每个未完成模块建立独立测试分组（见 `docs/research-agent-test-groups.md`）。
- [x] 保存固定质量集的覆盖率、引用支持率、耗时和模型调用基线。
- [x] 对旧流程和新 Agent Loop 做可复现的同题盲评。

## 1. 理解问题与 ResearchIntent

### 已有基础

- [x] `intake` 接收原始问题、最近历史和会话记忆。
- [x] 历史回答只作检索线索，证据权重为零。
- [x] Planner 输出简要 intent、子问题、主题词、排除词和完成条件。
- [x] Planner 提示词要求按运行日期解释“最近几年”等相对时间。
- [x] 已知跨域歧义可经过确定性主题门禁。

### 待实现

- [x] 新增 `ResearchIntent` Schema 和任务类型。
- [x] 增加 `domains`、`research_objects` 和 `methods`。
- [x] 增加 `data_requirements`，记录数据类型、分辨率和来源要求。
- [x] 增加规范化的 `time_range`、`geography` 和语言限制。
- [x] 增加 `must_answer`、`must_include`、`must_exclude` 和 `deliverables`。
- [x] 增加 `ambiguities`、`assumptions` 和 `clarification_required`。
- [x] 用确定性规则先抽取显式年份、地域、范围、格式和排除条件。
- [x] Intake 模型只补充隐含意图，不得覆盖显式用户限制。
- [x] 新增 Topic Validator，独立校验领域、对象、方法和排除概念。
- [x] 阻塞性歧义进入 `ask_user`，回答后继续同一运行。
- [x] ResearchIntent 贯穿 Planner、检索、筛选、Reviewer 和最终门禁。
- [x] UI 展示识别出的范围、假设和待确认项。
- [x] 增加中英文、多义词、相对日期和复合任务测试。

## 2. 拆分子问题

- [x] `ResearchSubquestion` 保存稳定 ID、问题和最低证据等级。
- [x] Planner 可拆分最多 10 个子问题。
- [x] 模型失败时，中文复合问题仍能回退拆分。
- [x] 覆盖矩阵通过 `Q1`、`Q2` 等 ID 关联证据。
- [x] 增加人物/工作、数据、方法、流程、结果、局限、比较、空白等类型。
- [x] 增加优先级、依赖关系和单项完成判据。
- [x] 确保每个 `must_answer` 至少映射一个子问题。
- [x] 合并重复子问题，阻止子问题超出原始意图。
- [x] 增加“漏掉用户要求”和“自行扩题”的确定性测试。

## 3. 中英文及来源专用检索式

### 已有基础

- [x] `ResearchPlan` 保存普通字符串检索式。
- [x] Assessor 可在覆盖不足时生成补充检索式。
- [x] 检索式会去重并受数量预算限制。
- [x] 多轮本地检索会使用上一轮的缺口查询。

### QuerySpec 改造

- [x] 新增 `QuerySpec`：ID、子问题 ID、语言、来源和查询文本。
- [x] 保存核心概念、同义词、缩写、排除词、日期和生成理由。
- [x] 每个子问题至少生成一个中文本地检索式。
- [x] 每个子问题至少生成一个英文核心检索式。
- [x] 每个子问题至少生成一个英文同义词/缩写扩展式。
- [x] 为 PubMed 生成适用的 MeSH/字段检索式。
- [x] 为 arXiv、OpenAlex 和 Crossref 生成来源适配检索式。
- [x] 本地查询优先题名、摘要、关键词和全文术语，不直接复用冗长原问题。
- [x] 建立受控中英文术语映射并保留原始词。
- [x] 记录每条查询的命中、相关、重复和分数分布。
- [x] 零命中时扩展同义词，噪声过高时增加领域限定和排除词。
- [x] 查询改写受固定轮数限制，连续无收益时停止。
- [x] UI 按子问题和来源显示查询及效果。
- [x] 增加 Schema、来源适配和查询改写测试。

## 4. 多轮本地检索

- [x] 支持最多三轮本地补检索，并可配置预算。
- [x] 本地检索通过受控 `search_library` 工具执行。
- [x] 工具参数校验、调用预算和并行结果归并已接通。
- [x] 证据按指纹去重，并限制单篇文献的片段占比。
- [x] 每轮执行 relevant/adjacent/irrelevant 三级筛选。
- [x] 邻近和无关证据保留审计，但不参与综合和 DOI 候选。
- [x] 增加题名/摘要、关键词、向量结果的可配置融合与重排。
- [x] 支持年份、类型、作者、期刊和证据等级过滤。
- [x] 记录 Recall@k、MRR 和 nDCG 等可选指标。
- [x] UI 解释每篇候选为何入选或排除。

## 5. 阅读题录、摘要和全文

- [x] 区分 metadata、structured_abstract、fulltext_section、fulltext_page。
- [x] 本地检索可返回全文块、章节和页码。
- [x] 在线题录/摘要不会冒充全文证据。
- [x] 证据面板可显示来源、片段、章节和页码。
- [x] 增加 `get_item` 工具读取完整题录、标识符、附件和处理状态。
- [x] 增加 `read_fulltext_chunks`，按文献、章节、页码和查询读取互补片段。
- [x] 关键文献执行多片段阅读，而非只用首次命中的一个片段。
- [x] 方法、数值、图、表、公式问题自动提高最低证据等级。
- [x] 支持图题、表题、公式邻近文本和参考文献段落定向读取。
- [x] 记录已读与未读范围，禁止把局部检索称为“读完全文”。
- [x] 对扫描 PDF、解析失败和页码缺失给出明确限制。
- [x] 增加证据等级、跨页片段和图表问答测试。

## 6. 证据覆盖矩阵与缺口判断

- [x] 每个子问题有 covered、partial 或 insufficient_evidence 状态。
- [x] 覆盖项记录证据等级、证据 ID、缺失内容和下一查询。
- [x] 每轮本地检索及联网后重新计算覆盖。
- [x] 混合模式仅在本地覆盖不足时联网。
- [x] 确定性检查 covered 项的证据存在且达到最低等级。
- [x] 检查覆盖是否只依赖同一文献的重复片段。
- [x] 人物、方法比较和结论类问题支持最低文献多样性要求。
- [x] 记录每个缺口不能回答的具体原因。
- [x] 覆盖矩阵接入 `shouldStopAfterTurn`。
- [x] 增加伪覆盖、单一来源覆盖和等级不足测试。

## 7. 联网学术搜索

- [x] 支持 Crossref、OpenAlex、arXiv 和 PubMed 元数据搜索。
- [x] 记录各来源成功、失败、超时和结果数量。
- [x] 在线证据进入统一账本并接受主题筛选。
- [x] Google Scholar 仅浏览器跳转，不自动抓取。
- [x] 在线 DOI 可经用户批准后导入。
- [x] QuerySpec 明确目标来源，不把同一字符串无差别发送给所有来源。
- [x] 按 DOI、PMID、arXiv ID 和规范化题名生成跨来源合并报告。
- [x] 优先保留信息完整、可追溯且有摘要的记录。
- [x] 增加种子文献的参考文献和后续引用追踪。
- [x] 组合时间排序与相关性排序。
- [x] 增加来源级重试、限流退避及降级说明。
- [x] 增加来源夹具测试和可选真实服务烟雾测试。

## 8. DOI/PDF、解析和向量化闭环

- [x] 在线发现 DOI 后发出 `approval_available`。
- [x] 用户批准前不写入文库。
- [x] DOI 规范化和唯一标识避免重复题录。
- [x] DOI 导入后可查询合法开放 PDF。
- [x] PDF 按内容哈希保存并排队解析、向量化。
- [x] 等待期内完成的全文可回到同一运行证据账本。
- [x] 超时后可用现有摘要继续并保留任务状态。
- [x] 将 `lookup_doi`、`import_dois`、`queue_fulltext`、`job_status`、`parse_pdf`、
      `embed_document` 注册为 Agent 受控工具。
- [x] 所有写工具接入审批、文库作用域、幂等键和审计日志。
- [x] Agent 根据缺口决定查题录、获取全文或等待解析。
- [x] 解析完成后从暂停 action turn 继续，而不是重跑全部只读阶段。
- [x] 增加已有摘要/PDF/向量、重复 DOI 和失败重试端到端测试。

## 9. 跨文献比较与综合

- [x] Synthesizer 接收问题、覆盖矩阵和筛选后的证据包。
- [x] 正文只能引用当前证据账本 ID。
- [x] 成文后主题门禁会移除已知跨域污染。
- [x] 输出列出未覆盖问题和不可用在线来源。
- [x] 新增 `ComparisonMatrix`，记录对象、数据、方法、流程、结果和局限。
- [x] 区分文献原述、跨文献归纳和系统推断。
- [x] 对互相矛盾的结果保留条件差异并交给 Reviewer。
- [x] “谁做过”输出作者、年份、文献和贡献的对应关系。
- [x] “数据流程”合并共同步骤并保留各文献特有步骤。
- [x] “缺陷”区分作者自述局限和系统推断。
- [x] 增加矩阵完整性及跨文献矛盾测试。

## 10. 初稿、引用和主题安全

- [x] 初稿生成前完成计划、检索、筛选和覆盖判断。
- [x] 模型不能引用未提供的证据 ID。
- [x] 引用 ID 必须真实出现在正文。
- [x] 证据准入、综合和成文后共有三道主题检查。
- [x] 把每个事实性主张拆成稳定 `claim_id`。
- [x] 保存 claim、citation 和 evidence span 对应关系。
- [x] 限制单个引用支撑过多不同主张。
- [x] 明确标记证据不足、推断和建议性内容。
- [x] 增加引用漂移、无引用事实和主题漏判测试。

## 11. Reviewer 与修订

### 已有基础

- [x] 确定性检查未知引用、正文未使用引用和缺失引用。
- [x] 独立 Reviewer 接收问题、子问题、覆盖、初稿和证据原文。
- [x] Reviewer 可报告不支持、无效引用、等级越界、漏答和矛盾。
- [x] 阻塞问题最多触发一次修订。
- [x] 修订后再次执行确定性引用检查和主题门禁。

### 逐主张审查流水线

- [x] Claim Extractor 将初稿拆成可单独核验的事实主张。
- [x] Citation Validator 检查每个主张附近的引用。
- [x] Evidence Level Validator 按主张类型检查证据等级。
- [x] Entailment Reviewer 逐项判断直接支持、部分支持或不支持。
- [x] Contradiction Reviewer 比较证据之间及证据与正文的矛盾。
- [x] Coverage Reviewer 独立检查 `must_answer` 和全部子问题。
- [x] Topic Reviewer 按领域、对象和方法检查跨域引用。
- [x] 审查结果保存 claim ID、严重度、证据 ID、理由和修改建议。
- [x] 修订器只能删除、降级或用现有证据改写。
- [x] 修订后重新执行完整语义 Reviewer。
- [x] 仍有阻塞问题时删除主张或返回受限答案，不发布原句。
- [x] UI 展示阻塞、警告和被删除/降级的主张。
- [x] 增加错误引文、摘要冒充全文、图表越界、矛盾和漏答测试。

## 12. 真正的 Action/Tool Loop

### 当前固定编排基础

- [x] 当前按固定状态机执行计划、检索、评估、综合、审查和修订。
- [x] 状态机拒绝非法阶段跳转。
- [x] 运行有模型步数、工具数、轮数和超时预算。
- [x] `search_library`、`search_online` 已走统一工具注册表。

### Agent Action 协议

- [x] 新增 `AgentAction` Schema。
- [x] 支持 `call_tools` 和一个或多个结构化工具调用。
- [x] 支持可恢复的 `ask_user`。
- [x] 支持受限的 `delegate`。
- [x] 支持 `synthesize`、`review` 和 `finish`。
- [x] 每轮模型只决定下一步动作，不能伪造工具结果。
- [x] 工具结果以 `tool_result` 消息追加到运行上下文。
- [x] 下一轮根据证据、覆盖和预算继续决策。
- [x] 未知工具、无效参数、越权和重复写入返回结构化错误。
- [x] 每个 turn、action、tool call 和结果均持久化。
- [x] 应用重启后从最后完整 turn 恢复。
- [x] 增加空证据、补检索、工具失败、等待用户和预算耗尽测试。

### 工具清单

- [x] `search_library`。
- [x] `search_online`。
- [x] `get_item`。
- [x] `read_fulltext_chunks`。
- [x] `lookup_doi`。
- [x] `import_dois`。
- [x] `queue_fulltext`。
- [x] `job_status`。
- [x] `parse_pdf`。
- [x] `embed_document`。
- [x] `export_references`。
- [x] 每个工具定义最小 Schema、权限、并发、超时和幂等策略。

## 13. `beforeToolCall` / `afterToolCall`

- [x] Registry 已预留 `before_call` 和 `after_call` 接口。
- [x] Registry 拒绝直接注册未审批写工具。
- [x] 工具执行会发出开始/结束事件并返回结构化错误。
- [x] Orchestrator 实际安装 `beforeToolCall` 策略链。
- [x] 调用前校验 Schema、文库作用域、预算、审批、域名和幂等键。
- [x] 写工具调用前生成明确审批请求。
- [x] Orchestrator 实际安装 `afterToolCall` 策略链。
- [x] 调用后规范化结果并更新证据账本、覆盖矩阵。
- [x] 调用后记录耗时、来源状态、错误分类和使用量。
- [x] 外部文本按不可信数据隔离，阻断其中的提示注入。
- [x] 策略拒绝可被 Agent 理解，但不能被绕过。
- [x] 增加钩子顺序、拒绝、异常、审批和注入测试。

## 14. `shouldStopAfterTurn`

- [x] 定义 `StopDecision`：stop、reason、status、next_requirement。
- [x] 检查取消和应用关闭信号。
- [x] 检查超时、模型步数、工具数、查询轮数和费用预算。
- [x] 检查必答子问题的覆盖情况。
- [x] 检查连续多轮无新增相关证据。
- [x] 检查待审批、待澄清和后台任务。
- [x] 检查 Reviewer 是否仍有阻塞问题。
- [x] 仅在可输出完整、受限或明确失败结果时允许 finish。
- [x] 每轮持久化停止判断和理由。
- [x] 增加完成、退让、超时、取消、等待和防死循环测试。

## 15. 事件流、Steering 和 Abort

- [x] 事件带递增序号并持久化。
- [x] SSE 返回阶段、工具、证据、覆盖、审查和答案事件。
- [x] 客户端重连可读取持久化事件。
- [x] API/UI 可取消运行。
- [x] 取消信号传入模型和工具。
- [x] Steering 在阶段边界进入 Assessor/Synthesizer 上下文。
- [x] Steering 区分 constraint、correction 和 follow_up。
- [x] 新约束触发意图、查询、候选证据和覆盖重校验。
- [x] 冲突旧证据标记失效，不再参与综合。
- [x] 立即约束可中止当前可取消工具并重新规划。
- [x] UI 显示 Steering 何时生效及影响步骤。
- [x] 增加执行中取消、阶段 Steering 和 SSE 重连测试。

## 16. Context Compaction 与会话记忆

- [x] 最近 8 条裁剪后的历史消息进入模型上下文。
- [x] 会话记忆保存目标、约束、术语、来源标识和未解决问题。
- [x] 历史回答只作 navigation hypotheses。
- [x] 最终回答后更新持久化记忆。
- [x] 使用 tokenizer 估算上下文，而非只按消息数和字符数裁剪。
- [x] 达到窗口阈值时生成结构化 compaction checkpoint。
- [x] 保存摘要覆盖边界、生成模型和版本。
- [x] 无损保留关键用户限制和 DOI/PMID/arXiv 标识。
- [x] 新问题重新读取原证据，不把压缩摘要当证据。
- [x] 新证据冲突时更新或移除旧假设。
- [x] 增加超长会话、跨轮约束、错误历史和恢复测试。

## 17. 会话树和分支

- [x] 支持多个持久化线性会话和运行历史。
- [x] `chat_messages` 增加 `parent_message_id` 或等价关系。
- [x] 分支保存根消息、名称和创建来源。
- [x] 可从任意历史用户消息创建分支。
- [x] 分支只继承节点之前的约束和来源标识。
- [x] 旧回答不作为分支证据，仍需重新检索。
- [x] UI 展示路径、切换入口和当前分支。
- [x] 删除/归档不破坏其他分支引用。
- [x] 增加迁移、创建、并发和历史读取测试。

## 18. Subagent

- [x] 可并行启动只读 Scout，返回结构化 `ScoutFinding`。
- [x] Scout 只接收子问题和已筛选证据，无写权限。
- [x] Scout 结果可供 Assessor 使用。
- [x] Subagent 有独立任务、预算、上下文和允许工具列表。
- [x] Subagent 有自己的 action/tool loop，而非单次模型调用。
- [x] Subagent 可执行本地检索和定向全文阅读。
- [x] 在线 Subagent 按来源或策略分工并避免重复搜索。
- [x] Subagent 只返回结构化证据、缺口和查询建议。
- [x] Aggregator 校验证据 ID 和主题后再合并。
- [x] 父 Agent 可取消单个或全部 Subagent。
- [x] 限制并发、总步数、工具数和上下文大小。
- [x] 固定质量集 A/B 验证 Subagent 的收益和成本。

## 19. Follow-up 队列与精确恢复

- [x] 区分当前运行 Steering 和运行结束后的 Follow-up。
- [x] 持久化 Follow-up 队列、顺序、来源消息和目标分支。
- [x] 当前运行结束后按策略启动下一项。
- [x] UI 可查看、排序和删除未开始 Follow-up。
- [x] 启动时把失去 worker 的活动运行标为 paused。
- [x] failed、paused、cancelled 运行可 retry。
- [x] DOI、内容哈希和任务状态避免明显重复写入。
- [x] Checkpoint 保存 turn、action、工具结果、覆盖和预算。
- [x] Retry 从安全 checkpoint 继续，不重复已完成的只读工具。
- [x] 恢复前确认审批有效期和外部任务状态。
- [x] 增加崩溃恢复、重复写、过期审批和队列测试。

## 20. UI 与诊断

- [x] UI 显示阶段、查询、主题边界、证据数、工具数和审查问题。
- [x] Markdown 正常渲染，右侧证据区独立滚动。
- [x] 运行中可停止任务。
- [x] 历史对话和研究运行可重新打开。
- [x] 展示 ResearchIntent 和确认状态。
- [x] 展示 QuerySpec、命中率和改写过程。
- [x] 展示 action、tool call、tool result 和停止理由时间线。
- [x] 展示 claim 到 evidence span 的对应关系。
- [x] 展示待审批、待澄清、后台任务和 Follow-up。
- [x] 展示会话树和分支。
- [x] 提供不含密钥和全文的诊断导出。
- [x] 完成键盘、窄窗口、长答案和独立滚动测试。

## 21. 最终验收与发布

- [x] 本文档第 0-20 节功能条目均为 `[x]`，且有自动化测试或可观察事件证据。
- [x] Python、Ruff、TypeScript、Vite、Rust 和源码/打包 sidecar MCP smoke 全部通过。
- [x] Agent Loop、Reviewer、恢复和会话树端到端测试通过。
- [ ] DeepSeek 完成本地、混合、在线、无证据和歧义真实烟雾测试。
- [ ] MiniMax 完成摘要、全文块和查询向量真实烟雾测试。
- [ ] 在线元数据和开放全文来源完成可选真实烟雾测试。
- [ ] 固定质量集引用 ID 有效率 100%。
- [ ] 全文证据等级越界错误为 0。
- [ ] 跨主题证据进入最终答案数量为 0。
- [ ] 人工判定引用直接支持附近主张的比例不低于 90%。
- [ ] 新流程相对 0.3.1 的覆盖率和总体评分有可复现提升。
- [ ] Windows 干净环境安装、升级、卸载和数据保留测试通过。
- [x] 生成新 NSIS/MSI 并记录版本、大小和 SHA-256。
- [x] 更新 README、架构、Pi 对照、Changelog 和发布说明。
- [ ] 完成代码审查；质量不达标时继续保留功能分支。

## 实施与验收记录

### 2026-09-04：统一 Agent Loop、精确恢复、Subagent 工具回合与 0.4.0 打包

Checklist items:

- [x] 第 12 节：模型输出的 `AgentAction` 实际控制本地/在线工具参数，工具结果进入下一轮模型上下文。
- [x] 第 18 节：Subagent 先选择受限工具动作，收到真实观察后再生成结构化 Scout 结果。
- [x] 第 19 节：阶段 checkpoint 保存完整运行态，恢复后跳过已完成的 intake、planning 和 retrieval。
- [x] 第 20 节：当前 API 的浏览器回归覆盖设置滚动、Markdown、在线研究、文献状态和移动端布局。
- [x] 第 21 节：统一 `0.4.0` 版本并生成、校验 NSIS/MSI 与打包 sidecar。

Implementation:

- `src/researchbrain/orchestration/orchestrator.py`、`tools.py`、`evidence.py`、`store.py`：加入统一动作控制、
  状态快照恢复、模型驱动 Subagent 工具回合和在线超时批次截断。
- `tests/test_research_agent_action_loop.py`：覆盖模型查询控制、崩溃后精确恢复、Subagent 工具观察反馈和
  在线软超时不执行工具。
- `desktop/scripts/online_research_check.cjs`、`visual_check.cjs`：改用当前 Research Run API 和完整设置页
  fixture，验证独立滚动与窄窗口无溢出。
- `docs/architecture.md`、`docs/pi-patterns.md`、`docs/research-orchestrator-plan.md`、
  `docs/release-0.4.0.md`：同步最终实现边界和发布产物。

Verification:

- `.venv/Scripts/python.exe -m pytest -q`：全量通过，1 条 Starlette/httpx 弃用警告。
- `.venv/Scripts/python.exe -m ruff format --check src tests scripts`、`ruff check src tests scripts`：通过。
- `npm run format:check`、`typecheck`、`build`、`chat:check`、`online:check`、`library:check`、
  `visual:check`：通过，浏览器控制台 0 error。
- `cargo fmt --check`、`cargo check`、`cargo clippy --all-targets -- -D warnings`：通过。
- 源码模式及 `researchbrain-sidecar-x86_64-pc-windows-msvc.exe` 的 MCP smoke：通过。
- `scripts/public_repo_audit.py`：223 个公开文件通过。
- NSIS：157,976,936 B，SHA-256
  `11588feb04a9a30f378d648cd3dd69d1a445467f5efec39397d0afe423b5a143`。
- MSI：159,059,968 B，SHA-256
  `5bb4e63aea77be2c28edc8471dba7f314beb1f5320744bfe719e470663becca0`。

Remaining risks:

- 第 21 节保留未勾选的真实 DeepSeek/MiniMax、真实在线提供方、固定质量集人工盲评和干净 Windows
  安装验收；这些需要外部凭据、真实文库或另一台机器，不能由模拟测试冒充。
- 本记录取代下方早期记录中“固定阶段主编排”“Subagent 确定性单步循环”和“恢复会重放只读阶段”
  的历史限制说明。

### 2026-09-04：立即 Steering 中止可取消工具并同运行重规划

Checklist items:

- [x] 第 15 节：立即约束可中止当前可取消工具并重新规划。

Implementation:

- `src/researchbrain/api/app.py`：为每个研究运行维护 `steering_abort_event`；运行中收到 constraint/correction/clarification 时设置当前工具中止信号，并在 `steering_abort_requested` 中暴露 `current_tool_abort_signal`。
- `src/researchbrain/orchestration/tools.py`：Registry 在受控工具执行期间监听 Steering 中止事件；事件触发时取消正在等待的可取消工具任务，返回结构化 `steering_interrupted` 工具结果，而不是取消整个研究运行。
- `src/researchbrain/orchestration/orchestrator.py`：Orchestrator 消费 Steering 后清除中止信号，发出 `steering_replan_requested`，并在后续阶段边界重新校验意图、查询计划、候选证据和覆盖。
- `tests/test_research_runs_api.py`：新增 API 级测试，覆盖执行中 Steering 中止慢工具、同一运行继续完成、SSE 可观察中止工具结果和重规划事件，且不会误发 `run_cancelled`。

Verification:

- `.venv/Scripts/python.exe -m pytest -q tests/test_research_runs_api.py::test_running_steering_interrupts_cancellable_tool_and_replans_same_run tests/test_research_runs_api.py::test_running_steering_emits_abort_request_and_sse_reconnect_replays_events`：通过。
- `.venv/Scripts/python.exe -m pytest -m research_steering_abort tests/test_research_runs_api.py tests/test_steering_abort.py -q`：通过。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/api/app.py src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_research_runs_api.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/api/app.py src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_research_runs_api.py`：通过。

### 2026-09-04：执行中 Steering 取消请求与 SSE 重连测试

Checklist items:

- [x] 第 15 节：增加执行中取消、阶段 Steering 和 SSE 重连测试。

Implementation:

- `src/researchbrain/api/app.py`：运行中收到 constraint/correction/clarification Steering 时，除入队 `steering_queued` 外，额外持久化 `steering_abort_requested`，记录当前阶段、可取消标记和阶段边界重新规划意图，供 UI/SSE 观察。
- `tests/test_research_runs_api.py`：新增 `research_steering_abort` API 测试，覆盖运行中阶段 Steering、取消运行，以及通过 `Last-Event-ID` 重连回放 `steering_queued`、`steering_abort_requested` 和 `run_cancelled` 事件。

Verification:

- `.venv/Scripts/python.exe -m pytest -q tests/test_research_runs_api.py::test_running_steering_emits_abort_request_and_sse_reconnect_replays_events`：通过。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/api/app.py tests/test_research_runs_api.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/api/app.py tests/test_research_runs_api.py`：通过。

Remaining risks:

- 当前实现完成可观察的立即中止请求和阶段边界重规划标记；对正在执行的长耗时底层工具做细粒度软中断仍保留第 15 节上一项。

### 2026-09-04：安全 checkpoint 恢复与只读工具复用测试

Checklist items:

- [x] 第 12 节：应用重启后从最后完整 turn 恢复。
- [x] 第 19 节：Retry 从安全 checkpoint 继续，不重复已完成的只读工具。
- [x] 第 19 节：恢复前确认审批有效期和外部任务状态。

Implementation:

- `tests/test_research_runs_api.py`：新增 `research_agent_action_loop` / `research_followup_recovery` API 级恢复测试，先持久化一个完整 readonly `call_tools` turn 和 checkpoint 后模拟失败，再通过 retry 验证新 Orchestrator 收到最后安全 checkpoint、事件流发出 `checkpoint_resumed`、只读结果复用标记为真，并在恢复前记录审批有效性与外部任务状态。

Verification:

- `.venv/Scripts/python.exe -m pytest -q tests/test_research_runs_api.py::test_retry_resumes_from_safe_checkpoint_and_reuses_readonly_results tests/test_research_runs_api.py::test_expired_research_approval_is_marked_and_rejected`：通过。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/api/app.py src/researchbrain/orchestration/store.py tests/test_research_runs_api.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/api/app.py src/researchbrain/orchestration/store.py tests/test_research_runs_api.py`：通过。

Remaining risks:

- `src/researchbrain/api/app.py`、`src/researchbrain/orchestration/store.py`：过期审批由持久化状态标记为 `expired` 后再拒绝，避免只返回错误但 UI 仍显示 pending。
- `tests/test_research_runs_api.py`：补充过期审批 API 测试；与同文件崩溃 checkpoint retry、Follow-up 队列测试及工具策略重复写测试共同覆盖第 19 节恢复测试矩阵。

### 2026-09-04：研究 UI 键盘、窄窗口、长答案与独立滚动测试

Checklist items:

- [x] 第 20 节：完成键盘、窄窗口、长答案和独立滚动测试。

Implementation:

- `tests/test_research_diagnostic_ui.py`：补充 `research_diagnostics_ui` 静态契约测试，覆盖主导航/分支路径/证据按钮/输入区 aria 标签、`aria-live` 进度提示、原生 `details` 键盘交互入口、长答案换行、窄窗口媒体查询，以及消息区和证据区独立滚动 CSS。

Verification:

- `.venv/Scripts/python.exe -m pytest -q tests/test_research_diagnostic_ui.py`：通过。
- `cd desktop && bun run typecheck`：通过。

### 2026-09-04：缺口驱动 DOI/PDF 获取与 AgentAction 防伪造

Checklist items:

- [x] 第 8 节：Agent 根据缺口决定查题录、获取全文或等待解析。
- [x] 第 8 节：解析完成后从暂停 action turn 继续，而不是重跑全部只读阶段。
- [x] 第 8 节：增加已有摘要/PDF/向量、重复 DOI 和失败重试端到端测试。
- [x] 第 12 节：每轮模型只决定下一步动作，不能伪造工具结果。

Implementation:

- `src/researchbrain/orchestration/orchestrator.py`：新增 `_plan_acquisition_actions`，根据覆盖矩阵缺口、最低证据等级和在线 DOI 候选生成 `lookup_doi`、`import_dois`、`queue_fulltext`、`job_status`、`parse_pdf`、`embed_document`、`wait_for_parse` 的可审计动作计划；覆盖已满足时不再盲目导入在线 DOI。
- `src/researchbrain/orchestration/orchestrator.py`：导入审批前发出 `acquisition_decision`；后台 DOI/PDF/解析/向量状态 ready 后发出 `action_turn_resumed`，并在同一运行中重新检索本地新增全文证据。
- `src/researchbrain/orchestration/models.py`：`AgentAction`、`AgentToolCall` 和 `ToolResultMessage` 禁止额外字段，工具结果只能由 Registry 作为持久化 `tool_result` 消息进入上下文，模型不能在 action payload 中伪造 result/tool_result 字段。
- `tests/test_research_doi_pdf_loop.py`、`tests/test_research_agent_action_loop.py`、`tests/test_orchestrator.py`、`tests/test_research_runs_api.py`：补充缺口驱动 acquisition 决策、解析完成后同运行恢复事件、已有 PDF/解析/向量复用、重复 DOI 规范化去重、失败重排，以及伪造工具结果 schema 拒绝测试。

Verification:

- `.venv/Scripts/python.exe -m pytest -q tests/test_research_doi_pdf_loop.py tests/test_research_agent_action_loop.py tests/test_orchestrator.py::test_approved_acquisition_is_retrieved_into_the_same_evidence_ledger tests/test_research_runs_api.py::test_online_doi_acquisition_requires_one_time_approval`：通过。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_research_doi_pdf_loop.py tests/test_research_agent_action_loop.py tests/test_orchestrator.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_research_doi_pdf_loop.py tests/test_research_agent_action_loop.py tests/test_orchestrator.py`：通过。

Remaining risks:

- 本次恢复点覆盖 DOI/PDF 后台任务完成后的同运行续读；应用进程崩溃后的精确 phase 跳过仍归第 12/19 节恢复项。
- 第 8 节已有摘要/PDF/向量、重复 DOI 和失败重试链路已由工具级端到端测试与 API 审批重复 DOI 测试共同覆盖；后续真实后台 worker 长链路仍归最终验收烟雾测试。

### 2026-09-04：研究诊断 UI 时间线、Claim 证据映射与安全导出

Checklist items:

- [x] 第 15 节：UI 显示 Steering 何时生效及影响步骤。
- [x] 第 20 节：展示 ResearchIntent、QuerySpec/命中率、Action/Tool/Stop 时间线、Claim 到 evidence span、待审批/待澄清/后台任务/Follow-up、会话树和分支，并提供不含密钥和全文的诊断导出。

Implementation:

- `desktop/src/api.ts`：扩展研究事件类型，接收 `claims_ready`、`agent_action`、`tool_result`、`stop_decision`、Steering、审批与后台任务等诊断字段。
- `desktop/src/App.tsx`：研究过程面板新增 Action / Tool / Stop 时间线、Steering 生效影响、Claim → Evidence span、待处理项和诊断 JSON 导出；导出内容仅包含意图、检索式、统计、时间线、审查和 claim/span 摘要，不包含密钥、API key、原始全文或证据正文。
- `tests/test_research_diagnostic_ui.py`：加入 `research_diagnostics_ui` 分组静态自检，覆盖上述 UI 接入点和事件类型。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_research_diagnostic_ui.py -q`：通过。
- `cd desktop && bun run typecheck`：通过。
- `cd desktop && bunx prettier --check src/App.tsx src/api.ts`：通过。

Remaining risks:

- 本次完成诊断 UI 的可观察路径；键盘、窄窗口、长答案和独立滚动的系统化交互测试仍保留未勾选。
- 执行中立即中止当前可取消工具并重新规划、崩溃后的精确阶段恢复仍分别留在第 15、19 节后续实现。

### 2026-09-03：AgentAction、受控 DOI/PDF 工具与 turn checkpoint

Checklist items:

- [x] 第 8 节：注册 `lookup_doi`、`import_dois`、`queue_fulltext`、`job_status`、`parse_pdf`、`embed_document`，并为写工具接入审批、文库作用域、幂等键和审计。
- [x] 第 12 节：新增 `AgentAction`，接通 `call_tools`、`ask_user`、`delegate`、`synthesize`、`review`、`finish`、`tool_result` 上下文和结构化工具错误。
- [x] 第 12 节：持久化 turn、action、tool call 和 tool result；补齐 `export_references` 及每个工具的 Schema、权限、并发、超时和幂等策略；覆盖空证据、补检索、工具失败、等待用户和预算耗尽测试。
- [x] 第 19 节：Checkpoint 保存 turn、action、工具结果、覆盖、预算和上下文。

Implementation:

- `src/researchbrain/orchestration/models.py`、`src/researchbrain/orchestration/tools.py`：新增 `AgentAction`、`AgentToolCall`、`ToolResultMessage`；Registry 将每批受控调用记录为 action turn，把成功或失败结果作为 `tool_result` 加入后续模型上下文，并为未知工具、参数错误、策略拒绝和超时返回结构化错误。
- `src/researchbrain/orchestration/acquisition.py`、`src/researchbrain/orchestration/orchestrator.py`：实现并注册 DOI 查询/导入、开放全文排队、任务状态、PDF 解析、文档向量化和参考文献导出工具；已有 PDF、解析产物和向量可直接复用，失败任务可幂等重排。
- `src/researchbrain/db/models.py`、`src/researchbrain/migrations/versions/20260903_0010_research_action_checkpoints.py`、`src/researchbrain/orchestration/store.py`：新增 `research_turns`、`research_tool_calls`、`research_checkpoints`，保存 action、参数、结果、错误、幂等键、覆盖、预算和安全 checkpoint，并提供只读结果缓存。
- `src/researchbrain/api/app.py`：研究运行接入受控 Acquisition Tools；DOI 用户批准后通过 Registry 执行，而非直接绕过工具策略；新增 turns/checkpoint API，Retry 会复核审批期限和外部任务状态并复用已完成只读工具结果。
- `tests/test_research_agent_action_loop.py`、`tests/test_research_doi_pdf_loop.py`、`tests/test_research_runs_api.py`：覆盖 Action Schema、tool-result 下一轮上下文、动作时间线、持久化 checkpoint、未知工具、审批拒绝、作用域、DOI 去重、已有 PDF/解析/向量复用、失败重排和 API 审计。

Verification:

- `.venv/Scripts/python.exe -m pytest -m research_agent_action_loop -q`：通过。
- `.venv/Scripts/python.exe -m pytest -m research_doi_pdf_loop -q`：通过。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：166 passed, 1 skipped。
- `.venv/Scripts/python.exe -m ruff check --no-cache src tests scripts`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src tests scripts`：通过。

Remaining risks:

- 主编排仍由 Planner/Assessor 的专用结构化输出和固定安全阶段共同决定；第 12 节“每轮模型只返回统一 AgentAction”尚未完成，因此保持未勾选。
- 当前 Retry 会从安全 checkpoint 读取上下文、复核审批/后台任务并复用只读工具结果，但尚未按 checkpoint phase 跳过全部已完成模型阶段；第 12、19 节精确阶段恢复保持未勾选。
- Agent 尚未根据覆盖缺口自主串联 lookup/import/queue/status/parse/embed 全链，并且解析完成后还不能从暂停 action turn 精确续跑，所以第 8 节后三项保持未勾选。

### 2026-09-03：持久化 Follow-up 队列与顺序执行

Checklist items:

- [x] 第 19 节：区分当前运行 Steering 和运行结束后的 Follow-up。
- [x] 第 19 节：持久化 Follow-up 队列、顺序、来源消息和目标分支。
- [x] 第 19 节：当前运行结束后按策略启动下一项。
- [x] 第 19 节：UI 可查看、排序和删除未开始 Follow-up。

Implementation:

- `src/researchbrain/db/models.py`、`src/researchbrain/migrations/versions/20260903_0009_research_follow_ups.py`：新增 `research_follow_ups` 持久化队列表，保存来源运行/消息、目标会话/分支、位置、模式、状态、启动运行和时间信息，并建立目标队列索引。
- `src/researchbrain/orchestration/store.py`：新增 Follow-up 入队、列表、严格全量排序、删除、原子领取、运行关联、完成/失败和启动失败释放操作；队列位置在删除后重新压紧。
- `src/researchbrain/api/app.py`：`follow_up` 不再进入当前运行的内存 Steering 上下文，而是持久化并发出 `follow_up_queued`；constraint/correction/clarification 继续作为当前运行 Steering。新增队列列表、排序和删除 API。
- `src/researchbrain/api/app.py`：当前运行完成后按目标会话领取队首，创建新的用户消息和 ResearchRun；Follow-up 运行完成后继续领取下一项，确保同一目标分支顺序执行。终态运行也可新增 Follow-up 并立即启动。
- `desktop/src/api.ts`、`desktop/src/App.tsx`、`desktop/src/styles.css`：UI 明确区分“当前运行”和“后续队列”，展示待执行项及状态，并提供上移、下移和删除入口；目标分支 ID 随请求持久化。
- `tests/test_research_runs_api.py`、`tests/test_followup_queue_ui.py`：加入 `research_followup_recovery` 分组测试，覆盖 Steering/Follow-up 分流、持久化来源和目标、排序、删除、运行结束后按序自动执行以及 UI 操作入口。

Verification:

- `.venv/Scripts/python.exe -m pytest -m research_followup_recovery tests/test_research_runs_api.py tests/test_followup_queue_ui.py -q`：3 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_research_runs_api.py tests/test_api.py -q`：23 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_steering_abort.py tests/test_research_clarification.py -q`：3 passed。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/db/models.py src/researchbrain/orchestration/store.py src/researchbrain/api/app.py src/researchbrain/migrations/versions/20260903_0009_research_follow_ups.py tests/test_research_runs_api.py tests/test_followup_queue_ui.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/db/models.py src/researchbrain/orchestration/store.py src/researchbrain/api/app.py src/researchbrain/migrations/versions/20260903_0009_research_follow_ups.py tests/test_research_runs_api.py tests/test_followup_queue_ui.py`：通过。
- `cd desktop && bunx prettier --check src/App.tsx src/api.ts src/styles.css`：通过。
- `cd desktop && bun run typecheck`：通过。

Remaining risks:

- 本组只完成第 19 节 Follow-up 队列链路；turn/action/tool-result checkpoint、从安全 checkpoint 精确恢复、审批有效期与外部任务复核仍依赖第 12 节 AgentAction 持久化协议，因此保持未勾选。
- 当前同一目标会话通过活动运行检查和 `starting` 状态避免重复启动；多进程 worker 部署时还需将领取操作升级为数据库行锁或等价租约。

### 2026-09-03：Subagent 独立任务与工具循环

Checklist items:

- [x] 第 18 节：Subagent 有独立任务、预算、上下文和允许工具列表。
- [x] 第 18 节：Subagent 有自己的 action/tool loop，可执行本地检索和定向全文阅读。
- [x] 第 18 节：在线 Subagent 按来源或策略分工并避免重复搜索。
- [x] 第 18 节：Subagent 只返回结构化证据、缺口和查询建议；Aggregator 校验证据 ID 和主题后再合并。
- [x] 第 18 节：父 Agent 可取消 Subagent，并限制并发、总步数、工具数和上下文大小。
- [x] 第 18 节：固定质量集 A/B 验证 Subagent 的收益和成本。

Implementation:

- `src/researchbrain/orchestration/models.py`：新增 `SubagentBudget`、`SubagentTask` 和 `SubagentResult`，显式记录任务 ID、子问题、策略、允许工具、来源分工、步数/工具/上下文预算和结构化结果。
- `src/researchbrain/orchestration/orchestrator.py`：将原 Scout 单次模型调用升级为 `_run_subagent_loop`；父 Agent 为每个子问题构建独立任务，限制并发为 3，并通过共享取消信号中止单个或全部 Subagent。
- `src/researchbrain/orchestration/orchestrator.py`：Subagent loop 先读取受限证据上下文，再按允许工具执行一次本地补检索、在线分源检索或定向全文读取；结果只包含 evidence IDs、findings、missing 和 next_queries，不生成最终答案。
- `src/researchbrain/orchestration/orchestrator.py`：在线 Subagent 按 QuerySpec 来源去重分配，避免多个 Subagent 重复搜索同一来源；Aggregator 合并前校验 evidence ID 必须存在于当前 ledger，无效 ID 进入审计事件而不进入 ScoutFinding。
- `src/researchbrain/orchestration/evaluation.py`：新增 `summarize_subagent_ab_results`，用于固定质量集 A/B 汇总 Subagent 开关带来的覆盖/引用收益和模型步数/工具调用成本。
- `tests/test_orchestrator.py`、`tests/test_research_evaluation.py`：加入 `research_subagent_loop` 分组测试，覆盖独立任务预算、工具循环、来源去重、聚合校验、事件审计和 A/B 收益成本汇总。

Verification:

- `.venv/Scripts/python.exe -m pytest -m research_subagent_loop tests/test_orchestrator.py tests/test_research_evaluation.py -q`：3 passed。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/evaluation.py tests/test_orchestrator.py tests/test_research_evaluation.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/evaluation.py tests/test_orchestrator.py tests/test_research_evaluation.py`：通过。

Remaining risks:

- 当前 Subagent action/tool loop 是受控的小步确定性循环，重点限制权限、预算和可审计合并；更完整的自由 AgentAction 协议仍与第 12 节主 Agent Loop 衔接。
- Subagent 的 A/B 验证已接入确定性汇总函数，真实固定质量集收益和成本还需最终验收时用真实模型和文库跑出 JSON 产物。

### 2026-09-03：会话树和分支

Checklist items:

- [x] 第 17 节：`chat_messages` 增加 `parent_message_id` 等价关系。
- [x] 第 17 节：分支保存根消息、名称和创建来源，可从任意历史用户消息创建分支。
- [x] 第 17 节：分支只继承节点之前的约束和来源标识，旧回答不作为分支证据。
- [x] 第 17 节：UI 展示路径、切换入口和当前分支；删除/归档不破坏其他分支引用。
- [x] 第 17 节：增加迁移、创建、并发和历史读取测试。

Implementation:

- `src/researchbrain/db/models.py`、`src/researchbrain/migrations/versions/20260903_0008_chat_branches.py`：为 `chat_messages` 增加 `parent_message_id`，为 `chat_sessions` 增加父会话、根消息、分支源消息、分支名称、创建来源和归档时间字段，并建立父消息索引。
- `src/researchbrain/api/app.py`：新增创建分支、读取分支路径和归档会话 API；创建分支时要求源消息属于当前会话且为历史用户消息，分支记忆只复制目标、约束、术语、来源标识和未解决问题，清空 `supported_findings` 并标记 `branch_inherits_evidence=False`。
- `src/researchbrain/api/app.py`、`src/researchbrain/orchestration/store.py`：新用户消息连接到同会话最近消息，研究运行完成后的助手消息连接到对应用户消息，历史消息 API 返回 `parent_message_id`。
- `desktop/src/api.ts`、`desktop/src/App.tsx`、`desktop/src/styles.css`：前端接入分支创建、路径读取和归档 API；会话列表标出分支，会话顶部展示分支路径和当前节点，历史用户消息提供“创建分支”入口。
- `tests/test_api.py`、`tests/test_session_branching_ui.py`：加入 `research_session_branching` 分组测试，覆盖分支创建、安全记忆继承、旧回答非证据、路径读取、归档后子分支引用仍可恢复，以及 UI 入口自检。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_api.py::test_empty_library_chat_returns_a_visible_readiness_answer tests/test_api.py::test_chat_sessions_are_persisted_and_listed_with_latest_message tests/test_api.py::test_chat_session_branches_preserve_tree_and_inherit_only_safe_memory tests/test_api.py::test_chat_branch_archive_does_not_break_child_branch_path tests/test_session_branching_ui.py -q`：5 passed。
- `.venv/Scripts/python.exe -m pytest -m research_session_branching tests/test_api.py tests/test_session_branching_ui.py -q`：3 passed。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/db/models.py src/researchbrain/api/app.py src/researchbrain/orchestration/store.py tests/test_api.py tests/test_session_branching_ui.py src/researchbrain/migrations/versions/20260903_0008_chat_branches.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/db/models.py src/researchbrain/api/app.py src/researchbrain/orchestration/store.py tests/test_api.py tests/test_session_branching_ui.py src/researchbrain/migrations/versions/20260903_0008_chat_branches.py`：通过。
- `cd desktop && bunx prettier --check src/App.tsx src/api.ts src/styles.css`：通过。
- `cd desktop && bun run typecheck`：通过。

Remaining risks:

- 分支记忆当前按已有 session memory 的安全字段复制；若以后需要严格“时间点前”增量记忆，需要在 memory 更新时记录每条约束和来源标识的来源消息。
- 归档采用软删除，默认列表隐藏归档会话但分支路径仍可解析；完整删除策略仍应继续避免破坏其他分支引用。

### 2026-09-03：Context Compaction 与会话记忆

Checklist items:

- [x] 第 16 节：使用 tokenizer 估算上下文，而非只按消息数和字符数裁剪。
- [x] 第 16 节：达到窗口阈值时生成结构化 compaction checkpoint，并保存覆盖边界、生成模型和版本。
- [x] 第 16 节：无损保留关键用户限制和 DOI/PMID/arXiv 标识。
- [x] 第 16 节：新问题重新读取原证据，不把压缩摘要当证据；新证据冲突时更新或移除旧假设。
- [x] 第 16 节：增加超长会话、跨轮约束、错误历史和恢复测试。

Implementation:

- `src/researchbrain/orchestration/context.py`：上下文裁剪改为基于 `researchbrain-regex-token-estimator-v1` 的 token 预算，优先保留硬约束和来源标识；超出窗口阈值时生成结构化 `compaction_checkpoint`，记录覆盖边界、估算前后 token、生成模型/版本、内容哈希和非证据策略。
- `src/researchbrain/orchestration/context.py`：从历史消息和记忆中无损抽取并去重 DOI、PMID、arXiv 标识；压缩摘要标记 `summary_is_evidence=False`、`must_reread_original_evidence=True`，确保后续问题只能把历史作为导航线索而非证据。
- `src/researchbrain/orchestration/context.py`：新增冲突失效处理，按 `invalidated_hypotheses` 和 `conflicting_evidence_identifiers` 移除旧假设，同时保留原始来源 ID 以便重新读取证据。
- `src/researchbrain/orchestration/orchestrator.py`：`context_transformed` 事件输出 tokenizer、估算 token、compaction checkpoint 和重新读取原证据策略，使 API/UI 事件流可观察。
- `tests/test_research_context.py`：加入 `research_context_compaction` 分组测试，覆盖 token 预算压缩、checkpoint 元数据、跨轮约束与 DOI/PMID/arXiv 保留、错误历史假设失效和非证据策略。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_research_context.py -q`：3 passed。
- `.venv/Scripts/python.exe -m pytest -m research_context_compaction tests/test_research_context.py -q`：3 passed。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/context.py src/researchbrain/orchestration/orchestrator.py tests/test_research_context.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/context.py src/researchbrain/orchestration/orchestrator.py tests/test_research_context.py`：通过。

Remaining risks:

- 当前 tokenizer 是确定性正则估算器，适合做稳定预算门禁；若将来按具体模型接入官方 tokenizer，需要保留相同 checkpoint schema。
- 本次处理的是进入模型上下文前的 compaction 和记忆失效策略；会话树分支、精确 checkpoint 恢复和完整诊断 UI 仍分别留在第 17、19、20 节。

### 2026-09-03：Steering 分类与约束重校验

Checklist items:

- [x] 第 15 节：Steering 区分 constraint、correction 和 follow_up。
- [x] 第 15 节：新约束触发意图、查询、候选证据和覆盖重校验。
- [x] 第 15 节：冲突旧证据标记失效，不再参与综合。

Implementation:

- `src/researchbrain/orchestration/orchestrator.py`：`_consume_steering` 将运行中消息规范化为 constraint、correction、clarification 和 follow_up；constraint/correction/clarification 进入当前运行上下文，follow_up 单独进入延后队列并发出 `follow_up_queued`，不污染当前综合提示词。
- `src/researchbrain/orchestration/orchestrator.py`：新增 `_apply_steering_to_research_state`，在证据整理、在线补充和综合前按 Steering 版本重校验计划排除词、候选证据和覆盖矩阵，并发出 `steering_revalidated` 诊断事件。
- `src/researchbrain/orchestration/orchestrator.py`：当新约束排除的概念命中旧证据时，将对应证据写入 irrelevant screening，覆盖项降级为 partial 或 insufficient_evidence，后续 `ledger.evidence()` 和 Synthesizer 不再使用该证据。
- `tests/test_steering_abort.py`：新增 `research_steering_abort` 分组测试，覆盖 Steering 分类、Follow-up 延后、约束触发重校验、冲突证据失效和覆盖降级。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_steering_abort.py -q`：2 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_steering_abort.py tests/test_research_clarification.py tests/test_orchestrator.py -q`：23 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：152 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/orchestrator.py tests/test_steering_abort.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/orchestrator.py tests/test_steering_abort.py`：通过。

Remaining risks:

- 本次完成的是阶段边界 Steering 分类和约束重校验；“立即约束中止当前可取消工具并重新规划”、UI 明确展示 Steering 生效影响，以及执行中取消/SSE 重连测试仍留在第 15 节未勾选项。
- 排除概念抽取目前覆盖中英文常见表达，复杂自然语言约束仍需后续结合 AgentAction Loop 和 UI 确认进一步增强。

### 2026-09-03：阻塞性歧义 ask_user 与同运行恢复

Checklist items:

- [x] 第 1 节：阻塞性歧义进入 `ask_user`，回答后继续同一运行。

Implementation:

- `src/researchbrain/orchestration/orchestrator.py`：Intake 识别 `clarification_required` 后先发出 `ask_user` 事件和 `waiting_for_clarification` 停止判断；收到用户澄清后清除阻塞歧义，把澄清内容追加到同一运行的问题、ResearchIntent 假设和必答项，再继续进入 Planning，不重启只读阶段。
- `src/researchbrain/api/app.py`、`desktop/src/api.ts`：Steering 消息类型补充 `clarification` 和 `correction`，使运行中澄清可通过现有队列进入 Orchestrator。
- `tests/test_research_clarification.py`：新增 `research_clarification` 分组端到端测试，覆盖 `ask_user` 事件、等待澄清 stop decision、收到澄清后同一运行继续到 `plan_ready` 与最终答案。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_research_clarification.py -q`：1 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_research_clarification.py tests/test_orchestrator.py tests/test_stop_decision.py -q`：29 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：150 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/api/app.py tests/test_research_clarification.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/api/app.py tests/test_research_clarification.py`：通过。
- `cd desktop && bun run typecheck`：通过。

Remaining risks:

- 当前澄清沿用运行中的 steering 队列传递，UI 仍是通用补充要求输入框；更专门的待澄清卡片和确认状态展示留给第 20 节诊断 UI。
- 若用户在等待预算内没有回答，运行会以 `clarification_required` 失败而不是长期占用 worker；精确暂停和 checkpoint 恢复将与第 12、19 节 AgentAction/恢复机制衔接。

### 2026-09-03：beforeToolCall / afterToolCall 策略链

Checklist items:

- [x] 第 13 节：Orchestrator 实际安装 `beforeToolCall` / `afterToolCall` 策略链。
- [x] 第 13 节：调用前校验 Schema、文库作用域、预算、审批、域名和幂等键；写工具调用前生成明确审批请求。
- [x] 第 13 节：调用后规范化结果并更新证据账本、覆盖矩阵，记录耗时、来源状态、错误分类和使用量。
- [x] 第 13 节：外部文本按不可信数据隔离，策略拒绝返回 Agent 可理解的结构化错误且不能被绕过。
- [x] 第 13 节：增加钩子顺序、拒绝、异常、审批和注入测试。

Implementation:

- `src/researchbrain/orchestration/tools.py`：Registry 的 hook 签名携带 tool、call_id、结果和耗时；`before_call`、handler、`after_call` 均包在统一执行路径内，策略拒绝返回 `ToolResult.error`，`tool_execution_end` 记录 duration 与错误分类。
- `src/researchbrain/orchestration/orchestrator.py`：构造 Registry 时实际安装 `_before_tool_call` 和 `_after_tool_call`；调用前执行 Schema 后的文库作用域、取消/预算、在线来源域名、写工具审批与幂等键策略；调用后输出结果摘要、证据/覆盖/诊断更新目标和 prompt-injection 隔离标记。
- `tests/test_tool_policy_hooks.py`：新增 `research_tool_policy_hooks` 分组，覆盖策略链安装、文库越权拒绝、在线来源拒绝、结构化错误分类、after hook 结果归一化和外部文本不可信隔离标记。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_tool_policy_hooks.py tests/test_research_tools.py -q`：4 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：149 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/tools.py src/researchbrain/orchestration/orchestrator.py tests/test_tool_policy_hooks.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/tools.py src/researchbrain/orchestration/orchestrator.py tests/test_tool_policy_hooks.py`：通过。

Remaining risks:

- 当前实际注册工具仍以只读工具为主，写工具审批路径已作为不可绕过策略接入，但 DOI/PDF 写工具本身要等第 8、12 节注册后进入真实运行路径。
- after hook 记录证据账本/覆盖矩阵更新目标，实际更新仍由现有检索、阅读全文和在线搜索阶段消费工具结果完成；真正逐 turn AgentAction Loop 的 tool_result 上下文续跑留到第 12 节。

### 2026-09-03：本地候选入选/排除理由 UI

Checklist items:

- [x] 第 4 节：UI 解释每篇候选为何入选或排除。

Implementation:

- `desktop/src/App.tsx`：`evidence_screened` 事件不再只展示相邻/排除项，同时收集 relevant 候选的入选理由；研究轨迹面板新增“候选入选理由”和“候选排除理由”两组可读列表，区分“入选”“排除”和“相邻保留审计”。
- `desktop/src/App.tsx`：右侧证据详情面板读取持久化 `relevance_reason`，对最终可点击证据显示“入选理由 / 排除理由 / 相邻保留审计理由”，让单篇候选为何进入或离开证据包可直接观察。
- `tests/test_local_retrieval_ui.py`：新增 `research_local_retrieval_ui` 分组自检，确认前端消费筛选判断、展示入选/排除理由，并在证据详情中展示持久化筛选原因。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_local_retrieval_ui.py -q`：2 passed。
- `.venv/Scripts/python.exe -m pytest -m research_local_retrieval_ui tests/test_local_retrieval_ui.py -q`：2 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：147 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache tests/test_local_retrieval_ui.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache tests/test_local_retrieval_ui.py`：通过。
- `cd desktop && bunx prettier --check src/App.tsx`：通过。
- `cd desktop && bun run typecheck`：通过。
- `cd desktop && bun run build`：TypeScript 与 Vite production build 通过。

Remaining risks:

- 当前 UI 展示最近一轮筛选事件中前 6 条入选和前 6 条排除/相邻理由；完整候选审计仍保存在事件流和研究证据账本中，后续第 20 节诊断时间线可扩展为分页/过滤视图。
- 候选理由质量取决于 relevance 模型输出和确定性 fallback；本次只接通前端可观察路径，不改变筛选算法。

### 2026-09-03：固定质量集基线与同题盲评导出

Checklist items:

- [x] 第 0 节：保存固定质量集的覆盖率、引用支持率、耗时和模型调用基线。
- [x] 第 0 节：对旧流程和新 Agent Loop 做可复现的同题盲评。

Implementation:

- `src/researchbrain/orchestration/evaluation.py`：新增 `summarize_quality_results`，按实现版本汇总固定质量集 case 数、完成/失败数、覆盖率、引用 ID 有效率、可见回答率、主题相关率、耗时、模型步数和工具调用数；新增 `build_blind_review_packet`，用固定 seed 为同一问题的 v1/v2 结果生成可复现 A/B 盲评包，并把实现标签单独放入 `hidden_key`。
- `scripts/evaluate_research_answers.py`：固定质量集运行结果现在同时保存原始结果、聚合 baseline 和 blind review packet；新增 `--baseline-output`、`--blind-output` 和 `--blind-seed`，可分别落盘基线指标与同题盲评材料。
- `tests/test_research_evaluation.py`：加入 `research_baseline` 分组测试，覆盖覆盖率/引用支持率/耗时/模型调用聚合，以及盲评包的可复现性和 pair 内隐藏实现标签。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_research_evaluation.py -q`：5 passed。
- `.venv/Scripts/python.exe -m pytest -m research_baseline tests/test_research_evaluation.py tests/test_research_agent_test_groups.py -q`：8 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：145 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/evaluation.py scripts/evaluate_research_answers.py tests/test_research_evaluation.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/evaluation.py scripts/evaluate_research_answers.py tests/test_research_evaluation.py`：通过。

Remaining risks:

- 当前只接通固定质量集结果的确定性聚合和可复现盲评材料导出；真实模型/真实文库的基线数值仍需在验收环境运行 `scripts/evaluate_research_answers.py` 后保存具体 JSON 产物。
- “引用支持率”目前使用自动化的引用 ID 有效性与覆盖指标作为基线信号；人工判定附近主张直接支持比例仍留到最终验收质量集执行。

### 2026-09-03：未完成模块独立测试分组

Checklist items:

- [x] 第 0 节：为下列每个未完成模块建立独立测试分组。

Implementation:

- `pyproject.toml`：注册剩余研究 Agent 模块的 pytest markers，覆盖基线质量门禁、阻塞澄清、本地检索 UI、DOI/PDF 闭环、Agent Action Loop、Tool Hook、Steering/Abort、Context Compaction、会话分支、Subagent、Follow-up/恢复、诊断 UI 和最终发布验收。
- `docs/research-agent-test-groups.md`：新增独立测试分组说明和每组可直接运行的 `pytest -m ...` 命令，要求后续实现项把测试加入对应 marker。
- `tests/test_research_agent_test_groups.py`：新增基线分组自检，确保 marker 注册、文档命令和实施清单链接保持一致。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_research_agent_test_groups.py -q`：3 passed。
- `.venv/Scripts/python.exe -m pytest -m research_baseline tests/test_research_agent_test_groups.py -q`：3 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：143 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache tests/test_research_agent_test_groups.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache tests/test_research_agent_test_groups.py`：通过。

Remaining risks:

- 本次只建立独立分组和自检，不代表各未完成模块的功能已实现；后续每组功能完成时仍需把实际单元、集成或端到端测试标记到对应 marker 后再打勾。
- 固定质量集指标基线和旧/新 Agent Loop 盲评仍是第 0 节未完成项。

### 2026-09-03：逐主张 Reviewer 流水线与阻塞主张删除

Checklist items:

- [x] 第 11 节：Claim Extractor、Citation Validator、Evidence Level Validator、Entailment Reviewer、Contradiction Reviewer、Coverage Reviewer 和 Topic Reviewer。
- [x] 第 11 节：审查结果保存 claim ID、严重度、证据 ID、理由和修改建议。
- [x] 第 11 节：修订器只能删除、降级或用现有证据改写；修订后重新执行完整语义 Reviewer。
- [x] 第 11 节：仍有阻塞问题时删除主张或返回受限答案，不发布原句。
- [x] 第 11 节：UI 展示阻塞、警告和被删除/降级的主张；增加错误引文、摘要冒充全文、图表越界、矛盾和漏答测试。

Implementation:

- `src/researchbrain/orchestration/models.py`：扩展 `ReviewIssue`，新增 `claim_id`、`severity`、`evidence_ids` 和 `suggestion`，使审查结果可追踪到逐主张与证据片段。
- `src/researchbrain/orchestration/orchestrator.py`：`_review` 接收 claims 与 comparison matrix；确定性审查流水线新增 citation、evidence level、entailment、contradiction、coverage 和 topic 检查，并把结构化问题写入 `review_ready` 事件，供 UI 展示阻塞/警告和被删除/降级主张。
- `src/researchbrain/orchestration/orchestrator.py`：修订提示词保持“只用现有证据删除/降级/改写”约束；修订后重新执行 `_review` 完整语义审查。若仍有阻塞问题，`_remove_blocking_claims` 删除对应句子；若无安全句子，返回受限答案，不发布原阻塞主张。
- `tests/test_claim_review_pipeline.py`：新增逐主张 Reviewer 流水线测试，覆盖附近引用、摘要/metadata 冒充图表页码、低重叠蕴含、跨主题排除概念、覆盖漏答、跨文献矛盾和阻塞主张删除。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_claim_review_pipeline.py tests/test_claims.py -q`：4 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_stop_decision.py tests/test_claim_review_pipeline.py tests/test_claims.py -q`：32 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：140 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_claims.py tests/test_claim_review_pipeline.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_claims.py tests/test_claim_review_pipeline.py`：通过。

Remaining risks:

- 当前 entailment 和 topic reviewer 为确定性启发式，适合做硬门禁和回归保护；更细语义判断仍依赖模型 Reviewer 的结构化输出。
- 图表越界检查按主张关键词和 page-level 证据等级判断，尚未做图表编号级定位。

### 2026-09-03：初稿逐主张标识、引用跨度和安全诊断

Checklist items:

- [x] 第 10 节：把每个事实性主张拆成稳定 `claim_id`。
- [x] 第 10 节：保存 claim、citation 和 evidence span 对应关系。
- [x] 第 10 节：限制单个引用支撑过多不同主张。
- [x] 第 10 节：明确标记证据不足、推断和建议性内容。
- [x] 第 10 节：增加引用漂移、无引用事实和主题漏判测试。

Implementation:

- `src/researchbrain/orchestration/models.py`：新增 `AnswerClaim` 与 `ClaimEvidenceSpan`，保存稳定 `claim_id`、主张文本、附近引用、证据片段、主张类型和支持状态。
- `src/researchbrain/orchestration/orchestrator.py`：综合和修订后执行确定性 claim extraction，生成 `claims_ready` 事件，写入 synthesis step payload 和最终 metrics，并传入 Reviewer 上下文。
- `src/researchbrain/orchestration/orchestrator.py`：确定性审查新增无引用事实性主张阻塞、单个引用支撑超过 3 个事实主张警告；证据不足、推断和建议性内容会以非事实类型标记，不冒充已证实事实。
- `tests/test_claims.py`：新增稳定 claim ID、claim-citation-evidence span 映射、证据不足/建议类型标记、无引用事实和引用过载测试；主题漏判继续由既有主题门禁回归测试覆盖。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_claims.py -q`：2 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_claims.py tests/test_orchestrator.py -q`：22 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：138 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_claims.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_claims.py`：通过。

Remaining risks:

- 当前主张切分和证据 span 定位为确定性近邻/词项重叠规则；深层语义蕴含、矛盾和图表越界仍需第 11 节逐主张 Reviewer 流水线继续加强。
- 单引用支撑上限目前固定为 3，后续可按任务类型和证据等级配置。

### 2026-09-03：跨文献比较矩阵与综合诊断

Checklist items:

- [x] 第 9 节：新增 `ComparisonMatrix`，记录对象、数据、方法、流程、结果和局限。
- [x] 第 9 节：区分文献原述、跨文献归纳和系统推断。
- [x] 第 9 节：对互相矛盾的结果保留条件差异并交给 Reviewer。
- [x] 第 9 节：“谁做过”输出作者、年份、文献和贡献的对应关系。
- [x] 第 9 节：“数据流程”合并共同步骤并保留各文献特有步骤。
- [x] 第 9 节：“缺陷”区分作者自述局限和系统推断。
- [x] 第 9 节：增加矩阵完整性及跨文献矛盾测试。

Implementation:

- `src/researchbrain/orchestration/models.py`：新增 `ComparisonMatrix` 与 `ComparisonMatrixRow`，逐条保存对象、数据、方法、流程、结果、局限、作者、年份、贡献、证据 ID、证据等级、陈述类型和矛盾标记。
- `src/researchbrain/orchestration/orchestrator.py`：综合前从已筛选证据构建比较矩阵，写入 `comparison_matrix_ready` 事件、synthesis step payload、最终 metrics，并作为结构化上下文交给 Synthesizer。
- `src/researchbrain/orchestration/orchestrator.py`：矩阵区分 `reported` 证据原述、共同流程步骤的跨文献归纳，以及系统推断类 limitation；互相矛盾的结果会保留 evidence ID、方向差异和 contradiction note，并随 Reviewer payload 进入审查。
- `tests/test_comparison_matrix.py`：新增矩阵完整性测试，覆盖作者/年份/贡献对应关系、共同流程合并、作者自述局限与系统推断区分，以及跨文献结果矛盾保留。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_comparison_matrix.py -q`：2 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_comparison_matrix.py tests/test_orchestrator.py -q`：22 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：136 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_comparison_matrix.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_comparison_matrix.py`：通过。

Remaining risks:

- 当前矩阵抽取为确定性文本规则，优先保障可审计和不虚构；复杂表格、隐含实验条件和深层语义矛盾仍需后续逐主张 Reviewer 强化。
- UI 目前可通过事件/metrics 获取矩阵；更细的前端矩阵展示可与第 20 节诊断 UI 一并处理。

### 2026-09-03：在线检索扩展、排序、重试与烟雾测试

Checklist items:

- [x] 第 7 节：增加种子文献的参考文献和后续引用追踪。
- [x] 第 7 节：组合时间排序与相关性排序。
- [x] 第 7 节：增加来源级重试、限流退避及降级说明。
- [x] 第 7 节：增加来源夹具测试和可选真实服务烟雾测试。

Implementation:

- `src/researchbrain/discovery/service.py`：`LiteratureDiscovery.search_with_status` 新增 `track_citations`，对相关性排序靠前的种子文献调用支持方的 `expand_seed`，把参考文献和后续引用候选纳入同一合并、筛选和诊断流程。
- `src/researchbrain/discovery/service.py`：`OpenAlexSearchProvider.expand_seed` 读取 `referenced_works` 并使用 `cites:` 查询后续引用；种子扩展状态进入 `ProviderStatus` 和 `seed_report`。
- `src/researchbrain/discovery/service.py`：在线结果按查询词相关性、年份新近度、可追溯性和摘要长度组合排序，并输出 `ranking_report`。
- `src/researchbrain/discovery/service.py`：来源级执行增加 provider-level retry；重试后成功标记 `degraded`，失败标记 `failed` 并写入 attempts、degraded_reason 和错误说明。底层 HTTP 429/5xx/超时仍保留退避重试。
- `src/researchbrain/orchestration/tools.py`、`src/researchbrain/orchestration/orchestrator.py`：`search_online` 工具参数新增 `track_citations`，默认接入在线检索实际运行路径；query diagnostic 写入 ranking、seed 和 provider status 诊断。
- `tests/test_discovery.py`：补充排序、来源级重试降级、种子参考/引用扩展和可选真实服务 smoke 测试；现有 Crossref、OpenAlex、arXiv、PubMed MockTransport 夹具测试继续覆盖来源解析。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_discovery.py -q`：10 passed, 1 skipped。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：134 passed, 1 skipped, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/discovery/service.py src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_discovery.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/discovery/service.py src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_discovery.py`：通过。

Remaining risks:

- 当前参考/引用追踪先接入 OpenAlex；Crossref、PubMed Central、Semantic Scholar 等更丰富 citation graph 可在后续来源扩展中加入。
- 排序为确定性词项相关性与时间组合，不等同于来源原生 BM25/学习排序；后续可把 provider 原始分数并入 ranking report。
- 可选真实服务 smoke 默认跳过，需设置 `RESEARCHBRAIN_REAL_DISCOVERY_SMOKE=1` 才会访问真实元数据服务。

### 2026-09-03：在线跨来源合并报告与完整记录优先

Checklist items:

- [x] 第 7 节：按 DOI、PMID、arXiv ID 和规范化题名生成跨来源合并报告。
- [x] 第 7 节：优先保留信息完整、可追溯且有摘要的记录。

Implementation:

- `src/researchbrain/discovery/service.py`：新增 `DiscoveryMergeReport`，`DiscoverySearchResult` 返回跨来源合并报告，记录 canonical key、匹配键、来源、标识符、合并数量、保留来源和保留原因。
- `src/researchbrain/discovery/service.py`：跨来源合并使用 DOI、PMID、arXiv ID 和规范化题名匹配；合并时优先保留有摘要、标识符、作者年份、venue 和 URL 的完整可追溯记录，同时补齐其他来源的标识符与来源列表。
- `src/researchbrain/orchestration/orchestrator.py`：在线检索 query diagnostic 写入 merge report，供 UI/诊断时间线后续展示。
- `tests/test_discovery.py`：新增 DOI 合并报告断言，以及 PMID、arXiv ID、规范化题名合并和完整记录优先保留测试。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_discovery.py -q`：7 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：131 passed, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/discovery/service.py src/researchbrain/orchestration/orchestrator.py tests/test_discovery.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/discovery/service.py src/researchbrain/orchestration/orchestrator.py tests/test_discovery.py`：通过。

Remaining risks:

- 参考文献/后续引用追踪、时间与相关性组合排序、来源级降级说明和真实服务烟雾测试仍属第 7 节后续未完成项。
- 规范化题名匹配目前为确定性空白归一化；复杂副标题、标点差异和译名匹配仍需后续增强。

### 2026-09-03：定向图表公式阅读范围与限制说明

Checklist items:

- [x] 第 5 节：支持图题、表题、公式邻近文本和参考文献段落定向读取。
- [x] 第 5 节：记录已读与未读范围，禁止把局部检索称为“读完全文”。
- [x] 第 5 节：对扫描 PDF、解析失败和页码缺失给出明确限制。
- [x] 第 5 节：增加证据等级、跨页片段和图表问答测试。

Implementation:

- `src/researchbrain/orchestration/tools.py`：`read_fulltext_chunks` 参数新增 `target_kind`，支持 `figure`、`table`、`equation` 和 `references` 定向读取。
- `src/researchbrain/orchestration/orchestrator.py`：全文读取按图题/表题/公式邻近文本/参考文献段落进行目标过滤；`read_scope` 新增 `target_kind`、`read_chunk_ids`、`read_ordinals`、`unread_chunks` 和 `unread_ordinal_ranges`，持续标记 `not_full_document=True`。
- `src/researchbrain/orchestration/orchestrator.py`：读取诊断新增 limitations，明确 PDF 未保存、解析产物缺失、解析失败/未完成、扫描件或未索引、页码缺失等限制。
- `tests/test_orchestrator.py`：扩展完整题录/全文工具测试，覆盖图题定向读取、已读/未读范围和页码缺失限制；新增解析失败/扫描 PDF 限制测试。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py -q`：20 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：130 passed, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_orchestrator.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_orchestrator.py`：通过。

Remaining risks:

- 目标类型识别目前基于解析后文本中的 figure/table/equation/references 标记和中英文关键词；若解析器未保留图表标题或公式文本，仍会通过 limitations 暴露限制。
- 图表内容本身的视觉理解尚未接入，只读取图题、表题和邻近文本。

### 2026-09-03：覆盖矩阵文献多样性和缺口原因

Checklist items:

- [x] 第 6 节：检查覆盖是否只依赖同一文献的重复片段。
- [x] 第 6 节：人物、方法比较和结论类问题支持最低文献多样性要求。
- [x] 第 6 节：记录每个缺口不能回答的具体原因。
- [x] 第 6 节：覆盖矩阵接入 `shouldStopAfterTurn`。
- [x] 第 6 节：增加伪覆盖、单一来源覆盖和等级不足测试。

Implementation:

- `src/researchbrain/orchestration/orchestrator.py`：覆盖规范化阶段新增证据来源键，按 DOI/PMID/arXiv、本地 item 或规范化题名判断独立来源。
- `src/researchbrain/orchestration/orchestrator.py`：当 covered 仅由同一文献重复片段支撑时降级为 partial；人物/工作、方法、比较和结论类子问题要求至少两篇独立文献。
- `src/researchbrain/orchestration/orchestrator.py`：Assessor 未给出 missing 细节时补充确定性缺口原因；覆盖矩阵已进入第 14 节 `_should_stop_after_turn` 停止决策事件。
- `tests/test_orchestrator.py`：新增同一文献伪覆盖、多样性不足和缺口原因测试；沿用等级不足回归测试。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py -q`：19 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_stop_decision.py -q`：27 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：129 passed, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/orchestrator.py tests/test_orchestrator.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/orchestrator.py tests/test_orchestrator.py`：通过。

Remaining risks:

- 最低文献多样性目前是确定性类型规则；更细的按任务配置阈值和 UI 展示仍可在后续 Reviewer/UI 阶段扩展。
- 在线记录合并质量会影响来源多样性判定，需与第 7 节跨来源合并报告继续衔接。

### 2026-09-03：shouldStopAfterTurn 停止决策

Checklist items:

- [x] 第 14 节：定义 `StopDecision`：stop、reason、status、next_requirement。
- [x] 第 14 节：检查取消、超时、模型步数、工具数、查询轮数和预算。
- [x] 第 14 节：检查必答子问题覆盖、连续无新增相关证据、待审批/后台任务和 Reviewer 阻塞问题。
- [x] 第 14 节：仅在完整、受限或明确失败状态下停止，并通过事件持久化停止判断和理由。
- [x] 第 14 节：增加完成、退让、取消、等待和防死循环测试。

Implementation:

- `src/researchbrain/orchestration/models.py`：新增 `StopStatus` 与 `StopDecision` Pydantic Schema。
- `src/researchbrain/orchestration/orchestrator.py`：新增 `_compute_stop_decision` 与 `_should_stop_after_turn`，统一判断取消、超时、模型/工具/查询预算、覆盖缺口、连续无收益、等待审批/后台任务和 Reviewer 阻塞；在本地检索缺口评估、审批可用和审查后写出 `stop_decision` 事件。
- `tests/test_stop_decision.py`：新增独立测试组覆盖完成、覆盖不足继续、连续无收益退让、等待审批、Reviewer 阻塞和取消信号。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_stop_decision.py -q`：8 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_stop_decision.py tests/test_orchestrator.py -q`：25 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：127 passed, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_stop_decision.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/models.py src/researchbrain/orchestration/orchestrator.py tests/test_stop_decision.py`：通过。

Remaining risks:

- 当前停止决策已进入固定编排路径并通过事件持久化；真正的逐 turn Agent Loop、可恢复 ask_user 和后台任务 checkpoint 仍依赖第 12、19 节后续实现。
- 费用预算目前随模型/工具/轮数预算一起作为运行约束处理，尚未接入真实 provider token/费用计量。

### 2026-09-03：完整题录与定向全文多片段阅读工具

Checklist items:

- [x] 第 5 节：增加 `get_item` 工具读取完整题录、标识符、附件和处理状态。
- [x] 第 5 节：增加 `read_fulltext_chunks`，按文献、章节、页码和查询读取互补片段。
- [x] 第 5 节：关键文献执行多片段阅读，而非只用首次命中的一个片段。
- [x] 第 12 节工具清单：`get_item`、`read_fulltext_chunks`。

Implementation:

- `src/researchbrain/orchestration/tools.py`：新增 `GetItemArguments` 与 `FulltextChunkReadArguments`，作为受控只读工具的最小参数 Schema。
- `src/researchbrain/orchestration/orchestrator.py`：注册 `get_item`、`read_fulltext_chunks`；`get_item` 返回题录、DOI 等标识符、作者、附件、解析与全文索引状态；`read_fulltext_chunks` 按文献、章节、页码范围、查询词和排除 chunk 读取互补全文片段，并返回 `read_scope.not_full_document` 诊断。
- `src/researchbrain/orchestration/orchestrator.py`：本地检索命中关键全文片段后，会对最多 3 篇不同文献调用 `read_fulltext_chunks` 读取同章节/同页范围的补充片段并加入同一证据账本，避免只依赖首次命中的单个片段。
- `tests/test_orchestrator.py`：增加题录/状态读取、定向全文互补片段读取，以及本地检索实际流程触发多片段阅读的测试。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py -q`：17 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_research_tools.py -q`：19 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：119 passed, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_orchestrator.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/tools.py tests/test_orchestrator.py`：通过。

Remaining risks:

- 图题、表题、公式邻近文本和参考文献段落定向读取尚未实现。
- 已读/未读范围目前以 `read_scope` 诊断记录，不等同于完整全文阅读审计；扫描 PDF、解析失败和页码缺失的限制说明仍待补齐。

### 2026-09-03：本地检索融合、过滤和可选指标

Checklist items:

- [x] 第 4 节：增加题名/摘要、关键词、向量结果的可配置融合与重排。
- [x] 第 4 节：支持年份、类型、作者、期刊和证据等级过滤。
- [x] 第 4 节：记录 Recall@k、MRR 和 nDCG 等可选指标。

Implementation:

- `src/researchbrain/orchestration/models.py`：为本地检索加入向量、关键词、题录摘要和全文重排权重预算。
- `src/researchbrain/orchestration/tools.py`：扩展 `search_library` 参数，支持年份、类型、作者、期刊和证据等级过滤。
- `src/researchbrain/orchestration/orchestrator.py`：`search_library` 受控工具进入实际运行路径后执行候选放大、术语/元数据/证据等级过滤、加权重排，并把候选数量、融合来源和 Recall@k/MRR/nDCG 占位指标写入 `query_diagnostic.retrieval_metrics`；默认编排不提前丢弃排除词命中的候选，仍交给证据筛选保留审计。
- `src/researchbrain/retrieval/service.py`：题录摘要索引文本补充受控关键词字段，使题名、摘要、关键词与向量/关键词检索结果可一起进入本地融合重排。
- `tests/test_orchestrator.py`、`tests/test_retrieval.py`：增加本地检索过滤、重排、可选指标、元数据过滤和关键词索引确定性测试。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_retrieval.py -q`：20 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：117 passed, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/models.py src/researchbrain/orchestration/tools.py src/researchbrain/retrieval/service.py tests/test_orchestrator.py tests/test_retrieval.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/orchestrator.py src/researchbrain/orchestration/models.py src/researchbrain/orchestration/tools.py src/researchbrain/retrieval/service.py tests/test_orchestrator.py tests/test_retrieval.py`：通过。

Remaining risks:

- `Recall@k` 在没有固定质量集相关性标注时只能记录为可选空值；MRR/nDCG 当前基于本轮已选候选的运行时近似指标，后续需接入固定质量集真实标注。
- 第 4 节 UI 逐篇解释仍未勾选；下一批可衔接候选入选/排除理由的持久化与前端展示，或进入第 5 节完整题录/全文定向阅读工具。

### 2026-09-03：QuerySpec 英文核心式与扩展式补齐

Checklist items:

- [x] 第 3 节：每个子问题至少生成一个英文核心检索式。
- [x] 第 3 节：每个子问题至少生成一个英文同义词/缩写扩展式。

Implementation:

- `src/researchbrain/orchestration/queries.py`：英文 QuerySpec 改为确定性构造纯英文核心式；同义词/缩写扩展式按子问题补齐，未知中文不混入英文来源查询，原始中文仍保留在中文本地 QuerySpec 与受控映射中。
- `tests/test_query_specs.py`：增加多子问题英文核心式与扩展式覆盖测试，并校验英文来源查询不含 CJK 字符。

Verification:

- `.venv/Scripts/python.exe -m pytest tests/test_query_specs.py -q`：5 passed。
- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：114 passed, 1 warning。
- `.venv/Scripts/python.exe -m ruff check --no-cache src/researchbrain/orchestration/queries.py tests/test_query_specs.py`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src/researchbrain/orchestration/queries.py tests/test_query_specs.py`：通过。

Remaining risks:

- 确定性英文构造优先保证“可发送到英文来源且不混入中文”，任意学科术语的高质量翻译仍依赖受控术语表后续扩充或模型补充。
- 下一批可继续衔接第 5 节完整题录/全文定向阅读工具。

### 2026-09-03：ResearchIntent、子问题与 QuerySpec 第一批

Checklist items:

- [x] 第 1 节除可恢复 `ask_user` 外的 ResearchIntent 结构化、显式约束、主题校验和 UI 项。
- [x] 第 2 节的类型、优先级、依赖、完成判据、必答映射、去重和越界阻断项。
- [x] 第 3 节的 QuerySpec Schema、来源适配、诊断、改写停止和 UI 项；任意术语的纯英文
      确定性翻译仍未勾选。
- [x] 第 5 节的最低证据等级自动提升、第 6 节的 covered 等级确定性校验，以及第 7 节的
      QuerySpec 来源定向。

Implementation:

- `src/researchbrain/orchestration/intent.py`
- `src/researchbrain/orchestration/queries.py`
- `src/researchbrain/orchestration/orchestrator.py`
- `src/researchbrain/orchestration/evidence.py`
- `src/researchbrain/discovery/service.py`
- `desktop/src/App.tsx`、`desktop/src/api.ts`
- `tests/test_research_intent.py`、`tests/test_query_specs.py` 及编排器/来源回归测试

Verification:

- `.venv/Scripts/python.exe -m pytest -p no:cacheprovider`：113 passed。
- `.venv/Scripts/python.exe -m ruff check --no-cache src tests scripts`：通过。
- `.venv/Scripts/python.exe -m ruff format --check --no-cache src tests scripts`：通过。
- `cd desktop && bun run typecheck`：通过。
- `cd desktop && bun run build`：TypeScript 与 Vite production build 通过。
- `cd desktop && bunx prettier --check src scripts ../README.md ../README.en.md ../docs ../evaluation ../.github`：通过。

Remaining risks:

- 阻塞性歧义尚未进入可恢复 `ask_user`。
- 未依赖模型时，只能对受控术语表保证高质量中英互译；任意学科术语的纯英文核心式和同义词式
  仍保持未完成状态。
- 下一批应衔接完整题录/全文定向阅读和更严格的覆盖多样性校验。

### 2026-09-05：非阻断歧义、同轮澄清与 0.4.1 修复包

Checklist items:

- [x] 有明确领域、对象或方法的宽泛问题不再因可选范围偏好停止检索。
- [x] 未采纳的歧义文本不进入假设或后续检索上下文，防止跨域概念污染。
- [x] 真正阻断的问题显示专用澄清控件，并以 `clarification` Steering 恢复同一运行。
- [x] 澄清等待使用独立预算；超时后生成可见中文请求，不再返回英文内部错误。
- [x] 截图原问题完成固定回归与真实 DeepSeek 意图解析验证。
- [x] 版本统一为 `0.4.1`，生成并校验 NSIS、MSI 和打包 sidecar。

Implementation:

- `src/researchbrain/orchestration/intent.py`、`models.py`、`orchestrator.py`
- `src/researchbrain/api/app.py`
- `desktop/src/App.tsx`、`desktop/src/api.ts`、`desktop/src/styles.css`
- `tests/test_research_clarification.py`、`tests/test_research_runs_api.py`、
  `tests/test_research_diagnostic_ui.py`

Verification:

- Python 全量测试、Ruff check/format：通过。
- TypeScript、Vite、Prettier 和桌面 Playwright 检查：通过。
- Rust fmt/check/clippy、Tauri release build、打包 sidecar MCP smoke：通过。

Remaining risks:

- 完整报告质量仍取决于当前文库证据覆盖和在线来源可用性；本次修复针对错误阻断与交互，
  不等同于人工确认最终综述质量。

### 2026-09-05：工具预算收束与 0.4.2 修复包

Checklist items:

- [x] 工具批次超过剩余调用额度时按剩余额度截断，不再使整个运行失败。
- [x] 达到工具预算后停止新增本地/联网检索，并使用已筛选证据继续综合、审查和输出。
- [x] 本地或混合模式已作出的停止决定不会被后续联网分支覆盖。
- [x] 每个查询自动补读最高排名的一篇全文；Subagent 为主循环预留模型与工具额度。
- [x] 唯一合法的非工具动作走确定性路径，保留综合与 Reviewer 所需模型步数。
- [x] 并发工具批次使用原子预算预留，桌面过程区显示预算收束状态。
- [x] 版本统一为 `0.4.2`，生成并校验 NSIS、MSI 和打包 sidecar。

Implementation:

- `src/researchbrain/orchestration/orchestrator.py`、`tools.py`
- `desktop/src/App.tsx`、`desktop/src/api.ts`
- `tests/test_research_agent_action_loop.py`

Verification:

- Python 全量测试、Ruff check/format：通过。
- TypeScript、Vite、Prettier 和四组桌面 Playwright 检查：通过。
- Rust fmt/check/clippy、Tauri release build、打包 sidecar MCP smoke：通过。

Remaining risks:

- 尚需在安装后的真实本地/混合文库中重新运行截图中的长调研问题，核对最终报告质量和限制说明。
- 安装包未签名，尚未在另一台干净 Windows 机器验证升级、卸载和用户数据保留。

### 2026-09-05：引用协议容错与 0.4.3 修复包

Checklist items:

- [x] 常见全角括号、圆括号和小写证据编号在验证前归一化。
- [x] 合法证据已声明但行内引用遗漏时，执行一次受证据账本约束的引用修复。
- [x] 引用修复不得引入账本外证据；非法证据编号继续硬性拒绝。
- [x] 修复失败或额度不可用时，确定性返回带引用的筛选证据目录和限制说明。
- [x] 降级稿跳过语义改写，避免 Reviewer/Reviser 再次造成模型错误。
- [x] 桌面研究过程显示引用修复开始、完成和降级事件。

Implementation:

- `src/researchbrain/orchestration/orchestrator.py`
- `desktop/src/App.tsx`
- `tests/test_orchestrator.py`

Verification:

- Python 全量测试、Ruff check/format：通过。
- TypeScript、Vite、Prettier 和四组桌面 Playwright 检查：通过。
- Rust fmt/check/clippy、Tauri release build、源码及打包 sidecar MCP smoke：通过。
- 版本统一为 `0.4.3`，NSIS、MSI 和 sidecar 的 SHA-256 已写入发布说明。

Remaining risks:

- 需要在安装版中重试真实联网调研，确认所用模型稳定遵循修复提示词。
- 检索到的 TEC 预测文献不能自动视为地震前兆证据，报告仍需明确证据边界。

### 2026-09-06：Markdown 主张审查与 0.4.4 报告完整性修复

Checklist items:

- [x] 使用实际运行数据库确认初稿、修订稿和最终短回答之间的删减链路。
- [x] 按 Markdown 行、表格行和自然句提取主张，不再全局压平换行。
- [x] 标题、表头、分隔线不进入事实主张审查。
- [x] 根据章节识别研究建议、可检验假设和证据限制。
- [x] 部分事实有引用但仍有漏引时，在 Reviewer 前执行引用绑定。
- [x] 最终删除按 claim ID 和 Markdown 块进行，不再留下残缺标题或编号。
- [x] 生成稿或删减稿不满足调研报告完整度时，输出确定性受限报告。
- [x] 受限报告包含证据表、覆盖矩阵、证据缺口、检索式、研究步骤和限制。
- [x] 重复 Reviewer 错误合并显示，避免几十条相近限制污染结果。
- [x] 桌面研究过程显示报告质量门禁及受限报告重建状态。

Implementation:

- `src/researchbrain/agent/service.py`
- `src/researchbrain/orchestration/orchestrator.py`
- `desktop/src/App.tsx`
- `tests/test_claim_review_pipeline.py`
- `tests/test_orchestrator.py`

Verification:

- Python 全量测试、Ruff check/format：通过。
- TypeScript、Vite、Prettier 和四组桌面 Playwright 检查：通过。

Remaining risks:

- 真实模型的报告内容仍受在线检索覆盖和全文可获得性限制。
- 必须在安装版中重新运行原 TEC 问题，核对受限报告的实际可读性和引用准确性。

## 建议实施顺序

1. `ResearchIntent`、歧义识别和确认。
2. `QuerySpec`、来源适配和检索诊断。
3. `ComparisonMatrix`、Claim Extractor 和逐主张 Reviewer。
4. 把所有只读能力接入 Registry。
5. 实现 `AgentAction -> tool_result -> next turn` 主循环。
6. 接通 `beforeToolCall`、`afterToolCall`、`shouldStopAfterTurn`。
7. 接入审批写工具、任务等待和 checkpoint 恢复。
8. 实现 Follow-up、会话树和真正的 Subagent。
9. 完成质量 A/B、Windows 打包和合并评审。

## 每次更新模板

```text
Checklist items:
- [x] 章节号 + 条目

Implementation:
- files / migrations / API / UI

Verification:
- exact test commands and results
- optional real-service or packaged-app result

Remaining risks:
- known limits and next unchecked item
```
