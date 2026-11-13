"""Post invariants. No device or model access in this module."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import re
import unicodedata
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit


class ContentError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        # Coarse date used for freshness only; retain the original date in the source record.
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ContentError("来源时间必须包含时区")
    return result.astimezone(timezone.utc)


def canonical_url(value):
    p = urlsplit(value.strip())
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not host or p.username or p.password or p.port not in (None, 443):
        raise ContentError("来源必须为公开 HTTPS 链接")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ContentError("不能引用本地服务")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ContentError("不能引用私有网络地址")
    except ValueError as e:
        if isinstance(e, ContentError):
            raise
    if host in ("twitter.com", "www.twitter.com", "www.x.com"):
        host = "x.com"
    if host == "x.com" and re.fullmatch(r"/[^/]+/status/\d+/?", p.path):
        return f"https://x.com{p.path.rstrip('/')}"
    if host == "youtu.be":
        return "https://www.youtube.com/watch?" + urlencode({"v": p.path.strip("/")})
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        video = parse_qs(p.query).get("v", [None])[0]
        if p.path.startswith(("/shorts/", "/live/")):
            video = p.path.split("/")[2]
        if video:
            return "https://www.youtube.com/watch?" + urlencode({"v": video})
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref", "source")]
    return urlunsplit(("https", host, p.path.rstrip("/") or "/", urlencode(query), ""))


def _char_weight(char):
    n = ord(char)
    return 1 if n <= 0x10FF or 0x2000 <= n <= 0x200D or 0x2010 <= n <= 0x201F or 0x2032 <= n <= 0x2037 else 2


def weighted_length(text):
    """Conservative Twitter-text bound; URLs must occupy their own lines.

    Emoji sequences may be over-counted, never intentionally under-counted.
    The actual X button state is checked again before submission.
    """
    total = 0
    lines = unicodedata.normalize("NFC", text).split("\n")
    for i, line in enumerate(lines):
        total += 23 if re.fullmatch(r"https://[^\s]+", line) else sum(_char_weight(c) for c in line)
        total += i > 0
    return total


@dataclass
class Source:
    url: str
    title: str
    kind: str
    published_at: str
    excerpt: str


@dataclass
class Draft:
    body: str
    topic: str
    sources: list[Source]

    @property
    def text(self):
        return self.body.strip() + ("\n" + "\n".join(s.url for s in self.sources) if self.sources else "")

    @property
    def fingerprint(self):
        normalized = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", self.body)).casefold()
        return hashlib.sha256(normalized.encode()).hexdigest()

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        try:
            return cls(value["body"], value["topic"], [Source(**s) for s in value["sources"]])
        except (KeyError, TypeError) as e:
            raise ContentError("草稿结构不完整") from e


def validate_draft(draft, now=None):
    now = now or utcnow()
    if not isinstance(draft.body, str) or not draft.body.strip() or "\x00" in draft.body:
        raise ContentError("文案不能为空或含空字符")
    if re.search(r"https?://|www\.", draft.body):
        raise ContentError("正文不能插入未经核对的链接；请使用来源字段")
    if not isinstance(draft.topic, str) or not draft.topic.strip():
        raise ContentError("必须标明独立事件主题")
    if not 1 <= len(draft.sources) <= 2:
        raise ContentError("每条需要 1–2 个真实来源")
    valid_kinds = {"article", "post", "video_transcript", "video_description", "video_title"}
    for s in draft.sources:
        canonical = canonical_url(s.url)
        # Canonical identity is for dedupe. Some article servers treat /path
        # and /path/ differently; fetch exactly the URL the researcher opened.
        if urlsplit(canonical).hostname in ("x.com", "www.youtube.com"):
            s.url = canonical
        else:
            s.url = s.url.strip()
        if s.kind not in valid_kinds or not s.title.strip() or not 15 <= len(s.excerpt.strip()) <= 350:
            raise ContentError("来源需要类型、标题及可核对的短证据")
        age = now - timestamp(s.published_at)
        if age < -timedelta(minutes=10) or age > timedelta(days=7):
            raise ContentError("来源过旧或发布时间在未来")
    if all(s.kind == "video_title" for s in draft.sources):
        raise ContentError("只有视频标题不能支持内容分析，需要正文/字幕/原帖证据")
    if len({canonical_url(s.url) for s in draft.sources}) != len(draft.sources):
        raise ContentError("引用来源重复")
    if weighted_length(draft.text) > 280:
        raise ContentError("超出普通 X 帖子的保守长度预算")
    return draft
