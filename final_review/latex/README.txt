S7 project report, LaTeX source (Amrita S7_Project_Report_Template, preamble unchanged)

Files
  S7_Project_Report.tex   the report (the template's preamble, front matter, chapter and section names, longtable
                          landscape table, bibliography and appendix structure)
  amrita-logo.png         institution logo, must sit beside the .tex file (the template looks for it there)
  figures/                every chart, diagram and dashboard screenshot used by the report

Build (twice, so the table of contents and lists settle)
  pdflatex S7_Project_Report.tex
  pdflatex S7_Project_Report.tex
or, with Tectonic:  tectonic S7_Project_Report.tex

Only packages the template already loads are used (mathptmx, geometry, titlesec, fancyhdr, graphicx, array, longtable,
pdflscape, setspace, hyperref). Result: 69 pages, A4, 12 pt, one-and-a-half spacing.

Values to change if they are wrong: certificate and declaration date (4 November 2026), the guide's designation
(Assistant Professor). The coordinator and chairperson are left without printed names, as requested.
