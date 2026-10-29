# -*- coding: utf-8 -*-
"""Report generator module integrating text_utils and math_stats."""

from benchmarks.py2_app.text_utils import render_banner
from benchmarks.py2_app.math_stats import integer_average, generate_series


def build_summary_report(title, scores):
    """Generate a formatted legacy summary report."""
    banner = render_banner(title)
    avg_score = integer_average(scores)
    series = generate_series(start=1, count=len(scores))

    lines = [banner]
    lines.append("Average Score: %d" % avg_score)
    lines.append("Series Items: %s" % str(series))
    return "\n".join(lines)
