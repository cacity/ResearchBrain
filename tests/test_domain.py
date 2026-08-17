import pytest

from researchbrain.domain import normalize_doi


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1029/2025GL116964", "10.1029/2025gl116964"),
        ("https://doi.org/10.1000/Test.", "10.1000/test"),
        ("doi: 10.1000/example(1)", "10.1000/example(1)"),
    ],
)
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


def test_normalize_doi_rejects_invalid():
    with pytest.raises(ValueError):
        normalize_doi("not-a-doi")
