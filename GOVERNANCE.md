# Governance

NeuroWorkspace is currently maintained through a lightweight maintainer model.
This document describes the starting policy for the alpha; it should evolve as
regular contributors and users emerge.

## Decisions

- Bug fixes and contained documentation changes require one maintainer approval.
- Scientific behavior, security boundaries, public APIs, storage contracts, and
  architectural changes require an ADR and review from a maintainer familiar with
  the affected area.
- Format support claims require a license-reviewed, checksum-pinned public fixture
  and a passing real-data acceptance test.
- Roadmap priority is based on repeated user evidence, severity, and maintenance
  cost. A checklist in an old plan is not by itself a commitment.

Maintainers may merge urgent security fixes privately before publishing details.
Otherwise, substantive decisions and rationale should be visible in issues, pull
requests, or ADRs.

## Roles

- **Contributor:** anyone submitting an issue, documentation, test, or code change.
- **Reviewer:** a contributor trusted to review a particular technical or
  scientific area.
- **Maintainer:** a reviewer with merge, release, and repository-administration
  authority.

Maintainer status is earned through sustained, constructive contributions and
sound judgment. Existing maintainers nominate and approve new maintainers by
consensus. Maintainers who are inactive for six months may move to emeritus status
after private notice; they can return through the same consensus process.

## Conduct and security

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Report
security vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue.
Conflicts of interest should be disclosed in the relevant review.

## Releases

NeuroWorkspace follows semantic versioning, with the usual expectation that APIs
may change during `0.x` releases. Each release must pass the documented quality
gates, update the changelog, and link its measured compatibility evidence. Dataset
files are never release assets. Signing, SBOM generation, and build provenance are
release-hardening goals until the repository infrastructure implements and verifies
them.
