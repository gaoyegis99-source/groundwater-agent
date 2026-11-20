"""Offline compiler tests; synthetic sources only, no project report fixtures."""

from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from platform_knowledge_next.docx_package import (
    M, W, compile_packages, extract_docx, table_rows, visible_text,
)
from platform_knowledge_next.models import KnowledgePackageV1


class DocxPackageTests(unittest.TestCase):
    def compile(self, blocks, **changes):
        args = dict(project="test", prefix="report", title="测试报告", publisher="测试",
                    version="1", review_after="2027-01-01", accessed_at="2026-11-20")
        args.update(changes)
        return compile_packages({"blocks": blocks, "file_name": "test.docx", "sha256": "sha256:" + "0" * 64}, **args)

    def paragraph(self, index, text):
        return {"id": f"b{index:04d}", "type": "paragraph", "text": text, "visual_count": 0}

    def test_merged_cells_retain_labels(self):
        element = ET.fromstring(f'''<w:tbl xmlns:w="{W}">
          <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>水质</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>单位</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p><w:r><w:t>mg/L</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>''')
        self.assertEqual(table_rows(element), [["水质", "水质", "单位"], ["水质", "水质", "mg/L"]])

    def test_equations_not_flattened_or_deleted_text_revived(self):
        element = ET.fromstring(f'''<w:p xmlns:w="{W}" xmlns:m="{M}"><w:del><w:r><w:t>旧值</w:t></w:r></w:del><w:ins><w:r><w:t>新值</w:t></w:r></w:ins><m:oMath><m:t>x/y</m:t></m:oMath></w:p>''')
        self.assertEqual(visible_text(element), "新值【公式见原件；未转换】")

    def test_large_table_dependencies_survive_document_split(self):
        rows = [["指标", "单位"], *[[str(i), "mg/L"] for i in range(150)]]
        packages = self.compile([{"id": "b0001", "type": "table", "rows": rows}])
        row_units = []
        for package in packages:
            validated = KnowledgePackageV1.model_validate(package)
            for doc in validated.documents:
                ids = {u.unit_id for u in doc.units}
                self.assertLessEqual(len(ids), 64)
                for unit in doc.units:
                    self.assertTrue(set(unit.required_units) <= ids)
                    if "row" in unit.locator:
                        row_units.append(unit)
                        self.assertEqual(set(unit.required_units), {"scope", "b0001.header"})
        self.assertEqual(len(row_units), len(rows))

    def test_long_text_is_lossless_and_bounded(self):
        original = "示例。" * 4100
        packages = self.compile([self.paragraph(1, original)])
        units = [u for p in packages for d in p["documents"] for u in d["units"] if u["unitId"] != "scope"]
        self.assertEqual("".join(u["content"] for u in units), original)
        self.assertTrue(all(len(u["content"]) <= 4000 for u in units))

    def test_table_notes_are_required_context(self):
        packages = self.compile([
            self.paragraph(1, "表5-1 技术适用性"),
            self.paragraph(2, "注：A表示孔隙介质"),
            {"id": "b0003", "type": "table", "rows": [["技术", "A"], ["修复", "※"], ["注：※部分适用", "注：※部分适用"]]},
        ])
        units = packages[0]["documents"][0]["units"]
        header = next(u for u in units if u["unitId"] == "b0003.header")
        self.assertIn("注：A表示孔隙介质", header["content"])
        self.assertIn("注：※部分适用", header["content"])
        data = next(u for u in units if u["unitId"] == "b0003.r2.p1")
        self.assertIn(header["unitId"], data["requiredUnits"])

    def test_many_sections_split_atomic_packages(self):
        packages = self.compile([self.paragraph(i, f"{i}）章节") for i in range(1, 140)])
        self.assertEqual([len(p["documents"]) for p in packages], [128, 11])
        for package in packages:
            KnowledgePackageV1.model_validate(package)

    def test_idempotency_includes_project_and_semantics(self):
        blocks = [self.paragraph(1, "原文")]
        a = self.compile(blocks)
        self.assertEqual(a, self.compile(blocks))
        self.assertNotEqual(a[0]["idempotencyKey"], self.compile(blocks, project="other")[0]["idempotencyKey"])
        self.assertNotEqual(a[0]["idempotencyKey"], self.compile(blocks, version="2")[0]["idempotencyKey"])

    def test_body_locator_and_visual_only_paragraph(self):
        xml = f'''<w:document xmlns:w="{W}"><w:body><w:p/><w:p><w:r><w:t>测试</w:t></w:r></w:p><w:p><w:r><w:drawing/></w:r></w:p><w:sectPr/></w:body></w:document>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.docx"
            with ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
                archive.writestr("word/comments.xml", "<comments/>")
            extracted = extract_docx(path)
        self.assertEqual([b["id"] for b in extracted["blocks"]], ["b0002", "b0003"])
        self.assertEqual(extracted["blocks"][1]["visual_count"], 1)
        self.assertEqual(extracted["excluded_parts"], ["word/comments.xml"])

    def test_invalid_identity_and_oversize_header_fail(self):
        with self.assertRaisesRegex(ValueError, "INVALID_PROJECT"):
            self.compile([self.paragraph(1, "x")], project="../x")
        with self.assertRaisesRegex(ValueError, "TABLE_HEADER_TOO_LARGE"):
            self.compile([{"id": "b0001", "type": "table", "rows": [["长" * 4000]]}])
        with self.assertRaisesRegex(ValueError, "NO_READABLE_DOCX_CONTENT"):
            self.compile([])
        with self.assertRaisesRegex(ValueError, "INVALID_SOURCE_METADATA_LENGTH"):
            self.compile([self.paragraph(1, "x")], title="")

    def test_utf8_package_limit_is_respected(self):
        import json

        packages = self.compile([self.paragraph(i, f"{i}）" + "水" * 180000) for i in range(1, 35)])
        self.assertGreater(len(packages), 1)
        for package in packages:
            self.assertLessEqual(len(json.dumps(package, ensure_ascii=False, indent=2).encode()), 16 * 1024 * 1024)
            KnowledgePackageV1.model_validate(package)


if __name__ == "__main__":
    unittest.main()
