# The workshop cut, as done

*Rewritten 2026-08-30 after the cut landed. The costed plan this file used to
hold was measured in the wrong LaTeX style and is superseded by what actually
happened.*

## Where it ended

`paper/workshop.md` fits **nine content pages** in the NeurIPS 2026 style: the
conclusion ends at the bottom of page 9 and page 10 opens with the references.
Build it with

```
python paper/build_tex.py --source paper/workshop.md --neurips --anonymous \
    --outdir <dir>
```

`--anonymous` selects the style's `dblblindworkshop` option, which prints
"Anonymous Author(s)" in place of the title block; drop it for
`sglblindworkshop`, which prints the real one. Whether the venue is blind is
still unconfirmed (§ below).

## The measurement mistake worth remembering

The first estimate said sections 1–7 were 21 pages against a 9-page limit, a
twelve-page gap. That was measured in the paper's own arXiv preamble --
`article` at 11pt with 1in margins -- and the limit is not defined there. In the
NeurIPS style the same text is **9.6 pages, not 11.9**: `textwidth` is 5.5in
rather than 6.5in, but the style sets Times at 10pt against Computer Modern at
11pt, and the typeface change dominates. An arithmetic estimate of the
difference put it at 2.6% and it is nearer 16%.

**Measure the submission in the style it will be judged in, from the first
count.** `--neurips` exists so this cannot happen again.

## What actually reduced it

Twelve passes of compression took 21 pages to about 10 and then stopped dead:
two consecutive passes rewrote whole sections and saved 0.08 and 0.00 pages.
Dense prose, rewritten by the same hand, comes out the same length.

The last page and a half came from three things:

1. **Deleting claims made twice**, without replacement — the significance-test
   caveat in 3.1 repeated in 4.2, the grid-point caveat in 4.3 that section 5
   also carries, the rows-rule result stated twice in 4.4, the batch/step
   decomposition already moved to Appendix L, and three figure captions that
   restated the paragraphs beside them.
2. **The abstract**, which was 1,890 characters because that is arXiv's form cap.
   The workshop has no such form; it is now 1,291.
3. **Figure aspect ratio.** The bracket figure was drawn 9.4x5.6, which at
   `\linewidth` in a 5.5in column is 3.3in tall — two-thirds of a page for
   thirteen rows. At 10.4x4.9 it costs 2.6in and reads the same.

## What moved where

Nothing was dropped. Appendices are excluded from the limit, so sections 1–7
keep a summary in place and send the rest to six new appendices — which also
preserved the section numbering the prose cites, rather than renumbering
everything after a hole.

| appendix | holds |
| --- | --- |
| H | section 4.3 in full, the inherit-versus-retune comparison |
| I | section 4.5 in full, the corpus and packing-ratio rule-outs |
| J | section 4.7.1 in full, the pretraining-budget control |
| K | the wide-batch control's match table, residual diagnosis, and figure |
| L | the exponent decomposed into a batch term and a step term |
| M | numerical stability, the warmup and data-seen confounds, sweep sharpness |

All three main-body figures stayed: the regime figure in 4.6, the base-model
quality figure in 4.7.2, and the bracket figure in 6.

## Still open

- **Anonymity.** NeurIPS 2026's main track is double-blind; the Pre-to-Post call
  does not say whether the workshop inherits that, and OpenReview renders
  through JavaScript so the group page could not be read from here. It is a
  build flag either way, and `paper.md` carries no repository link, no link to
  the published version and no name — all of that lives in `build_tex.py`.
- **arXiv endorsement**, which is an account-level step for a first `cs.LG`
  submission and cannot be done from here.
- The arXiv version keeps its full length. Nothing in this file touches it.
