"""Orchestration: independent model, state and device boundaries."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import time
import uuid

from .android import Adb, DeviceError, PhonePoster
from .config import Config
from .content import ContentError, Draft, timestamp, utcnow, validate_draft, validate_note_media, weighted_length
from .research import CodexResearcher, SourceVerifier
from .schedule import Schedule
from .store import Store, SubmissionBlocked


def cancel_inflight(config):
    marker = config.state/"cancel-version"
    temp = config.state/f"cancel-{uuid.uuid4().hex}.tmp"
    temp.write_text(uuid.uuid4().hex)
    temp.replace(marker)


def cancellation_version(config):
    marker = config.state/"cancel-version"
    return marker.read_text() if marker.exists() else ""


@contextmanager
def exclusive(state):
    state = Path(state); state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state/"worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeviceError("另一个采集或手机任务仍在运行") from None
        yield


class Pipeline:
    def __init__(self, config, researcher=None, verifier=None, poster=None, clock=None):
        self.config = config
        self.clock = clock or utcnow
        self.sleep = time.sleep
        self.cancel_version = cancellation_version(config)
        self.store = Store(config.state/"queue.sqlite3", hourly_limit=config.hourly_limit, min_interval_minutes=config.min_interval_minutes)
        self.schedule = Schedule(config.state/"schedule.sqlite3", hourly_limit=config.hourly_limit, min_interval_minutes=config.min_interval_minutes)
        self.researcher = researcher or CodexResearcher(executable=config.codex, audit_dir=config.state, project_root=config.root, platform=config.platform)
        self.verifier = verifier or SourceVerifier()
        poster_class = PhonePoster
        if config.platform == 'xiaohongshu':
            from .xiaohongshu import XiaohongshuPoster
            poster_class = XiaohongshuPoster
        self.poster = poster or poster_class(Adb(config.adb, config.serial, config.device_id), config.account,
                                          config.root/".env", config.device_state, config.allow_unlock, config.unlock_method,
                                          credential_prefix=config.credential_prefix)

    def current_config(self):
        return Config.load(self.config.root, self.config.profile) if self.config.profile else Config.load(self.config.root)

    def collect(self, count=3, enqueue_limit=None):
        history = [{"topic": row["topic"], "sources": json.loads(row["draft"])["sources"]} for row in self.store.rows(90)]
        generated = self.researcher.generate(count, history, self.config.persona)
        accepted_ids = []; rejected = []; verified_sources = 0
        for draft in generated:
            try:
                if draft.platform != self.config.platform:
                    raise ContentError('候选平台与当前账号不匹配')
                validate_draft(draft)
                self.verifier.verify(draft)
                if self.config.platform == 'xiaohongshu':
                    from .cards import ensure_note_images
                    if self.config.images:
                        draft.images = list(self.config.images)
                    draft = ensure_note_images(draft, self.config.state, self.config.font_path)
                    if len(draft.images) != 1:
                        raise ContentError('当前小红书发布端只支持一张封面')
                verified_sources += len(draft.sources)
                post_id = self.store.enqueue(draft)
                if post_id:
                    accepted_ids.append(post_id)
                    if enqueue_limit is not None and len(accepted_ids) >= enqueue_limit:
                        break
                else:
                    rejected.append({"topic": draft.topic, "reason": "重复事件、正文或来源"})
            except (ContentError, ValueError, KeyError):
                # Reasons are content-only; never include subprocess environments or credentials.
                import sys
                rejected.append({"topic": draft.topic, "reason": str(sys.exception())})
        report = {"generated": len(generated), "accepted": len(accepted_ids), "accepted_ids": accepted_ids,
                  "verified_sources": verified_sources, "rejected": rejected}
        (self.config.state/"last-collect.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    def manual_draft(self, text, title='', images=None):
        text = text.strip()
        if not text or "\x00" in text:
            raise ContentError('手动文案为空或格式无效')
        if self.config.platform == 'xiaohongshu':
            if not title or not title.strip() or len(title) > 20 or any(c in title for c in '\x00\r\n') or len(text) > 1000:
                raise ContentError('小红书需要单行1–20字标题及不超过1000字正文')
            draft = Draft(text, 'manual-' + hashlib.sha256(text.encode()).hexdigest()[:16], [], title=title,
                          images=list(images or self.config.images), platform='xiaohongshu')
            validate_note_media(draft)
            if len(draft.images) != 1:
                raise ContentError('当前小红书发布端只支持一张封面')
            return draft
        if title or images:
            raise ContentError('X文字发布不使用 --title / --image')
        if weighted_length(text) > 280:
            raise ContentError("手动文案为空或超出长度预算；网址请单独占一行")
        return Draft(text, "manual-"+hashlib.sha256(text.encode()).hexdigest()[:16], [])

    def add(self, text, title='', images=None):
        draft = self.manual_draft(text, title, images)
        self.store.enqueue(draft)
        return next(row["id"] for row in self.store.rows(10000) if row["fingerprint"] == draft.fingerprint)

    def preview(self, post_id):
        draft = self.store.draft(post_id)
        if draft.platform != self.config.platform:
            raise ContentError('草稿平台与当前账号不匹配')
        with self.poster.session():
            if self.config.platform == 'xiaohongshu':
                validate_note_media(draft); self.poster.prepare_note(draft)
            else:
                self.poster.prepare(draft.text)
        return {"status": "preview", "id": post_id, "account": self.config.account,
                "text": draft.text, "note": "仅填写并核对，未点击发布"}

    def preview_text(self, text, title='', images=None):
        draft = self.manual_draft(text, title, images)
        with self.poster.session():
            if self.config.platform == 'xiaohongshu':
                self.poster.prepare_note(draft)
            else:
                self.poster.prepare(draft.text)
        return {"status": "preview", "account": self.config.account, "text": text,
                "note": "只预览，不进入自动发布队列"}

    def retire_invalid(self, now):
        for row in self.store.rows(10000):
            if row["status"] != "queued":
                continue
            try:
                draft = Draft.from_dict(json.loads(row["draft"]))
                if draft.sources:
                    validate_draft(draft, now)
            except (ValueError, ContentError):
                self.store.reject(row["id"], "草稿来源过期或内容不合格，不再排队")

    def publish(self, post_id=None, now=None, due_at=None, automatic=False):
        if not self.config.device_id:
            raise DeviceError("正式发布必须绑定物理设备 device_id")
        now = now or self.clock()
        self.store.recover()
        self.retire_invalid(now)
        row = self.store.claim(now, post_id)
        if not row:
            return {"status": "skipped", "reason": "没有可发草稿、未到最小间隔、每小时已达3条或存在待核对提交"}
        draft = Draft.from_dict(json.loads(row["draft"]))
        # Revalidate automated sources at send time. A queued item can become stale while offline.
        try:
            if draft.platform != self.config.platform:
                raise ContentError('草稿平台与当前账号不匹配')
            if self.config.platform == 'xiaohongshu':
                validate_note_media(draft)
            if draft.sources:
                validate_draft(draft, now)
            def before_submit():
                if cancellation_version(self.config) != self.cancel_version:
                    raise DeviceError("任务已被停止，未提交帖子")
                if automatic:
                    current = self.current_config()
                    if not current.publish_enabled:
                        raise DeviceError("自动发布已关闭")
                    if (current.account, current.platform, current.device_id) != (self.config.account, self.config.platform, self.config.device_id):
                        raise DeviceError('账号或设备配置已变化，本次停止提交')
                    self.store.hourly_limit = current.hourly_limit
                    self.store.gap_seconds = current.min_interval_minutes * 60
                actual = self.clock()
                if draft.sources:
                    validate_draft(draft, actual)
                self.store.submitting(row["id"], actual, due_at)
            with self.poster.session():
                if self.config.platform == 'xiaohongshu':
                    status, note = self.poster.send_note(draft, before_submit)
                else:
                    status, note = self.poster.send(draft.text, draft.body, before_submit)
            self.store.finish(row["id"], status, note)
            return {"status": status, "id": row["id"], "note": note}
        except Exception as error:
            current = self.store.get(row["id"])
            if current["status"] == "submitting":
                self.store.finish(row["id"], "unknown", "提交过程异常，须核对 X 后手工 resolve")
                return {"status": "unknown", "id": row["id"], "note": "提交过程异常，已禁止自动重发"}
            if isinstance(error, ContentError) and not isinstance(error, SubmissionBlocked):
                self.store.reject(row["id"], str(error))
                return {"status": "rejected", "id": row["id"], "note": str(error)}
            self.store.defer(row["id"], str(error) if isinstance(error, (DeviceError, ContentError)) else "设备操作失败")
            if isinstance(error, (DeviceError, ContentError)):
                return {"status": "deferred", "id": row["id"], "note": str(error)}
            raise

    def tick(self, publish=False, now=None):
        now = now or self.clock()
        self.store.recover()
        self.retire_invalid(now)
        self.schedule.plan(now)
        if self.store.has_unresolved():
            return {"status": "blocked", "note": "有尚未核对的提交，不继续采集或发布"}
        due = self.schedule.take_due(now) if publish and self.config.publish_enabled else None
        if due is None:
            return {"status": "waiting", "publish_enabled": self.config.publish_enabled, "planned": self.schedule.upcoming(now)}
        # Research belongs to this send slot. Never take a pre-collected queue head.
        try:
            report = self.collect(3, enqueue_limit=1)
        except ContentError as error:
            return {"status": "research-unavailable", "note": str(error), "due_at": due}
        if not report["accepted_ids"]:
            return {"status": "no-verified-news", "research": report, "due_at": due}
        # Research duration varies. Keep this slot/id while its lawful submit
        # time is still inside the same window instead of dropping the slot.
        deadline = min(due+600, (int(due)//3600+1)*3600-.001)
        while True:
            if cancellation_version(self.config) != self.cancel_version or not self.current_config().publish_enabled:
                self.store.reject(report["accepted_ids"][0], "本次发送已取消")
                return {"status": "cancelled", "note": "任务已停止，未提交帖子"}
            current = self.clock()
            eligible = self.store.next_eligible_at(current)
            if eligible.timestamp() > deadline:
                self.store.reject(report["accepted_ids"][0], "发送窗口内无法满足账号的实际最小间隔")
                return {"status": "skipped", "note": "本时段剩余时间不足，不补发"}
            delay = (eligible-current).total_seconds()
            if delay <= 0:
                break
            self.sleep(min(delay, 30))
        actual = self.clock()
        if actual.timestamp()-due > 600 or int(actual.timestamp())//3600 != int(due)//3600:
            self.store.reject(report["accepted_ids"][0], "本次现场采集超过发送窗口，不留给后续时段发送")
            return {"status": "skipped", "note": "当前计划已过期，不补发"}
        result = self.publish(post_id=report["accepted_ids"][0], now=actual, due_at=due, automatic=True)
        result["research"] = report
        return result
