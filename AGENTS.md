# NLP Research Project — Codex Instructions

## Project

**Title:** I Know Better Than You: When and Why LLM Agents Override Benign User Preferences.

This is an academic NLP research project at UIUC investigating how LLM agents behave when explicit, benign user preferences conflict with alternatives offering objectively measurable advantages.

The complete project knowledge base is in `docs/project_context.md`. Use it as the historical reference for research decisions, previous discussions, literature, methodology, and open questions. The proposal files are in `proposal/`.

## Essential research context

We investigate two conceptual dimensions:

1. **Preference adherence vs. selecting an objectively better alternative.**
2. **User dependence vs. agent autonomy.**

Our original behavioral taxonomy:

1. Follow the user's preference without mentioning alternatives.
2. Follow the preference while mentioning a better alternative.
3. Recommend the alternative but leave the decision to the user.
4. Autonomously select or execute the alternative.

A measurable objective advantage does not necessarily mean higher overall user utility.

Distinguish disclosure, recommendation, confirmation, action selection, and actual execution.

## Established methodological direction

- Focus on explicit, benign user preferences.
- Construct controlled preference–utility conflict scenarios.
- Characterize behavior rather than using only binary accuracy.
- Include tool-using agents.
- Begin with one small domain, then expand to multiple domains.
- Investigate adapting an existing tool-calling environment, particularly APOLLO, while creating new scenario data.

## Still unresolved

- Pilot domain and scenario schema.
- Exact preference-strength and objective-benefit manipulations.
- Operational definitions and scoring for the two evaluation dimensions.
- Confirmation and authorization handling.
- Environment/framework selection.
- Model roster, experimental sample sizes, and statistical analysis.

Do not silently decide these scientific questions.

## Technical workflow

- Use Python and the existing project-local `.venv`.
- Inspect existing files before making changes.
- Keep code modular, testable, and reproducible.
- Separate data generation, environments, agent execution, scoring, and analysis.
- Record experimental configurations and complete traces.
- Never commit API keys, `.env` files, or virtual environments.
- Write and run appropriate tests for implementation changes.

## Context efficiency

- Do not reread or summarize the entire `project_context.md` at the start of every task.
- Use this `AGENTS.md` as the default working context.
- Consult only relevant sections of `docs/project_context.md` for the current task.
- Read the complete document only when explicitly requested or when a major cross-cutting research decision requires it.
- Do not repeat historical background unless it matters to the current question.
- Prefer direct, actionable responses over long summaries.

## Decisions and communication

- Distinguish established decisions, proposals, and unresolved questions.
- Keep `docs/decisions.md` updated with decisions explicitly approved by the team.
- Before major architectural or methodological changes, propose alternatives and trade-offs.
- Do not implement new research assumptions without approval.
- Do not invent research findings, code, data, or paper citations.
- Report changed files, tests, and unresolved issues after coding tasks.

## Current phase

Repository and Python environment setup are underway or completed. We are now planning benchmark implementation. Do not implement the benchmark until specifically requested.