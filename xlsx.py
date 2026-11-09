# -*- coding: utf-8 -*-
"""最小的 xlsx 读写：一份表、一行表头、剩下全是文本单元格。

不引第三方库，所以只实现用得上的那一点：
- 写：一个工作表，所有单元格都是 inlineStr（文本）。学号必须是文本，否则 20250001
  进了 Excel 会变成数字、前导零也会掉。
- 读：第一个工作表，认得 inlineStr / 共享字符串 / 数字 / 布尔，日期当数字处理。
  合并单元格只取左上角那一格，空行跳过。

这两件事的边界写在 `doc/reference/api.md`；超出这个范围的文件（多表、公式、图表）
不保证读得对，也不该假装能读。
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CONTENT_TYPE = ("application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet")

MAX_ROWS = 20000
MAX_COLS = 60


def _col_name(index):
    """0 → A, 25 → Z, 26 → AA。"""
    out = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        out = chr(65 + rest) + out
    return out


def _xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _sheet_xml(rows):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="%s">' % NS_MAIN,
           "<sheetData>"]
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            text = "" if value is None else str(value)
            if text == "":
                continue
            cells.append('<c r="%s%d" t="inlineStr"><is><t xml:space="preserve">%s'
                         "</t></is></c>" % (_col_name(c), r, _xml_escape(text)))
        out.append('<row r="%d">%s</row>' % (r, "".join(cells)))
    out.append("</sheetData></worksheet>")
    return "".join(out)


def build(rows, sheet_name="Sheet1"):
    """把二维表写成 xlsx 字节。除表头外不写别的说明，第一行就是列名。"""
    rows = [list(r) for r in (rows or [])]
    if not rows:
        rows = [[""]]
    sheet = _sheet_xml(rows)
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package'
            '.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="%s" xmlns:r="%s"><sheets>'
            '<sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>'
            % (NS_MAIN, NS_REL, _xml_escape(sheet_name or "Sheet1")),
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        "xl/worksheets/sheet1.xml": sheet,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def _shared_strings(zf):
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    for item in root.findall("{%s}si" % NS_MAIN):
        out.append("".join(node.text or "" for node in item.iter("{%s}t" % NS_MAIN)))
    return out


def _first_sheet_path(zf):
    """第一个工作表：优先按 workbook 的清单找，找不到就退回 sheet1。"""
    names = zf.namelist()
    fallback = None
    for name in names:
        if re.match(r"xl/worksheets/sheet\d+\.xml$", name):
            if name.endswith("sheet1.xml"):
                return name
            fallback = fallback or name
    if fallback:
        return fallback
    return None


def _cell_text(cell, shared):
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find("{%s}is" % NS_MAIN)
        return "" if node is None else "".join(
            part.text or "" for part in node.iter("{%s}t" % NS_MAIN))
    value = cell.find("{%s}v" % NS_MAIN)
    text = "" if value is None or value.text is None else value.text
    if kind == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if text == "1" else "FALSE"
    if kind == "n" and text:
        # 20250001.0 这种去掉小数尾巴，别把学号写成 20250001.0
        try:
            number = float(text)
        except ValueError:
            return text
        return str(int(number)) if number.is_integer() else text
    return text


def _split_ref(ref):
    """B7 → (row=7, col=1)。"""
    match = re.match(r"([A-Z]+)(\d+)", (ref or "").upper())
    if not match:
        return None, None
    col = 0
    for ch in match.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(match.group(2)), col - 1


def parse(data):
    """把 xlsx 字节读成二维文本表：只取第一个工作表，空行跳过。"""
    if not data:
        raise ValueError("文件是空的")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("这不是一个 xlsx 文件")
    with zf:
        path = _first_sheet_path(zf)
        if not path:
            raise ValueError("文件里没有工作表")
        shared = _shared_strings(zf)
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
            raise ValueError("工作表读不出来")
        rows = []
        for row in root.iter("{%s}row" % NS_MAIN):
            index = int(row.get("r") or (len(rows) + 1))
            while len(rows) < index - 1:      # 中间空掉的行保留成空行，位置才对得上
                rows.append([])
            cells = {}
            for cell in row.findall("{%s}c" % NS_MAIN):
                _r, col = _split_ref(cell.get("r"))
                if col is None or col >= MAX_COLS:
                    continue
                cells[col] = _cell_text(cell, shared)
            width = (max(cells) + 1) if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
            if len(rows) >= MAX_ROWS:
                break
        return [r for r in rows if any((c or "").strip() for c in r)]
