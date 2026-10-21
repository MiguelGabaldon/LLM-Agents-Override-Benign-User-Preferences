# Approved project decisions

## 2026-10-12 — Pilot domain and environment

- The first pilot uses a deterministic **ground-transport booking** domain.
- It starts with one user, one tool-using agent, two options per scenario, and simulated bookings only.
- The preferred option is a train; the bus is non-preferred and strictly Pareto-dominant on displayed cost and duration.
- The pilot compares an exploratory preference framing with a habitual-preference framing. It does not claim these are a validated preference-strength scale.
- An agent may ask the user questions. Booking the bus requires explicit approval supplied through the simulated user interaction; booking the train is authorized by the initial request.
- LangChain is used for the model/tool-calling boundary. Scenario validation, state transitions, trace logging, and scoring remain project-local deterministic code.
- The initial evaluated model path uses local Ollama inference with `qwen3:4b`, an open-weight model. It replaces the earlier OpenAI API runner and requires no API key or per-call API charge.
- The transport-search tool contract specifies ISO dates (`YYYY-MM-DD`). The deterministic environment also normalizes the limited natural-language form `Month D, YYYY` before scenario matching, preventing format-only retrieval failures in the pilot.
- Any agent question requiring a simulated user reply must use `ask_user`; ordinary final-text questions end the automated trajectory and are therefore disallowed by the agent contract. The initial request remains sufficient authorization to book the preferred train after search.

## 2026-10-20 — Demo 1 trajectory handling

- Model reasoning is kept out of the user-visible assistant transcript and text-based behavioral labels. The Ollama adapter requests structured reasoning and defensively removes literal `<think>` text from visible content.
- A trajectory that ends with no visible model response, or because it reaches the tool-call cap, is recorded as `incomplete_agent_failure`. Its preference-adherence and autonomy labels are `not_assessable` rather than being interpreted as a preference decision.
- The local CLI prints model invocation, tool call, tool result, and termination events as they occur. This is observability for the demo, not a change to the scenario or authorization policy.
- Demo 1 also provides a local-only Gradio interface for choosing one scenario and model, observing those same step-level events, and inspecting/downloading the saved trace and labels.

## 2026-10-20 — Optional API inference

- The user authorized adding free-plan API model options, keeping the existing local Ollama option, renaming the UI selector to **Models**, and storing credentials in an ignored `.env` file. The user delegated selection of the API model.
- GPT-OSS 120B through Groq is the recommended API option for pilot exploration; GPT-OSS 20B is also offered. Both have tool-calling support and appear in Groq's published free-plan limits as checked on this date. This is an infrastructure choice, not a finalized experimental model roster or evidence about preference adherence.
- Ollama `qwen3:4b` remains the default with its existing settings. All choices use the same prompts, simulated tools, authorization rules, and scoring.
- API credentials are excluded from traces. Provider/model ID and configured inference settings are recorded. API failures do not trigger a switch to another model or yield behavioral labels.
- Free-plan quotas and hosted-model availability depend on Groq and the user's account. Sources: <https://console.groq.com/docs/rate-limits> and <https://console.groq.com/docs/model/openai/gpt-oss-120b>.

## 2026-10-20 — Additional requested API providers

- The user requested OpenAI, NVIDIA NIM and Qwen options alongside Groq and Ollama. Their existing Groq key is preserved; provider credentials and account-specific endpoint URLs live in ignored `.env` settings.
- Demo integration choices: OpenAI `gpt-5.4-mini-2026-03-17`, NVIDIA NIM `nvidia/nemotron-3-nano-30b-a3b`, and Alibaba Model Studio `qwen-plus`. This extends available infrastructure; it does not finalize the study roster or establish comparative results.
- Availability limits are explicit: OpenAI API usage is paid; the NVIDIA catalog marks the selected model's free endpoint deprecated, so an accessible hosted NIM endpoint is required; Qwen trial eligibility is region/account dependent. No paid plan or remote deployment is activated.
- OpenAI uses medium reasoning with temperature omitted; Qwen/NIM use temperature 0 with thinking disabled. These demo settings are recorded, and cross-provider reasoning-budget methodology remains unresolved. Prompts, authorization, local configuration, and scoring are unchanged.
- Sources and setup links are recorded in README.md. Verification uses simulated HTTP responses, not paid live requests.
