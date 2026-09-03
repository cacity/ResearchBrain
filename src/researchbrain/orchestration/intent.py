from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from researchbrain.orchestration.models import ResearchIntent, ResearchTimeRange

_YEAR_RE = re.compile(r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_NUMBER_WORDS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_GEOGRAPHIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("中国", ("中国", "国内", "china", "chinese")),
    ("美国", ("美国", "美國", "united states", "u.s.", " usa ")),
    ("欧洲", ("欧洲", "歐洲", "europe", "european")),
    ("亚洲", ("亚洲", "亞洲", "asia", "asian")),
    ("全球", ("全球", "全世界", "global", "worldwide")),
)

_DOMAIN_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("地球物理学", ("地球物理", "geophysics", "geomagnetic", "地磁", "重力场")),
    ("医学", ("医学", "临床", "疾病", "患者", "medical", "clinical", "disease", "patient")),
    ("计算机科学", ("计算机", "机器学习", "人工智能", "computer science", "machine learning", " ai ")),
    ("海洋测绘", ("海洋测绘", "多波束", "水深测量", "multibeam", "hydrographic")),
    ("遥感", ("遥感", "卫星影像", "remote sensing", "satellite imagery")),
)

_METHOD_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("球谐分析", ("球谐分析", "球面谐波", "spherical harmonic", "spherical harmonics")),
    ("最小二乘", ("最小二乘", "least squares")),
    ("机器学习", ("机器学习", "machine learning")),
    ("深度学习", ("深度学习", "deep learning")),
    ("系统综述", ("系统综述", "systematic review")),
    ("荟萃分析", ("荟萃分析", "meta-analysis", "meta analysis")),
)

_OBJECT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("地磁场", ("地磁场", "地磁", "geomagnetic field")),
    ("重力场", ("重力场", "gravity field")),
    ("多波束声呐", ("多波束声呐", "多波束测深", "multibeam sonar")),
    ("患者", ("患者", "病人", "patients", "patient")),
    ("数据集", ("数据集", "dataset", "datasets")),
)

_DELIVERABLE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("调研报告", ("调研报告", "研究报告", "research report")),
    ("文献综述", ("文献综述", "综述", "literature review")),
    ("比较表", ("比较表", "对比表", "comparison table")),
    ("参考文献列表", ("参考文献", "bibliography", "reference list")),
    ("数据流程", ("数据流程", "处理流程", "data workflow")),
)

_DATA_PATTERNS = (
    re.compile(
        r"[^，,。；;]{0,24}(?:数据类型|数据来源|数据集|空间分辨率|时间分辨率|采样率|分辨率)[^，,。；;]{0,40}"
    ),
    re.compile(
        r"[^,.;]{0,30}(?:data source|dataset|spatial resolution|temporal resolution|"
        r"sampling rate)[^,.;]{0,50}",
        re.I,
    ),
)

_EXCLUDE_PATTERNS = (
    re.compile(r"(?:排除|不包括|不要包含|不考虑|剔除|仅排除)\s*([^，,。；;]{1,100})"),
    re.compile(r"(?:exclude|excluding|do not include|without)\s+([^,.;]{1,120})", re.I),
)

_INCLUDE_PATTERNS = (
    re.compile(r"(?:必须包括|必须包含|需要包括|需要包含|重点包括)\s*([^，,。；;]{1,100})"),
    re.compile(r"(?:must include|including)\s+([^,.;]{1,120})", re.I),
)


def extract_explicit_intent(question: str, current_year: int) -> ResearchIntent:
    """Extract constraints stated in the current user message without model inference."""
    normalized = " ".join(question.split()).strip()
    years = [int(value) for value in _YEAR_RE.findall(normalized)]
    time_range = _extract_time_range(normalized, years, current_year)
    must_exclude = _pattern_values(normalized, _EXCLUDE_PATTERNS)
    must_include = _pattern_values(normalized, _INCLUDE_PATTERNS)
    data_requirements = _matching_fragments(normalized, _DATA_PATTERNS)
    must_answer = _extract_must_answer(normalized)
    return ResearchIntent(
        task_type=_infer_task_type(normalized),
        normalized_question=normalized,
        domains=_controlled_matches(normalized, _DOMAIN_TERMS),
        research_objects=_controlled_matches(normalized, _OBJECT_TERMS),
        methods=_controlled_matches(normalized, _METHOD_TERMS),
        data_requirements=data_requirements,
        time_range=time_range,
        geography=_controlled_matches(normalized, _GEOGRAPHIES),
        languages=_extract_languages(normalized),
        must_answer=must_answer,
        must_include=must_include,
        must_exclude=must_exclude,
        deliverables=_controlled_matches(normalized, _DELIVERABLE_TERMS),
    )


