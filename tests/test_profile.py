from pathlib import Path

import pypdf
import pytest

from jobapply.profile import load_evidence, load_profile


@pytest.fixture
def empty_pdf_reader(monkeypatch):
    class Reader:
        pages = []

    monkeypatch.setattr(pypdf, "PdfReader", lambda path: Reader())


def test_loads_profile_and_builds_stable_string_evidence(tmp_path, empty_pdf_reader):
    resume = tmp_path / "resume.pdf"
    resume.touch()
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text(
        """full_name: Ada Example
email: ada@example.test
resume_path: resume.pdf
work_history:
  - id: acme
    title: Engineer
    achievements: [Built tools, Improved reliability]
education:
  - id: state-u
    degree: BS Computer Science
skills: [Python, SQL]
preferences:
  locations: [Remote, Oakland]
sensitive_answers:
  work_authorization: authorized
""",
        encoding="utf-8",
    )

    profile = load_profile(profile_yaml)
    evidence = load_evidence(profile)

    assert profile.resume_path == resume
    assert [(fact.id, fact.value, fact.source) for fact in evidence] == [
        ("profile.full_name", "Ada Example", "profile"),
        ("profile.email", "ada@example.test", "profile"),
        (
            "profile.work_history.acme.achievements",
            "Built tools; Improved reliability",
            "profile",
        ),
        ("profile.work_history.acme.id", "acme", "profile"),
        ("profile.work_history.acme.title", "Engineer", "profile"),
        (
            "profile.education.state-u.degree",
            "BS Computer Science",
            "profile",
        ),
        ("profile.education.state-u.id", "state-u", "profile"),
        ("profile.skills", "Python; SQL", "profile"),
        ("profile.preferences.locations", "Remote; Oakland", "profile"),
        (
            "profile.sensitive_answers.work_authorization",
            "authorized",
            "profile",
        ),
    ]


def test_missing_required_resume_path_is_actionable(tmp_path):
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text("full_name: Ada Example\n", encoding="utf-8")

    with pytest.raises(ValueError, match="resume_path"):
        load_profile(profile_yaml)


def test_unknown_profile_key_is_rejected_instead_of_silently_dropped(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.touch()
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text(
        "resume_path: resume.pdf\nfullname: Ada Example\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="fullname"):
        load_profile(profile_yaml)


def test_malformed_yaml_is_actionable(tmp_path):
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text("full_name: [unterminated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unable to parse YAML profile"):
        load_profile(profile_yaml)


def test_missing_sensitive_answer_stays_missing(tmp_path, empty_pdf_reader):
    resume = tmp_path / "resume.pdf"
    resume.touch()
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text("resume_path: resume.pdf\n", encoding="utf-8")

    profile = load_profile(profile_yaml)
    evidence = load_evidence(profile)

    assert profile.sensitive_answers.get("work_authorization") is None
    assert not any("sensitive_answers.work_authorization" == item.id for item in evidence)


def test_extracts_pdf_pages_from_resolved_resume_path(tmp_path, monkeypatch):
    resume = tmp_path / "resume.pdf"
    resume.touch()
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text("resume_path: resume.pdf\n", encoding="utf-8")
    opened = []

    class Page:
        def extract_text(self):
            return "Experience at Acme"

    def fake_pdf_reader(path):
        opened.append(Path(path))
        return type("Reader", (), {"pages": [Page()]})()

    monkeypatch.setattr(pypdf, "PdfReader", fake_pdf_reader)

    facts = load_evidence(load_profile(profile_yaml))

    assert opened == [resume]
    assert [(fact.id, fact.value, fact.source) for fact in facts[-1:]] == [
        ("resume.page.1", "Experience at Acme", "resume")
    ]


def test_missing_resume_file_is_actionable(tmp_path):
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text("resume_path: missing.pdf\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="missing.pdf"):
        load_profile(profile_yaml)


def test_duplicate_work_history_ids_are_rejected(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.touch()
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text(
        "resume_path: resume.pdf\nwork_history:\n  - id: same\n  - id: same\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="work_history entry IDs must be unique"):
        load_profile(profile_yaml)


def test_malformed_resume_pdf_is_actionable(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_text("not a PDF", encoding="utf-8")
    profile_yaml = tmp_path / "profile.yaml"
    profile_yaml.write_text("resume_path: resume.pdf\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unable to extract text from resume PDF"):
        load_evidence(load_profile(profile_yaml))
