"""CLI entry point for scholarposter."""
from __future__ import annotations

import difflib
import importlib.metadata
import importlib.resources
import os
import re
import sys
import time
import tomllib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import find_dotenv, load_dotenv
from loguru import logger
from mastodon import Mastodon, MastodonAPIError

from scholarposter.audit.log import build_audit_record
from scholarposter.collector import MastodonCollector
from scholarposter.config import EnrichmentConfig, NotificationBackendConfig, PlatformConfig, ScholarposterConfig, load_config
from scholarposter.enrichment.pipeline import EnrichmentPipeline
from scholarposter.filters import evaluate_filters
from scholarposter.models import BibliographyEntry, PlatformState, PostResult, PostStatus, UnifiedPost
from scholarposter.notifications.base import BaseNotifier
from scholarposter.notifications.ntfy import NtfyNotifier
from scholarposter.auth.cli import auth_app
from scholarposter.discovery.digest import format_table, send_digest
from scholarposter.discovery.ranking import rank
from scholarposter.state import StateManager

app = typer.Typer(help="Mastodon cross-poster for academics.")
app.add_typer(auth_app, name="auth")

VALID_PLATFORMS = {"bluesky", "linkedin", "all"}

# FR-98: URL regex for extracting numeric toot ID from Mastodon URLs.
# Matches both /@<user>/<id> and /users/<user>/statuses/<id> formats.
_TOOT_URL_RE = re.compile(
    r"https?://[^/]+/(?:@[^/]+|users/[^/]+/statuses)/(\d+)"
)

_SET_WATERMARK_USAGE = (
    "Usage:\n"
    "  scholarposter set-watermark --toot-id 113456789012345678\n"
    "  scholarposter set-watermark --toot-url 'https://mastodon.social/@you/113456789012345678'\n"
    "  scholarposter set-watermark --date 2026-01-15"
)

_REDACT_BEARER = re.compile(r"Bearer \S+")
_REDACT_SECRETS = re.compile(
    r"(password|secret|token|api_key)=[^\s&]+", re.IGNORECASE
)
# FR-64: OAuth-specific redaction — scoped to OAuth context
_REDACT_OAUTH = re.compile(r"(code|state)=[^\s&]+")


def _redact(msg: str) -> str:
    """Redact sensitive patterns from a log message."""
    msg = _REDACT_BEARER.sub("Bearer [REDACTED]", msg)
    msg = _REDACT_SECRETS.sub(r"\1=[REDACTED]", msg)
    # FR-64: redact OAuth code/state only in OAuth-related contexts
    if "/oauth/" in msg or "/callback" in msg:
        msg = _REDACT_OAUTH.sub(r"\1=[REDACTED]", msg)
    return msg


def _redact_filter(record: dict[str, Any]) -> bool:
    record["message"] = _redact(record["message"])
    return True


def setup_logging(level: str = "INFO", log_file: Optional[str] = None,
                  rotation: str = "10 MB", retention: str = "30 days") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, filter=_redact_filter)  # type: ignore[arg-type]
    if log_file:
        logger.add(log_file, level=level, rotation=rotation, retention=retention,
                   filter=_redact_filter)  # type: ignore[arg-type]


