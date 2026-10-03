# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project URLs, keywords, and extra classifiers in package metadata (shown on PyPI).
- This changelog.

## [0.1.0] - 2026-10-02

### Added
- Initial release.
- `IdempotencyMiddleware` (global ASGI middleware) and `@idempotent` route-level decorator.
- Deterministic SHA-256 request fingerprinting and atomic distributed locks.
- Pluggable storage backends: `MemoryBackend`, `SQLiteBackend`, `RedisBackend`.
- Portuguese (Brazil) README.

[Unreleased]: https://github.com/Luan1Schons/fastapi-idempotency-key/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Luan1Schons/fastapi-idempotency-key/releases/tag/v0.1.0
