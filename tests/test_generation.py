import pytest

from jobapply.forms import FormQuestion
from jobapply.generation import DraftAnswer, SupportedClaim, TextGenerator
from jobapply.jev import ClaimSupport
from jobapply.policy import validate_and_render_answer
from jobapply.profile import EvidenceFact
from jobapply.settings import Settings


@pytest.fixture
def evidence():
    return [
        EvidenceFact(id="work.acme.role", value="Engineering manager", source="profile"),
        EvidenceFact(id="work.acme.team", value="Led a team of eight engineers", source="profile"),
        EvidenceFact(id="profile.email", value="private@example.test", source="profile"),
        EvidenceFact(id="profile.sensitive_answers.visa", value="H-1B", source="profile"),
        EvidenceFact(id="resume.page.1", value="Full resume with unrelated content", source="resume"),
    ]


@pytest.fixture
def question():
    return FormQuestion("q1", "Describe your team leadership experience", True, "text", [], 200)


def test_validated_claims_render_deterministically(evidence):
    draft = DraftAnswer(
        claims=[
            SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["work.acme.team"]),
            SupportedClaim(text="I worked as an engineering manager.", evidence_ids=["work.acme.role"]),
        ]
    )

    class FakeJev:
        settings = Settings(jev_min_confidence=0.98)

        def check_claim_support(self, question, claim, cited):
            return ClaimSupport("supported", 0.99, [fact["id"] for fact in cited])

    decision, rendered = validate_and_render_answer(
        draft, evidence, question=FormQuestion("q", "Describe leadership", True, "text", [], 200),
        jev_client=FakeJev(),
    )

    assert decision.action == "submit"
    assert rendered == "I led a team of eight engineers. I worked as an engineering manager."


def test_arbitrary_text_cannot_be_rendered_without_validation(evidence, question):
    draft = DraftAnswer(claims=[SupportedClaim(text="Unsupported claim", evidence_ids=["missing-id"])])

    decision, rendered = validate_and_render_answer(
        draft, evidence, question=question, jev_client=None
    )
    assert decision.action == "defer"
    assert rendered is None


def test_draft_with_unknown_evidence_id_is_rejected_before_jev(evidence):
    draft = DraftAnswer(
        claims=[SupportedClaim(text="I led a team", evidence_ids=["missing-id"])]
    )

    class UnreachableJev:
        def check_claim_support(self, *args):
            pytest.fail("unknown evidence reached Jev")

    result, rendered = validate_and_render_answer(
        draft, evidence, question=FormQuestion("q", "Leadership", True, "text", [], 200),
        jev_client=UnreachableJev(),
    )
    assert rendered is None
    assert "invalid_evidence_reference" in result.reason_codes


def test_blank_claim_text_is_rejected(evidence):
    draft = DraftAnswer(claims=[SupportedClaim(text="  ", evidence_ids=["work.acme.team"])])

    result, rendered = validate_and_render_answer(
        draft, evidence, question=FormQuestion("q", "Leadership", True, "text", [], 200),
        jev_client=None,
    )
    assert rendered is None


def test_answer_longer_than_question_limit_is_rejected(evidence):
    draft = DraftAnswer(
        claims=[SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["work.acme.team"])]
    )

    result, rendered = validate_and_render_answer(
        draft, evidence, question=FormQuestion("q", "Leadership", True, "text", [], 10),
        jev_client=None,
    )
    assert rendered is None


def test_question_max_length_is_enforced(evidence):
    draft = DraftAnswer(
        claims=[SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["work.acme.team"])]
    )
    result, rendered = validate_and_render_answer(
        draft,
        evidence,
        question=FormQuestion("q", "Leadership", True, "text", [], 10),
        jev_client=None,
    )

    assert rendered is None
    assert "answer_too_long" in result.reason_codes


def test_empty_draft_defers_for_absent_factual_answer(evidence):
    result, rendered = validate_and_render_answer(
        DraftAnswer(claims=[]), evidence,
        question=FormQuestion("q", "Leadership", True, "text", [], 200),
        jev_client=None,
    )
    assert rendered is None


