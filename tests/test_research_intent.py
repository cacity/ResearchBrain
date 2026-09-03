from researchbrain.orchestration.intent import (
    TopicValidator,
    extract_explicit_intent,
    merge_research_intent,
    validate_research_intent,
)
from researchbrain.orchestration.models import ResearchIntent, ResearchTimeRange


def test_explicit_intent_extracts_chinese_scope_without_a_model():
    intent = extract_explicit_intent(
        "请调研中国近五年的球谐分析方法，使用中英文文献，必须包括数据来源，"
        "排除多波束声呐，形成比较表和调研报告",
        2026,
    )

    assert intent.task_type == "comparison"
    assert intent.time_range.start_year == 2022
    assert intent.time_range.end_year == 2026
    assert intent.geography == ["中国"]
    assert intent.languages == ["zh", "en"]
    assert "球谐分析" in intent.methods
    assert intent.must_include == ["数据来源"]
    assert intent.must_exclude == ["多波束声呐"]
    assert intent.deliverables == ["调研报告", "比较表"]
    assert intent.must_answer


def test_explicit_intent_extracts_english_dates_language_and_exclusion():
    intent = extract_explicit_intent(
        "Review clinical studies in Europe from 2021 to 2024, English papers only, excluding animal studies",
        2026,
    )

    assert intent.time_range.start_year == 2021
    assert intent.time_range.end_year == 2024
    assert intent.geography == ["欧洲"]
    assert intent.languages == ["en"]
    assert intent.must_exclude == ["animal studies"]
    assert "医学" in intent.domains


def test_model_inference_cannot_override_explicit_user_constraints():
    explicit = ResearchIntent(
        normalized_question="只研究中国 2020-2024 年工作，排除声呐",
        time_range=ResearchTimeRange(start_year=2020, end_year=2024, description="2020-2024"),
        geography=["中国"],
        languages=["zh"],
        must_exclude=["声呐"],
        must_answer=["中国有哪些工作"],
    )
    inferred = ResearchIntent(
        normalized_question="global work",
        time_range=ResearchTimeRange(start_year=1990, end_year=2026),
        geography=["全球"],
        languages=["en"],
        must_exclude=[],
        must_answer=["What is known globally?"],
    )

    merged = validate_research_intent(
        merge_research_intent(explicit, inferred), explicit.normalized_question, 2026
    )

    assert merged.normalized_question == explicit.normalized_question
    assert merged.time_range.start_year == 2020
    assert merged.time_range.end_year == 2024
    assert merged.geography == ["中国"]
    assert merged.languages == ["zh"]
    assert merged.must_exclude == ["声呐"]
    assert merged.must_answer[0] == "中国有哪些工作"


def test_topic_validator_checks_object_method_and_exclusions_independently():
    validator = TopicValidator(
        ResearchIntent(
            normalized_question="球谐分析方法",
            methods=["球谐分析"],
            must_exclude=["多波束声呐"],
        )
    )

    accepted = validator.validate("A spherical harmonic analysis algorithm")
    rejected = validator.validate("Multibeam sonar data processing")

    # The controlled bilingual method is added by intake in a real run; explicit exclusions remain hard here.
    assert accepted.accepted is True
    assert rejected.accepted is False
    assert "multibeam sonar" in rejected.excluded_terms
