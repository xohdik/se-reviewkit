"""Experimental claim-audit utilities for reproducible evidence ledgers."""
import argparse
from collections import Counter
from itertools import combinations
import hashlib
import json
from fractions import Fraction
from math import ceil, floor
from pathlib import Path
import sys
from urllib.parse import urlsplit

REQUIRED = ("claim_id", "family", "source", "version", "locator",
            "configuration", "metric", "target_population", "evidence_level",
            "status", "reference", "raw_artifact_status")


def load_jsonl(path):
    raw = Path(path).read_bytes()
    records = []
    for line_no, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"line {line_no}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"line {line_no}: expected object")
        records.append(item)
    if not records:
        raise ValueError("empty ledger")
    return records, hashlib.sha256(raw).hexdigest()


def validate(records):
    seen = set()
    for r in records:
        for key in REQUIRED:
            if not isinstance(r.get(key), str) or not r[key].strip():
                raise ValueError(f"{r.get('claim_id', '?')}: missing or invalid {key}")
        cid = r["claim_id"]
        if cid in seen:
            raise ValueError(f"duplicate claim_id: {cid}")
        seen.add(cid)
        url = urlsplit(r["source"])
        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError(f"{cid}: source must be an HTTP(S) locator")
        if "denominator" not in r:
            raise ValueError(f"{cid}: denominator must be explicit")
        n = r["denominator"]
        if n is not None and (type(n) is not int or n <= 0):
            raise ValueError(f"{cid}: denominator must be positive integer or null")
        for key in ("reported", "derived", "human_eligibility", "human_claim_adjudication"):
            if key not in r:
                raise ValueError(f"{cid}: missing explicit {key}")
    return {
        "claims": len(records),
        "families": len({r["family"] for r in records}),
        "evidence_levels": dict(Counter(r["evidence_level"] for r in records)),
        "statuses": dict(Counter(r["status"] for r in records)),
        "null_denominator": sum(r["denominator"] is None for r in records),
        "human_eligibility_pending": sum(r["human_eligibility"] is None for r in records),
        "human_claim_adjudication_pending": sum(r["human_claim_adjudication"] is None for r in records),
        "note": "Structural validation only; no inference of eligibility or validity.",
    }


def compare(records):
    """Compare two explicit binary outcomes on unique shared IDs."""
    ids = set()
    counts = Counter({k: 0 for k in ("both", "left_only", "right_only", "neither",
                                     "left_missing", "right_missing", "both_missing")})
    for r in records:
        ident = r.get("instance_id")
        if not isinstance(ident, str) or not ident.strip() or ident in ids:
            raise ValueError(f"missing or duplicate instance_id: {ident}")
        ids.add(ident)
        for key in ("left", "right"):
            if key not in r or (r[key] is not None and type(r[key]) is not bool):
                raise ValueError(f"{ident}: {key} must be true, false or null")
        a, b = r["left"], r["right"]
        if a is None and b is None:
            key = "both_missing"
        elif a is None:
            key = "left_missing"
        elif b is None:
            key = "right_missing"
        else:
            key = {(True, True): "both", (True, False): "left_only",
                   (False, True): "right_only", (False, False): "neither"}[a, b]
        counts[key] += 1
    paired = sum(counts[k] for k in ("both", "left_only", "right_only", "neither"))
    return dict(universe=len(ids), paired=paired, cells=dict(counts),
                right_minus_left_pp=100 * (counts["right_only"] - counts["left_only"]) / paired if paired else None,
                note="Complete-pair descriptive difference; missing outcomes not imputed.")


def rounded_count_interval(percent, n, decimals):
    """Conservative exact count bounds under rounding to nearest percentage."""
    if type(n) is not int or n <= 0 or type(decimals) is not int or not 0 <= decimals <= 12:
        raise ValueError("invalid denominator or displayed precision")
    try:
        p = Fraction(str(percent))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("invalid percentage") from exc
    if not 0 <= p <= 100 or (p * (10 ** decimals)).denominator != 1:
        raise ValueError("percentage outside range or inconsistent with precision")
    half = Fraction(1, 2 * 10 ** decimals)
    lower = max(0, ceil((p - half) * n / 100))
    upper = min(n, floor((p + half) * n / 100))
    k = max(0, upper - lower + 1)
    return {"minimum": lower if k else None, "maximum": upper if k else None,
            "candidate_count": k, "unique": lower if k == 1 else None}


