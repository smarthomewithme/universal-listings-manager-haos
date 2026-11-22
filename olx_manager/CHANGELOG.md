# Changelog

## 1.4.0

- Add ChatGPT-native file attachment input to the existing central Home Assistant MCP
  with the documented openai/fileParams contract; no second plugin, domain or port.
- Stream signed file downloads through a public-address-only resolver and strict HTTPS,
  no redirects, a 12 MB cap and matching JPG/PNG/WebP magic checks.
- Reuse the existing encrypted ULM Photo Bridge, original-image SHA-256 receipts,
  revision locking, cover-photo selection, quota and private owner approval rules.
- A minimal scoped HA Core 2026.11 MCP formatter compatibility hook adds file metadata
  only for ulm_attach_chat_photos and removes itself on integration unload.
- This integration targets Home Assistant Core 2026.11.x. Other Core revisions require
  verifying the private MCP formatter seam before promoting an update.


## 1.3.0

- Add four write-only-in-policy photo transfer tools to the existing Home Assistant MCP.
  Byte-capable clients can resume authenticated encrypted uploads with SHA-256 receipts.
- Preserve exact JPG, PNG and WebP originals, generate previews and choose a cover without
  changing listing fields. The draft revision and approval rules apply at finalization.
- Accept private price suggestions through the central draft creation tool.
- Add a SQLite schema migration for pending uploads. Restore a pre-update backup to roll
  back to 1.2.x.

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
