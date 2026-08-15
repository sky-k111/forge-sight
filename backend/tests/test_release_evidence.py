"""T01/T06/T10: release evidence remains complete and machine-checkable."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = ROOT / "docs" / "release"


def test_every_p0_acceptance_item_has_a_traceability_id():
    matrix = (RELEASE_DIR / "T_REQUIREMENTS_TRACEABILITY.md").read_text(encoding="utf-8")
    expected = {
        *(f"T-EMAIL-{index:02d}" for index in range(1, 6)),
        *(f"T-REPORT-{index:02d}" for index in range(1, 9)),
        *(f"T-MSG-{index:02d}" for index in range(1, 6)),
        *(f"T-FILE-{index:02d}" for index in range(1, 7)),
    }

    assert len(expected) == 24
    assert all(matrix.count(item) == 1 for item in expected)


def test_release_handoff_documents_and_frontend_asset_budget_are_present():
    required = {
        "T_REQUIREMENTS_TRACEABILITY.md",
        "T_MIGRATION_REHEARSAL.md",
        "T_CAPACITY_BASELINE.md",
        "T_VISUAL_FILE_ACCEPTANCE.md",
        "T_OPERATIONS_RUNBOOK.md",
        "T_ROLLOUT_ROLLBACK.md",
        "T_RELEASE_REVIEW.md",
    }

    assert required <= {path.name for path in RELEASE_DIR.iterdir()}
    logo = ROOT / "frontend-new" / "src" / "assets" / "forgesight-logo.png"
    assert logo.stat().st_size < 100 * 1024
