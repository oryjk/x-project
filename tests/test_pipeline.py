from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from x_project.android import DeviceError
from x_project.pipeline import Config, Pipeline
from test_content import draft


class Poster:
    def __init__(self, failure=""):
        self.failure = failure
        self.calls = 0
    @contextmanager
    def session(self):
        yield self
    def send(self, text, body, before_submit):
        self.calls += 1
        if self.failure == "before": raise DeviceError("device unavailable")
        before_submit()
        if self.failure == "after": raise DeviceError("response lost")
        return "sent", "positive receipt"
    def prepare(self, text):
        self.calls += 1
        return (100, 100)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = Config(root, "tester", "adb", "serial", False, True, "专业中文", "bound-test-device")
        self.now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    def seed(self, pipeline):
        one = draft(); one.sources[0].published_at = self.now.isoformat()
        pipeline.store.enqueue(one, self.now)

    def test_submission_exception_is_unknown_and_future_ticks_cannot_repost_it(self):
        poster = Poster("after"); p = Pipeline(self.config, poster=poster); self.seed(p)
        result = p.publish(now=self.now)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(p.store.get(1)["status"], "unknown")
        self.assertEqual(p.publish(now=self.now.replace(minute=30))["status"], "skipped")
        self.assertEqual(poster.calls, 1)

    def test_pre_submit_failure_preserves_draft_and_receipt_commits_success(self):
        poster = Poster("before"); p = Pipeline(self.config, poster=poster); self.seed(p)
        self.assertEqual(p.publish(now=self.now)["status"], "deferred")
        self.assertEqual(p.store.get(1)["status"], "queued")
        poster.failure = ""
        self.assertEqual(p.publish(now=self.now.replace(minute=30))["status"], "sent")
        self.assertEqual(p.store.get(1)["status"], "sent")

    def test_one_bad_generated_source_does_not_discard_other_verified_drafts(self):
        class Research:
            def generate(self, *args):
                a = draft(); a.sources[0].published_at = self_time.isoformat()
                b = draft("未经核对的另一则消息", "https://example.org/unverified"); b.topic = "bad source"
                b.sources[0].published_at = self_time.isoformat()
                return [a, b]
        class Verifier:
            def verify(self, d):
                if d.topic == "bad source": raise ValueError("not verified")
                return d
        self_time = self.now
        p = Pipeline(self.config, researcher=Research(), verifier=Verifier(), poster=Poster())
        result = p.collect()
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(p.store.queued_count(), 1)
        self.assertEqual(len(result["rejected"]), 1)

    def test_raw_preview_does_not_enqueue_a_test_message_for_later_automatic_publication(self):
        poster = Poster(); p = Pipeline(self.config, poster=poster)
        self.assertEqual(p.preview_text("仅预览，不发布")["status"], "preview")
        self.assertEqual(p.store.queued_count(), 0)
        self.assertEqual(poster.calls, 1)

    def test_stale_head_is_retired_so_fresh_content_is_not_blocked(self):
        poster = Poster(); p = Pipeline(self.config, poster=poster)
        old = draft(); old.sources[0].published_at = "2020-01-01"; p.store.enqueue(old, self.now)
        fresh = draft("新的独立事件", "https://example.org/new"); fresh.topic = "new event"
        fresh.sources[0].published_at = self.now.isoformat(); p.store.enqueue(fresh, self.now)
        result = p.publish(now=self.now)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["id"], 2)
        self.assertEqual(p.store.get(1)["status"], "rejected")

    def test_cancellation_during_phone_preparation_blocks_irreversible_submit(self):
        from x_project.pipeline import cancel_inflight
        config = self.config
        class CancelPoster(Poster):
            def send(self, text, body, before_submit):
                cancel_inflight(config)
                before_submit()
                raise AssertionError("must never reach publication")
        p = Pipeline(config, poster=CancelPoster()); self.seed(p)
        self.assertEqual(p.publish(now=self.now)["status"], "deferred")
        self.assertEqual(p.store.get(1)["status"], "queued")

    def test_direct_publication_without_a_device_binding_is_rejected_before_phone_access(self):
        poster = Poster(); self.config.device_id = ""
        p = Pipeline(self.config, poster=poster); self.seed(p)
        with self.assertRaises(DeviceError): p.publish(now=self.now)
        self.assertEqual(poster.calls, 0)
        self.assertEqual(p.store.get(1)["status"], "queued")
