# Open-source strategy

## Positioning

The repository should be useful before it is popular: a reproducible local stack,
public sample, format adapter docs, honest matrix, and clear non-clinical boundary.
No invented users, testimonials, adoption, compatibility, compliance, or benchmark
claims. Screenshots use attributed public/synthetic data.

## License recommendation

Apache-2.0 is recommended for the code because it is permissive, familiar to
commercial adopters, includes an explicit patent grant, and permits a future
commercial hosted product without making the community edition unusable. MIT is
simpler but lacks the same express patent language. Copyleft could protect shared
improvements but may slow research/company adoption. This is an engineering tradeoff,
not legal advice; counsel/maintainers make the final choice before release.

Dataset licenses remain separate. The repository does not redistribute real EEG
files unless a specific license clearly permits it; fetch scripts preserve
attribution and terms.

## Contributor experience

README leads with use case, screenshot/demo, support matrix, five-minute setup,
architecture, limitations, roadmap, and contribution entry points. CONTRIBUTING
covers environment, tests, scientific expectations, security reporting, DCO/signoff
decision, and extension tutorials for format/QC/export/processing. Code of Conduct,
security policy, governance, changelog, issue/PR templates, and release process are
checked in before v0.1.0.

Labels and starter issues distinguish user pain from speculative features. Format
claims require a pinned real file and test evidence. Scientific behavior changes
need provenance/migration implications and a reviewer familiar with the domain.

## Governance and releases

Initial maintainers use the lightweight policy in `GOVERNANCE.md` and public ADRs.
Security reports use a private channel. Semantic versioning governs API/package
releases; early APIs may change only with changelog/migration notes. Releases
publish test/real-data matrix links without data files. Signing, SBOM generation,
and build provenance remain explicit release-hardening work until implemented and
verified.

`v0.1.0` means alpha-ready under `ALPHA_READINESS_REPORT.md`, not production or
clinical readiness. Support tiers are documented per format and operation.

## Community feedback loop

Issue forms capture role, workflow, format/device, scale, expected output, impact,
and reproducible non-sensitive sample availability. Roadmap items carry one of:
validated user need, hypothesis, engineering improvement, speculative idea.
Quarterly summaries report repeated problems and removals as well as additions.
