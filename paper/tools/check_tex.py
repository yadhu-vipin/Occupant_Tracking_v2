"""Structural sanity checks for dacs2026_paper.tex (no LaTeX compiler is installed on this machine)."""
import re
import sys
from collections import Counter
from pathlib import Path

t = Path(sys.argv[1] if len(sys.argv) > 1 else "dacs2026_paper.tex").read_text(encoding="utf-8")
ok = True

b = re.findall(r"\\begin\{(\w+\*?)\}", t)
e = re.findall(r"\\end\{(\w+\*?)\}", t)
print("environments balanced:", Counter(b) == Counter(e)); ok &= Counter(b) == Counter(e)

u = re.sub(r"\\[{}]", "", t)
print("brace balance:", u.count("{") - u.count("}")); ok &= (u.count("{") == u.count("}"))

d = t.replace("\\$", "")
print("dollar signs even:", d.count("$") % 2 == 0); ok &= d.count("$") % 2 == 0

labels = set(re.findall(r"\\label\{([^}]+)\}", t))
refs = set(re.findall(r"\\ref\{([^}]+)\}", t))
print("refs without label:", sorted(refs - labels)); ok &= not (refs - labels)

# a number wrapped in math that itself contains a "$\pm$" (e.g. "$0.49 $\pm$ 0.13$")
nested = re.findall(r"\$-?[0-9.]+ \$\\pm\$ [0-9.]+\$", t)
print("nested-math bugs:", len(nested), nested); ok &= not nested

for m in re.finditer(r"\\begin\{tabular\}\{([^}]*)\}(.*?)\\end\{tabular\}", t, re.S):
    spec = m.group(1)
    ncol = sum(spec.count(c) for c in "rlc")
    for row in m.group(2).split("\\\\"):
        row = row.replace("\\hline", "").strip()
        if not row or "multicolumn" in row:
            continue
        n = row.count("&") + 1
        if n != ncol:
            print("COLUMN MISMATCH", spec, n, row[:70]); ok = False

cites = set(re.findall(r"\\cite\{([^}]+)\}", t))
bibs = set(re.findall(r"\\bibitem\{([^}]+)\}", t))
allc = {c.strip() for cs in cites for c in cs.split(",")}
print("cites without bibitem:", sorted(allc - bibs)); ok &= not (allc - bibs)
print("ALL OK" if ok else "PROBLEMS FOUND")
