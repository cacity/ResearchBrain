from __future__ import annotations

import re
from collections import defaultdict

from researchbrain.orchestration.intent import TopicValidator
from researchbrain.orchestration.models import (
    CoverageItem,
    QuerySource,
    QuerySpec,
    ResearchIntent,
    ResearchPlan,
    ResearchSubquestion,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z][a-z0-9-]{1,}", re.I)

# Deliberately small and auditable. Original terms are always retained beside mapped terms.
_TERM_PAIRS: tuple[tuple[str, str], ...] = (
    ("球谐分析", "spherical harmonic analysis"),
    ("球面谐波", "spherical harmonics"),
    ("内外源分离", "internal external field separation"),
    ("地磁场", "geomagnetic field"),
    ("重力场", "gravity field"),
    ("患者", "patients"),
    ("临床", "clinical"),
    ("诊断", "diagnosis"),
    ("多波束声呐", "multibeam sonar"),
    ("机器学习", "machine learning"),
    ("深度学习", "deep learning"),
    ("最小二乘", "least squares"),
    ("数据集", "dataset"),
    ("数据来源", "data source"),
    ("数据处理", "data processing"),
    ("工作流程", "workflow"),
    ("处理流程", "processing workflow"),
    ("研究方法", "research methods"),
    ("方法", "methods"),
    ("结果", "results"),
    ("局限", "limitations"),
    ("缺陷", "limitations"),
    ("比较", "comparison"),
    ("研究空白", "research gaps"),
    ("作者", "authors"),
    ("文献", "literature"),
)

_BIOMEDICAL_TERMS = {
    "医学",
    "临床",
    "疾病",
    "患者",
    "药物",
    "生物医学",
    "medical",
    "clinical",
    "disease",
    "patient",
    "drug",
    "biomedical",
}

_STOPWORDS = {
    "about",
    "and",
    "compare",
    "find",
    "for",
    "how",
    "literature",
    "of",
    "research",
    "review",
    "study",
    "the",
    "what",
    "which",
    "请",
    "帮我",
    "调研",
    "研究",
    "分析",
    "一下",
    "形成",
    "报告",
}


def normalize_subquestions(
    subquestions: list[ResearchSubquestion],
    intent: ResearchIntent,
    limit: int,
) -> list[ResearchSubquestion]:
    """Keep planner decomposition in scope, deduplicate it, and cover every must-answer item."""
    validator = TopicValidator(intent)
    candidates: list[ResearchSubquestion] = []
    seen: list[set[str]] = []
    for source in sorted(subquestions, key=lambda value: (value.priority, int(value.id[1:]))):
        validation = validator.validate(source.question)
        if not validation.accepted:
            continue
        tokens = _tokens(source.question)
        if any(_similar(tokens, previous) >= 0.82 for previous in seen):
            continue
        seen.append(tokens)
        candidates.append(source)

    requirements = list(intent.must_answer) or [intent.normalized_question]
    for requirement in requirements:
        if any(_requirement_is_mapped(requirement, value.question) for value in candidates):
            continue
        candidates.append(
            ResearchSubquestion(
                id=f"Q{len(candidates) + 1}",
                question=requirement[:500],
                type=_subquestion_type(requirement),
                priority=1,
                completion_criteria=_completion_criteria(requirement),
                required_level=_required_level(requirement),
            )
        )

    if not candidates:
        candidates.append(
            ResearchSubquestion(
                id="Q1",
                question=intent.normalized_question[:500],
                type=_subquestion_type(intent.normalized_question),
                priority=1,
                completion_criteria=_completion_criteria(intent.normalized_question),
                required_level=_required_level(intent.normalized_question),
            )
        )

    # If requirements exceed the configured limit, preserve them in one final composite subquestion.
    selected = candidates[:limit]
    unmapped = [
        value
        for value in requirements
        if not any(_requirement_is_mapped(value, question.question) for question in selected)
    ]
    if unmapped and selected:
        composite = "；".join(unmapped)[:500]
        selected[-1] = selected[-1].model_copy(
            update={
                "question": f"{selected[-1].question}；{composite}"[:500],
                "completion_criteria": _deduplicate(
                    [*selected[-1].completion_criteria, *unmapped],
                    8,
                ),
            }
        )

    old_to_new = {value.id: f"Q{index}" for index, value in enumerate(selected, 1)}
    normalized: list[ResearchSubquestion] = []
    for index, value in enumerate(selected, 1):
        dependencies = [
            old_to_new[item]
            for item in value.depends_on
            if item in old_to_new and old_to_new[item] != f"Q{index}"
        ]
        normalized.append(
            value.model_copy(
                update={
                    "id": f"Q{index}",
                    "type": value.type if value.type != "other" else _subquestion_type(value.question),
                    "depends_on": list(dict.fromkeys(dependencies)),
                    "completion_criteria": value.completion_criteria or _completion_criteria(value.question),
                    "required_level": _stronger_level(value.required_level, _required_level(value.question)),
                }
            )
        )
    return normalized


