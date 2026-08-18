# Roadmap freeze

> No major new feature category should be built until external user feedback
> indicates demand, unless necessary for correctness, security, reliability,
> deployment, or severe usability problems.

Every idea receives one evidence class:

- **VALIDATED:** observed across real workflows with behavior showing the problem
  blocks or materially improves continued use.
- **REPEATED REQUEST:** requested independently by multiple relevant users and
  connected to a demonstrated workflow.
- **SINGLE USER REQUEST:** useful to one participant; retain without committing.
- **HYPOTHESIS:** a falsifiable product belief with a cheap user test.
- **SPECULATIVE:** no current behavioral evidence.

Only VALIDATED and repeated high-value requests should normally enter the
near-term roadmap. A repeated request is not automatically valuable: document the
affected workflow, frequency, current workaround, retention impact, cost, and kill
condition.

Correctness, cross-tenant security, data integrity, severe accessibility/usability,
deployment recovery, and parser compatibility regressions may bypass the feature
freeze because they protect the existing contract. Live streaming, new modalities,
ML/AI features, diagnostic behavior, new formats, Kubernetes, enterprise identity,
mobile apps, and elaborate collaboration remain outside the alpha until evidence
changes their class.

Review evidence after the first five observed sessions and again after ten
successful own-data ingestions. Do not move an idea between classes based on
founder enthusiasm, competitor screenshots, or model-generated speculation.