def _load_config_and_state(config: Path) -> tuple[StateManager, Optional[Any]]:
    """Load config and construct StateManager.

    Returns (state_mgr, cfg). On config failure, returns (StateManager(), None).
    On StateManager failure, returns (StateManager(), cfg) — config is preserved.
    """
    try:
        cfg = load_config(config)
    except Exception:
        return StateManager(), None
    try:
        state_dir = config.parent.resolve()
        state_mgr = StateManager(
            state_dir=state_dir,
            state_file=Path(cfg.state.state_file).name,
            cache_file=Path(cfg.state.cache_file).name,
            bibliography_file=cfg.state.bibliography_file,
        )
        return state_mgr, cfg
    except Exception:
        return StateManager(), cfg


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
    env_path = Path(config).parent.resolve() / ".env"
    load_dotenv(dotenv_path=env_path)
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

    state_dir = config.parent.resolve()
    state_mgr = StateManager(
        state_dir=state_dir,
        state_file=cfg.state.state_file,
        cache_file=cfg.state.cache_file,
        lock_file=cfg.state.lock_file,
        audit_file=cfg.audit.resolved_file if cfg.audit.enabled else None,
        bibliography_file=cfg.state.bibliography_file,
        audit_rotation_max_bytes=(
            cfg.audit.rotation_max_mb * 1024 * 1024 if cfg.audit.enabled else 0
        ),
        audit_retention_days=(
            cfg.audit.retention_days if cfg.audit.enabled else 0
        ),
    )

    if not state_mgr.acquire_lock():
        # FR-40: lock held = another instance running; exit 0 (not an error for cron)
        logger.info("Another instance is already running. Exiting cleanly.")
        raise typer.Exit(code=0)

    state_mgr.prune_audit()

    try:
        mastodon = _build_mastodon_client(cfg, env_path)
        collector = MastodonCollector(mastodon)
        pipeline = EnrichmentPipeline(config=cfg.enrichment, cache=state_mgr)
        notifiers = _build_notifiers(cfg.notifications.backends)

        platforms_to_run = (
            list(cfg.platforms.keys()) if platform == "all" else [platform]
        )
        notified_platforms: set[str] = set()

        # B1: lazy init — only call mastodon.me() when a first enabled platform is reached
        user_id: Optional[str] = None

        # FR-60: LinkedIn refresh token expiry warning (7 days)
        _check_linkedin_token_expiry(state_mgr, env_path)

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

            result = _dispatch_post(plat, post, plat_cfg, dry_run, state_mgr=state_mgr, env_path=env_path)

            # Retry on transient errors (429, 5xx)
            for attempt in range(2):
                if not result.retryable:
                    break
                logger.info(f"[{plat}] Retrying ({attempt+1}/2) after transient error")
                time.sleep(2 ** attempt)
                result = _dispatch_post(plat, post, plat_cfg, dry_run, state_mgr=state_mgr, env_path=env_path)

            # FR-90: audit log — one record per (toot, platform), outside dry_run guard
            if cfg.audit.enabled:
                record = build_audit_record(
                    toot_id=post.source_id,
                    platform=plat,
                    post=post,
                    result=result,
                    dry_run=dry_run,
                )
                state_mgr.append_audit(record)

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


def _build_mastodon_client(
    cfg: ScholarposterConfig,
    env_path: Optional[Path] = None,
) -> Mastodon:
    """Construct Mastodon client with token validation. Exits 1 on 401 with re-auth instructions."""
    mastodon_client = Mastodon(
        access_token=cfg.mastodon.credentials_file,
        api_base_url=cfg.mastodon.instance,
    )
    try:
        mastodon_client.account_verify_credentials()
        return mastodon_client
    except MastodonAPIError as e:
        if getattr(getattr(e, "response", None), "status_code", None) == 401:
            logger.error("Mastodon token revoked. Run `scholarposter auth mastodon` to re-authorize.")
            if env_path:
                _send_refresh_notification(
                    env_path,
                    "Mastodon token revoked. Run `scholarposter auth mastodon` to re-authorize."
                )
            raise typer.Exit(code=1)
        else:
            logger.warning(f"Mastodon verify_credentials failed: {_redact(str(e))}")
            return mastodon_client
    except Exception as e:
        logger.warning(f"Mastodon verify_credentials failed (non-API): {_redact(str(e))}")
        return mastodon_client


def _dispatch_post(
    platform: str,
    post: UnifiedPost,
    plat_cfg: PlatformConfig,
    dry_run: bool,
    state_mgr: Optional[StateManager] = None,
    env_path: Optional[Path] = None,
) -> PostResult:
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
        # FR-63: check auth state (expired token)
        if state_mgr:
            li_state = state_mgr.load_state().get("linkedin", {})
            if li_state.get("auth_status") == "auth_expired":
                return PostResult(platform=platform, status=PostStatus.SKIPPED,
                    error="LinkedIn token expired. Run: scholarposter auth linkedin")

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


def _check_linkedin_token_expiry(state_mgr: StateManager, env_path: Path) -> None:
    """FR-60: Warn 7 days before LinkedIn access token expiry."""
    expires_str = os.environ.get("LINKEDIN_TOKEN_EXPIRES_AT")
    if not expires_str:
        return
    try:
        expires = datetime.fromisoformat(expires_str)
    except ValueError:
        return
    days_left = (expires - datetime.now(timezone.utc)).days
    if days_left > 7:
        return

    # Dedup: one warning per day (UTC)
    today = datetime.now(timezone.utc).date()
    li_state = state_mgr.load_state().get("linkedin", {})
    last_warned = li_state.get("refresh_warning_last_sent")
    if last_warned:
        try:
            if date.fromisoformat(last_warned) == today:
                return
        except (ValueError, TypeError):
            pass

    msg = f"LinkedIn token expires on {expires.date()}. Run `scholarposter auth linkedin` to re-authorize."
    logger.warning(msg)
    _send_refresh_notification(env_path, msg)
    state_mgr.update_platform_state("linkedin", PlatformState(
        refresh_warning_last_sent=today,
    ))