def normalize_query_specs(plan: ResearchPlan, intent: ResearchIntent) -> list[QuerySpec]:
    """Normalize model queries and deterministically fill the required bilingual/source plan."""
    subquestions = {value.id: value for value in plan.subquestions}
    specs: list[QuerySpec] = []
    for source in plan.query_specs:
        if source.subquestion_id not in subquestions:
            continue
        query = _clean_query(source.query)
        if not query:
            continue
        target_source: QuerySource = "crossref" if source.source == "all_online" else source.source
        specs.append(
            source.model_copy(
                update={
                    "source": target_source,
                    "query": adapt_query_text(query, target_source, source, intent),
                    "start_year": source.start_year or intent.time_range.start_year,
                    "end_year": source.end_year or intent.time_range.end_year,
                    "excluded_terms": _deduplicate(
                        [*source.excluded_terms, *plan.excluded_terms, *intent.must_exclude],
                        20,
                    ),
                }
            )
        )

    for subquestion in plan.subquestions:
        current = [value for value in specs if value.subquestion_id == subquestion.id]
        if not any(value.source == "local" and value.language == "zh" for value in current):
            specs.append(
                _base_spec(
                    subquestion,
                    intent,
                    "zh",
                    "local",
                    "中文本地核心检索",
                    plan.excluded_terms,
                )
            )
        if not any(value.language == "en" and value.source != "local" for value in current):
            specs.append(
                _base_spec(
                    subquestion,
                    intent,
                    "en",
                    "crossref",
                    "英文核心检索",
                    plan.excluded_terms,
                )
            )
        current = [value for value in specs if value.subquestion_id == subquestion.id]
        if not any(value.language == "en" and value.synonyms for value in current):
            specs.append(_synonym_spec(subquestion, intent, plan.excluded_terms))

    # Ensure every online adapter is represented without multiplying every subquestion by every source.
    first = plan.subquestions[0]
    existing_sources = {value.source for value in specs}
    for source in ("crossref", "openalex", "arxiv"):
        if source not in existing_sources:
            specs.append(_source_spec(first, intent, source, plan.excluded_terms))
    if _pubmed_applicable(intent) and "pubmed" not in existing_sources:
        specs.append(_source_spec(first, intent, "pubmed", plan.excluded_terms))

    unique: list[QuerySpec] = []
    seen: set[tuple[str, str, str]] = set()
    for value in specs:
        key = (value.subquestion_id, value.source, _normalize(value.query))
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return [value.model_copy(update={"id": f"S{index}"}) for index, value in enumerate(unique[:48], 1)]


def local_query_specs(plan: ResearchPlan, limit: int) -> list[QuerySpec]:
    values = [value for value in plan.query_specs if value.source == "local"]
    if not values:
        values = [
            QuerySpec(
                id=f"S{index}",
                subquestion_id=subquestion.id,
                language="mixed",
                source="local",
                query=_clean_query(subquestion.question),
                rationale="兼容旧计划的本地检索式",
            )
            for index, subquestion in enumerate(plan.subquestions, 1)
        ]
    return _round_robin(values, limit)


def runtime_query_specs(
    queries: list[str],
    plan: ResearchPlan,
    *,
    source: QuerySource,
    limit: int,
) -> list[QuerySpec]:
    values: list[QuerySpec] = []
    first_id = _next_spec_number(plan.query_specs)
    for index, query in enumerate(_deduplicate(queries, limit), first_id):
        subquestion = _best_subquestion(query, plan.subquestions)
        values.append(
            QuerySpec(
                id=f"S{index}",
                subquestion_id=subquestion.id,
                language=_query_language(query),
                source=source,
                query=_clean_query(query),
                concepts=_concepts(query),
                excluded_terms=list(plan.excluded_terms),
                start_year=(plan.research_intent.time_range.start_year if plan.research_intent else None),
                end_year=(plan.research_intent.time_range.end_year if plan.research_intent else None),
                rationale="证据缺口触发的补充检索",
            )
        )
    return values


