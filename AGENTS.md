# Working on the OmniSeed Neon Provider

- Provider organisation and canonical ID: Neon / `neon`.
- PostgreSQL, projects, branches, roles, databases, compute endpoints, and pooled connections are Neon products/services, not separate Providers.
- This Provider implements only the canonical `memory` primitive family for durable runtime state.
- Git remains canonical desired state; Neon stores runtime state, observations, evidence, and Activity only.
- Never print, return, or persist API keys, passwords, or connection URIs in evidence.
- Project creation must be deterministic against an approved project identity and fail on ambiguous name matches.
- Do not delete projects through this first production slice.
- Run `npm test` before proposing a change. Live mutation requires explicit approval.
