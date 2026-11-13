import sys
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


    def test_github_relative_time_is_an_independent_publication_date(self):
        d=draft(url="https://github.com/org/project/releases/tag/v1")
        page='<relative-time datetime="2026-11-13T01:00:00Z">13 Nov</relative-time><p>Release evidence from the original announcement.</p>'
        with patch("x_project.research.fetch",return_value=page):self.assertIs(SourceVerifier().verify(d),d)

    def test_press_release_label_supports_visible_date_but_not_copyright_year(self):
        d=draft();page='<p>For immediate release November 13, 2026</p><p>Release evidence from the original announcement.</p>'
        with patch("x_project.research.fetch",return_value=page):self.assertIs(SourceVerifier().verify(d),d)
        with patch("x_project.research.fetch",return_value='<p>Copyright November 13, 2026</p>'):
            with self.assertRaises(ContentError):SourceVerifier().verify(d)

    def test_article_request_keeps_original_trailing_slash_while_deduping_canonically(self):
        from x_project.content import validate_draft,canonical_url
        d=draft(url="https://example.org/release/")
        def page(url):
            if url.endswith("/"):return '<meta property="article:published_time" content="2026-11-13"><p>Release evidence from the original announcement.</p>'
            return '<html>Different server response</html>'
        with patch("x_project.research.fetch",side_effect=page):
            validate_draft(d);self.assertIs(SourceVerifier().verify(d),d)
        self.assertEqual(canonical_url(d.sources[0].url),canonical_url("https://example.org/release"))


    def test_empty_metadata_attributes_cannot_crash_source_verification(self):
        d=draft()
        page='<meta property="og:title" content><meta property content="ignored"><meta property="article:published_time" content="2026-11-13T01:00:00Z"><p>Release evidence from the original announcement.</p>'
        with patch("x_project.research.fetch",return_value=page):
            self.assertIs(SourceVerifier().verify(d),d)


class ResearchRuntimeTest(unittest.TestCase):
    def test_custom_codex_installation_runs_inside_real_isolation(self):
        import json
        import tempfile
        from pathlib import Path
        from x_project.research import CodexResearcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / 'custom-tools' / 'codex'
            executable.parent.mkdir()
            (executable.parent / 'phone.env').write_text('synthetic-private-value')
            executable.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
try:
    Path(__file__).with_name('phone.env').read_text()
except PermissionError:
    pass
else:
    raise SystemExit(5)
