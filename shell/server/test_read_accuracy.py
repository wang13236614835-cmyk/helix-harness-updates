"""3.1：文件读取准确性（顺序、表格、日期时间、编码、分隔符、任务表头）。"""
import datetime as dt, io, tempfile, unittest
from pathlib import Path
import projects as P


class ReadAccuracy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_semicolon_and_utf16(self):
        p = self.root / "a.csv"; p.write_text("名称;数值\n甲;1,5\n乙;2\n", encoding="utf-8")
        r = P.preview(p); self.assertEqual(r["sheets"][0]["rows"][1], ["甲", "1,5"])
        q = self.root / "b.csv"; q.write_bytes("id\tname\n1\t苯\n".encode("utf-16"))
        r = P.preview(q); self.assertEqual(r["sheets"][0]["rows"][1], ["1", "苯"])

    def test_trailing_empty_cells_trimmed(self):
        p = self.root / "c.csv"; p.write_text("a,b,,\n1,2,,\n,,,\n", encoding="utf-8")
        self.assertEqual(P.preview(p)["sheets"][0]["rows"], [["a", "b"], ["1", "2"]])

    def test_xlsx_values_datetime_and_numbers(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl")
        p = self.root / "d.xlsx"; w = Workbook(); s = w.active
        s.append(["时间", "数", "整数"]); s.append([dt.datetime(2026, 10, 18, 14, 30), 0.1 + 0.2, 3.0]); s.append([dt.date(2026, 1, 2), None, "=B2*2"])
        w.save(p)
        rows = P.preview(p)["sheets"][0]["rows"]
        self.assertEqual(rows[1][0], "2026-10-18 14:30:00"); self.assertEqual(rows[1][2], "3"); self.assertEqual(rows[1][1], "0.3")
        self.assertEqual(rows[2][0], "2026-01-02"); self.assertEqual(rows[2][2], "=B2*2")  # 没有缓存值时才显示公式

    def test_docx_order_tables_tabs(self):
        try:
            from docx import Document
        except ImportError:
            self.skipTest("python-docx")
        d = Document(); d.add_paragraph("第一段"); t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text = "表头A"; t.cell(0, 1).text = "表头B"; t.cell(1, 0).text = "x"; t.cell(1, 1).text = "y"
        para = d.add_paragraph("左"); para.add_run().add_tab(); para.add_run("右")
        p = self.root / "e.docx"; d.save(p)
        text = P.preview(p)["text"]
        self.assertLess(text.index("第一段"), text.index("表头A | 表头B")); self.assertLess(text.index("x | y"), text.index("左\t右"))

    def test_parse_tasks_header_variants(self):
        data = "说明：本周任务\n编号, 周次 ,交付物（deliverable）,负责人,截止(日期)\nT1,1,写报告,甲,2026-10-20\n,,,,\n".encode("utf-8")
        r = P.parse_tasks("t.csv", data)
        self.assertEqual(r["rows"], [{"task_id": "T1", "week": "1", "deliverable": "写报告", "owner": "甲", "end": "2026-10-20"}])

    def test_parse_tasks_picks_right_sheet(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl")
        w = Workbook(); w.active.title = "封面"; w.active.append(["项目计划"])
        s = w.create_sheet("任务"); s.append(["task_id", "deliverable"]); s.append(["A1", "建模"])
        buf = io.BytesIO(); w.save(buf)
        self.assertEqual(P.parse_tasks("p.xlsx", buf.getvalue())["rows"], [{"task_id": "A1", "deliverable": "建模"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
