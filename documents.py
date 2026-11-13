"""Turns a tailored resume / cover letter (plain dicts) into .docx and/or .pdf files.

Word is the default ([agent] formats in config.toml). Both formats are built from the
same content so they always match. No Word or LibreOffice needed: .docx via
python-docx, .pdf via fpdf2.
"""

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from fpdf import FPDF
from fpdf.enums import XPos, YPos

ACCENT = (0x1F, 0x3A, 0x5F)   # dark blue for headings
MUTED = (0x55, 0x55, 0x55)    # grey for contact line / locations
MARGIN_IN = 0.7

# ---------- shared helpers ----------

def contact_line(contact):
    parts = [contact.get("city"), contact.get("phone"), contact.get("email"),
             contact.get("linkedin"), contact.get("website")]
    return "  |  ".join(p.replace("https://", "").replace("www.", "") for p in parts if p)


def full_name(contact):
    return f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()


def today_text():
    d = date.today()
    return f"{d:%B} {d.day}, {d.year}"


# ---------- Word ----------

def _docx_base():
    doc = Document()
    for section in doc.sections:
        section.left_margin = section.right_margin = Inches(MARGIN_IN)
        section.top_margin = section.bottom_margin = Inches(0.6)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    normal.paragraph_format.space_after = Pt(2)
    return doc


def _para(doc, text="", size=None, bold=False, italic=False, color=None, after=None, before=None):
    p = doc.add_paragraph()
    if text:
        run = p.add_run(text)
        run.bold, run.italic = bold, italic
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = RGBColor(*color)
    if after is not None:
        p.paragraph_format.space_after = Pt(after)
    if before is not None:
        p.paragraph_format.space_before = Pt(before)
    return p


def _bottom_border(paragraph):
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in {"w:val": "single", "w:sz": "6", "w:space": "1", "w:color": "1F3A5F"}.items():
        bottom.set(qn(k), v)
    borders.append(bottom)
    p_pr.append(borders)


def _docx_header(doc, contact):
    _para(doc, full_name(contact), size=20, bold=True, color=ACCENT, after=0)
    _para(doc, contact_line(contact), size=9.5, color=MUTED, after=6)


def _docx_heading(doc, text):
    p = _para(doc, text.upper(), size=10.5, bold=True, color=ACCENT, before=8, after=3)
    p.paragraph_format.keep_with_next = True   # never strand a heading at the bottom of a page
    _bottom_border(p)


def _docx_bullet(doc, text):
    p = doc.add_paragraph(text, style="List Bullet")
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.left_indent = Inches(0.25)


def _docx_title_line(doc, left, right, width_in):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.keep_with_next = True   # job title stays with its first bullet
    p.paragraph_format.tab_stops.add_tab_stop(Inches(width_in), WD_TAB_ALIGNMENT.RIGHT)
    p.add_run(left).bold = True
    if right:
        r = p.add_run("\t" + right)
        r.font.color.rgb = RGBColor(*MUTED)
    return p


def resume_docx(resume, contact, path):
    doc = _docx_base()
    width = 8.5 - 2 * MARGIN_IN
    _docx_header(doc, contact)
    if resume.get("headline"):
        _para(doc, resume["headline"], size=11.5, bold=True, after=2)
    if resume.get("summary"):
        _para(doc, resume["summary"], after=2)

    if resume.get("skills"):
        _docx_heading(doc, "Core skills")
        _para(doc, "  •  ".join(resume["skills"]), after=2)

    if resume.get("experience"):
        _docx_heading(doc, "Experience")
        for role in resume["experience"]:
            _docx_title_line(doc, f"{role['title']}, {role['company']}", role.get("dates", ""), width)
            if role.get("location"):
                loc = _para(doc, role["location"], size=9.5, italic=True, color=MUTED, after=1)
                loc.paragraph_format.keep_with_next = True
            for b in role.get("bullets", []):
                _docx_bullet(doc, b)

    if resume.get("education"):
        _docx_heading(doc, "Education")
        for ed in resume["education"]:
            _docx_title_line(doc, f"{ed['credential']}, {ed['school']}", ed.get("dates", ""), width)
            if ed.get("details"):
                _para(doc, ed["details"], size=9.5, color=MUTED, after=1)

    for section in resume.get("other_sections", []):
        if section.get("items"):
            _docx_heading(doc, section["heading"])
            for item in section["items"]:
                _docx_bullet(doc, item)

    doc.save(path)


def cover_letter_docx(letter, contact, company, path):
    doc = _docx_base()
    _docx_header(doc, contact)
    _para(doc, today_text(), before=6, after=10)
    if company:
        _para(doc, company, after=10)
    _para(doc, letter["greeting"], after=8)
    for text in letter["paragraphs"]:
        _para(doc, text, after=8)
    _para(doc, letter.get("sign_off") or "Sincerely,", before=4, after=14)
    _para(doc, full_name(contact))
    doc.save(path)


# ---------- PDF ----------

# Fonts tried in order. Unicode TTF fonts handle accents, curly quotes and dashes.
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf", "C:/Windows/Fonts/calibrii.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
]
LATIN1_FIXES = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                "\u2014": "-", "\u2022": "-", "\u2026": "...", "\u00a0": " "}


