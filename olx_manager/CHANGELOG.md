# Changelog

## 1.2.0

- Add a companion Home Assistant integration for HACS. Register 14 ULM tools in the existing
  Home Assistant MCP instance, with native read-only filtering and an explicit admin account.
- Connect Core to the ULM app over a separate authenticated private route. Keep owner approvals
  in ULM and permit only simulated publication through the central tool set.
- Add connection testing, credential redaction and an end-to-end acceptance job using official
  Home Assistant Core. The app's existing panel, MCP endpoint and data format are preserved.

## 1.1.0

- Install and update through a Home Assistant Apps repository.
- Build and test an amd64 image before publishing to GHCR. Promote the Home Assistant update only after an anonymous pull and a smoke test of that image.
- Add Polish installation, backup, restore and update instructions to the app's Documentation tab.
- Keep the `olx_manager` slug, existing settings and SQLite schema. Same-installation updates preserve drafts, photo albums, audit events and unexpired approvals.

## 1.0.0

- Polish mobile panel, photo albums, versioned listing approvals, SQLite, MCP and the OLX adapter with a demo mode.
