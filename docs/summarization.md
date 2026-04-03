# Summarization setup

When scholarposter finds a link in a toot, it fetches the page, extracts the main text, and generates a 2-3 sentence summary to attach to the cross-post. Three backends are supported, tried in order until one succeeds:

```
gemini → ollama → extractive
```

Set your preferred backend in `config.toml`:

```toml
[enrichment.summarization]
backend = "gemini"   # "gemini", "ollama", or "extractive"
```

If the preferred backend fails (process not found, timeout, API error), the next one in the chain is tried automatically. If all fail, no summary is attached — the post still goes through.

---

## Extractive (no setup required)

The extractive backend uses [sumy](https://github.com/miso-belica/sumy)'s KL-divergence algorithm to pick the most representative sentences from the article. It is always available and requires no external services.

Quality: sentences are extracted verbatim — accurate but mechanical. Good enough for factual academic abstracts; less suitable for opinion pieces.

To use extractive exclusively:

```toml
[enrichment.summarization]
enabled = true
backend = "extractive"

[enrichment.summarization.extractive]
max_sentences = 5
```

---

## Gemini CLI

scholarposter communicates with the Gemini CLI via the Agent Client Protocol (ACP) —
a structured JSON-RPC interface that replaces raw subprocess invocation.

### Install

Install the Gemini CLI and the ACP client library:

```bash
# Gemini CLI
pip install google-generativeai
# or follow the official install instructions at https://ai.google.dev/gemini-api/docs/downloads

# ACP client library (required for Gemini summarization)
pip install scholarposter[gemini]
# or: pip install agent-client-protocol>=0.9.0
```

Verify the CLI is on your PATH:

```bash
which gemini
gemini --version   # requires 0.34.0+
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
prompt = "Summarize this academic paper/article in 2-3 sentences for a social media post. Focus on the key finding and methodology. Be concise and precise."

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
then Ollama, then extractive. No crash, no error — just a log message at DEBUG level.

---

## Lemonade (local LLM — preferred over Ollama)

[Lemonade](https://lemonade.ai) provides local LLM inference with an OpenAI-compatible
API. It is preferred over Ollama because it uses the standard `/v1/chat/completions`
endpoint with system/user message roles for better instruction following.

### Install

Install Lemonade from https://lemonade.ai or via your package manager. Start the server:

```bash
lemonade status     # check if running
lemonade list       # see available models
```

### Pull a model

```bash
lemonade pull Phi-4-mini-instruct-GGUF
# or for higher quality:
lemonade pull DeepSeek-Qwen3-8B-GGUF
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
models (3-4B) come first for fast CPU inference, with larger 8B models as options
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
is based on cross-referencing benchmark results from [MLCommons MLPerf](https://mlcommons.org/2025/09/small-llm-inference-5-1/),
[DistilLabs 12-SLM benchmark](https://www.distillabs.ai/blog/we-benchmarked-12-small-language-models-across-8-tasks-to-find-the-best-base-model-for-fine-tuning/),
and [HuggingFace model evaluations](https://huggingface.co/microsoft/Phi-4-mini-instruct),
prioritizing instruction-following quality and CPU inference speed.

| Tier | Model | Params | RAM (Q4_K_M) | Best for |
|------|-------|--------|--------------|----------|
| 1 (CPU) | Phi-4-mini-instruct-GGUF | 3.8B | ~2.5 GB | Best quality/size — beats 6-9B models on accuracy |
| 1 (CPU) | Qwen3-4B-Instruct-2507-GGUF | 4B | ~2.8 GB | #1 in fine-tuned benchmarks, strong multilingual |
| 2 (GPU) | Qwen3-8B-GGUF | 8B | ~5 GB | Strongest instruction-following at this tier |
| 2 (GPU) | DeepSeek-Qwen3-8B-GGUF | 8B | ~5 GB | DeepSeek distillation quality |
| 3 | Llama-3.2-3B-Instruct-GGUF | 3B | ~2 GB | 128K context, good instruction following |
| 3 | Gemma-3-4b-it-GGUF | 4B | ~2.8 GB | Solid all-rounder |
| 4 | Qwen3-1.7B-GGUF | 1.7B | ~1.2 GB | Rivals vintage 7B models |
| 4 | Llama-3.2-1B-Instruct-GGUF | 1B | ~1.5 GB | Minimal hardware, still usable |

**CPU-first rationale:** scholarposter runs as an unattended cron job. GPU may not be
available or dedicated. Lemonade's llamacpp backend auto-selects CPU/GPU, but 3-4B
models are fast on CPU-only while producing summaries nearly indistinguishable from
larger models (summarization degrades less under quantization than code generation).

**If you have a GPU:** Pull an 8B model (`lemonade pull Qwen3-8B-GGUF`) and it will
be used automatically — it appears earlier in your downloaded list or you can reorder
`preferred_models` in config.

**Quantization:** All models use Q4_K_M quantization (the community consensus for
best quality/speed tradeoff on CPU — retains 90-95% of full-precision quality).

**Context window:** Default `ctx_size = 8192` is sufficient for most academic papers
(prompt ~50 tokens + article 2000-6000 tokens + output 200 tokens). For very long
papers, increase to `32768` (requires more RAM/VRAM).

### Available models

```bash
lemonade list               # all available (Downloaded column shows Yes/No)
lemonade list --downloaded  # only downloaded
curl http://127.0.0.1:8000/v1/models  # currently loaded
```

### Cron PATH fix

Cron runs with a minimal PATH. If `gemini` is not in `/usr/bin` or `/bin`, cron won't find it. Add a PATH line at the top of your crontab:

```
PATH=/usr/local/bin:/usr/bin:/bin:/home/user/.local/bin
```

Replace the last entry with the output of `dirname $(which gemini)`.

---

## Ollama

Ollama runs a local inference server. scholarposter calls it via HTTP:

```
POST http://localhost:11434/api/generate
```

### Install

```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

Enable and start the service:

```bash
systemctl enable --now ollama
```

### Pull a model

| Model | Pull command | RAM needed | Best for |
|-------|-------------|-----------|----------|
| `gemma3:9b` | `ollama pull gemma3:9b` | ~6 GB | Default; superior instruction following for structured academic extraction |
| `llama4:8b` | `ollama pull llama4:8b` | ~8 GB | Advanced reasoning and multi-step synthesis of complex papers |
| `deepseek-v4:7b` | `ollama pull deepseek-v4:7b` | ~5 GB | Best performance-to-size ratio; strong on multilingual academic text |
| `mistral-nemo:12b` | `ollama pull mistral-nemo:12b` | ~8 GB | Best with LaTeX/PDF artifacts and long abstracts |
| `phi4:3.8b` | `ollama pull phi4:3.8b` | ~3 GB | Lightweight; for NAS/homelab with limited RAM |

### Verify

```bash
curl http://localhost:11434/api/generate \
  -d '{"model":"gemma3:9b","prompt":"hello","stream":false}'
```

You should see a JSON response with a `"response"` field.

### Configure in `config.toml`

```toml
[enrichment.summarization]
backend = "ollama"

[enrichment.summarization.ollama]
model = "gemma3:9b"
host = "http://localhost:11434"
timeout_seconds = 30
```

If Ollama runs on a different machine, change `host` to that machine's address and ensure port 11434 is reachable.

---

## Custom prompt

You can change the summarization prompt for all backends:

```toml
[enrichment.summarization]
prompt = "In 2 sentences, describe the main finding of this research for a non-specialist audience."
max_chars = 400
```

The prompt is passed as the system/prefix prompt; the article text follows as the user input.
