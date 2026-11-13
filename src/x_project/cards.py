"""Optional local typography covers. No model, credentials or network access."""
from dataclasses import replace
import os
from pathlib import Path
from uuid import uuid4

from .content import ContentError, Draft, utcnow, validate_draft, validate_note_media


def _font_candidates(font_path):
    if font_path:
        return [Path(font_path)]
    paths = [Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
             Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
             Path("/System/Library/Fonts/PingFang.ttc"),
             Path("/System/Library/Fonts/STHeiti Light.ttc"),
             Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")]
    for root in (Path("/usr/local/share/fonts"), Path.home()/".local/share/fonts", Path.home()/"Library/Fonts"):
        for pattern in ("*CJK*Regular*", "*SC*Regular*", "*SimHei*", "*msyh*"):
            paths.extend(sorted(root.rglob(pattern)))
    return paths


def _load_font(ImageFont, font_path):
    for path in _font_candidates(font_path):
        if not path.is_file():
            continue
        try:
            font = ImageFont.truetype(str(path), 36)
            # Reject tofu-only Latin fonts, including explicitly supplied ones.
            missing = bytes(font.getmask("\U0010ffff"))
            if bytes(font.getmask("中")) == missing or bytes(font.getmask("文")) == missing:
                continue
            return str(path)
        except (OSError, ValueError):
            continue
    raise ContentError("生成图文封面需要本机中文字体；请配置FONT_PATH或提供PNG/JPEG图片")


def _wrap(text, font, width):
    """Wrap Chinese prose and long source URLs without dropping content."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for character in paragraph:
            if line and font.getlength(line + character) > width:
                lines.append(line)
                line = ""
            line += character
        lines.append(line)
    return lines


def ensure_note_images(draft: Draft, state_dir, font_path="") -> Draft:
    """Keep supplied local images or render a sourced card beneath state_dir.

    Source verification is a separate mandatory pipeline step. Rendering uses
    only this draft's validated title, body and original citation URLs.
    """
    validate_draft(draft, now=utcnow())
    if draft.platform != "xiaohongshu":
        raise ContentError("只有小红书图文可以生成笔记封面")
    if draft.images:
        return validate_note_media(draft)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ContentError("生成图文封面需要可选Pillow依赖；请安装x-project[cards]或提供PNG/JPEG图片") from None
    font_file = _load_font(ImageFont, font_path)
    try:
        title_font = ImageFont.truetype(font_file, 66)
        body_font = ImageFont.truetype(font_file, 34)
        label_font = ImageFont.truetype(font_file, 23)
        source_font = ImageFont.truetype(font_file, 25)
        title_lines = _wrap(draft.title.strip(), title_font, 868)
        body_lines = _wrap(draft.body.strip(), body_font, 868)
        source_lines = _wrap("\n".join(source.url for source in draft.sources), source_font, 868)
        body_y = 220 + len(title_lines) * 86 + 65
        source_y = body_y + len(body_lines) * 53 + 88
        height = max(1440, source_y + len(source_lines) * 38 + 140)
        canvas = Image.new("RGB", (1080, height), "#f4f0e7")
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((44, 44, 1036, height - 44), radius=28, fill="#123c3c")
        draw.rounded_rectangle((100, 106, 110, 147), radius=4, fill="#94e6cd")
        draw.text((132, 109), "AI / TECH NOTES", font=label_font, fill="#a6e9d7")
        draw.text((100, 220), "\n".join(title_lines), font=title_font, fill="#fffaf0", spacing=20)
        draw.line((100, body_y - 30, 968, body_y - 30), fill="#557470", width=2)
        for index, line in enumerate(body_lines):
            draw.text((100, body_y + index * 53), line, font=body_font, fill="#edf0e8")
        draw.text((100, source_y - 44), "来源 / ORIGINAL SOURCES", font=label_font, fill="#a6e9d7")
        for index, line in enumerate(source_lines):
            draw.text((100, source_y + index * 38), line, font=source_font, fill="#bbccc6")
    except (OSError, ValueError, UnicodeError):
        raise ContentError("中文字体无法渲染图文封面；请检查FONT_PATH或提供PNG/JPEG图片") from None
    output = None
    try:
        state = Path(state_dir).resolve()
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = state/"note-cards"
        if not directory.resolve().is_relative_to(state):
            raise ContentError("封面目录必须位于账号状态目录内")
        directory.mkdir(mode=0o700, exist_ok=True)
        output = directory/f"{uuid4().hex}.png"
        # Exclusive creation avoids overwriting existing state; private from the
        # first byte, without a temporary copy outside the requested directory.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            canvas.save(target, format="PNG")
        return validate_note_media(replace(draft, images=[str(output)]))
    except OSError:
        if output is not None:
            output.unlink(missing_ok=True)
        raise ContentError("无法在账号状态目录中保存图文封面") from None
