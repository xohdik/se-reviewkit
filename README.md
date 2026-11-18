# SE-ReviewKit

**Auditable literature retrieval and evidence management for software engineering systematic reviews.**

Version 0.1.1 — research prototype. Python 3.10 or newer; no third-party packages. No account credentials, private manuscript, paid publisher exports, or review outcomes are included.

## Quick start (Windows PowerShell)

1. Extract this release and open PowerShell in the extracted `SE-ReviewKit` folder. Verify Python with `py --version`.
2. Copy your OpenAlex key from https://openalex.org/settings/api. Treat it as a password; never post it in a chat, issue, screenshot, or repository.
3. Run a small, capped pilot. Paste your key when prompted (characters are hidden).

```powershell
py slrkit.py search --config config/coding-agents-pilot.json --out runs/pilot-01 --max-pages 1
py slrkit.py merge --run runs/pilot-01 --allow-partial --out outputs/pilot-01
```

The first command fetches up to 100 records for each of four Boolean queries and up to 50 ranked results from the separate semantic query (at most 450 occurrences); the second creates an inspection-ready corpus, not an included-study count. Check `runs/pilot-01/manifest.json` and `outputs/pilot-01/report.json`. Do not present capped results as exhaustive.

### Recovering a v0.1.0 pilot that stopped on Q05 (HTTP 400)

Keep your original `runs/pilot-01` folder. The first four queries' raw responses remain valid and are checksum-verified by merge. The semantic endpoint supports year filters, not exact publication-date filters, and returns at most 50 ranked works. Version 0.1.1 sends a separate bounded semantic request and marks exact-date mismatches in provenance for review. No need to repeat the first four queries.

```powershell
py slrkit.py search --config config/coding-agents-pilot.json --only Q05_semantic_discovery --out runs/pilot-semantic-02
py slrkit.py merge --run runs/pilot-01 --run runs/pilot-semantic-02 --allow-partial --out outputs/pilot-02
```

Use a fresh output folder if any of these names already exist. To recover the four Boolean pages alone, omit the second `--run` argument. `--allow-partial` is intentional for the old failed and capped pilot; this is not a formal search.

On macOS/Linux use `python3` instead of `py`. An optional `OPENALEX_API_KEY` environment variable supports non-interactive execution. Never put keys on command lines or in committed config. User quotas still apply: review https://openalex.org/settings before scaling. The client defaults to a conservative 1.1-second delay between calls, retries transient failures, and stops on non-retryable HTTP errors. The official current limits and quotas can change.

## Workflow

1. **Calibrate:** revise the example strings with independent reviewers; freeze exact dates and queries, check retrieval of positive controls and reserve some seeds for holdout validation. The supplied configuration is a pilot, not a registered protocol.
2. **Retrieve:** choose a fresh run folder for each run. Omit `--max-pages` only after approving search scope and estimated quota. Raw API pages are stored byte-for-byte with hashes, query strings, dates, provider-reported hit counts, cursor-page counts and completion statuses.

```powershell
py slrkit.py search --config config/coding-agents-pilot.json --out runs/formal-01
```

3. **Citation chasing:** manually confirm seed OpenAlex work IDs, place one ID per line in `private/seeds.txt`. Use a fixed date boundary and record the seed selection rationale in the protocol. Forward chasing uses the `cites` filter; backward chasing retrieves indexed reference IDs. Date-window exclusions and incomplete citation coverage constrain the result.

```powershell
py slrkit.py chase --seeds private/seeds.txt --from-date 2022-01-01 --to-date 2026-11-18 --out runs/chase-01 --direction both
```

4. **Institutional sources:** use your library's authorized Scopus/WoS/ACM/IEEE search interface, preserve the exact query, hit counts, timestamp, filters and original CSV. Require title, abstract when available, DOI, year, authors, and source. Header aliases are supported for common CSV exports, but other formats must be converted transparently. Do not upload subscription-derived raw data publicly unless its terms permit. Institutional query logging is currently manual.

```powershell
py slrkit.py merge --run runs/formal-01 --run runs/chase-01 --csv "Scopus=private/scopus.csv" --csv "WoS=private/wos.csv" --out outputs/corpus-01
```

5. **Screen:** independently copy `title_abstract_screening.csv` for each reviewer. Use `include`, `exclude`, `uncertain`; write exclusion reasons, and resolve disagreements separately. Full-text screening, source-located evidence extraction and final study-family adjudication are human-controlled. Do not screen out missing abstracts automatically.

## Outputs and interpretation

| File | Function |
|---|---|
| `manifest.json` and `config.json` | Query and run provenance, completion state, hashes |
| `raw/*.json` | Original OpenAlex responses |
| `records.csv` | Identifier-linked candidate clusters |
| `provenance.csv` | Every retrieval occurrence, source, original row location and date-scope flag |
| `identifier_conflicts.csv` | Distinct DOI or OpenAlex IDs linked into a candidate cluster; requires adjudication |
| `version_candidates.csv` | Exact normalized-title matches with separate ID clusters; never silently merged |
| `*_screening.csv` | Blank human screening worksheets |
| `evidence_claims.csv` | Blank source-located evidence schema |
| `report.json` | Retrieval, overlap, missing-abstract and candidate counts (not inclusions) |

Identifier clusters are provisional, not independent study families. OpenAlex IDs themselves may unify preprint and published metadata; individual versions and conflicting identifiers must be checked against the source. Record IDs are deterministic for the current inputs but may change when new identifiers link clusters. Archive original outputs before a refresh, and reconcile by provenance rather than overwriting existing reviewer judgments. Title-only matches do not prove duplicates. Conflicts are flagged, not automatically resolved. CSV cells contain source-supplied text; inspect with a text editor or import as text (disable formulas) in spreadsheet software.

Source-exclusive candidate counts and overlaps support a later **coverage audit**: after screening, compare each source's unique eligible study families and leave-one-source-out losses. These are relative to the observed union, not estimates of absolute literature recall. The example semantic query uses year-level server filters and a 50-result limit; semantic results outside the exact protocol date range or with missing dates stay visible and flagged in provenance for review. A `ranked_limit` query status means the requested bounded discovery completed, not exhaustive recall. Its service-ranked retrieval limits and possible ranking drift preclude claiming exhaustive recall.

## Validation and limitations

```powershell
py -m unittest discover -s tests -v
```

Offline tests simulate the OpenAlex API and synthetic metadata. A real authenticated connection must be commissioned on the researcher's network; passing tests do not establish API availability, query adequacy, retrieval recall, AI screening accuracy, or scientific inclusion. The toolkit does not currently implement AI screening, semantic embedding locally, publisher-specific API adapters, screening consensus, automatic BibTeX export, or automatic revision linking. These are planned extensions. Use human validation and disclose any later AI use. Preserve raw licensed files privately and release only permitted metadata.

## Reproducibility and documentation

- OpenAlex API: https://help.openalex.org/api/
- Search and cursor paging: https://help.openalex.org/api/searching/ and https://help.openalex.org/api/paging/
- Citation chasing: https://help.openalex.org/how-to/api-recipes/
- PRISMA-S: https://doi.org/10.1186/s13643-020-01542-z
- TARCiS: https://doi.org/10.1136/bmj-2023-078384

This tool provides an auditable technical foundation; its use does not by itself establish compliance or methodological novelty. The substantive contribution of a review is the evidence synthesis, validated scope and research implications.

## Repository publication

Repository: https://github.com/xohdik/se-reviewkit. Keep research outputs in an ignored private directory; do not commit API credentials or unpublished review materials. The code is publicly accessible, but an explicit open-source license and archival release will be selected with the authors before formal redistribution.
