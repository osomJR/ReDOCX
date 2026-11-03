"""Isolated JPEG worker: replace affected MCU blocks, retain all other DCT data."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile


def _components(jpeg):
    result = [jpeg.Y]
    if jpeg.num_components >= 3:
        result.extend([jpeg.Cb, jpeg.Cr])
    if jpeg.num_components == 4:
        result.append(jpeg.K)
    return result


def mask_blocks(source: str, output: str, replacement: str, boxes) -> None:
    import jpeglib
    import numpy as np
    from PIL import Image, JpegImagePlugin

    jpeg = jpeglib.read_dct(source)
    jpeg.load()
    expected = _components(jpeg)
    sampling = np.asarray(jpeg.samp_factor)
    max_v, max_h = sampling.max(axis=0)

    if boxes:
        with tempfile.TemporaryDirectory(prefix="redocx-mask-coefficients-") as directory:
            candidate_path = Path(directory) / "replacement.jpg"
            with Image.open(source) as original, Image.open(replacement) as pixels:
                if pixels.size != original.size:
                    raise RuntimeError("Replacement JPEG pixels have different dimensions.")
                image = pixels.convert(original.mode)
                image.save(
                    candidate_path, format="JPEG", qtables=original.quantization,
                    subsampling=JpegImagePlugin.get_sampling(original),
                    keep_rgb=str(jpeg.jpeg_color_space) == "JCS_RGB",
                )
            candidate = jpeglib.read_dct(str(candidate_path))
            candidate.load()
            values = _components(candidate)
            if (jpeg.num_components != candidate.num_components
                    or str(jpeg.jpeg_color_space) != str(candidate.jpeg_color_space)
                    or not np.array_equal(sampling, candidate.samp_factor)):
                raise RuntimeError("The JPEG replacement cannot preserve the source colour space.")
            for index in range(jpeg.num_components):
                if not np.array_equal(jpeg.qt[jpeg.quant_tbl_no[index]],
                                      candidate.qt[candidate.quant_tbl_no[index]]):
                    raise RuntimeError("The JPEG replacement changed quantization tables.")
            for x0, y0, x1, y1 in boxes:
                mx0 = max(0, math.floor(x0 / (8 * max_h)))
                my0 = max(0, math.floor(y0 / (8 * max_v)))
                mx1 = math.ceil(min(jpeg.width, x1) / (8 * max_h))
                my1 = math.ceil(min(jpeg.height, y1) / (8 * max_v))
                for index, component in enumerate(expected):
                    v, h = sampling[index]
                    component[my0*v:my1*v, mx0*h:mx1*h] = values[index][my0*v:my1*v, mx0*h:mx1*h]

    jpeg.write_dct(output)
    verified = jpeglib.read_dct(output)
    verified.load()
    actual = _components(verified)
    if (not np.array_equal(jpeg.qt, verified.qt)
            or not np.array_equal(sampling, verified.samp_factor)
            or str(jpeg.jpeg_color_space) != str(verified.jpeg_color_space)
            or len(expected) != len(actual)
            or any(not np.array_equal(left, right) for left, right in zip(expected, actual))):
        Path(output).unlink(missing_ok=True)
        raise RuntimeError("JPEG masking could not verify all stored colour components.")


if __name__ == "__main__":
    request = json.load(sys.stdin)
    try:
        mask_blocks(request["source"], request["output"], request["replacement"], request["boxes"])
    except Exception:
        Path(request["output"]).unlink(missing_ok=True)
        raise
