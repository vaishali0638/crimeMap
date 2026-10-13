"""Phase 7 pure functions: copilot guards and detector statistics."""

import math

import pytest
from crimemap_worker.ai import (
    block,
    duplicate_score,
    input_digest,
    normal_upper_tail,
    poisson_upper_tail,
    tail_label,
)

from crimemap_api.ai import copilot


@pytest.mark.parametrize(
    "question",
    [
        "Who is the likely suspect for the thefts?",
        "Which person will commit the next burglary?",
        "Give me a risk score for him",
        "Run facial recognition on this image",
        "Should we arrest the gang at the market?",
        "Deploy officers to sector 4 tonight",
        "Predict which individual reoffends",
        # Phase 7 audit: phrasings that passed the first guard
        "Which individuals are behind the thefts?",
        "Name the man who stole the bikes",
        "Who stole the phone?",
        "Where does the victim live?",
        "Give the home address of the complainant",
        "Rank persons by likelihood of reoffending",
        "Which people should we watch?",
        "Identify the youth responsible for the break-ins",
        "Send patrols to cell PDY:1000:1:2 tonight",
        "Forecast next week's burglaries",
    ],
)
def test_prohibited_questions_are_refused(question: str) -> None:
    assert copilot.refusal(question) is not None


@pytest.mark.parametrize(
    "question",
    [
        "Summarise property theft in PDY this month",
        "Which cells had the largest rise in vehicle theft?",
    ],
)
def test_place_and_time_questions_pass_the_guard(question: str) -> None:
    assert copilot.refusal(question) is None


@pytest.mark.parametrize(
    "secret",
    [
        "0413-2234567",
        "98765-432-10",
        "1234-5678-9012",
        "abcde1234f",
        "ABC1234567",
        "py01ab1234",
        "98765 43210",
    ],
)
def test_redaction_covers_grouped_and_lowercase_identifiers(secret: str) -> None:
    assert secret not in copilot.redact(f"details: {secret} end")


def test_redaction_removes_personal_identifiers() -> None:
    text = "Call 98765 43210 or +91-9876543210, mail a.b@example.org, Aadhaar 1234 5678 9012, PAN ABCDE1234F, car PY 01 AB 1234."
    out = copilot.redact(text)
    for secret in (
        "98765",
        "9876543210",
        "a.b@example.org",
        "1234 5678 9012",
        "ABCDE1234F",
        "PY 01 AB 1234",
    ):
        assert secret not in out
    assert "[phone]" in out and "[email]" in out and "[vehicle]" in out  # noqa: PT018


class _Echo:
    def __init__(self, citations: list[str]) -> None:
        self.citations = citations

    def answer(self, question: str, context: copilot.Context) -> copilot.Answer:
        return copilot.Answer(f"About {question}: phone 98765 43210", self.citations, "low")


def test_answers_need_an_adapter_citations_and_are_redacted() -> None:
    context = copilot.Context()
    context.add("insight:1", "Cell PDY:1000:1:2 rose; contact 98765 43210")
    assert "[phone]" in context.items[0].text
    with pytest.raises(copilot.NotConfigured):
        copilot.answer(None, "Summarise the rise", context)
    with pytest.raises(PermissionError):
        copilot.answer(_Echo(["insight:1"]), "Who is the suspect?", context)
    with pytest.raises(ValueError, match="cite"):
        copilot.answer(_Echo(["snapshot:other"]), "Summarise the rise", context)
    with pytest.raises(ValueError, match="cite"):
        copilot.answer(_Echo([]), "Summarise the rise", context)
    good = copilot.answer(_Echo(["insight:1"]), "Summarise the rise", context)
    assert good.citations == ["insight:1"]
    assert "98765" not in good.text


def test_poisson_upper_tail() -> None:
    assert poisson_upper_tail(0, 3.0) == 1.0
    assert math.isclose(poisson_upper_tail(1, 2.0), 1 - math.exp(-2.0))
    # P(X >= 23 | 3.5) is far below the 0.001 threshold; P(X >= 5 | 3.5) is not.
    assert poisson_upper_tail(23, 3.5) < 1e-10
    assert poisson_upper_tail(5, 3.5) > 0.1


def test_normal_tail_and_duplicate_score() -> None:
    assert math.isclose(normal_upper_tail(0), 0.5)
    assert normal_upper_tail(3) < 0.0014
    p = {"max_distance_m": 100, "max_gap_minutes": 120}
    assert duplicate_score(0, 0, True, True, p) == 1.0
    assert duplicate_score(100, 120, False, False, p) == 0.0
    assert duplicate_score(30, 10, True, True, p) == pytest.approx(0.855, abs=0.001)


def test_input_digest_ignores_row_order() -> None:
    assert input_digest([[1, 2], [3, 4]]) == input_digest([[3, 4], [1, 2]])
    assert len(input_digest([])) == 64


def test_poisson_tail_at_the_extremes() -> None:
    """Phase 7 audit: 0.0 at (40, 2) and 1.0 at (800, 760) from underflow."""
    tiny = poisson_upper_tail(40, 2.0)
    assert 0 < tiny < 1e-30
    assert 0.05 < poisson_upper_tail(800, 760.0) < 0.15
    assert math.isclose(poisson_upper_tail(3, 3.0), 1 - math.exp(-3) * (1 + 3 + 4.5), rel_tol=1e-9)


def test_labels_follow_the_tail_probability_and_patterns_alert_once_per_block() -> None:
    assert tail_label(normal_upper_tail(3.2)) == "LOW"
    assert tail_label(1e-5) == "MEDIUM"
    assert tail_label(1e-7) == "HIGH"
    from datetime import date

    assert block(date(2026, 10, 1)) == block(
        date(2026, 10, 1 + (27 - date(2026, 10, 1).toordinal() % 28))
    )
    assert block(date(2026, 10, 12)) != block(date(2026, 11, 12))
