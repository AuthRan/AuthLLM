# The workshop cut, costed

*Written 2026-08-30 against the compiled PDF, not against the markdown. Every
page number here was measured; none was estimated. Companion to the length
section of `NIGHT-PLAN.md`.*

## The gap

Sections 1–7 compile to **21 pages**. References are page 22, appendices 23–29,
and Pre-to-Post excludes both from both tracks. So:

| track | limit | must remove |
| --- | ---: | ---: |
| long (NeurIPS main-track limit, confirmed) | 9 | **12 pages** |
| short | 4–5 | 16–17 pages |

**Only about 3 of those 12 pages can be moved.** Relocation to the appendix is
free but finite: §4.5 (0.91), §4.7.1 (0.91) and §4.3 (0.82) are the three
sections that stand alone well enough to go whole, and that is 2.6 pages. The
other **9 pages have to be written away**, which is why this is a day of prose
and not an afternoon of moving blocks.

## Proposed budget for the long track

Targets, not measurements. They sum to 9.0 and want one compile-and-trim pass.

| section | now | target | how |
| --- | ---: | ---: | --- |
| §1 Introduction | 1.70 | 0.90 | keep the finding and the cost of getting it wrong; the survey of what the paper will do goes |
| §2 Related work | 1.14 | 0.50 | one dense paragraph; the workshop audience knows the packing literature |
| §3 Method (3.1–3.4) | 2.41 | 1.20 | one setup paragraph, the confound stated once, the grid as a table; §3.4 folds into §4.4 where it is used |
| §4.1 Full factorial | 0.39 | 0.40 | keep whole — it is the result |
| §4.2 Second corpus | 1.43 | 0.50 | keep Dolly's numbers and the failed prediction; drop the walk-through |
| §4.3 Inherit vs retune | 0.82 | 0.00 | **to appendix**, cited in §6 where the cost is claimed |
| §4.4 Batch or packing | 1.64 | 0.90 | the strongest control in the paper; keep the design, compress the discussion |
| §4.5 Not a function of | 0.91 | 0.00 | **to appendix** — it is two rule-outs and one retraction, all citable |
| §4.6 Scale | ~2.4 | 1.20 | the three-point series and the two registered predictions; the decomposition goes to the appendix |
| §4.7 Model sizes | ~1.4 | 0.90 | keep; it is half of why this fits the workshop |
| §4.7.1 Pretraining budget | 0.91 | 0.00 | **to appendix**, one sentence in §4.7 carries the null |
| §4.7.2 Size or quality | 0.94 | 0.50 | keep the control and its registered prediction; the series table goes |
| §5 Threats | 1.88 | 0.80 | keep the retractions, which are the distinctive content; cut the enumeration |
| §6 What to do | 1.77 | 0.80 | keep the range and the sweep recipe; drop the worked example |
| §7 Conclusion | 0.56 | 0.40 | |
| | **21** | **9.0** | |

## What must not be cut

The retractions. Four findings in this paper were withdrawn after replication
(§4.5's packing-ratio trend, §4.6's linearity, §6's bracket, and the ±0.006
bound), and the registered predictions that were written before the deciding
runs. That is the paper's most distinctive content and the reason a
negative-results venue was on the list at all. A cut that keeps the results and
drops the retractions produces a more ordinary paper.

This is the argument against the short track. Four to five pages cannot hold
thirteen settings, three model sizes, six registered predictions and four
retractions; it would have to become an extended abstract about the headline
number, and the headline number is the least interesting thing here.

## Mechanical, and already done

- Relocation needs no new machinery beyond renumbering: the markdown writes its
  own section numbers into the headings and the prose cites them as plain text,
  so a moved section means rewriting every "section 4.5" that points at it.
  `tests/unit/test_build_tex.py` simulates LaTeX's counters and will catch a
  heading whose number no longer matches what the prose claims.
- Blinding is `python paper/build_tex.py --anonymous`. `paper.md` carries no
  repository link, no link to the published version, and no name; all of that
  lives in `build_tex.py` alone. Whether the workshop needs it is still open.
- The arXiv version keeps all 29 pages. Nothing here touches it.
