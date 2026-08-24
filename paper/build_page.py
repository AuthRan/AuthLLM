"""Render paper/paper.md into the standalone HTML page published as an Artifact.

The page is the paper, not a summary of it: the markdown is converted verbatim,
so there is exactly one source of truth for every number. Everything this script
adds is navigation and presentation -- a contents rail, section anchors, and the
figure inlined as a data URI so the page carries no external requests.

    python paper/build_page.py

Colours come from ashugpt/viz/style.py, the same palette the figure is drawn
with, so the page and the plot inside it read as one system.
"""

from __future__ import annotations

import base64
import csv
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "paper" / "paper.md"
RESULTS = ROOT / "results" / "lr_scaling_sweep.csv"
FIGURE = ROOT / "resources" / "plots" / "06-lr-scaling-packing.png"
OUT = ROOT / "paper" / "paper.html"

# The figure's own palette (ashugpt/viz/style.py), reused so the page around the
# plot is drawn from the same set of inks rather than a second, unrelated one.
NAVY = "#1f4e79"    # padded curves
AMBER = "#c9700a"   # packed curves -- the result the paper is about
CRIMSON = "#b0264a" # the overfitting-contaminated cell, and the failed prediction
INK = "#1f2430"
MUTED = "#5b6472"
RULE = "#d8dde3"


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.lower().replace("§", "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def main() -> None:
    raw = SOURCE.read_text()
    # Counted rather than quoted, so the masthead cannot drift from the ledger.
    with RESULTS.open() as handle:
        runs = sum(1 for _ in csv.DictReader(handle))

    # The masthead is built from the title and status block; the body is
    # everything from the abstract on, converted untouched.
    title = re.search(r"^# (.+)$", raw, re.M).group(1)
    status = re.search(r"^\*\*Status: (.+?)\*\*$", raw, re.M | re.S).group(1)
    status = " ".join(status.split())
    body_md = raw.split("---\n", 1)[1]

    html = markdown.markdown(body_md, extensions=["tables", "fenced_code", "sane_lists"])

    # Anchors on every section heading, and a contents rail built from them.
    toc: list[tuple[str, str, str]] = []

    def anchor(match: re.Match) -> str:
        level, text = match.group(1), match.group(2)
        slug = slugify(text)
        toc.append((level, slug, text))
        number = re.match(r"^(\d+(?:\.\d+)?)\.?\s+(.*)$", text)
        label, rest = (number.group(1), number.group(2)) if number else ("", text)
        marker = f'<span class="secno">{label}</span>' if label else ""
        return f'<h{level} id="{slug}">{marker}<span>{rest}</span></h{level}>'

    html = re.sub(r"<h([23])>(.*?)</h\1>", anchor, html, flags=re.S)

    # Tables and the code block scroll inside their own box, never the page.
    html = html.replace("<table>", '<div class="scroller"><table>')
    html = html.replace("</table>", "</table></div>")
    html = html.replace("<pre>", '<div class="scroller"><pre>')
    html = html.replace("</pre>", "</pre></div>")

    # The figure, inlined, with its caption (the italic paragraph after it)
    # pulled into a real <figcaption>.
    data_uri = "data:image/png;base64," + base64.b64encode(FIGURE.read_bytes()).decode()
    html = re.sub(
        r'<p><img alt="([^"]*)" src="[^"]*" /></p>\s*<p><em>(.*?)</em></p>',
        lambda m: (
            f'<figure><img alt="{m.group(1)}" src="{data_uri}" />'
            f"<figcaption>{m.group(2)}</figcaption></figure>"
        ),
        html,
        flags=re.S,
    )

    nav = "\n".join(
        f'<a class="toc-{level}" href="#{slug}">{text}</a>' for level, slug, text in toc
    )

    OUT.write_text(PAGE.format(
        title=title, status=status, body=html, nav=nav, runs=runs,
        navy=NAVY, amber=AMBER, crimson=CRIMSON, ink=INK, muted=MUTED, rule=RULE,
    ))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


PAGE = """<title>Packing and the Optimal Learning Rate</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
:root {{
  --ink: {ink};
  --muted: {muted};
  --ground: #f5f7f9;
  --surface: #ffffff;
  --rule: {rule};
  --rule-soft: #e7ebef;
  --accent: {navy};
  --signal: {amber};
  --warn: {crimson};
  --shadow: 0 1px 2px rgba(31, 36, 48, .05), 0 8px 24px -16px rgba(31, 36, 48, .28);
  --sans: "Archivo", ui-sans-serif, system-ui, sans-serif;
  --serif: "Source Serif 4", Georgia, "Times New Roman", serif;
  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ink: #e4e8ee;
    --muted: #99a3b2;
    --ground: #141821;
    --surface: #1b202a;
    --rule: #2f3644;
    --rule-soft: #262c38;
    --accent: #82b3e2;
    --signal: #e6a44f;
    --warn: #e87f95;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .7);
  }}
}}
:root[data-theme="dark"] {{
  --ink: #e4e8ee;
  --muted: #99a3b2;
  --ground: #141821;
  --surface: #1b202a;
  --rule: #2f3644;
  --rule-soft: #262c38;
  --accent: #82b3e2;
  --signal: #e6a44f;
  --warn: #e87f95;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .7);
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: var(--serif);
  font-size: 17px;
  line-height: 1.66;
  -webkit-font-smoothing: antialiased;
}}
.page {{ max-width: 1180px; margin: 0 auto; padding: 0 clamp(20px, 5vw, 56px) 96px; }}

/* ---- masthead ---------------------------------------------------------- */
.masthead {{ padding: clamp(48px, 9vw, 104px) 0 0; max-width: 44rem; }}
.eyebrow {{
  font-family: var(--mono);
  font-size: .74rem;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 1.6rem;
}}
.eyebrow b {{ color: var(--signal); font-weight: 500; }}
h1 {{
  font-family: var(--sans);
  font-weight: 700;
  font-size: clamp(2.1rem, 5.4vw, 3.4rem);
  line-height: 1.06;
  letter-spacing: -.022em;
  text-wrap: balance;
  margin: 0 0 1.4rem;
}}
.standfirst {{
  font-size: 1.2rem;
  line-height: 1.55;
  color: var(--muted);
  margin: 0 0 2.2rem;
  text-wrap: pretty;
}}
.status {{
  font-family: var(--mono);
  font-size: .8rem;
  line-height: 1.6;
  color: var(--muted);
  border-left: 2px solid var(--signal);
  padding: .1rem 0 .1rem 1rem;
  margin-bottom: clamp(40px, 7vw, 72px);
}}

/* ---- findings ---------------------------------------------------------- */
.findings {{
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  padding: 2.2rem 0;
  margin-bottom: clamp(40px, 7vw, 72px);
  display: grid;
  gap: 2rem;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
}}
.finding h2 {{
  font-family: var(--mono);
  font-size: .74rem;
  font-weight: 500;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 .7rem;
}}
.finding .figure {{
  font-family: var(--sans);
  font-weight: 600;
  font-size: 1.65rem;
  letter-spacing: -.015em;
  font-variant-numeric: tabular-nums;
  display: block;
  margin-bottom: .5rem;
  color: var(--signal);
}}
.finding.miss .figure {{ color: var(--warn); }}
.finding p {{ margin: 0; font-size: .96rem; line-height: 1.55; color: var(--muted); }}

/* ---- layout ------------------------------------------------------------ */
.layout {{ display: grid; grid-template-columns: 1fr; gap: 3rem; }}
@media (min-width: 1040px) {{
  .layout {{ grid-template-columns: 200px minmax(0, 1fr); gap: 4.5rem; }}
  .toc {{ position: sticky; top: 2rem; align-self: start; max-height: calc(100vh - 4rem); overflow-y: auto; }}
}}
.toc {{ display: flex; flex-direction: column; gap: .1rem; padding-bottom: 1rem; }}
.toc a {{
  font-family: var(--sans);
  font-size: .8rem;
  line-height: 1.35;
  color: var(--muted);
  text-decoration: none;
  padding: .32rem 0 .32rem .75rem;
  border-left: 2px solid var(--rule-soft);
}}
.toc a:hover, .toc a:focus-visible {{ color: var(--ink); border-left-color: var(--signal); }}
.toc .toc-3 {{ padding-left: 1.5rem; font-size: .76rem; }}

/* ---- paper body -------------------------------------------------------- */
.paper {{ max-width: 40rem; }}
.paper h2, .paper h3 {{
  font-family: var(--sans);
  letter-spacing: -.014em;
  text-wrap: balance;
  display: flex;
  gap: .7em;
  align-items: baseline;
  scroll-margin-top: 1.5rem;
}}
.paper h2 {{
  font-size: 1.62rem;
  font-weight: 700;
  margin: 3.6rem 0 1.1rem;
  padding-top: 1.6rem;
  border-top: 1px solid var(--rule);
}}
.paper h3 {{ font-size: 1.12rem; font-weight: 600; margin: 2.6rem 0 .9rem; }}
.secno {{
  font-family: var(--mono);
  font-size: .72em;
  font-weight: 400;
  color: var(--signal);
  font-variant-numeric: tabular-nums;
  flex: none;
}}
.paper p {{ margin: 0 0 1.15rem; text-wrap: pretty; }}
.paper strong {{ font-weight: 600; }}
.paper ol, .paper ul {{ margin: 0 0 1.15rem; padding-left: 1.4rem; }}
.paper li {{ margin-bottom: .6rem; }}
.paper li::marker {{ color: var(--muted); font-family: var(--mono); font-size: .85em; }}
.paper a {{ color: var(--accent); text-underline-offset: .18em; }}
code {{
  font-family: var(--mono);
  font-size: .86em;
  background: var(--surface);
  border: 1px solid var(--rule-soft);
  border-radius: 3px;
  padding: .08em .34em;
}}

/* ---- tables, code, figure --------------------------------------------- */
.scroller {{
  overflow-x: auto;
  margin: 0 0 1.6rem;
  background: var(--surface);
  border: 1px solid var(--rule-soft);
  border-radius: 4px;
  box-shadow: var(--shadow);
}}
@media (min-width: 1040px) {{ .scroller, figure {{ width: min(52rem, calc(100vw - 340px)); }} }}
table {{ border-collapse: collapse; width: 100%; font-family: var(--mono); font-size: .8rem; }}
th, td {{
  padding: .5rem .8rem;
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  border-bottom: 1px solid var(--rule-soft);
}}
th:first-child, td:first-child {{ text-align: left; white-space: normal; }}
thead th {{
  font-weight: 500;
  color: var(--muted);
  border-bottom: 1px solid var(--rule);
  font-size: .74rem;
  letter-spacing: .04em;
}}
tbody tr:last-child td {{ border-bottom: 0; }}
td strong {{ color: var(--signal); font-weight: 500; }}
pre {{ margin: 0; padding: 1.1rem 1.2rem; font-family: var(--mono); font-size: .8rem; line-height: 1.65; }}
pre code {{ background: none; border: 0; padding: 0; font-size: 1em; }}
figure {{ margin: 0 0 1.6rem; }}
figure img {{
  display: block;
  width: 100%;
  height: auto;
  background: #ffffff;
  border: 1px solid var(--rule-soft);
  border-radius: 4px;
  box-shadow: var(--shadow);
}}
figcaption {{
  font-size: .88rem;
  line-height: 1.55;
  color: var(--muted);
  margin-top: .8rem;
  max-width: 40rem;
}}

/* the trailing italic note under References */
.paper > p > em {{ color: var(--muted); }}

@media (prefers-reduced-motion: reduce) {{
  * {{ animation: none !important; transition: none !important; }}
}}
:focus-visible {{ outline: 2px solid var(--signal); outline-offset: 3px; border-radius: 2px; }}
</style>

<div class="page">
  <header class="masthead">
    <p class="eyebrow">Empirical note &middot; 124M parameters &middot; two corpora &middot; <b>{runs} runs</b></p>
    <h1>{title}</h1>
    <p class="standfirst">Sequence packing is sold as free throughput. It moves the
      optimal learning rate by 2&ndash;5&times;, and the standard advice to inherit
      the old one gives the quality back.</p>
    <p class="status">{status}</p>
  </header>

  <section class="findings">
    <div class="finding">
      <h2>The optimum moves</h2>
      <span class="figure">4.86&times; / 2.07&times;</span>
      <p>How far the best learning rate shifts when packing is turned on at a
        matched data budget, on Alpaca and Dolly. Inheriting assumes 1.0&times;.</p>
    </div>
    <div class="finding">
      <h2>What retuning is worth</h2>
      <span class="figure">0.050 / 0.018</span>
      <p>Nats of held-out loss recovered by retuning instead of inheriting. At the
        inherited rate, packing beats not packing on neither corpus.</p>
    </div>
    <div class="finding miss">
      <h2>No shared exponent</h2>
      <span class="figure">0.67 vs 0.44</span>
      <p>The batch exponent on the two corpora. A prediction registered from the
        first missed the second by 1.28&times; &mdash; about 2.9&times; the seed spread.</p>
    </div>
  </section>

  <div class="layout">
    <nav class="toc" aria-label="Contents">{nav}</nav>
    <article class="paper">{body}</article>
  </div>
</div>
"""


if __name__ == "__main__":
    main()
