"""Orchestration: independent model, state and device boundaries."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import tomllib
import uuid

from .android import Adb, DeviceError, PhonePoster
from .content import ContentError, Draft, timestamp, utcnow, validate_draft, weighted_length
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


@dataclass
class Config:
    root: Path
    account: str
    adb: str
    serial: str
    allow_unlock: bool
    publish_enabled: bool
    persona: str
    device_id: str = ""
    unlock_method: str = "pin"

    @classmethod
    def load(cls, root):
        root = Path(root).resolve()
        values = tomllib.loads((root/"config.toml").read_text())
        if not re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", values["account"]):
            raise ValueError("config.toml 的 account 不是有效 X 用户名")
        return cls(root=root, **values)

    @property
    def state(self):
        p = self.root/".state"; p.mkdir(mode=0o700, parents=True, exist_ok=True); return p


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
        self.cancel_version = cancellation_version(config)
        self.store = Store(config.state/"queue.sqlite3")
        self.schedule = Schedule(config.state/"schedule.sqlite3")
        self.researcher = researcher or CodexResearcher(audit_dir=config.state)
        self.verifier = verifier or SourceVerifier()
        self.poster = poster or PhonePoster(Adb(config.adb, config.serial, config.device_id), config.account,
                                          config.root/".env", config.state, config.allow_unlock, config.unlock_method)

    def collect(self, count=3):
        history = [{"topic": row["topic"], "sources": json.loads(row["draft"])["sources"]} for row in self.store.rows(90)]
        generated = self.researcher.generate(count, history, self.config.persona)
        accepted = 0; rejected = []
        for draft in generated:
            try:
                validate_draft(draft)
                self.verifier.verify(draft)
                if self.store.enqueue(draft): accepted += 1
                else: rejected.append({"topic": draft.topic, "reason": "重复事件、正文或来源"})
            except (ContentError, ValueError, KeyError):
                # Reasons are content-only; never include subprocess environments or credentials.
                import sys
                rejected.append({"topic": draft.topic, "reason": str(sys.exception())})
        report = {"generated": len(generated), "accepted": accepted, "rejected": rejected}
        (self.config.state/"last-collect.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    def add(self, text):
        text = text.strip()
        if not text or "\x00" in text or weighted_length(text) > 280:
            raise ContentError("手动文案为空或超出长度预算；网址请单独占一行")
        draft = Draft(text, "manual-"+hashlib.sha256(text.encode()).hexdigest()[:16], [])
        self.store.enqueue(draft)
        return next(row["id"] for row in self.store.rows(10000) if row["fingerprint"] == draft.fingerprint)

    def preview(self, post_id):
        draft = self.store.draft(post_id)
        with self.poster.session():
            self.poster.prepare(draft.text)
        return {"status": "preview", "id": post_id, "account": self.config.account,
                "text": draft.text, "note": "仅填写并核对，未点击发布"}

    def preview_text(self, text):
        text = text.strip()
        if not text or "\x00" in text or weighted_length(text) > 280:
            raise ContentError("预览文案为空或超出长度预算")
        with self.poster.session():
            self.poster.prepare(text)
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
            if draft.sources:
                validate_draft(draft, now)
            def before_submit():
                if cancellation_version(self.config) != self.cancel_version:
                    raise DeviceError("任务已被停止，未提交帖子")
                if automatic and not Config.load(self.config.root).publish_enabled:
                    raise DeviceError("自动发布已关闭")
                actual = self.clock()
                if draft.sources:
                    validate_draft(draft, actual)
                self.store.submitting(row["id"], actual, due_at)
            with self.poster.session():
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
        now = now or utcnow()
        self.store.recover()
        self.retire_invalid(now)
        self.schedule.plan(now)
        due = self.schedule.take_due(now) if publish and self.config.publish_enabled else None
        generation_marker = self.config.state/"last-generation-hour"
        hour = str(int(now.timestamp())//3600)
        if self.store.queued_count() < 3 and (not generation_marker.exists() or generation_marker.read_text() != hour):
            generation_marker.write_text(hour)  # One model batch/hour; failed sources must not cause minute-by-minute generation.
            try:
                self.collect(3)
            except ContentError as error:
                return {"status": "research-unavailable", "note": str(error)}
        if due is None:
            return {"status": "waiting", "publish_enabled": self.config.publish_enabled, "planned": self.schedule.upcoming(now)}
        # Slow generation must not turn an old due time into catch-up posting.
        actual = self.clock()
        if actual.timestamp()-due > 120:
            return {"status": "skipped", "note": "当前计划已过期，不补发"}
        return self.publish(now=actual, due_at=due, automatic=True)
