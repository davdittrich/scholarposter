# Implementation Plan: GitHub Optimization

## Goal
Transform the GitHub repository into a high-trust, high-impact landing page that drives stars and organic usage.

## Phase 1: Visual & Branding (Immediate)
- [ ] **GIF Demo:** Create a short (10s) GIF of the `scholarposter` CLI fetching a post and cross-posting. Place it at the top of the README.
- [x] **Badges:** Add a row of badges to the README:
  - [x] GitHub Stars / Forks
  - [ ] CI Build Status (GitHub Actions) - *Pending CI setup*
  - [x] Code Coverage (from `.coverage-thresholds.json`)
  - [x] License (MIT)
  - [x] Python Version (3.11+)
- [x] **GitHub Topics:** Update the repository "About" section with exhaustive tags.

## Phase 2: Documentation Clarity (Mid-Term)
- [ ] **One-Liner Hook:** Refine the top sentence of the README to: *"The Research Impact Multiplier: One-click cross-posting from Mastodon to Bluesky, LinkedIn, and X for Scholars."*
- [ ] **"Why ScholarPoster?" Section:** Add a dedicated section explaining the problem of "platform fragmentation" for academics.
- [ ] **Success Stories:** Add `docs/SUCCESS_STORIES.md` with testimonials or screenshots of successful cross-posts.

## Phase 3: Community & Maintenance (Long-Term)
- [ ] **`CONTRIBUTING.md`:** Outline how others can add new adapters (e.g., Threads, Reddit).
- [ ] **`ROADMAP.md`:** Show future plans (Zotero integration, ORCID sync, AI-driven post-shortening for LinkedIn).
- [ ] **GitHub Action for Releases:** Automate the creation of GitHub Releases with changelogs.

## Acceptance Criteria
1. [x] README has at least 5 badges.
2. [ ] README has a visual element (GIF/Image).
3. [x] GitHub "About" section has 10+ relevant topics.
4. [ ] `CONTRIBUTING.md` and `ROADMAP.md` are present in the repo root.
