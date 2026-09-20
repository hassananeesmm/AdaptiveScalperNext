"""ML / self-learning (directive: observer stage first).

No source self-modification, no `eval`/`exec`, CPU-friendly local models
only. This package currently implements the OBSERVER stage: the model
lifecycle state machine (`learning.lifecycle`), the persisted registry
(`learning.registry`), the promotion gate (`learning.promotion`), and the
drift-response guarantee (`learning.drift`) that drift can only ever
LOWER a model's influence, never raise risk. Real model TRAINING (walk-
forward, purged/temporal splits, untouched OOS evaluation) is scoped
into the backtest/walk-forward subsystem, not duplicated here — this
package owns the lifecycle/promotion/registry machinery a trained
model's result flows through, not the training pipeline itself.
"""
