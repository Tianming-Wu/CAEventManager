#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import a registration roster into a running lottery instance.

    # from the Excel file the club exported (needs openpyxl)
    python tools/import_roster.py --xlsx ~/Downloads/报名.xlsx --base http://127.0.0.1:8931

    # from a CSV already in 学号,姓名,班级 form (works anywhere)
    python tools/import_roster.py --csv roster.csv --base http://127.0.0.1:8931

    # just check the file, change nothing
    python tools/import_roster.py --xlsx 报名.xlsx --dry-run

The spreadsheet is read in place; nothing personal is written into the project.
Duplicated registrations (same id, same name) are dropped, and anything that
needs a human decision is listed instead of being silently guessed at.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def norm(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", "", str(value)).translate(FULLWIDTH)


SECTION_WORDS = (
    (("志愿者", "志愿"), "volunteer"),
    (("工作人员", "干事", "管理", "主办", "协办"), "staff"),
    (("参加人员", "参与人员", "参与者", "人员", "名单"), "participant"),
)


def section_role(text):
    """A merged heading like 工作人员 / 志愿者 starts a new block."""
    raw = (text or "").strip()
    if not raw or len(raw) > 8:
        return None
    for words, role in SECTION_WORDS:
        if any(w in raw for w in words):
            return role
    return None


def role_value(text):
    """Map a role cell onto participant/staff/volunteer.

    Accepts what a human writes (工作人员 / 志愿者 / 参加人员) and what the tool
    itself writes in its CSV round-trip (staff / volunteer / participant).
    """
    raw = (text or "").strip().lower()
    if raw in ("participant", "staff", "volunteer"):
        return raw
    return section_role(raw) or "participant"


def from_xlsx(path):
    """Read name / id / group / role out of a club spreadsheet.

    Handles the two shapes this club actually uses: one flat table with an
    identity column, and one table split into merged-cell sections
    (参加人员 / 工作人员 / 志愿者), where the section carries the role.
    """
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("reading .xlsx needs openpyxl; export a CSV instead")

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))

    header = None
    col_name = col_sid = col_grp = col_role = None
    role = "participant"
    body = []

    for i, row in enumerate(rows):
        cells = [norm(c) for c in row]
        filled = [c for c in cells if c]

        # 1. a section heading: one label per row, everything else empty
        if len(filled) == 1 and not (("姓名" in cells) or ("学号" in cells)):
            maybe = section_role(filled[0])
            if maybe:
                role = maybe
                if not body and header is None and maybe != "participant":
                    role = maybe
                continue

        # 2. a header row
        if any("姓名" == c for c in cells) and any("学号" == c for c in cells):
            header = cells
            col_name = cells.index("姓名")
            col_sid = cells.index("学号")
            col_grp = col_role = None
            for j, c in enumerate(cells):
                if not c or j in (col_name, col_sid):
                    continue
                if any(k in c for k in ("身份", "类别", "角色")):
                    col_role = j
                elif "签名" not in c and "备注" not in c and col_grp is None:
                    col_grp = j
            continue

        if header is None:
            continue

        # 3. a data row
        def cell(idx):
            return norm(row[idx]) if idx is not None and idx < len(row) else ""

        name, sid = cell(col_name), cell(col_sid)
        if not name and not sid:
            continue
        row_role = role_value(cell(col_role)) if col_role is not None else role
        body.append((i + 1, name, sid, cell(col_grp), row_role))
    return body


def from_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return []
    header = [norm(c) for c in rows[0]]
    has_header = "学号" in header and "姓名" in header
    if has_header:
        col_sid, col_name = header.index("学号"), header.index("姓名")
        col_grp = header.index("班级") if "班级" in header else None
        col_role = next((j for j, c in enumerate(header)
                         if any(k in c for k in ("身份", "类别", "角色"))), None)
    else:
        col_sid, col_name, col_grp, col_role = 0, 1, (2 if len(header) > 2 else None), None
    out = []
    for i, row in enumerate(rows[1:] if has_header else rows, start=1):
        def cell(idx):
            return norm(row[idx]) if idx is not None and idx < len(row) else ""
        out.append((i, cell(col_name), cell(col_sid), cell(col_grp),
                    role_value(cell(col_role))))
    return out


