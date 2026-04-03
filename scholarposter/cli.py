"""CLI entry point for scholarposter."""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import find_dotenv, load_dotenv
from loguru import logger
from mastodon import Mastodon

from scholarposter.collector import MastodonCollector
from scholarposter.config import EnrichmentConfig, NotificationBackendConfig, load_config
from scholarposter.enrichment.pipeline import EnrichmentPipeline
from scholarposter.filters import evaluate_filters
from scholarposter.models import BibliographyEntry, PlatformState, PostResult, PostStatus, UnifiedPost
from scholarposter.notifications.base import BaseNotifier
from scholarposter.notifications.ntfy import NtfyNotifier
from scholarposter.state import StateManager

app = typer.Typer(help="Mastodon cross-poster for academics.")

VALID_PLATFORMS = {"bluesky", "linkedin", "all"}

_REDACT_BEARER = re.compile(r"Bearer \S+")
_REDACT_SECRETS = re.compile(
    r"(password|secret|token|api_key)=[^\s&]+", re.IGNORECASE
)


def _redact(msg: str) -> str:
    """Redact sensitive patterns from a log message."""
    msg = _REDACT_BEARER.sub("Bearer [REDACTED]", msg)
    msg = _REDACT_SECRETS.sub(r"\1=[REDACTED]", msg)
    return msg


def _redact_filter(record: dict[str, Any]) -> bool:
    record["message"] = _redact(record["message"])
    return True


def setup_logging(level: str = "INFO", log_file: Optional[str] = None,
                  rotation: str = "10 MB", retention: str = "30 days") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, filter=_redact_filter)
    if log_file:
        logger.add(log_file, level=level, rotation=rotation, retention=retention,
                   filter=_redact_filter)


def _check_env_permissions(cfg=None) -> None:
    """Warn if .env or credential files are world- or group-readable."""
    paths_to_check: list[str] = []
    env_path = find_dotenv()
    if env_path:
        paths_to_check.append(env_path)
    if cfg:
        cred = Path(cfg.mastodon.credentials_file)
        if cred.exists():
            paths_to_check.append(str(cred))
    for path in paths_to_check:
        mode = os.stat(path).st_mode
        if mode & 0o077:
            logger.warning(
                f"{path} has unsafe permissions "
                f"(mode {oct(mode & 0o777)}). Recommend: chmod 600 {path}"
            )


def _build_notifiers(backends: list[NotificationBackendConfig]) -> list[BaseNotifier]:
    notifiers: list[BaseNotifier] = []
    for cfg in backends:
        if cfg.type == "ntfy":
            if not cfg.topic:
                logger.warning("ntfy backend configured without a topic, skipping.")
                continue
            notifiers.append(NtfyNotifier(
                topic=cfg.topic,
                server=cfg.server or "https://ntfy.sh",
            ))
        elif cfg.type == "signal":
            if not cfg.api_url or not cfg.phone_number or not cfg.recipients:
                logger.warning("signal backend missing api_url, phone_number, or recipients, skipping.")
                continue
            from scholarposter.notifications.signal import SignalNotifier
            notifiers.append(SignalNotifier(
                api_url=cfg.api_url,
                phone_number=cfg.phone_number,
                recipients=cfg.recipients,
            ))
        elif cfg.type == "email":
            if not cfg.smtp_host or not cfg.from_addr or not cfg.to_addr:
                logger.warning("email backend missing smtp_host, from_addr, or to_addr, skipping.")
                continue
            from scholarposter.notifications.email import EmailNotifier
            notifiers.append(EmailNotifier(
                smtp_host=cfg.smtp_host,
                smtp_port=cfg.smtp_port,
                from_addr=cfg.from_addr,
                to_addr=cfg.to_addr,
            ))
        else:
            logger.warning(f"Unknown notification backend type: {cfg.type}")
    return notifiers


