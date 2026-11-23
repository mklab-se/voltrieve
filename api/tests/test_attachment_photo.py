import base64
import io

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.attachments import HANDLERS, AttachmentHandler, Part, Rejected, identify
from app.attachments.photo import PhotoHandler, sharpness

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
GIF = b"GIF89a\x01\x00"


def striped(width: int, height: int, stripe: int = 40) -> Image.Image:
    """A made-up picture with stripes and some text, standing in for a sharp photo."""
    image = Image.new("RGB", (width, height), (230, 230, 225))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, stripe * 2):
        draw.rectangle((x, 0, x + stripe, height), fill=(20, 60, 140))
    font = ImageFont.load_default(size=max(24, height // 12))
    for i, line in enumerate(("Parking", "2 h", "8-18")):
        draw.text((width // 8, height // 8 + i * height // 5), line, font=font, fill=(255, 255, 255))
    return image


def sign_picture() -> Image.Image:
    """A made-up street scene with a drawn sign, as seen from a distance."""
    image = Image.new("RGB", (1024, 768), (196, 210, 222))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 560, 1024, 768), fill=(72, 72, 76))
    draw.rectangle((515, 150, 525, 640), fill=(120, 124, 128))
    draw.rounded_rectangle((455, 130, 585, 260), 10, fill=(0, 82, 155), outline="white", width=4)
    draw.text((490, 136), "P", font=ImageFont.load_default(size=100), fill="white")
    draw.rectangle((430, 268, 610, 340), fill="white", outline=(40, 40, 40), width=2)
    draw.text((450, 280), "2 tim 8-18", font=ImageFont.load_default(size=24), fill=(0, 82, 155))
    return image


def to_bytes(image: Image.Image, fmt: str = "JPEG", **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def decode(part: Part) -> Image.Image:
    url = part.content["image_url"]
    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    image.load()
    return image


@pytest.fixture
def sharp_jpeg() -> bytes:
    return to_bytes(striped(1200, 800), quality=90)


@pytest.fixture
def sharp_jpeg_with_exif() -> bytes:
    exif = Image.Exif()
    exif[0x0112] = 6
    exif.get_ifd(0x8825).update({1: "N", 2: (59.0, 19.0, 0.0), 3: "E", 4: (18.0, 3.0, 0.0)})
    data = to_bytes(striped(1200, 800), quality=90, exif=exif)
    assert Image.open(io.BytesIO(data)).getexif()
    return data


@pytest.fixture
def jpeg_4000x3000() -> bytes:
    return to_bytes(striped(4000, 3000, stripe=120), quality=85)


@pytest.fixture
def png_199x800() -> bytes:
    return to_bytes(striped(199, 800), "PNG")


@pytest.fixture
def blurry_jpeg() -> bytes:
    return to_bytes(striped(1200, 800).filter(ImageFilter.BoxBlur(16)), quality=90)


@pytest.fixture
def truncated_jpeg(sharp_jpeg: bytes) -> bytes:
    return sharp_jpeg[: len(sharp_jpeg) // 2]


@pytest.fixture
def png_6000x5000_flat() -> bytes:
    return to_bytes(Image.new("RGB", (6000, 5000), (128, 128, 128)), "PNG")


def test_shared_names_come_from_the_package():
    assert isinstance(HANDLERS[0], PhotoHandler) and AttachmentHandler
    assert Rejected("x", "y").code == "x"


def test_a_blurred_picture_with_a_bright_edge_is_rejected():
    page = Image.new("RGB", (1200, 800), "white")
    ImageDraw.Draw(page).text((200, 300), "Parking 2 h", font=ImageFont.load_default(size=120), fill="black")
    page = page.filter(ImageFilter.BoxBlur(16))
    assert sharpness(Image.open(io.BytesIO(to_bytes(page)))) < 60
    with pytest.raises(Rejected) as exc:
        PhotoHandler().process(to_bytes(page, quality=90), "")
    assert exc.value.code == "too_blurry"


def test_photo_becomes_one_image_part(sharp_jpeg):
    [part] = PhotoHandler().process(sharp_jpeg, "front camera")
    assert part.label == "front camera" and part.content["type"] == "input_image"
    assert part.content["detail"] == "high"
    assert part.content["image_url"].startswith("data:image/jpeg;base64,")


def test_part_size_and_detail_describe_the_processed_jpeg(sharp_jpeg):
    [part] = PhotoHandler().process(sharp_jpeg, "")
    raw = base64.b64decode(part.content["image_url"].split(",", 1)[1])
    assert part.size == len(raw)
    assert part.detail == "1200x800"


def test_photo_is_upright_and_has_no_metadata(sharp_jpeg_with_exif):
    image = decode(PhotoHandler().process(sharp_jpeg_with_exif, "")[0])
    assert image.size == (800, 1200) and not image.getexif()


def test_large_photo_is_shrunk(jpeg_4000x3000):
    [part] = PhotoHandler().process(jpeg_4000x3000, "")
    assert max(decode(part).size) == 1600 and part.detail == "1600x1200"


def test_webp_is_accepted(sharp_jpeg):
    data = to_bytes(Image.open(io.BytesIO(sharp_jpeg)), "WEBP")
    assert PhotoHandler().process(data, "")[0].content["type"] == "input_image"


@pytest.mark.parametrize(
    "data,code",
    [
        ("png_199x800", "too_small"),
        ("blurry_jpeg", "too_blurry"),
        ("truncated_jpeg", "unreadable"),
        ("png_6000x5000_flat", "unreadable"),  # over 25 megapixels
    ],
)
def test_poor_photos_are_rejected(data, code, request):
    with pytest.raises(Rejected) as exc:
        PhotoHandler().process(request.getfixturevalue(data), "")
    assert exc.value.code == code and exc.value.message


def test_oversize_picture_is_rejected_from_the_header(png_6000x5000_flat, monkeypatch):
    loaded = []
    monkeypatch.setattr(Image.Image, "load", lambda self, *a, **k: loaded.append(1))
    with pytest.raises(Rejected) as exc:
        PhotoHandler().process(png_6000x5000_flat, "")
    assert exc.value.code == "unreadable" and not loaded


def test_a_sharp_sign_passes_and_the_same_sign_blurred_is_rejected():
    sign = sign_picture()
    assert PhotoHandler().process(to_bytes(sign, quality=85), "")
    with pytest.raises(Rejected) as exc:
        PhotoHandler().process(to_bytes(sign.filter(ImageFilter.BoxBlur(8)), quality=85), "")
    assert exc.value.code == "too_blurry"


def test_garbage_and_other_formats_are_unreadable(sharp_jpeg):
    gif = to_bytes(striped(400, 300), "GIF")
    for data in (b"not an image", b"", gif):
        with pytest.raises(Rejected) as exc:
            PhotoHandler().process(data, "")
        assert exc.value.code == "unreadable"


def test_decompression_bomb_is_unreadable(monkeypatch, sharp_jpeg):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(Rejected) as exc:
        PhotoHandler().process(sharp_jpeg, "")
    assert exc.value.code == "unreadable"


@pytest.mark.parametrize(
    "head,found",
    [(JPEG, True), (PNG, True), (WEBP, True), (GIF, False), (b"%PDF-1.7", False), (b"", False)],
)
def test_kind_is_decided_from_content(head, found):
    assert (identify(head) is not None) is found


def test_a_new_kind_is_one_handler():
    class Note:
        kind, max_bytes = "note", 1000

        def accepts(self, head):
            return head.startswith(b"NOTE")

        def process(self, data, label):
            return [Part(label, {"type": "input_text", "text": data.decode()}, len(data))]

    assert identify(b"NOTE hi", (*HANDLERS, Note())).kind == "note"
