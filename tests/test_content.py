import unittest
from datetime import datetime, timezone
from x_project.content import Draft, Source, ContentError, validate_draft, canonical_url, weighted_length

NOW = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)

def draft(body="模型更新值得关注，但落地还要看成本和可靠性。", url="https://example.org/release"):
    return Draft(body, "模型发布与落地", [Source(url, "Release", "article", "2026-11-13T01:00:00Z", "Release evidence from the original announcement.")])

class ContentTest(unittest.TestCase):
    def test_chinese_budget_and_citation_rendering(self):
        d = validate_draft(draft(), NOW)
        self.assertIn("\nhttps://example.org/release", d.text)
        self.assertLessEqual(weighted_length(d.text), 280)
        with self.assertRaises(ContentError): validate_draft(draft("模" * 140), NOW)

    def test_rejects_stale_future_or_unsafe_sources(self):
        for url, date in (("http://127.0.0.1/private", "2026-11-13T01:00:00Z"), ("file:///etc/passwd", "2026-11-13T01:00:00Z"), ("https://example.org/release", "2026-01-01T00:00:00Z"), ("https://example.org/release", "2027-01-01T00:00:00Z")):
            d = draft(url=url); d.sources[0].published_at = date
            with self.assertRaises(ContentError): validate_draft(d, NOW)

    def test_video_with_only_a_title_cannot_support_claimed_video_summary(self):
        d = draft(); d.sources[0].kind = "video_title"
        with self.assertRaises(ContentError): validate_draft(d, NOW)

    def test_tracking_parameters_do_not_defeat_source_deduplication(self):
        self.assertEqual(canonical_url("https://twitter.com/person/status/123?utm_source=x&s=20"), "https://x.com/person/status/123")
        self.assertEqual(canonical_url("https://youtu.be/abcdef12345?si=secret"), "https://www.youtube.com/watch?v=abcdef12345")

    def test_model_cannot_embed_additional_unverified_links(self):
        with self.assertRaises(ContentError): validate_draft(draft("观点 https://evil.example/unverified"), NOW)

    def test_source_with_only_a_published_date_is_accepted_without_inventing_a_time(self):
        d = draft(); d.sources[0].published_at = "2026-11-12"
        self.assertEqual(validate_draft(d, NOW).sources[0].published_at, "2026-11-12")
