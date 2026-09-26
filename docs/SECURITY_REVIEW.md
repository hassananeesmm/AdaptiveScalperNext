# Security Review

## Results

- Isolated `pip-audit -r requirements.txt`: **no known vulnerabilities found** on
  2026-09-25.
- `pip check`: no broken requirements.
- Tracked secret-pattern scan found only policy/scanner text and token terminology, not a
  credential value.
- Dashboard bind is restricted to 127.0.0.1 and non-local bind attempts are rejected.
- Dashboard uses SQLite read-only mode and has no mutation endpoint.
- Release tooling packages tracked files and rejects databases, keys, `.env`, credentials,
  secrets and model artifacts.
- Release 0.1.3 was scanned through that allowlist/denylist path, checksum-verified, installed
  in a fresh temporary environment and tested with `ASN_DISABLE_MT5=1`.
- No shell execution or unsafe subprocess path was found in the runtime package.
- YAML uses safe loading. SQL values use parameters in reviewed operational paths.
- Logs redact secret-shaped key/value text; broker passwords never enter the application.

## Residual risks

1. `joblib.load` is pickle-compatible. Checksums detect corruption but are not signatures.
   Only locally produced artifacts are supported; do not import downloaded model files.
2. Local database, backups and logs inherit user filesystem permissions. Protect the Windows
   account and backup directory; they contain trading history and operational telemetry.
3. Two MT5 installations exist and terminal selection is implicit. The DEMO gate limits the
   consequence, but explicit path/identity pinning would improve assurance.
4. The local HTTP dashboard has no authentication because it is localhost-only. Do not bind
   it to LAN/WAN interfaces or expose it through a tunnel/reverse proxy.
5. Dependency results are point-in-time. Re-run pip-audit for each release.

Runtime operation has no dependency on Claude, Codex, OmniRoute or a paid LLM credential.