def _send_refresh_notification(env_path: Path, message: str) -> None:
    """Send a notification about LinkedIn token refresh issues."""
    config_path = env_path.parent / "config.toml"
    if not config_path.exists():
        return
    try:
        cfg = load_config(config_path)
        notifiers = _build_notifiers(cfg.notifications.backends)
        for notifier in notifiers:
            try:
                notifier.notify("linkedin", "token-refresh", message)
            except Exception as e:
                logger.warning(f"Refresh notification failed: {e}")
    except Exception as e:
        logger.warning(f"Could not load config for notifications: {e}")


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
    state_dir = config.parent.resolve() if cfg else Path(".")
    state_mgr = StateManager(state_dir=state_dir, state_file=state_file)
    state = state_mgr.load_state()

    if not state:
        typer.echo("No state recorded yet.")
        return

    # Try to get pending counts via Mastodon API
    pending_counts: dict[str, str] = {}
    if cfg:
        try:
            mastodon = _build_mastodon_client(cfg)
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

    # Load .env for token expiry display
    env_path = config.parent.resolve() / ".env"
    load_dotenv(dotenv_path=env_path)

    for plat, data in state.items():
        pending = pending_counts.get(plat, "?")

        # FR-62: LinkedIn auth status display
        if plat == "linkedin":
            auth_st = data.get("auth_status", "normal")
            if auth_st == "auth_expired":
                typer.echo(f"{plat}: token expired — run `scholarposter auth linkedin`")
                continue
            expires_at = os.environ.get("LINKEDIN_TOKEN_EXPIRES_AT")
            extra = ""
            if expires_at:
                try:
                    days = (datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)).days
                    extra = f", token_expires_in={days}d"
                    if days <= 7:
                        extra += " (WARNING: expiring soon)"
                except ValueError:
                    pass

        else:
            extra = ""

        typer.echo(
            f"{plat}: last_toot_id={data.get('last_toot_id')}, "
            f"status={data.get('last_status')}, "
            f"pending={pending}, "
            f"last_error={data.get('last_error')}{extra}"
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
    env_path = Path(config).parent.resolve() / ".env"
    load_dotenv(dotenv_path=env_path)
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

    state_dir = config.parent.resolve()
    state_mgr = StateManager(
        state_dir=state_dir,
        state_file=cfg.state.state_file,
        cache_file=cfg.state.cache_file,
        lock_file=cfg.state.lock_file,
        bibliography_file=cfg.state.bibliography_file,
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

        mastodon = _build_mastodon_client(cfg, env_path)
        collector = MastodonCollector(mastodon)
        pipeline = EnrichmentPipeline(config=cfg.enrichment, cache=state_mgr)

        # Fetch the specific toot directly (bypasses timeline pagination)
        raw_toot = mastodon.status(toot_id)
        post = collector.toot_to_unified_post(raw_toot)
        post = pipeline.enrich(post)
        result = _dispatch_post(platform, post, plat_cfg, dry_run, state_mgr=state_mgr, env_path=env_path)

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


# ---------------------------------------------------------------------------
# config-update helpers (US-017)
# ---------------------------------------------------------------------------

def _load_example_config() -> tuple[str, dict]:
    """Load shipped config.example.toml. Returns (raw_text, parsed_dict)."""
    resource = importlib.resources.files("scholarposter.data").joinpath("config.example.toml")
    raw = resource.read_bytes().decode("utf-8")
    return raw, tomllib.loads(raw)


def _collect_missing_keys(
    user: dict, example: dict, prefix: str = ""
) -> list[tuple[str, str, Any]]:
    """Return [(section_path, key, value)] for all leaf keys in example absent from user."""
    missing: list[tuple[str, str, Any]] = []
    for k, v in example.items():
        new_prefix = k if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            user_sub = user.get(k, {}) if isinstance(user, dict) else {}
            missing.extend(_collect_missing_keys(user_sub, v, new_prefix))
        else:
            if not isinstance(user, dict) or k not in user:
                missing.append((prefix, k, v))
    return missing


def _is_key_commented(raw: str, section: str, key: str) -> bool:
    """Return True if '# key =' appears within this section's config-update block(s).

    Scans from the first matching sentinel to EOF, but stops at any sentinel
    belonging to a *different* section to prevent cross-section false positives.
    Multiple same-section sentinels (cross-version appends) are scanned through.
    """
    sentinel = f"# --- config-update: {section} ---"
    idx = raw.find(sentinel)
    if idx == -1:
        return False
    region = raw[idx:]
    for line in region.splitlines():
        stripped = line.strip()
        # Stop at a different section's sentinel to avoid cross-section matches
        if (stripped.startswith("# --- config-update:") and stripped != sentinel):
            break
        if stripped.startswith(f"# {key} =") or stripped.startswith(f"# {key}="):
            return True
    return False


def _get_config_update_version() -> str:
    try:
        return importlib.metadata.version("scholarposter")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _format_toml_value(v: Any) -> str:
    """Return a TOML-valid string representation of v (no quoting of non-strings)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    elif isinstance(v, (int, float)):
        return str(v)
    elif isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(v, list):
        items = ", ".join(_format_toml_value(item) for item in v)
        return f"[{items}]"
    else:
        escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'


def _redact_toml_value(key: str, formatted_value: str) -> str:
    """Replace sensitive field values with '<redacted>' (TOML-quoted)."""
    if key in _SENSITIVE_FIELDS:
        return '"<redacted>"'
    return formatted_value


def _section_absent(user_parsed: dict, section_path: str) -> bool:
    """Return True if the section path is not present in user_parsed."""
    parts = section_path.split(".")
    d: Any = user_parsed
    for part in parts:
        if not isinstance(d, dict) or part not in d:
            return True
        d = d[part]
    return False


def _build_append_block(
    missing: list[tuple[str, str, Any]],
    user_raw: str,
    user_parsed: dict,
    version: str,
) -> str:
    """Build the text block to append to config.toml for all missing keys."""
    if not missing:
        return ""

    # Group by section_path, preserving order
    sections: dict[str, list[tuple[str, Any]]] = {}
    for section_path, key, value in missing:
        if section_path not in sections:
            sections[section_path] = []
        sections[section_path].append((key, value))

    lines: list[str] = [""]  # leading blank line separator

    for section_path, keys in sections.items():
        remaining = [
            (k, v) for k, v in keys
            if not _is_key_commented(user_raw, section_path, k)
        ]
        if not remaining:
            continue

        absent = _section_absent(user_parsed, section_path)
        lines.append(f"# --- config-update: {section_path} ---")
        lines.append(f"# Added by scholarposter config-update {version} — {section_path}")
        if absent:
            lines.append(f"# [{section_path}]")
        for k, v in remaining:
            lines.append(f"# {k} = {_redact_toml_value(k, _format_toml_value(v))}")
        lines.append("")

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write content atomically via a .tmp file + rename."""
    tmp = path.parent / (path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.rename(tmp, path)


@app.command(name="config-update")
def config_update_cmd(
    config: Path = typer.Option(Path("config.toml"), "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    diff: bool = typer.Option(False, "--diff"),
) -> None:
    """Append missing config keys (commented out) from config.example.toml."""
    try:
        _, example_parsed = _load_example_config()
    except FileNotFoundError:
        typer.echo("Shipped example config not found — reinstall the package", err=True)
        raise typer.Exit(code=1)

    if config.exists():
        raw_bytes = config.read_bytes()
        user_raw = raw_bytes.decode("utf-8")
        user_parsed: dict = tomllib.loads(user_raw)
    else:
        user_parsed = {}
        user_raw = ""

    version = _get_config_update_version()
    missing = _collect_missing_keys(user_parsed, example_parsed)
    block = _build_append_block(missing, user_raw, user_parsed, version)

    if not block:
        typer.echo(f"{config} is up to date")
        return

    new_content = user_raw + block

    if diff or dry_run:
        if diff:
            old_lines = user_raw.splitlines(keepends=True)
            new_lines = new_content.splitlines(keepends=True)
            for line in difflib.unified_diff(
                old_lines, new_lines,
                fromfile="config.toml", tofile="config.toml (updated)",
                lineterm="",
            ):
                typer.echo(line)
        else:
            typer.echo(block)
        return

    _atomic_write_text(config, new_content)
    key_count = sum(
        1 for line in block.splitlines()
        if line.startswith("# ") and " = " in line
    )
    typer.echo(f"Appended {key_count} missing key(s) to {config}")


@app.command()
def bibliography(
    output_format: str = typer.Option("bibtex", "--format", help="bibtex, json, or markdown"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file"),
    config: Path = typer.Option(Path("config.toml"), "--config"),
) -> None:
    """Export bibliography of shared papers."""
    state_mgr, _ = _load_config_and_state(config)
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
    state_mgr, cfg = _load_config_and_state(config)
    enrichment_cfg = cfg.enrichment if cfg else EnrichmentConfig()

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


_DISCOVER_MODES = {"cited-by", "cites", "co-cited", "all"}
_TITLE_WIDTH = 40  # truncate at 40 chars for 120-col table


@app.command()
def discover(
    config: Path = typer.Option(Path("config.toml"), "--config"),
    mode: Optional[str] = typer.Option(None, "--mode",
        help="Discovery mode: cited-by | cites | co-cited | all (default: config modes)"),
    since: Optional[str] = typer.Option(None, "--since",
        help="Only show papers from YYYY-MM-DD onwards"),
    days: Optional[int] = typer.Option(None, "--days",
        help="[Deprecated] Look back N days — use --since instead"),
    limit: int = typer.Option(10, "--limit", help="Max suggestions"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    wide: bool = typer.Option(False, "--wide", help="Don't truncate titles"),
    email_digest: bool = typer.Option(False, "--email-digest",
        help="Send digest email to discovery.digest_email"),
) -> None:
    """Discover related papers via OpenAlex citation graph (US-014)."""
    import json as json_mod

    from scholarposter.discovery.graph import cited_by as _cited_by, cites as _cites

    state_mgr, cfg = _load_config_and_state(config)
    email = cfg.enrichment.crossref.etiquette_email if cfg else ""
    disc_cfg = cfg.discovery if cfg else None

    # --days deprecation
    if days is not None:
        typer.echo("Warning: --days is deprecated. Use --since.", err=True)
        if since is None:
            from datetime import timedelta
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    # Resolve modes
    resolved_modes: list[str]
    if mode is None:
        resolved_modes = (disc_cfg.modes if disc_cfg else ["cited-by", "cites"])
    elif mode not in _DISCOVER_MODES:
        typer.echo(f"Unknown --mode '{mode}'. Choose from: {', '.join(sorted(_DISCOVER_MODES))}",
                   err=True)
        raise typer.Exit(code=1)
    elif mode == "co-cited":
        typer.echo("co-cited is not yet implemented (Phase 6).")
        return
    elif mode == "all":
        resolved_modes = ["cited-by", "cites"]
        logger.info("co-cited skipped: Phase 6 not yet implemented")
    else:
        resolved_modes = [mode]

    bib = state_mgr.load_bibliography()
    if not bib:
        typer.echo("No bibliography found. Share papers first with `scholarposter run`.",
                   err=True)
        raise typer.Exit(code=1)

    seed_dois = [e["doi"] for e in bib if e.get("doi")]
    bib_doi_set = set(seed_dois)

    # Parse since year for filtering
    since_year: Optional[int] = None
    if since:
        try:
            since_year = int(since[:4])
        except (ValueError, IndexError):
            typer.echo(f"Invalid --since date: {since}", err=True)
            raise typer.Exit(code=2)

    import httpx as _httpx
    from scholarposter.config import DiscoveryConfig as _DiscoveryConfig
    from scholarposter.discovery.cache import DiscoveryCache as _DiscoveryCache
    effective_cfg = disc_cfg if disc_cfg is not None else _DiscoveryConfig(limit=limit)
    client = _httpx.Client(timeout=10.0)

    # Wire cache — resolved relative to the config file's parent directory
    disc_cache: Optional[_DiscoveryCache] = None
    if effective_cfg.cache_ttl_hours > 0:
        cache_path = config.parent.resolve() / "discovery_cache.json"
        disc_cache = _DiscoveryCache(cache_path)

    results = []
    try:
        for m in resolved_modes:
            if m == "cited-by":
                results.extend(_cited_by(seed_dois, effective_cfg, email, client,
                                         bibliography_dois=bib_doi_set,
                                         cache=disc_cache))
            elif m == "cites":
                results.extend(_cites(seed_dois, effective_cfg, email, client,
                                      bibliography_dois=bib_doi_set,
                                      cache=disc_cache))
    finally:
        client.close()

    # Filter by since_year
    if since_year:
        results = [p for p in results if p.year is None or p.year >= since_year]

    # Rank: deduplicate by DOI (highest score wins), sort descending, top N
    unique = rank(results, effective_cfg.ranking, limit)

    if not unique:
        typer.echo("No new papers found matching your interests.")
        return

    if json_output:
        import dataclasses
        typer.echo(json_mod.dumps([dataclasses.asdict(p) for p in unique], indent=2))
        return

    # Tabular output (120-col, 40-char title truncation unless --wide)
    typer.echo(f"Paper Discovery — {len(unique)} suggestions\n")
    typer.echo(format_table(unique, wide=wide))
    typer.echo()

    # --email-digest
    if email_digest:
        disc_email = effective_cfg.digest_email
        if not disc_email:
            typer.echo(
                "Error: --email-digest requires discovery.digest_email to be set in config.toml",
                err=True,
            )
            raise typer.Exit(code=1)
        # Find SMTP backend from notifications config if available
        smtp_host, smtp_port, from_addr = "localhost", 25, "scholarposter@localhost"
        if cfg:
            for backend in cfg.notifications.backends:
                if backend.type == "email" and backend.smtp_host:
                    smtp_host = backend.smtp_host
                    smtp_port = backend.smtp_port
                    from_addr = backend.from_addr or from_addr
                    break
        try:
            send_digest(
                unique, effective_cfg, date.today(),
                smtp_host=smtp_host, smtp_port=smtp_port, from_addr=from_addr,
            )
            typer.echo(f"Digest sent to {disc_email}.")
        except Exception as e:
            typer.echo(f"Error sending digest: {e}", err=True)
            raise typer.Exit(code=1)

    if effective_cfg.digest_auto and not email_digest:
        disc_email = effective_cfg.digest_email
        if disc_email:
            smtp_host, smtp_port, from_addr = "localhost", 25, "scholarposter@localhost"
            if cfg:
                for backend in cfg.notifications.backends:
                    if backend.type == "email" and backend.smtp_host:
                        smtp_host = backend.smtp_host
                        smtp_port = backend.smtp_port
                        from_addr = backend.from_addr or from_addr
                        break
            try:
                send_digest(
                    unique, effective_cfg, date.today(),
                    smtp_host=smtp_host, smtp_port=smtp_port, from_addr=from_addr,
                )
                logger.info("Auto-digest sent to %s.", disc_email)
            except Exception as e:
                logger.warning("Auto-digest failed: %s", e)
        else:
            logger.warning(
                "digest_auto is true but discovery.digest_email is not set; skipping digest."
            )


@app.command(name="audit")
def audit_cmd(
    config: Path = typer.Option(Path("config.toml"), "--config", help="Path to config.toml"),
    platform: Optional[str] = typer.Option(None, "--platform", help="Filter by platform"),
    since: Optional[str] = typer.Option(None, "--since", help="Filter from date (YYYY-MM-DD)"),
    until: Optional[str] = typer.Option(None, "--until", help="Filter until date (YYYY-MM-DD, inclusive)"),
    status_filter: Optional[str] = typer.Option(None, "--status", help="Filter by status: posted|failed|dry_run"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max records to show"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON-lines"),
    csv_output: bool = typer.Option(False, "--csv", help="Emit CSV"),
) -> None:
    """Query the audit log (FR-92)."""
    import csv
    import io
    import json as json_mod

    try:
        cfg = load_config(config)
    except Exception as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(code=1)

    if not cfg.audit.enabled or cfg.audit.resolved_file is None:
        typer.echo("Audit logging is disabled. Set [audit] enabled = true in config.toml.", err=True)
        raise typer.Exit(code=1)

    audit_path = cfg.audit.resolved_file
    if not audit_path.exists():
        typer.echo("No audit records matching filter.")
        return

    # Parse since/until date boundaries
    since_dt: Optional[datetime] = None
    until_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo(f"Invalid --since date: {since}", err=True)
            raise typer.Exit(code=2)
    if until:
        try:
            until_dt = datetime.fromisoformat(until).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        except ValueError:
            typer.echo(f"Invalid --until date: {until}", err=True)
            raise typer.Exit(code=2)

    # Read and filter records
    records: list[dict] = []
    with open(audit_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json_mod.loads(line)
            except json_mod.JSONDecodeError:
                continue
            if platform and rec.get("platform") != platform:
                continue
            if status_filter and rec.get("status") != status_filter:
                continue
            if since_dt:
                ts_str = rec.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < since_dt:
                        continue
                except (ValueError, TypeError):
                    pass
            if until_dt:
                ts_str = rec.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts > until_dt:
                        continue
                except (ValueError, TypeError):
                    pass
            records.append(rec)

    if limit is not None:
        records = records[:limit]

    if not records:
        typer.echo("No audit records matching filter.")
        return

    if json_output:
        for rec in records:
            typer.echo(json_mod.dumps(rec))
        return

    if csv_output:
        columns = [
            "timestamp", "toot_id", "platform", "status", "doi",
            "llm_backend_used", "summary_chars", "bluesky_likes",
            "bluesky_reposts", "engagement_synced_at",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)
        typer.echo(buf.getvalue().rstrip())
        return

    # Default: tabular output
    # Columns: timestamp | toot_id | platform | status | doi | llm_backend | summary_chars | engagement
    header = f"{'timestamp':<22} {'toot_id':<20} {'platform':<10} {'status':<8} {'doi':<20} {'llm_backend':<12} {'sum_chars':<10} engagement"
    typer.echo(header)
    typer.echo("-" * len(header))
    for rec in records:
        ts = rec.get("timestamp", "")[:19]
        toot_id = str(rec.get("toot_id", ""))[:20]
        plat = str(rec.get("platform", ""))[:10]
        st = str(rec.get("status", ""))[:8]
        doi = str(rec.get("doi") or "")[:20]
        backend = str(rec.get("llm_backend_used") or "")[:12]
        sc = str(rec.get("summary_chars") or "")[:10]
        likes = rec.get("bluesky_likes")
        reposts = rec.get("bluesky_reposts")
        if likes is None and reposts is None:
            engagement = "unsynced"
        else:
            engagement = f"likes={likes} reposts={reposts}"
        typer.echo(
            f"{ts:<22} {toot_id:<20} {plat:<10} {st:<8} {doi:<20} {backend:<12} {sc:<10} {engagement}"
        )


@app.command(name="sync-engagement")
def sync_engagement_cmd(
    config: Path = typer.Option(Path("config.toml"), "--config", help="Path to config.toml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print planned updates without writing"),
    force: bool = typer.Option(False, "--force", help="Re-sync records already synced"),
) -> None:
    """Sync Bluesky likes/reposts into audit.jsonl (FR-93)."""
    import fcntl

    from scholarposter.audit.engagement import sync_engagement

    try:
        cfg = load_config(config)
    except Exception as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(code=1)

    env_path = Path(config).parent.resolve() / ".env"
    load_dotenv(dotenv_path=env_path)

    if not cfg.audit.enabled or cfg.audit.resolved_file is None:
        typer.echo("Audit logging is disabled. Set [audit] enabled = true in config.toml.", err=True)
        raise typer.Exit(code=1)

    audit_path = cfg.audit.resolved_file
    lock_path = audit_path.with_suffix(".lock")

    # Acquire exclusive lock; exit nonzero if another process holds it
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            os.close(lock_fd)
        except (OSError, UnboundLocalError):
            pass  # os.open() failed — lock_fd unbound
        # Do not unlink — the file belongs to the process that holds the lock.
        typer.echo(
            "Could not acquire audit lock — another process is running. "
            "Try again shortly.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        email = os.environ.get("BLUESKY_EMAIL")
        password = os.environ.get("BLUESKY_PASSWORD")
        if not email or not password:
            typer.echo("Missing BLUESKY_EMAIL or BLUESKY_PASSWORD env vars.", err=True)
            raise typer.Exit(code=1)

        from atproto import Client
        client = Client()
        try:
            client.login(email, password)
        except Exception as e:
            typer.echo(f"Bluesky login failed: {_redact(str(e))}", err=True)
            raise typer.Exit(code=1)

        synced, skipped, errors = sync_engagement(
            audit_path=audit_path,
            client=client,
            dry_run=dry_run,
            force=force,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(lock_fd)
        except OSError:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    prefix = "[dry-run] " if dry_run else ""
    typer.echo(
        f"{prefix}Synced engagement for {synced} posts "
        f"({skipped} skipped, {errors} errors)."
    )
    if errors:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# US-016: set-watermark — helper and command
# ---------------------------------------------------------------------------

def _find_watermark_for_date(
    mastodon: Mastodon,
    account_id: int,
    target_date: date,
) -> Optional[int]:
    """Page newest-first through account_statuses to find the last toot before midnight UTC.

    Returns the toot ID (int) of the most recent toot created strictly before
    midnight UTC on target_date, or None if no such toot exists within 500 pages.

    Two distinct None paths:
    - Empty page: account has no toots before the cutoff (or account is empty).
    - 500-page cap: 20,000+ toots exist but none before the cutoff date.
    Both cause the caller to delete last_toot_id from state.
    """
    cutoff = datetime(
        target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc
    )
    max_id: Optional[int] = None
    for _ in range(500):  # hard cap: 500 pages × 40 toots = 20,000 toots
        try:
            kwargs: dict[str, Any] = {"limit": 40}
            if max_id is not None:
                kwargs["max_id"] = max_id
            toots = mastodon.account_statuses(account_id, **kwargs)
        except Exception as e:
            logger.warning(f"set-watermark: API error during date lookup: {e}")
            raise  # caller handles exit-1
        if not toots:
            return None  # empty page → no toot before cutoff
        for toot in toots:
            if toot["created_at"] < cutoff:
                return int(toot["id"])
        max_id = int(toots[-1]["id"])
    return None  # 500-page cap reached without finding a toot before cutoff


@app.command(name="set-watermark")
def set_watermark_cmd(
    config: Path = typer.Option(Path("config.toml"), "--config",
        help="Path to config file."),
    platform: str = typer.Option("all", "--platform",
        help="Platform to update: bluesky, linkedin, or all."),
    toot_id: Optional[int] = typer.Option(None, "--toot-id",
        help="Toot ID to use as watermark (last-processed toot)."),
    toot_url: Optional[str] = typer.Option(None, "--toot-url",
        help="Toot URL; the numeric ID is extracted automatically."),
    date_str: Optional[str] = typer.Option(None, "--date",
        help="YYYY-MM-DD. Requires Mastodon credentials configured in config.toml."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Print what would happen without writing state."),
    yes: bool = typer.Option(False, "--yes", "-y",
        help="Skip confirmation prompt."),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Set the last_toot_id watermark in state.json.

    The next 'run' will process toots strictly after the watermark.
    Exactly one of --toot-id, --toot-url, or --date is required.
    """
    setup_logging(level="DEBUG" if verbose else ("WARNING" if quiet else "INFO"))

    # Validate: exactly one anchor flag required
    anchor_count = sum(x is not None for x in [toot_id, toot_url, date_str])
    if anchor_count != 1:
        typer.echo(
            f"Error: exactly one of --toot-id, --toot-url, or --date is required\n\n"
            f"{_SET_WATERMARK_USAGE}",
            err=True,
        )
        raise typer.Exit(code=2)

    # Validate platform
    if platform not in VALID_PLATFORMS:
        typer.echo("Error: --platform must be bluesky, linkedin, or all", err=True)
        raise typer.Exit(code=2)

    platforms_to_write = ["bluesky", "linkedin"] if platform == "all" else [platform]

    # --- Load config once (needed for StateManager in all modes; mastodon in --date mode) ---
    try:
        cfg = load_config(config)
    except Exception as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(code=1)

    state_dir = config.parent.resolve()
    state_mgr = StateManager(
        state_dir=state_dir,
        state_file=Path(cfg.state.state_file).name,
        bibliography_file=cfg.state.bibliography_file,
    )

    # --- Resolve the watermark ID ---
    resolved_id: Optional[int]

    if toot_id is not None:
        resolved_id = toot_id

    elif toot_url is not None:
        m = _TOOT_URL_RE.match(toot_url)
        if not m:
            typer.echo(f"Cannot parse toot ID from URL: {toot_url}", err=True)
            raise typer.Exit(code=2)
        resolved_id = int(m.group(1))

    else:  # date_str
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()  # type: ignore[arg-type]
        except ValueError:
            typer.echo(
                f"Invalid date format: {date_str!r}. Expected YYYY-MM-DD", err=True
            )
            raise typer.Exit(code=2)

        mastodon_client = _build_mastodon_client(cfg)
        account_id = mastodon_client.me()["id"]
        try:
            resolved_id = _find_watermark_for_date(mastodon_client, account_id, target)
        except Exception as e:
            typer.echo(f"Error fetching timeline: {e}", err=True)
            raise typer.Exit(code=1)

    # --- Dry-run: print and return without touching state or lock ---
    if dry_run:
        for plat in platforms_to_write:
            typer.echo(f"[dry-run] Would set {plat} last_toot_id = {resolved_id}")
        return

    # --- Confirmation prompt ---
    if not yes:
        for plat in platforms_to_write:
            confirmed = typer.confirm(
                f"Set watermark for {plat}: last_toot_id = {resolved_id}\n"
                f"The next 'run' will process toots after this. Continue?"
            )
            if not confirmed:
                return

    # --- Acquire lock ---
    if not state_mgr.acquire_lock():
        typer.echo("Another scholarposter process is running", err=True)
        raise typer.Exit(code=1)

    try:
        for plat in platforms_to_write:
            state_mgr.update_platform_state(plat, PlatformState(last_toot_id=resolved_id))
            typer.echo(f"Set {plat} last_toot_id = {resolved_id}")
    finally:
        state_mgr.release_lock()
