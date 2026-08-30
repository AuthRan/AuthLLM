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

1. **Compiled.** `main.tex` builds clean: 29 pages, no overfull or underfull
   boxes, no LaTeX warnings, no undefined references. Built with Tectonic
   0.17.0, which is a XeTeX engine; arXiv runs pdfLaTeX, so the one difference
   to expect is that `inputenc` is a no-op under XeTeX and active under
   pdfLaTeX. It is kept in the preamble because pdfLaTeX is what arXiv uses.

   This machine has no TeX installation and no sudo, but it does have network,
   and Tectonic ships a static binary that needs neither:

   ```
   curl -sSL -o tectonic.tar.gz https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz
   tar -xzf tectonic.tar.gz
   ./tectonic -X compile main.tex
   ```

   It fetches the packages it needs on first run and caches them in
   `~/.cache/Tectonic`. `tests/unit/test_build_tex.py` still runs the cheap
   structural checks -- brace and environment balance, table column counts,
   unescaped specials, surviving markdown, and LaTeX's section numbering
   against the numbers the prose cites -- so a build that cannot reach the
   network still catches most of what a compiler would.

2. **The author block is filled in.** `paper/build_tex.py` carries `AUTHOR`,
   `AFFILIATION` and `CONTACT` near the top; they are the only content in the
   build script that is not derived from the markdown, because the markdown
   does not carry them. They read "Ashutosh Ranjan / Independent Researcher /
   authran.off@gmail.com", and the address is set as a `mailto:` link through
   `hyperref`. Edit there, not in `main.tex`.

   **"Independent Researcher" is deliberate and should not be "corrected".** The
   author is enrolled at NIT Sikkim, and the institution was briefly put on the
   title page on 2026-08-30 before being reverted the same day. The work was done
   on personal hardware with no advisor, no institutional compute and no funding,
   so independent status is the accurate description of the work rather than a
   gap to be filled. Publishers list it as a valid affiliation for exactly this.

3. **The wide tables set legibly.** Every table is wrapped in a conditional
   `\resizebox` that shrinks it only if it overruns the text width. Checked in
   the compiled PDF: Appendix F's nine-column table is the widest and is
   comfortably readable at the size it lands on. No landscape page needed.

4. **The abstract fits the form.** `abstract.txt` is the paper's abstract as
   plain text, cut to 1,890 characters against arXiv's cap of about 1,920.
   `build_tex.py` checks the length on every build and says so if an edit
   pushes it back over.

5. **Endorsement.** arXiv requires an endorsement for a first submission to
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
