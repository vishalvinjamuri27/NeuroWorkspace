# ADR 0005: Vendor-neutral identity and mandatory workspace authorization

## Context

Hosted deployments need real identity while open-source/local development must be
simple. Coupling domain records to one auth vendor is costly; home-grown production
password auth is risky. Opaque IDs do not prevent cross-tenant access.

## Decision

Domain code accepts a verified `Identity` and central policy service. Local dev uses
a signed HTTP-only session for seeded users; production requires an OIDC verifier
and refuses dev auth. Every repository query and object/signing operation is scoped
through active workspace membership and role capability. Denials are uniform.

## Alternatives

A commercial vendor end to end speeds hosted UI but weakens self-hosting and domain
portability. Implementing passwords, recovery, MFA, and abuse defenses distracts
from core product. Database RLS alone does not cover object storage or task policy.

## Consequences

Deployment may select an OIDC provider without changing business rules. The local
path is intentionally non-production. Central policy/repository APIs require more
care but enable exhaustive two-user isolation tests and later RLS defense-in-depth.