def unalerted_event_bounds(event_percent, alert_percent, decimals=1):
    """Sharp marginal bounds on event without alert, allowing display rounding.

    Rates must describe the same population. Results are percentage points,
    not counts or estimates of the unknown joint distribution.
    """
    if type(decimals) is not int or not 0 <= decimals <= 12:
        raise ValueError("invalid displayed precision")
    p, q = Fraction(str(event_percent)), Fraction(str(alert_percent))
    if any(not 0 <= v <= 100 or (v * 10**decimals).denominator != 1 for v in (p, q)):
        raise ValueError("invalid displayed rates")
    h = Fraction(1, 2 * 10**decimals)
    pl, pu = max(0, p-h), min(100, p+h)
    ql, qu = max(0, q-h), min(100, q+h)
    return {"display_lower": float(max(0, p-q)), "display_upper": float(min(p, 100-q)),
            "rounding_lower": float(max(0, pl-qu)), "rounding_upper": float(min(pu, 100-ql))}


def paired_counts_from_rounded_marginals(n, baseline_percent, alternative_percent,
                                        decimals, baseline_only, alternative_only):
    """Exact feasible joint partitions given rounded margins and known discordants.

    Each output tuple is (baseline successes, alternative successes, both, neither).
    Empty output means the supplied inputs cannot jointly hold; no imputation.
    """
    if any(type(x) is not int or x < 0 for x in (baseline_only, alternative_only)):
        raise ValueError("discordant counts must be nonnegative integers")
    if baseline_only + alternative_only > n:
        raise ValueError("discordant counts exceed denominator")
    left = rounded_count_interval(baseline_percent, n, decimals)
    right = rounded_count_interval(alternative_percent, n, decimals)
    if not left['candidate_count'] or not right['candidate_count']:
        return []
    # Intersection bounds avoid enumerating impossible Cartesian products.
    lo = max(0, left['minimum'] - baseline_only, right['minimum'] - alternative_only)
    hi = min(n-baseline_only-alternative_only, left['maximum']-baseline_only,
             right['maximum']-alternative_only)
    return [(both+baseline_only, both+alternative_only, both,
             n-both-baseline_only-alternative_only) for both in range(lo,hi+1)]


def paired_marginal_bounds(n, baseline, alternative):
    """Sharp overlap and turnover bounds for two complete binary marginals."""
    if type(n) is not int or n <= 0 or any(type(x) is not int or not 0 <= x <= n for x in (baseline, alternative)):
        raise ValueError("invalid complete binary margins")
    lo, hi = max(0, baseline + alternative - n), min(baseline, alternative)
    return dict(both=[lo, hi], losses=[baseline-hi, baseline-lo],
                gains=[alternative-hi, alternative-lo],
                turnover=[baseline+alternative-2*hi, baseline+alternative-2*lo])


