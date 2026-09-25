# Dashboard Acceptance

The integrated responsive 17-panel dashboard was tested in live Chrome against the real
SQLite database and running PAPER telemetry. It is bound to localhost and exposes only GET
and WebSocket observer routes.

## Browser matrix

The following CSS viewport sizes were exercised: 1366x768, 1600x900, 1920x1080,
2560x1440, 1093x614, 911x512, 1280x720, 1536x864 and 1707x960. The latter five cover the
layout sizes produced by the requested 125%/150% scaling combinations.

At every size:

- 17 panels were reachable in All panels;
- page-level horizontal overflow was false;
- no panel escaped the viewport;
- sidebar and navigation overflow were false;
- minimum visible font was 11 px;
- the feed was live and broker/account state was distinct from browser connectivity.

Seven navigation views, keyboard-focus styling, dark/light theme, internal table scrolling,
polling/WebSocket behavior and stale-data presentation are covered by automated tests and
the preceding W6 Chrome session. The current live page showed PAPER RUNNING, DEMO account
telemetry, fresh quotes, risk ceilings, an uninitialized kill switch, and no dangerous
UNKNOWN order.

## Acceptance limitations

- Viewports emulate the effective CSS space of 125%/150%; Windows display scaling itself was
  not changed during this audit.
- A dashboard process must be restarted after deploying new frontend code; an already-loaded
  browser tab cannot prove the server is running the newest build. Preflight checks for the
  integrated monitor marker and `/api/health`.
- Dashboard shutdown isolation is covered by the preceding Windows checkpoint. The temporary
  audit dashboard used port 8766 and was independent of the operator's port 8765 instance.

Result: **ACCEPTED for local PAPER monitoring**; DEMO acceptance remains tied to the global
DEMO readiness gates.