@app.command()
def run(
    config: Path = typer.Option(Path("config.toml"), "--config", help="Path to config.toml"),
    platform: str = typer.Option("all", "--platform", help="Platform to post to: bluesky, linkedin, all"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate posting without making API calls"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable DEBUG logging"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress INFO logging"),
) -> None:
    """Cross-post the oldest unprocessed Mastodon toot to configured platforms."""
    load_dotenv()
    if platform not in VALID_PLATFORMS:
        typer.echo(f"Invalid platform '{platform}'. Choose from: {', '.join(sorted(VALID_PLATFORMS))}", err=True)
        raise typer.Exit(code=2)

    try:
        cfg = load_config(config)
    except FileNotFoundError:
        typer.echo(f"Config not found: {config}\nCopy config.toml.example to config.toml", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(code=1)
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else cfg.logging.level)
    setup_logging(level=log_level, log_file=cfg.logging.file if not dry_run else None)

    _check_env_permissions(cfg)

    state_mgr = StateManager(
        state_dir=Path("."),
        state_file=cfg.state.state_file,
        cache_file=cfg.state.cache_file,
        lock_file=cfg.state.lock_file,
    )

    if not state_mgr.acquire_lock():
        # FR-40: lock held = another instance running; exit 0 (not an error for cron)
        logger.info("Another instance is already running. Exiting cleanly.")
        raise typer.Exit(code=0)

    try:
        mastodon = Mastodon(
            access_token=cfg.mastodon.credentials_file,
            api_base_url=cfg.mastodon.instance,
        )
        collector = MastodonCollector(mastodon)
        pipeline = EnrichmentPipeline(config=cfg.enrichment, cache=state_mgr)
        notifiers = _build_notifiers(cfg.notifications.backends)

        platforms_to_run = (
            list(cfg.platforms.keys()) if platform == "all" else [platform]
        )
        notified_platforms: set[str] = set()

        # B1: lazy init — only call mastodon.me() when a first enabled platform is reached
        user_id: Optional[str] = None

        for plat in platforms_to_run:
            if plat not in cfg.platforms:
                logger.warning(f"Platform '{plat}' not configured, skipping.")
                continue
            plat_cfg = cfg.platforms[plat]
            if not plat_cfg.enabled:
                continue

            if user_id is None:
                user_id = mastodon.me()["id"]

            since_id = state_mgr.get_since_id(plat)
            post = collector.fetch_oldest_unprocessed(user_id=user_id, since_id=since_id)
            if post is None:
                logger.info(f"[{plat}] No unprocessed toots found.")
                continue

            # FR-8: evaluate filters BEFORE enrichment (fail fast, don't waste API calls)
            filter_result = evaluate_filters(post, plat_cfg.filters)
            if not filter_result.passed:
                logger.info(f"[{plat}] Toot {post.source_id} filtered: {filter_result.reason}")
                state_mgr.update_platform_state(plat, PlatformState(
                    last_toot_id=int(post.source_id),
                    last_status="skipped",
                ))
                continue

            post = pipeline.enrich(post)

            result = _dispatch_post(plat, post, plat_cfg, dry_run)

            # Retry on transient errors (429, 5xx)
            for attempt in range(2):
                if not result.retryable:
                    break
                logger.info(f"[{plat}] Retrying ({attempt+1}/2) after transient error")
                time.sleep(2 ** attempt)
                result = _dispatch_post(plat, post, plat_cfg, dry_run)

            # FR-37: set last_posted_at on success; last_error on failure
            # Dry-run: adapter still returns POSTED but we do NOT persist state
            if not dry_run:
                posted_at = datetime.now(timezone.utc) if result.status == PostStatus.POSTED else None
                state_mgr.update_platform_state(plat, PlatformState(
                    last_toot_id=int(post.source_id),
                    last_status=result.status.value,
                    last_posted_at=posted_at,
                    last_error=result.error,
                ))

                # Append DOI-enriched links to bibliography
                if result.status == PostStatus.POSTED:
                    for link in post.links:
                        if link.doi and link.title:
                            authors: list[str] = []
                            pub_year: Optional[int] = None
                            cached = state_mgr.cache_get(f"doi:{link.doi}")
                            if cached:
                                authors = cached.get("authors", [])
                                pub_year = cached.get("year")
                            entry = BibliographyEntry(
                                doi=link.doi,
                                title=link.title,
                                authors=authors,
                                abstract=link.description or "",
                                url=link.resolved_url or link.original_url,
                                shared_at=datetime.now(timezone.utc),
                                publication_year=pub_year,
                                platforms=[plat],
                                source_toot_id=post.source_id,
                            )
                            state_mgr.append_bibliography(entry)

            if result.status == PostStatus.POSTED:
                logger.info(f"[{plat}] Posted {post.source_id}: {result.post_url}")
            else:
                logger.error(f"[{plat}] Failed to post {post.source_id}: {result.error}")
                if plat not in notified_platforms:
                    for notifier in notifiers:
                        try:
                            notifier.notify(plat, post.source_id, result.error or "unknown error")
                        except Exception as e:
                            logger.warning(f"Notification dispatch failed: {e}")
                    notified_platforms.add(plat)
    finally:
        state_mgr.release_lock()


def _dispatch_post(platform: str, post, plat_cfg, dry_run: bool):
    """Instantiate adapter and post. Validates credentials before API calls."""
    if platform == "bluesky":
        email = os.environ.get("BLUESKY_EMAIL")
        password = os.environ.get("BLUESKY_PASSWORD")
        if not email or not password:
            return PostResult(
                platform=platform,
                status=PostStatus.FAILED,
                error="Missing BLUESKY_EMAIL or BLUESKY_PASSWORD env vars",
            )
        # To add a new platform: add an elif branch here, and register in config.py
        from atproto import Client
        from scholarposter.adapters.bluesky import BlueskyAdapter
        client = Client()
        try:
            client.login(email, password)
        except Exception as e:
            return PostResult(platform=platform, status=PostStatus.FAILED,
                              error=f"Bluesky login failed: {_redact(str(e))}")
        adapter = BlueskyAdapter(client=client, hashtag_rules=plat_cfg.hashtag_rules, media_config=plat_cfg.media)
    elif platform == "linkedin":
        token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
        owner = os.environ.get("LINKEDIN_OWNER_URN")
        if not token or not owner:
            return PostResult(
                platform=platform,
                status=PostStatus.FAILED,
                error="Missing LINKEDIN_ACCESS_TOKEN or LINKEDIN_OWNER_URN env vars",
            )
        from scholarposter.adapters.linkedin import LinkedInAdapter
        adapter = LinkedInAdapter(access_token=token, owner_urn=owner, media_config=plat_cfg.media)
    else:
        return PostResult(platform=platform, status=PostStatus.SKIPPED)

    return adapter.post(post, dry_run=dry_run)


@app.command()
def status(
    config: Path = typer.Option(Path("config.toml"), "--config", help="Path to config.toml"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable DEBUG logging"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress INFO logging"),
) -> None:
    """Show last posted toot ID per platform."""
    try:
        cfg = load_config(config)
    except Exception as e:
        typer.echo(f"Warning: config load failed ({e}). Showing local state only.", err=True)
        cfg = None

    log_level = "DEBUG" if verbose else ("WARNING" if quiet else (cfg.logging.level if cfg else "INFO"))
    setup_logging(level=log_level)

    state_file = cfg.state.state_file if cfg else "state.json"
    state_mgr = StateManager(state_file=state_file)
    state = state_mgr.load_state()

    if not state:
        typer.echo("No state recorded yet.")
        return

    # Try to get pending counts via Mastodon API
    pending_counts: dict[str, str] = {}
    if cfg:
        try:
            mastodon = Mastodon(
                access_token=cfg.mastodon.credentials_file,
                api_base_url=cfg.mastodon.instance,
            )
            user_id = mastodon.me()["id"]
            for plat, data in state.items():
                last_id = data.get("last_toot_id")
                if last_id:
                    kwargs: dict = {"exclude_replies": True, "limit": 50, "min_id": last_id}
                    toots = mastodon.account_statuses(user_id, **kwargs)
                    count = len(toots)
                    pending_counts[plat] = f"{count}+" if count >= 50 else str(count)
                else:
                    pending_counts[plat] = "?"
        except Exception as e:
            logger.debug(f"Could not fetch pending counts: {e}")

    for plat, data in state.items():
        pending = pending_counts.get(plat, "?")
        typer.echo(
            f"{plat}: last_toot_id={data.get('last_toot_id')}, "
            f"status={data.get('last_status')}, "
            f"pending={pending}, "
            f"last_error={data.get('last_error')}"
        )


@app.command()
def retry(
    config: Path = typer.Option(Path("config.toml"), "--config", help="Path to config.toml"),
    platform: str = typer.Option(..., "--platform", help="Platform to retry: bluesky or linkedin"),
    toot_id: int = typer.Option(..., "--toot-id", help="Toot ID to retry"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate posting without making API calls"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable DEBUG logging"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress INFO logging"),
) -> None:
    """Retry posting a specific toot to a single platform."""
    load_dotenv()
    if platform not in {"bluesky", "linkedin"}:
        typer.echo(f"Invalid platform '{platform}'. Choose from: bluesky, linkedin", err=True)
        raise typer.Exit(code=2)

    try:
        cfg = load_config(config)
    except FileNotFoundError:
        typer.echo(f"Config not found: {config}\nCopy config.toml.example to config.toml", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(code=1)
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else cfg.logging.level)
    setup_logging(level=log_level)
    _check_env_permissions(cfg)

    state_mgr = StateManager(
        state_dir=Path("."),
        state_file=cfg.state.state_file,
        cache_file=cfg.state.cache_file,
        lock_file=cfg.state.lock_file,
    )

    if not state_mgr.acquire_lock():
        # FR-40: lock held = another instance running; exit 0 (not an error)
        logger.info("Another instance is already running. Retry after current run completes.")
        raise typer.Exit(code=0)

    try:
        # Validate config before any API calls (guard inside try so lock is always released)
        if platform not in cfg.platforms:
            typer.echo(f"Platform '{platform}' not configured.", err=True)
            raise typer.Exit(code=1)
        plat_cfg = cfg.platforms[platform]

        mastodon = Mastodon(
            access_token=cfg.mastodon.credentials_file,
            api_base_url=cfg.mastodon.instance,
        )
        collector = MastodonCollector(mastodon)
        pipeline = EnrichmentPipeline(config=cfg.enrichment, cache=state_mgr)

        # Fetch the specific toot directly (bypasses timeline pagination)
        raw_toot = mastodon.status(toot_id)
        post = collector._toot_to_unified_post(raw_toot)
        post = pipeline.enrich(post)
        result = _dispatch_post(platform, post, plat_cfg, dry_run)

        # FR-37: set last_posted_at on success; last_error on failure
        # Dry-run: adapter still returns POSTED but we do NOT persist state
        if not dry_run:
            posted_at = datetime.now(timezone.utc) if result.status == PostStatus.POSTED else None
            state_mgr.update_platform_state(platform, PlatformState(
                last_toot_id=int(post.source_id),
                last_status=result.status.value,
                last_posted_at=posted_at,
                last_error=result.error,
            ))

        if result.status == PostStatus.POSTED:
            logger.info(f"[{platform}] Retried {post.source_id}: {result.post_url}")
            typer.echo(f"OK: {result.post_url}")
        else:
            logger.error(f"[{platform}] Retry failed {post.source_id}: {result.error}")
            typer.echo(f"FAILED: {result.error}", err=True)
            raise typer.Exit(code=1)
    finally:
        state_mgr.release_lock()


@app.command(name="config")
def config_cmd(
    config: Path = typer.Option(Path("config.toml"), "--config", help="Path to config.toml"),
    action: str = typer.Argument("validate", help="Action: validate"),
) -> None:
    """Config management subcommands (currently: validate)."""
    if action != "validate":
        typer.echo(f"Unknown action '{action}'. Available: validate", err=True)
        raise typer.Exit(code=2)

    cfg = load_config(config)
    _print_masked_config(cfg.model_dump())


_SENSITIVE_FIELDS = frozenset({
    "credentials_file", "access_token", "password", "smtp_password",
    "secret", "api_key",
})


def _print_masked_config(data: Any, indent: int = 0) -> None:
    prefix = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if key in _SENSITIVE_FIELDS:
                typer.echo(f"{prefix}{key}: [REDACTED]")
            elif isinstance(value, (dict, list)):
                typer.echo(f"{prefix}{key}:")
                _print_masked_config(value, indent + 1)
            else:
                typer.echo(f"{prefix}{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # First key of a dict-in-list gets the "- " bullet; remaining keys align
                for i, (key, value) in enumerate(item.items()):
                    bullet = "- " if i == 0 else "  "
                    if key in _SENSITIVE_FIELDS:
                        typer.echo(f"{prefix}{bullet}{key}: [REDACTED]")
                    elif isinstance(value, (dict, list)):
                        typer.echo(f"{prefix}{bullet}{key}:")
                        _print_masked_config(value, indent + 1)
                    else:
                        typer.echo(f"{prefix}{bullet}{key}: {value}")
            else:
                typer.echo(f"{prefix}- {item}")


@app.command()
def bibliography(
    output_format: str = typer.Option("bibtex", "--format", help="bibtex, json, or markdown"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file"),
    config: Path = typer.Option(Path("config.toml"), "--config"),
) -> None:
    """Export bibliography of shared papers."""
    try:
        cfg = load_config(config)
        state_dir = config.parent.resolve()
        state_mgr = StateManager(
            state_dir=state_dir,
            state_file=Path(cfg.state.state_file).name,
            cache_file=Path(cfg.state.cache_file).name,
        )
    except Exception:
        state_mgr = StateManager()

    raw = state_mgr.load_bibliography()
    if not raw:
        typer.echo("No bibliography entries yet.")
        return

    entries: list[BibliographyEntry] = []
    for d in raw:
        try:
            entries.append(BibliographyEntry.model_validate(d))
        except Exception as e:
            logger.warning(f"Skipping malformed bibliography entry: {e}")
    if not entries:
        typer.echo("No valid bibliography entries found.")
        return

    if output_format == "bibtex":
        from scholarposter.bibliography import to_bibtex
        text = to_bibtex(entries)
    elif output_format == "markdown":
        from scholarposter.bibliography import to_markdown
        text = to_markdown(entries)
    elif output_format == "json":
        import json as json_mod
        text = json_mod.dumps([e.model_dump(mode="json") for e in entries], indent=2, default=str)
    else:
        typer.echo(f"Unknown format: {output_format}", err=True)
        raise typer.Exit(code=2)

    if output:
        output.write_text(text)
        typer.echo(f"Written to {output}")
    else:
        typer.echo(text)


@app.command()
def enrich(
    url: str = typer.Argument(help="URL to enrich"),
    config: Path = typer.Option(Path("config.toml"), "--config"),
    summarize: bool = typer.Option(True, help="Include summary"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Enrich a URL: resolve, extract metadata, look up DOI, summarize."""
    try:
        cfg = load_config(config)
        enrichment_cfg = cfg.enrichment
        state_dir = config.parent.resolve()
        state_mgr = StateManager(
            state_dir=state_dir,
            state_file=Path(cfg.state.state_file).name,
            cache_file=Path(cfg.state.cache_file).name,
        )
    except Exception:
        enrichment_cfg = EnrichmentConfig()
        state_mgr = StateManager()

    if not summarize:
        enrichment_cfg = enrichment_cfg.model_copy(
            update={"summarization": enrichment_cfg.summarization.model_copy(
                update={"enabled": False}
            )}
        )

    pipeline = EnrichmentPipeline(config=enrichment_cfg, cache=state_mgr)

    dummy_post = UnifiedPost(
        source_id="enrich-cli",
        text="",
        source_url="",
        created_at=datetime.now(timezone.utc),
        urls=[url],
    )
    enriched = pipeline.enrich(dummy_post)

    link = enriched.links[0] if enriched.links else None
    if link is None:
        typer.echo("No enrichment data found.", err=True)
        raise typer.Exit(code=1)

    has_metadata = any([link.title, link.doi, link.description, link.summary])

    if json_output:
        import json as json_mod
        typer.echo(json_mod.dumps(
            link.model_dump(mode="json", exclude={"body_text", "thumbnail_bytes"}),
            indent=2, default=str,
        ))
    else:
        if link.title:
            typer.echo(f"Title:    {link.title}")
        if link.doi:
            typer.echo(f"DOI:      {link.doi}")
        if link.description:
            desc = link.description[:200] + ("…" if len(link.description) > 200 else "")
            typer.echo(f"Abstract: {desc}")
        if link.resolved_url and link.resolved_url != link.original_url:
            typer.echo(f"Resolved: {link.resolved_url}")
        if link.summary:
            typer.echo(f"\nSummary:\n{link.summary}")
        if not has_metadata:
            typer.echo("No structured metadata found.")


@app.command()
def discover(
    config: Path = typer.Option(Path("config.toml"), "--config"),
    days: int = typer.Option(30, help="Look back N days"),
    limit: int = typer.Option(10, help="Max suggestions"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Discover recent papers matching your sharing interests."""
    try:
        cfg = load_config(config)
        state_dir = config.parent.resolve()
        state_mgr = StateManager(
            state_dir=state_dir,
            state_file=Path(cfg.state.state_file).name,
            cache_file=Path(cfg.state.cache_file).name,
        )
        email = cfg.enrichment.crossref.etiquette_email
    except Exception:
        state_mgr = StateManager()
        email = ""

    bib = state_mgr.load_bibliography()
    if not bib:
        typer.echo("No bibliography found. Share papers first with `scholarposter run`.", err=True)
        raise typer.Exit(code=1)

    from scholarposter.discovery import extract_interests, discover_papers
    interests = extract_interests(bib)
    if not interests["top_authors"]:
        typer.echo("Not enough data. Share more papers with DOIs.", err=True)
        raise typer.Exit(code=1)

    if len(interests["top_authors"]) < 3:
        typer.echo("Note: few authors in bibliography — results may be limited.\n", err=True)

    papers = discover_papers(interests, etiquette_email=email, max_results=limit, days=days)
    if not papers:
        typer.echo("No new papers found matching your interests.")
        return

    if json_output:
        import json as json_mod
        typer.echo(json_mod.dumps(papers, indent=2))
    else:
        typer.echo(f"Paper Discovery — {len(papers)} suggestions\n")
        for i, p in enumerate(papers, 1):
            authors = ", ".join(p["authors"][:3])
            typer.echo(f"{i}. \"{p['title']}\"")
            typer.echo(f"   {authors} | {p['publication_date']} | Cited: {p['cited_by_count']}")
            typer.echo(f"   DOI: {p['doi']}")
            if p.get("open_access_url"):
                typer.echo(f"   OA: {p['open_access_url']}")
            typer.echo()
