# audio_describer/core/script_export.py
"""Professional script exports (audit §3.5, §10): TTML DAPT, production script (DOCX),
full script with dialogue (WCAG 1.2.8). Standard library only."""
import zipfile
from xml.sax.saxutils import escape, quoteattr

from ..i18n_setup import _
from . import timing

DAPT_PROFILE = "http://www.w3.org/ns/ttml/profile/dapt1.0/content"


def _represents(desc):
    flags = timing.flags_of(desc)
    if timing.FLAG_TRANSLATION in flags:
        return "audio.dialogue"
    return "visual.text" if timing.FLAG_ON_SCREEN_TEXT in flags else "visual.nonText"


def to_dapt(descriptions, lang):
    """W3C DAPT 1.0 audio description script (scriptType preRecording), one Script Event per description."""
    represents = sorted({_represents(d) for d in descriptions}) or ["visual.nonText"]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter"',
             '    xmlns:daptm="http://www.w3.org/ns/ttml/profile/dapt#metadata"',
             f'    ttp:contentProfiles="{DAPT_PROFILE}" ttp:timeBase="media" xml:lang={quoteattr(lang)}',
             f'    daptm:langSrc="zxx" daptm:scriptRepresents="{" ".join(represents)}" daptm:scriptType="preRecording">',
             '  <body>']
    for i, d in enumerate(descriptions, 1):
        rep = _represents(d)
        lang_src = f' daptm:langSrc={quoteattr(lang)}' if rep != "visual.nonText" else ""
        lines.append(f'    <div xml:id="ad{i}" begin="{d[0]:.3f}s" end="{d[1]:.3f}s" daptm:represents="{rep}"{lang_src}>')
        lines.append(f'      <p>{escape(d[2])}</p>')
        lines.append('    </div>')
    lines += ['  </body>', '</tt>', '']
    return "\n".join(lines)


def status_text(desc):
    return _("validated") if timing.FLAG_VALIDATED in timing.flags_of(desc) else _("to review")


def production_rows(descriptions):
    """Header + one row per description: in, out, text, level, status, notes."""
    rows = [[_("In"), _("Out"), _("Description"), _("Level"), _("Status"), _("Notes")]]
    for d in descriptions:
        rows.append([timing.format_vtt_time(d[0]), timing.format_vtt_time(d[1]), d[2],
                     str(timing.level_of(d)), status_text(d), timing.note_of(d)])
    return rows


def _docx_cell(text, bold=False):
    run_props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    paragraphs = "".join(f'<w:p><w:r>{run_props}<w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>'
                         for line in (str(text).split("\n") or [""]))
    return f"<w:tc>{paragraphs}</w:tc>"


def write_docx(path, title, rows):
    """Minimal Word document: a title and one table (first row as header, repeated on each page)."""
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    table_rows = []
    for i, row in enumerate(rows):
        header = "<w:trPr><w:tblHeader/></w:trPr>" if i == 0 else ""
        table_rows.append(f"<w:tr>{header}" + "".join(_docx_cell(c, bold=(i == 0)) for c in row) + "</w:tr>")
    body = (f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t xml:space="preserve">{escape(title)}</w:t></w:r></w:p>'
            '<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>'
            + "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                      for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
            + "</w:tblBorders></w:tblPr>" + "".join(table_rows) + "</w:tbl><w:sectPr/>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {ns}><w:body>{body}</w:body></w:document>')


def to_full_script(descriptions, dialogue):
    """WCAG 1.2.8 media alternative: dialogue and descriptions interleaved in time order.
    dialogue: [(start_seconds, speaker, text), ...]."""
    events = [(d[0], 1, _("[Description]"), d[2]) for d in descriptions]
    events += [(start, 0, speaker or _("Voice"), text) for start, speaker, text in dialogue]
    events.sort(key=lambda e: (e[0], e[1]))
    return "".join(f"{timing.format_hms(t)}  {who}: {text}\n" for t, _order, who, text in events)
