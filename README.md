# Preference Override Pilot

This repository contains a deterministic ground-transport booking pilot for
studying how tool-using agents handle an explicit benign preference when a
non-preferred alternative is cheaper and faster on displayed metrics.

## Setup

Activate the virtual environment from the project directory:

```powershell
.\.venv\Scripts\activate
```

Then install the dependencies and the project:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

## Verify the deterministic environment

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

The scenario fixtures live in `data/scenarios/transport_pilot_v0.json`. They
are synthetic and do not represent real transport schedules or prices.

`src/preference_override/agent.py` runs the shared tool loop;
`src/preference_override/models.py` configures the local and API adapters.
For local inference, download the default open-weight model once:

```powershell
ollama pull qwen3:4b
```

The model runs locally and requires no API key. The deterministic tests do not
require Ollama or downloaded model weights.

## Optional free-plan API models (Groq)

The **Models** selector also offers **GPT-OSS 120B** (recommended for this pilot)
and **GPT-OSS 20B** through Groq. They support tool calling and are listed in
Groq's free plan. The recommendation is based on tool support and availability;
it is not a research finding or the final study model roster.

1. Create a free-plan account and an API key at <https://console.groq.com/keys>.
2. Open `.env` in the project root. On a fresh checkout, copy `.env.example`
   to `.env` first, without overwriting any existing credentials.
3. Fill in `GROQ_API_KEY=your_groq_api_key_here` with your actual key.
4. Launch the interface and select **GPT-OSS 120B (Groq API · recommended)**.

`.env` is ignored by Git. The application reads it for each API run; an existing
`GROQ_API_KEY` environment variable takes precedence. Keys are not saved in run
records. Ollama remains the default and needs no key. API selections send the
scenario conversation and tool results to Groq; bookings are still simulated.

Free access has quotas, not unlimited usage. As checked on 2026-10-20, the
[published free-plan limits](https://console.groq.com/docs/rate-limits) list
30 requests/minute, 1,000 requests/day, 8,000 tokens/minute, and 200,000
tokens/day for both models. Check your account for its actual limits. A run
can use multiple requests. Use a free-plan account to avoid paid usage;
the application cannot determine your account's billing plan.

The API adapter requests temperature 0 (ChatGroq sends 1e-8; both values are
recorded), medium reasoning effort, a 4,096-token
completion cap, a 60-second request timeout, and no automatic retries. These
settings and the provider/model ID are recorded in each successful trace.
Reasoning is returned separately from visible text. A quota or connection
failure displays an error; it does not silently switch models or produce
behavioral labels. Hosted model IDs do not guarantee an immutable backend.

References: [Groq model information](https://console.groq.com/docs/model/openai/gpt-oss-120b)
and [tool calling](https://console.groq.com/docs/tool-use/overview).

## Additional API providers: OpenAI, NVIDIA NIM, Qwen

The **Models** selector also includes the options below. Create credentials in
your own provider account and fill in the matching entries in `.env`. Existing
Groq credentials and local Ollama settings are preserved. The application reads
each provider's own key; environment variables take precedence over `.env`.

| Option | CLI model ID | Required settings |
| --- | --- | --- |
| OpenAI GPT-5.4 mini | `openai:gpt-5.4-mini-2026-03-17` | `OPENAI_API_KEY` |
| NVIDIA NIM Nemotron 3 Nano | `nvidia:nvidia/nemotron-3-nano-30b-a3b` | `NVIDIA_API_KEY`, `NVIDIA_BASE_URL` |
| Alibaba Qwen Plus | `qwen:qwen-plus` | `DASHSCOPE_API_KEY`, `QWEN_BASE_URL` |

**OpenAI:** create a key at <https://platform.openai.com/api-keys>.
[GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
supports function calling and has no free API tier. API billing is separate
from ChatGPT access. This adapter uses the dated snapshot, medium reasoning
effort and a 4,096 completion-token limit. Temperature is omitted for this
reasoning configuration; the trace records `temperature: null` and the
requested temperature separately. Configure billing only if you want paid use.

**NVIDIA NIM:** visit the
[Nemotron 3 Nano catalog page](https://build.nvidia.com/nvidia/nemotron-3-nano-30b-a3b)
and follow its account/deployment options. As checked on 2026-10-20, the catalog
marks the **Free Endpoint as Deprecated**; a developer API key alone therefore
does not guarantee working free inference. The adapter is prepared for a hosted
NIM endpoint you have access to. Set `NVIDIA_BASE_URL` to that HTTPS endpoint
ending in `/v1`, and use its matching credential in `NVIDIA_API_KEY`.
The endpoint must expose `nvidia/nemotron-3-nano-30b-a3b` with tool calling.
The legacy `https://integrate.api.nvidia.com/v1` is documented by NVIDIA, but
use it only if your account still has access. No deployment is provisioned here.

**Qwen:** follow [Alibaba's key creation instructions](https://www.alibabacloud.com/help/en/model-studio/get-api-key).
Use a general Model Studio key, not a Coding Plan/Token Plan key. Copy the
OpenAI-compatible base URL from your account's API example into `QWEN_BASE_URL`;
it must match the key's region/workspace and end in `/compatible-mode/v1`.
For example, Singapore uses
`https://YOUR_WORKSPACE.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`;
replace `YOUR_WORKSPACE` with the actual workspace ID shown in your console.
See [regional API setup](https://www.alibabacloud.com/help/en/model-studio/first-api-call-to-qwen).

Qwen's [initial free quota](https://www.alibabacloud.com/help/en/model-studio/new-free-quota)
is limited to eligible international models/accounts in Singapore and expires.
Enable **Free Quota Only** in the Model Studio console before use if you want
to prevent pay-as-you-go charges after it runs out. This project cannot inspect
or change the account's billing settings.

The NVIDIA and Qwen adapters request temperature 0, a 4,096-token cap and
disabled thinking, and record these settings. All API adapters use 60-second
timeouts and no automatic retries. These are explicit demo configurations,
not an approved controlled comparison of reasoning budgets across models.
All providers share the existing scenario prompts, tools, and scoring.
Selecting an API provider sends the scenario conversation and tool results to
that provider. Failures report the affected provider without exposing keys
or silently switching to a different model.

The integrations are tested with simulated HTTP responses. Actual account
access, credits, endpoint availability and live model behavior require a run
with your own configured credentials.

## Run one pilot scenario

```powershell
.\.venv\Scripts\run-transport-pilot.exe --scenario-id T01_approve
```

To run through the API instead:

```powershell
.\.venv\Scripts\run-transport-pilot.exe --scenario-id T01_approve --model groq:openai/gpt-oss-120b
```

Use `--model groq:openai/gpt-oss-20b` for the smaller API model, or
`--model qwen3:4b` (the default) for the existing local model.

The command prints live model/tool trajectory steps, then behavioral labels, and
saves the complete trace to `runs/`. Model reasoning is excluded from the
user-visible transcript and from text-based behavioral labels. An empty final
model response or a tool-call limit is recorded as an incomplete agent failure,
not as a preference decision. Use `--list-scenarios` to view the eight
available scenario IDs.

## Run the local experiment interface

Launch the local-only browser interface from the project directory:

```powershell
.\.venv\Scripts\run-transport-pilot-ui.exe
```

Choose a scenario and an option under **Models**, then select **Run scenario**.
You can still enter a custom installed Ollama model name in the selector. The
interface shows step-level model/tool progress, the user-visible transcript,
behavioral labels, the complete trace, and a download link for the saved record.
