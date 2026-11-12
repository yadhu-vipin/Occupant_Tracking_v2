"""Rebuild the four Results and Analysis slides (17-20) of the review deck from the measured numbers.
Usage: python build_results_slides.py <in.pptx> <out.pptx>   (reads agg_cen.json, agg50.json from the scratchpad)"""
import io, json, sys
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Pt, Emu
from pptx.enum.shapes import MSO_SHAPE

S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
C = json.load(open(S + r"\agg_cen.json"))
A = json.load(open(S + r"\agg50.json"))
CRIMSON, GREY, PANEL, INK, WHITE, BAND = RGBColor(0xA4, 0x12, 0x3F), RGBColor(0x6B, 0x6B, 0x6B), RGBColor(0xFB, 0xF4, 0xF6), RGBColor(0x22, 0x22, 0x22), RGBColor(255, 255, 255), RGBColor(0xF7, 0xF0, 0xF2)
FONT = "Georgia"
src, dst = sys.argv[1], sys.argv[2]
prs = Presentation(src)


def slide_by_title(n):
    return prs.slides[n - 1]


def clear(slide, keep=("Title 2", "Slide Number Placeholder 3")):
    for sh in list(slide.shapes):
        if sh.name not in keep:
            sh._element.getparent().remove(sh._element)


def text(slide, x, y, w, h, paras, size=12, bold=False, color=INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, after=4):
    """paras: list of str or list of (str, dict(bold,color,size)) runs per paragraph."""
    tb = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Pt(4)
    tf.margin_top = tf.margin_bottom = Pt(2)
    for i, p in enumerate(paras):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align
        para.space_after = Pt(after)
        runs = p if isinstance(p, list) else [(p, {})]
        for t, o in runs:
            r = para.add_run()
            r.text = t
            r.font.name = FONT
            r.font.size = Pt(o.get("size", size))
            r.font.bold = o.get("bold", bold)
            r.font.color.rgb = o.get("color", color)
    return tb


def panel(slide, x, y, w, h):
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Pt(x), Pt(y), Pt(w), Pt(h))
    sh.adjustments[0] = 0.06
    sh.fill.solid()
    sh.fill.fore_color.rgb = PANEL
    sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def stat(slide, x, y, w, h, big, small, big_size=26):
    panel(slide, x, y, w, h)
    text(slide, x + 10, y + 6, w - 20, 36, [big], size=big_size, bold=True, color=CRIMSON)
    text(slide, x + 10, y + 6 + big_size * 1.35, w - 20, h - 12 - big_size * 1.35, [small], size=11, color=INK)


def table(slide, x, y, widths, rows, hdr_rows=1, size=11, row_h=22, bold_first=True, merges=(), aligns=None):
    n_r, n_c = len(rows), len(widths)
    gf = slide.shapes.add_table(n_r, n_c, Pt(x), Pt(y), Pt(sum(widths)), Pt(row_h * n_r))
    tbl = gf.table
    tbl.first_row = False
    tbl.horz_banding = False
    for j, w in enumerate(widths):
        tbl.columns[j].width = Pt(w)
    for i in range(n_r):
        tbl.rows[i].height = Pt(row_h)
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.margin_left = cell.margin_right = Pt(5)
            cell.margin_top = cell.margin_bottom = Pt(2)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            head = i < hdr_rows
            cell.fill.fore_color.rgb = CRIMSON if head else (BAND if (i - hdr_rows) % 2 == 1 else WHITE)
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.CENTER
            if aligns and aligns[j]:
                p.alignment = aligns[j]
            r = p.add_run()
            r.text = val
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = head or (bold_first and j == 0)
            r.font.color.rgb = WHITE if head else INK
    for (r0, c0, r1, c1) in merges:
        tbl.cell(r0, c0).merge(tbl.cell(r1, c1))
    return gf


def pct(v, d=1):
    return f"{100 * v:.{d}f}%"


def f3(v):
    return f"{v:.3f}"


D, Z = C["decentralised"], C["centralised"]

