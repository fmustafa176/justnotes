"""Verify actual QR payload bytes, capacity, and scan-friendly rendering."""

import unittest
from unittest.mock import patch

from qr import QRCodeError, encode_qr, qr_pixels


def decode_version_one(matrix):
    """Read byte-mode version 1 independently, including format/mask decoding."""
    # Format information's low three bits select the data mask (before BCH).
    coords = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(i, 8) for i in range(5, -1, -1)]
    value = sum(int(matrix[y][x]) << i for i, (x, y) in enumerate(coords)) ^ 0x5412
    mask = (value >> 10) & 7
    masks = (lambda x,y:(x+y)%2 == 0, lambda x,y:y%2 == 0,
             lambda x,y:x%3 == 0, lambda x,y:(x+y)%3 == 0,
             lambda x,y:(y//2+x//3)%2 == 0, lambda x,y:x*y%2+x*y%3 == 0,
             lambda x,y:(x*y%2+x*y%3)%2 == 0,
             lambda x,y:((x+y)%2+x*y%3)%2 == 0)
    reserved = {(x,y) for y in range(21) for x in range(21)
                if (x <= 8 and y <= 8) or (x >= 13 and y <= 8)
                or (x <= 8 and y >= 13) or x == 6 or y == 6}
    bits = []
    upward = True
    right = 20
    while right > 0:
        if right == 6:
            right -= 1
        for y in (range(20, -1, -1) if upward else range(21)):
            for x in (right, right - 1):
                if (x,y) not in reserved:
                    bits.append(int(matrix[y][x]) ^ masks[mask](x,y))
        upward = not upward
        right -= 2
    def number(start, count):
        return sum(int(bit) << (count-i-1) for i, bit in enumerate(bits[start:start+count]))
    if number(0, 4) != 4:
        raise AssertionError("Expected QR byte mode")
    count = number(4, 8)
    return bytes(number(12+i*8, 8) for i in range(count))


class QRTests(unittest.TestCase):
    def test_text_payload_round_trips_as_utf8_without_a_web_service(self):
        for text in ("Hello, phone", "*bold*\nline 2", "مرحبا", "hi 🌍", "a\0b"):
            with self.subTest(text=text):
                matrix = encode_qr(text)
                self.assertEqual(len(matrix), 21)
                self.assertEqual(decode_version_one(matrix), text.encode("utf-8"))

    def test_larger_note_selects_larger_version_and_capacity_is_byte_based(self):
        self.assertGreater(len(encode_qr("note " * 100)), 21)
        self.assertEqual(len(encode_qr("a" * 2331)), 177)
        for text in ("a" * 2332, "🌍" * 583):
            with self.assertRaisesRegex(QRCodeError, "too long"):
                encode_qr(text)

    def test_empty_note_and_missing_library_have_useful_messages(self):
        for text in ("", " \n\t"):
            with self.assertRaisesRegex(QRCodeError, "Write something"):
                encode_qr(text)
        with patch("qr.ctypes.CDLL", side_effect=OSError()):
            with self.assertRaisesRegex(QRCodeError, "libqrencode"):
                encode_qr("text")

    def test_render_preserves_modules_with_white_quiet_zone(self):
        matrix = encode_qr("a note")
        pixels, size = qr_pixels(matrix)
        scale = size // (len(matrix)+8)
        self.assertEqual(len(pixels), size*size*3)
        for y in range(size):
            for x in range(size):
                column, row = x//scale-4, y//scale-4
                dark = (0 <= column < len(matrix) and 0 <= row < len(matrix)
                        and matrix[row][column])
                offset = (y*size+x)*3
                self.assertEqual(pixels[offset:offset+3], b'\0\0\0' if dark else b'\xff\xff\xff')
