# ResearchBrain 研究 Agent 实施清单

更新时间：2026-09-03  
核对分支：`feature/pi-research-orchestrator`  
核对提交：`53e3b2d`

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
- [ ] 为下列每个未完成模块建立独立测试分组。
- [ ] 保存固定质量集的覆盖率、引用支持率、耗时和模型调用基线。
- [ ] 对旧流程和新 Agent Loop 做可复现的同题盲评。

## 1. 理解问题与 ResearchIntent

### 已有基础

- [x] `intake` 接收原始问题、最近历史和会话记忆。
- [x] 历史回答只作检索线索，证据权重为零。
- [x] Planner 输出简要 intent、子问题、主题词、排除词和完成条件。
- [x] Planner 提示词要求按运行日期解释“最近几年”等相对时间。
- [x] 已知跨域歧义可经过确定性主题门禁。

### 待实现

- [ ] 新增 `ResearchIntent` Schema 和任务类型。
- [ ] 增加 `domains`、`research_objects` 和 `methods`。
- [ ] 增加 `data_requirements`，记录数据类型、分辨率和来源要求。
- [ ] 增加规范化的 `time_range`、`geography` 和语言限制。
- [ ] 增加 `must_answer`、`must_include`、`must_exclude` 和 `deliverables`。
- [ ] 增加 `ambiguities`、`assumptions` 和 `clarification_required`。
- [ ] 用确定性规则先抽取显式年份、地域、范围、格式和排除条件。
- [ ] Intake 模型只补充隐含意图，不得覆盖显式用户限制。
- [ ] 新增 Topic Validator，独立校验领域、对象、方法和排除概念。
- [ ] 阻塞性歧义进入 `ask_user`，回答后继续同一运行。
- [ ] ResearchIntent 贯穿 Planner、检索、筛选、Reviewer 和最终门禁。
- [ ] UI 展示识别出的范围、假设和待确认项。
- [ ] 增加中英文、多义词、相对日期和复合任务测试。

## 2. 拆分子问题

- [x] `ResearchSubquestion` 保存稳定 ID、问题和最低证据等级。
- [x] Planner 可拆分最多 10 个子问题。
- [x] 模型失败时，中文复合问题仍能回退拆分。
- [x] 覆盖矩阵通过 `Q1`、`Q2` 等 ID 关联证据。
- [ ] 增加人物/工作、数据、方法、流程、结果、局限、比较、空白等类型。
- [ ] 增加优先级、依赖关系和单项完成判据。
- [ ] 确保每个 `must_answer` 至少映射一个子问题。
- [ ] 合并重复子问题，阻止子问题超出原始意图。
- [ ] 增加“漏掉用户要求”和“自行扩题”的确定性测试。

## 3. 中英文及来源专用检索式

### 已有基础

- [x] `ResearchPlan` 保存普通字符串检索式。
- [x] Assessor 可在覆盖不足时生成补充检索式。
- [x] 检索式会去重并受数量预算限制。
- [x] 多轮本地检索会使用上一轮的缺口查询。

### QuerySpec 改造

- [ ] 新增 `QuerySpec`：ID、子问题 ID、语言、来源和查询文本。
- [ ] 保存核心概念、同义词、缩写、排除词、日期和生成理由。
- [ ] 每个子问题至少生成一个中文本地检索式。
- [ ] 每个子问题至少生成一个英文核心检索式。
- [ ] 每个子问题至少生成一个英文同义词/缩写扩展式。
- [ ] 为 PubMed 生成适用的 MeSH/字段检索式。
- [ ] 为 arXiv、OpenAlex 和 Crossref 生成来源适配检索式。
- [ ] 本地查询优先题名、摘要、关键词和全文术语，不直接复用冗长原问题。
- [ ] 建立受控中英文术语映射并保留原始词。
- [ ] 记录每条查询的命中、相关、重复和分数分布。
- [ ] 零命中时扩展同义词，噪声过高时增加领域限定和排除词。
- [ ] 查询改写受固定轮数限制，连续无收益时停止。
- [ ] UI 按子问题和来源显示查询及效果。
- [ ] 增加 Schema、来源适配和查询改写测试。

## 4. 多轮本地检索

