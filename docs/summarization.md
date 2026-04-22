# Summarization setup

When scholarposter finds a link in a toot, it fetches the page, extracts the main
text, and generates a single-sentence summary (~150 characters) placed in the link
card description field on Bluesky and LinkedIn, not appended to the post text. Four
backends are available, tried in fallback order until one succeeds:

```
gemini → lemonade → ollama → extractive
```

Set your preferred starting backend in `config.toml`:

```toml
[enrichment.summarization]
backend = "lemonade"   # "gemini", "lemonade", "ollama", or "extractive"
```

If the preferred backend fails (process not found, timeout, API error), the next
one in the chain is tried automatically. Fallback only moves toward simpler
backends — no wrap-around. If all fail, the card omits the summary; the post still
goes through.

## How summaries appear

### Card placement

The summary populates the link card description visible below the title. The original Mastodon post provides context in the main text body. When a post has media (images), the embed slot prioritizes images, and the link card drops the summary.

### Three-tiered description priority

The `card_description` property on each enriched link resolves via a three-tiered priority:

1. **DOI-enriched links** (any type): Crossref abstract → AI summary → OG description → empty
2. **File links** (PDFs, documents): AI summary → OG description → empty
3. **Web pages** (HTML): OG description → AI summary → empty

The `card_title` resolves similarly: Crossref title → OG/extracted title → empty. **LinkedIn only:** when the title is empty, scholarposter falls back to the link's domain name (e.g., `doi.org`) to satisfy LinkedIn's required title field.

The pipeline sanitizes all card text before display. It applies NFC Unicode normalization, strips control characters and bidi overrides, and hard-truncates at 150 graphemes (description) or 70 graphemes (title).

### LinkedIn thumbnail requirement

LinkedIn requires a thumbnail image for every article card. scholarposter uploads
`link.thumbnail_bytes` (fetched during enrichment) to the LinkedIn Images API and
uses the resulting `urn:li:image:…` as the card thumbnail.

If the enrichment pipeline produced no thumbnail (e.g. the page has no OG image),
or if `media.enabled = false` in config, the post fails immediately with a clear
error before reaching the LinkedIn API. The failure is recorded in state and visible
under `scholarposter status` as a recent failure.

### Link selection

scholarposter selects the most enriched link for the card when a toot contains multiple URLs:

- DOI-resolved = rank 4 (highest)
- File type (PDF/doc) = rank 3
- HTML with OG metadata = rank 2
- Bare link = rank 1

First appearance in the text breaks any ties. LinkedIn selects the single highest-ranked link for the post. Bluesky uses per-chunk selection (see next section).

### Bluesky threading

scholarposter splits a toot into a thread when it exceeds 300 graphemes:

- Each chunk embeds the most-enriched link from its text
- The first chunk prefers images when media is present (no link card in chunk 1)

**Promotion rule**: If images consume chunk 1's embed slot, scholarposter promotes the post's most-enriched link to chunk 2 even if that URL does not appear in chunk 2's text.

## Extractive (no setup required)

