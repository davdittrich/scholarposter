"""CLI entry point for scholarposter."""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import find_dotenv
from loguru import logger
from mastodon import Mastodon

from scholarposter.collector import MastodonCollector
from scholarposter.config import NotificationBackendConfig, load_config
from scholarposter.enrichment.pipeline import EnrichmentPipeline
from scholarposter.filters import evaluate_filters
from scholarposter.models import PlatformState, PostStatus
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


def setup_logging(level: str = "INFO", log_file: Optional[str] = None,
                  rotation: str = "10 MB", retention: str = "30 days") -> None:
    def _redact_filter(record: dict[str, Any]) -> bool:
        record["message"] = _redact(record["message"])
        return True

    logger.remove()
    logger.add(sys.stderr, level=level, filter=_redact_filter)
    if log_file:
        logger.add(log_file, level=level, rotation=rotation, retention=retention,
                   filter=_redact_filter)


def _check_env_permissions() -> None:
    """Warn if .env file is world- or group-readable."""
    env_path = find_dotenv()
    if not env_path:
        return
    mode = os.stat(env_path).st_mode
    if mode & 0o077:
        logger.warning(
            f".env file at {env_path} has unsafe permissions "
            f"(mode {oct(mode & 0o777)}). Recommend: chmod 600 {env_path}"
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
    if platform not in VALID_PLATFORMS:
        typer.echo(f"Invalid platform '{platform}'. Choose from: {', '.join(sorted(VALID_PLATFORMS))}", err=True)
        raise typer.Exit(code=2)

    cfg = load_config(config)
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else cfg.logging.level)
    setup_logging(level=log_level, log_file=cfg.logging.file if not dry_run else None)

    _check_env_permissions()

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

            # FR-37: set last_posted_at on success; last_error on failure
            posted_at = datetime.now(timezone.utc) if result.status == PostStatus.POSTED else None
            state_mgr.update_platform_state(plat, PlatformState(
                last_toot_id=int(post.source_id),
                last_status=result.status.value,
                last_posted_at=posted_at,
                last_error=result.error,
            ))

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
    """Instantiate adapter and post."""
    from dotenv import load_dotenv
    load_dotenv()

    if platform == "bluesky":
        from atproto import Client
        from scholarposter.adapters.bluesky import BlueskyAdapter
        client = Client()
        client.login(os.environ.get("BLUESKY_EMAIL", ""), os.environ.get("BLUESKY_PASSWORD", ""))
        adapter = BlueskyAdapter(client=client, hashtag_rules=plat_cfg.hashtag_rules)
    elif platform == "linkedin":
        from scholarposter.adapters.linkedin import LinkedInAdapter
        adapter = LinkedInAdapter()
    else:
        from scholarposter.models import PostResult, PostStatus
        return PostResult(platform=platform, status=PostStatus.SKIPPED)

    return adapter.post(post, dry_run=dry_run)


@app.command()
def status(
    config: Path = typer.Option(Path("config.toml"), "--config", help="Path to config.toml"),
) -> None:
    """Show last posted toot ID per platform."""
    try:
        cfg = load_config(config)
    except Exception:
        cfg = None

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
        except Exception:
            pass  # API unavailable; pending counts remain unknown

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
) -> None:
    """Retry posting a specific toot to a single platform."""
    if platform not in {"bluesky", "linkedin"}:
        typer.echo(f"Invalid platform '{platform}'. Choose from: bluesky, linkedin", err=True)
        raise typer.Exit(code=2)

    cfg = load_config(config)
    setup_logging(level=cfg.logging.level)
    _check_env_permissions()

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
            elif isinstance(item, list):
                _print_masked_config(item, indent + 1)
            else:
                typer.echo(f"{prefix}- {item}")
