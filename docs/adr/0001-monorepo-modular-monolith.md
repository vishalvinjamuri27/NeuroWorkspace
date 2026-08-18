# ADR 0001: Monorepo and modular monolith

## Context

The product needs a typed web client, Python scientific stack, asynchronous work,
and shared contracts. A small founding team needs one coherent development and
release surface; user evidence does not justify independently owned services.

## Decision

Use a pnpm/Python monorepo. Deploy the Next.js web, FastAPI API, and Celery worker
as separate processes from one modular Python domain/application package. Keep
transport, domain, scientific adapters, and persistence boundaries explicit.

## Alternatives

A Python-rendered UI would reduce languages but compromise the required web viewer.
Microservices could isolate workloads but add schemas, deployment, tracing, and
failure modes before teams or traffic demand them. One process cannot safely host
long scientific parsing.

## Consequences

Atomic changes and local setup are simpler; API/worker can scale independently.
Module discipline and generated contracts are required to prevent a monolith from
becoming tangled. A measured hotspot can later become a service at an existing
interface.