class _Pdf(FPDF):
    def __init__(self):
        super().__init__(format="letter", unit="pt")
        m = MARGIN_IN * 72
        self.set_margins(m, 0.6 * 72, m)
        self.set_auto_page_break(True, margin=0.6 * 72)
        self.family = "Helvetica"
        self.unicode = False
        for regular, bold, italic in FONT_CANDIDATES:
            if all(Path(f).exists() for f in (regular, bold, italic)):
                self.add_font("Body", "", regular)
                self.add_font("Body", "B", bold)
                self.add_font("Body", "I", italic)
                self.family, self.unicode = "Body", True
                break
        self.add_page()

    def clean(self, text):
        text = text or ""
        if self.unicode:
            return text
        for a, b in LATIN1_FIXES.items():
            text = text.replace(a, b)
        return text.encode("latin-1", "replace").decode("latin-1")

    def font(self, size, style="", color=(0, 0, 0)):
        self.set_font(self.family, style, size)
        self.set_text_color(*color)

    def text_block(self, text, size=10.5, style="", color=(0, 0, 0), after=2, line=1.3):
        self.font(size, style, color)
        self.multi_cell(0, size * line, self.clean(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(after)

    def keep_room(self, height):
        """Start a new page now if the next `height` points won't fit, so headings and job
        titles never sit alone at the bottom of a page."""
        if self.get_y() + height > self.page_break_trigger:
            self.add_page()

    def heading(self, text):
        self.keep_room(6 + 14 + 4 + 45)   # heading plus a couple of lines of what follows
        self.ln(6)
        self.font(10.5, "B", ACCENT)
        self.cell(0, 14, self.clean(text.upper()), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def title_line(self, left, right):
        self.keep_room(3 + 14 + 13 + 30)  # title, location line and the first bullet
        self.ln(3)
        left, right = self.clean(left), self.clean(right)
        self.font(10.5, "B")
        left_w = self.get_string_width(left)
        self.font(10, "", MUTED)
        right_w = self.get_string_width(right)
        if left_w + right_w + 12 > self.epw:   # too long for one line: dates go below
            self.text_block(left, style="B", after=0)
            if right:
                self.text_block(right, size=10, color=MUTED, after=0)
            return
        y = self.get_y()
        self.font(10.5, "B")
        self.cell(left_w + 2, 14, left)
        self.font(10, "", MUTED)
        self.set_xy(self.l_margin, y)
        self.cell(0, 14, right, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def bullet(self, text):
        indent = 14
        self.font(10.5)
        self.keep_room(28)   # never leave a lone bullet glyph at the bottom of a page
        y = self.get_y()
        self.set_x(self.l_margin + 4)
        self.cell(indent - 4, 13.5, self.clean("\u2022"))
        self.set_xy(self.l_margin + indent, y)
        self.multi_cell(self.epw - indent, 13.5, self.clean(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def header_block(self, contact):
        self.text_block(full_name(contact), size=20, style="B", color=ACCENT, after=0, line=1.2)
        self.text_block(contact_line(contact), size=9.5, color=MUTED, after=6)


def resume_pdf(resume, contact, path):
    pdf = _Pdf()
    pdf.header_block(contact)
    if resume.get("headline"):
        pdf.text_block(resume["headline"], size=11.5, style="B", after=1)
    if resume.get("summary"):
        pdf.text_block(resume["summary"])
    if resume.get("skills"):
        pdf.heading("Core skills")
        pdf.text_block("  \u2022  ".join(resume["skills"]) if pdf.unicode else "  |  ".join(resume["skills"]))
    if resume.get("experience"):
        pdf.heading("Experience")
        for role in resume["experience"]:
            pdf.title_line(f"{role['title']}, {role['company']}", role.get("dates", ""))
            if role.get("location"):
                pdf.text_block(role["location"], size=9.5, style="I", color=MUTED, after=1)
            for b in role.get("bullets", []):
                pdf.bullet(b)
    if resume.get("education"):
        pdf.heading("Education")
        for ed in resume["education"]:
            pdf.title_line(f"{ed['credential']}, {ed['school']}", ed.get("dates", ""))
            if ed.get("details"):
                pdf.text_block(ed["details"], size=9.5, color=MUTED, after=1)
    for section in resume.get("other_sections", []):
        if section.get("items"):
            pdf.heading(section["heading"])
            for item in section["items"]:
                pdf.bullet(item)
    pdf.output(str(path))


def cover_letter_pdf(letter, contact, company, path):
    pdf = _Pdf()
    pdf.header_block(contact)
    pdf.ln(6)
    pdf.text_block(today_text(), after=10)
    if company:
        pdf.text_block(company, after=10)
    pdf.text_block(letter["greeting"], after=8)
    for text in letter["paragraphs"]:
        pdf.text_block(text, after=8)
    pdf.text_block(letter.get("sign_off") or "Sincerely,", after=16)
    pdf.text_block(full_name(contact))
    pdf.output(str(path))


def render_all(package, contact, company, folder, formats=("docx",)):
    """Write the resume + cover letter in each requested format ("docx", "pdf").
    Returns the list of file paths, in the order of `formats`."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    builders = {
        "docx": {"resume.docx": lambda p: resume_docx(package["resume"], contact, p),
                 "cover_letter.docx": lambda p: cover_letter_docx(package["cover_letter"], contact, company, p)},
        "pdf": {"resume.pdf": lambda p: resume_pdf(package["resume"], contact, p),
                "cover_letter.pdf": lambda p: cover_letter_pdf(package["cover_letter"], contact, company, p)},
    }
    formats = [f for f in dict.fromkeys(f.lower().lstrip(".") for f in formats) if f in builders] or ["docx"]
    out = []
    for fmt in formats:
        for name, build in builders[fmt].items():
            build(folder / name)
            out.append(folder / name)
    return out


def primary_files(file_names):
    """From a draft's file list, the resume and cover letter to send/upload: the first
    format listed (Word unless PDF was chosen). Returns {"resume": name, "cover": name}."""
    for name in file_names:
        stem, _, ext = name.rpartition(".")
        if stem in ("resume", "cover_letter"):
            return {"resume": f"resume.{ext}", "cover": f"cover_letter.{ext}"}
    return {"resume": "resume.pdf", "cover": "cover_letter.pdf"}   # drafts made before Word became the default