- [x] 支持最多三轮本地补检索，并可配置预算。
- [x] 本地检索通过受控 `search_library` 工具执行。
- [x] 工具参数校验、调用预算和并行结果归并已接通。
- [x] 证据按指纹去重，并限制单篇文献的片段占比。
- [x] 每轮执行 relevant/adjacent/irrelevant 三级筛选。
- [x] 邻近和无关证据保留审计，但不参与综合和 DOI 候选。
- [ ] 增加题名/摘要、关键词、向量结果的可配置融合与重排。
- [ ] 支持年份、类型、作者、期刊和证据等级过滤。
- [ ] 记录 Recall@k、MRR 和 nDCG 等可选指标。
- [ ] UI 解释每篇候选为何入选或排除。

## 5. 阅读题录、摘要和全文

- [x] 区分 metadata、structured_abstract、fulltext_section、fulltext_page。
- [x] 本地检索可返回全文块、章节和页码。
- [x] 在线题录/摘要不会冒充全文证据。
- [x] 证据面板可显示来源、片段、章节和页码。
- [ ] 增加 `get_item` 工具读取完整题录、标识符、附件和处理状态。
- [ ] 增加 `read_fulltext_chunks`，按文献、章节、页码和查询读取互补片段。
- [ ] 关键文献执行多片段阅读，而非只用首次命中的一个片段。
- [ ] 方法、数值、图、表、公式问题自动提高最低证据等级。
- [ ] 支持图题、表题、公式邻近文本和参考文献段落定向读取。
- [ ] 记录已读与未读范围，禁止把局部检索称为“读完全文”。
- [ ] 对扫描 PDF、解析失败和页码缺失给出明确限制。
- [ ] 增加证据等级、跨页片段和图表问答测试。

## 6. 证据覆盖矩阵与缺口判断

- [x] 每个子问题有 covered、partial 或 insufficient_evidence 状态。
- [x] 覆盖项记录证据等级、证据 ID、缺失内容和下一查询。
- [x] 每轮本地检索及联网后重新计算覆盖。
- [x] 混合模式仅在本地覆盖不足时联网。
- [ ] 确定性检查 covered 项的证据存在且达到最低等级。
- [ ] 检查覆盖是否只依赖同一文献的重复片段。
- [ ] 人物、方法比较和结论类问题支持最低文献多样性要求。
- [ ] 记录每个缺口不能回答的具体原因。
- [ ] 覆盖矩阵接入 `shouldStopAfterTurn`。
- [ ] 增加伪覆盖、单一来源覆盖和等级不足测试。

## 7. 联网学术搜索

- [x] 支持 Crossref、OpenAlex、arXiv 和 PubMed 元数据搜索。
- [x] 记录各来源成功、失败、超时和结果数量。
- [x] 在线证据进入统一账本并接受主题筛选。
- [x] Google Scholar 仅浏览器跳转，不自动抓取。
- [x] 在线 DOI 可经用户批准后导入。
- [ ] QuerySpec 明确目标来源，不把同一字符串无差别发送给所有来源。
- [ ] 按 DOI、PMID、arXiv ID 和规范化题名生成跨来源合并报告。
- [ ] 优先保留信息完整、可追溯且有摘要的记录。
- [ ] 增加种子文献的参考文献和后续引用追踪。
- [ ] 组合时间排序与相关性排序。
- [ ] 增加来源级重试、限流退避及降级说明。
- [ ] 增加来源夹具测试和可选真实服务烟雾测试。

## 8. DOI/PDF、解析和向量化闭环

- [x] 在线发现 DOI 后发出 `approval_available`。
- [x] 用户批准前不写入文库。
- [x] DOI 规范化和唯一标识避免重复题录。
- [x] DOI 导入后可查询合法开放 PDF。
- [x] PDF 按内容哈希保存并排队解析、向量化。
- [x] 等待期内完成的全文可回到同一运行证据账本。
- [x] 超时后可用现有摘要继续并保留任务状态。
- [ ] 将 `lookup_doi`、`import_dois`、`queue_fulltext`、`job_status`、`parse_pdf`、
  `embed_document` 注册为 Agent 受控工具。
