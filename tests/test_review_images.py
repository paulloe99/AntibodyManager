from array import array
import io
import random

from PIL import Image
import pytest

from antibody_manager.review_images import MAX_EDGE, MAX_STORED_BYTES, prepare_image


def encode(image, format='PNG', **kwargs):
    buffer = io.BytesIO()
    image.save(buffer, format=format, **kwargs)
    return buffer.getvalue()


def test_small_images_remain_byte_identical():
    data = encode(Image.new('RGB', (20, 20), 'red'))
    assert prepare_image('small.png', data) == ('small.png', data)
    data = encode(Image.new('I;16', (20, 20)), 'TIFF')
    assert prepare_image('small.tif', data) == ('small.tif', data)


def test_large_noisy_picture_has_bounded_size_and_dimensions():
    image = Image.frombytes('RGB', (2400, 1400), random.Random(4).randbytes(2400 * 1400 * 3))
    original = encode(image)
    name, stored = prepare_image('noisy.png', original)
    assert name == 'noisy_review.jpg'
    assert len(stored) <= MAX_STORED_BYTES
    assert len(stored) < len(original) / 4
    with Image.open(io.BytesIO(stored)) as reduced:
        assert max(reduced.size) <= MAX_EDGE
        assert abs(reduced.width / reduced.height - 2400 / 1400) < 0.01
        assert reduced.format == 'JPEG'


def test_large_16_bit_tiff_has_viewable_display_range():
    data = array('H', (i % 4096 for i in range(2200 * 500)))
    image = Image.frombytes('I;16', (2200, 500), data.tobytes())
    original = encode(image, 'TIFF')
    name, stored = prepare_image('microscope.tif', original)
    assert name == 'microscope_review.jpg'
    assert len(stored) <= MAX_STORED_BYTES
    with Image.open(io.BytesIO(stored)) as reduced:
        low, high = reduced.convert('L').getextrema()
        assert low < 10 and high > 245
        assert max(reduced.size) <= MAX_EDGE


def test_orientation_and_transparency():
    exif = Image.Exif()
    exif[274] = 6
    original = encode(Image.new('RGB', (3000, 1000), 'red'), 'JPEG', exif=exif)
    _, stored = prepare_image('rotated.jpg', original)
    with Image.open(io.BytesIO(stored)) as reduced:
        assert reduced.height == MAX_EDGE and reduced.width < reduced.height
    original = encode(Image.new('RGBA', (3000, 1000), (0, 0, 0, 0)))
    _, stored = prepare_image('transparent.png', original)
    with Image.open(io.BytesIO(stored)) as reduced:
        assert reduced.getpixel((0, 0)) == (255, 255, 255)


def test_multipage_tiff_is_not_silently_truncated():
    original = encode(Image.new('RGB', (8, 8), 'red'), 'TIFF', save_all=True,
                      append_images=[Image.new('RGB', (8, 8), 'blue')])
    with pytest.raises(ValueError, match='multi-page TIFF'):
        prepare_image('stack.tif', original)


def test_upload_and_pixel_limits():
    with pytest.raises(ValueError, match='10 MB'):
        prepare_image('large.png', b'x' * (10 * 1024 * 1024 + 1))
    data = encode(Image.new('1', (5001, 5000)))
    with pytest.raises(ValueError, match='25 megapixels'):
        prepare_image('large_dimensions.png', data)