def clean(body):
    records, problems, seen_sid, seen_name = [], [], {}, {}
    for lineno, name, sid, grp, role in body:
        if not name and not sid:
            continue
        if name in ("备注", "说明", "合计") and not sid:
            continue
        if not sid:
            problems.append("行 %d: %s 没有学号，已跳过" % (lineno, name or "(空)"))
            continue
        if not name:
            problems.append("行 %d: 学号 %s 没有姓名，已跳过" % (lineno, sid))
            continue
        # 本科 10 位、研究生 9 位并存，别拿一个固定长度去卡；只挡明显不像学号的值
        if not sid.isdigit():
            problems.append("行 %d: %s 的学号 %r 不是纯数字（仍然导入）" % (lineno, name, sid))
        elif not (6 <= len(sid) <= 16):
            problems.append("行 %d: %s 的学号 %s 是 %d 位，不太像学号 —— 请核对"
                            % (lineno, name, sid, len(sid)))
        if sid in seen_sid:
            problems.append("行 %d: %s 与行 %d 学号重复，视为重复报名，已跳过"
                            % (lineno, name, seen_sid[sid]))
            continue
        seen_sid[sid] = lineno
        seen_name.setdefault(name, lineno)
        records.append({"sid": sid, "name": name, "grp": grp, "role": role})
    return records, problems


def post(base, path, body, opener, headers=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "X-Requested-With": "lottery"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method="POST")
    with opener.open(req, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--xlsx")
    src.add_argument("--csv")
    ap.add_argument("--base", default="http://127.0.0.1:8931")
    ap.add_argument("--secret", help="access id; read from config.json when omitted")
    ap.add_argument("--password")
    ap.add_argument("--password-file")
    ap.add_argument("--user", default="laffey")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write-csv", help="also save the cleaned roster here")
    args = ap.parse_args()

    body = from_xlsx(args.xlsx) if args.xlsx else from_csv(args.csv)
    records, problems = clean(body)

    by_role = {}
    for r in records:
        by_role[r["role"]] = by_role.get(r["role"], 0) + 1
    labels = {"participant": "参加人员", "staff": "工作人员", "volunteer": "志愿者"}
    split = "，".join("%s %d" % (labels.get(k, k), v) for k, v in sorted(by_role.items()))
    print("解析到 %d 条有效记录%s" % (len(records), ("（" + split + "）") if split else ""))
    if problems:
        print("需要人看的 (%d):" % len(problems))
        for p in problems:
            print("  -", p)
    if args.write_csv:
        with open(args.write_csv, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["学号", "姓名", "班级", "身份"])
            for r in records:
                writer.writerow([r["sid"], r["name"], r["grp"], r["role"]])
        print("已写出:", args.write_csv)
    if args.dry_run:
        print("(dry run，没有导入)")
        return 0

    secret = args.secret
    if not secret:
        try:
            import _baseurl
            secret = _baseurl.secret()
        except Exception:
            secret = ""
    if not secret:
        raise SystemExit("need --secret or a local config.json")

    password = args.password
    if not password and args.password_file:
        with open(args.password_file, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("password:"):
                    password = line.split(":", 1)[1].strip()
    if not password:
        raise SystemExit("need --password or --password-file")

    base = args.base.rstrip("/") + "/" + secret
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    post(base, "/api/login", {"user": args.user, "pass": password,
                              "cookie_path": "/%s/" % secret}, opener)

    text = "学号,姓名,班级,身份\n" + "\n".join(
        "%s,%s,%s,%s" % (r["sid"], r["name"], r["grp"], r["role"]) for r in records)
    res = post(base, "/api/admin/import", {"text": text}, opener)
    r = res["result"]
    print()
    print("导入完成：新增 %d，更新 %d，跳过 %d" % (r["added"], r["updated"], r["skipped"]))
    for e in r["errors"]:
        print("  !", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
