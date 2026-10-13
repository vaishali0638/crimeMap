"""Optional language-model adapter (spec "LLM/Copilot restrictions", ADR-037). Disabled by default.

No model is configured or approved, so the endpoint answers ``503 feature-disabled``. What is built
and tested here is the control path an approved adapter would have to pass through:

1. ``refusal(question)`` refuses prohibited questions (about people, suspects, risk scores,
   predictions about individuals, faces, enforcement or deployment decisions) before anything else.
2. ``redact(text)`` removes personal identifiers (phone, Aadhaar-like, PAN-like, vehicle
   registration, email) from anything sent to a model.
3. ``Context`` holds only data the caller is already authorised to see (built by the API from
   authorised queries), each item with a citation to an internal id.
4. ``Adapter`` has no tools: it can only return text, so it cannot write actions, change records,
   export data or approve decisions. Answers must cite context ids.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

# Questions an assistant must refuse, whatever the configuration (spec "Prohibited AI use cases").
# Deliberately broad: any question about a person, their identity or whereabouts, risk, faces, or
# enforcement and deployment is refused; the copilot answers about places, times and categories only.
_PEOPLE = (
    r"suspects?|accused|offenders?|culprits?|perpetrators?|criminals?|gangs?|individuals?|persons?|people|"
    r"m[ae]n|wom[ae]n|boys?|girls?|youths?|juveniles?|minors?|victims?|complainants?|witness(es)?|informants?|"
    r"families|family|neighbou?rs?|relatives?|names?|nicknames?|address(es)?|homes?|whereabouts|"
    r"who|whom|whose|someone|anyone|somebody"
)
_PROHIBITED = [
    (re.compile(rf"\b({_PEOPLE})\b", re.I), "people"),
    (re.compile(r"\b(lives?|living|resid(es|ing)) (at|in|near)\b", re.I), "people"),
    (
        re.compile(
            r"\b(reoffend\w*|recidiv\w*|risk scores?|risk of (him|her|them)|dangerous|criminality)\b",
            re.I,
        ),
        "individual risk",
    ),
    (re.compile(r"\b(face|facial)\s*(recognition|match\w*|id\w*)?\b", re.I), "facial recognition"),
    (
        re.compile(
            r"\b(arrest\w*|detain\w*|raid\w*|dispatch\w*|deploy\w*|patrols?|surveil\w*|track(ing)?|follow(ing)?|watch(ing)?)\b",
            re.I,
        ),
        "enforcement or deployment",
    ),
    (
        re.compile(r"\bsend\b.*\b(police|officers?|teams?|units?|force)\b", re.I),
        "enforcement or deployment",
    ),
    (re.compile(r"\b(predict|forecast)\w*\b", re.I), "predictions"),
]

_REDACTIONS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"),
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[id-number]"),  # Aadhaar-like, before phones
    (
        re.compile(r"(?<!\w)(?:\+?91[\s-]?)?(?:\d[\s-]?){9}\d(?!\w)"),
        "[phone]",
    ),  # 10 digits, any grouping
    (re.compile(r"\b0\d{2,4}[\s-]?\d{6,8}\b"), "[phone]"),  # landline with STD code
    (re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b"), "[id-number]"),  # PAN-like
    (re.compile(r"\b[A-Za-z]{3}\d{7}\b"), "[id-number]"),  # voter-ID-like
    (re.compile(r"\b[A-Za-z]{2}[\s-]?\d{1,2}[\s-]?[A-Za-z]{1,3}[\s-]?\d{3,4}\b"), "[vehicle]"),
]
# Names and free-text addresses cannot be removed reliably by patterns: an approved redaction
# service is a rollout gate of the copilot model ("redaction_tested").


def refusal(question: str) -> str | None:
    """The reason a question must be refused, or None."""
    for pattern, reason in _PROHIBITED:
        if pattern.search(question):
            return reason
    return None


def redact(value: str) -> str:
    for pattern, replacement in _REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


@dataclass(frozen=True)
class ContextItem:
    citation: str  # e.g. "insight:<uuid>", "snapshot:<uuid>", "query:<id>"
    text: str


@dataclass
class Context:
    """Authorised, redacted context for one question."""

    items: list[ContextItem] = field(default_factory=list)

    def add(self, citation: str, text: str) -> None:
        self.items.append(ContextItem(citation, redact(text)))


@dataclass(frozen=True)
class Answer:
    text: str
    citations: list[str]
    uncertainty: str


class Adapter(Protocol):
    """An approved model deployment. Text in, text out; no tools."""

    def answer(self, question: str, context: Context) -> Answer: ...


class NotConfigured(RuntimeError):
    pass


def answer(adapter: Adapter | None, question: str, context: Context) -> Answer:
    """Refuse, redact, ask, and check that the answer only cites the given context."""
    reason = refusal(question)
    if reason is not None:
        raise PermissionError(f"refused: questions about {reason} are not answered")
    if adapter is None:
        raise NotConfigured("no approved language model is configured")
    result = adapter.answer(redact(question), context)
    allowed = {item.citation for item in context.items}
    if not result.citations or not set(result.citations) <= allowed:
        raise ValueError("the answer must cite only the authorised context")
    return Answer(redact(result.text), result.citations, result.uncertainty)