def merge_research_intent(explicit: ResearchIntent, inferred: ResearchIntent) -> ResearchIntent:
    """Merge model inference while giving every explicit user constraint precedence."""
    start_year = explicit.time_range.start_year or inferred.time_range.start_year
    end_year = explicit.time_range.end_year or inferred.time_range.end_year
    description = explicit.time_range.description or inferred.time_range.description
    return inferred.model_copy(
        update={
            "normalized_question": explicit.normalized_question,
            "task_type": explicit.task_type
            if explicit.task_type != "literature_review"
            else inferred.task_type,
            "domains": _union(explicit.domains, inferred.domains, 12),
            "research_objects": _union(explicit.research_objects, inferred.research_objects, 20),
            "methods": _union(explicit.methods, inferred.methods, 20),
            "data_requirements": _union(explicit.data_requirements, inferred.data_requirements, 20),
            "time_range": ResearchTimeRange(
                start_year=start_year,
                end_year=end_year,
                description=description,
            ),
            "geography": explicit.geography or inferred.geography,
            "languages": explicit.languages or inferred.languages,
            "must_answer": _union(explicit.must_answer, inferred.must_answer, 20),
            "must_include": _union(explicit.must_include, inferred.must_include, 20),
            "must_exclude": _union(explicit.must_exclude, inferred.must_exclude, 20),
            "deliverables": _union(explicit.deliverables, inferred.deliverables, 12),
            "ambiguities": _union(explicit.ambiguities, inferred.ambiguities, 12),
            "assumptions": _union(explicit.assumptions, inferred.assumptions, 12),
            "clarification_required": explicit.clarification_required or inferred.clarification_required,
        }
    )


def validate_research_intent(intent: ResearchIntent, question: str, current_year: int) -> ResearchIntent:
    """Normalize ranges and ensure the current request remains the authoritative scope."""
    start = intent.time_range.start_year
    end = intent.time_range.end_year
    if start and end and start > end:
        start, end = end, start
    if not intent.must_answer:
        must_answer = [" ".join(question.split())[:500]]
    else:
        must_answer = _union(intent.must_answer, [], 20)
    assumptions = list(intent.assumptions)
    if end and end > current_year + 1:
        assumptions = _union(assumptions, [f"用户指定的结束年份 {end} 晚于当前年份 {current_year}"], 12)
    return intent.model_copy(
        update={
            "normalized_question": " ".join(question.split()).strip(),
            "time_range": intent.time_range.model_copy(update={"start_year": start, "end_year": end}),
            "must_answer": must_answer,
            "assumptions": assumptions,
            "must_include": _without_conflicts(intent.must_include, intent.must_exclude),
        }
    )


@dataclass(frozen=True)
class TopicValidation:
    accepted: bool
    reason: str
    matched_terms: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()


class TopicValidator:
    """Deterministic domain/object/method boundary validator shared by planning and evidence gates."""

    def __init__(self, intent: ResearchIntent):
        self.intent = intent
        self.required_terms = _expand_controlled_terms(
            [*intent.domains, *intent.research_objects, *intent.methods, *intent.must_include]
        )
        self.excluded_terms = _expand_controlled_terms(intent.must_exclude)

    def validate(self, text: str, *, require_topic_match: bool = False) -> TopicValidation:
        matched = tuple(value for value in self.required_terms if contains_term(text, value))
        excluded = tuple(value for value in self.excluded_terms if contains_term(text, value))
        if excluded and not matched:
            return TopicValidation(
                False,
                "命中用户排除概念且未命中研究领域、对象或方法",
                matched,
                excluded,
            )
        if require_topic_match and self.required_terms and not matched:
            return TopicValidation(False, "未命中 ResearchIntent 的领域、对象或方法", matched, excluded)
        return TopicValidation(True, "符合 ResearchIntent 主题边界", matched, excluded)


def contains_term(text: str, term: str) -> bool:
    normalized_text = _normalize(text)
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    if _CJK_RE.search(normalized_term):
        return normalized_term.replace(" ", "") in normalized_text.replace(" ", "")
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", normalized_text) is not None


def _extract_time_range(text: str, years: list[int], current_year: int) -> ResearchTimeRange:
    range_match = re.search(
        r"((?:18|19|20|21)\d{2})\s*(?:年)?\s*(?:-|–|—|至|到|~|to|through)\s*"
        r"((?:18|19|20|21)\d{2})",
        text,
    )
    if range_match:
        start, end = (int(range_match.group(1)), int(range_match.group(2)))
        return ResearchTimeRange(
            start_year=min(start, end), end_year=max(start, end), description=range_match.group(0)
        )
    relative = re.search(r"(?:最近|近)\s*([一二三四五六七八九十\d]+)\s*年", text)
    if not relative:
        relative = re.search(r"(?:last|past)\s+(\d+)\s+years?", text, re.I)
    if relative:
        count = _number(relative.group(1))
        if count:
            return ResearchTimeRange(
                start_year=current_year - count + 1,
                end_year=current_year,
                description=relative.group(0),
            )
    if re.search(r"最近几年|近年来|recent years", text, re.I):
        return ResearchTimeRange(
            start_year=current_year - 4,
            end_year=current_year,
            description="最近五年（按运行日期规范化）",
        )
    since = re.search(r"(?:自|从|since|from)\s*((?:18|19|20|21)\d{2})(?:\s*年)?", text, re.I)
    if since:
        return ResearchTimeRange(
            start_year=int(since.group(1)), end_year=current_year, description=since.group(0)
        )
    until = re.search(r"(?:截至|到|before|until|through)\s*((?:18|19|20|21)\d{2})(?:\s*年)?", text, re.I)
    if until:
        return ResearchTimeRange(end_year=int(until.group(1)), description=until.group(0))
    if len(years) >= 2:
        return ResearchTimeRange(start_year=min(years), end_year=max(years), description="显式年份范围")
    if years:
        return ResearchTimeRange(start_year=years[0], end_year=years[0], description=f"{years[0]} 年")
    return ResearchTimeRange()