# ------------------------------------------------------------------ slide 17: identification and routing
s = slide_by_title(17)
clear(s)
text(s, 45, 74, 870, 22, ["Identification and routing: mean of 50 independent random populations (seeds 0 to 49); 10,000 events per run, 8,000 local and 2,000 visitor"], size=12, color=GREY)
# other buildings asked per event, by cohort (mean candidates queried)
def asked(side, c):
    a_all, a_vis = side["asked_per_event_all"], side["asked_per_visitor_event"]
    return {"all": a_all, "visitor": a_vis, "local": max(0.0, (a_all * 10000 - a_vis * 2000) / 8000)}[c]


rows = [["Cohort", "Events", "Precision", "", "Recall", "", "F1 score", "", "Other buildings asked", ""],
        ["", "", "Central", "Ours", "Central", "Ours", "Central", "Ours", "Central", "Ours"]]
for label, c, n in (("All events", "all", "10,000"), ("Local (registered here)", "local", "8,000"), ("Visitor (registered elsewhere)", "visitor", "2,000")):
    rows.append([label, n] + [f3(x[c][k]) for k in ("precision", "recall", "f1") for x in (Z, D)] + [f"{asked(Z, c):.2f}", f"{asked(D, c):.2f}"])
table(s, 45, 100, [190, 52, 60, 60, 60, 60, 60, 60, 62, 62], rows, hdr_rows=2, size=11, row_h=22,
      merges=((0, 2, 0, 3), (0, 4, 0, 5), (0, 6, 0, 7), (0, 8, 0, 9), (0, 0, 1, 0), (0, 1, 1, 1)))
text(s, 45, 222, 870, 20, ["Tracking state (who is where after every event, against the state ground truth)"], size=12, bold=True, color=GREY)
rows = [["Tracking state", "Labels", "Precision", "", "Recall", "", "F1 score", ""],
        ["", "", "Central", "Ours", "Central", "Ours", "Central", "Ours"]]
for label, lv in (("Building", "building"), ("Zone", "zone"), ("Building and zone", "location")):
    rows.append([label, str(int(D["state"][lv]["labels"]))] + [f3(x["state"][lv][k]) for k in ("mean_precision", "mean_recall", "mean_f1") for x in (Z, D)])
table(s, 45, 244, [150, 50, 56, 56, 56, 56, 56, 56], rows, hdr_rows=2, size=11, row_h=22,
      merges=((0, 2, 0, 3), (0, 4, 0, 5), (0, 6, 0, 7), (0, 0, 1, 0), (0, 1, 1, 1)))
# right of the state table: the integer buildings-contacted figure
rr = Z["visitor"]["events"]
stat(s, 590, 244, 325, 104, f"{round(Z['asked_per_routed_lookup'])} vs {round(D['asked_per_routed_lookup'])}",
     "buildings contacted per routed visitor lookup: the central search asks all 9 other buildings; ours asks the shortlist, 2 on average")
gap = 100 * C["recall_gap_all"]
stat(s, 45, 368, 280, 96, f"{gap:.2f} pts", f"lower recall than the central search ({pct(D['all']['recall'], 2)} against {pct(Z['all']['recall'], 2)}); the central search is ahead in {C['central_ahead_seeds']} of 50 seeds", 24)
stat(s, 337, 368, 280, 96, "0.00 pts", f"local recall gap: both identify the same {D['local']['tp']:,.0f} local events; the central search gives {Z['local']['fp'] - D['local']['fp']:.0f} more wrong answers where ours abstains", 24)
stat(s, 629, 368, 286, 96, f"{pct(Z['visitor']['recall'] - D['visitor']['recall'], 1).replace('%', '')} pts", f"visitor recall gap ({pct(D['visitor']['recall'])} against {pct(Z['visitor']['recall'])}): the cost of routing to a shortlist", 24)
text(s, 45, 468, 870, 28, ["Central = the same pipeline, but a visitor is searched against all 450 other people directly. Precision, recall and F1 are one-vs-rest scores; recall counts every abstention as a miss."], size=9.5, color=GREY)

