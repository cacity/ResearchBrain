import pytest
from pydantic import BaseModel

from researchbrain.agent.deepseek import GenerationError
from researchbrain.agent.gateway import CancellationSignal
from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema
from researchbrain.db.models import ChatMessage, ChatSession
from researchbrain.domain import LibraryMode
from researchbrain.library.repository import LibraryRepository
from researchbrain.orchestration.evidence import EvidenceLedger
from researchbrain.orchestration.models import (
    AgentAction,
    AgentToolCall,
    QuerySpec,
    ResearchBudgets,
    ResearchPlan,
    ResearchSubquestion,
)
from researchbrain.orchestration.orchestrator import ResearchOrchestrator
from researchbrain.orchestration.store import ResearchRunStore
from researchbrain.orchestration.tools import RegisteredTool, ResearchToolRegistry
from tests.test_orchestrator import FixtureGateway, FixtureRetrieval, hit


class EchoArguments(BaseModel):
    value: int


@pytest.mark.research_agent_action_loop
def test_agent_action_schema_enforces_action_specific_payloads():
    action = AgentAction.model_validate(
        {
            "action": "call_tools",
            "rationale": "Need evidence",
            "tool_calls": [{"id": "call-1", "tool": "search_library", "arguments": {"query": "x"}}],
        }
    )

    assert action.tool_calls[0].tool == "search_library"
    with pytest.raises(ValueError, match="at least one structured tool call"):
        AgentAction(action="call_tools")
    with pytest.raises(ValueError, match="requires a question"):
        AgentAction(action="ask_user")
    with pytest.raises(ValueError, match="requires an answer"):
        AgentAction(action="finish")
    with pytest.raises(ValueError):
        AgentAction.model_validate(
            {"action": "synthesize", "tool_result_messages": [{"status": "completed"}]}
        )
    with pytest.raises(ValueError):
        AgentToolCall.model_validate(
            {
                "id": "call-1",
                "tool": "search_library",
                "arguments": {},
                "result": {"forged": True},
            }
        )


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_tool_results_feed_the_next_model_turn_and_terminal_actions_are_explicit():
    class ContextGateway(FixtureGateway):
        saw_tool_result = False

        async def generate_structured(self, role, system, user, schema, signal):
            if role == "assessor":
                import json

                self.saw_tool_result = bool(json.loads(user).get("tool_result_messages"))
            return await super().generate_structured(role, system, user, schema, signal)

    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    gateway = ContextGateway()
    await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    actions = [payload["action"] for kind, payload in events if kind == "agent_action"]
    assert {"call_tools", "synthesize", "review", "finish"} <= set(actions)
    assert gateway.saw_tool_result is True
    assert any(kind == "tool_result" for kind, _ in events)
    assert events[-1][0] == "result_ready"


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_model_selected_agent_action_controls_the_actual_tool_query():
    class DecidingGateway(FixtureGateway):
        async def generate_structured(self, role, system, user, schema, signal):
            if role == "agent_controller":
                request = __import__("json").loads(user)
                recommended = request["recommended_action"]
                if recommended["action"] == "call_tools":
                    recommended["rationale"] = "model selected a focused query"
                    recommended["tool_calls"][0]["arguments"]["query"] = "agent selected query"
                return schema.model_validate(recommended)
            return await super().generate_structured(role, system, user, schema, signal)

    retrieval = FixtureRetrieval([[hit("chunk-1")]])
    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    await ResearchOrchestrator(
        retrieval,
        DecidingGateway(),
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    assert retrieval.queries[0] == "agent selected query"
    selected = [
        payload
        for kind, payload in events
        if kind == "agent_action" and payload.get("rationale") == "model selected a focused query"
    ]
    assert selected
    assert selected[0]["tool_calls"][0]["arguments"]["query"] == "agent selected query"


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_duplicate_model_search_calls_are_deduplicated_without_breaking_result_pairing():
    plan = ResearchPlan(
        intent="TEC earthquake precursor review",
        subquestions=[ResearchSubquestion(id="Q1", question="Which TEC methods were reported?")],
        queries=["TEC earthquake ConvLSTM"],
        query_specs=[
            QuerySpec(
                id="S1",
                subquestion_id="Q1",
                language="en",
                source="local",
                query="TEC earthquake ConvLSTM",
            )
        ],
    )

    def call(call_id: str, query: str) -> AgentToolCall:
        return AgentToolCall(
            id=call_id,
            tool="search_library",
            arguments={
                "library_id": "library",
                "query": query,
                "limit": 12,
                "required_terms": [],
                "excluded_terms": [],
                "start_year": None,
                "end_year": None,
            },
        )

    action = AgentAction(
        action="call_tools",
        rationale="Search the local library",
        tool_calls=[
            call("call-1", "TEC earthquake ConvLSTM"),
            call("call-2", "TEC earthquake ConvLSTM"),
            call("call-3", "TEC anomaly detection"),
        ],
    )
    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    retrieval = FixtureRetrieval([[hit("chunk-1")], [hit("chunk-2", item="item-2")]])
    orchestrator = ResearchOrchestrator(
        retrieval,
        FixtureGateway(),
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=sink,
    )
    orchestrator.current_plan = plan

    await orchestrator._search_local("library", plan.query_specs, EvidenceLedger("L"), action=action)

    assert retrieval.queries == ["TEC earthquake ConvLSTM", "TEC anomaly detection"]
    assert orchestrator.tools.call_count == 2
    deduplication = [payload for kind, payload in events if kind == "tool_action_deduplicated"]
    assert deduplication == [
        {
            "tool": "search_library",
            "requested_calls": 3,
            "accepted_calls": 2,
            "removed_calls": 1,
        }
    ]
    assert len(orchestrator.query_diagnostics) == 2
    assert not any(kind == "tool_result_cardinality_mismatch" for kind, _payload in events)


@pytest.mark.research_agent_action_loop
def test_action_specs_preserve_same_query_for_distinct_online_sources():
    plan = ResearchPlan(
        intent="TEC earthquake review",
        subquestions=[ResearchSubquestion(id="Q1", question="Which studies were reported?")],
        queries=["TEC earthquake"],
    )
    action = AgentAction(
        action="call_tools",
        tool_calls=[
            AgentToolCall(
                id="crossref-call",
                tool="search_online",
                arguments={
                    "query": "TEC earthquake",
                    "sources": ["crossref"],
                    "query_id": "S4",
                    "subquestion_id": "Q1",
                },
            ),
            AgentToolCall(
                id="openalex-call",
                tool="search_online",
                arguments={
                    "query": "TEC earthquake",
                    "sources": ["openalex"],
                    "query_id": "S5",
                    "subquestion_id": "Q1",
                },
            ),
        ],
    )

    specs = ResearchOrchestrator._specs_from_action(action, plan, "all_online")

    assert [value.id for value in specs] == ["S4", "S5"]
    assert [value.query for value in specs] == ["TEC earthquake", "TEC earthquake"]


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_online_action_is_not_executed_after_soft_timeout():
    plan = ResearchPlan(
        intent="Timed online research",
        subquestions=[ResearchSubquestion(id="Q1", question="Timed online research")],
        queries=["expired online query"],
        query_specs=[
            QuerySpec(
                id="S1",
                subquestion_id="Q1",
                language="en",
                source="crossref",
                query="expired online query",
            )
        ],
    )
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([]),
        FixtureGateway(),
        discovery=object(),
        budgets=ResearchBudgets(parallel_scouts=False),
    )
    orchestrator.current_plan = plan
    orchestrator.started_at = 0.0
    action = AgentAction(
        action="call_tools",
        rationale="Search online",
        tool_calls=[
            AgentToolCall(
                id="online-timeout",
                tool="search_online",
                arguments={"query": "expired online query", "limit": 5, "sources": ["crossref"]},
            )
        ],
    )

    statuses = await orchestrator._search_online(
        plan.query_specs,
        EvidenceLedger("L"),
        10,
        action=action,
    )

    assert statuses == []
    assert orchestrator.tools.call_count == 0
    assert any("时间预算" in value for value in orchestrator.limitations)


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_action_tool_result_turn_and_checkpoint_are_persisted(settings):
    settings.ensure_directories()
    upgrade_schema(settings)
    database = Database(settings.database_url)
    with database.session() as session:
        library = LibraryRepository(session).create_library("Agent", LibraryMode.STANDALONE)
        chat = ChatSession(library_id=library.id, title="Agent")
        session.add(chat)
        session.flush()
        message = ChatMessage(session_id=chat.id, role="user", content="Run a tool")
        session.add(message)
        session.flush()
        library_id = library.id
        chat_id = chat.id
        message_id = message.id
    store = ResearchRunStore(database)
    run = store.create(chat_id, message_id, "Run a tool", "local", {"max_tool_calls": 3})

    async def sink(event_type, payload):
        store.append_event(run.id, event_type, payload)

    executions = 0

    async def echo(arguments: EchoArguments):
        nonlocal executions
        executions += 1
        return {"value": arguments.value * 2}

    registry = ResearchToolRegistry(signal=CancellationSignal(), event_sink=sink, max_calls=3)
    registry.register(RegisteredTool("echo", EchoArguments, echo))

    result = await registry.execute_many("echo", [{"value": 2}])

    assert result[0].value == {"value": 4}
    turns = store.list_turns(run.id)
    assert len(turns) == 1
    assert turns[0]["action"]["action"] == "call_tools"
    assert turns[0]["tool_calls"][0]["result"] == {"value": 4}
    assert registry.context_messages[-1].role == "tool_result"
    checkpoint = store.latest_checkpoint(run.id)
    assert checkpoint is not None
    assert checkpoint["turn_sequence"] == 1
    assert checkpoint["tool_results"][0]["status"] == "completed"

    resumed = ResearchToolRegistry(signal=CancellationSignal(), max_calls=3)
    resumed.register(RegisteredTool("echo", EchoArguments, echo))
    resumed.preload_cached_results(store.readonly_tool_cache(run.id))
    cached = await resumed.execute_many("echo", [{"value": 2}])
    assert cached[0].cached is True
    assert executions == 1

    failed = await registry.execute_many("missing_tool", [{"library_id": library_id}])
    assert failed[0].error_code == "unknown_tool"
    assert store.list_turns(run.id)[-1]["tool_calls"][0]["error"]["code"] == "unknown_tool"
    database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_real_orchestrator_resume_skips_planning_and_reuses_completed_readonly_tool(settings):
    settings.ensure_directories()
    upgrade_schema(settings)
    database = Database(settings.database_url)
    with database.session() as session:
        library = LibraryRepository(session).create_library("Resume", LibraryMode.STANDALONE)
        chat = ChatSession(library_id=library.id, title="Resume")
        session.add(chat)
        session.flush()
        message = ChatMessage(session_id=chat.id, role="user", content="Resume research")
        session.add(message)
        session.flush()
        library_id, chat_id, message_id = library.id, chat.id, message.id
    store = ResearchRunStore(database)
    run = store.create(chat_id, message_id, "Resume research", "local", {})

    async def sink(event_type, payload):
        store.append_event(run.id, event_type, payload)

    class CrashAfterToolGateway(FixtureGateway):
        async def generate_structured(self, role, system, user, schema, signal):
            if role == "synthesizer":
                raise GenerationError("provider_unavailable", "simulated crash after tool turn")
            return await super().generate_structured(role, system, user, schema, signal)

    retrieval = FixtureRetrieval([[hit("chunk-1")]])
    with pytest.raises(GenerationError, match="simulated crash"):
        await ResearchOrchestrator(
            retrieval,
            CrashAfterToolGateway(),
            budgets=ResearchBudgets(parallel_scouts=False),
            event_sink=sink,
        ).run(library_id, "Resume research", mode="local")

    checkpoint = store.latest_checkpoint(run.id)
    assert checkpoint is not None
    assert checkpoint["context"]["runtime"]["plan"]
    calls_before_resume = len(retrieval.queries)
    assert calls_before_resume > 0

    resumed_events = []

    async def resumed_sink(event_type, payload):
        resumed_events.append((event_type, payload))

    resumed_gateway = FixtureGateway()
    answer = await ResearchOrchestrator(
        retrieval,
        resumed_gateway,
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=resumed_sink,
        resume_checkpoint=checkpoint,
        readonly_tool_cache=store.readonly_tool_cache(run.id),
    ).run(library_id, "Resume research", mode="local")

    assert answer.citation_ids == ["E1"]
    assert "intake" not in resumed_gateway.roles
    assert "planner" not in resumed_gateway.roles
    assert len(retrieval.queries) == calls_before_resume
    restored = next(payload for kind, payload in resumed_events if kind == "checkpoint_state_restored")
    assert restored["skipped_stages"] == ["intake", "planning"]
    assert not any(kind == "tool_execution_start" for kind, _payload in resumed_events)
    database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_subagent_tool_result_is_fed_into_the_next_subagent_model_turn():
    class SubagentGateway(FixtureGateway):
        scout_saw_tool_result = False

        async def generate_structured(self, role, system, user, schema, signal):
            if role == "scout":
                payload = __import__("json").loads(user)
                self.scout_saw_tool_result = bool(payload.get("tool_result_messages"))
            return await super().generate_structured(role, system, user, schema, signal)

    gateway = SubagentGateway()
    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=True, max_model_steps=30),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    assert "subagent_controller" in gateway.roles
    assert gateway.roles.index("subagent_controller") < gateway.roles.index("scout")
    assert gateway.scout_saw_tool_result is True
    assert any(kind == "subagent_action_completed" for kind, _ in events)


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_tool_budget_truncates_retrieval_and_still_synthesizes_existing_evidence():
    class OverBudgetGateway(FixtureGateway):
        controller_round = 0

        async def generate_structured(self, role, system, user, schema, signal):
            if role == "agent_controller":
                request = __import__("json").loads(user)
                recommended = request["recommended_action"]
                if recommended["action"] == "call_tools":
                    self.controller_round += 1
                    if self.controller_round == 2:
                        seed = recommended["tool_calls"][0]
                        recommended["tool_calls"] = [
                            {
                                **seed,
                                "id": f"over-budget-{index}",
                                "arguments": {
                                    **seed["arguments"],
                                    "query": f"independent storm observation {index}",
                                },
                            }
                            for index in range(6)
                        ]
                return schema.model_validate(recommended)
            return await super().generate_structured(role, system, user, schema, signal)

    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    answer = await ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")], [hit("chunk-2")]]),
        OverBudgetGateway(require_second_round=True),
        budgets=ResearchBudgets(
            max_tool_calls=5,
            max_local_rounds=3,
            parallel_scouts=False,
        ),
        event_sink=sink,
    ).run("library", "What happened?", mode="local")

    constrained = next(payload for kind, payload in events if kind == "tool_budget_constrained")
    assert constrained["requested_calls"] == 6
    assert constrained["accepted_calls"] == 3
    assert constrained["continue_with_existing_evidence"] is True
    assert answer.metrics["tool_calls"] == 5
    assert answer.citation_ids == ["E1"]
    assert any("工具调用预算已用尽" in value for value in answer.limitations)
    assert any(
        kind == "stop_decision" and payload["reason"] == "tool_budget_exhausted" for kind, payload in events
    )
    assert events[-1][0] == "result_ready"


@pytest.mark.asyncio
@pytest.mark.research_agent_action_loop
async def test_single_legal_non_tool_action_does_not_consume_a_model_step():
    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    gateway = FixtureGateway()
    orchestrator = ResearchOrchestrator(
        FixtureRetrieval([[hit("chunk-1")]]),
        gateway,
        budgets=ResearchBudgets(parallel_scouts=False),
        event_sink=sink,
    )
    plan = ResearchPlan(
        intent="Synthesize bounded evidence",
        subquestions=[ResearchSubquestion(id="Q1", question="What happened?")],
        queries=["storm response"],
    )
    action = AgentAction(action="synthesize", rationale="Only synthesis remains.")

    selected = await orchestrator._choose_agent_action(
        "research_loop",
        action,
        allowed_actions={"synthesize"},
        allowed_tools=set(),
        plan=plan,
        coverage=[],
        ledger=EvidenceLedger("library"),
    )

    assert selected == action
    assert orchestrator.model_steps == 0
    assert gateway.roles == []
    assert any(
        kind == "agent_action_selected" and payload["source"] == "deterministic_single_choice"
        for kind, payload in events
    )