assert 'X_PHONE_PIN' not in __import__('os').environ
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('{"items":[]}')
print(json.dumps({'type': 'item.completed', 'item': {'type': 'web_search', 'action': {'type': 'search'}}}))
''')
            executable.chmod(0o700)
            self.assertEqual(CodexResearcher(executable=str(executable), audit_dir=root / 'audit').generate(1, [], '中文'), [])

    def test_auth_directory_cannot_contain_alternate_configuration_root(self):
        import os
        import tempfile
        from pathlib import Path
        from x_project.research import CodexResearcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'alternate-config'; project.mkdir()
            with patch.dict(os.environ, {'CODEX_HOME': str(root)}):
                with self.assertRaisesRegex(ContentError, '认证目录'):
                    CodexResearcher(project_root=project).generate(1, [], '中文')

    def test_npm_style_installation_can_read_its_package_and_native_resources(self):
        import tempfile
        from pathlib import Path
        from x_project.research import CodexResearcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'node_modules' / '@openai' / 'codex'
            (package / 'bin').mkdir(parents=True)
            (package / 'package.json').write_text('{"name":"@openai/codex"}')
            native = package.parent / 'codex-linux-x64'
            native.mkdir(); (native / 'native-resource').write_text('native-code')
            executable = package / 'bin' / 'codex'
            executable.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
package = Path(__file__).resolve().parent.parent
assert json.loads((package / 'package.json').read_text())['name'] == '@openai/codex'
assert (package.parent / 'codex-linux-x64' / 'native-resource').read_text() == 'native-code'
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('{"items":[]}')
print(json.dumps({'type': 'item.completed', 'item': {'type': 'web_search', 'action': {'type': 'search'}}}))
''')
            executable.chmod(0o700)
            self.assertEqual(CodexResearcher(executable=str(executable)).generate(1, [], '中文'), [])

    def test_research_process_does_not_inherit_phone_configuration_or_credentials(self):
        import os
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from x_project.research import CodexResearcher
        phone = {'X_PHONE_PIN': '123456', 'X_PHONE_PATTERN': '12589', 'X_ADB_PAIR_CODE': '654321', 'X_ADB_HOST': '192.0.2.5', 'pwd': '123456'}
        captured = {}
        def run(args, **kwargs):
            captured.update(kwargs['env'])
            Path(args[args.index('--output-last-message') + 1]).write_text('{"items":[]}')
            return SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'item.completed', 'item': {'type': 'web_search', 'action': {'type': 'search'}}}), stderr='')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, phone), patch('x_project.research.subprocess.run', side_effect=run):
            CodexResearcher(executable=sys.executable, audit_dir=directory).generate(1, [], '中文')
        self.assertTrue(set(phone).isdisjoint(captured))

    def invoke(self, events, stderr="", returncode=0, items=None):
        import json,tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from x_project.research import CodexResearcher
        with tempfile.TemporaryDirectory() as directory:
            def run(args,**kw):
                self.command=args
                output=Path(args[args.index("--output-last-message")+1])
                output.write_text(json.dumps({"items":items or []}))
                return SimpleNamespace(returncode=returncode,stdout="\n".join(json.dumps(e) for e in events),stderr=stderr)
            with patch("x_project.research.subprocess.run",side_effect=run):
                result=CodexResearcher(executable=sys.executable,audit_dir=directory).generate(1,[],"中文")
            report=Path(directory)/"last-research.json"
            self.report=json.loads(report.read_text()) if report.exists() else {}
            return result

    def test_search_runtime_is_available_and_real_search_events_are_recorded(self):
        events=[{"type":"item.completed","item":{"type":"web_search","action":{"type":"search"}}}]
        self.assertEqual(self.invoke(events),[])
        self.assertEqual(self.report.get("searches"),1)
        self.assertIn("--json",self.command)
        self.assertIn(["--enable","code_mode_host"],[self.command[i:i+2] for i in range(len(self.command)-1)])
        self.assertNotIn(["--disable","code_mode_host"],[self.command[i:i+2] for i in range(len(self.command)-1)])

    def test_empty_output_without_actual_search_is_a_runtime_failure(self):
        with self.assertRaisesRegex(ContentError,"实际.*检索"):
            self.invoke([])

    def test_router_failure_is_not_reported_as_no_news(self):
        with self.assertRaisesRegex(ContentError,"工具"):
            self.invoke([],"ERROR codex_core::tools::router: error=code-mode host is disabled")

    def test_nonzero_after_completed_search_records_failure_not_searched(self):
        import tempfile,json
        from pathlib import Path
        from types import SimpleNamespace
        from x_project.research import CodexResearcher
        event=json.dumps({"type":"item.completed","item":{"type":"web_search","action":{"type":"search"}}})
        with tempfile.TemporaryDirectory() as d:
            with patch("x_project.research.subprocess.run",return_value=SimpleNamespace(returncode=1,stdout=event,stderr="")):
                with self.assertRaises(ContentError):CodexResearcher(executable=sys.executable,audit_dir=d).generate(1,[],"中文")
            report=json.loads((Path(d)/"last-research.json").read_text())
            self.assertEqual(report["status"],"failed")
            self.assertTrue(report["reason"])

    def test_timeout_replaces_a_previous_success_audit(self):
        import tempfile,json,subprocess
        from pathlib import Path
        from x_project.research import CodexResearcher
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"last-research.json";path.write_text('{"at":"old","status":"searched"}')
            with patch("x_project.research.subprocess.run",side_effect=subprocess.TimeoutExpired("test",1)):
                with self.assertRaises(ContentError):CodexResearcher(executable=sys.executable,audit_dir=d).generate(1,[],"中文")
            report=json.loads(path.read_text())
            self.assertEqual(report["status"],"failed")
            self.assertNotEqual(report["at"],"old")

    def test_search_only_result_remains_a_candidate_requiring_independent_source_verification(self):
        events=[{"type":"item.completed","item":{"type":"web_search","action":{"type":"search"}}}]
        items=self.invoke(events,items=[draft().to_dict()])
        self.assertEqual(len(items),1)
        self.assertEqual(self.report["opened_pages"],0)
        self.assertEqual(self.report["source_verification"],"required")
