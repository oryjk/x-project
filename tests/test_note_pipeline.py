from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from x_project.config import Config
from x_project.content import ContentError
from x_project.pipeline import Pipeline
from test_platform_content import note, png_bytes
from test_content import draft


class NotePoster:
    def __init__(self, uncertain=False): self.calls = []; self.uncertain = uncertain
    @contextmanager
    def session(self): yield self
    def prepare_note(self, draft): self.calls.append(('preview', draft.title, list(draft.images)))
    def send_note(self, draft, before_submit):
        self.calls.append(('send', draft.title, list(draft.images)))
        before_submit()
        return ('unknown', 'uncertain') if self.uncertain else ('sent', 'confirmed')


class NotePipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'cover.png'; self.image.write_bytes(png_bytes())
        self.config = Config(self.root, 'red_id', 'adb', 'phone', False, False, '中文', 'bound', platform='xiaohongshu')

    def test_manual_note_preview_does_not_enqueue_and_routes_title_and_cover(self):
        poster = NotePoster(); pipeline = Pipeline(self.config, poster=poster)
        result = pipeline.preview_text('正式发布前的预览', '图文标题', [str(self.image)])
        self.assertEqual(result['status'], 'preview')
        self.assertEqual(pipeline.store.queued_count(), 0)
        self.assertEqual(poster.calls, [('preview', '图文标题', [str(self.image)])])

    def test_uncertain_note_freezes_only_its_account_and_never_retries(self):
        poster = NotePoster(uncertain=True); pipeline = Pipeline(self.config, poster=poster)
        post_id = pipeline.add('用户授权文案', '图文标题', [str(self.image)])
        self.assertEqual(pipeline.publish(post_id)['status'], 'unknown')
        self.assertEqual(pipeline.publish(post_id)['status'], 'skipped')
        self.assertEqual(len(poster.calls), 1)
        other = Config(self.root, 'other', 'adb', 'other-phone', False, False, '中文', 'bound2', profile='other')
        self.assertFalse(Pipeline(other, poster=NotePoster()).store.has_unresolved())

    def test_automated_note_is_source_verified_before_media_or_queue(self):
        item = note(); item.sources[0].published_at = datetime.now(timezone.utc).isoformat()
        research = Mock(); research.generate.return_value = [item]
        verifier = Mock(); verifier.verify.side_effect = ContentError('证据不符')
        pipeline = Pipeline(self.config, researcher=research, verifier=verifier, poster=NotePoster())
        self.assertEqual(pipeline.collect()['accepted'], 0)
        self.assertEqual(pipeline.store.queued_count(), 0)
        self.assertFalse((self.config.state / 'note-cards').exists())

    def test_x_candidate_cannot_be_queued_for_xiaohongshu(self):
        research = Mock(); research.generate.return_value = [draft()]
        pipeline = Pipeline(self.config, researcher=research, verifier=Mock(), poster=NotePoster())
        self.assertEqual(pipeline.collect()['accepted'], 0)