def online_query_specs(
    plan: ResearchPlan,
    coverage: list[CoverageItem],
    question: str,
    limit: int,
) -> list[QuerySpec]:
    runtime: list[QuerySpec] = []
    next_id = _next_spec_number(plan.query_specs)
    intent = plan.research_intent or ResearchIntent(normalized_question=question)
    for item in coverage:
        if item.status == "covered":
            continue
        for query_index, query in enumerate(item.next_queries):
            source = _gap_source(plan, item.subquestion_id, query_index)
            spec = QuerySpec(
                id=f"S{next_id}",
                subquestion_id=item.subquestion_id,
                language=_query_language(query),
                source=source,
                query=_clean_query(query),
                rationale="覆盖矩阵缺口查询",
            )
            runtime.append(
                spec.model_copy(update={"query": adapt_query_text(spec.query, source, spec, intent)})
            )
            next_id += 1
    planned = [value for value in plan.query_specs if value.source != "local"]
    values = _round_robin([*runtime, *planned], limit)
    if not values:
        first = plan.subquestions[0]
        values = [
            QuerySpec(
                id=f"S{next_id}",
                subquestion_id=first.id,
                language=_query_language(question),
                source="crossref",
                query=_clean_query(question),
                rationale="在线检索回退",
            )
        ]
    return values


def _next_spec_number(specs: list[QuerySpec]) -> int:
    numbers = [int(value.id[1:]) for value in specs if value.id[1:].isdigit()]
    return max(numbers, default=0) + 1


def _gap_source(plan: ResearchPlan, subquestion_id: str, index: int) -> QuerySource:
    sources = [
        value.source
        for value in plan.query_specs
        if value.subquestion_id == subquestion_id and value.source not in {"local", "all_online"}
    ]
    return sources[index % len(sources)] if sources else "crossref"


def rewrite_local_query_specs(
    round_specs: list[QuerySpec],
    diagnostics: dict[str, dict[str, object]],
    plan: ResearchPlan,
    limit: int,
) -> list[QuerySpec]:
    """Expand zero-hit queries and narrow noisy queries without escaping the original intent."""
    values: list[QuerySpec] = []
    next_id = _next_spec_number([*plan.query_specs, *round_specs])
    intent = plan.research_intent
    scope_terms = [*intent.domains, *intent.research_objects, *intent.methods] if intent else plan.topic_terms
    for spec in round_specs:
        diagnostic = diagnostics.get(spec.id) or {}
        result_count = int(diagnostic.get("result_count") or 0)
        relevant_count = int(diagnostic.get("relevant_count") or 0)
        query = ""
        rationale = ""
        synonyms: list[str] = []
        if result_count == 0:
            expansions = [
                value
                for candidate in plan.query_specs
                if candidate.subquestion_id == spec.subquestion_id
                for value in [*candidate.synonyms, *candidate.abbreviations]
            ]
            synonyms = _deduplicate(expansions, 8)
            if synonyms:
                query = " ".join([spec.query, *synonyms])
                rationale = "零命中，使用受控同义词和缩写扩展"
        elif result_count >= 5 and relevant_count / result_count < 0.25:
            query = " ".join(_deduplicate([*scope_terms, spec.query], 12))
            rationale = "噪声过高，增加领域、对象、方法和排除边界"
        if not query or _normalize(query) == _normalize(spec.query):
            continue
        values.append(
            spec.model_copy(
                update={
                    "id": f"S{next_id}",
                    "query": query[:500],
                    "synonyms": _deduplicate([*spec.synonyms, *synonyms], 20),
                    "excluded_terms": _deduplicate(
                        [*spec.excluded_terms, *plan.excluded_terms],
                        20,
                    ),
                    "rationale": rationale,
                }
            )
        )
        next_id += 1
        if len(values) >= limit:
            break
    return values


def query_spec_sources(spec: QuerySpec) -> list[str]:
    return [] if spec.source in {"local", "all_online"} else [spec.source]


def adapt_query_text(
    query: str,
    source: QuerySource,
    spec: QuerySpec,
    intent: ResearchIntent,
) -> str:
    clean = _clean_query(query)
    if source == "pubmed":
        if "[" not in clean:
            concepts = spec.concepts or _concepts(clean)
            clean = " AND ".join(f'"{value}"[Title/Abstract]' for value in concepts[:5]) or clean
        if spec.start_year or intent.time_range.start_year:
            start = spec.start_year or intent.time_range.start_year
            end = spec.end_year or intent.time_range.end_year or 3000
            clean += f' AND ("{start}"[Date - Publication] : "{end}"[Date - Publication])'
        for value in (spec.excluded_terms or intent.must_exclude)[:4]:
            clean += f' NOT "{value}"[Title/Abstract]'
    elif source == "arxiv":
        concepts = spec.concepts or _concepts(clean)
        clean = " AND ".join(f'"{value}"' if " " in value else value for value in concepts[:6]) or clean
    elif source in {"crossref", "openalex"}:
        clean = " ".join((spec.concepts or _concepts(clean))[:8]) or clean
    return clean[:500]