- [ ] 所有写工具接入审批、文库作用域、幂等键和审计日志。
- [ ] Agent 根据缺口决定查题录、获取全文或等待解析。
- [ ] 解析完成后从暂停 action turn 继续，而不是重跑全部只读阶段。
- [ ] 增加已有摘要/PDF/向量、重复 DOI 和失败重试端到端测试。

## 9. 跨文献比较与综合

- [x] Synthesizer 接收问题、覆盖矩阵和筛选后的证据包。
- [x] 正文只能引用当前证据账本 ID。
- [x] 成文后主题门禁会移除已知跨域污染。
- [x] 输出列出未覆盖问题和不可用在线来源。
- [ ] 新增 `ComparisonMatrix`，记录对象、数据、方法、流程、结果和局限。
- [ ] 区分文献原述、跨文献归纳和系统推断。
- [ ] 对互相矛盾的结果保留条件差异并交给 Reviewer。
- [ ] “谁做过”输出作者、年份、文献和贡献的对应关系。
- [ ] “数据流程”合并共同步骤并保留各文献特有步骤。
- [ ] “缺陷”区分作者自述局限和系统推断。
- [ ] 增加矩阵完整性及跨文献矛盾测试。

## 10. 初稿、引用和主题安全

- [x] 初稿生成前完成计划、检索、筛选和覆盖判断。
- [x] 模型不能引用未提供的证据 ID。
- [x] 引用 ID 必须真实出现在正文。
- [x] 证据准入、综合和成文后共有三道主题检查。
- [ ] 把每个事实性主张拆成稳定 `claim_id`。
- [ ] 保存 claim、citation 和 evidence span 对应关系。
- [ ] 限制单个引用支撑过多不同主张。
- [ ] 明确标记证据不足、推断和建议性内容。
- [ ] 增加引用漂移、无引用事实和主题漏判测试。

## 11. Reviewer 与修订

### 已有基础

- [x] 确定性检查未知引用、正文未使用引用和缺失引用。
- [x] 独立 Reviewer 接收问题、子问题、覆盖、初稿和证据原文。
- [x] Reviewer 可报告不支持、无效引用、等级越界、漏答和矛盾。
- [x] 阻塞问题最多触发一次修订。
- [x] 修订后再次执行确定性引用检查和主题门禁。

### 逐主张审查流水线

- [ ] Claim Extractor 将初稿拆成可单独核验的事实主张。
- [ ] Citation Validator 检查每个主张附近的引用。
- [ ] Evidence Level Validator 按主张类型检查证据等级。
- [ ] Entailment Reviewer 逐项判断直接支持、部分支持或不支持。
- [ ] Contradiction Reviewer 比较证据之间及证据与正文的矛盾。
- [ ] Coverage Reviewer 独立检查 `must_answer` 和全部子问题。
- [ ] Topic Reviewer 按领域、对象和方法检查跨域引用。
- [ ] 审查结果保存 claim ID、严重度、证据 ID、理由和修改建议。
- [ ] 修订器只能删除、降级或用现有证据改写。
- [ ] 修订后重新执行完整语义 Reviewer。
- [ ] 仍有阻塞问题时删除主张或返回受限答案，不发布原句。
- [ ] UI 展示阻塞、警告和被删除/降级的主张。
- [ ] 增加错误引文、摘要冒充全文、图表越界、矛盾和漏答测试。

## 12. 真正的 Action/Tool Loop

### 当前固定编排基础

- [x] 当前按固定状态机执行计划、检索、评估、综合、审查和修订。
- [x] 状态机拒绝非法阶段跳转。
- [x] 运行有模型步数、工具数、轮数和超时预算。
- [x] `search_library`、`search_online` 已走统一工具注册表。

### Agent Action 协议

- [ ] 新增 `AgentAction` Schema。
- [ ] 支持 `call_tools` 和一个或多个结构化工具调用。
- [ ] 支持可恢复的 `ask_user`。
- [ ] 支持受限的 `delegate`。
- [ ] 支持 `synthesize`、`review` 和 `finish`。
- [ ] 每轮模型只决定下一步动作，不能伪造工具结果。
- [ ] 工具结果以 `tool_result` 消息追加到运行上下文。
- [ ] 下一轮根据证据、覆盖和预算继续决策。
- [ ] 未知工具、无效参数、越权和重复写入返回结构化错误。
- [ ] 每个 turn、action、tool call 和结果均持久化。
- [ ] 应用重启后从最后完整 turn 恢复。
- [ ] 增加空证据、补检索、工具失败、等待用户和预算耗尽测试。

