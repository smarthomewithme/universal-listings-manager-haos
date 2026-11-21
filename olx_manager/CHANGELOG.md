# Changelog

## 1.1.0

- Install and update through a Home Assistant Apps repository.
- Build and test an amd64 image before publishing to GHCR. Promote the Home Assistant update only after an anonymous pull and a smoke test of that image.
- Add Polish installation, backup, restore and update instructions to the app's Documentation tab.
- Keep the `olx_manager` slug, existing settings and SQLite schema. Same-installation updates preserve drafts, photo albums, audit events and unexpired approvals.

## 1.0.0

- Polish mobile panel, photo albums, versioned listing approvals, SQLite, MCP and the OLX adapter with a demo mode.
