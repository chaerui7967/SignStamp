"""흰 종이에 쓴 서명 사진 → 배경이 투명한 PNG 로 변환."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:
    from scipy import ndimage
except ImportError:  # scipy 가 없으면 잡티 제거만 건너뜀
    ndimage = None

MAX_SIDE = 3000

INK_COLORS = {
    "검정": (20, 20, 20),
    "파랑": (20, 45, 140),
    "원본 색": None,
}


def load_photo(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return img


def _estimate_background(gray: Image.Image) -> np.ndarray:
    """잉크를 지운 종이 밝기 지도(그림자·조명 얼룩 포함)를 추정."""
    w, h = gray.size
    factor = max(1, max(w, h) // 400)
    small = gray.resize((max(1, w // factor), max(1, h // factor)), Image.BILINEAR)
    # 밝은 값으로 팽창 → 어두운 획이 사라지고 종이만 남음
    small = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MaxFilter(9))
    small = small.filter(ImageFilter.GaussianBlur(6))
    bg = np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32)
    return np.maximum(bg, 1.0)


def make_transparent(
    img: Image.Image,
    strength: float = 0.5,
    color: str = "검정",
    despeckle: bool = True,
) -> Image.Image:
    """서명 사진을 투명 배경 RGBA 이미지로 만들어 서명 부분만 잘라 반환.

    strength: 0(옅은 획까지 살림) ~ 1(진한 획만 남김)
    """
    gray = img.convert("L")
    bg = _estimate_background(gray)
    g = np.asarray(gray, dtype=np.float32)

    # 종이 대비 어두운 정도 (0 = 종이, 1 = 완전 검정)
    darkness = np.clip(1.0 - g / bg, 0.0, 1.0)

    lo = 0.06 + 0.24 * strength
    hi = lo + 0.25
    alpha = np.clip((darkness - lo) / (hi - lo), 0.0, 1.0)

    if despeckle and ndimage is not None:
        mask = alpha > 0.3
        labels, n = ndimage.label(mask)
        if n:
            sizes = ndimage.sum(mask, labels, range(1, n + 1))
            min_size = max(15, int(mask.size * 0.00002))
            keep = np.zeros(n + 1, dtype=bool)
            keep[1:] = sizes >= min_size
            # 남긴 덩어리 근처의 반투명 가장자리는 유지
            keep_mask = ndimage.binary_dilation(keep[labels], iterations=2)
            alpha = alpha * keep_mask

    h, w = alpha.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgb = INK_COLORS.get(color)
    if rgb is None:
        # 원본 색: 종이 밝기로 나눠 화이트밸런스를 맞추고 진하게
        src = np.asarray(img, dtype=np.float32)
        norm = np.clip(src / bg[..., None], 0, 1)
        rgba[..., :3] = (norm ** 1.8 * 255).astype(np.uint8)
    else:
        rgba[..., :3] = rgb
    rgba[..., 3] = (alpha * 255).astype(np.uint8)

    out = Image.fromarray(rgba, "RGBA")
    return crop_to_ink(out)


def crop_to_ink(img: Image.Image, pad_ratio: float = 0.03) -> Image.Image:
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.nonzero(a > 20)
    if len(xs) == 0:
        return img
    pad = int(max(img.size) * pad_ratio)
    x0, x1 = max(0, xs.min() - pad), min(img.width, xs.max() + pad + 1)
    y0, y1 = max(0, ys.min() - pad), min(img.height, ys.max() + pad + 1)
    return img.crop((x0, y0, x1, y1))


BOLD_MAX = 5


def thicken(img: Image.Image, level: int) -> Image.Image:
    """획을 굵게 (level 0 = 원본, 클수록 굵게). 투명도는 팽창, 색은 가장 진한 이웃 색으로."""
    if level <= 0:
        return img
    img = img.convert("RGBA")
    steps = level * max(1, round(max(img.size) / 700))
    alpha, rgb = img.getchannel("A"), img.convert("RGB")
    for _ in range(steps):
        alpha = alpha.filter(ImageFilter.MaxFilter(3))
        rgb = rgb.filter(ImageFilter.MinFilter(3))
    out = rgb.convert("RGBA")
    out.putalpha(alpha.filter(ImageFilter.GaussianBlur(0.6)))
    return out


def render_placed(path: str, w: float, h: float, angle: float, bold: int) -> Image.Image:
    """배치 상태(크기 비율·굵기·회전)를 반영한 이미지. 회전하면 캔버스가 회전 외곽 크기로 커진다.

    angle: 시계 방향 각도(도)
    """
    img = thicken(Image.open(path).convert("RGBA"), bold)
    target_h = max(1, round(img.width * h / w))
    if abs(target_h - img.height) > 1:  # 비율을 바꿔 늘린 경우
        img = img.resize((img.width, target_h), Image.LANCZOS)
    if angle % 360:
        img = img.rotate(-angle, resample=Image.BICUBIC, expand=True)
    return img
