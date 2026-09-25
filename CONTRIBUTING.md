# Contributing

Start with `README.md`, `docs/ARCHITECTURE.md`, and the ADR for whatever
area you're touching. Open an issue before a large product or storage
change so it doesn't go to waste.

1. Install locked dependencies with `make install`.
2. Keep scientific/domain code independent of FastAPI/Celery/React.
3. Add tests that fail before the change. Scientific behavior needs explicit units,
   time/sample semantics, precision tolerance, and provenance impact.
4. Run `make lint typecheck test build`.
5. Never commit recordings, credentials, `.env`, caches, or generated build output.

A new format requires one adapter, unit/malformed fixtures, and an independently
produced public real file in the manifest. Generated round trips do not establish
compatibility. A new QC rule documents formula, unit, configurable threshold,
coverage, limitations, and must remain a review suggestion.

Public communication is neutral and evidence-based. Do not claim clinical validity,
HIPAA compliance, adoption, users, or performance without current evidence.

