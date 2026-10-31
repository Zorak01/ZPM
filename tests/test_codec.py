# SPDX-License-Identifier: GPL-3.0-or-later

"""Format tests run in ordinary Python, without bpy or Blender installed."""

import hashlib
import struct
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

from zpm.format import FormatError, Mesh, Profile, decode, encode, load, save
from zpm.format.codec import HEADER


REFERENCE_HASHES = {
    "repository_obj418": (
        "20af93382db2d13150bc2e2bd76cd9daa9e42506f7590209017ad9f10f5064e5"
    ),
    "loose_nonmanifold": (
        "8af07f8f63c83481407f8e0feb565befdd7ac2a91127d1090ec55e9e2ce2f198"
    ),
    "mixed_tri_quad_ngon": (
        "d87b3265e39f268532956eaec6c806559bc667510456c2250df04490a2dd077f"
    ),
}


def triangle() -> Mesh:
    return Mesh(
        array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0]),
        array("I", [3]),
        array("I", [0, 1, 2]),
        array("I", [0, 1, 1, 2, 2, 0]),
    )


class CodecTests(unittest.TestCase):
    def assert_round_trip(self, source):
        for profile in Profile:
            with self.subTest(profile=profile):
                decoded = decode(encode(source, profile=profile))
                self.assertEqual(
                    decoded.positions.tobytes(), source.positions.tobytes()
                )
                self.assertEqual(list(decoded.corners), list(source.corners))
                self.assertEqual(list(decoded.edges), list(source.edges))
                sizes = (
                    [decoded.arity] * decoded.face_count
                    if decoded.arity
                    else list(decoded.face_sizes)
                )
                expected = (
                    [source.arity] * source.face_count
                    if source.arity
                    else list(source.face_sizes)
                )
                self.assertEqual(sizes, expected)

    def test_triangle(self):
        self.assert_round_trip(triangle())

    def test_mixed_faces_and_wire(self):
        mesh = Mesh(
            array("f", range(24)),
            array("I", [3, 4, 5]),
            array("I", [0, 1, 2, 0, 2, 3, 4, 0, 4, 5, 6, 7]),
            array("I", [7, 6, 0, 1]),
        )
        self.assert_round_trip(mesh)

    def test_wire_only_and_empty(self):
        for count in (0, 2):
            self.assert_round_trip(
                Mesh(
                    array("f", [0] * count * 3),
                    array("I"),
                    array("I"),
                    array("I", [1, 0] if count else []),
                )
            )

    def test_exact_float_bits(self):
        mesh = triangle()
        bits = [0x80000000, 0x7FC01234, 0x7F800000, 0xFF800000, 1, 0, 0, 0, 0]
        mesh.positions = array("f")
        mesh.positions.frombytes(struct.pack("=9I", *bits))
        self.assert_round_trip(mesh)

    def test_index_width_boundary(self):
        for count, width in ((65536, 2), (65537, 4)):
            mesh = Mesh(
                array("f", [0]) * count * 3,
                array("I"),
                array("I"),
                array("I", [0, count - 1]),
            )
            result = decode(encode(mesh))
            self.assertEqual(result.edges.itemsize, width)
            self.assertEqual(result.edges[-1], count - 1)

    def test_compact_saves_counts_and_index_bytes(self):
        self.assertEqual(len(encode(triangle())), 64 + 36 + 6 + 12)
        self.assertEqual(
            len(encode(triangle(), profile=Profile.RAW32)), 64 + 36 + 4 + 12 + 24
        )

    def test_every_truncation_and_trailing_data(self):
        packet = encode(triangle())
        for length in range(len(packet)):
            with self.assertRaises(FormatError):
                decode(packet[:length])
        with self.assertRaises(FormatError):
            decode(packet + b"extra")

    def test_direct_truncation_and_trailing_data(self):
        packet = encode(triangle(), profile=Profile.DIRECT)
        for length in range(len(packet)):
            with self.assertRaises(FormatError):
                decode(packet[:length])
        with self.assertRaises(FormatError):
            decode(packet + b"extra")

    def test_direct_repeated_positions_loose_edges_and_grid(self):
        side = 40
        positions = array("f")
        for y in range(side):
            for x in range(side):
                positions.extend((x, -y, 0))
        corners = array("I")
        for y in range(side - 1):
            for x in range(side - 1):
                v = y * side + x
                corners.extend((v, v + 1, v + side + 1, v + side))
        edges = array("I")
        for face in range(0, len(corners), 4):
            for j in range(4):
                a, b = corners[face + j], corners[face + (j + 1) % 4]
                edges.extend((min(a, b), max(a, b)))
        grid = Mesh(positions, array("I", [4] * (len(corners) // 4)), corners, edges)
        self.assert_round_trip(grid)
        packet = encode(grid, profile=Profile.DIRECT)
        self.assertLess(len(packet), len(encode(grid)) / 2)
        twins = array("f", [1, 2, 3] * 200)
        self.assert_round_trip(
            Mesh(twins, array("I"), array("I"), array("I", [0, 199, 5, 3]))
        )
        mixed = Mesh(
            array("f", range(24)),
            array("I", [3, 5]),
            array("I", [0, 1, 2, 2, 3, 4, 5, 6]),
            array("I", [1, 0, 2, 1, 7, 6, 0, 2, 6, 2, 2, 3, 3, 4, 4, 5, 5, 6]),
        )
        self.assert_round_trip(mixed)

    def test_direct_matches_reference_encoder(self):
        """Hashes come from benchmarks/direct-read: both encoders write equal bytes."""
        root = Path(__file__).resolve().parents[1] / "benchmarks/mesh-core/fixtures"
        for name, expected in REFERENCE_HASHES.items():
            with self.subTest(name=name):
                data = (root / f"{name}.zms").read_bytes()
                v, f, c, e = struct.unpack_from("<4I", data, 4)
                values = array("I")
                values.frombytes(data[20:])
                positions = array("f")
                positions.frombytes(values[: v * 3].tobytes())
                source = Mesh(
                    positions,
                    values[v * 3 : v * 3 + f],
                    values[v * 3 + f : v * 3 + f + c],
                    values[v * 3 + f + c :],
                )
                packet = encode(source, profile=Profile.DIRECT)
                self.assertEqual(hashlib.sha256(packet).hexdigest(), expected)
                self.assertEqual(list(decode(packet).edges), list(source.edges))

    def test_invalid_headers(self):
        source = encode(triangle())
        for field, value in (
            (1, 2),
            (2, 0),
            (3, 99),
            (4, 999),
            (6, 1),
            (8, 3),
            (9, 2),
            (10, 2),
            (11, 999),
            (15, 7),
        ):
            header = list(HEADER.unpack_from(source))
            header[field] = value
            with self.subTest(field=field), self.assertRaises(FormatError):
                decode(HEADER.pack(*header) + source[HEADER.size :])
        with self.assertRaises(FormatError):
            decode(b"BAD!" + source[4:])

    def test_invalid_meshes(self):
        mesh = triangle()
        mesh.corners[0] = 99
        with self.assertRaises(FormatError):
            encode(mesh)
        mesh = triangle()
        mesh.face_sizes[0] = 4
        with self.assertRaises(FormatError):
            encode(mesh)

    def test_decode_size_limit(self):
        with self.assertRaises(FormatError):
            decode(encode(triangle()), max_bytes=64)

    def test_atomic_save_and_bounded_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.zpm"
            save(path, triangle())
            original = path.read_bytes()
            self.assertEqual(load(path).vertex_count, 3)
            invalid = triangle()
            invalid.corners[0] = 99
            with self.assertRaises(FormatError):
                save(path, invalid)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(FormatError):
                load(path, max_bytes=64)
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
