"""Tests for the ``python -m cowic.sim`` and ``python -m cowic.analysis`` CLIs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cowic.analysis import __main__ as analysis_cli
from cowic.analysis.plotter import REQUIRED_COLUMNS
from cowic.sim import __main__ as sim_cli
from tests.test_plotter import sweep_results

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_sim_cli_runs_config_and_prints_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = sim_cli.main(
        [
            "--config",
            str(REPO_ROOT / "configs/exp1_uncoordinated.yaml"),
            "--duration",
            "2",
            "--trace-dir",
            str(tmp_path),
            "--json",
        ]
    )
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["experiment_id"] == "Exp_1"
    assert result["duration_s"] == 2.0
    assert result["cycles_run"] == 2
    for column in REQUIRED_COLUMNS[1:]:
        assert column in result
    assert list(tmp_path.glob("Exp_1_*_2s_42.parquet"))


def test_sim_cli_default_output_is_aligned_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = sim_cli.main(
        [
            "--config",
            str(REPO_ROOT / "configs/exp1_uncoordinated.yaml"),
            "--duration",
            "2",
            "--trace-dir",
            str(tmp_path),
        ]
    )
    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    rows = dict(line.split(None, 1) for line in lines)
    assert rows["experiment_id"] == "Exp_1"
    assert float(rows["duration_s"]) == 2.0
    assert float(rows["zigbee_gain_ratio"]) >= 0.0


def test_sim_cli_reports_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sim_cli.main(["--config", str(tmp_path / "nope.yaml")]) == 2
    assert "config not found" in capsys.readouterr().err


def test_sim_cli_rejects_non_positive_duration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = sim_cli.main(
        [
            "--config",
            str(REPO_ROOT / "configs/exp1_uncoordinated.yaml"),
            "--duration",
            "0",
            "--trace-dir",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "duration_s" in capsys.readouterr().err


def test_sim_cli_reports_non_mapping_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_config = tmp_path / "scalar.yaml"
    bad_config.write_text("42\n")
    assert sim_cli.main(["--config", str(bad_config)]) == 2
    assert "YAML mapping" in capsys.readouterr().err


def test_analysis_cli_redraws_figures_from_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_path = tmp_path / "results.csv"
    sweep_results().to_csv(csv_path, index=False)
    out = tmp_path / "figs"
    code = analysis_cli.main(["--results-csv", str(csv_path), "--output-dir", str(out)])
    assert code == 0
    assert sorted(p.name for p in out.glob("*.png")) == [
        "fig11_Traffic_I_tzb1000.png",
        "fig16_sensing.png",
    ]
    assert "Wrote" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (None, 2),
        ("", 2),
        ("experiment_id\nExp_1\n", 2),
        ("a,b,c\n1,2\n3,4,5,6\n", 2),  # Extra field: pandas raises ParserError.
    ],
)
def test_analysis_cli_reports_bad_csv(
    tmp_path: Path, content: str | None, code: int
) -> None:
    csv_path = tmp_path / "results.csv"
    if content is not None:
        csv_path.write_text(content)
    assert analysis_cli.main(["--results-csv", str(csv_path)]) == code


def test_analysis_cli_reports_csv_without_sweep_rows(tmp_path: Path) -> None:
    results = sweep_results()
    results["sweep"] = "other"
    csv_path = tmp_path / "results.csv"
    results.to_csv(csv_path, index=False)
    code = analysis_cli.main(
        ["--results-csv", str(csv_path), "--output-dir", str(tmp_path / "f")]
    )
    assert code == 1


@pytest.mark.parametrize("module", ["cowic.sim", "cowic.analysis"])
def test_modules_run_as_scripts(module: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-W", "error", "-m", module, "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"usage: python -m {module}" in proc.stdout
