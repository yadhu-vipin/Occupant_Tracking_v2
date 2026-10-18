"""Springer LNCS conformance checks for routing_paper_springer.tex (items taken from the llncs sample paper)."""
import re
import sys
from pathlib import Path

t = Path(sys.argv[1] if len(sys.argv) > 1 else "routing_paper_springer.tex").read_text(encoding="utf-8")
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))
    ok &= bool(cond)


abstract = re.search(r"\\begin\{abstract\}(.*?)\\keywords", t, re.S).group(1)
text = re.sub(r"\$[^$]*\$", " MATH ", abstract)
text = re.sub(r"\\[a-zA-Z]+", " ", text).replace("{", " ").replace("}", " ").replace("%", " ")
words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-']*", text))
check("abstract is 150-250 words", 150 <= words <= 250, f"({words} words)")
check("documentclass llncs runningheads", "\\documentclass[runningheads]{llncs}" in t)
check("T1 fontenc + newtx (template)", "\\usepackage[T1]{fontenc}" in t and "newtxtext" in t and "varvw" in t)
check("titlerunning given", "\\titlerunning{" in t)
check("authorrunning given", "\\authorrunning{" in t)
check("keywords inside abstract", t.index("\\keywords") < t.index("\\end{abstract}"))
check("no heading deeper than \\paragraph (max 4 levels)", "\\subparagraph" not in t)
check("no typed period in run-in headings (class adds it)", not re.search(r"\\subsubsection\{[^}]*\.\}", t))
check("table captions above tabular", all(
    m.group(0).index("\\caption") < m.group(0).index("\\begin{tabular}")
    for m in re.finditer(r"\\begin\{table\}.*?\\end\{table\}", t, re.S)))
check("credits environment with ackname/discintname", "\\begin{credits}" in t and "\\ackname" in t and "\\discintname" in t)
bib = re.findall(r"\\bibitem\{([^}]+)\}", t)
cited = {c.strip() for cs in re.findall(r"\\cite\{([^}]+)\}", t) for c in cs.split(",")}
check("every bibitem is cited", set(bib) == cited, f"(uncited: {sorted(set(bib) - cited)})")
width = int(re.search(r"\\begin\{thebibliography\}\{(\d+)\}", t).group(1))
check("thebibliography width matches entry count", width == len(bib), f"({width} vs {len(bib)})")
check("no raw 'Fig.' needed / no figures", "\\includegraphics" not in t)
check("sections use Table~\\ref (non-breaking)", not re.search(r"Table \\ref|Section \\ref", t))
check("placeholders resolved", "@@" not in t)
print("ALL PASS" if ok else "SOME CHECKS FAILED")
