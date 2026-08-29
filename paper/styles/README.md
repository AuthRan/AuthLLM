# Vendored submission styles

`neurips_2026.sty` is the official NeurIPS 2026 style, from
<https://media.neurips.cc/Conferences/NeurIPS2026/Formatting_Instructions_For_NeurIPS_2026.zip>,
unmodified. It is vendored rather than fetched at build time because a
submission has to compile from the sources you upload, and because the page
limit is defined in this file: `textwidth=5.5in`, `textheight=9in`.

`paper/build_tex.py --neurips` copies it beside `main.tex` and emits a preamble
that loads it. The style carries the workshop options this project needs and
does the anonymisation itself:

* `dblblindworkshop` -- double-blind; `\maketitle` prints "Anonymous Author(s)"
  in place of the author block, whatever `\author` says.
* `sglblindworkshop` -- single-blind; the author block is printed.

`--neurips` selects between them from `--anonymous`, so the one open question
about the venue is a build flag rather than an edit.
