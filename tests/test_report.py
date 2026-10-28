"""Тесты логики сведения — без обращений к сети.

Проверяем: нормализацию ключа, объединение расхода и заявок, подсчёт квала,
цену заявки/квала, «тратят деньги без заявок» и заявки без ключа.
"""

import unittest

from ad_analytics.report import build_report, normalize_key


class NormalizeKeyTest(unittest.TestCase):
    def test_case_and_spaces(self):
        self.assertEqual(normalize_key("  Видео_Риски  V2 "), "видео_риски v2")

    def test_none(self):
        self.assertEqual(normalize_key(None), "")


class BuildReportTest(unittest.TestCase):
    def setUp(self):
        # Два объявления в Meta: у одного есть заявки, второе только тратит.
        self.insights = [
            {"ad_name": "video_a_v1", "campaign_name": "ОСК_лиды", "spend": "100", "clicks": "50"},
            {"ad_name": "video_a_v1", "campaign_name": "ОСК_лиды", "spend": "50", "clicks": "25"},
            {"ad_name": "banner_b", "campaign_name": "ОСК_лиды", "spend": "200", "clicks": "10"},
        ]
        # Заявки Bitrix: utm_content — ключ связывания; STATUS_ID — качество.
        self.leads = [
            {"ID": "1", "UTM_CONTENT": "Video_A_v1", "STATUS_ID": "CONVERTED"},
            {"ID": "2", "UTM_CONTENT": "video_a_v1 ", "STATUS_ID": "NEW"},
            {"ID": "3", "UTM_CONTENT": "video_a_v1", "STATUS_ID": "CONVERTED"},
            {"ID": "4", "UTM_CONTENT": "", "STATUS_ID": "NEW"},  # без ключа
        ]
        self.report = build_report(
            self.insights,
            self.leads,
            key_fields=["UTM_CONTENT"],
            qualified_field="STATUS_ID",
            qualified_values={"CONVERTED"},
        )

    def test_spend_aggregated_and_key_normalized(self):
        row = next(r for r in self.report["rows"] if r["key"] == "video_a_v1")
        self.assertEqual(row["spend"], 150.0)  # 100 + 50
        self.assertEqual(row["clicks"], 75)
        self.assertEqual(row["leads"], 3)       # три лида с этим ключом (регистр/пробелы схлопнуты)
        self.assertEqual(row["qualified"], 2)   # два CONVERTED

    def test_cost_per_lead_and_qualified(self):
        row = next(r for r in self.report["rows"] if r["key"] == "video_a_v1")
        self.assertEqual(row["cost_per_lead"], 50.0)        # 150 / 3
        self.assertEqual(row["cost_per_qualified"], 75.0)   # 150 / 2
        self.assertTrue(row["matched"])

    def test_wasted_creative(self):
        self.assertEqual(len(self.report["wasted"]), 1)
        self.assertEqual(self.report["wasted"][0]["ad_name"], "banner_b")
        self.assertEqual(self.report["wasted"][0]["leads"], 0)

    def test_unmatched_leads_counted(self):
        self.assertEqual(self.report["unmatched_leads"], 1)

    def test_totals(self):
        t = self.report["totals"]
        self.assertEqual(t["spend"], 350.0)
        self.assertEqual(t["leads"], 4)        # включая заявку без ключа
        self.assertEqual(t["qualified"], 2)


class DealSemanticTest(unittest.TestCase):
    """Сделки: «квалифицирован» = выигранная сделка (STAGE_SEMANTIC_ID = S)."""

    def test_won_deals_counted_as_qualified(self):
        insights = [{"ad_name": "video_a", "spend": "300", "clicks": "20"}]
        deals = [
            {"UTM_CONTENT": "video_a", "STAGE_SEMANTIC_ID": "S"},  # выиграна
            {"UTM_CONTENT": "video_a", "STAGE_SEMANTIC_ID": "P"},  # в работе
            {"UTM_CONTENT": "video_a", "STAGE_SEMANTIC_ID": "F"},  # проиграна
        ]
        report = build_report(
            insights, deals,
            key_fields=["UTM_CONTENT"],
            qualified_field="STAGE_SEMANTIC_ID",
            qualified_values={"S"},
        )
        row = next(r for r in report["rows"] if r["key"] == "video_a")
        self.assertEqual(row["leads"], 3)
        self.assertEqual(row["qualified"], 1)
        self.assertEqual(row["cost_per_lead"], 100.0)       # 300 / 3
        self.assertEqual(row["cost_per_qualified"], 300.0)  # 300 / 1


if __name__ == "__main__":
    unittest.main()
