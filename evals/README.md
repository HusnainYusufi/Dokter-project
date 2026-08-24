# Evals

A scored comparison of real extraction output against an answer key built by
reading the source pages by eye.

Every prompt change before this was verified structurally - the prompt
assembles, the tests pass, the types compile - and never against ground truth.
That is how a change that removed every word ceiling shipped, and how an EMR
results-index row became an imaging report asserting a normal impression for a
study that was abnormal. This makes a change measurable.

## Running

Download a completed job and score it:

```bash
curl -s "$API/api/v1/extract/jobs/$JOB_ID" > job.json
python -m evals.score evals/cases/lilian_30_pages.json job.json
```

Exit status is 0 when nothing critical or error-level is found, 1 otherwise, so
it drops into CI unchanged.

Output is a list of findings and a headline score:

```
61/83 checks clean (73%) - 4 critical, 16 error, 2 warning
```

## Severity

- **critical** - the output asserts something false, or loses a page. A false
  normal on an abnormal study lives here.
- **error** - a wrong date, a wrong author, a document split or merged, an
  entry well over its ceiling.
- **warning** - a wrong registered type, or an entry slightly over its ceiling.

## Cases

- `cases/lilian_30_pages.json` - 30 pages, 20 documents, 6 traps. Built by
  rendering the PDF and reading every page. The traps are the specific
  mistakes this file provokes: a results-index table that looks like results, a
  form whose signature page looks administrative, routing metadata that looks
  like authorship, two instruments that look like one, and a clinical
  photograph that looks like a radiograph.

## Adding a case

1. Render the PDF: `pypdfium2` at scale 200/72 is enough to read.
2. Read every page. Record, per document: page range, bucket, date, author,
   title.
3. Write the traps - the things a page-at-a-time reader cannot get right from
   that page alone. These are the point of the case; the document list is
   scaffolding.
4. Add a test in `tests/test_evals.py` that replays the real failure and
   asserts the scorer catches it. A scorer that silently passes bad output is
   worse than no scorer.

The key holds no patient data beyond what is needed to score: names of
clinicians as printed, dates, and page numbers. Keep the source PDF out of the
repository.
