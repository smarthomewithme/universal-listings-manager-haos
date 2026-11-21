# Release engineering

Push changes to `development`, or open a pull request against it. Both paths run lint, dependency checks, Python and browser tests, an amd64 Docker build and an in-container smoke test. A push to `development` with a strictly higher app version additionally prepares a release. `main` is the default branch read by Home Assistant.

## Publish an update

1. Bump `olx_manager/config.yaml` and `olx_manager/app/VERSION` to the same stable SemVer. Add a matching section to `olx_manager/CHANGELOG.md`. Leave the generated `image` field out of development.
2. Push to `development`. The workflow tests the candidate and upgrades an isolated volume seeded by the last stable image. The first release uses the preserved `baseline-v1.0.0` branch or the local `v1.0.0` tag. All Docker tests run with networking disabled and use the OLX simulator.
3. The publisher transfers the exact tested image, checks for an existing GHCR tag with write-authorized credentials, and publishes a version tag once. It checks the anonymous manifest digest, pulls with separate empty Docker credentials, verifies the platform and source identity, and then runs another container smoke test.
4. After those checks, an atomic Git push promotes the release to `main` and creates the version tag. The default branch gains the actual GHCR image path and the installation button. GitHub Release notes are generated from the app changelog. A build or smoke-test failure leaves the stable branch unchanged.

GitHub Packages initially creates new containers as **Private**, including for public repositories. For the first release, an owner opens the package settings and changes its visibility to **Public**. The workflow posts the exact package link in its summary if the anonymous check cannot pass. Then use **Re-run failed jobs** on the same GitHub Actions run. That preserves the published tag and reruns the anonymous checks. Do not change the candidate commit between the first push and the visibility retry.

If a version tag already exists in GHCR, the workflow resumes only for an image with the same source revision and version. Use a new version for a changed candidate. Keep existing release tags and existing GHCR version manifests for Home Assistant rollback. GitHub administrators should also avoid overwriting package tags manually. Use the same repository URL, default branch and app slug permanently.

## Compatibility and data

For an incompatible upgrade, add its target version to `breaking_versions` in the app config and document the required preparation and recovery. Home Assistant uses that list to avoid automatically updating across the break. Add and test transactional migrations. Before a manual update, the owner makes a backup through Home Assistant. Restore the app and its data from the same backup when rolling back across a schema change.

The tested upgrade sequence uses the app's own data directory. A local `local_olx_manager` install and a GitHub install have different Supervisor IDs. Their data is isolated. A separate cross-ID migration is required if the owner previously used a local install.

- [Home Assistant repository structure](https://contributors.home-assistant.io/apps/guides/repository/)
- [Home Assistant app publishing](https://contributors.home-assistant.io/apps/guides/publishing/)
- [GitHub package visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
