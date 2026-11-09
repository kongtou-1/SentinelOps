"""Readable approved reports with a separate, complete frozen evidence download."""

from collections import Counter
from io import BytesIO
from xml.sax.saxutils import escape

from fastapi import HTTPException

from app.core.config import get_settings


def sections(row, content):
    yield "事故摘要", [content["summary"]]
    yield "影响范围", [content["impact"]]
    timeline = sorted(
        [
            r
            for r in row.snapshot["records"]
            if r["kind"] == "timeline"
            and (
                r["data"].get("actor_type") == "user"
                or r["data"].get("event_type") not in ("alert_created", "alert_linked")
            )
        ],
        key=lambda r: (r["occurred_at"], r["id"]),
    )
    yield (
        "关键时间线",
        [f"{r['occurred_at']} · {r['data']['event_type']} · 来源 {r['id']}" for r in timeline]
        or ["冻结证据中没有关键时间线记录"],
    )
    confidence = {"confirmed": "已确认", "suspected": "推测", "unknown": "未知"}
    yield (
        "原因与证据",
        [
            f"{confidence[c['confidence']]}：{c['description']}\n"
            f"来源：{', '.join(c['evidence_ids']) or '无'}"
            for c in content["causes"]
        ]
        or ["根因未确定"],
    )
    yield "处置与恢复核验", [content["response_and_recovery"]]
    yield "经验教训", content["lessons"] or ["无"]
    yield (
        "改进措施",
        [
            f"{v['action']}\n负责人：{v['owner']}；期限：{v['deadline']}"
            for v in content["improvements"]
        ]
        or ["无"],
    )
    yield "证据缺口", content["data_gaps"] or ["无已记录缺口"]
    incident = next(
        r["data"] for r in row.snapshot["records"] if r["id"] == "incident:" + str(row.incident_id)
    )
    alert_records = [r for r in row.snapshot["records"] if r["kind"] == "alert"]
    closure = incident.get("closure") or {}
    if closure.get("forced"):
        yield (
            "管理员强制关闭",
            [
                f"操作人：{closure['actor_username']}（{closure['actor_id']}）\n"
                f"关闭时间：{closure['closed_at']}\n关闭原因：{closure['reason']}\n"
                + (
                    "关闭时资源恢复已核验。"
                    if closure.get("recovery_verified")
                    else "关闭时资源恢复未通过核验；关闭是人工处理决定，不代表修复成功。"
                )
            ],
        )
    if not any("recovery_status" in r["data"] for r in alert_records):
        # Preserve the rendering of already approved legacy snapshots and their
        # frozen email attachments. Only newly captured evidence uses verification.
        yield (
            "事故时间",
            [
                f"开始时间：{incident['started_at']}\n恢复时间：{incident['recovered_at']}\n"
                f"关闭时间：{incident['closed_at']}\n以上为 UTC 带时区时间；"
                "开始至恢复表示告警持续区间，不等同于业务停机时间。"
            ],
        )
        return
    verified = bool(alert_records) and all(
        r["data"].get("recovery_status") == "verified" and r["data"].get("recovery_evidence_at")
        for r in alert_records
    )
    recovery_time = (
        incident["recovered_at"]
        if verified and incident["recovered_at"]
        else "未取得恢复核验；以关闭说明为准"
    )
    if not verified and incident["recovered_at"]:
        recovery_time += f"（历史恢复时间记录：{incident['recovered_at']}，未核验）"
    yield (
        "事故时间",
        [
            f"开始时间：{incident['started_at']}\n核验恢复时间：{recovery_time}\n"
            f"关闭时间：{incident['closed_at']}\n以上为 UTC 带时区时间；"
            "恢复依据新鲜正常证据；告警持续区间不等同于业务停机时间。"
        ],
    )


def metadata(row):
    return (
        f"事故 {row.incident_id}\n报告 {row.id} 版本 {row.revision}\n"
        f"审核人 {row.reviewed_by}\n审核时间 {row.reviewed_at.isoformat()}\n"
        f"证据快照时间 {row.snapshot['captured_at']}\n快照 SHA256 {row.snapshot_hash}"
    )


KINDS = {
    "incident": "事故",
    "alert": "告警",
    "timeline": "时间线",
    "analysis": "诊断",
    "approval": "审批",
    "execution": "执行",
    "audit": "审计",
    "repair_handoff": "修复交接",
}


def evidence_summary(row, content):
    from app.utils.canonical_json import digest

    counts = Counter(r["kind"] for r in row.snapshot["records"])
    overview = "冻结证据共 {} 条：{}。".format(
        sum(counts.values()),
        "、".join(f"{KINDS.get(kind, kind)} {count} 条" for kind, count in sorted(counts.items())),
    )
    overview += (
        "下表列出正文引用的证据及其来源，长字段仅显示摘要。"
        "完整时间线、指标采样、日志和诊断材料请在报告页面下载原始证据 JSON；"
        "使用快照 SHA256 校验完整性。"
    )
    refs = set(content["evidence_ids"])
    for cause in content["causes"]:
        refs.update(cause["evidence_ids"])
    sources = {}
    for record in row.snapshot["records"]:
        sources[record["id"]] = (
            KINDS.get(record["kind"], record["kind"]),
            record["occurred_at"],
            record["data"],
            record["content_hash"],
            "",
        )
        if record["kind"] == "analysis":
            for evidence in record["data"].get("evidence", []):
                if isinstance(evidence.get("id"), str):
                    sources[evidence["id"]] = (
                        "诊断工具 " + evidence.get("source", "未知"),
                        evidence.get("observed_at", record["occurred_at"]),
                        evidence.get("data"),
                        digest(evidence),
                        record["id"],
                    )
    index = []
    fields = {
        "title": "标题",
        "status": "状态",
        "severity": "级别",
        "recovery_status": "恢复核验状态",
        "event_type": "事件",
        "action": "动作",
        "metric": "指标",
        "start": "查询开始",
        "end": "查询结束",
        "inventory_status": "容器清单状态",
    }
    for identifier in sorted(refs):
        kind, occurred_at, data, content_hash, parent = sources[identifier]
        lines = [kind, f"证据时间：{occurred_at}"]
        if parent:
            lines.append(f"所属诊断：{parent}")
        if isinstance(data, dict):
            for field, label in fields.items():
                value = data.get(field)
                if isinstance(value, (str, int, float, bool)):
                    value = str(value)
                    lines.append(
                        f"{label}：{value[:180]}"
                        + ("…（详见原始证据）" if len(value) > 180 else "")
                    )
            if isinstance(data.get("series"), list):
                lines.append(f"指标序列：{len(data['series'])} 条；完整采样见原始证据")
        lines.append(f"内容 SHA256：{content_hash}")
        index.append((identifier, "\n".join(lines)))
    return overview, index


