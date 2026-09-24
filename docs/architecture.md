# Architecture

C4 diagrams for artemis: system context, then component-level detail for the
two halves of the codebase. See `CLAUDE.md` for the code tour; this is the
picture to navigate from.

## System Context

```mermaid
C4Context
    Person(user, "You", "Runs the CLI against a curated list of job URLs")

    System(artemis, "artemis", "CLI that fills and submits job applications")

    System_Ext(ats, "ATS sites", "Greenhouse, Lever, Ashby application pages")
    System_Ext(chrome, "Google Chrome", "Real browser, driven via patchright")

    Rel(user, artemis, "Runs, answers prompts, reviews receipts")
    Rel(artemis, chrome, "Launches, drives via CDP")
    Rel(chrome, ats, "Loads pages, submits forms")
```

Everything lives in one local process. There's no server, no database
beyond a local SQLite file, and no network calls except the browser loading
real ATS pages.

## Components: request flow

`cli.py` and `pipeline.py` are the core; everything else is a dependency it
calls into per URL. See `pipeline.py`'s `ApplicationPipeline._process_url`
for the exact sequence.

```mermaid
C4Component
    Container_Boundary(artemis, "artemis") {
        Component(cli, "cli.py", "Typer", "Commands: apply, history, validate-profile")
        Component(pipeline, "pipeline.py", "ApplicationPipeline", "Per-URL state machine: claim, read, resolve, fill, submit")
        Component(answers, "answers.py", "resolve_question", "Resolves one field: profile alias, sensitive check, learned cache, or ask the user")
        Component(mapping, "mapping.py", "aliases + patterns", "Exact-match field aliases; sensitive-question detection")
        Component(profile, "profile.py", "Profile", "Loads/validates data/profile.yaml")
        Component(store, "answers_store.py", "LearnedAnswers", "YAML cache of past live-prompt answers")
        Component(adapters, "ats/*.py", "BaseATSAdapter + 3 subclasses", "Per-vendor: read questions, fill fields, submit, confirm")
        Component(history, "history.py", "HistoryStore", "SQLite: claim/finish, dedup, status")
    }

    Rel(cli, pipeline, "constructs, runs")
    Rel(pipeline, answers, "resolve_question per field")
    Rel(answers, mapping, "alias/sensitivity lookup")
    Rel(answers, profile, "read profile fields")
    Rel(answers, store, "read/write learned answers")
    Rel(pipeline, adapters, "read_questions, fill, submit")
    Rel(pipeline, history, "claim, finish")
```

## Components: browser execution layer

`pipeline.py` calls `ats/base.py` methods with a `page` object; where that
page comes from, and how it behaves, is this layer. `cli.py` wires it
together and is the only caller.

```mermaid
C4Component
    Container_Boundary(artemis, "artemis") {
        Component(cli, "cli.py", "Typer", "Builds the session, wires adapters + hooks")
        Component(browser, "browser.py", "open_session", "Launches patchright, persistent Chrome profile, fingerprint")
        Component(pacing, "pacing.py", "Pacer / NullPacer / HumanPacer", "Optional typing/click pacing, injected into adapters")
        Component(adapters, "ats/base.py", "BaseATSAdapter", "Calls self.pacer.* around every fill/click")
        Component(receipts, "receipts.py", "capture_receipt", "Screenshot + JSON sidecar, wired via OnFilled")
    }

    System_Ext(chrome, "Google Chrome", "via patchright")

    Rel(cli, browser, "open_session(options)")
    Rel(browser, chrome, "launch_persistent_context")
    Rel(cli, adapters, "constructs with pacer=")
    Rel(adapters, pacing, "before_click, type_text, think, ...")
    Rel(cli, receipts, "on_filled hook")
```

## Navigating from here

- Follow a real run: `cli.py:apply` → `pipeline.py:ApplicationPipeline._process_url`.
- Add a new ATS vendor: `ats/greenhouse.py` is the shortest example to copy.
- Change how a field gets answered: `answers.py:resolve_question`.
- Change browser/stealth behavior: `browser.py`, `pacing.py`.
