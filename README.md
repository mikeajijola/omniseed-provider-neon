# OmniSeed Neon Provider

This package implements the OmniSeed Provider Protocol for durable runtime-state
resources supplied by Neon. Neon is the Provider; PostgreSQL, branches, compute
endpoints, and pooled connection services are products beneath that boundary.

It supports the canonical `memory` primitive family for runtime state. Git
remains the canonical desired-state authority. This Provider never stores an
Omniform company definition as canonical state and never emits database
credentials as evidence.

## Runtime

```sh
NEON_API_KEY=... python3 provider/neon_provider.py
```

Configuration identifies a stable project name (and optionally an existing
project ID and Neon organisation). The API key is read only from the named
server-side environment variable. `provider.apply` reuses an exact project ID
or a uniquely named project before creating one, and returns only non-secret
resource identity. Connection credentials remain ordinary protected runtime
configuration for the consumer that needs them.

## Development

```sh
npm test
```

No live mutation test runs by default.
