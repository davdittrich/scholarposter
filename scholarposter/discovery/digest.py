"""Discovery digest formatting and email dispatch (US-014)."""
from __future__ import annotations

import smtplib
from datetime import date
from email.message import EmailMessage
from email.utils import parseaddr
from loguru import logger

from scholarposter.config import DiscoveryConfig
from scholarposter.discovery import CandidatePaper

_TITLE_WIDTH = 40


def format_table(papers: list[CandidatePaper], wide: bool = False) -> str:
    """Format papers as a plain-text 120-col table.

    Titles truncated at 40 chars (+ "…") unless wide=True.
    Returns empty string for empty input.
    """
    if not papers:
        return ""
    title_col = 60 if wide else _TITLE_WIDTH
    lines: list[str] = []
    for i, p in enumerate(papers, 1):
        if wide or len(p.title) <= title_col:
            title = p.title
        else:
            title = p.title[:title_col] + "…"
        oa_tag = "[OA]" if p.is_oa else "    "
        year_str = str(p.year) if p.year else "????"
        lines.append(f"{i:2}. {title}")
        lines.append(
            f"    {oa_tag} {year_str} | Cited: {p.cited_by_count:4d} | DOI: {p.doi} | mode: {p.mode}"
        )
    return "\n".join(lines)


def send_digest(
    papers: list[CandidatePaper],
    config: DiscoveryConfig,
    digest_date: date,
    *,
    smtp_host: str = "localhost",
    smtp_port: int = 25,
    from_addr: str = "scholarposter@localhost",
) -> None:
    """Send a discovery digest email.

    Re-validates digest_email via parseaddr() before opening any SMTP connection
    to prevent SMTP header injection (FR-88).

    Args:
        papers: ranked candidate papers to include in digest
        config: DiscoveryConfig with digest_email set
        digest_date: date label for the subject line
        smtp_host: SMTP server hostname (default localhost)
        smtp_port: SMTP server port (default 25)
        from_addr: From address for the email
    """
    _, to_addr = parseaddr(config.digest_email or "")
    if not to_addr:
        raise ValueError(
            f"digest_email is not a valid address — cannot send digest: {config.digest_email!r}"
        )

    subject = (
        f"scholarposter discovery digest — {digest_date}: {len(papers)} new candidates"
    )
    body = format_table(papers) + "\n"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.send_message(msg)
    except Exception as e:
        logger.warning(f"Discovery digest email failed: {e}")
        raise
