# Fourth-Pass Fixes — Remaining PRD Gaps

## Context

Full PRD audit completed against `tasks/prd-mastodon-crosspost-rewrite.md`. Three implementation
passes have run. This plan addresses the remaining confirmed gaps excluding video-related features
(FR-27a, FR-33, FR-21a), which are explicitly deferred.

---

## Confirmed Gaps

| ID | Requirement | Current State |
|----|-------------|---------------|
| FR-21 | Use `urllib.parse.urljoin` for relative URL resolution | No `urljoin` call anywhere in codebase; `enrichment/html.py` extracts OG `image` as-is (may be relative path) |
| FR-10 | `skip_content_types` supports `media_only` | Only `sensitive` and `poll` handled; `media_only` not checked |
| US-010 | `status` subcommand shows pending count | Only shows `last_toot_id`, `last_status`, `last_error`; no pending count |
| FR-43 | `signal-cli` notification backend | Stub: `raise NotImplementedError` |
| FR-44 | `email` notification backend | Stub: `raise NotImplementedError` |

### Explicitly Deferred (not in this plan)

| ID | Reason |
|----|--------|
| FR-27a | Bluesky video upload — requires new Bluesky video service API integration |
| FR-33 | LinkedIn video upload — requires chunked `/rest/videos` API integration |
| FR-21a | Video compression/remux — requires PyAV transcoding pipeline |
| FR-28 | Thread rollback delete — requires AT Protocol `deleteRecord` per chunk |

---

## Changes

### Fix 1 — FR-21: Resolve relative URLs with `urljoin`

**Problem**: The OG `image` tag from HTML extraction may contain a relative path (e.g.,
`/images/thumb.jpg`). When stored in `LinkEnrichment.thumbnail_url`, this relative path is later
passed to `download_media()` which will fail (httpx needs an absolute URL).

**Analysis**: The only place where relative URLs can enter the system is in
`enrichment/pipeline.py:_enrich_html()` — OG tags extracted from HTML may contain relative paths.
The toot URLs themselves are always absolute (Mastodon API returns full URLs). The PDF path also
uses absolute URLs (downloaded via `download_media()` which requires absolute URLs).

**File**: `scholarposter/enrichment/pipeline.py`

**Change**: After extracting OG tags in `_enrich_html()`, resolve any relative URLs against the
page URL:

```python
from urllib.parse import urljoin

# In _enrich_html(), after og = extract_og_tags(html):
if og.get("image"):
    updates["thumbnail_url"] = urljoin(url, og["image"])
```

The `urljoin` call is a no-op for absolute URLs (`urljoin("https://a.com/page", "https://b.com/img.jpg")`
returns `"https://b.com/img.jpg"`), so it's safe to apply unconditionally.

**Tests** (in `tests/test_enrichment_pipeline.py`):
```python
def test_enrich_html_resolves_relative_og_image(self):
    """OG image with relative path is resolved against page URL."""
    # Mock HTML with relative og:image
    # Assert link.thumbnail_url is absolute
```

---

### Fix 2 — FR-10: Support `media_only` in `skip_content_types`

**Problem**: PRD FR-10 specifies `skip_content_types` should support `media_only` — a toot that
has media attachments but no meaningful text content. Currently only `sensitive` and `poll` are
handled.

**Definition of `media_only`**: A toot where `text` (after HTML stripping) is empty or contains
only URLs and/or hashtags — i.e., no prose content beyond links and tags. This is common for
image-only posts where the author just shares a photo without commentary.

**File**: `scholarposter/models.py`

Add `import re` to the module-level imports (not inside the property — avoids per-call overhead
and follows the codebase convention of top-level imports).

Add a computed property to `UnifiedPost`:

```python
import re  # add to module-level imports

_URL_STRIP_RE = re.compile(r'https?://[^\s]+')
_TAG_STRIP_RE = re.compile(r'#\S+')

# ... inside UnifiedPost class:

@property
def is_media_only(self) -> bool:
    """True if post has media but no meaningful text content (only URLs/hashtags)."""
    if not self.media:
        return False
    # Strip URLs and hashtags from text, check if anything remains
    stripped = _URL_STRIP_RE.sub('', self.text)
    stripped = _TAG_STRIP_RE.sub('', stripped)
    return stripped.strip() == ""
```

**File**: `scholarposter/filters.py`

Add `media_only` check after the existing `poll` check:

