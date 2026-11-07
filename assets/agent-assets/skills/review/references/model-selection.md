# Resolve models and launch evidence

The portable default is one strongest actually available GPT lead and one
strongest actually available Claude lead. The offline controller remains generic:
an explicit task may choose other slots, families, models or evidence policies.
Do not encode today's model names as evergreen defaults.
These family names are an overridable portable skill default over the generic
`model_selection.family` field, not a vendor binding of the controller. A task
or explicit bundle can replace them; organization-specific access stays bundled.
An adapter supplied by a bundle expresses those defaults through explicit
`reviewers` and `model_selection` preparation inputs in the
[versioned schema](review-bundle.md); this does not require or permit installing
a second skill at the same managed asset path.
Family labels use `[A-Za-z0-9][A-Za-z0-9._-]*` (at most 128 characters), not
free-form display titles or slash-containing labels.

## Establish availability, then bind exact selections

Use current reliable capability information from the authorized harness or its
managed adapter. A model must be supported and accessibly selectable on that
route, not merely announced. Use documented capability/ranking information and
the task's needs; version-number guessing or a model-authored title is not proof
that a selection exists or is strongest. Do not invent discovery commands, make
an unapproved provider probe, install a client or repair access to obtain it.

Record the exact selected ID, family, selection basis and applicable harness for
each lead. Researchers default to that lead's exact model, including when new
research is added. Confirm the delegation interface supports this selection or
documents exact inheritance; do not assume an unspecified child gets that model.
Declare intentional exceptions in the slot's `researcher_model_overrides` before
binding the round; an empty list keeps the same-model default.
Missing a required family/model/capability means
the requested configuration is unavailable, not permission for silent fallback.

For a one-off interactive request, propose the resolved configuration and limits
for confirmation. In an explicitly autonomous workflow, resolve and use these
defaults within existing authority without reapproval at each stage. When current
information cannot establish the required choice, report the specific gap; do
not fabricate availability. Explicit user selections take precedence. Never
change the exact models of an already bound round silently.

## Keep identity claims precise

Record actual run/session identities and the source/meaning of model evidence.
Requested selection, verified launch selection and independently observed served
model are different facts. A command flag alone states intent; an authorized
adapter may attest an actual exact selection only to the extent the interface's
verified semantics support it. Missing served-model metadata remains unknown.
Contradictory identity evidence blocks acceptance, not a reason to discard it.

Choose the accepted evidence basis before launch. A policy accepting verified
launch selection need not pretend that served identity is known; a stricter
observed-identity policy remains available. Every child inherits its lead slot's
`identity_basis` and `identity_requirement`, even with another adapter or a
prebound model override. `researcher_model_overrides` changes allowed models,
not identity policy; confirm compatible child evidence before launch.
Use the supported versioned bundle fields in [review-bundle.md](review-bundle.md),
not guessed fields or fabricated
runtime metadata. Managed adapters provide access/availability/selection evidence;
they do not duplicate the portable checklist or team adjudication protocol.
