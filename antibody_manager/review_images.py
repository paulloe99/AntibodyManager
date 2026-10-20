"""Bounded storage copies for review pictures, independent of the storage backend."""
import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_STORED_BYTES = 1024 * 1024
MAX_EDGE = 2048
MAX_PIXELS = 25_000_000


def prepare_image(name, data):
    """Keep small pictures; reduce larger ones to an explicitly named review copy."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Each uploaded image must be at most 10 MB.')
    filename = Path(name.replace('\\', '/')).name
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG', 'TIFF') or image.width * image.height > MAX_PIXELS:
                raise ValueError('Use PNG/JPEG/TIFF images up to 25 megapixels.')
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('For a multi-page TIFF, export the frame you want to show as a separate picture. No frames were discarded or saved.')
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if len(data) <= MAX_STORED_BYTES and max(image.size) <= MAX_EDGE:
                return filename, data
            image = ImageOps.exif_transpose(image)
            if image.mode in ('I', 'F') or image.mode.startswith('I;16'):
                low, high = image.getextrema()
                if high > low:
                    image = image.convert('F').point(lambda v: (v - low) * 255 / (high - low))
            if image.mode == 'RGBA' or 'transparency' in image.info or image.mode == 'LA':
                rgba = image.convert('RGBA')
                image = Image.new('RGB', rgba.size, 'white')
                image.paste(rgba, mask=rgba.getchannel('A'))
            else:
                image = image.convert('RGB')
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            while True:
                for quality in (85, 75, 65):
                    output = io.BytesIO()
                    image.save(output, format='JPEG', quality=quality, optimize=True, progressive=True)
                    compressed = output.getvalue()
                    if len(compressed) <= MAX_STORED_BYTES:
                        return f'{Path(filename).stem}_review.jpg', compressed
                image.thumbnail((max(1, int(image.width * 0.8)), max(1, int(image.height * 0.8))),
                                Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('The uploaded file is not a valid image.') from None
