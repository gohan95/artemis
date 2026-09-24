import pytest

from jobapply.forms import FormQuestion
from jobapply.generation import DraftAnswer, SupportedClaim, TextGenerator
from jobapply.policy import validate_draft
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


def test_valid_claim_references_render_deterministically(evidence):
    from jobapply.generation import render_answer

    draft = DraftAnswer(
        claims=[
            SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["work.acme.team"]),
            SupportedClaim(text="I worked as an engineering manager.", evidence_ids=["work.acme.role"]),
        ]
    )

    decision = validate_draft(draft, evidence, max_length=200)

    assert decision.action == "submit"
    assert render_answer(draft) == (
        "I led a team of eight engineers. I worked as an engineering manager."
    )


def test_draft_with_unknown_evidence_id_is_rejected(evidence):
    draft = DraftAnswer(
        claims=[SupportedClaim(text="I led a team", evidence_ids=["missing-id"])]
    )

    assert validate_draft(draft, evidence, max_length=200).action == "defer"


def test_blank_claim_text_is_rejected(evidence):
    draft = DraftAnswer(claims=[SupportedClaim(text="  ", evidence_ids=["work.acme.team"])])

    assert validate_draft(draft, evidence, max_length=200).action == "defer"


def test_answer_longer_than_question_limit_is_rejected(evidence):
    draft = DraftAnswer(
        claims=[SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["work.acme.team"])]
    )

    assert validate_draft(draft, evidence, max_length=10).action == "defer"


def test_empty_draft_defers_for_absent_factual_answer(evidence):
    assert validate_draft(DraftAnswer(claims=[]), evidence, max_length=200).action == "defer"


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
