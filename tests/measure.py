"""Precision/recall of engine.extract_claims against tests/corpus/replies.jsonl.

Each corpus line is {"id", "text", "expected": [kind, ...], "category"}.
"expected" is the multiset of claim kinds a careful human reader would find in
that reply (empty list for negatives). We compare it against the multiset of
kinds extract_claims() actually returns, per entry, and roll the counts up
per kind and overall. Order within an entry is not compared: two claims of
the same kind in one reply only need to show up twice, not in a fixed slot.

Run: python3 tests/measure.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus", "replies.jsonl")


def load_corpus(path: str = CORPUS) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def score(rows: list[dict]) -> tuple[dict, dict, list]:
    """Return (per_kind stats, overall stats, mismatches). Stats dicts have tp/fp/fn."""
    per_kind: dict[str, dict[str, int]] = {}
    overall = {"tp": 0, "fp": 0, "fn": 0}
    misses = []

    for row in rows:
        expected = Counter(row.get("expected") or [])
        predicted = Counter(c.kind for c in engine.extract_claims(row["text"]))
        kinds = set(expected) | set(predicted)
        for kind in kinds:
            exp_n, pred_n = expected[kind], predicted[kind]
            tp = min(exp_n, pred_n)
            fp = max(0, pred_n - exp_n)
            fn = max(0, exp_n - pred_n)
            d = per_kind.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0})
            d["tp"] += tp
            d["fp"] += fp
            d["fn"] += fn
            overall["tp"] += tp
            overall["fp"] += fp
            overall["fn"] += fn
            if fp or fn:
                misses.append((row.get("id"), kind, fp, fn, row["text"]))
    return per_kind, overall, misses


def prf(d: dict) -> tuple[float, float]:
    tp, fp, fn = d["tp"], d["fp"], d["fn"]
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall


def main() -> None:
    rows = load_corpus()
    per_kind, overall, misses = score(rows)
    neg = sum(1 for r in rows if not r.get("expected"))
    print(f"Corpus: {len(rows)} entries ({neg} negative, {len(rows) - neg} positive)")
    print(f"{'kind':<12}{'tp':>4}{'fp':>4}{'fn':>4}{'precision':>11}{'recall':>9}")
    for kind in sorted(per_kind):
        d = per_kind[kind]
        p, r = prf(d)
        print(f"{kind:<12}{d['tp']:>4}{d['fp']:>4}{d['fn']:>4}{p:>11.3f}{r:>9.3f}")
    op, orc = prf(overall)
    print(f"{'OVERALL':<12}{overall['tp']:>4}{overall['fp']:>4}{overall['fn']:>4}{op:>11.3f}{orc:>9.3f}")
    if misses:
        print(f"\n{len(misses)} mismatches:")
        for entry_id, kind, fp, fn, text in misses:
            tag = "false positive" if fp else "false negative"
            print(f"  id={entry_id} kind={kind} ({tag}): {text!r}")


if __name__ == "__main__":
    main()
