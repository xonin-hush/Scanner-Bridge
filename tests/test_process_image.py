import base64
import io
import os

from PIL import Image


def decode_data_uri(uri):
    header, payload = uri.split(",", 1)
    return header, base64.b64decode(payload)


def test_small_image_round_trips_as_png(bridge):
    src = Image.new("RGB", (100, 80), color=(200, 30, 30))

    result = bridge.process_image(src)

    header, payload = decode_data_uri(result)
    assert header == "data:image/png;base64"
    decoded = Image.open(io.BytesIO(payload))
    assert decoded.size == (100, 80)


def test_oversized_image_is_capped_at_2000px(bridge):
    src = Image.new("RGB", (4000, 3000), color=(255, 255, 255))

    result = bridge.process_image(src)

    _, payload = decode_data_uri(result)
    decoded = Image.open(io.BytesIO(payload))
    assert max(decoded.size) == 2000
    assert decoded.size == (2000, 1500)


def test_incompressible_image_falls_back_to_jpeg(bridge):
    # Random noise defeats PNG compression, pushing the payload past 5 MB.
    noise = os.urandom(2000 * 2000 * 3)
    src = Image.frombytes("RGB", (2000, 2000), noise)

    result = bridge.process_image(src)

    header, _ = decode_data_uri(result)
    assert header == "data:image/jpeg;base64"


def test_broken_image_returns_none(bridge):
    assert bridge.process_image(object()) is None
