# SPDX-License-Identifier: GPL-3.0-or-later

"""Format tests run in ordinary Python, without bpy or Blender installed."""

import struct
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

from zpm.format import (
    FormatError,
    Mesh,
    decode,
    encode,
    load,
    map_file,
    same_geometry,
    save,
    snap_positions,
)
from zpm.format.layout import DIRECTORY_ENTRY, FILE_HEADER, SectionKind


def triangle() -> Mesh:
    return Mesh(
        array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0]),
        array("I", [3]),
        array("I", [0, 1, 2]),
        array("I", [0, 1, 1, 2, 2, 0]),
    )


def mixed_mesh() -> Mesh:
    return Mesh(
        array("f", range(24)),
        array("I", [3, 5]),
        array("I", [0, 1, 2, 2, 3, 4, 5, 6]),
        array("I", [1, 0, 2, 1, 7, 6, 0, 2, 6, 2, 2, 3, 3, 4, 4, 5, 5, 6]),
    )


def grid_mesh(side: int = 40) -> Mesh:
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
    return Mesh(positions, array("I", [4] * (len(corners) // 4)), corners, edges)


def real_fixture() -> Mesh:
    root = Path(__file__).resolve().parents[1] / "benchmarks/mesh-core/fixtures"
    data = (root / "repository_obj418.zms").read_bytes()
    v, f, c, _ = struct.unpack_from("<4I", data, 4)
    values = array("I")
    values.frombytes(data[20:])
    positions = array("f")
    positions.frombytes(values[: v * 3].tobytes())
    return Mesh(
        positions,
        values[v * 3 : v * 3 + f],
        values[v * 3 + f : v * 3 + f + c],
        values[v * 3 + f + c :],
    )


def empty_mesh() -> Mesh:
    return Mesh(array("f"), array("I"), array("I"), array("I"))


class CodecTests(unittest.TestCase):
    def assert_round_trip(self, source):
        decoded = decode(encode(source))
        self.assertTrue(same_geometry(snap_positions(source), decoded))

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

    def test_every_truncation_and_trailing_data(self):
        packet = encode(triangle())
        for length in range(len(packet)):
            with self.assertRaises(FormatError):
                decode(packet[:length])
        with self.assertRaises(FormatError):
            decode(packet + b"extra")

    def test_repeated_positions_loose_edges_and_grid(self):
        grid = grid_mesh()
        self.assert_round_trip(grid)
        id_count = len(grid.corners) + len(grid.edges)
        plain_bytes = len(grid.positions) * 4 + id_count * 2
        self.assertLess(len(encode(grid)), plain_bytes / 2)
        twins = array("f", [1, 2, 3] * 200)
        self.assert_round_trip(
            Mesh(twins, array("I"), array("I"), array("I", [0, 199, 5, 3]))
        )
        self.assert_round_trip(mixed_mesh())

    def test_real_fixture_round_trip_and_stable_bytes(self):
        source = real_fixture()
        self.assert_round_trip(source)
        self.assertEqual(encode(source), encode(source))

    def test_snapping_moves_points_barely_and_only_once(self):
        source = real_fixture()
        snapped = snap_positions(source)
        extent = max(
            max(source.positions[axis::3]) - min(source.positions[axis::3])
            for axis in range(3)
        )
        moved = max(
            abs(a - b) for a, b in zip(source.positions, snapped.positions, strict=True)
        )
        self.assertLessEqual(moved, extent / 2**21)
        self.assertEqual(
            snap_positions(snapped).positions.tobytes(), snapped.positions.tobytes()
        )

    def test_shared_positions_stay_shared(self):
        source = real_fixture()
        snapped = snap_positions(source)
        groups = {}
        for vertex in range(source.vertex_count):
            before = tuple(source.positions[vertex * 3 : vertex * 3 + 3])
            after = tuple(snapped.positions[vertex * 3 : vertex * 3 + 3])
            self.assertEqual(groups.setdefault(before, after), after)

    def test_saving_twice_changes_nothing_visible(self):
        first = decode(encode(real_fixture()))
        second = decode(encode(first))
        self.assertTrue(same_geometry(first, second))

    def test_reordered_real_mesh_uses_the_compact_sections(self):
        names = {region.name for region in map_file(encode(real_fixture()))}
        self.assertIn("CORNER_BACK_DISTANCES", names)
        self.assertIn("EDGE_FIRST_USE", names)
        self.assertNotIn("EDGE_CORNERS", names)

    def test_every_byte_is_named_exactly_once(self):
        for mesh in (triangle(), mixed_mesh(), grid_mesh(), empty_mesh()):
            data = encode(mesh)
            at = 0
            for region in map_file(data):
                self.assertEqual(region.offset, at, region.name)
                self.assertGreater(region.length, 0, region.name)
                at += region.length
            self.assertEqual(at, len(data))

    def test_directory_is_labelled_and_in_order(self):
        data = encode(grid_mesh())
        count = FILE_HEADER.unpack_from(data)[2]
        kinds = [
            DIRECTORY_ENTRY.unpack_from(data, FILE_HEADER.size + i * 8)[0]
            for i in range(count)
        ]
        self.assertEqual(kinds, sorted(set(kinds)))
        for kind in kinds:
            SectionKind(kind)

    def test_invalid_files(self):
        source = encode(triangle())
        count = FILE_HEADER.unpack_from(source)[2]
        first_entry = FILE_HEADER.size
        last_section = DIRECTORY_ENTRY.unpack_from(
            source, first_entry + (count - 1) * DIRECTORY_ENTRY.size
        )[2]
        for offset, value in (
            (0, 0),  # magic
            (4, 9),  # version
            (6, 99),  # section count
            (8, 1),  # file length
            (first_entry, 200),  # unknown section kind
            (first_entry + 4, 3),  # section offset
            (len(source) - 1, 1),  # tail
            (last_section, 255),  # column count
        ):
            changed = bytearray(source)
            changed[offset] = value
            with self.subTest(offset=offset), self.assertRaises(FormatError):
                decode(bytes(changed))

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
