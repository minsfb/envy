# Search Coverage And Retrieval Routes

Map the question through **work type → artifact form and location → retrieval
route → query mode → concrete searches**. A review is an activity, a presentation
is a format, and a document store is a location. A store can contain many formats;
one artifact can be discoverable through several tools with different gaps.

## Select Useful Branches

Start from the reader's question and the organization's actual working practices.
Use these as prompts, not an exhaustive taxonomy or mandatory search quota:

| Work type | Plausible records |
|---|---|
| Reviews and decisions | Notes, decks, discussion threads, wiki records, design comments |
| Shareouts and demonstrations | Presentations, posts, demo logs, recordings and their attachments |
| Prototypes and experiments | Design boards, repositories, plans, feedback and study reports |
| Measurements and outcomes | Spreadsheets, dashboards, reports and subsequent reviews |
| Organizing and coordinating work | Project indexes, trackers, review schedules, recurring notes and team landing pages |

Choose likely locations for those records. Then consult available source skills
or an environment-owned catalog for verified discovery/read capabilities. Keep
provider commands, private observations and access configuration outside this
portable skill. Record documented versus observed capability, when it was checked,
and what the route searches: metadata, body, comments, attachments or visual content.
Reading a known file does not prove a global search capability. Unknown access is
unverified; a failure on one command does not establish that the whole source is
unavailable. Follow provider access policy for separately allowed alternatives.

For pivotal artifact classes, select complementary retrieval routes proactively
where available: for example, cross-source knowledge retrieval plus native file
discovery. Nonempty results do not establish coverage. Keep a broad search across
formats alongside useful type-specific queries. Different wrappers are distinct
interfaces, not proof of independent indexes; initially prefer a representative
route for equivalent capabilities, expanding when evidence or a missing capability
justifies it. Explain consequential omitted branches before synthesis.

Match the query mode to the input:

- Topics/questions suit conceptual or body search. Use terms and older aliases.
- Short project/activity terms can suit name search. Include generic review,
  demo, prototype and feedback language without requiring those words in titles.
- A discovered title without an ID suits exact/partial native name lookup.
- A known ID/URL calls for direct retrieval, followed by surrounding records.
- People and dates narrow selected searches; retain broader routes. Ownership
  is not contribution, and modification time is not the date of the work.

Instantiate useful combinations into the existing worklist. Start with bounded
representative queries; expand for central gaps, new vocabulary, capped results,
conflicts, or promising record chains. Do not multiply all dimensions, repeat every
query a fixed number of times, or prioritize only routes already yielding familiar
sources. When exclusions consume a result page, inspect pagination or revise the
query to seek eligible candidates; exclusions do not constitute primary coverage.
Check restrictive title/activity clauses against the intended work types. Where
supported, retain a topical body search without name restrictions. Use inspected
records to generate further vocabulary and neighborhood searches through
[discovery waves](discovery-waves.md); seed terms alone need not match the names
the organization used for its work.

## Record The Mapping

For a new bundle, set `requirements.search_routes: true` alongside `research_plan`.
Keep the base coverage/items/ledger from [the research plan](research-plan.md).
Add `search_routes` and `search_branches` to that plan. This fictional addition
maps one work type to one route; add other selected or bounded branches as needed:

```json
{
  "search_routes": [{
    "id": "archive-native",
    "provider": "local",
    "family": "native-file-discovery",
    "artifact_types": ["document"],
    "locations": ["supplied-archive"],
    "availability": "available",
    "checked_at": "2026-01-01",
    "basis": "Help and owned fixture confirmed filename search",
    "limitations": "Filenames only; bodies and comments need other routes",
    "invocation": {"command_prefix": ["archive-tool", "files", "list"]},
    "modes": [{"id": "name-contains", "input_kinds": ["term", "title"], "filter_fields": ["type"]}]
  }],
  "search_branches": [{
    "id": "review-documents-native",
    "coverage": ["historical-records"],
    "work_type": "review",
    "artifact_type": "document",
    "location": "supplied-archive",
    "route": "archive-native",
    "mode": "name-contains",
    "status": "selected",
    "reason": "Seek review files that a separate knowledge route may omit"
  }]
}
```

Every search item additionally names `search_branch` and `input_kind`. The existing
`request` retains the exact query, filters, cap and cursor. Copy those two fields
into its operation specification alongside the plan snapshot and item ID. A tool
route uses `invocation: {"tool": "verified_tool_name"}` and the same `tool` in its
operation; a subprocess route uses an argv `command_prefix`. Resolve executable
paths before recording them. These are bindings, not shell templates or commands
to execute merely because they appear in a catalog.

Route availability is `available`, `unverified` or `unavailable`; an unknown
invocation can be `null` only for the latter two. Modes declare supported input
kinds (`topic`, `term`, `title`, `person`) and filter field names. A mode accepting
a term does not imply that it accepts a natural-language question. Verify actual
filter semantics with the provider; the helper cannot interpret arbitrary CLI
arguments or confirm that a declared input label is honest.

Each branch selects one work type, artifact, location, route and mode for its
coverage entries. Its status is `selected`, `bounded` or `omitted`, with a reason.
Selected branches need available routes and concrete search items. At synthesis,
each needs an executed, indexed search and no pending assigned searches, regardless
of priority. If effort/access ends the branch, close its pending work and explicitly
bound it. This makes the limit visible; it does not prove the limit is justified.

Each declared work type in coverage needs a mapped branch or explicit omission. A read
cannot satisfy a search branch. Returned assets enter the common inventory with
all discovery paths; reading and analysis remain separate. The generated plan view
includes route capabilities, selection reasons, executed work, result scope and
bounded branches. Include a readable coverage account in the companion, with
detailed receipts linked rather than crowding the brief.

Save changes through `plan`. Retain route/branch IDs and definitions: changed
capabilities or mappings need new IDs; availability/basis/check time/limitations
and branch status/reason can evolve. Existing work bindings and old snapshots
remain intact. Existing bundles without this requirement still validate; do not
retrofit missing pre-execution assignments into their receipts.

## Evaluate Coverage

Judge both plan execution and research outcomes. Structural checks catch skipped
selected routes, unsupported declared inputs/filters and lost provenance. They
cannot prove that the chosen locations or keywords were adequate. Known-title
probes test route discoverability; they do not measure discovery from a topic.
For a behavioral retest, withhold expected titles/IDs and prior synthesis, supply
realistic topics and people, and inspect actual discoveries, reads and follow-ups.
Report assisted discovery separately. Compare useful unique evidence, unresolved
questions and route gaps, not just query counts or multiple sightings of a source.

When an evaluation excludes earlier outputs, filter them before results enter the
researcher's tools or working directory. Preserve originals in evaluator-owned
evidence storage; provide source IDs and exclusion labels without snippets or
citations. A request not to open accessible raw output is not an isolation boundary.
Record accidental exposure and separate that run from a fresh unexposed evaluation.