def controlled_term_mapping() -> tuple[tuple[str, str], ...]:
    return _TERM_PAIRS


def _base_spec(
    subquestion: ResearchSubquestion,
    intent: ResearchIntent,
    language: str,
    source: QuerySource,
    rationale: str,
    excluded_terms: list[str] | None = None,
) -> QuerySpec:
    query = (
        _local_core(subquestion.question, intent) if language == "zh" else _to_english(subquestion.question)
    )
    concepts = _concepts(query)
    resolved_language = language if language == "zh" else _query_language(query)
    return QuerySpec(
        id="S1",
        subquestion_id=subquestion.id,
        language=resolved_language,
        source=source,
        query=query,
        concepts=concepts,
        excluded_terms=_deduplicate([*(excluded_terms or []), *intent.must_exclude], 20),
        start_year=intent.time_range.start_year,
        end_year=intent.time_range.end_year,
        rationale=rationale,
    )


def _synonym_spec(
    subquestion: ResearchSubquestion,
    intent: ResearchIntent,
    excluded_terms: list[str],
) -> QuerySpec:
    core = _to_english(subquestion.question)
    synonyms = _mapped_terms(subquestion.question, to_english=True)
    abbreviations = [value for value in re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", subquestion.question)]
    expansion = _deduplicate([core, *synonyms, *abbreviations], 8)
    return QuerySpec(
        id="S1",
        subquestion_id=subquestion.id,
        language=_query_language(core),
        source="openalex",
        query=" OR ".join(f'"{value}"' if " " in value else value for value in expansion)[:500],
        concepts=_concepts(core),
        synonyms=synonyms or [core],
        abbreviations=abbreviations,
        excluded_terms=_deduplicate([*excluded_terms, *intent.must_exclude], 20),
        start_year=intent.time_range.start_year,
        end_year=intent.time_range.end_year,
        rationale="英文同义词和缩写扩展检索",
    )


def _source_spec(
    subquestion: ResearchSubquestion,
    intent: ResearchIntent,
    source: QuerySource,
    excluded_terms: list[str],
) -> QuerySpec:
    base = _base_spec(
        subquestion,
        intent,
        "en",
        source,
        f"{source} 来源适配检索",
        excluded_terms,
    )
    return base.model_copy(update={"query": adapt_query_text(base.query, source, base, intent)})


def _local_core(question: str, intent: ResearchIntent) -> str:
    scientific = [*intent.domains, *intent.research_objects, *intent.methods]
    query = _to_chinese(_clean_query(question))
    query = re.sub(r"^(?:请|麻烦)?(?:帮我|给我)?(?:调研|研究|分析|查找|查询)(?:一下)?[，,：:\s]*", "", query)
    query = re.sub(r"(?:形成|生成|输出|写成).*(?:报告|综述|表)$", "", query).strip(" ，,。；;")
    return " ".join(_deduplicate([*scientific, query], 12))[:500]


def _to_chinese(value: str) -> str:
    translated = value
    for chinese, english in sorted(_TERM_PAIRS, key=lambda pair: len(pair[1]), reverse=True):
        translated = re.sub(re.escape(english), f" {chinese} ", translated, flags=re.I)
    translated = " ".join(translated.split())
    if not _CJK_RE.search(translated):
        translated = f"{translated} 相关文献"
    return translated


def _to_english(value: str) -> str:
    translated = value
    for chinese, english in sorted(_TERM_PAIRS, key=lambda pair: len(pair[0]), reverse=True):
        translated = translated.replace(chinese, f" {english} ")
    translated = re.sub(r"[请帮我调研一下形成报告综述有哪些各种还的了及与和、，。；：？]", " ", translated)
    translated = " ".join(translated.split())
    return translated or value


def _mapped_terms(value: str, *, to_english: bool) -> list[str]:
    return [
        target if to_english else source
        for source, target in _TERM_PAIRS
        if source in value or target in value.casefold()
    ]


def _pubmed_applicable(intent: ResearchIntent) -> bool:
    text = " ".join(
        [*intent.domains, *intent.research_objects, *intent.methods, intent.normalized_question]
    ).casefold()
    return any(value in text for value in _BIOMEDICAL_TERMS)


def _subquestion_type(value: str) -> str:
    lowered = value.casefold()
    patterns = (
        ("people_and_work", ("谁", "作者", "团队", "who", "author", "group")),
        ("data", ("数据", "dataset", "data source", "resolution")),
        ("workflow", ("流程", "步骤", "workflow", "pipeline", "procedure")),
        ("limitation", ("局限", "缺陷", "问题", "limitation", "drawback")),
        ("comparison", ("比较", "对比", "差异", "comparison", "compare", "versus")),
        ("research_gap", ("空白", "未来", "缺口", "research gap", "future work")),
        ("result", ("结果", "发现", "结论", "result", "finding", "outcome")),
        ("method", ("方法", "算法", "模型", "method", "algorithm", "model")),
    )
    return next((kind for kind, terms in patterns if any(term in lowered for term in terms)), "landscape")


def _required_level(value: str) -> str:
    lowered = value.casefold()
    if any(
        term in lowered
        for term in (
            "图",
            "表",
            "公式",
            "方程",
            "参数",
            "数值",
            "页",
            "figure",
            "table",
            "equation",
            "parameter",
            "numeric",
        )
    ):
        return "fulltext_page"
    if any(
        term in lowered
        for term in (
            "数据",
            "流程",
            "步骤",
            "方法",
            "局限",
            "缺陷",
            "结果",
            "data",
            "workflow",
            "method",
            "limitation",
            "result",
        )
    ):
        return "fulltext_section"
    return "structured_abstract"


def _stronger_level(left: str, right: str) -> str:
    order = {"metadata": 0, "structured_abstract": 1, "fulltext_section": 2, "fulltext_page": 3}
    return left if order[left] >= order[right] else right


def _completion_criteria(value: str) -> list[str]:
    kind = _subquestion_type(value)
    if kind == "comparison":
        return ["至少比较对象、数据、方法、结果和局限中的适用项", "差异均有证据 ID"]
    if kind == "people_and_work":
        return ["作者、年份、文献与贡献形成对应关系"]
    if kind in {"data", "method", "workflow", "result", "limitation"}:
        return ["至少一条达到所需证据等级的直接证据", "明确未覆盖细节"]
    return ["使用同主题文献回答并标明证据缺口"]


def _requirement_is_mapped(requirement: str, question: str) -> bool:
    normalized_requirement = _normalize(requirement).replace(" ", "")
    normalized_question = _normalize(question).replace(" ", "")
    if normalized_requirement and normalized_requirement in normalized_question:
        return True
    left = _tokens(requirement)
    right = _tokens(question)
    return bool(left) and (left <= right or _similar(left, right) >= 0.55)


def _best_subquestion(query: str, values: list[ResearchSubquestion]) -> ResearchSubquestion:
    query_tokens = _tokens(query)
    return max(values, key=lambda value: _similar(query_tokens, _tokens(value.question)))


def _round_robin(values: list[QuerySpec], limit: int) -> list[QuerySpec]:
    grouped: dict[str, list[QuerySpec]] = defaultdict(list)
    for value in values:
        grouped[value.subquestion_id].append(value)
    result: list[QuerySpec] = []
    while grouped and len(result) < limit:
        for key in list(grouped):
            if grouped[key]:
                result.append(grouped[key].pop(0))
                if len(result) >= limit:
                    break
            if not grouped[key]:
                del grouped[key]
    return result


def _query_language(value: str) -> str:
    has_cjk = bool(_CJK_RE.search(value))
    has_english = bool(_WORD_RE.search(value))
    if has_cjk and has_english:
        return "mixed"
    return "zh" if has_cjk else "en"


def _concepts(value: str) -> list[str]:
    quoted = re.findall(r'"([^"\n]{2,80})"', value)
    words = [word for word in _WORD_RE.findall(value) if word.casefold() not in _STOPWORDS]
    chinese = [item for item in re.split(r"[\s，,。；;：:、]+", value) if _CJK_RE.search(item)]
    return _deduplicate([*quoted, *chinese, *words], 20)


def _tokens(value: str) -> set[str]:
    normalized = _normalize(value)
    english = {word for word in _WORD_RE.findall(normalized) if word not in _STOPWORDS}
    chinese = set(re.findall(r"[\u4e00-\u9fff]{2,8}", normalized))
    if not chinese and _CJK_RE.search(normalized):
        chinese = {normalized.replace(" ", "")}
    return english | chinese


def _similar(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 0.0


def _clean_query(value: str) -> str:
    return " ".join(str(value).split()).strip(" ,，。；;：:")[:500]


def _deduplicate(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean_query(raw)
        key = _normalize(value)
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-/]+", " ", value.casefold()).strip()
