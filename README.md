# SE-ReviewKit

**Auditable literature retrieval and evidence management for software engineering systematic reviews.**

Version 0.6.1 — research prototype. Python 3.10 or newer; no third-party packages. No account credentials, private manuscript, paid publisher exports, or review outcomes are included.

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

### Scopus API (v0.2.0)

An Elsevier API key is not proof of data entitlement; use only an authorized academic account and institutional network or approved institutional token. Check https://dev.elsevier.com/academic_research_scopus.html and your institution's agreement before retrieval or sharing. Never purchase access solely to run this toolkit. Start with one query and one STANDARD page to check permission:

```powershell
py slrkit.py scopus-search --config config/scopus-coding-agents-pilot.json --only S01_agent_verification --out runs/scopus-check-01 --max-pages 1
py slrkit.py merge --run runs/scopus-check-01 --allow-partial --out outputs/scopus-check-01
```

Enter the Scopus key at the hidden prompt. Optional environment variables are `SCOPUS_API_KEY` and `SCOPUS_INST_TOKEN` (only use an institution-issued token). Do not paste credentials into the config. A 401/403 stops the run; check entitlement with your library or Elsevier. A 429 stops rather than retrying through a quota.

After a successful pilot, run the four-query example using `--max-pages 1` in a new directory. The default STANDARD view may return little or no abstract text; retain such records for review and obtain abstracts through permitted sources. If authorized, `--view COMPLETE` requests richer metadata (maximum 25 records per page). STANDARD supports up to 200 with `--count`; check the account's actual limits. Omit the pilot cap only after checking query coverage and entitlement. Offset pagination is capped at 5,000 results per query; the tool refuses to mark larger queries complete. Use authorized cursor retrieval (not implemented) or protocol-defined, non-overlapping year partitions if necessary and permitted.

Scopus server-side date filtering is year-granularity; the returned cover date is not necessarily first publication date. Provenance flags the cover date against the requested window; human date adjudication is still required. The raw query pages and provider hit counts are kept in the run directory. The merge links DOI, OpenAlex IDs and Scopus EIDs and preserves source-specific IDs in mixed-source outputs. Use a new merge directory; record IDs may change when new identifiers are linked.

Example after successful, uncapped retrieval:

```powershell
py slrkit.py merge --run runs/formal-01 --run runs/scopus-full-01 --out outputs/corpus-02
```

Do not redistribute licensed raw responses or abstracts without permission. Share only permissible aggregate statistics initially, and validate the data-use policy before transferring raw material to another service.

### Semantic Scholar bulk search (v0.3.0)

The Academic Graph bulk endpoint supports title/abstract Boolean retrieval with continuation tokens and up to 1,000 results per call. Use its provider-specific operators (`+` for AND and `|` for OR), not the syntax of Scopus or OpenAlex. The example below tries a single query and at most one page, with the public unauthenticated API:

```powershell
py slrkit.py s2-search --config config/s2-coding-agents-pilot.json --only T01_agent_verification --out runs/s2-check-01 --anonymous --max-pages 1
```

Anonymous access is subject to shared throttling and may be unavailable. If HTTP 429 or authentication errors occur, stop and request a free API key at https://www.semanticscholar.org/product/api. Remove `--anonymous` to enter the key at the hidden prompt (alternatively set `SEMANTIC_SCHOLAR_API_KEY`). After confirming access, use a fresh directory and omit the cap for the chosen calibrated queries. A bulk search's reported total is an estimate; completion is determined by reaching the end of continuation tokens, not equality to that estimate. Null publication dates are preserved and year-only dates separately flagged. Citation counts and publication types are not eligibility filters. Respect the API's terms and applicable metadata licenses.

Merge complete runs using repeated `--run` arguments, as for the other providers. Semantic Scholar paper IDs and DOI links are preserved; title-only matches remain unresolved. No research eligibility decision is automated.

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

### arXiv preprint search (v0.4.0; no API key)

Use the same Boolean search configuration as OpenAlex. The adapter translates each term or quoted phrase into a title-or-abstract clause; it skips semantic queries when running the whole configuration. Query matching is provider-specific, so translated searches are not necessarily equivalent in recall. The submitted-date window is UTC.

```powershell
py slrkit.py arxiv-search --config config/coding-agents-pilot.json --only Q01_agent_verification --out runs/arxiv-check-01 --max-pages 1
py slrkit.py arxiv-search --config config/coding-agents-pilot.json --out runs/arxiv-full-01
```

Run the uncapped command only after reviewing the capped pilot. The client uses 100 results per page and a default 3.1-second delay between calls, sequentially. Do not run concurrent arXiv searches. Refine or partition large queries; the adapter refuses to mark changed totals, repeated IDs, incomplete pages or more than 30,000 results complete. Raw XML and checksummed query manifests are retained. A network or access failure stops the run.

Merge completed arXiv runs just like other providers. Version suffixes on arXiv IDs are normalized for identity linking, while the observed version URLs are retained. A publisher DOI in arXiv metadata is a relationship, not automatic proof of identity with a published paper; record it separately and adjudicate study families. Only exact shared identifiers are merged. Preprints and peer-reviewed publications must be distinguished during screening. No full-text downloads are performed.

### Crossref complementary discovery (v0.5.0; no API key)

