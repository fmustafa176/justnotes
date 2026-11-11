"""Local QR encoding through libqrencode; no note text leaves the computer."""

import ctypes


class QRCodeError(ValueError):
    pass


class _QRcode(ctypes.Structure):
    _fields_ = [("version", ctypes.c_int), ("width", ctypes.c_int),
                ("data", ctypes.POINTER(ctypes.c_ubyte))]


def encode_qr(text):
    """Encode UTF-8 bytes with medium error correction and automatic version."""
    if not text.strip():
        raise QRCodeError("Write something in this note first.")
    data = text.encode("utf-8")
    if len(data) > 2331:
        raise QRCodeError("This note is too long for one QR code. Use a shorter note (up to 2,331 UTF-8 bytes).")
    try:
        library = ctypes.CDLL("libqrencode.so.4")
    except OSError as error:
        raise QRCodeError("Install libqrencode to generate QR codes (Ubuntu/Debian: sudo apt install libqrencode4).") from error
    library.QRcode_encodeData.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    library.QRcode_encodeData.restype = ctypes.POINTER(_QRcode)
    library.QRcode_free.argtypes = [ctypes.POINTER(_QRcode)]
    library.QRcode_free.restype = None
    code = library.QRcode_encodeData(len(data), data, 0, 1)
    if not code:
        raise QRCodeError("This note could not fit in one QR code. Try a shorter note.")
    try:
        width = code.contents.width
        return tuple(tuple(bool(code.contents.data[y * width + x] & 1)
                           for x in range(width)) for y in range(width))
    finally:
        library.QRcode_free(code)


def qr_pixels(matrix):
    """Render crisp RGB modules with the required four-module white quiet zone."""
    width = len(matrix)
    scale = max(2, 320 // (width + 8))
    size = (width + 8) * scale
    white = b"\xff\xff\xff"
    black = b"\0\0\0"
    rows = [white * size] * (4 * scale)
    for row in matrix:
        pixels = white * (4 * scale) + b"".join(
            (black if cell else white) * scale for cell in row) + white * (4 * scale)
        rows.extend([pixels] * scale)
    rows.extend([white * size] * (4 * scale))
    return b"".join(rows), size
