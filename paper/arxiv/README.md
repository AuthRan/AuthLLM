# arXiv submission package

Generated from `paper/paper.md` by `python paper/build_tex.py`. Do not edit
`main.tex` by hand: the markdown is the source of truth, the numbers in it are
regenerated from the ledgers by `scripts/update_paper_counts.py`, and an edit
here is lost the next time the paper is built.

```
paper/arxiv/
  main.tex          the paper
  figures/          the three figures, copied from resources/plots/
```

## Before submitting

1. **Compile it once.** The machine this was generated on has no TeX
   installation, so `main.tex` has never been run through LaTeX. It is written
   against a base texlive -- no package outside `graphicx`, `booktabs`,
   `amsmath`, `amssymb`, `caption`, `microtype`, `hyperref`, `geometry` -- and
   `tests/unit/test_build_tex.py` checks the failures a compiler would catch
   (brace and environment balance, table column counts, unescaped specials,
   markdown that survived conversion, and that LaTeX's section numbering
   reproduces the numbers the prose cites). That is not the same as compiling.

   ```
   cd paper/arxiv && pdflatex main.tex && pdflatex main.tex
   ```

   Twice, for the table of contents and any reference LaTeX resolves on a
   second pass.

2. **Fill in the author block.** `paper/build_tex.py` carries `AUTHOR` and
   `AFFILIATION` near the top; they are the only content in the build script
   that is not derived from the markdown, because the markdown does not carry
   them. There is a `% TODO` beside them in the generated preamble.

3. **Check the two figures that are wide.** Appendix F's table is scaled to the
   text width with `\resizebox`; at nine columns it may set very small. If it
   does, the alternative is a landscape page (`lscape`) or splitting the table.

4. **Endorsement.** arXiv requires an endorsement for a first submission to
   `cs.LG` from an author with no submission history. That is an account-level
   step and cannot be done from here. Suggested categories: `cs.LG` primary,
   `cs.CL` cross-list.

## Uploading

arXiv takes the sources, not the PDF:

```
tar czf arxiv.tar.gz -C paper/arxiv main.tex figures
```

Upload that. If arXiv's own build disagrees with the local one, its log names
the line, and the fix belongs in `paper/build_tex.py` rather than in `main.tex`.