### 工具清单

- [x] `search_library`。
- [x] `search_online`。
- [ ] `get_item`。
- [ ] `read_fulltext_chunks`。
- [ ] `lookup_doi`。
- [ ] `import_dois`。
- [ ] `queue_fulltext`。
- [ ] `job_status`。
- [ ] `parse_pdf`。
- [ ] `embed_document`。
- [ ] `export_references`。
- [ ] 每个工具定义最小 Schema、权限、并发、超时和幂等策略。

## 13. `beforeToolCall` / `afterToolCall`

- [x] Registry 已预留 `before_call` 和 `after_call` 接口。
- [x] Registry 拒绝直接注册未审批写工具。
- [x] 工具执行会发出开始/结束事件并返回结构化错误。
- [ ] Orchestrator 实际安装 `beforeToolCall` 策略链。
- [ ] 调用前校验 Schema、文库作用域、预算、审批、域名和幂等键。
- [ ] 写工具调用前生成明确审批请求。
- [ ] Orchestrator 实际安装 `afterToolCall` 策略链。
- [ ] 调用后规范化结果并更新证据账本、覆盖矩阵。
- [ ] 调用后记录耗时、来源状态、错误分类和使用量。
- [ ] 外部文本按不可信数据隔离，阻断其中的提示注入。
- [ ] 策略拒绝可被 Agent 理解，但不能被绕过。
- [ ] 增加钩子顺序、拒绝、异常、审批和注入测试。

## 14. `shouldStopAfterTurn`

- [ ] 定义 `StopDecision`：stop、reason、status、next_requirement。
- [ ] 检查取消和应用关闭信号。
- [ ] 检查超时、模型步数、工具数、查询轮数和费用预算。
- [ ] 检查必答子问题的覆盖情况。
- [ ] 检查连续多轮无新增相关证据。
- [ ] 检查待审批、待澄清和后台任务。
- [ ] 检查 Reviewer 是否仍有阻塞问题。
- [ ] 仅在可输出完整、受限或明确失败结果时允许 finish。
- [ ] 每轮持久化停止判断和理由。
- [ ] 增加完成、退让、超时、取消、等待和防死循环测试。

## 15. 事件流、Steering 和 Abort

- [x] 事件带递增序号并持久化。
- [x] SSE 返回阶段、工具、证据、覆盖、审查和答案事件。
- [x] 客户端重连可读取持久化事件。
- [x] API/UI 可取消运行。
- [x] 取消信号传入模型和工具。
- [x] Steering 在阶段边界进入 Assessor/Synthesizer 上下文。
- [ ] Steering 区分 constraint、correction 和 follow_up。
- [ ] 新约束触发意图、查询、候选证据和覆盖重校验。
- [ ] 冲突旧证据标记失效，不再参与综合。
- [ ] 立即约束可中止当前可取消工具并重新规划。
- [ ] UI 显示 Steering 何时生效及影响步骤。
- [ ] 增加执行中取消、阶段 Steering 和 SSE 重连测试。

## 16. Context Compaction 与会话记忆

- [x] 最近 8 条裁剪后的历史消息进入模型上下文。
- [x] 会话记忆保存目标、约束、术语、来源标识和未解决问题。
- [x] 历史回答只作 navigation hypotheses。
- [x] 最终回答后更新持久化记忆。
- [ ] 使用 tokenizer 估算上下文，而非只按消息数和字符数裁剪。
- [ ] 达到窗口阈值时生成结构化 compaction checkpoint。
- [ ] 保存摘要覆盖边界、生成模型和版本。
- [ ] 无损保留关键用户限制和 DOI/PMID/arXiv 标识。
- [ ] 新问题重新读取原证据，不把压缩摘要当证据。
- [ ] 新证据冲突时更新或移除旧假设。
- [ ] 增加超长会话、跨轮约束、错误历史和恢复测试。

## 17. 会话树和分支