The extractive backend uses [sumy](https://github.com/miso-belica/sumy)'s
KL-divergence and LSA algorithms to select the most representative sentences from
the article. It is always available and requires no external services.

Quality: sentences are extracted verbatim — accurate but mechanical. Sufficient for
factual academic abstracts; less suitable for opinion pieces or articles that require
paraphrase.

To use extractive exclusively:

```toml
[enrichment.summarization]
enabled = true
backend = "extractive"

[enrichment.summarization.extractive]
max_sentences = 5
```

---

## Lemonade (local LLM — preferred)

[Lemonade](https://lemonade.ai) provides local LLM inference with an
OpenAI-compatible API. It is preferred over Ollama because it uses the standard
`/v1/chat/completions` endpoint with system/user message roles for better
instruction following.

### Install

Install Lemonade from https://lemonade.ai or via your package manager. Start the
server:

```bash
lemonade status     # check if running
lemonade list       # see available models
```

### Pull a model

```bash
lemonade pull Phi-4-mini-instruct-GGUF
# or for higher quality (GPU recommended):
lemonade pull Qwen3-8B-GGUF
```

### Configure

```toml
[enrichment.summarization]
backend = "lemonade"

[enrichment.summarization.lemonade]
model = ""                              # auto-detect from server
host = "http://127.0.0.1:8000"
timeout_seconds = 60                    # higher for cold starts
```

When `model` is empty, scholarposter automatically loads the best available model.

### Auto-loading models

When no model is loaded on the server:

1. Queries `lemonade list --downloaded` for available models
2. Picks the first match from `preferred_models` (or first downloaded if no match)
3. Loads it with `lemonade load <model> --ctx-size <ctx_size>`
4. Caches the model ID for subsequent calls (no redundant loading)

The default `preferred_models` list is ordered CPU-first: smaller instruction-tuned
models (3–4B) come first for fast CPU inference, with larger 8B models as options
when GPU is available.

```toml
[enrichment.summarization.lemonade]
ctx_size = 8192
load_timeout_seconds = 180
preferred_models = [
    "Phi-4-mini-instruct-GGUF",         # 3.8B — best quality/size for CPU
    "Qwen3-4B-Instruct-2507-GGUF",      # 4B — strongest fine-tuned performance
    "Qwen3-8B-GGUF",                    # 8B — GPU recommended
    "DeepSeek-Qwen3-8B-GGUF",           # 8B — GPU recommended
    "Llama-3.2-3B-Instruct-GGUF",       # 3B — lightweight fallback
    "Gemma-3-4b-it-GGUF",               # 4B — solid all-rounder
    "Qwen3-1.7B-GGUF",                  # 1.7B — ultra-light
    "Llama-3.2-1B-Instruct-GGUF",       # 1B — minimal hardware
]
```

### Choosing a model

The first downloaded model in `preferred_models` is auto-loaded. The default ranking
is based on cross-referencing benchmark results from
[MLCommons MLPerf](https://mlcommons.org/2025/09/small-llm-inference-5-1/),
[DistilLabs 12-SLM benchmark](https://www.distillabs.ai/blog/we-benchmarked-12-small-language-models-across-8-tasks-to-find-the-best-base-model-for-fine-tuning/),
and [HuggingFace model evaluations](https://huggingface.co/microsoft/Phi-4-mini-instruct),
prioritizing instruction-following quality and CPU inference speed.

| Tier | Model | Params | RAM (Q4_K_M) | Best for |
|------|-------|--------|--------------|----------|
| 1 (CPU) | Phi-4-mini-instruct-GGUF | 3.8B | ~2.5 GB | Best quality/size — beats 6–9B models on accuracy |
| 1 (CPU) | Qwen3-4B-Instruct-2507-GGUF | 4B | ~2.8 GB | #1 in fine-tuned benchmarks, strong multilingual |
| 2 (GPU) | Qwen3-8B-GGUF | 8B | ~5 GB | Strongest instruction-following at this tier |
| 2 (GPU) | DeepSeek-Qwen3-8B-GGUF | 8B | ~5 GB | DeepSeek distillation quality |
| 3 | Llama-3.2-3B-Instruct-GGUF | 3B | ~2 GB | 128K context, good instruction following |
| 3 | Gemma-3-4b-it-GGUF | 4B | ~2.8 GB | Solid all-rounder |
| 4 | Qwen3-1.7B-GGUF | 1.7B | ~1.2 GB | Rivals vintage 7B models |
| 4 | Llama-3.2-1B-Instruct-GGUF | 1B | ~1.5 GB | Minimal hardware, still usable |

**CPU-first rationale.** scholarposter runs as an unattended cron job. GPU may not be
available or may be dedicated to other workloads. Lemonade's llamacpp backend
auto-selects CPU/GPU at runtime, but 3–4B models are fast on CPU while producing
summaries nearly indistinguishable from larger models — summarization degrades less
under quantization than code generation.

**If you have a GPU:** Pull an 8B model (`lemonade pull Qwen3-8B-GGUF`) and it will
be used automatically. Reorder `preferred_models` in config to prioritize it.

**Quantization:** All models use Q4_K_M quantization, the community consensus for
best quality/speed tradeoff on CPU — retains 90–95% of full-precision quality.

**Context window:** Default `ctx_size = 8192` is sufficient for most academic papers
(prompt ~50 tokens + article 2000–6000 tokens + output 200 tokens). For very long
papers, increase to `32768` (requires more RAM/VRAM).

### Available models

```bash
lemonade list               # all available (Downloaded column shows Yes/No)
lemonade list --downloaded  # only downloaded
curl http://127.0.0.1:8000/v1/models  # currently loaded
```

---

## Gemini (cloud)

scholarposter communicates with the Gemini CLI via the Agent Client Protocol (ACP) —
a structured JSON-RPC 2.0 interface over stdio, provided by the
[gemini-acp](https://github.com/davdittrich/gemini-acp) shared package.

### Install

Install the Gemini CLI and authenticate:

```bash
# Install the Gemini CLI binary (requires 0.36.0+):
# https://ai.google.dev/gemini-api/docs/downloads
# Verify: gemini --version

# The ACP client library is installed automatically with scholarposter
```

Verify the CLI is on your PATH:

```bash
which gemini
gemini --version
```

### Authenticate

```bash
gemini auth login
```

Follow the browser prompt to authorize with your Google account.

### Verify

```bash
echo "The sky is blue because of Rayleigh scattering." | gemini -p "Summarize in one sentence."
```

### Configure in `config.toml`

```toml
[enrichment.summarization]
backend = "gemini"

[enrichment.summarization.gemini]
model = "gemini-3-flash-preview"   # fast and cheap; or "" for CLI default
timeout_seconds = 30
```

### Model selection

| Model | Speed | Cost | Best for |
|-------|-------|------|----------|
| `gemini-3-flash-preview` | Fast | Low | Summarization, triage |
| `gemini-3.1-pro-preview` | Slower | Higher | Complex analysis |
| `""` (empty) | CLI default | Varies | Use whatever CLI is configured with |

### Graceful degradation

If the ACP library is not installed or the `gemini` binary is not on PATH, the
Gemini backend silently returns `None` and the fallback chain continues to Lemonade,
then Ollama, then extractive. No crash — just a log message at DEBUG level.

---

## Ollama (local LLM)

[Ollama](https://ollama.ai) runs a local inference server. scholarposter calls it
via HTTP at `/api/generate`.

### Install

```bash
curl -fsSL https://ollama.ai/install.sh | sh
systemctl enable --now ollama
```

### Pull a model

| Model | Pull command | RAM needed | Best for |
|-------|-------------|-----------|----------|
| `gemma3:9b` | `ollama pull gemma3:9b` | ~6 GB | Default; strong instruction following |
| `phi4:3.8b` | `ollama pull phi4:3.8b` | ~3 GB | Lightweight; for limited RAM |

### Verify

```bash
curl http://localhost:11434/api/generate \
  -d '{"model":"gemma3:9b","prompt":"hello","stream":false}'
```

### Configure in `config.toml`

```toml
[enrichment.summarization]
backend = "ollama"

[enrichment.summarization.ollama]
model = "gemma3:9b"
host = "http://localhost:11434"
timeout_seconds = 30
```

If Ollama runs on a different machine, change `host` to that machine's address and
ensure port 11434 is reachable.

---

## Custom prompt

The summarization prompt applies to all LLM backends (Gemini, Lemonade, Ollama).
It is sent as the system prompt; the article text follows as the user input.

```toml
[enrichment.summarization]
prompt = "In one sentence, describe the main finding of this research for a non-specialist audience."
max_chars = 150
```

The extractive backend ignores the prompt — it uses statistical sentence selection
rather than generative text.

---

## Cron PATH fix

Cron runs with a minimal PATH. If `gemini` or `lemonade` is not in `/usr/bin` or
`/bin`, cron will not find it. Add a PATH line at the top of your crontab:

```
PATH=/usr/local/bin:/usr/bin:/bin:/home/user/.local/bin
```

Replace the last entry with the directory containing your `gemini` or `lemonade`
binary.
