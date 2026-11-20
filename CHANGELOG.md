# Changelog

## 0.8.0

- Added a working-family reconciliation and evidence census CLI, including archived arithmetic validation and input digests.
- Added prospective deterministic family selection with explicit adjudication and protocol gates; no automatic confirmation of results.
- Added independent-review calibration with frozen metadata, complete-pair agreement, reviewer identity consistency and unresolved queues.
- Expanded reference audit to distinguish documented DOI absence and optionally validate local archive page digests with path containment.
- Refused successful OpenAlex responses that echo authentication credentials, in addition to the existing provider safeguards; API credentials are not forwarded on HTTP redirects.
- Added installable CLI entry points, offline tests, CI compile/install checks, clearer documentation, and private-output ignore rules.
- No manuscript, actual reviewer packets, unpublished research evidence or restricted data is bundled.

## 0.7.1

- Resumable, checksummed abstract enrichment and independent review guidance.
- Retrieval and merge, pairwise claim auditing, identifier and reference checks.
