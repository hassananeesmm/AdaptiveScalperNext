"""Reproducible AI-desk cycle benchmark (READ-ONLY on the trading database).

    .venv\\Scripts\\python scripts\\ai_desk_benchmark.py --cycles 5
    .venv\\Scripts\\python scripts\\ai_desk_benchmark.py --config config\\default.toml --cycles 5

Runs N full desk cycles in-process against the trading DB named in the trading
config (opened read-only), journaling into a throw-away temporary journal, and
prints JSON: import time, snapshot build, per-cycle wall time (min/median/max),
per-agent median latency, the Python-traced allocation of one extra cycle and
the process peak working set. Timing cycles run WITHOUT tracemalloc (it slows
the pure-Python bootstraps about 4x); memory is measured in a separate cycle.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _peak_working_set_mb() -> float | None:
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    import ctypes
    import ctypes.wintypes as w

    class Counters(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t)] + [(f"x{i}", ctypes.c_size_t) for i in range(6)]

    c = Counters()
    c.cb = ctypes.sizeof(Counters)
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = w.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
    if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        return None
    return c.PeakWorkingSetSize / 2**20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI desk cycle benchmark (read-only)")
    parser.add_argument("--config", default="config/default.toml", help="trading config (database path)")
    parser.add_argument("--ai-config", default="config/ai_desk.toml", help="AI desk config (journal is ignored)")
    parser.add_argument("--cycles", type=int, default=5)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    from adaptive_scalper.ai_desk.configuration import load_ai_desk_config
    from adaptive_scalper.ai_desk.journal import Journal
    from adaptive_scalper.ai_desk.orchestration import DeskOrchestrator
    from adaptive_scalper.config.loader import load_config

    import_s = time.perf_counter() - started
    trading = load_config(args.config)
    with tempfile.TemporaryDirectory() as tmp:
        desk = load_ai_desk_config(args.ai_config).model_copy(update={"journal_path": str(Path(tmp) / "j.sqlite3")})
        journal = Journal(desk.journal_path, trading_db_path=trading.database.path)
        orch = DeskOrchestrator(desk, trading_db_path=trading.database.path, journal=journal, trading_config=trading)
        t = time.perf_counter()
        snap = orch.builder.build(as_of_utc=int(time.time()), mode="CURRENT", prior_assessments={})
        snapshot_s = time.perf_counter() - t
        walls, per_agent = [], {}
        for i in range(args.cycles):
            t = time.perf_counter()
            report = orch.run_cycle(trigger="benchmark", as_of_utc=int(time.time()) + i)
            walls.append(time.perf_counter() - t)
            if report.status != "COMPLETED":
                journal.close()
                print(json.dumps({"error": report.error}))
                return 1
            for r in report.results:
                per_agent.setdefault(r.agent, []).append(r.duration_ms)
        tracemalloc.start()
        orch.run_cycle(trigger="benchmark-memory", as_of_utc=int(time.time()) + args.cycles + 1)
        traced_mb = tracemalloc.get_traced_memory()[1] / 2**20
        tracemalloc.stop()
        journal.close()
    peak = _peak_working_set_mb()
    print(json.dumps({
        "trading_db": trading.database.path, "evidence_items": len(snap.evidence), "cpus": os.cpu_count(),
        "import_s": round(import_s, 2), "snapshot_build_s": round(snapshot_s, 3),
        "cycle_s": {"n": len(walls), "min": round(min(walls), 2), "median": round(statistics.median(walls), 2),
                    "max": round(max(walls), 2)},
        "agent_ms_median": {k: statistics.median(v) for k, v in per_agent.items()},
        "traced_alloc_mb_one_cycle": round(traced_mb, 1),
        "process_peak_working_set_mb": None if peak is None else round(peak, 1),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
