# Rule Studio

Rule Studio is where the business rules the AI follows are written and changed.
Everything on this page is data in the database, so changing how a file is read,
summarized, or opined on no longer needs a developer or a release.

Open it from **Rule Studio** in the portal sidebar.

## Configurations

A *configuration* is one complete, named rule set. It holds:

- a **global golden rule prompt** applied to every AI stage,
- an **opinion template** (disability, critical illness, accommodation, underwriting),
- any number of **document type rules**,
- optional **prompt overrides** that replace the built-in summary or opinion instructions outright.

The system ships with **Default (Golden Rules)** — the behavior the pipeline had
before Rule Studio existed, written out as editable data. It is a normal
configuration: edit it, and your edits stay. Reseeding never overwrites it.

Create as many configurations as you have kinds of work (for example one for
long-term disability reviews and one for critical illness claims), and mark the
one you use most as the default.

## Document type rules

Each rule attaches behavior to a document type.

| Field | What it does |
|---|---|
| **Document type** | The label this rule governs. Pick a built-in type (clinical, imaging, pathology, functional, administrative) or type your own, such as "Referral Form" or a SKU-style code. |
| **How the AI recognizes this document** | Plain-English description of the document. For a custom type this is what teaches the AI to spot it while reading pages. |
| **Action** | What happens to matching documents. See below. |
| **What to do with it** | Instructions the summarizer follows for this type, such as "Extract the diagnosis, restrictions, and return-to-work guidance only." |
| **Max words** | Word ceiling for the summary of this type. Leave blank to let the system size it from the document's length. |
| **Feed to the Opinion as context** | Matching documents are handed to the opinion stage as referral/assignment context. Use it for referral forms carrying the questions to answer. |

### Actions

- **Extract** — summarize the document following the rule's instructions.
- **Whole data** — hand the AI the document's full page text rather than only the
  extracted evidence items. Use it when detail is being lost, at higher cost.
- **Skip** — keep the document visible as a numbered card with its date and title,
  but write no prose for it. This is how lab and pathology reports behave by default.

### How a document is matched

A document is matched to a rule by its custom type first, then by its built-in
type. So a rule on "Referral Form" wins over a rule on "clinical" for a document
the AI tagged as a referral form. A document that matches no rule is summarized
with the standard behavior.

Built-in types keep working even if a custom rule misfires, so document grouping
and page boundaries are never at risk from a rule change.

## Using a configuration

On the Summarizer page, pick a configuration next to **Start**. The extraction
runs under those rules, and the review page shows which configuration and version
produced the output.

## Versioning

Saving a configuration creates a new version. Completed extractions keep the exact
rules they ran with — editing a configuration never rewrites past results.

Because the version is part of a job's identity, re-running the same PDF after
editing its configuration produces a fresh extraction instead of returning the
earlier cached result.

## Practical notes

- Deleting a configuration is allowed, except for the last remaining one. Past
  extractions that used it are unaffected.
- **Duplicate** is the safe way to try a variation: copy the configuration, change
  the copy, and run a file through it before making it the default.
- The golden rule prompt reaches every stage, so put standing house rules there
  (tone, naming, date format, what to exclude) and keep per-document instructions
  in the rules.
- Prompt overrides replace the built-in instructions completely. Prefer the golden
  rule prompt and rule instructions first; reach for an override only when the
  built-in behavior is wrong rather than incomplete.
