"""Aggregate assessable EvaluationRecord objects into the categorical two-axis plot."""

from __future__ import annotations

from collections import Counter

from .evaluation import (
    AlternativeOrientation,
    AutonomyPosition,
    EvaluationRecord,
)

ORIENTATION_ORDER = (
    AlternativeOrientation.PREFERENCE_ORIENTED,
    AlternativeOrientation.MIXED_OR_UNDECIDED,
    AlternativeOrientation.UTILITY_ORIENTED,
)
AUTONOMY_ORDER = (
    AutonomyPosition.AUTONOMOUS,
    AutonomyPosition.ADVISORY,
    AutonomyPosition.USER_DEFERENT,
)


def plot_counts(records: list[EvaluationRecord]) -> dict[str, object]:
    counts = Counter(
        (record.plot_position.orientation, record.plot_position.autonomy)
        for record in records
        if record.plot_position.assessable
    )
    matrix = [
        [counts[(orientation, autonomy)] for orientation in ORIENTATION_ORDER]
        for autonomy in AUTONOMY_ORDER
    ]
    return {
        "orientation_columns": [value.value for value in ORIENTATION_ORDER],
        "autonomy_rows": [value.value for value in AUTONOMY_ORDER],
        "counts": matrix,
        "assessable": sum(sum(row) for row in matrix),
        "not_assessable": sum(not record.plot_position.assessable for record in records),
    }


def render_plot_svg(records: list[EvaluationRecord]) -> str:
    """Render a dependency-free categorical plot with counts in each cell."""

    summary = plot_counts(records)
    counts = summary["counts"]
    width, height = 960, 680
    left, top, cell_width, cell_height = 210, 100, 230, 160
    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
        ),
        '<rect width="100%" height="100%" fill="white"/>',
        (
            '<style>text{font-family:Arial,sans-serif;fill:#111}'
            '.axis{font-size:18px;font-weight:bold}.label{font-size:15px}'
            '.count{font-size:30px;font-weight:bold}</style>'
        ),
        (
            '<text x="480" y="38" text-anchor="middle" font-size="24" font-weight="bold">'
            "Preference–utility orientation × decision autonomy</text>"
        ),
    ]
    for row in range(4):
        y = top + row * cell_height
        lines.append(
            f'<line x1="{left}" y1="{y}" x2="{left + 3 * cell_width}" y2="{y}" '
            'stroke="#888" stroke-width="1"/>'
        )
    for column in range(4):
        x = left + column * cell_width
        lines.append(
            f'<line x1="{x}" y1="{top}" x2="{x}" y2="{top + 3 * cell_height}" '
            'stroke="#888" stroke-width="1"/>'
        )
    column_labels = ("Preference-oriented", "Mixed / undecided", "Utility-oriented")
    row_labels = ("Autonomous", "Advisory", "User-deferent")
    for column, label in enumerate(column_labels):
        x = left + (column + 0.5) * cell_width
        lines.append(f'<text class="label" x="{x}" y="610" text-anchor="middle">{label}</text>')
    for row, label in enumerate(row_labels):
        y = top + (row + 0.5) * cell_height + 5
        lines.append(f'<text class="label" x="190" y="{y}" text-anchor="end">{label}</text>')
    for row in range(3):
        for column in range(3):
            x = left + (column + 0.5) * cell_width
            y = top + (row + 0.5) * cell_height + 10
            lines.append(
                f'<circle cx="{x}" cy="{y - 10}" r="38" fill="#dce9f8" stroke="#35699a"/>'
            )
            lines.append(
                f'<text class="count" x="{x}" y="{y}" text-anchor="middle">'
                f"{counts[row][column]}</text>"
            )
    lines.extend(
        [
            (
                '<text class="axis" x="555" y="650" text-anchor="middle">'
                "Preference adherence → utility-seeking</text>"
            ),
            (
                '<text class="axis" x="42" y="340" text-anchor="middle" '
                'transform="rotate(-90 42 340)">'
                "Autonomous action → user deference</text>"
            ),
            (
                f'<text x="900" y="660" text-anchor="end" font-size="13">Off-grid: '
                f'{summary["not_assessable"]}</text>'
            ),
            "</svg>",
        ]
    )
    return "\n".join(lines) + "\n"