```python
if "media_only" in cfg.skip_content_types and post.is_media_only:
    return FilterResult(passed=False, reason="skip_content_type: media_only")
```

**Tests** (in `tests/test_filters.py`):
```python
def test_media_only_filter_skips_media_without_text():
    # post.text = "https://example.com #photo", post.media = [MediaAttachment(...)]
    # filter with skip_content_types=["media_only"]
    # assert not passed

def test_media_only_filter_passes_when_text_present():
    # post.text = "Check out this paper https://example.com", post.media = [...]
    # assert passed

def test_media_only_filter_passes_when_no_media():
    # post.text = "", post.media = []
    # assert passed (not media_only — no media at all)
```

**Tests** (in `tests/test_models.py`):
```python
def test_is_media_only_true_for_url_only_text():
    # text = "https://example.com", media = [MediaAttachment(...)]
    # assert post.is_media_only is True

def test_is_media_only_true_for_hashtag_only_text():
    # text = "#photo #science", media = [MediaAttachment(...)]
    # After stripping hashtags, text is empty → is_media_only = True
    # assert post.is_media_only is True

def test_is_media_only_true_for_whitespace_only_text():
    # text = "   ", media = [MediaAttachment(...)]
    # After strip(), text is empty → is_media_only = True
    # assert post.is_media_only is True

def test_is_media_only_false_with_prose():
    # text = "Great paper! https://example.com", media = [MediaAttachment(...)]
    # assert post.is_media_only is False

def test_is_media_only_false_without_media():
    # text = "", media = []
    # assert post.is_media_only is False (no media = not media_only)
```

---

### Fix 3 — US-010: `status` subcommand shows pending count

**Problem**: PRD US-010 AC says: "`status` subcommand: shows last processed toot ID per platform,
pending count, last error." The pending count requires knowing how many toots exist between
`last_toot_id` and the current newest toot — which requires a Mastodon API call.

