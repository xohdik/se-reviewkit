"""Validate a private working evidence bundle and generate descriptive censuses.

No eligibility, final family membership, scientific validity or confirmatory
outcome is inferred. All records are supplied by the operator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(row, key):
    value = row.get(key)
    require(isinstance(value, str) and bool(value.strip()) and value == value.strip(),
            f"Missing or malformed {key}")
    return value


def index(rows, key):
    require(isinstance(rows, list), f"{key}: expected array")
    result = {}
    for row in rows:
        require(isinstance(row, dict), f"{key}: expected object")
        value = identifier(row, key)
        require(value not in result, f"Duplicate {key}: {value}")
        result[value] = row
    return result


def positive_int(value):
    return type(value) is int and value > 0


def census(mapping, groups, outcomes):
    """Check a report/group crosswalk and count mutually exclusive endpoints and overlapping RQs."""
    reports = index(mapping, 'report_id')
    families = index(groups, 'group_id')
    present = {gid: set() for gid in families}
    for rid, row in reports.items():
        gid = identifier(row, 'group_id')
        require(gid in families, f"Unknown group for {rid}")
        present[gid].add(rid)
    for gid, row in families.items():
        members = row.get('working_included_reports')
        require(isinstance(members, list) and bool(members) and all(isinstance(x, str) for x in members),
                f"Invalid report membership: {gid}")
        require(len(members) == len(set(members)) and set(members) == present[gid],
                f"Group membership disagrees with crosswalk: {gid}")
        representative = identifier(row, 'working_representative')
        require(representative in present[gid], f"Representative outside group: {gid}")
        for rid in members:
            require(reports[rid].get('working_representative') == representative,
                    f"Crosswalk representative disagrees: {rid}")

    require(isinstance(outcomes, list), 'outcomes must be an array')
    seen = set()
    counts = Counter()
    endpoint_groups = {}
    rq_rows = {}
    rq_groups = {}
    rq_eps = {}
    observed_groups = set()
    for row in outcomes:
        require(isinstance(row, dict), 'Outcome must be an object')
        oid = row.get('outcome_ordinal')
        require(positive_int(oid) and oid not in seen, 'Missing or duplicate outcome_ordinal')
        seen.add(oid)
        rid = identifier(row, 'unit_id')
        gid = identifier(row, 'working_group_id')
        require(rid in reports and reports[rid]['group_id'] == gid,
                f"Outcome {oid} has inconsistent report/group mapping")
        for key in ('exact_version', 'locator', 'metric', 'population', 'source_url'):
            identifier(row, key)
        url = urlsplit(row['source_url'])
        require(url.scheme in ('https', 'http') and bool(url.netloc) and not url.username and not url.password,
                f"Outcome {oid} needs a credential-free HTTP(S) source locator")
        endpoint = identifier(row, 'descriptive_endpoint_group')
        routes = row.get('rq_routes')
        require(isinstance(routes, list) and all(isinstance(x, str) and x.strip() for x in routes)
                and len(set(routes)) == len(routes), f"Invalid or duplicated RQ routes for {oid}")
        counts[endpoint] += 1
        endpoint_groups.setdefault(endpoint, set()).add(gid)
        observed_groups.add(gid)
        for rq in routes:
            rq_rows.setdefault(rq, []).append(oid)
            rq_groups.setdefault(rq, set()).add(gid)
            rq_eps.setdefault(rq, Counter())[endpoint] += 1
    return {
        'working_reports': len(reports), 'working_groups': len(families),
        'outcome_entries': len(outcomes), 'groups_with_outcomes': len(observed_groups),
        'endpoint_census': [{'endpoint': ep, 'rows': counts[ep], 'groups': len(endpoint_groups[ep])}
                            for ep in sorted(counts)],
        'rq_census': [{'rq': rq, 'rows': len(rq_rows[rq]), 'groups': len(rq_groups[rq]),
                       'working_group_percent': 100*len(rq_groups[rq])/len(families),
                       'endpoint_counts': dict(sorted(rq_eps[rq].items())),
                       'outcome_ordinals': sorted(rq_rows[rq])} for rq in sorted(rq_rows)],
        'final_included_studies': None, 'confirmatory_result': None,
        'note': 'Descriptive working accounting only; RQ routes overlap. No final study count or effect pooling.'
    }


def check_arithmetic(rows, outcome_ordinals):
    require(isinstance(rows, list), 'Arithmetic checks must be an array')
    for row in rows:
        require(isinstance(row, dict), 'Arithmetic check must be an object')
        oid = row.get('outcome_ordinal')
        require(positive_int(oid) and oid in outcome_ordinals, 'Arithmetic check references unknown outcome')
        n, d = row.get('numerator'), row.get('denominator')
        require(type(n) is int and positive_int(d), f"Invalid arithmetic inputs for outcome {oid}")
        actual = row.get('recomputed_percent_or_pp')
        require(type(actual) in (int, float) and math.isfinite(actual) and
                math.isclose(100*n/d, actual, abs_tol=1e-9, rel_tol=0),
                f"Arithmetic mismatch for outcome {oid}")
        for key in ('source_version', 'source_url', 'locator'):
            identifier(row, key)
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('mapping', 'groups', 'outcomes', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--arithmetic', type=Path)
    args = parser.parse_args(argv)
    try:
        data, hashes = {}, {}
        for name in ('mapping', 'groups', 'outcomes', 'arithmetic'):
            path = getattr(args, name)
            if path is not None:
                raw = path.read_bytes()
                data[name] = json.loads(raw)
                hashes[name] = hashlib.sha256(raw).hexdigest()
        result = census(data['mapping'], data['groups'], data['outcomes'])
        if 'arithmetic' in data:
            result['arithmetic_operations_rechecked'] = check_arithmetic(
                data['arithmetic'], {r['outcome_ordinal'] for r in data['outcomes']})
        result['input_sha256'] = hashes
        with args.out.open('x', encoding='utf-8') as file:
            json.dump(result, file, indent=2, ensure_ascii=False, allow_nan=False)
            file.write('\n')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'SE-ReviewKit: {exc}\n')


if __name__ == '__main__':
    main()
