"""Post invariants. No device or model access in this module."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
from pathlib import Path
import re
import struct
import unicodedata
import zlib
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
    title: str = ""
    images: list[str] = field(default_factory=list)
    platform: str = "x"

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
            return cls(value["body"], value["topic"], [Source(**s) for s in value["sources"]],
                       title=value.get("title", ""), images=value.get("images", []), platform=value.get("platform", "x"))
        except (KeyError, TypeError) as e:
            raise ContentError("草稿结构不完整") from e


def validate_draft(draft, now=None):
    now = now or utcnow()
    if draft.platform not in ("x", "xiaohongshu"):
        raise ContentError("不支持的发布平台")
    if not isinstance(draft.title, str) or "\x00" in draft.title:
        raise ContentError("标题必须为文字且不能含空字符")
    if not isinstance(draft.images, list) or len(draft.images) > 9 or any(
            not isinstance(path, str) or not path.strip() or "\x00" in path for path in draft.images):
        raise ContentError("图片必须为至多9个本机文件路径")
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
    if draft.platform == "xiaohongshu":
        if not draft.title.strip() or len(draft.title) > 20 or any(c in draft.title for c in "\r\n"):
            raise ContentError("小红书标题须为1–20字的单行文字")
        if re.search(r"https?://|www\.", draft.title):
            raise ContentError("标题不能插入未经核对的链接")
        if len(draft.text) > 1000:
            raise ContentError("小红书正文含来源链接不能超过1000字")
    elif weighted_length(draft.text) > 280:
        raise ContentError("超出普通 X 帖子的保守长度预算")
    return draft


def _valid_png_chunks(image):
    image.seek(8)
    has_data = False
    first = True
    while True:
        chunk_header = image.read(8)
        if len(chunk_header) != 8:
            return False
        size, kind = struct.unpack(">I4s", chunk_header)
        if first and (kind != b"IHDR" or size != 13):
            return False
        first = False
        checksum = zlib.crc32(kind)
        remaining = size
        while remaining:
            block = image.read(min(remaining, 65536))
            if not block:
                return False
            checksum = zlib.crc32(block, checksum)
            remaining -= len(block)
        raw_checksum = image.read(4)
        if len(raw_checksum) != 4 or int.from_bytes(raw_checksum, "big") != checksum:
            return False
        has_data |= kind == b"IDAT" and size > 0
        if kind == b"IEND":
            return size == 0 and has_data and not image.read(1)


def _local_image_format(path):
    """Recognize image headers and terminal markers without requiring Pillow."""
    with path.open("rb") as image:
        header = image.read(32)
        if (header.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
                and len(header) >= 29 and all(struct.unpack(">II", header[16:24]))):
            if _valid_png_chunks(image):
                return "png"
        if header.startswith(b"\xff\xd8\xff"):
            image.seek(-2, 2)
            if image.read() != b"\xff\xd9":
                return None
            # A real JPEG has a frame with nonzero dimensions before scan data.
            image.seek(2)
            while True:
                if image.read(1) != b"\xff":
                    return None
                marker = image.read(1)
                while marker == b"\xff":
                    marker = image.read(1)
                if not marker or marker in (b"\xda", b"\xd9"):
                    return None
                raw_size = image.read(2)
                if len(raw_size) != 2:
                    return None
                size = int.from_bytes(raw_size, "big")
                if size < 2:
                    return None
                frame = image.read(size - 2)
                if len(frame) != size - 2:
                    return None
                if marker[0] in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                    if len(frame) >= 6 and all(struct.unpack(">HH", frame[1:5])):
                        return "jpeg"
                    return None
    return None


def validate_note_media(draft):
    """Require actual local PNG/JPEG assets before opening the note editor.

    Draft validation allows an empty list so a sourced candidate can receive a
    locally rendered cover later. This boundary never downloads a remote asset.
    """
    if (draft.platform != "xiaohongshu" or not isinstance(draft.images, list)
            or not 1 <= len(draft.images) <= 9):
        raise ContentError("小红书图文需要1–9张本机PNG/JPEG图片")
    for value in draft.images:
        if not isinstance(value, str) or not value.strip() or "\x00" in value or urlsplit(value).scheme:
            raise ContentError("图片必须是本机PNG/JPEG文件，不能使用网络地址")
        path = Path(value)
        expected = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}.get(path.suffix.lower())
        try:
            if not expected or not path.is_file() or _local_image_format(path) != expected:
                raise ContentError("图片不存在或不是有效PNG/JPEG文件")
        except (OSError, ValueError, struct.error):
            raise ContentError("图片不存在或不是有效PNG/JPEG文件") from None
    return draft