# ------------------------------------------------------------------ slide 18: dynamic visitor database
s = slide_by_title(18)
clear(s)
text(s, 45, 74, 870, 22, ["Dynamic visitor database: the same events and the same seeds, with the database answering lookups (on) and never answering (off)"], size=12, color=GREY)
ab = C["ablation"]
on, off = ab["db_on"], ab["db_off"]
rows = [["Visitor events (2,000 per run)", "Database off", "Database on"],
        ["Answered inside the building, no routing", "0", f"{on['from_db']:,.0f} ({100 * on['from_db'] / 2000:.1f}%)"],
        ["Other buildings asked per visitor event", f"{off['asked_per_visitor_event']:.2f}", f"{on['asked_per_visitor_event']:.2f}"],
        ["Buildings asked in total, per day", f"{off['total_asked']:,.0f}", f"{on['total_asked']:,.0f}"],
        ["Visitor precision", f3(off["visitor"]["precision"]), f3(on["visitor"]["precision"])],
        ["Visitor recall", f3(off["visitor"]["recall"]), f3(on["visitor"]["recall"])],
        ["Visitor F1 score", f3(off["visitor"]["f1"]), f3(on["visitor"]["f1"])],
        ["Visitor recall in the tracked state (building and zone)", f3(off["visitor_recall_state"]["location"]), f3(on["visitor_recall_state"]["location"])]]
table(s, 45, 100, [330, 110, 120], rows, hdr_rows=1, size=11, row_h=26)
stat(s, 640, 100, 275, 104, f"{100 * on['from_db'] / 2000:.1f}%", "of visitor events are answered from the visitor database, with no routing", 28)
cut = 100 * (1 - on["total_asked"] / off["total_asked"])
stat(s, 640, 214, 275, 94, f"{cut:.0f}% fewer", f"building queries over the day ({off['total_asked']:,.0f} to {on['total_asked']:,.0f}), and visitor recall {'rises' if on['visitor']['recall'] > off['visitor']['recall'] else 'changes'} by {abs(100 * (on['visitor']['recall'] - off['visitor']['recall'])):.1f} points", 22)
panel(s, 45, 322, 870, 160)
text(s, 58, 328, 845, 150, [
    [("Why it is worth having. ", {"bold": True, "color": CRIMSON}), ("A visitor is seen many times in a day. The first sighting is routed; the building then keeps the visitor's 20 reference embeddings for the rest of the day (day cache), so repeats are matched inside the building, which is faster and sends nothing to other buildings.", {})],
    [("What it costs. ", {"bold": True, "color": CRIMSON}), (f"A wrong first identification is then repeated: about 14 wrong identifications per day come from the database, and only 4.3% of them carry a warning flag.", {})],
    [("Visit pointers. ", {"bold": True, "color": CRIMSON}), (f"The home building records where its visitor is: {pct(A['visitor']['pointer_prec'] / 100)} of the closed visits (about 414 per run) pointed to the right building.", {})],
    [("Scope. ", {"bold": True, "color": CRIMSON}), ("These are one-day runs, so every database answer comes from the day cache; the permanent pool for frequent visitors needs visits over several days and is not exercised here.", {})]],
     size=11.5)

# ------------------------------------------------------------------ slide 19: security and RBAC
s = slide_by_title(19)
clear(s)
text(s, 45, 74, 870, 22, ["Security and role-based access control: verified by attack tests and a replay of 64 requests against the running system"], size=12, color=GREY)
# disclosure level chips
levels = [("L0", "no information"), ("L1", "presence only"), ("L2", "exact zone, present only"), ("L3", "full history")]
for i, (lv, d) in enumerate(levels):
    x = 45 + i * 219
    panel(s, x, 100, 209, 50)
    text(s, x + 6, 103, 197, 44, [[(lv + "  ", {"bold": True, "color": CRIMSON, "size": 14}), (d, {"size": 11})]], anchor=MSO_ANCHOR.MIDDLE)
text(s, 45, 160, 430, 20, ["Persona replay: 16 requests per role"], size=12, bold=True, color=GREY)
rows = [["Role", "Answered", "Refused", "Levels granted"],
        ["Dean", "11", "5", "L3 ×10, L2 ×1"],
        ["Teacher", "5", "11", "L3 ×3, L1 ×2"],
        ["Student", "1", "15", "L1 ×1"],
        ["Outsider (other building)", "4", "12", "L3 ×3 (self), L1 ×1"],
        ["Total", "21", "43", "all as the policy specifies"]]
