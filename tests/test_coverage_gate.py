from tools.coverage_gate import main, percentages


def test_coverage_percentages_and_independent_thresholds(tmp_path, capsys):
    report = {
        "totals": {
            "num_statements": 100,
            "covered_lines": 91,
            "num_branches": 20,
            "covered_branches": 17,
        }
    }
    assert percentages(report) == (91.0, 85.0)
    path = tmp_path / "coverage.json"
    path.write_text(__import__("json").dumps(report), encoding="utf-8")

    assert main([str(path), "90", "85"]) == 0
    assert '"branch":85.0' in capsys.readouterr().out
    assert main([str(path), "92", "85"]) == 1
