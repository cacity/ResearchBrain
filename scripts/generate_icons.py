from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "desktop" / "src-tauri" / "icons"


def make_icon(size: int) -> Image.Image:
    scale = size / 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(value * scale) for value in values)

    draw.rounded_rectangle(box((20, 20, 492, 492)), radius=round(104 * scale), fill="#19251f")
    draw.rounded_rectangle(box((96, 87, 416, 425)), radius=round(64 * scale), fill="#8ddfba")
    width = max(2, round(22 * scale))
    ink = "#17231d"
    draw.line(box((256, 157, 256, 357)), fill=ink, width=width)
    draw.line(box((145, 155, 145, 332)), fill=ink, width=width)
    draw.line(box((367, 155, 367, 332)), fill=ink, width=width)
    draw.arc(box((145, 126, 267, 232)), 188, 284, fill=ink, width=width)
    draw.arc(box((245, 126, 367, 232)), 256, 352, fill=ink, width=width)
    draw.arc(box((145, 267, 267, 371)), 76, 172, fill=ink, width=width)
    draw.arc(box((245, 267, 367, 371)), 8, 104, fill=ink, width=width)
    draw.ellipse(box((368, 362, 412, 406)), fill="#f2ad4e")
    return image


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for size, filename in (
        (32, "32x32.png"),
        (128, "128x128.png"),
        (256, "128x128@2x.png"),
        (512, "icon.png"),
    ):
        make_icon(size).save(OUTPUT / filename, optimize=True)
    make_icon(256).save(
        OUTPUT / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