def test_valid_structure_defers_when_support_is_missing_or_raises(evidence, question):
    draft = DraftAnswer(claims=[SupportedClaim(text="I led eight engineers.", evidence_ids=["work.acme.team"])])

    class RaisingJev:
        settings = Settings(jev_min_confidence=0.98)

        def check_claim_support(self, *args):
            raise RuntimeError("Jev unavailable")

    for jev in (None, RaisingJev()):
        result, rendered = validate_and_render_answer(draft, evidence, question=question, jev_client=jev)
        assert rendered is None
        assert "claim_support_unconfirmed" in result.reason_codes


def test_valid_structure_defers_when_jev_returns_unsupported_or_low_confidence(evidence, question):
    draft = DraftAnswer(claims=[SupportedClaim(text="I led eight engineers.", evidence_ids=["work.acme.team"])])

    class FakeJev:
        settings = Settings(jev_min_confidence=0.98)

        def __init__(self, status, confidence):
            self.status = status
            self.confidence = confidence

        def check_claim_support(self, _question, _claim, cited):
            return ClaimSupport(self.status, self.confidence, [fact["id"] for fact in cited])

    for jev in (FakeJev("unsupported", 0.99), FakeJev("supported", 0.97)):
        result, rendered = validate_and_render_answer(draft, evidence, question=question, jev_client=jev)
        assert rendered is None


def test_valid_structure_defers_when_jev_returns_no_support_result(evidence, question):
    draft = DraftAnswer(claims=[SupportedClaim(text="I led eight engineers.", evidence_ids=["work.acme.team"])])

    class EmptyResultJev:
        settings = Settings(jev_min_confidence=0.98)

        def check_claim_support(self, *_args):
            return None

    result, rendered = validate_and_render_answer(
        draft, evidence, question=question, jev_client=EmptyResultJev()
    )
    assert rendered is None
    assert "claim_support_unconfirmed" in result.reason_codes


def test_generation_sends_only_relevant_non_sensitive_fact_values(question, evidence):
    class FakeResponses:
        def parse(self, **kwargs):
            self.request = kwargs
            return type("Response", (), {"output_parsed": DraftAnswer(claims=[
                SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["work.acme.team"])
            ])})()

    class FakeClient:
        def __init__(self):
            self.responses = FakeResponses()

    client = FakeClient()
    generator = TextGenerator(Settings(openai_api_key="test", openai_model="test-model"), client=client)

    draft = generator.draft(question, evidence, "We need an experienced engineering team lead.")

    assert draft.claims[0].evidence_ids == ["work.acme.team"]
    assert client.responses.request["model"] == "test-model"
    assert client.responses.request["text_format"] is DraftAnswer
    user_content = client.responses.request["input"][1]["content"]
    assert "Led a team of eight engineers" in user_content
    assert "private@example.test" not in user_content
    assert "H-1B" not in user_content
    assert "Full resume with unrelated content" not in user_content


def test_failed_generation_request_defers(question, evidence):
    class FakeResponses:
        def parse(self, **kwargs):
            raise RuntimeError("offline")

    class FakeClient:
        responses = FakeResponses()

    generator = TextGenerator(Settings(openai_api_key="test", openai_model="test-model"), client=FakeClient())

    assert generator.draft(question, evidence, "Relevant job description").claims == []


def test_resume_evidence_is_sent_as_an_excerpt_not_as_a_complete_page(question):
    import json

    resume_text = "Led a team of eight engineers and delivered reliable hiring systems."

    class FakeResponses:
        def parse(self, **kwargs):
            self.request = kwargs
            return type("Response", (), {"output_parsed": DraftAnswer(claims=[
                SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["resume.page.1"])
            ])})()

    class FakeClient:
        def __init__(self):
            self.responses = FakeResponses()

    client = FakeClient()
    generator = TextGenerator(Settings(openai_api_key="test", openai_model="test-model"), client=client)

    generator.draft(
        question,
        [EvidenceFact(id="resume.page.1", value=resume_text, source="resume")],
        "Hiring team leadership experience",
    )

    sent = json.loads(client.responses.request["input"][1]["content"])["evidence"][0]["value"]
    assert sent != resume_text
