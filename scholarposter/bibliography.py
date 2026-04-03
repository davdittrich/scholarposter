"""Bibliography export: BibTeX and Markdown formatting."""
from __future__ import annotations

import re

from scholarposter.models import BibliographyEntry

_BIBTEX_SPECIAL = str.maketrans({
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}", "\\": r"\textbackslash{}",
})


def _escape_bibtex(s: str) -> str:
    """Escape LaTeX/BibTeX special characters."""
    return s.translate(_BIBTEX_SPECIAL)


def _bibtex_key(doi: str) -> str:
    """Generate a valid BibTeX key from DOI. Always starts with 'doi_'."""
    return "doi_" + re.sub(r"[^a-zA-Z0-9]", "_", doi)


def to_bibtex(entries: list[BibliographyEntry]) -> str:
    """Format bibliography entries as BibTeX."""
    lines: list[str] = []
    for entry in entries:
        key = _bibtex_key(entry.doi)
        year = str(entry.publication_year or entry.shared_at.year)
        authors = " and ".join(_escape_bibtex(a) for a in entry.authors) if entry.authors else ""
        escaped_doi = _escape_bibtex(entry.doi)
        lines.append(f"@article{{{key},")
        if authors:
            lines.append(f"  author = {{{authors}}},")
        lines.append(f"  title = {{{_escape_bibtex(entry.title)}}},")
        lines.append(f"  year = {{{year}}},")
        lines.append(f"  doi = {{{escaped_doi}}},")
        lines.append(f"  url = {{https://doi.org/{escaped_doi}}},")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def to_markdown(entries: list[BibliographyEntry]) -> str:
    """Format bibliography entries as Markdown reading list."""
    lines = ["# Shared Papers\n"]
    for entry in entries:
        authors = ", ".join(entry.authors) if entry.authors else "Unknown"
        year = entry.publication_year or entry.shared_at.year
        lines.append(f"- **{entry.title}** ({year}) — {authors}")
        lines.append(f"  DOI: [{entry.doi}](https://doi.org/{entry.doi})")
        if entry.abstract:
            snippet = entry.abstract[:200] + ("…" if len(entry.abstract) > 200 else "")
            lines.append(f"  *{snippet}*")
        lines.append("")
    return "\n".join(lines)