- [x] 支持多个持久化线性会话和运行历史。
- [ ] `chat_messages` 增加 `parent_message_id` 或等价关系。
- [ ] 分支保存根消息、名称和创建来源。
- [ ] 可从任意历史用户消息创建分支。
- [ ] 分支只继承节点之前的约束和来源标识。
- [ ] 旧回答不作为分支证据，仍需重新检索。
- [ ] UI 展示路径、切换入口和当前分支。
- [ ] 删除/归档不破坏其他分支引用。
- [ ] 增加迁移、创建、并发和历史读取测试。

## 18. Subagent

- [x] 可并行启动只读 Scout，返回结构化 `ScoutFinding`。
- [x] Scout 只接收子问题和已筛选证据，无写权限。
- [x] Scout 结果可供 Assessor 使用。
- [ ] Subagent 有独立任务、预算、上下文和允许工具列表。
- [ ] Subagent 有自己的 action/tool loop，而非单次模型调用。
- [ ] Subagent 可执行本地检索和定向全文阅读。
- [ ] 在线 Subagent 按来源或策略分工并避免重复搜索。
- [ ] Subagent 只返回结构化证据、缺口和查询建议。
- [ ] Aggregator 校验证据 ID 和主题后再合并。
- [ ] 父 Agent 可取消单个或全部 Subagent。
- [ ] 限制并发、总步数、工具数和上下文大小。
- [ ] 固定质量集 A/B 验证 Subagent 的收益和成本。

## 19. Follow-up 队列与精确恢复

- [ ] 区分当前运行 Steering 和运行结束后的 Follow-up。
- [ ] 持久化 Follow-up 队列、顺序、来源消息和目标分支。
- [ ] 当前运行结束后按策略启动下一项。
- [ ] UI 可查看、排序和删除未开始 Follow-up。
- [x] 启动时把失去 worker 的活动运行标为 paused。
- [x] failed、paused、cancelled 运行可 retry。
- [x] DOI、内容哈希和任务状态避免明显重复写入。
- [ ] Checkpoint 保存 turn、action、工具结果、覆盖和预算。
- [ ] Retry 从安全 checkpoint 继续，不重复已完成的只读工具。
- [ ] 恢复前确认审批有效期和外部任务状态。
- [ ] 增加崩溃恢复、重复写、过期审批和队列测试。

## 20. UI 与诊断

- [x] UI 显示阶段、查询、主题边界、证据数、工具数和审查问题。
- [x] Markdown 正常渲染，右侧证据区独立滚动。
- [x] 运行中可停止任务。
- [x] 历史对话和研究运行可重新打开。
- [ ] 展示 ResearchIntent 和确认状态。
- [ ] 展示 QuerySpec、命中率和改写过程。
- [ ] 展示 action、tool call、tool result 和停止理由时间线。
- [ ] 展示 claim 到 evidence span 的对应关系。
- [ ] 展示待审批、待澄清、后台任务和 Follow-up。
- [ ] 展示会话树和分支。
- [ ] 提供不含密钥和全文的诊断导出。
- [ ] 完成键盘、窄窗口、长答案和独立滚动测试。

## 21. 最终验收与发布

- [ ] 本文档所有功能条目均为 `[x]`，且有测试或验收证据。
- [ ] Python、Ruff、TypeScript、Vite、Rust 和 MCP smoke 全部通过。
- [ ] Agent Loop、Reviewer、恢复和会话树端到端测试通过。
- [ ] DeepSeek 完成本地、混合、在线、无证据和歧义真实烟雾测试。
- [ ] MiniMax 完成摘要、全文块和查询向量真实烟雾测试。
- [ ] 在线元数据和开放全文来源完成可选真实烟雾测试。
- [ ] 固定质量集引用 ID 有效率 100%。
- [ ] 全文证据等级越界错误为 0。
- [ ] 跨主题证据进入最终答案数量为 0。
- [ ] 人工判定引用直接支持附近主张的比例不低于 90%。
- [ ] 新流程相对 0.3.1 的覆盖率和总体评分有可复现提升。
- [ ] Windows 干净环境安装、升级、卸载和数据保留测试通过。
- [ ] 生成新 NSIS/MSI 并记录版本、大小和 SHA-256。
- [ ] 更新 README、架构、Pi 对照、Changelog 和发布说明。
- [ ] 完成代码审查；质量不达标时继续保留功能分支。

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