table(s, 45, 182, [150, 70, 70, 140], rows, hdr_rows=1, size=11, row_h=24)
text(s, 45, 334, 430, 100, [
    "A request for a person in another building is refused unless a relationship allows it; every refusal carries a rule code.",
    "A Student sees only themself and the current position of an assigned teacher; no history of anyone else."], size=11, color=INK)
text(s, 500, 160, 415, 20, ["Automated tests: 574, all passing"], size=12, bold=True, color=GREY)
rows = [["Area", "Tests"],
        ["Access control", "112"], ["Sealed transfers", "61"], ["API", "98"], ["Queries and explanations", "181"],
        ["Pipeline, population, events, evaluation", "98"], ["Standalone, centralised, state library", "19"], ["Shared utilities", "5"]]
table(s, 500, 182, [330, 85], rows, hdr_rows=1, size=11, row_h=21)
panel(s, 45, 424, 870, 62)
text(s, 58, 428, 845, 56, [
    [("Attacks that are refused: ", {"bold": True, "color": CRIMSON}), ("a tampered header field, a replayed message, a wrong sender or recipient, a revoked certificate, brute-force login, a token used as another person, and a cache shared between roles. A failed step returns nothing (fail closed).", {})],
    [("Limits: ", {"bold": True, "color": CRIMSON}), ("the network and certificate authority are simulated, and there is no real TLS or penetration test: this is a tested design, not a hardened deployment.", {})]], size=11)

# ------------------------------------------------------------------ slide 20: explainability
pics = {}
old = slide_by_title(20)
for sh in old.shapes:
    if sh.shape_type == 13:
        pics[sh.name] = sh.image.blob
s = old
clear(s)
text(s, 45, 74, 870, 22, ["Explainability: do the flags catch wrong answers, and what do the explanations cost? Mean of 50 seeds"], size=12, color=GREY)
F = A["flags_ident"]
rows = [["Identified", "Wrong ones flagged", "Correct ones flagged"]]
for label, k in (("All identifications", "all"), ("In the person's own building", "local"), ("Visitors found by routing", "routed"), ("Visitors from the visitor database", "db")):
    rows.append([label, f"{F[k]['share_of_wrong_flagged']:.1f}%", f"{F[k]['share_of_correct_flagged']:.1f}%"])
table(s, 45, 100, [250, 130, 130], rows, hdr_rows=1, size=11, row_h=26)
text(s, 45, 234, 510, 40, ["More flagged among the wrong is better; fewer among the correct is better. A flag is raised from facts saved at identification and never uses the true identity."], size=10.5, color=GREY)
ans = A["answers"]
stat(s, 570, 100, 345, 80, f"{ans['recall']:.0f}% / {ans['precision']:.0f}%", "of wrong query answers carry a flag / of flagged answers are wrong", 22)
ov = json.load(open(S + r"\overhead53.json"))
stat(s, 570, 190, 345, 84, "+13%", "time per query (5.9 ms to 6.6 ms); the response grows from 16 KB to 119 KB with the full explanation", 22)
text(s, 45, 282, 430, 18, ["The same query from two roles"], size=12, bold=True, color=GREY)
names = sorted(pics)
s.shapes.add_picture(io.BytesIO(pics[names[0]]), Pt(45), Pt(304), Pt(330), Pt(170))
s.shapes.add_picture(io.BytesIO(pics[names[1]]), Pt(390), Pt(304), Pt(330), Pt(170))
text(s, 45, 474, 330, 36, ["Dean: the answer, with its rule, evidence and uncertainty flags."], size=10, color=GREY)
text(s, 390, 474, 330, 36, ["Student: refused with a rule code; the explanation is withheld."], size=10, color=GREY)
panel(s, 735, 304, 180, 170)
text(s, 743, 310, 164, 160, [[("Faithful by test. ", {"bold": True, "color": CRIMSON}), ("40 yes, no and empty cases check that each piece of evidence is a real event and that redoing the answer from it gives the same answer. An explanation never shows more than its role may see.", {})]], size=10.5)
prs.save(dst)
print("saved", dst)
