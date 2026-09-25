# Trading & Fintech Domain Rules (Non-Negotiable)

These rules apply to **every** trading-related module, strategy, order execution path, or risk management component. Agents must read and apply these in addition to `CLAUDE.md` whenever the project involves trading, financial markets, or order execution.

## Architect-produced artifacts

- `plan.md` must include a dedicated **Risk Management** section specifying: position sizing rules, stop-loss policy, max drawdown limit, and capital allocation strategy. Implementation does not begin until these are explicit in the plan.
- `plan.md` must call out which modules are trading-sensitive (order execution, risk logic, credential handling) so downstream agents apply the rules below with extra scrutiny.

## Order execution & idempotency

- Every order execution path must be **idempotent** and include retry with exponential backoff + circuit breaker.
- Any strategy that can place real orders must include a clear **dry-run / paper trading** mode flag that is easy to enable.
- **Audit logging** is mandatory for every trade decision, order placement, fill, cancellation, and error.
- Error handling must never silently fail on order-related operations — fail fast and loud with full context.

## Credentials & secrets

- All credential handling (API keys, secrets, tokens) must go through a dedicated secrets manager or environment variables — **never** hard-coded or logged.

## Market data integrity

- Never assume market data is clean — always validate timestamps, prices, volumes, and exchange status before using them in calculations.
- All financial calculations must use appropriate decimal precision (avoid floating-point rounding errors on money).

## Verification (in addition to CLAUDE.md §4)

- Trading-related code: run at least one backtest on historical data + one stress test (high volatility period) before marking complete.

## Additional Production Readiness Checks

In addition to the universal checklist in `CLAUDE.md`:

- [ ] Idempotency + error handling + retry logic verified for all order/risk paths
- [ ] Backtesting + stress test completed and results documented (for any strategy or execution logic)
- [ ] Performance acceptable under market-open spike load (≤ 5,000 concurrent users)
- [ ] "Would you be comfortable putting real capital behind this code?"
