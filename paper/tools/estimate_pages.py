"""Rough page estimate for an llncs paper: text words / ~480 per page + table and equation heights.
Only a guide -- the real page count needs a LaTeX compile."""
import re
import sys
from pathlib import Path


def stats(p):
    t = Path(p).read_text(encoding="utf-8")
    body = t[t.index("\\section"):t.index("\\begin{thebibliography}")]
    tables = re.findall(r"\\begin\{table\}.*?\\end\{table\}", body, re.S)
    rows = sum(len(re.findall(r"\\\\", tb)) for tb in tables)
    text = re.sub(r"\\begin\{table\}.*?\\end\{table\}", "", body, flags=re.S)
    text = re.sub(r"\$[^$]*\$", " M ", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-']*", text))
    eq = len(re.findall(r"\\begin\{equation\}", body))
    bib = len(re.findall(r"\\bibitem", t))
    # llncs page: ~480 words; a table row ~0.022 page + 0.07 per table (caption, spacing); equation ~0.04 page;
    # bibliography ~0.045 page per entry; title+abstract ~0.45 page
    pages = words / 480 + rows * 0.022 + len(tables) * 0.07 + eq * 0.04 + bib * 0.045 + 0.45
    return words, len(tables), rows, eq, bib, pages


for p in sys.argv[1:]:
    w, nt, r, e, b, pg = stats(p)
    print(f"{Path(p).name}: words {w}, tables {nt} ({r} rows), equations {e}, refs {b} -> about {pg:.1f} pages")
