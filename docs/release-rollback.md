<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Release Rollback & Unpublish Runbook

This runbook describes how maintainers respond when a bad Node Wire release
reaches PyPI or GitHub. Read it together with
[packaging.md](packaging.md) and [versioning.md](versioning.md).

## PyPI constraints (read first)

- **Published versions cannot be overwritten.** Uploading the same version again
  will fail.
- **Prefer yanking** over deleting a release. Yanking hides a version from the
  default `pip install` resolver while leaving it available for explicit pins.
- **Deletion is exceptional** — use only for legal, trademark, or severe
  security cases, and coordinate with PyPI support if needed.
- **Sigstore attestations** published with a release remain on the public
  transparency log; yanking does not revoke them.

## When to use this runbook

| Scenario | Typical response |
|---|---|
| Broken wheel / install failure | Yank + patch release |
| Critical security vulnerability in published code | Yank + advisory + patch/hotfix |
| Wrong version tagged (metadata only) | Yank if published; fix tag/docs |
| Secrets committed and released | Yank + rotate secrets + history remediation |
| Non-security functional regression | Patch release; yank only if install is unsafe |

## Roles

- **Release maintainer** — executes PyPI yank, publishes corrective release,
  updates GitHub release/tag state.
- **Security contact** — triages severity, coordinates advisory if needed
  ([SECURITY.md](https://github.com/AOT-Technologies/node-wire/blob/main/SECURITY.md)).
- **Comms** — notifies users via GitHub Discussions, release notes, or advisory.

## Step 1 — Triage and freeze

1. Confirm which **package(s)** and **version(s)** are affected (nine publishable
   packages; see [packaging.md](packaging.md#package-inventory)).
2. Record the Git tag (`vX.Y.Z`), commit SHA, and PyPI project name(s).
3. **Stop further publishes** of the affected version until root cause is known.
4. Open an internal incident thread (issue or private channel) with:
   - impact (install broken, data leak, CVE, etc.)
   - affected versions and platforms
   - whether users who already installed must take action

## Step 2 — Yank on PyPI

Yanking requires a PyPI account with maintainer rights on the project.

```bash
# List current files for a project (optional)
pip index versions node-wire-runtime

# Yank via PyPI web UI (recommended):
#   https://pypi.org/manage/project/<project>/releases/
#   → select version → "Yank" → provide reason (shown to users)

# Or via twine (if configured):
twine yank node-wire-runtime 1.0.0 --reason "Install regression; use 1.0.1 instead"
```

Repeat for every affected package (runtime and any connector wheels published
at the bad version).

**Yank reason template:**

> Do not use. Install &lt;fixed-version&gt; instead. See &lt;GitHub release or advisory URL&gt;.

## Step 3 — Publish a corrective release

1. Fix the defect on `main` (or a release branch) with tests.
2. Bump **PATCH** per [SemVer](versioning.md) (e.g. `1.0.0` → `1.0.1`).
3. Update [CHANGELOG.md](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md) with the fix and yank notice.
4. Run the local pre-publish checklist in [packaging.md](packaging.md#pre-pypi-local-validation-checklist).
5. Dispatch `.github/workflows/publish.yml` for each affected `package_path`
   with the corrective release `tag` (e.g. `v1.0.1`).

## Step 4 — GitHub release and tags

| Situation | Action |
|---|---|
| Tag points at bad commit, not widely used | Delete remote tag; retag fixed commit; edit GitHub Release |
| Tag already referenced externally | **Do not** rewrite history; publish new tag `vX.Y.Z+1` and mark old release as pre-release with warning |
| GitHub Release notes wrong | Edit release description; link to corrective version |

```bash
# Delete a remote tag only when safe (no external references)
git push origin :refs/tags/v1.0.0
git tag -a v1.0.1 <fixed-sha> -m "Release 1.0.1"
git push origin v1.0.1
```

## Step 5 — Communicate

Minimum user-facing notice (GitHub Release, Discussion, or advisory):

- Affected package names and version(s)
- Whether the version was yanked
- Fixed version to install: `pip install node-wire-runtime==X.Y.Z`
- Any manual steps (config change, secret rotation, data fix)
- Link to CHANGELOG entry

For security issues, follow coordinated disclosure in [SECURITY.md](https://github.com/AOT-Technologies/node-wire/blob/main/SECURITY.md)
before public announcement.

## Step 6 — Post-incident

- [ ] Root cause documented (issue or post-mortem)
- [ ] CI gap closed if the defect should have been caught pre-publish
- [ ] [CHANGELOG.md](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md) and [docs/troubleshooting.md](troubleshooting.md) updated if user-visible
- [ ] Branch protection / required checks reviewed
- [ ] If secrets were exposed: rotate credentials, run secret scan (see
      [quality-security-gates.md](quality-security-gates.md#secret-scanning)),
      and consider `git filter-repo` only with legal/security approval

## Docker images

Demo MCP images built from yanked wheels should be rebuilt and re-tagged with the
fixed version. Document the new tag in release notes; do not delete public images
without a communications plan.

## Quick reference

```bash
# Verify what pip would install after yank
pip install "node-wire-runtime>=1.0,<1.1" --dry-run

# Install explicit fixed version
pip install node-wire-runtime==1.0.1
```

## Related docs

- [Packaging & Publishing](packaging.md) — publish workflow and pre-release checks
- [Versioning policy](versioning.md) — when to bump MAJOR/MINOR/PATCH
- [Security Policy](https://github.com/AOT-Technologies/node-wire/blob/main/SECURITY.md) — vulnerability reporting and advisories
- [Quality & security gates](quality-security-gates.md) — CI checks and scans
