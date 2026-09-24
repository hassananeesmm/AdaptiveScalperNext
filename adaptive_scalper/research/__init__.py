"""Research-only statistical validation (Phase 1 of the completion
directive): label intervals, purging/embargo, purged K-fold, CPCV,
PSR/DSR, PBO and the research trial ledger.

Nothing on the execution-critical path imports this package, and this
package imports nothing that can reach a broker, the kill switch or risk
sizing (`tests/test_research_validation.py` checks both directions). It
answers "how much of this backtest result is selection bias or luck?",
never "should we trade?".

Implemented here from the published definitions (Lopez de Prado,
"Advances in Financial Machine Learning", ch. 7 and 11-12; Bailey &
Lopez de Prado 2012/2014; Bailey et al. 2017 for PBO/CSCV) instead of
depending on a third-party package, and verified with hand-constructed
cases whose correct answer is known independently.
"""
