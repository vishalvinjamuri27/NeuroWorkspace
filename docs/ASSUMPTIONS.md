# Recorded assumptions

1. This empty project mirror is the intended repository root; it is not currently
   nested in a Git checkout. Initialization here is authorized by the mission.
2. `NeuroWorkspace` is a replaceable working name; brand strings are centralized.
3. Apache-2.0 is the engineering recommendation but remains subject to maintainer/
   legal choice before public release.
4. Hosted authentication uses OIDC; local dev auth is clearly non-production.
5. PostgreSQL 18, Redis, S3/MinIO, Next.js 16, Python 3.12, MNE 1.12, and Zarr 3.3
   are the initial compatibility line, locked in repository manifests.
6. Float32 canonical values are acceptable only after source-comparison tolerances;
   immutable originals and conversion provenance remain available.
7. Public dataset candidates in the mission are unverified leads until fetch,
   license, format, checksum, and real tests complete.
8. The current workstation has no Docker or uv command at the initial audit. Local
   native tests will run; Docker acceptance is blocked only if still unavailable at
   the final gate.