**Design**: The `status` command currently takes only `--config` and reads `state.json`. To show
pending count, it needs to:
1. Load Mastodon credentials from config
2. Call `mastodon.me()` to get `user_id`
3. Call `account_statuses(user_id, min_id=last_toot_id, limit=50)` per platform
4. Report `len(toots)` as pending count (capped at 50 since that's the API page limit)

This is an optional enhancement — the `status` command must still work without network access
(e.g., when credentials are invalid or the instance is down). The pending count should be shown
as `pending=?` when the API call fails.

**File**: `scholarposter/cli.py`

Modify the `status()` command:

```python
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
                    kwargs = {"exclude_replies": True, "limit": 50, "min_id": last_id}
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
```

**Tests** (in `tests/test_cli.py`):
```python
def test_status_shows_pending_count(self, tmp_path):
    """status subcommand shows pending=N when API available."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(_BSKY_TOML)
    state_file = tmp_path / "state.json"
    # Pre-populate state
    from scholarposter.state import StateManager as RealSM
    sm = RealSM(state_file=str(state_file))
    from scholarposter.models import PlatformState
    sm.update_platform_state("bluesky", PlatformState(last_toot_id=100, last_status="posted"))

    with (
        patch("scholarposter.cli.load_config") as mock_cfg,
        patch("scholarposter.cli.StateManager") as mock_sm_cls,
        patch("scholarposter.cli.Mastodon") as mock_masto_cls,
    ):
        mock_cfg.return_value.state.state_file = str(state_file)
        mock_cfg.return_value.mastodon.credentials_file = "t.secret"
        mock_cfg.return_value.mastodon.instance = "https://fediscience.org"
        mock_sm_cls.return_value = RealSM(state_file=str(state_file))
        mock_masto = MagicMock()
        mock_masto.me.return_value = {"id": "42"}
        mock_masto.account_statuses.return_value = [{}, {}, {}]  # 3 pending
        mock_masto_cls.return_value = mock_masto
        result = runner.invoke(app, ["status", "--config", str(config_file)])
    assert "pending=3" in result.output

def test_status_shows_pending_unknown_on_api_failure(self, tmp_path):
    """status subcommand shows pending=? when API unavailable."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(_BSKY_TOML)
    state_file = tmp_path / "state.json"
    from scholarposter.state import StateManager as RealSM
    sm = RealSM(state_file=str(state_file))
    from scholarposter.models import PlatformState
    sm.update_platform_state("bluesky", PlatformState(last_toot_id=100, last_status="posted"))

    with (
        patch("scholarposter.cli.load_config") as mock_cfg,
        patch("scholarposter.cli.StateManager") as mock_sm_cls,
        patch("scholarposter.cli.Mastodon") as mock_masto_cls,
    ):
        mock_cfg.return_value.state.state_file = str(state_file)
        mock_cfg.return_value.mastodon.credentials_file = "t.secret"
        mock_cfg.return_value.mastodon.instance = "https://fediscience.org"
        mock_sm_cls.return_value = RealSM(state_file=str(state_file))
        mock_masto_cls.side_effect = Exception("auth failed")
        result = runner.invoke(app, ["status", "--config", str(config_file)])
    assert "pending=?" in result.output
```

---

### Fix 4 — FR-43: Implement signal-cli notification backend

**Problem**: `notifications/signal.py` is a stub that raises `NotImplementedError`. The PRD says
FR-43 should "send message via signal-cli REST API or dbus." Per US-006: "At least one backend
must be implemented at launch; others can be stubs." ntfy is the primary backend. However, since
signal-cli has a well-known REST API, a minimal implementation is straightforward.

**Design**: signal-cli exposes a REST API (typically at `http://localhost:8080`) when run in
daemon mode. The endpoint is `POST /v2/send` with a JSON body. The required config fields are
`phone_number` (sender) and `recipients` (list of recipient phone numbers).

**File**: `scholarposter/notifications/signal.py`

```python
"""Signal notification backend via signal-cli REST API."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from loguru import logger

from scholarposter.notifications.base import BaseNotifier


class SignalNotifier(BaseNotifier):
    def __init__(self, api_url: str, phone_number: str, recipients: list[str]):
        self._api_url = api_url.rstrip("/")
        self._phone_number = phone_number
        self._recipients = recipients

    def notify(self, platform: str, toot_id: str, error: str) -> None:
        message = (
            f"[{datetime.now(timezone.utc).isoformat()}] "
            f"Cross-post to {platform} failed for toot {toot_id}: {error}"
        )
        try:
            httpx.post(
                f"{self._api_url}/v2/send",
                json={
                    "message": message,
                    "number": self._phone_number,
                    "recipients": self._recipients,
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Signal notification failed: {e}")
```

**File**: `scholarposter/config.py`

Add optional signal-cli fields to `NotificationBackendConfig`:

```python
class NotificationBackendConfig(BaseModel):
    model_config = {"extra": "allow"}

    type: str
    topic: Optional[str] = None
    server: Optional[str] = None
    # signal-cli fields
    api_url: Optional[str] = None
    phone_number: Optional[str] = None
    recipients: list[str] = []
```

**File**: `scholarposter/cli.py`

Add signal-cli case to `_build_notifiers()`:

```python
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
```

**Existing tests that must be updated**:

1. `tests/test_notifications.py:51-54` — `TestStubNotifiers.test_signal_raises_not_implemented`:
   Delete this test. `SignalNotifier` is no longer a stub and its constructor requires arguments.
   Replace with the new `TestSignalNotifier` tests below.

2. `tests/test_cli.py:104-107` — `test_signal_type_skipped`: Update to assert that signal
   with **incomplete** config (no `api_url`) returns empty list. The test already passes
   `type="signal"` with no other fields, which will hit the new "missing api_url" warning path
   and return `[]`. The test assertion (`len == 0`) stays correct, but rename to
   `test_signal_type_skipped_without_required_fields` for clarity.

3. `tests/test_cli.py:114-122` — `test_unknown_type_warning_message`: Change the test input from
   `type="signal"` to `type="fax_machine"` (signal is no longer an unknown type; `carrier_pigeon`
   is already used by `test_unknown_type_skipped` at line 100). The assertion
   ("Unknown notification backend type") stays correct.

**New tests** (in `tests/test_notifications.py`):
```python
class TestSignalNotifier:
    @respx.mock
    def test_sends_notification(self):
        respx.post("http://localhost:8080/v2/send").mock(
            return_value=httpx.Response(200)
        )
        notifier = SignalNotifier(
            api_url="http://localhost:8080",
            phone_number="+1234567890",
            recipients=["+0987654321"],
        )
        notifier.notify("bluesky", "113456789", "API error")
        assert respx.calls.call_count == 1
        body = respx.calls.last.request.content.decode()
        assert "bluesky" in body

    @respx.mock
    def test_failure_does_not_raise(self):
        # Use ConnectError (transport-level), not HTTP 500 (which doesn't raise)
        respx.post("http://localhost:8080/v2/send").mock(
            side_effect=httpx.ConnectError("refused")
        )
        notifier = SignalNotifier(
            api_url="http://localhost:8080",
            phone_number="+1234567890",
            recipients=["+0987654321"],
        )
        # Should not raise
        notifier.notify("bluesky", "123", "error")
```

**New tests** (in `tests/test_cli.py`):
```python
def test_signal_backend_created_with_config(self):
    # NotificationBackendConfig with type="signal", api_url, phone_number, recipients
    # Assert SignalNotifier instance returned

def test_signal_backend_skipped_without_api_url(self):
    # NotificationBackendConfig with type="signal" but no api_url
    # Assert empty list returned
```

---

### Fix 5 — FR-44: Implement email notification backend

**Problem**: `notifications/email.py` is a stub. The PRD says FR-44 should "send via SMTP with
configurable server, port, credentials."

**Design**: Use Python's stdlib `smtplib` and `email.message` — no new dependencies. Config
fields: `smtp_host`, `smtp_port`, `from_addr`, `to_addr`, `smtp_user` (optional), `smtp_password`
(optional, from env var).

**File**: `scholarposter/notifications/email.py`

```python
"""Email notification backend via SMTP."""
from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from loguru import logger

from scholarposter.notifications.base import BaseNotifier


class EmailNotifier(BaseNotifier):
    def __init__(self, smtp_host: str, smtp_port: int, from_addr: str, to_addr: str):
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._from_addr = from_addr
        self._to_addr = to_addr

    def notify(self, platform: str, toot_id: str, error: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"scholarposter failure: {platform}"
        msg["From"] = self._from_addr
        msg["To"] = self._to_addr
        msg.set_content(
            f"[{datetime.now(timezone.utc).isoformat()}] "
            f"Cross-post to {platform} failed for toot {toot_id}: {error}"
        )
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                server.ehlo()
                if self._smtp_port == 587:
                    server.starttls()
                user = os.environ.get("SMTP_USER")
                password = os.environ.get("SMTP_PASSWORD")
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
        except Exception as e:
            logger.warning(f"Email notification failed: {e}")
```

**File**: `scholarposter/config.py`

Add optional email fields to `NotificationBackendConfig`:

```python
class NotificationBackendConfig(BaseModel):
    model_config = {"extra": "allow"}

    type: str
    topic: Optional[str] = None
    server: Optional[str] = None
    # signal-cli fields
    api_url: Optional[str] = None
    phone_number: Optional[str] = None
    recipients: list[str] = []
    # email fields
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    from_addr: Optional[str] = None
    to_addr: Optional[str] = None
```

**File**: `scholarposter/cli.py`

Add email case to `_build_notifiers()`:

```python
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
```

**Existing tests that must be updated**:

1. `tests/test_notifications.py:56-59` — `TestStubNotifiers.test_email_raises_not_implemented`:
   Delete this test (and the entire `TestStubNotifiers` class since both tests are removed).
   `EmailNotifier` is no longer a stub and its constructor requires arguments.
   Replace with the new `TestEmailNotifier` tests below.

2. `tests/test_cli.py:109-112` — `test_email_type_skipped`: Update to assert that email
   with **incomplete** config (no `smtp_host`) returns empty list. Same behavior as before
   (returns `[]`), but rename to `test_email_type_skipped_without_required_fields` for clarity.

**New tests** (in `tests/test_notifications.py`):
```python
class TestEmailNotifier:
    def test_sends_message(self):
        with patch("scholarposter.notifications.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            notifier = EmailNotifier(smtp_host="smtp.test", smtp_port=587,
                                     from_addr="a@b.com", to_addr="c@d.com")
            notifier.notify("bluesky", "123", "API error")
            mock_server.send_message.assert_called_once()

    def test_uses_starttls_on_port_587(self):
        with patch("scholarposter.notifications.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            notifier = EmailNotifier(smtp_host="smtp.test", smtp_port=587,
                                     from_addr="a@b.com", to_addr="c@d.com")
            notifier.notify("bluesky", "123", "error")
            mock_server.starttls.assert_called_once()

    def test_failure_does_not_raise(self):
        # Patch at class level so the constructor itself raises
        with patch("scholarposter.notifications.email.smtplib.SMTP",
                   side_effect=ConnectionRefusedError("refused")):
            notifier = EmailNotifier(smtp_host="smtp.test", smtp_port=587,
                                     from_addr="a@b.com", to_addr="c@d.com")
            # Should not raise — exception caught, logger.warning emitted
            notifier.notify("bluesky", "123", "error")
```

**New tests** (in `tests/test_cli.py`):
```python
def test_email_backend_created_with_config(self):
    # NotificationBackendConfig with type="email", smtp_host, from_addr, to_addr
    # Assert EmailNotifier instance returned

def test_email_backend_skipped_without_smtp_host(self):
    # NotificationBackendConfig with type="email" but no smtp_host
    # Assert empty list returned
```

---

## Files Changed

| File | Changes |
|------|---------|
| `scholarposter/enrichment/pipeline.py` | Fix 1: `urljoin` for relative OG image URLs |
| `scholarposter/models.py` | Fix 2: `is_media_only` property on `UnifiedPost` |
| `scholarposter/filters.py` | Fix 2: `media_only` content type check |
| `scholarposter/cli.py` | Fix 3: pending count in `status`; Fix 4+5: signal/email in `_build_notifiers` |
| `scholarposter/config.py` | Fix 4+5: signal-cli and email fields on `NotificationBackendConfig` |
| `scholarposter/notifications/signal.py` | Fix 4: signal-cli REST API implementation |
| `scholarposter/notifications/email.py` | Fix 5: SMTP email implementation |
| `tests/test_enrichment_pipeline.py` | Fix 1 test |
| `tests/test_filters.py` | Fix 2 tests |
| `tests/test_models.py` | Fix 2 tests |
| `tests/test_cli.py` | Fix 3+4+5 tests |
| `tests/test_notifications.py` | Fix 4+5 tests |

---

## Existing Tests Requiring Updates

These existing tests will break without modification and MUST be updated as part of this plan:

| Test | File:Line | Issue | Resolution |
|------|-----------|-------|------------|
| `TestStubNotifiers.test_signal_raises_not_implemented` | `test_notifications.py:51-54` | `SignalNotifier()` constructor now requires args → `TypeError` | Delete; replace with `TestSignalNotifier` class |
| `TestStubNotifiers.test_email_raises_not_implemented` | `test_notifications.py:56-59` | `EmailNotifier()` constructor now requires args → `TypeError` | Delete; replace with `TestEmailNotifier` class |
| `test_unknown_type_warning_message` | `test_cli.py:114-122` | Uses `type="signal"` which now hits the new `elif` branch | Change input to `type="fax_machine"` (not `carrier_pigeon` — that's already used by `test_unknown_type_skipped` at line 100) |
| `test_signal_type_skipped` | `test_cli.py:104-107` | Still passes (incomplete config returns `[]`) but name is misleading | Rename to `test_signal_type_skipped_without_required_fields` |
| `test_email_type_skipped` | `test_cli.py:109-112` | Still passes (incomplete config returns `[]`) but name is misleading | Rename to `test_email_type_skipped_without_required_fields` |
| `test_status_shows_last_error` | `test_cli.py:632-654` | New `status()` code adds `Mastodon()` instantiation inside `try/except`; test doesn't mock it — `Mastodon()` will raise (caught silently), causing `pending=?` in output | Add `patch("scholarposter.cli.Mastodon")` to the `with` block. The mock doesn't need configuration — the `Mastodon` constructor won't be called because the `except Exception: pass` in `status()` catches any error. The existing assertions (`last_error=...` in output) remain valid since the pending count gracefully degrades to `?`. |

---

## Invariants

- `urljoin` is a no-op for absolute URLs — no regression on existing OG image handling
- `is_media_only` returns False when no media — empty-text posts without media are NOT filtered
- `status` command works without network (pending count = "?" on API failure)
- Signal and email backends follow the same pattern as ntfy: log warnings on failure, never crash
- Signal failure tests use `httpx.ConnectError` (transport-level), not HTTP 500 (which doesn't raise in httpx)
- SMTP credentials come from env vars (`SMTP_USER`, `SMTP_PASSWORD`), not config file (FR-49)
- All existing tests continue to pass (283 → ≥ 305 tests)
- Coverage ≥ 90%
