import math
import re
import statistics

CITATION_PATTERN = re.compile(r"\[Doc ID:\s*([^\]]+)\]", re.IGNORECASE)


def percentile(values, fraction):
    """Nearest-rank percentile; returns None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)) - 1, 0)]


def latency_summary(seconds):
    if not seconds:
        return {"count": 0, "mean": None, "median": None, "p95": None}
    return {
        "count": len(seconds),
        "mean": statistics.mean(seconds),
        "median": statistics.median(seconds),
        "p95": percentile(seconds, 0.95),
    }


def wilson_interval(successes, total, z=1.96):
    """95% interval for a proportion; stays sensible for small samples."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def classification_summary(expected, predicted, precision_target=0.85):
    """Per-class precision/recall plus the confusion matrix the framework asks for."""
    from sklearn.metrics import classification_report, confusion_matrix

    if not expected:
        return {
            "samples": 0,
            "per_class": {},
            "weighted_precision": None,
            "macro_precision": None,
            "classes_below_precision_target": [],
            "confusion_matrix": {"labels": [], "matrix": []},
        }
    labels = sorted(set(expected) | set(predicted))
    report = classification_report(
        expected, predicted, labels=labels, output_dict=True, zero_division=0
    )
    matrix = confusion_matrix(expected, predicted, labels=labels)
    predicted_counts = matrix.sum(axis=0)
    per_class = {
        label: {
            "precision": report[label]["precision"],
            "recall": report[label]["recall"],
            "f1": report[label]["f1-score"],
            "support": int(report[label]["support"]),
            "predicted": int(predicted_counts[index]),
        }
        for index, label in enumerate(labels)
    }
    return {
        "samples": len(expected),
        "per_class": per_class,
        "weighted_precision": report["weighted avg"]["precision"],
        "macro_precision": report["macro avg"]["precision"],
        "classes_below_precision_target": sorted(
            label
            for label, item in per_class.items()
            if item["predicted"] and item["precision"] < precision_target
        ),
        "confusion_matrix": {"labels": labels, "matrix": matrix.tolist()},
    }


def calibration_table(pairs, bands=5, tolerance_points=5.0, min_samples=10):
    """Compare stated confidence with observed accuracy per confidence band."""
    rows = []
    for index in range(bands):
        low, high = index / bands, (index + 1) / bands
        group = [
            (confidence, correct)
            for confidence, correct in pairs
            if low <= confidence < high or (index == bands - 1 and confidence == high)
        ]
        if not group:
            continue
        stated = sum(confidence for confidence, _ in group) / len(group)
        observed = sum(1 for _, correct in group if correct) / len(group)
        gap_points = (stated - observed) * 100
        rows.append(
            {
                "low": low,
                "high": high,
                "tickets": len(group),
                "stated_confidence": stated,
                "observed_accuracy": observed,
                "gap_points": gap_points,
                "low_sample": len(group) < min_samples,
                "within_tolerance": abs(gap_points) <= tolerance_points,
            }
        )
    reliable = [row for row in rows if not row["low_sample"]]
    return {
        "samples": len(pairs),
        "bands": rows,
        "max_abs_gap_points": max((abs(row["gap_points"]) for row in reliable), default=None),
        "passes": all(row["within_tolerance"] for row in reliable) if reliable else None,
    }


def cross_group_variation(segments, min_group=20):
    """Best-versus-worst quality gap, in percentage points, for each segment field."""
    fields = {}
    for field, groups in segments.items():
        scores = {name: item["quality_score"] for name, item in groups.items()}
        if len(scores) < 2:
            continue
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        fields[field] = {
            "best_group": best,
            "worst_group": worst,
            "gap_points": (scores[best] - scores[worst]) * 100,
            "smallest_group_tickets": min(item["tickets"] for item in groups.values()),
            "reliable": all(item["tickets"] >= min_group for item in groups.values()),
        }
    return {
        "fields": fields,
        "max_gap_points": max((item["gap_points"] for item in fields.values()), default=None),
        "reliable": bool(fields) and all(item["reliable"] for item in fields.values()),
    }
