# Job Application Agent: v1 Design

## Purpose

Build a personal CLI agent that reduces repetitive work in applying to curated job opportunities. The project is also a learning environment for agentic workflows and current AI application technologies. The initial product is single-user, with boundaries that do not prevent future multi-user support, but it does not implement multi-tenancy.

## Product boundary

The user provides a list of curated job application URLs. The agent uses a structured, inspectable profile and resume files to complete applications on a small, explicitly supported set of ATS platforms. Job discovery, opportunity ranking, a graphical interface, and broad support for arbitrary websites are outside v1.

The CLI may submit applications autonomously when required information is present and validated. If the agent cannot determine an answer reliably, it defers that application and explains what is missing or ambiguous.

## Decision and generation responsibilities

- **Deterministic application code** handles known field mappings, supported ATS interactions, allowed-value checks, required-field validation, submission, and repeat-run protections.
- **Jev** is an experimental decision component for ambiguous structured questions, such as classifying a form question or selecting a likely profile-field mapping. Straightforward mappings should bypass model calls. Jev's typed output and confidence are not proof of correctness and do not independently authorize submission.
- **A generative LLM** may draft free-text answers from the user's profile, resume, and job description. It must not invent experience, credentials, metrics, or other biographical facts. Claims must be supported by supplied source material.

Jev should be evaluated against labeled application examples and compared with deterministic rules and/or a general LLM before its decisions influence live submissions. The project should preserve the ability to inspect disagreements and measure deferral behavior.

## Profile and sensitive information

The structured profile and resume files are the source of truth for personal application data. Sensitive or legally significant questions—including work authorization, sponsorship, disability, veteran status, and criminal history—may be answered only from an explicit profile value. If that value is absent, the agent must defer rather than infer.

## Submission conditions

The agent may submit only when:

1. The application is on a supported ATS flow.
2. Every required field has a validated answer grounded in the profile, resume, or permitted generated text.
3. Any model-produced structured decision has passed the workflow's evaluation and validation requirements; model confidence alone is insufficient.
4. Free-text content contains no unsupported factual claims.
5. The local processing history does not show that this application was already submitted.

If any condition fails, the agent must not submit. It should record a deferred result with a reason that identifies missing data, uncertainty, or unsupported flow.

## Processing history and repeat runs

The agent must keep local processing history sufficient to identify previously submitted applications and skip them on later runs. It should record the job URL, employer and role when available, processing status, submission time when submitted, and deferral or failure reason. The design should distinguish a confirmed submission from an attempt whose outcome is unknown, so a retry cannot silently create a duplicate application.

## Out of scope for v1

- Finding or ranking jobs.
- Applying to arbitrary websites or unsupported ATS platforms.
- A web dashboard or other graphical UI.
- Multi-user accounts, billing, or tenant administration.
- Automatically inferring missing sensitive or legally significant profile facts.
- Treating a model's self-reported confidence as sufficient evidence to submit.

## Success criteria

- A user can provide curated links and process supported applications from the CLI.
- Exact profile-backed fields are filled consistently and validated against form constraints.
- Ambiguous questions can be routed through a measurable Jev experiment.
- Free-text answers, when needed, are grounded in supplied source material.
- Missing or uncertain required answers lead to clear deferral rather than a guessed submission.
- Re-running a job list does not submit an application already recorded as submitted.
- Each processed link has an inspectable outcome: submitted, deferred, failed, or uncertain.

## Design assumptions and open implementation choices

- The first supported ATS platforms will be selected during implementation planning based on access, stability, and testability.
- The exact profile schema and local history format remain implementation decisions.
- Jev is accessed as an external service according to its current availability. Before sending personal resume or profile data, the implementation must account for that data leaving the local environment; initial Jev evaluation can use synthetic or redacted examples.
- The design does not prescribe a particular browser automation library, LLM provider, programming language, or storage engine.
