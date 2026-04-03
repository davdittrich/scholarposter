"""CLI entry point for tldr-scholar."""
from __future__ import annotations

import json as json_mod
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from loguru import logger

from tldr_scholar import summarize_file, summarize_url
from tldr_scholar.config import load_config
from tldr_scholar.ingest import EmptyTextError, PasswordProtectedError, UnsupportedInputError
from tldr_scholar.models import SummaryRequest

app = typer.Typer(help="Academic text summarizer — PDF, URL, Markdown, text.")

_LENGTH_PRESETS = {"short": 200, "medium": 500, "long": 1000}


@app.command()
def main(
    source: str = typer.Argument(help="File path or URL to summarize"),
    length: str = typer.Option("medium", "--length", help="short|medium|long"),
    max_chars: Optional[int] = typer.Option(None, "--max-chars", help="Override length preset"),
    focus: str = typer.Option("main findings and novel insights", "--focus"),
    hashtags: int = typer.Option(0, "--hashtags", help="Number of hashtags to generate"),
    output_format: str = typer.Option("text", "--format", help="text|json|markdown"),
    backend: str = typer.Option("auto", "--backend",
                                help="gemini|lemonade|ollama|extractive|auto"),
    config: Optional[Path] = typer.Option(None, "--config", help="Config file path"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Summarize a PDF, URL, Markdown file, or text file."""
    # Validate backend
    valid_backends = {"auto", "gemini", "lemonade", "ollama", "extractive"}
    if backend not in valid_backends:
        typer.echo(f"Invalid backend '{backend}'. Choose from: {', '.join(sorted(valid_backends))}", err=True)
        raise typer.Exit(code=2)

    # Validate format
    if output_format not in ("text", "json", "markdown"):
        typer.echo(f"Invalid format '{output_format}'. Choose: text, json, markdown", err=True)
        raise typer.Exit(code=2)

    # Logging
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    logger.remove()
    logger.add(sys.stderr, level=log_level)

    # Resolve max_chars
    if max_chars is not None:
        effective_max_chars = max_chars
    elif length in _LENGTH_PRESETS:
        effective_max_chars = _LENGTH_PRESETS[length]
    else:
        typer.echo(f"Invalid length '{length}'. Choose: short, medium, long", err=True)
        raise typer.Exit(code=2)

    # Load config
    config_path = config or os.environ.get("TLDR_SCHOLAR_CONFIG")
    backend_config = {}
    if config_path:
        cfg = load_config(Path(config_path))
        if backend in ("gemini", "auto"):
            backend_config.update(cfg.gemini.model_dump())
        if backend in ("lemonade", "auto"):
            backend_config.update(cfg.lemonade.model_dump())
        if backend in ("ollama", "auto"):
            backend_config.update(cfg.ollama.model_dump())

    # Run summarization
    try:
        from urllib.parse import urlparse
        parsed = urlparse(source)
        if parsed.scheme in ("http", "https"):
            result = summarize_url(
                source, max_chars=effective_max_chars, focus=focus,
                hashtags=hashtags, backend=backend, backend_config=backend_config,
            )
        else:
            result = summarize_file(
                source, max_chars=effective_max_chars, focus=focus,
                hashtags=hashtags, backend=backend, backend_config=backend_config,
            )
    except UnsupportedInputError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=2)
    except PasswordProtectedError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except EmptyTextError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=2)

    # Handle empty result
    if not result.text and backend != "auto":
        typer.echo(f"Error: backend '{backend}' returned empty response", err=True)
        raise typer.Exit(code=1)
    elif not result.text:
        typer.echo("Error: all backends failed to produce a summary", err=True)
        raise typer.Exit(code=1)

    # Output
    if output_format == "json":
        typer.echo(json_mod.dumps(result.model_dump(mode="json"), indent=2, default=str))
    elif output_format == "markdown":
        typer.echo(f"## Summary\n\n{result.text}")
        if result.hashtags:
            typer.echo(f"\n## Hashtags\n\n{' '.join(result.hashtags)}")
    else:  # text
        typer.echo(result.text)
        if result.hashtags:
            typer.echo(" ".join(result.hashtags))
