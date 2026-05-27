"""CLI entry point. Run with:

  uv run python -m evals.run                 # all scenarios
  uv run python -m evals.run mundane_hi      # one scenario by id
  uv run python -m evals.run --no-judge      # run scenarios, skip judging
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from evals.harness import (
    PROJECT_ROOT,
    ScenarioResult,
    result_to_jsonable,
    run_scenario,
    transcript_to_text,
)
from evals.judge import judge
from evals.scenarios import SCENARIOS, Scenario

RESULTS_DIR = PROJECT_ROOT / "evals" / "results"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _print_score_line(scenario_id: str, scores: dict | None) -> None:
    if not scores:
        print(f"  {scenario_id}: (no judge result)")
        return
    axes = ["question_discipline", "shape_matching", "own_life", "self_user_balance", "playfulness"]
    parts = []
    for a in axes:
        s = scores.get(a, {}).get("score", "?")
        parts.append(f"{a[:4]}:{s}")
    overall = scores.get("overall", "?")
    print(f"  {scenario_id}: {' '.join(parts)}  [{overall}]")


def _markdown_report(run_dir: Path, results: list[tuple[ScenarioResult, dict | None]]) -> str:
    lines = ["# eval run", f"_{_now_stamp()}_", ""]
    axes = ["question_discipline", "shape_matching", "own_life", "self_user_balance", "playfulness"]
    lines.append("## scores")
    lines.append("")
    header = "| scenario | category | " + " | ".join(a[:8] for a in axes) + " | overall |"
    sep = "|" + " --- |" * (len(axes) + 3)
    lines.append(header)
    lines.append(sep)
    for result, scores in results:
        if scores:
            cells = [str(scores.get(a, {}).get("score", "?")) for a in axes]
            overall = scores.get("overall", "?")
        else:
            cells = ["?"] * len(axes)
            overall = "—"
        lines.append(
            f"| {result.scenario_id} | {result.category} | "
            + " | ".join(cells)
            + f" | {overall} |"
        )
    lines.append("")
    lines.append("## transcripts")
    for result, scores in results:
        lines.append(f"\n### {result.scenario_id} ({result.category}, {result.kind})")
        if scores:
            lines.append("")
            for a in axes:
                s = scores.get(a, {})
                lines.append(f"- **{a}** ({s.get('score','?')}): {s.get('rationale','')}")
            lines.append(f"- **overall**: {scores.get('overall','?')} — {scores.get('overall_note','')}")
        lines.append("")
        lines.append("```")
        lines.append(transcript_to_text(result))
        lines.append("```")
        if result.final_journal.strip():
            lines.append("\n**final journal:**")
            lines.append("```")
            lines.append(result.final_journal.rstrip())
            lines.append("```")
        if result.final_owner.strip():
            lines.append("\n**final owner.md:**")
            lines.append("```")
            lines.append(result.final_owner.rstrip())
            lines.append("```")
    return "\n".join(lines)


async def _run(selected_ids: list[str], do_judge: bool) -> int:
    scenarios: list[Scenario] = SCENARIOS
    if selected_ids:
        scenarios = [s for s in SCENARIOS if s.id in selected_ids]
        missing = set(selected_ids) - {s.id for s in scenarios}
        if missing:
            print(f"unknown scenario ids: {sorted(missing)}", file=sys.stderr)
            return 2
    if not scenarios:
        print("no scenarios to run", file=sys.stderr)
        return 1

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = RESULTS_DIR / _now_stamp()
    run_dir.mkdir()
    print(f"writing results to {run_dir}")

    paired: list[tuple[ScenarioResult, dict | None]] = []
    for scenario in scenarios:
        print(f"\n[{scenario.id}] running ({scenario.category}/{scenario.kind})...")
        t0 = time.time()
        result = await run_scenario(scenario)
        dt = time.time() - t0
        print(f"  ran in {dt:.1f}s, {len(result.turns)} turn(s)")
        scores: dict | None = None
        if do_judge:
            transcript_text = transcript_to_text(result)
            t1 = time.time()
            try:
                scores = judge(transcript_text)
            except Exception as e:
                print(f"  judge failed: {e}")
            else:
                print(f"  judged in {time.time() - t1:.1f}s")
        _print_score_line(scenario.id, scores)
        # Per-scenario JSON.
        (run_dir / f"{scenario.id}.json").write_text(
            json.dumps(
                {"result": result_to_jsonable(result), "scores": scores},
                ensure_ascii=False,
                indent=2,
            )
        )
        paired.append((result, scores))

    report = _markdown_report(run_dir, paired)
    (run_dir / "report.md").write_text(report)
    print(f"\nreport: {run_dir / 'report.md'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*", help="scenario ids to run; default = all")
    parser.add_argument("--no-judge", action="store_true", help="skip judge step")
    args = parser.parse_args()
    return asyncio.run(_run(args.ids, do_judge=not args.no_judge))


if __name__ == "__main__":
    raise SystemExit(main())
