# Recorder-native ingestion fixtures

These fictional inputs are used by `tests/test_research_graph_ingest.py`. The
tests generate actual operation requests, budget admissions, stdout/stderr,
receipts, saved plans/ledger checkpoints, and reader-return file/event pairs
through the existing recorder. They do not present G0 `fixture_only` records as
native evidence. Tests mock only the automatic graph hook to model the crash
boundary between retaining an original and normalizing it, and inject a graph
ingest interruption to test chunk restart.

`source.html` contains one HTML link and one unlinked named record. The structured
return also deliberately declares a repeated link occurrence to test retention.
The ingestion layer preserves supplied observations; it does **not** mechanically
extract or verify every HTML/Markdown/provider-document link. The fixture's
reference list is a reader attestation, and its scan limitations stay visible.
Provider extraction and semantic correctness require separate adapter/reader
validation. G7 tests must distinguish extraction recall from normalization loss.

The fixture timestamp is replaced with the actual local test inspection time.
The retriever and reader have separate real saved-plan assignments. Only their
coordinator's normalization batch uses coordinator authority: row-level operation,
return, and inspection identities remain those of the actual original actors.
Automatic ingestion proposes assertions and creates pending obligations; it does
not accept judgments, schedule follow-ups, or close research.

Captured bytes may be viewed through a receipt-identical copy. Derived text,
additional binary files, or provider-specific representations need a future
receipt/derivation adapter; an arbitrary file cannot become native evidence.
Legacy ID-only records retain unknown identity metadata, and historical returns
retain `legacy_attested` inspections and explicit limitations. They never count
as native sufficient scope.

Run only the owned-fixture ingestion tests with:

```sh
python3 -I -B -m unittest discover -s tests -p 'test_research_graph_ingest.py' -v
```
