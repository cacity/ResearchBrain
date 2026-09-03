from researchbrain.orchestration.models import (
    QuerySpec,
    ResearchIntent,
    ResearchPlan,
    ResearchSubquestion,
    ResearchTimeRange,
)
from researchbrain.orchestration.queries import (
    normalize_query_specs,
    normalize_subquestions,
    rewrite_local_query_specs,
)


def test_subquestions_are_typed_deduplicated_scoped_and_cover_requirements():
    intent = ResearchIntent(
        normalized_question="比较球谐分析的数据、方法和局限，排除多波束声呐",
        methods=["球谐分析"],
        must_answer=["使用了哪些数据", "比较了哪些方法", "有哪些局限"],
        must_exclude=["多波束声呐"],
    )
    values = normalize_subquestions(
        [
            ResearchSubquestion(id="Q1", question="球谐分析使用了哪些数据"),
            ResearchSubquestion(id="Q2", question="球谐分析使用了哪些数据"),
            ResearchSubquestion(id="Q3", question="多波束声呐如何处理数据"),
        ],
        intent,
        6,
    )

    assert [value.id for value in values] == ["Q1", "Q2", "Q3"]
    assert [value.type for value in values] == ["data", "comparison", "limitation"]
    assert all(value.completion_criteria for value in values)
    assert all(
        requirement in " ".join(value.question for value in values) for requirement in intent.must_answer
    )
    assert all("多波束" not in value.question for value in values)


def test_query_specs_fill_bilingual_queries_and_source_adapters():
    intent = ResearchIntent(
        normalized_question="比较球谐分析方法和数据处理",
        domains=["地球物理学"],
        methods=["球谐分析"],
        time_range=ResearchTimeRange(start_year=2022, end_year=2026),
        must_exclude=["多波束声呐"],
        must_answer=["比较球谐分析方法和数据处理"],
    )
    plan = ResearchPlan(
        intent="比较球谐分析",
        research_intent=intent,
        subquestions=[ResearchSubquestion(id="Q1", question="比较球谐分析方法和数据处理", type="comparison")],
        queries=["冗长原问题"],
    )

    specs = normalize_query_specs(plan, intent)

    assert any(value.language == "zh" and value.source == "local" for value in specs)
    assert any(value.language == "en" and value.source == "crossref" for value in specs)
    assert any(value.language == "en" and value.synonyms for value in specs)
    assert {"crossref", "openalex", "arxiv"} <= {value.source for value in specs}
    assert all(value.source != "all_online" for value in specs)
    assert all(value.subquestion_id == "Q1" for value in specs)
    assert all(value.start_year == 2022 and value.end_year == 2026 for value in specs)
    assert all("多波束声呐" in value.excluded_terms for value in specs)
    assert len({value.id for value in specs}) == len(specs)


def test_query_rewrite_expands_zero_hits_and_narrows_noisy_results():
    intent = ResearchIntent(
        normalized_question="球谐分析方法",
        domains=["地球物理学"],
        methods=["球谐分析"],
        must_exclude=["多波束声呐"],
    )
    plan = ResearchPlan(
        intent="球谐分析方法",
        research_intent=intent,
        subquestions=[ResearchSubquestion(id="Q1", question="球谐分析方法")],
        queries=["球谐分析方法"],
        topic_terms=["球谐分析"],
        excluded_terms=["多波束声呐"],
        query_specs=[
            QuerySpec(
                id="S1",
                subquestion_id="Q1",
                language="zh",
                source="local",
                query="球谐分析方法",
            ),
            QuerySpec(
                id="S2",
                subquestion_id="Q1",
                language="en",
                source="openalex",
                query="spherical harmonics",
                synonyms=["spherical harmonic analysis"],
                abbreviations=["SHA"],
            ),
        ],
    )

    expanded = rewrite_local_query_specs(
        [plan.query_specs[0]],
        {"S1": {"result_count": 0, "relevant_count": 0}},
        plan,
        3,
    )
    narrowed = rewrite_local_query_specs(
        [plan.query_specs[0]],
        {"S1": {"result_count": 12, "relevant_count": 1}},
        plan,
        3,
    )

    assert "spherical harmonic analysis" in expanded[0].query
    assert "SHA" in expanded[0].query
    assert "零命中" in expanded[0].rationale
    assert "地球物理学" in narrowed[0].query
    assert "多波束声呐" in narrowed[0].excluded_terms
    assert "噪声过高" in narrowed[0].rationale


def test_pubmed_query_uses_fields_dates_and_exclusions_for_biomedical_intent():
    intent = ResearchIntent(
        normalized_question="比较患者机器学习诊断方法",
        domains=["医学"],
        research_objects=["患者"],
        methods=["机器学习"],
        time_range=ResearchTimeRange(start_year=2020, end_year=2025),
        must_exclude=["animal"],
    )
    plan = ResearchPlan(
        intent="临床方法比较",
        research_intent=intent,
        subquestions=[ResearchSubquestion(id="Q1", question="比较患者机器学习诊断方法")],
        queries=["患者机器学习诊断"],
        query_specs=[
            QuerySpec(
                id="S1",
                subquestion_id="Q1",
                language="en",
                source="pubmed",
                query="patient machine learning diagnosis",
                concepts=["patient", "machine learning", "diagnosis"],
            )
        ],
    )

    pubmed = next(value for value in normalize_query_specs(plan, intent) if value.source == "pubmed")

    assert "[Title/Abstract]" in pubmed.query
    assert "[Date - Publication]" in pubmed.query
    assert 'NOT "animal"[Title/Abstract]' in pubmed.query
