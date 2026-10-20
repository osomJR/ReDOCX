"""Private JPEG coefficient worker. Run in a child process to isolate codec failures."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys


def redact_blocks(source: str, output: str, boxes: list[list[int]]) -> None:
    import jpeglib
    import numpy as np

    jpeg = jpeglib.read_dct(source)
    jpeg.load()
    sampling = np.asarray(jpeg.samp_factor)
    # read_dct stores each row as (vertical, horizontal).
    max_v, max_h = sampling.max(axis=0)
    components = [jpeg.Y]
    if jpeg.num_components >= 3:
        components.extend([jpeg.Cb, jpeg.Cr])
    if jpeg.num_components == 4:
        components.append(jpeg.K)
    colour = str(jpeg.jpeg_color_space)
    black_dc = {
        "JCS_GRAYSCALE": [-1024],
        "JCS_RGB": [-1024, -1024, -1024],
        "JCS_YCbCr": [-1024, 0, 0],
        # Adobe JPEG stores inverted CMYK components.
        "JCS_CMYK": [1016, 1016, 1016, -1024],
        "JCS_YCCK": [1016, 0, 0, -1024],
    }.get(colour)
    if black_dc is None or len(black_dc) != jpeg.num_components:
        raise ValueError("Unsupported JPEG colour space.")
    for x0, y0, x1, y1 in boxes:
        mx0 = max(0, math.floor(x0 / (8 * max_h)))
        my0 = max(0, math.floor(y0 / (8 * max_v)))
        mx1 = math.ceil(min(jpeg.width, x1) / (8 * max_h))
        my1 = math.ceil(min(jpeg.height, y1) / (8 * max_v))
        for index, component in enumerate(components):
            v, h = sampling[index]
            section = component[my0*v:my1*v, mx0*h:mx1*h]
            section[...] = 0
            quant = float(jpeg.qt[jpeg.quant_tbl_no[index], 0, 0])
            section[..., 0, 0] = round(black_dc[index] / quant)
    jpeg.write_dct(output)
    if not Path(output).is_file():
        raise RuntimeError("JPEG writer did not produce an output.")
    verified = jpeglib.read_dct(output)
    verified.load()
    result = [verified.Y]
    if verified.num_components >= 3:
        result.extend([verified.Cb, verified.Cr])
    if verified.num_components == 4:
        result.append(verified.K)
    if (not np.array_equal(jpeg.qt, verified.qt) or len(components) != len(result)
            or any(not np.array_equal(expected, actual) for expected,actual in zip(components,result))):
        # In particular, never publish a CMYK file whose black channel still
        # carries the original sensitive glyphs, even when it renders black.
        raise RuntimeError("The JPEG codec did not remove every selected component.")


if __name__ == "__main__":
    # Paths/coordinates travel over stdin and never become shell commands.
    request = json.load(sys.stdin)
    redact_blocks(request["source"], request["output"], request["boxes"])
