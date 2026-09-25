# MT5 Verification

## Verified on the connected terminal

- Account trade mode: DEMO (`ICMarketsSC-Demo`, Raw Trading Ltd, USD).
- Terminal/account connected and expert-trading permission visible.
- Symbols resolve exactly: XAUUSD, GBPJPY and BTCUSD; minimum/step volume 0.01.
- Server-time rule: `UTC+2/US_DST`; current quotes classify VERIFIED after conversion.
- Historical winter/DST behavior and skipped spring-forward rows were investigated in the
  Windows-validation worklog; ten impossible local-hour rows were quarantined, not guessed.
- A prior controlled `order_check` for XAUUSD BUY returned retcode 0 / Done and was never
  sent. There were zero positions/orders before and after.
- The current audit called only read operations. It did not call `order_check` or `order_send`.

## Still unverified

- A naturally generated DEMO entry/fill, partial fill, broker-side protective stop rejection,
  live close, stop modification and restart with a real open position.
- Netting behavior (the connected account is hedging).
- Pinning among the two locally installed MT5 terminals.
- Every future DST boundary; startup verification remains the fail-closed control.

## Readiness

`preflight` reports `READY_FOR_PAPER`, not `READY_FOR_DEMO`. Required operator/evidence work:

1. Keep the kill switch under human control; do not bootstrap/clear it merely for testing.
2. Obtain a fresh CLEAN reconciliation in the controlled DEMO startup path.
3. Keep GBPJPY DEMO entries blocked until credible slippage evidence exists, or intentionally
   remove GBPJPY from the DEMO symbol set.
4. Confirm the dashboard and monitoring remain healthy.

REAL, CONTEST and UNKNOWN accounts remain prohibited.

