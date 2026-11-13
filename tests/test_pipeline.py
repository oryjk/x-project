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

    def live_pipeline(self, items=None):
        from unittest.mock import Mock
        research=Mock()
        (self.config.root/"config.toml").write_text('account="tester"\nadb="adb"\nserial="serial"\nallow_unlock=false\npublish_enabled=true\npersona="中文"\ndevice_id="bound-test-device"\n')
        fresh=draft("现场采集的独立新消息", "https://example.org/live");fresh.topic="live-topic";fresh.sources[0].published_at=self.now.isoformat()
        research.generate.return_value=[fresh] if items is None else items
        return Pipeline(self.config,researcher=research,verifier=Mock(),poster=Poster()),research

    def test_no_research_before_a_send_slot(self):
        p,research=self.live_pipeline()
        result=p.tick(publish=True,now=self.now)
        self.assertEqual(result["status"],"waiting")
        research.generate.assert_not_called()
        self.assertEqual(p.store.queued_count(),0)

    def test_due_slot_only_sends_the_newly_collected_item_not_old_queue(self):
        from unittest.mock import patch
        p,research=self.live_pipeline();self.seed(p)
        due=p.schedule.plan(self.now)[0]
        now=datetime.fromtimestamp(due,timezone.utc);p.clock=lambda:now
        with patch("x_project.pipeline.Config.load",return_value=self.config):result=p.tick(True,now)
        self.assertEqual(result["status"],"sent")
        self.assertEqual(result["id"],2)
        self.assertEqual(p.store.get(1)["status"],"queued")
        self.assertEqual(research.generate.call_count,1)

    def test_empty_live_research_never_falls_back_to_precollected_queue(self):
        p,research=self.live_pipeline([]);self.seed(p)
        due=p.schedule.plan(self.now)[0];now=datetime.fromtimestamp(due,timezone.utc);p.clock=lambda:now
        result=p.tick(True,now)
        self.assertEqual(result["status"],"no-verified-news")
        self.assertEqual(p.poster.calls,0)
        self.assertEqual(p.store.get(1)["status"],"queued")

    def test_three_minute_research_still_sends_within_its_window(self):
        from datetime import timedelta
        from unittest.mock import patch
        p,_=self.live_pipeline();due=p.schedule.plan(self.now)[0]
        now=datetime.fromtimestamp(due,timezone.utc);p.clock=lambda:now+timedelta(minutes=3)
        with patch("x_project.pipeline.Config.load",return_value=self.config):result=p.tick(True,now)
        self.assertEqual(result["status"],"sent")

    def test_uncertain_previous_submit_blocks_new_research(self):
        p,research=self.live_pipeline();self.seed(p)
        row=p.store.claim(self.now);p.store.submitting(row["id"],self.now);p.store.finish(row["id"],"unknown")
        due=p.schedule.plan(self.now)[0];now=datetime.fromtimestamp(due,timezone.utc);p.clock=lambda:now
        self.assertEqual(p.tick(True,now)["status"],"blocked")
        research.generate.assert_not_called()

    def test_live_collection_saves_only_one_selected_candidate(self):
        a=draft();a.sources[0].published_at=self.now.isoformat()
        b=draft("第二条候选", "https://example.org/alternative");b.topic="alternative";b.sources[0].published_at=self.now.isoformat()
        p,_=self.live_pipeline([a,b])
        report=p.collect(3,enqueue_limit=1)
        self.assertEqual(report["accepted_ids"],[1])
        self.assertEqual(p.store.queued_count(),1)

    def test_live_slot_waits_for_actual_eight_minute_gap_using_its_same_new_id(self):
        from datetime import timedelta
        p,_=self.live_pipeline();self.seed(p)
        due=p.schedule.plan(self.now)[1]
        ready=datetime.fromtimestamp(due+60,timezone.utc)
        first=p.store.claim(ready-timedelta(minutes=9))
        p.store.submitting(first["id"],ready-timedelta(minutes=3));p.store.finish(first["id"],"sent")
        clock=[ready];waits=[]
        p.clock=lambda:clock[0]
        def sleep(seconds):waits.append(seconds);clock[0]+=timedelta(seconds=seconds)
        p.sleep=sleep
        result=p.tick(True,datetime.fromtimestamp(due,timezone.utc))
        self.assertEqual(result["status"],"sent")
        self.assertEqual(result["id"],2)
        self.assertEqual(sum(waits),300)
        self.assertLessEqual(max(waits),30)

    def test_live_wait_stops_if_disable_arrives_before_submission_eligibility(self):
        from datetime import timedelta
        from x_project.pipeline import cancel_inflight
        p,_=self.live_pipeline();self.seed(p);due=p.schedule.plan(self.now)[1]
        ready=datetime.fromtimestamp(due+60,timezone.utc)
        first=p.store.claim(ready-timedelta(minutes=9));p.store.submitting(first["id"],ready-timedelta(minutes=3));p.store.finish(first["id"],"sent")
        p.clock=lambda:ready;p.sleep=lambda seconds:cancel_inflight(self.config)
        self.assertEqual(p.tick(True,datetime.fromtimestamp(due,timezone.utc))["status"],"cancelled")
        self.assertEqual(p.poster.calls,0)
