"""可选的图片缩放：装了 Pillow 就有，没装就当作"没有"。

标准库解不了 JPEG/WebP，所以服务端缩图这件事只能靠一个真正的图像库。这里把它
做成**可选的**：`requirements.txt` 里的 Pillow 装上了（见 doc/ops/install.md），
`?w=` 请求就能现场生成对应宽度的小图并缓存；没装就一切照旧——直接给原图，
上传时浏览器预生成的那几档小图仍然可用。

装坏了、版本不对、导入时抛异常，都只是让这里退回"没有"，不影响服务启动。
"""

import io as _io
import os

try:                                    # noqa: SIM105 —— 缺库是最正常的一种情况
    from PIL import Image
except Exception:                       # pragma: no cover - 取决于环境
    Image = None

# 再大的宽度没有意义：原图也不会更清楚
MAX_WIDTH = 2400
QUALITY = 82


def available():
    """能不能在服务端缩图。"""
    return Image is not None


def resize_webp(data, width, quality=QUALITY):
    """把一张图缩到 width 宽，返回 WebP 字节；不需要或做不到时返回 None。

    只缩不放：原图比目标还窄时返回 None，让调用方把原图交出去。
    """
    if Image is None or not data:
        return None
    try:
        width = int(width)
    except (TypeError, ValueError):
        return None
    if width <= 0:
        return None
    width = min(width, MAX_WIDTH)
    try:
        with Image.open(_io.BytesIO(data)) as img:
            img.load()
            if img.width <= width:
                return None
            ratio = width / float(img.width)
            size = (width, max(1, int(round(img.height * ratio))))
            out = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
            out = out.resize(size, Image.LANCZOS)
            buf = _io.BytesIO()
            out.save(buf, "WEBP", quality=quality, method=4)
            return buf.getvalue()
    except Exception:                   # 坏图、缺编解码器：当作没做成
        return None


def trim_cache(dirpath, limit, keep=None):
    """缓存目录超过限值就按最旧的先丢。keep 是本次要保住的文件名。"""
    try:
        items = []
        total = 0
        for name in os.listdir(dirpath):
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            items.append((stat.st_mtime, stat.st_size, name))
            total += stat.st_size
        if total <= limit:
            return
        items.sort()
        for _mtime, size, name in items:
            if keep and name == keep:
                continue
            try:
                os.unlink(os.path.join(dirpath, name))
            except OSError:
                continue
            total -= size
            if total <= limit:
                return
    except OSError:
        return
