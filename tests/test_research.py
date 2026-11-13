import unittest
from unittest.mock import patch
from x_project.content import ContentError
from x_project.research import SourceVerifier
from test_content import draft


class ResearchTest(unittest.TestCase):
    def test_old_x_status_id_cannot_be_relabelled_with_todays_date(self):
        import json
        from datetime import datetime, timezone
        old = int((datetime(2020,1,1,tzinfo=timezone.utc).timestamp()*1000-1288834974657)) << 22
        d = draft(url=f"https://x.com/person/status/{old}"); d.sources[0].kind = "post"
        with patch("x_project.research.fetch", return_value=json.dumps({"html": "<p>Release evidence from the original announcement.</p>"})):
            with self.assertRaises(ContentError): SourceVerifier().verify(d)

    def test_video_publication_date_must_be_verified_independently(self):
        d = draft(url="https://www.youtube.com/watch?v=abcdef12345"); d.sources[0].kind = "video_description"
        page = '<html><script>{"publishDate":"2020-01-01","shortDescription":"Release evidence from the original announcement."}</script></html>'
        with patch("x_project.research.fetch", return_value=page):
            with self.assertRaises(ContentError): SourceVerifier().verify(d)

    def test_invented_evidence_is_rejected_even_when_the_page_exists(self):
        d = draft()
        with patch("x_project.research.fetch", return_value="<html><body>Different release details</body></html>"):
            with self.assertRaises(ContentError): SourceVerifier().verify(d)

    def test_page_publication_metadata_overrides_model_claimed_date(self):
        d = draft()
        page = '<html><meta property="article:published_time" content="2020-01-01T00:00:00Z"><body>Release evidence from the original announcement.</body></html>'
        with patch("x_project.research.fetch", return_value=page):
            with self.assertRaises(ContentError): SourceVerifier().verify(d)

    def test_visible_original_evidence_is_accepted_with_matching_source_date(self):
        d = draft()
        page = '<html><meta property="article:published_time" content="2026-11-13T01:00:00Z"><body>Release evidence from the original announcement.</body></html>'
        with patch("x_project.research.fetch", return_value=page):
            self.assertIs(SourceVerifier().verify(d), d)
