import sys, csv, re, unicodedata, collections
from datetime import datetime, timezone
sys.path.insert(0, "outreach")
import serve_send as ss
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

def clean(s):
    s = unicodedata.normalize("NFKC", s or "")
    return s.encode("latin-1", "replace").decode("latin-1")

info = {r["email"].lower(): r for r in csv.DictReader(open("scraper/hunts/practice_list_2026-09-29.csv", encoding="utf-8"))}
ss.AUDIENCE_ONLY = "practice"
q = ss.initial_candidates(ss.load_sent())
rows = []
for i, r in enumerate(q, 1):
    e = r["email"].strip().lower(); h = info.get(e, {})
    addr = [x.strip() for x in (h.get("address") or "").split(",")]
    city = ", ".join(addr[-3:-1]) if len(addr) >= 3 else ""
    city = re.sub(r"\s+\d{5}(-\d{4})?$", "", city)
    person = " ".join(x for x in (h.get("first_name",""), h.get("last_name","")) if x)
    rows.append([str(i), r["business_name"], (h.get("practice") or "").capitalize(), person,
                 h.get("position",""), city, e, h.get("phone","")])

styles = getSampleStyleSheet()
cell = ParagraphStyle("cell", parent=styles["Normal"], fontName="Helvetica", fontSize=6.6, leading=8)
head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
P = lambda t, st=cell: Paragraph(clean(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), st)
out = "exports/practice_queue_2026-09-30.pdf"
doc = SimpleDocTemplate(out, pagesize=landscape(letter), leftMargin=0.4*inch, rightMargin=0.4*inch,
                        topMargin=0.45*inch, bottomMargin=0.45*inch, title="Practice List Queue", author="Marinexis Biologics")
story = [Paragraph("Practice list queue (your Medspa_US_2 list)", styles["Title"]),
         Paragraph(clean(f"{len(rows):,} practices queued, in send order. HELD: none of these send until you say go. "
             f"They sit behind the RUO vendors (74 left, done Oct 1). At 50/day this list is about {round(len(rows)/50)} days of sending. "
             f"Built {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC."), styles["Normal"]), Spacer(1, 10)]
c = collections.Counter(r[2] for r in rows)
summ = [[P("Practice type", head), P("Queued", head)]] + [[P(k), P(f"{v:,}")] for k, v in c.most_common()] + [[P("Total"), P(f"{len(rows):,}")]]
t = Table(summ, colWidths=[2.6*inch, 0.9*inch])
t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0b6e6d")),("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#b7c9c7")),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f4f8f7")])]))
story += [t, Spacer(1, 10), Paragraph(clean("Also held, not in this queue: 2,205 catch-all addresses from the same list "
    "(the mail server could not confirm them), and 9,134 rows whose practice type is not a peptide buyer "
    "(dentists, day spas, surgical centers, massage, physical therapy and similar)."), styles["Normal"]), PageBreak()]
cols = ["#", "Business", "Type", "Contact", "Role", "City", "Email", "Phone"]
widths = [0.4, 2.0, 1.1, 1.2, 1.1, 1.2, 2.2, 1.0]
t = Table([[P(h, head) for h in cols]] + [[P(x) for x in r] for r in rows], colWidths=[w*inch for w in widths], repeatRows=1)
t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#0b6e6d")),("VALIGN",(0,0),(-1,-1),"TOP"),
    ("LINEBELOW",(0,0),(-1,-1),0.25,colors.HexColor("#d3e0de")),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f4f8f7")]),
    ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
story.append(t)
def footer(canv, d):
    canv.saveState(); canv.setFont("Helvetica", 7); canv.setFillColor(colors.HexColor("#4a5b5a"))
    canv.drawString(0.4*inch, 0.25*inch, "Marinexis Biologics - practice list queue (held)")
    canv.drawRightString(landscape(letter)[0]-0.4*inch, 0.25*inch, f"Page {d.page}")
    canv.restoreState()
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(out, len(rows))