def _extract_languages(text: str) -> list[str]:
    lowered = text.casefold()
    values: list[str] = []
    if re.search(r"中英文|中文和英文|中文及英文|chinese\s+(?:and|&)\s+english", lowered):
        return ["zh", "en"]
    if re.search(r"中文(?:文献|资料|论文)|chinese-language|in chinese", lowered):
        values.append("zh")
    if re.search(
        r"英文(?:文献|资料|论文)|english-language|in english|english (?:papers|literature|studies) only",
        lowered,
    ):
        values.append("en")
    return values


def _extract_must_answer(text: str) -> list[str]:
    cleaned = re.sub(r"^(?:请|麻烦)?(?:帮我|给我)?(?:调研|研究|分析|查找|查询)(?:一下)?[，,：:\s]*", "", text)
    clauses = [
        value.strip(" ，,。；;：:")
        for value in re.split(r"[；;。]|(?:，|,)(?=(?:还|并|以及|同时|比较|分析|说明|列出|总结))", cleaned)
        if value.strip(" ，,。；;：:")
    ]
    requirements = [
        value
        for value in clauses
        if not re.search(
            r"^(?:形成|生成|输出|写成|provide|return).*(?:报告|综述|表|report|review|table)$", value, re.I
        )
    ]
    return _union(requirements or ([cleaned] if cleaned else []), [], 20)


def _infer_task_type(text: str) -> str:
    lowered = text.casefold()
    if any(value in lowered for value in ("复现", "可重复", "reproduc")):
        return "reproducibility"
    if any(value in lowered for value in ("比较", "对比", "差异", "compare", "comparison", "versus", " vs ")):
        return "comparison"
    if any(value in lowered for value in ("数据集", "数据来源", "dataset", "data source")):
        return "data_review"
    if any(value in lowered for value in ("方法", "算法", "流程", "method", "algorithm", "workflow")):
        return "method_review"
    if any(value in lowered for value in ("是否", "谁", "哪篇", "what is", "who ", "when ")):
        return "fact_lookup"
    return "literature_review"


def _expand_controlled_terms(values: list[str]) -> list[str]:
    vocabularies = (*_DOMAIN_TERMS, *_METHOD_TERMS, *_OBJECT_TERMS)
    expanded: list[str] = []
    for value in values:
        expanded.append(value)
        for canonical, aliases in vocabularies:
            family = (canonical, *aliases)
            if any(contains_term(value, member) or contains_term(member, value) for member in family):
                expanded.extend(family)
    return _union(expanded, [], 60)


def _controlled_matches(text: str, vocabulary: Iterable[tuple[str, tuple[str, ...]]]) -> list[str]:
    lowered = f" {text.casefold()} "
    return [
        canonical
        for canonical, aliases in vocabulary
        if any(alias.casefold() in lowered for alias in aliases)
    ]


def _pattern_values(text: str, patterns: Iterable[re.Pattern[str]]) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        values.extend(match.group(1).strip() for match in pattern.finditer(text))
    return _union(values, [], 20)


def _matching_fragments(text: str, patterns: Iterable[re.Pattern[str]]) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        values.extend(match.group(0).strip() for match in pattern.finditer(text))
    return _union(values, [], 20)


def _number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _NUMBER_WORDS:
        return _NUMBER_WORDS[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + _NUMBER_WORDS.get(value[1], 0)
    if value.endswith("十") and len(value) == 2:
        return _NUMBER_WORDS.get(value[0], 0) * 10
    if "十" in value:
        left, right = value.split("十", 1)
        return _NUMBER_WORDS.get(left, 1) * 10 + _NUMBER_WORDS.get(right, 0)
    return None


def _without_conflicts(included: list[str], excluded: list[str]) -> list[str]:
    excluded_normalized = {_normalize(value) for value in excluded}
    return [value for value in included if _normalize(value) not in excluded_normalized]


def _union(first: Iterable[str], second: Iterable[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in [*first, *second]:
        value = " ".join(str(raw).split()).strip(" ,，。；;：:")
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
