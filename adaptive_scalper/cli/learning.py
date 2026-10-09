"""ML commands: models, learning status/train/scores, model-walk-forward.
Training registers BASELINE at most and reports the promotion gate; no
command here promotes a model or gives it influence."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from adaptive_scalper.cli.common import add_symbol_arg, open_db, print_json
from adaptive_scalper.learning.jobs import SOURCE_BACKTEST, SOURCE_PAPER, load_training_rows, run_training_job
from adaptive_scalper.learning.model_walk_forward import result_to_dict, run_model_walk_forward
from adaptive_scalper.research.ledger import new_trial_id, record_trial


def cmd_models(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rows = conn.execute("SELECT model_key, version, lifecycle_state, training_sample_count, artifact_checksum, "
                        "metrics_json, created_at_utc FROM models ORDER BY model_key, version").fetchall()
    conn.close()
    out = []
    for r in rows:
        metrics = json.loads(r["metrics_json"] or "{}")
        wf = metrics.get("walk_forward") or {}
        out.append({"model_key": r["model_key"], "version": r["version"], "state": r["lifecycle_state"],
                    "samples": r["training_sample_count"], "checksum": (r["artifact_checksum"] or "")[:16] or None,
                    "source": metrics.get("source"), "wf_brier_skill": wf.get("brier_skill"), "wf_ece": wf.get("ece"),
                    "created_at_utc": r["created_at_utc"]})
    print_json({"models": out, "influence": "NONE (STAGE 1 OBSERVER; Stage 2 not implemented)"})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    states = {r[0]: r[1] for r in conn.execute("SELECT lifecycle_state, COUNT(*) FROM models GROUP BY lifecycle_state")}
    trials = {r[0]: r[1] for r in conn.execute("SELECT kind, COUNT(*) FROM research_trials GROUP BY kind")}
    scored = conn.execute("SELECT COUNT(*) FROM journal_events WHERE event_type = 'MODEL_USED'").fetchone()[0]
    conn.close()
    print_json({"models_by_state": states, "research_trials_by_kind": trials, "observer_scores_journaled": scored,
                "stage": "1 (observer, zero influence)"})
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    cfg, conn = open_db(args.config, require_utc_history=True)
    artifact_dir = args.artifact_dir or str(Path(cfg.database.path).parent / "models")
    report = run_training_job(conn, canonical_symbol=args.symbol, artifact_dir=artifact_dir, source=args.source,
                              n_folds=args.folds, gap_seconds=args.gap_seconds, min_samples=args.min_samples,
                              seed=args.seed)
    conn.close()
    print_json(report.summary())
    return 0 if report.training.trained else 1


def cmd_scores(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rows = conn.execute(
        "SELECT event_timestamp_utc, canonical_symbol, strategy_key, payload_json FROM journal_events "
        "WHERE event_type = 'MODEL_USED' ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
    conn.close()
    print_json([{"at": r[0], "symbol": r[1], "strategy": r[2], **json.loads(r[3]).get("evidence", {})} for r in rows])
    return 0


def cmd_model_walk_forward(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config, require_utc_history=True)
    loaded = load_training_rows(conn, canonical_symbol=args.symbol, source=args.source)
    result = run_model_walk_forward(loaded.rows, n_folds=args.folds, gap_seconds=args.gap_seconds,
                                    min_train_rows=args.min_train_rows, seed=args.seed)
    now = int(time.time())
    trial_id = new_trial_id("model-wf-eval", "entry_model", args.symbol, now_utc=now)
    record_trial(conn, trial_id=trial_id, family=f"entry_model:{args.symbol}", kind="MODEL_WALK_FORWARD",
                 strategy_versions={}, params={"source": args.source, "folds": args.folds,
                                               "gap_seconds": args.gap_seconds, "evaluation_only": True},
                 status="COMPLETED" if result.oos_rows else "FAILED", model="LogisticRegression",
                 n_observations=result.oos_rows, result=result_to_dict(result), notes=result.detail, now_utc=now)
    conn.close()
    print_json({"trial_id": trial_id, "rows": len(loaded.rows), "excluded": loaded.excluded,
                "oos_protected": loaded.oos_protected, **result_to_dict(result)})
    return 0


def _training_args(p: argparse.ArgumentParser) -> None:
    add_symbol_arg(p)
    p.add_argument("--source", choices=(SOURCE_BACKTEST, SOURCE_PAPER), default=SOURCE_BACKTEST)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--gap-seconds", type=int, default=0)
    p.add_argument("--seed", type=int, default=0)


def register(sub) -> None:
    sub.add_parser("models", help="model registry (versions, states, walk-forward evidence)").set_defaults(
        func=cmd_models)
    learning = sub.add_parser("learning", help="Stage-1 ML observer")
    lsub = learning.add_subparsers(dest="learning_command", required=True)
    lsub.add_parser("status", help="models by state, research trials, observer scores").set_defaults(func=cmd_status)
    train = lsub.add_parser("train", help="walk-forward + train + register BASELINE (never promotes)")
    _training_args(train)
    train.add_argument("--min-samples", type=int, default=200)
    train.add_argument("--artifact-dir")
    train.set_defaults(func=cmd_train)
    scores = lsub.add_parser("scores", help="recent observer scores from the journal")
    scores.add_argument("--limit", type=int, default=50)
    scores.set_defaults(func=cmd_scores)

    mwf = sub.add_parser("model-walk-forward", help="evaluate the entry model by walk-forward (no registration)")
    _training_args(mwf)
    mwf.add_argument("--min-train-rows", type=int, default=100)
    mwf.set_defaults(func=cmd_model_walk_forward)