Crossref searches are fuzzy ranked bibliographic queries, not equivalent to Boolean title/abstract searches. Use explicit natural-language queries and a predeclared rank cutoff. The example below requests at most 25 candidates for one query; the full example requests at most 200 per query (2,400 occurrences across twelve queries). The provider hit count is **not** the retrieved count or a claim of recall.

```powershell
py slrkit.py crossref-search --config config/crossref-coding-agents-pilot.json --only C01_agent_verification --out runs/crossref-check-01 --top 25
py slrkit.py crossref-search --config config/crossref-coding-agents-pilot.json --out runs/crossref-ranked-01 --top 200
```

The adapter retains the exact date filters, relevance order, raw response, hashes, query text and requested rank limit. `ranked_limit` means the bounded discovery was completed, not exhaustive. The public API requires no key; optionally set `CROSSREF_MAILTO` in the environment for Crossref's polite pool. The client uses a two-second or longer gap between requests and stops on HTTP rate limiting. Missing abstracts and partial publication dates remain visible; no dates are invented. DOI identity linking works with all other sources. Provider-specific differences and study-family decisions still require review. Check the pilot's relevance before choosing and freezing the rank cutoff.

### DBLP snapshot search (v0.6.0; offline)

Download an official timestamped XML snapshot and its associated DTD from https://dblp.org/xml/release/ (the current DTD is also at https://dblp.org/xml/dblp.dtd). Keep the original `.xml.gz` compressed file. This example scans the entire snapshot once, using the same Boolean configuration as OpenAlex but matching exact normalized title words and phrases only. It skips semantic queries. The scan is deliberately not equivalent to a title/abstract search: DBLP has bibliographic metadata and year, not abstracts or reliable day-level publication dates.

```powershell
py slrkit.py dblp-search --config config/coding-agents-pilot.json --snapshot "C:\Downloads\dblp-2026-11-01.xml.gz" --dtd "C:\Downloads\dblp.dtd" --snapshot-citation "10.4230/dblp.xml.2026-11-01" --out runs/dblp-full-01
```

The parser streams the compressed XML without expanding it to disk or loading the entire catalogue into memory. It resolves only the explicitly supplied local DTD (no network accesses), checksums both input files, retains matched records and source query provenance, reports publications scanned, and uses exact DBLP record keys and unambiguous DOI identity for linking. Multiple DOI candidates remain flagged for adjudication. Use a fixed snapshot DOI for reproducibility and record that the snapshot may predate the review's date cutoff. No payment or API key is required. Do not claim matched title records are included studies.

Version 0.6.1 uses bounded XML parser segments, restarting only at verified top-level publication boundaries, to avoid the Expat 2 GiB document-index limitation seen on some Windows builds. If a prior scan failed, retain the failed manifest and rerun into a fresh output directory with the same snapshot and DTD. The previous partial scan must not be treated as complete.

## Outputs and interpretation

| File | Function |
|---|---|
| `manifest.json` and `config.json` | Query and run provenance, completion state, hashes |
| `raw/*.json` or `raw/*.xml` | Original API responses (keep licensed data private) |
| `records.csv` | Identifier-linked candidate clusters |
| `provenance.csv` | Every retrieval occurrence, source, original row location and date-scope flag |
| `identifier_conflicts.csv` | Distinct DOI, OpenAlex IDs or Scopus EIDs linked into a candidate cluster; requires adjudication |
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

Offline tests simulate OpenAlex, Scopus, Semantic Scholar, arXiv and Crossref responses and DBLP XML snapshots using synthetic metadata. A real connection or snapshot scan must be commissioned on the researcher's machine; passing tests do not establish API availability, query adequacy, retrieval recall, AI screening accuracy, or scientific inclusion. The toolkit does not currently implement AI screening, semantic embedding locally, the Web of Science API adapter, screening consensus, automatic BibTeX export, or automatic revision linking. These are planned extensions. Use human validation and disclose any later AI use. Preserve raw licensed files privately and release only permitted metadata.

## Reproducibility and documentation

- OpenAlex API: https://help.openalex.org/api/
- Scopus Search API: https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl
- Scopus access and quotas: https://dev.elsevier.com/api_key_settings.html
- Semantic Scholar Academic Graph API: https://api.semanticscholar.org/api-docs/
- arXiv API manual: https://info.arxiv.org/help/api/user-manual.html
- arXiv API terms: https://info.arxiv.org/help/api/tou.html
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- DBLP XML parsing: https://dblp.org/faq/How+to+parse+dblp+xml.html
- Search and cursor paging: https://help.openalex.org/api/searching/ and https://help.openalex.org/api/paging/
- Citation chasing: https://help.openalex.org/how-to/api-recipes/
- PRISMA-S: https://doi.org/10.1186/s13643-020-01542-z
- TARCiS: https://doi.org/10.1136/bmj-2023-078384

This tool provides an auditable technical foundation; its use does not by itself establish compliance or methodological novelty. The substantive contribution of a review is the evidence synthesis, validated scope and research implications.

## Repository publication

Repository: https://github.com/xohdik/se-reviewkit. Keep research outputs in an ignored private directory; do not commit API credentials or unpublished review materials. The code is publicly accessible, but an explicit open-source license and archival release will be selected with the authors before formal redistribution.
