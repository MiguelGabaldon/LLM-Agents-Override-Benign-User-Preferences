# Approved project decisions

## 2026-10-12 — Pilot domain and environment

- The first pilot uses a deterministic **ground-transport booking** domain.
- It starts with one user, one tool-using agent, two options per scenario, and simulated bookings only.
- The preferred option is a train; the bus is non-preferred and strictly Pareto-dominant on displayed cost and duration.
- The pilot compares an exploratory preference framing with a habitual-preference framing. It does not claim these are a validated preference-strength scale.
- An agent may ask the user questions. Booking the bus requires explicit approval supplied through the simulated user interaction; booking the train is authorized by the initial request.
- LangChain is used for the model/tool-calling boundary. Scenario validation, state transitions, trace logging, and scoring remain project-local deterministic code.
- The initial evaluated model path uses local Ollama inference with `qwen3:4b`, an open-weight model. It replaces the earlier OpenAI API runner and requires no API key or per-call API charge.