def portfolio_audit(universe, configurations):
    """Audit recorded success membership across an explicitly aligned task universe.

    Each configuration maps every task ID to success, non_success, error, or missing.
    Errors/missing never earn released success credit; bounds allow their hypothetical
    resolution without treating them as observed outcomes.
    """
    ids = list(universe)
    if not ids or any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("expected a nonempty, unique string-ID universe")
    if not configurations or any(not isinstance(k, str) or not k for k in configurations):
        raise ValueError("expected named configurations")
    u = set(ids)
    labels = ("success", "non_success", "error", "missing")
    names = list(configurations)
    successes, possible, counts = {}, {}, {}
    for name, row in configurations.items():
        if set(row) != u or any(v not in labels for v in row.values()):
            raise ValueError("each configuration must explicitly classify every universe ID")
        successes[name] = {i for i, v in row.items() if v == "success"}
        possible[name] = {i for i, v in row.items() if v != "non_success"}
        counts[name] = {label: sum(v == label for v in row.values()) for label in labels}
    union = set.union(*successes.values())
    intersection = set.intersection(*successes.values())
    freq = Counter(sum(i in successes[k] for k in names) for i in ids)
    unique = {k: sorted(successes[k] - set().union(*(successes[j] for j in names if j != k))) for k in names}
    pairs = []
    for a, b in combinations(names, 2):
        sa, sb = successes[a], successes[b]
        pairs.append(dict(left=a, right=b, both=len(sa & sb), left_only=len(sa - sb),
                          right_only=len(sb - sa), neither_credited=len(u - sa - sb),
                          turnover=len(sa ^ sb),
                          joint_known=sum(configurations[a][i] in labels[:2] and configurations[b][i] in labels[:2] for i in ids)))
    return dict(n=len(ids), configurations=counts, union_credited=len(union),
                intersection_credited=len(intersection), union_upper=len(set.union(*possible.values())),
                intersection_upper=len(set.intersection(*possible.values())),
                multiplicity={str(i): freq[i] for i in range(len(names)+1)},
                unique_success_ids=unique, pairs=pairs)


def transition_matrix(matrix, expected_n):
    """Rows: fail→success, success→fail, both success, both fail.
    Columns: no→no, no→yes, yes→no, yes→yes for another binary behavior.
    """
    if type(expected_n) is not int or expected_n <= 0 or not isinstance(matrix, list) or len(matrix) != 4:
        raise ValueError("invalid transition matrix or denominator")
    if any(not isinstance(row, list) or len(row) != 4 or
           any(type(x) is not int or x < 0 for x in row) for row in matrix):
        raise ValueError("matrix must contain nonnegative integer counts in 4x4 shape")
    gains, losses, both, neither = map(sum, matrix)
    if gains + losses + both + neither != expected_n:
        raise ValueError("matrix total does not match denominator")
    return {"n": expected_n, "gains": gains, "losses": losses,
            "both_success": both, "both_fail": neither,
            "baseline_success": both + losses, "intervention_success": both + gains,
            "net_pp": 100 * (gains - losses) / expected_n,
            "turnover_pct": 100 * (gains + losses) / expected_n,
            "behavior_column_totals": [sum(row[j] for row in matrix) for j in range(4)],
            "net_per_behavior": [matrix[0][j] - matrix[1][j] for j in range(4)]}


def joint_binary_partition(cells, expected_n):
    """Summarize counts ordered both, left-only, neither, right-only, missing."""
    if type(expected_n) is not int or expected_n <= 0 or not isinstance(cells, (list, tuple)) or len(cells) != 5:
        raise ValueError("expected five outcome cells and positive denominator")
    if any(type(c) is not int or c < 0 for c in cells) or sum(cells) != expected_n:
        raise ValueError("invalid outcome counts or denominator mismatch")
    both, left_only, neither, right_only, missing = cells
    left, right = both + left_only, both + right_only
    return dict(n=expected_n, both=both, left_only=left_only, neither=neither,
                right_only=right_only, missing=missing, left_pass=left, right_pass=right,
                left_pass_pct=100*left/expected_n, right_pass_pct=100*right/expected_n,
                both_pct=100*both/expected_n, gap_pp=100*left_only/expected_n,
                right_given_left_pct=100*both/left if left else None,
                left_only_given_left_pct=100*left_only/left if left else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "compare"))
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, help="New output path (never overwrites)")
    args = parser.parse_args(argv)
    try:
        records, digest = load_jsonl(args.input)
        report = (validate if args.command == "validate" else compare)(records)
        report["sha256"] = digest
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.out:
            with args.out.open("x", encoding="utf-8") as f:
                f.write(encoded)
        print(encoded, end="")
        return 0
    except (OSError, ValueError) as exc:
        print(f"claim-audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
