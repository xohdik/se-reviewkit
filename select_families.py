"""Prospective, deterministic stratified selection from an adjudicated family frame."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path


def rank(seed, *fields):
    return hashlib.sha256(json.dumps([seed, *fields], ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def select(frame, seed, target):
    if not isinstance(frame, dict) or not isinstance(seed, str) or not seed.strip() or type(target) is not int or target < 1:
        raise ValueError('A frame, nonempty frozen seed and positive target are required')
    required = ('eligibility_complete', 'family_adjudication_complete', 'frame_frozen', 'protocol_author_approved')
    if any(frame.get(k) is not True for k in required):
        raise ValueError('STOP: final eligible family frame and author-approved protocol required')
    if not isinstance(frame.get('coordinator'), str) or not frame['coordinator'].strip():
        raise ValueError('STOP: coordinator required')
    stamp = frame.get('freeze_date')
    if not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp:
        raise ValueError('STOP: ISO freeze date required')
    digest = frame.get('adjudication_evidence_sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest):
        raise ValueError('STOP: adjudication inventory SHA-256 required')
    rows = frame.get('families')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Empty or malformed family frame')
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Family must be an object')
        for key in ('family_id', 'mechanism', 'venue_stratum', 'preferred_report_id',
                    'eligibility_adjudication_locator', 'family_adjudication_locator'):
            if not isinstance(row.get(key), str) or not row[key].strip() or row[key] != row[key].strip():
                raise ValueError(f'Missing or malformed {key}')
        if row['family_id'] in seen:
            raise ValueError('Duplicate family ID')
        seen.add(row['family_id'])
        groups[(row['mechanism'], row['venue_stratum'])].append(row)
    n = len(rows)
    size = min(target, n)
    quotas = {k: size*len(v)//n for k, v in groups.items()}
    order = sorted(groups, key=lambda k: (-(size*len(groups[k]) % n), rank(seed, 'quota', *k), k))
    for key in order[:size-sum(quotas.values())]:
        quotas[key] += 1
    result = []
    for key in sorted(groups):
        chosen = sorted(groups[key], key=lambda r: (rank(seed, 'family', r['family_id']), r['family_id']))[:quotas[key]]
        result.extend({**r, 'selection_rank_sha256': rank(seed, 'family', r['family_id']),
                       'claim_enumeration_status': 'not_started', 'replacement_allowed': False} for r in chosen)
    if len(result) != size:
        raise RuntimeError('Selection size mismatch')
    return {'seed': seed, 'target': target, 'eligible_family_count': n, 'selected_family_count': size,
            'strata': [{'mechanism': k[0], 'venue_stratum': k[1], 'population': len(groups[k]),
                        'selected': quotas[k]} for k in sorted(groups)], 'selected_families': result,
            'status': 'selected; independent claim enumeration and reconstruction pending',
            'note': 'Input attestations are checked for form, not truth. No confirmatory result is computed.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('frame', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--seed', required=True, help='Previously frozen protocol seed')
    parser.add_argument('--target', required=True, type=int, help='Previously approved family sample size')
    args = parser.parse_args(argv)
    try:
        raw = args.frame.read_bytes()
        data = select(json.loads(raw), args.seed, args.target)
        data['frame_sha256'] = hashlib.sha256(raw).hexdigest()
        with args.output.open('x', encoding='utf-8') as file:
            json.dump(data, file, indent=2, ensure_ascii=False, allow_nan=False)
            file.write('\n')
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(1, f'SE-ReviewKit: {exc}\n')


if __name__ == '__main__':
    main()
