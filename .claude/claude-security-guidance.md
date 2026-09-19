# Adaptive Scalper Next — project security & safety guidance

This project is a MetaTrader 5 algorithmic trading system. In addition to
normal security review (secrets, injection, unsafe deserialization, etc.),
flag the following project-specific issues when reviewing diffs:

- Any code path that could submit an order against a real-money (non-DEMO)
  MT5 account. The system must positively verify DEMO status immediately
  before every order; anything that infers DEMO from configuration alone,
  or that removes/weakens that check, is a critical finding.
- Any change to `ALLOWED_CANONICAL_SYMBOLS` (must remain exactly XAUUSD,
  GBPJPY, BTCUSD) or to `RETIRED_STRATEGY_KEYS` (must always contain
  `failed_breakout_fade` and `support_resistance_reaction`, and neither may
  ever be made active/executable again).
- Any change that raises a hard-coded risk limit (risk_per_trade_pct,
  max_total_open_risk_pct, max_daily_loss_pct, max_drawdown_pct,
  max_open_positions, max_positions_per_symbol) without an explicit human
  request to do so.
- Any change that lets learning/RAG/ML code write to: allowed symbols,
  retired-strategy list, risk limits, kill switch, news blackout, the DEMO
  gate, executor behavior, or broker constraints. Learning may only adjust
  bounded, validated adaptive artifacts (scores, calibration, weights).
- Any change that deletes or weakens tests specifically to make a failing
  suite pass, rather than fixing the underlying defect.
- Any file write or shell command that targets the sibling directory
  `C:\AdaptiveScalper` (the old, separate project) rather than
  `C:\AdaptiveScalperNext`.
- Hardcoded broker credentials, API keys, or MT5 login/password values
  committed to the repository.

These checks are ADDITIVE to your normal review — they never suppress a
finding you would otherwise report.
