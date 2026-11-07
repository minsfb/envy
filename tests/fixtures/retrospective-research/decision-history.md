# Harbor Records Export

Fictional fixture. These are all records available to this investigation. IDs are
stable citation locators within this file. Bodies explicitly marked unavailable
cannot be inspected through another tool. There is no live source service.

## D1 — Quay event-stream proposal

Date: 2021-10-05. Named author: Morgan Reed. Full body excerpt below; comments and
revision history were not exported. Quay was renamed Harbor in January 2022.

Replace periodic batches with an event stream. The release-1 proposal targets a
20% throughput improvement, pending a controlled comparison. Reconnect behavior,
duplicate delivery, and storage growth require evaluation before admission.

## D2 — Harbor release-1 admission notes

Date: 2022-02-14. Author: Morgan Reed. Attendees: service lead and release manager.
Complete notes body below; attachments unavailable.

The release manager deferred event streaming from release 1 because reconnects
can replay deliveries without a stable deduplication key. Batch sync remains the
release-1 default. This is not a conclusion about streaming's throughput ceiling.
After launch, revisit a bounded trial if stable event identity and reconnect tests
are ready. The service lead accepted that trial criterion.

## D3 — Harbor retrospective overview

Date: 2023-06-19. Author: Casey Brook, who joined in 2023. Body excerpt below.

The original team abandoned streaming because it was slow. I have not located
the original test report; this description came from an onboarding conversation.
The overview links to the release-1 admission notes as background.

## D4 — Current implementation inspection

Date: 2024-03-11. Inspected revision: fixture-r4. Complete inspection note below.

The experimental stream path stores stable event IDs. Tests at fixture-r4 pass
three scripted reconnect scenarios with no duplicate delivery. No production
deployment, scale test, prolonged outage test, or controlled batch-versus-stream
performance measurement is included in this inspection.

## D5 — Quay reconnect exploration board

Created: 2021-11-08. Owner: Morgan Reed. Metadata-only result.
Title and owner are visible. Board body, frames, comments, and prototype behavior
are unavailable. A search preview says "replay alternatives / reconnect states".

## D6 — Early steady-state throughput experiment

Date: 2022-01-20. Author: Jordan Lee. Complete result excerpt below.

Streaming handled 100 items/second; the batch baseline handled 90 items/second.
The streaming run used a smaller dataset and a different cache warmup. Neither
run tested reconnects. These exploratory observations are not a controlled
comparison and do not establish the proposed 20% improvement target.
