# Contributing

Contributions should preserve deterministic generation, fail-closed pool semantics, source/capability separation, and publication gates.

1. Create a focused branch.
2. Add or update tests with every behavior change.
3. Use only fictional `.invalid.example` hosts and fixture credentials.
4. Run `make check`; run the integration suite when generator/core behavior changes.
5. Update schemas and documentation when declarations change.
6. Never attach a real generated config or subscription URL to a PR or issue.

New services should normally require only a module Boolean, a `services.yaml` row, a rule file, and tests. New Python service branches require an architecture justification.
