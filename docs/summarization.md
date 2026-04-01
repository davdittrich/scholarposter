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
algorithm = "kl"      # "kl" or "lsa"
max_sentences = 5
timeout_seconds = 10
```

---

## Gemini CLI

scholarposter calls the `gemini` CLI binary as a subprocess, piping the article text via stdin:

```
gemini -p "<your prompt>"
```

### Install

Install the Google Gemini CLI. The exact package name may vary; as of early 2025:

```bash
pip install google-generativeai
# or follow the official install instructions at https://ai.google.dev/gemini-api/docs/downloads
```

After installation, verify the binary is on your PATH:

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
prompt = "Summarize this academic paper/article in 2-3 sentences for a social media post. Focus on the key finding and methodology. Be concise and precise."

[enrichment.summarization.gemini]
timeout_seconds = 30
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
| `gemma2:9b` | `ollama pull gemma2:9b` | ~6 GB | Default; strong precision/speed for academic text |
| `gemma3:9b` | `ollama pull gemma3:9b` | ~6 GB | 2025 release; better instruction following |
| `mistral-nemo:12b` | `ollama pull mistral-nemo:12b` | ~8 GB | Best with LaTeX/PDF artifacts and long abstracts |
| `phi4:3.8b` | `ollama pull phi4:3.8b` | ~3 GB | Lightweight; for NAS/homelab with limited RAM |

### Verify

```bash
curl http://localhost:11434/api/generate \
  -d '{"model":"gemma2:9b","prompt":"hello","stream":false}'
```

You should see a JSON response with a `"response"` field.

### Configure in `config.toml`

```toml
[enrichment.summarization]
backend = "ollama"

[enrichment.summarization.ollama]
model = "gemma2:9b"
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
