# Changelog

All notable changes will be documented here.

## [Unreleased]

### Changed

- Restored pinned `ACL4SSR_Online_Full.ini` semantics as the canonical owner of all non-AI rule targets, policy groups, member order, and final routing.
- Separated FlClash presentation-only containers from semantic ACL4SSR policy groups so UI nesting no longer changes rule behavior.
- Removed the canonical local direct-rule prelude and subscription-source route exclusions that previously altered ACL4SSR behavior.
- Kept service-aware OpenAI, Claude, and Gemini live qualification as the explicit routing-semantic extension.
- Added canonical production-shaped Mihomo integration coverage and a fail-closed compatibility boundary requiring the pinned ACL4SSR Provider files to verify all nine legacy `URL-REGEX` omissions, with zero unverified legacy rules.

## [0.1.0] - 2026-08-30

### Added

- From-scratch declaration, parser, classifier, generator, validator, and CLI architecture.
- Arbitrary secret-injected subscriptions and explicit capability metadata.
- Data-driven ChatGPT, Claude, and Gemini service scheduling.
- General, Google Play, bulk, residential, EMBY, high-multiplier, and controlled chain pools.
- Deterministic inline-provider generation and fail-closed empty behavior.
- Static graph/security validation and real Mihomo load/start/HEAD integration tests.
- PR, stable promotion, prerelease observation, Artifact, Release, and optional Gist workflows.
- Public documentation, schemas, fixtures, and repository safety audit.