def pdf(row, content):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, TableStyle

    font_path = get_settings().report_font_file
    if not font_path.is_file():
        raise HTTPException(503, "Report font is unavailable")
    if "ReportChinese" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ReportChinese", str(font_path), subfontIndex=0))
    body = ParagraphStyle(
        "body", fontName="ReportChinese", fontSize=10.5, leading=16, wordWrap="CJK", spaceAfter=8
    )
    heading = ParagraphStyle(
        "heading", parent=body, fontSize=14, leading=20, spaceBefore=12, keepWithNext=True
    )
    title = ParagraphStyle("title", parent=heading, fontSize=18, leading=26)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=12)

    def p(text, style=body):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    story = [p(row.snapshot["title"] + "事故复盘报告", title), p(metadata(row), small)]
    for name, paragraphs in sections(row, content):
        story.append(p(name, heading))
        story.extend(p(t) for t in paragraphs)
    overview, index = evidence_summary(row, content)
    story.extend([p("证据摘要与来源", heading), p(overview)])
    table = [[p("证据编号", small), p("来源与摘要", small)]]
    table.extend([p(identifier, small), p(text, small)] for identifier, text in index)
    layout = LongTable(table, colWidths=[170, 337], repeatRows=1, hAlign="LEFT")
    layout.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9edf1")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9d9d9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(layout)
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=44,
        leftMargin=44,
        topMargin=40,
        bottomMargin=40,
        title=row.snapshot["title"] + "事故复盘报告",
        author="AI Operations Platform",
        invariant=1,
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("ReportChinese", 8)
        canvas.drawString(44, 22, "已审核事故复盘报告")
        canvas.drawRightString(A4[0] - 44, 22, f"第 {doc.page} 页")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def docx(row, content):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.7)
    section.left_margin = section.right_margin = Inches(0.7)
    for name in ("Normal", "Title", "Heading 1", "Heading 2"):
        style = document.styles[name]
        style.font.name = "WenQuanYi Zen Hei"
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "WenQuanYi Zen Hei")
        style.font.color.rgb = RGBColor(0, 0, 0)
    # python-docx's default Title style includes a blue paragraph border.
    title_properties = document.styles["Title"].element.get_or_add_pPr()
    for border in list(title_properties.findall(qn("w:pBdr"))):
        title_properties.remove(border)
    document.styles["Normal"].font.size = Pt(11)
    document.add_paragraph(row.snapshot["title"] + "事故复盘报告", "Title")
    document.add_paragraph(metadata(row))
    for name, paragraphs in sections(row, content):
        document.add_heading(name, 1)
        for text in paragraphs:
            document.add_paragraph(text)
    overview, index = evidence_summary(row, content)
    document.add_heading("证据摘要与来源", 1)
    document.add_paragraph(overview)
    table = document.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.autofit = False
    borders = OxmlElement("w:tblBorders")
    for name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement("w:" + name)
        for key, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
            border.set(qn("w:" + key), value)
        borders.append(border)
    table._tbl.tblPr.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for name in ("top", "left", "bottom", "right"):
        margin = OxmlElement("w:" + name)
        margin.set(qn("w:w"), "100")
        margin.set(qn("w:type"), "dxa")
        margins.append(margin)
    table._tbl.tblPr.append(margins)
    table.columns[0].width, table.columns[1].width = Inches(2.4), Inches(4.7)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "证据编号", "来源与摘要"
    table.rows[0].cells[0].width, table.rows[0].cells[1].width = Inches(2.4), Inches(4.7)
    for cell in table.rows[0].cells:
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), "E9EDF1")
        cell._tc.get_or_add_tcPr().append(shading)
    repeat = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for identifier, text in index:
        table_row = table.add_row()
        table_row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        cells = table_row.cells
        cells[0].width, cells[1].width = Inches(2.4), Inches(4.7)
        cells[0].text, cells[1].text = identifier, text
        for cell in cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(4)
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    footer = section.footer.paragraphs[0]
    footer.add_run("已审核事故复盘报告 第 ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    footer.add_run(" 页")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def export_report(row, content, format):
    if format == "json":
        from app.utils.canonical_json import digest, encoded

        return encoded(
            {
                "report_id": str(row.id),
                "incident_id": str(row.incident_id),
                "revision": row.revision,
                "reviewed_by": str(row.reviewed_by),
                "reviewed_at": row.reviewed_at.isoformat(),
                "content": content,
                "content_hash": digest(content),
                "snapshot": row.snapshot,
                "snapshot_hash": row.snapshot_hash,
            }
        )
    return pdf(row, content) if format == "pdf" else docx(row, content)
