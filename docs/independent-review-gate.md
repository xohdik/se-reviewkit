# Independent review and source-adjudication gate

The toolkit's retrieval, deduplication, enrichment and numerical claim-audit outputs **do not constitute independent human systematic-review screening**. No final included-study counts, agreement statistics or claim-validity decisions may be inferred from automation alone.

## Two independent screening passes

1. Freeze candidate identifiers and bibliographic metadata in a checksum manifest.
2. Provide two reviewers the same independently shuffled, AI-blinded title-and-abstract records. Keep reviewer files separate until both are locked.
3. Each reviewer records `include`, `exclude` or `uncertain` with one protocol-compatible reason, brief rationale and timestamp. `include` means advance to full text, not final inclusion.
4. Compute the three-by-three confusion matrix and observed agreement only from complete matched pairs; report incomplete pairs separately. Calculate Cohen's kappa only if defined. Never fill missing decisions or turn ambiguity into exclusion.
5. Resolve discordance by documented human discussion or a third adjudicator. Confirm preprint-to-publication families and record a sourced reason for every excluded full text. Only then report the final selected-study population and a selection diagram.

## Source-level claim checks

Before publishing claims of cross-study correctness, two independent readers should verify a separate calibration set against the exact cited source versions. Capture the observed value and precision, effective denominator (or justified unknown), configuration, population, locator and artifact availability. Disagreeing observations go into a separate human adjudication queue. Independent arithmetic reproduction of source-reported values is distinct from validating a verifier's correctness and from fresh task-level execution.

## Integrity safeguards

Retain raw API/publisher metadata according to their licenses and preserve review responses privately. Publish only software, documentation, appropriately licensed aggregate results and sources after author approval. Changes to screening or extraction rules after calibration must be versioned. Do not report a numerical kappa or count as "included" before the corresponding decisions exist.

**Reusable code:** `screening_agreement.py` supports complete-pair agreement. `review_calibration.py` freezes source metadata, validates two separate reviewer packets, calculates agreement for complete pairs, and writes unresolved queues. See [the evidence workflow](evidence-workflow.md) for input columns and commands. Reviewer IDs and times are supplied attestations; software cannot establish reviewers' actual independence.
