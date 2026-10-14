"""Command-line interface for the eval harness.

Wires the runner (:mod:`evals.runner`) and result storage
(:mod:`evals.storage`) into a small Typer app so a person and CI run the *same*
code path::

    python -m evals.cli run --suite smoke --config configs/v1.yaml
    python -m evals.cli list-runs
    python -m evals.cli diff last prev

``run`` exits non-zero when any gate fails, which is what lets the GitHub
Actions workflow block a merge on an eval regression. The ``triage-eval``
console script points at :data:`app`.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from evals.config import GateThresholds, load_config
from evals.runner import RunReport, evaluate_gates, run_suite
from evals.suites import SUITES

app = typer.Typer(
    name="triage-eval",
    help="Run, inspect, and diff support-triage eval suites.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)

# Metric keys where a *lower* value is better; everything else is higher-better.
_LOWER_IS_BETTER = ("cost", "latency", "error", "tokens")

# Nested subtrees too granular for a run-to-run diff view.
_NOISY_KEYS = {"per_class", "confusion"}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _pct(value: object) -> str:
    return "[dim]--[/dim]" if value is None else f"{float(value):.1%}"


def _num(value: object, digits: int = 4) -> str:
    return "[dim]--[/dim]" if value is None else f"{float(value):.{digits}f}"


def _render(value: object) -> str:
    if value is None:
        return "[dim]--[/dim]"
    if isinstance(value, bool):
        return "[green]yes[/green]" if value else "[red]no[/red]"
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return str(value)


def _flatten(data: dict, prefix: str = "") -> dict:
    """Flatten a nested metrics dict into dotted keys for diffing.

    Confusion matrices and per-class breakdowns are skipped: they are useful in
    the database but drown the signal in a terminal diff.
    """

    out: dict = {}
    for key, value in (data or {}).items():
        if key in _NOISY_KEYS:
            continue
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{dotted}."))
        else:
            out[dotted] = value
    return out


def _delta_cell(key: str, before: object, after: object) -> tuple[str, str]:
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return "[dim]--[/dim]", ""
    delta = float(after) - float(before)
    if delta == 0:
        return "[dim]+0.0000[/dim]", "[dim]=[/dim]"
    lower_better = any(token in key.lower() for token in _LOWER_IS_BETTER)
    improved = delta < 0 if lower_better else delta > 0
    color = "green" if improved else "red"
    arrow = "up" if delta > 0 else "down"
    return f"[{color}]{delta:+.4f}[/{color}]", f"[{color}]{arrow}[/{color}]"


def _gates_for_run(run) -> object:
    """Recompute gate results for a stored run from its config snapshot."""

    gates_raw = (run.config_snapshot or {}).get("gates", {}) or {}
    known = set(GateThresholds.__dataclass_fields__)
    thresholds = GateThresholds(**{k: v for k, v in gates_raw.items() if k in known})
    return evaluate_gates(thresholds, run.metrics or {})


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def run(
    suite: Annotated[str, typer.Option("--suite", "-s", help="Suite to run.")] = "smoke",
    config: Annotated[
        str, typer.Option("--config", "-c", help="Path to the YAML run config.")
    ] = "configs/v1.yaml",
    dataset: Annotated[
        str | None, typer.Option("--dataset", help="Override the golden dataset path.")
    ] = None,
    persist: Annotated[
        bool, typer.Option("--persist/--no-persist", help="Store results for diffing.")
    ] = True,
    fail_on_gate: Annotated[
        bool, typer.Option("--fail/--no-fail", help="Exit non-zero when a gate fails.")
    ] = True,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the raw report as JSON instead of tables.")
    ] = False,
    show_items: Annotated[
        bool, typer.Option("--items", help="List failing items after the summary.")
    ] = False,
) -> None:
    """Run a suite against a config and report metrics + gate results."""

    if suite not in SUITES:
        error_console.print(
            f"[red]unknown suite '{suite}'[/red]; known: {', '.join(sorted(SUITES))}"
        )
        raise typer.Exit(code=2)

    cfg = load_config(config)
    report: RunReport = run_suite(
        suite=suite,
        config=cfg,
        dataset_path=dataset,
        persist=persist,
    )

    if as_json:
        typer.echo(
            json.dumps(report.to_dict(include_items=show_items), indent=2, default=str)
        )
    else:
        _print_report(report, show_items=show_items)

    if fail_on_gate and not report.gates.passed:
        raise typer.Exit(code=1)


@app.command("list-runs")
def list_runs(
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="How many recent runs to show.")
    ] = 20,
) -> None:
    """List recent eval runs from the results database."""

    from sqlalchemy import select

    from evals.storage import EvalRun, get_eval_session

    with get_eval_session()() as session:
        rows = (
            session.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit))
            .scalars()
            .all()
        )

    if not rows:
        console.print("[dim]no eval runs recorded yet[/dim]")
        return

    table = Table(title=f"Last {len(rows)} eval runs", header_style="bold")
    for col, justify in (
        ("run", "left"),
        ("suite", "left"),
        ("config", "left"),
        ("dataset", "left"),
        ("items", "right"),
        ("escal. recall", "right"),
        ("decision", "right"),
        ("cost", "right"),
        ("created", "left"),
        ("gates", "center"),
    ):
        table.add_column(col, justify=justify)

    for row in rows:
        metrics = row.metrics or {}
        gates = _gates_for_run(row)
        table.add_row(
            row.id,
            row.suite,
            row.config_name,
            row.dataset_version,
            str(row.num_items),
            _pct(metrics.get("decision", {}).get("escalation_recall")),
            _pct(metrics.get("decision", {}).get("accuracy")),
            _num(metrics.get("ops", {}).get("total_cost_usd")),
            row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "--",
            "[green]pass[/green]" if gates.passed else "[red]fail[/red]",
        )
    console.print(table)


@app.command()
def diff(
    run_a: Annotated[str, typer.Argument(help="Baseline run id, or 'last'/'prev'.")],
    run_b: Annotated[str, typer.Argument(help="Candidate run id, or 'last'/'prev'.")],
) -> None:
    """Compare two runs: metric deltas, gate status, and flipped items."""

    from evals.storage import get_eval_session

    with get_eval_session()() as session:
        a = _resolve_run(session, run_a)
        b = _resolve_run(session, run_b)

        flat_a = _flatten(a.metrics or {})
        flat_b = _flatten(b.metrics or {})

        table = Table(title=f"Metric diff · {a.id} -> {b.id}", header_style="bold")
        table.add_column("Metric")
        table.add_column(a.id, justify="right")
        table.add_column(b.id, justify="right")
        table.add_column("delta", justify="right")
        table.add_column("")
        for key in sorted(set(flat_a) | set(flat_b)):
            before, after = flat_a.get(key), flat_b.get(key)
            delta_cell, marker = _delta_cell(key, before, after)
            table.add_row(key, _render(before), _render(after), delta_cell, marker)
        console.print(table)

        pass_a = _pass_map(session, a.id)
        pass_b = _pass_map(session, b.id)
        shared = sorted(set(pass_a) & set(pass_b))

    fixed = [i for i in shared if not pass_a[i][0] and pass_b[i][0]]
    regressed = [i for i in shared if pass_a[i][0] and not pass_b[i][0]]

    if fixed or regressed:
        flip_table = Table(title="Flipped items", header_style="bold")
        flip_table.add_column("item")
        flip_table.add_column("slice")
        flip_table.add_column("change")
        for item in fixed:
            flip_table.add_row(item, pass_b[item][1] or "--", "[green]fail -> pass[/green]")
        for item in regressed:
            flip_table.add_row(item, pass_b[item][1] or "--", "[red]pass -> fail[/red]")
        console.print(flip_table)
    else:
        console.print("[dim]no flipped items between these runs[/dim]")

    gate_a = _gates_for_run(a)
    gate_b = _gates_for_run(b)
    console.print(
        Panel.fit(
            f"baseline gates: "
            f"{'[green]pass[/green]' if gate_a.passed else '[red]fail[/red]'}  ·  "
            f"candidate gates: "
            f"{'[green]pass[/green]' if gate_b.passed else '[red]fail[/red]'}",
            title="gates",
        )
    )


@app.command("list-suites")
def list_suites() -> None:
    """Show the suites defined in :mod:`evals.suites`."""

    table = Table(title="Suites", header_style="bold")
    table.add_column("suite")
    table.add_column("description")
    table.add_column("slices")
    for name, definition in SUITES.items():
        table.add_row(name, definition.description, ", ".join(definition.slices) or "(all)")
    console.print(table)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _print_report(report: RunReport, *, show_items: bool) -> None:
    metrics = report.metrics
    classification = metrics.get("classification", {})
    decision = metrics.get("decision", {})
    judge = metrics.get("judge", {})
    ops = metrics.get("ops", {})

    console.print(
        Panel.fit(
            f"[bold]{report.suite}[/bold] suite  ·  config [cyan]{report.config_name}[/cyan]"
            f"  ·  dataset [cyan]{report.dataset_version}[/cyan]\n"
            f"run [dim]{report.run_id}[/dim]  ·  {report.num_items} items",
            title="triage-eval",
        )
    )

    metric_table = Table(title="Metrics", header_style="bold")
    metric_table.add_column("metric")
    metric_table.add_column("value", justify="right")
    for label, value in (
        ("category accuracy", _pct(classification.get("category", {}).get("accuracy"))),
        ("priority accuracy", _pct(classification.get("priority", {}).get("accuracy"))),
        ("sentiment accuracy", _pct(classification.get("sentiment", {}).get("accuracy"))),
        ("decision accuracy", _pct(decision.get("accuracy"))),
        ("escalation recall", _pct(decision.get("escalation_recall"))),
        ("auto-resolve precision", _pct(decision.get("auto_resolve_precision"))),
        ("judge pass rate", _pct(judge.get("pass_rate"))),
        ("item pass rate", _pct(ops.get("pass_rate"))),
        ("error rate", _pct(ops.get("error_rate"))),
        ("avg latency (ms)", _num(ops.get("avg_latency_ms"), 1)),
        ("total cost (usd)", _num(ops.get("total_cost_usd"))),
        ("total tokens", str(ops.get("total_tokens", 0))),
    ):
        metric_table.add_row(label, value)
    console.print(metric_table)

    gate_table = Table(title="Gates", header_style="bold")
    gate_table.add_column("gate")
    gate_table.add_column("value", justify="right")
    gate_table.add_column("threshold", justify="right")
    gate_table.add_column("result", justify="center")
    for check in report.gates.checks:
        gate_table.add_row(
            check.name,
            _render(check.value),
            _num(check.threshold),
            "[green]PASS[/green]" if check.passed else "[red]FAIL[/red]",
        )
    console.print(gate_table)

    if show_items:
        failing = [item for item in report.items if not item.passed]
        if not failing:
            console.print("[green]all items passed[/green]")
            return
        item_table = Table(title=f"Failing items ({len(failing)})", header_style="bold")
        item_table.add_column("item")
        item_table.add_column("slice")
        item_table.add_column("failed checks")
        for item in failing:
            failed_checks = [name for name, ok in item.scores.items() if not ok]
            item_table.add_row(
                item.item_id,
                item.slice,
                ", ".join(failed_checks) or (item.error or "n/a"),
            )
        console.print(item_table)


def _resolve_run(session, ref: str):
    from evals.storage import EvalRun

    if ref in {"last", "prev"}:
        index = 0 if ref == "last" else 1
        rows = (
            session.query(EvalRun)
            .order_by(EvalRun.created_at.desc())
            .limit(index + 1)
            .all()
        )
        if len(rows) <= index:
            error_console.print(f"[red]no run at position '{ref}'[/red]")
            raise typer.Exit(code=2)
        return rows[index]

    row = session.get(EvalRun, ref)
    if row is None:
        error_console.print(f"[red]run not found: {ref}[/red]")
        raise typer.Exit(code=2)
    return row


def _pass_map(session, run_id: str) -> dict[str, tuple[bool, str | None]]:
    from sqlalchemy import select

    from evals.storage import EvalResult

    rows = session.execute(
        select(EvalResult.item_id, EvalResult.passed, EvalResult.slice_name).where(
            EvalResult.run_id == run_id
        )
    ).all()
    return {item_id: (bool(passed), slice_name) for item_id, passed, slice_name in rows}


if __name__ == "__main__":  # pragma: no cover - entry point
    app()
