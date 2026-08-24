# Deep Research Evaluation Dataset v1

This directory is a materialized, snapshot-grounded evaluation dataset. Offline
evaluation must use `source_snapshots.jsonl` rather than fetching live URLs.

## Contents

- `cases.jsonl`: evaluation prompts, expected facets, and each case's allowed source IDs.
- `source_snapshots.jsonl`: deduplicated evidence excerpts with canonical URLs, retrieval
  timestamps, and SHA-256 content hashes.
- `claim_annotations.jsonl`: manually reviewed claim labels. A `supported` claim lists
  sources that directly support it; `insufficient` means the reviewed evidence does not
  directly support the claim and must not be presented as a verified fact.
- `materialization_failures.jsonl`: candidates that were visited but could not yield a
  usable evidence snapshot. They are intentionally excluded from offline evidence.

## Dataset State

- Dataset ID: `deep-research-v1`
- Status: `ready_for_offline_regression_with_capture_gaps`
- Cases: 12
- Source snapshots: 21
- Claim annotations: 24
- Capture gaps: 2

The draft and batch capture records are retained in `evaluation_data/v1-draft/`.
Re-materialize a new dataset version when any source, evidence excerpt, or annotation
changes; do not overwrite this version for live-web changes.
