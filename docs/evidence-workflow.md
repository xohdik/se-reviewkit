# Evidence integrity and reproducibility workflow

All commands operate on researcher-supplied local files. Keep inputs and outputs inside `private/` or another ignored location. Do not publish private study data, publisher archives or reviewer identities. These are structural audits, not replacements for research judgments.

## 1. Independent reviewer calibration

Make two private directories with the same immutable source metadata and separately recorded responses. Each directory contains:

| File | Immutable columns |
| --- | --- |
| `screening_original.csv` | `record_id`, `title`, `year`, `type`, `doi`, `url`, `abstract` |
| `screening_supplementary.csv` | `component`, `title`, `year`, `venue`, `type`, `url`, `abstract` |
| `calibration.jsonl`, `remaining.jsonl` | `claim_id`, `family`, `source`, `version`, `locator`, `configuration`, `metric`, `target_population` |

Screening response columns: `reviewer_id`, `decision`, `reason_code`, `rationale`, `reviewed_utc`. The supported reason codes and claim response enums are declared in `review_calibration.py`. For each review, use a timezone-qualified ISO timestamp and a stable reviewer ID; never copy another reviewer's responses. JSONL claim responses record source access, observed reported values and denominator, denominator kind, locator/scope confirmation, artifact availability, notes, reviewer ID and time. Missing values remain null or blank.

```sh
python review_calibration.py freeze private/reviewer-a --out private/frozen.json
python review_calibration.py audit private/reviewer-a private/reviewer-b --frozen private/frozen.json --out private/calibration-audit
```

Freeze before responses are collected; only source metadata is fingerprinted. A successful audit writes complete-pair statistics, invalid-row diagnostics and separate unresolved screening and source-claim queues. Unresolved and invalid decisions are not exclusions. Complete agreement does not create an inclusion decision. A separate coordinator records adjudication outside this tool.

## 2. Working family reconciliation and evidence census

`evidence_reconcile.py` consumes three JSON arrays and an optional fourth arithmetic array. The following rows are synthetic examples only.

`mapping.json`:

```json
[{"report_id":"r1","group_id":"g1","working_representative":"r1"}]
```

`groups.json`:

```json
[{"group_id":"g1","working_included_reports":["r1"],"working_representative":"r1"}]
```

`outcomes.json`:

```json
[{"outcome_ordinal":1,"unit_id":"r1","working_group_id":"g1","exact_version":"v1","source_url":"https://example.invalid/paper","locator":"Table 1","metric":"accuracy","population":"synthetic tasks","descriptive_endpoint_group":"performance","rq_routes":["RQ1","RQ2"]}]
```

`arithmetic.json` (optional):

```json
[{"outcome_ordinal":1,"numerator":3,"denominator":4,"recomputed_percent_or_pp":75,"source_version":"v1","source_url":"https://example.invalid/paper","locator":"Table 1"}]
```

```sh
python evidence_reconcile.py --mapping private/mapping.json --groups private/groups.json --outcomes private/outcomes.json --arithmetic private/arithmetic.json --out private/census.json
```

The command rejects duplicate report, group and outcome IDs, inconsistent memberships and representatives, invalid source locators, duplicate routing labels and arithmetic mismatches. It hashes inputs, computes exclusive endpoint counts, distinct group counts and overlapping RQ routing counts. Groups do not equal finally included studies; an outcome may contribute to several research questions. No pooled effect or confirmatory result is created. The arithmetic check is a deterministic recalculation, not a validation of the source's interpretation.

## 3. Reference integrity

```sh
python reference_audit.py private/references.jsonl --archive-root private/extracted-archives --out private/reference-audit.json
```

The archived ledger requires a citation key, component, title, year, author metadata, DOI (or explicit `doi_status: not_assigned`), archive, page, row and SHA-256. The optional archive root verifies exact local page bytes and rejects paths outside that root. A missing DOI is not automatically an error if explicitly documented. Results flag collisions, missing metadata, invalid locators and digest mismatches; they never merge references or claim publisher/source identity has been verified. Read and resolve all flags before reporting verification.

## 4. Prospective confirmatory family selection

Do not select until eligibility and family adjudication are complete, the frame is frozen, and the protocol is approved. The JSON object has four true boolean attestations (`eligibility_complete`, `family_adjudication_complete`, `frame_frozen`, `protocol_author_approved`), `coordinator`, ISO `freeze_date`, `adjudication_evidence_sha256`, and `families`. Each family has `family_id`, `mechanism`, `venue_stratum`, `preferred_report_id`, `eligibility_adjudication_locator` and `family_adjudication_locator`.

```sh
python select_families.py private/final-family-frame.json private/selection.json --seed previously-frozen-seed --target 25
```

The target and seed must be set in the protocol before observing outcomes. Proportional allocation uses largest remainders across mechanism × venue strata, with deterministic SHA-256 ties and within-stratum ranking. Empty or unapproved frames fail closed. Preserve all selected families, even if access or reconstruction fails; no substitution. Selection is not a completed reconstruction. The program cannot verify the truth of the attestations. Preserve a frame digest, separately enumerate claims, freeze estimands and tolerances, then record scoped reconstruction statuses and missingness.

## 5. Offline test and release checks

```sh
python -m unittest discover -s tests -v
python -m pip install --no-deps .
se-reviewkit --version
```

Tests use only synthetic records and mocked providers. They do not access live sources or claim scientific completion. All output paths above must be new; archives should be retained as immutable checkpoints.
