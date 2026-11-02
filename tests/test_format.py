# SPDX-License-Identifier: GPL-3.0-or-later

"""Format tests run in ordinary Python, without bpy or Blender installed."""

import math
import struct
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

from zpm.format import (
    Armature,
    Attribute,
    AttributeDomain,
    AttributeMeaning,
    AttributeType,
    Bone,
    Camera,
    Clip,
    Collection,
    ColorLayer,
    Curve,
    Extension,
    FormatError,
    Image,
    ImageTile,
    Lattice,
    Light,
    Material,
    Mesh,
    PoseChannel,
    Reference,
    Sampler,
    Scene,
    SceneObject,
    SceneSpace,
    ShapeInterpolation,
    ShapeTarget,
    SpaceAxis,
    Spline,
    TextureSlot,
    Track,
    decode,
    decode_scene,
    encode,
    encode_scene,
    load,
    map_file,
    same_geometry,
    save,
    snap_mesh,
)
from zpm.format.codec.quantize import decode_normal, encode_normal
from zpm.format.codec.scene_tables import read_indices
from zpm.format.container.layout import (
    DIRECTORY_ENTRY,
    FILE_HEADER,
    NO_INDEX,
    OPTIONAL_KIND_START,
    AlphaMode,
    AreaShape,
    HandleType,
    InheritScale,
    KeyMode,
    LightType,
    LightUnit,
    ObjectFlags,
    ObjectType,
    ParentType,
    ResourceKind,
    RotationMode,
    SamplerFilter,
    SamplerWrap,
    SectionFlags,
    SectionKind,
    SkinFlags,
    SplineFlags,
    SplineType,
    TrackFlags,
    TrackValue,
    align,
)
from zpm.format.container.sections import Section, SectionReader, write_file
from zpm.format.processing.animation import reduce_track
from zpm.format.processing.faces import face_lists
from zpm.format.processing.reordering import reorder_mesh


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


def split_vertex_mesh() -> Mesh:
    source = grid_mesh(12)
    count = source.vertex_count
    positions = array("f", source.positions)
    positions.extend(source.positions)
    corners = array("I")
    normals = array("f")
    edges = array("I")
    for face in range(source.face_count):
        ring = source.corners[face * 4 : face * 4 + 4]
        for vertex in ring:
            corners.append(vertex + face % 2 * count)
            normals.extend(_split_normal(vertex))
        for j in range(4):
            a = ring[j] + face % 2 * count
            b = ring[(j + 1) % 4] + face % 2 * count
            edges.extend((min(a, b), max(a, b)))
    return Mesh(
        positions,
        array("I", [4] * source.face_count),
        corners,
        edges,
        corner_normals=normals,
        has_custom_normals=True,
    )


def _split_normal(vertex: int) -> tuple:
    angle = vertex * 0.03
    return math.sin(angle), 0.0, math.cos(angle)


def summed_weight_mesh() -> Mesh:
    mesh = grid_mesh(8)
    mesh.vertex_groups = ["A", "B", "C"]
    offsets, groups, values = array("I", [0]), array("I"), array("f")
    for vertex in range(mesh.vertex_count):
        share = (vertex % 5 + 1) / 8
        groups.extend((1, 2, 0))
        values.extend((share, 1 - share - 0.125, 0.125))
        offsets.append(len(values))
    mesh.weight_offsets, mesh.weight_groups, mesh.weight_values = (
        offsets,
        groups,
        values,
    )
    return mesh


def shared_map_mesh() -> Mesh:
    mesh = grid_mesh(12)
    first = array("f")
    second = array("f")
    for corner in range(len(mesh.corners)):
        pair = ((corner // 4 % 5) * 0.125, (corner % 4) * 0.125)
        first.extend(pair)
        second.extend((pair[0] + 0.5, pair[1] + 0.25))
    mesh.uv_layers = {"A": first, "B": second}
    mesh.attributes["label"] = Attribute(
        AttributeDomain.POINT,
        AttributeType.STRING,
        1,
        array("I", [v % 2 for v in range(mesh.vertex_count)]),
        strings=["odd", "even"],
    )
    return mesh


def surface_mesh() -> Mesh:
    mesh = grid_mesh(12)
    corners = len(mesh.corners)
    normals = array("f")
    for corner in range(corners):
        angle = corner * 0.01
        normals.extend((math.sin(angle), 0.0, math.cos(angle)))
    mesh.corner_normals = normals
    mesh.has_custom_normals = True
    mesh.uv_layers = {
        "UVMap": array("f", [corner * 0.125 % 1 for corner in range(corners * 2)]),
        "Detail": array("f", [corner * 0.5 for corner in range(corners * 2)]),
    }
    mesh.sharp_edges = array("B", [i % 7 == 0 for i in range(mesh.edge_count)])
    mesh.seam_edges = array("B", [i % 11 == 0 for i in range(mesh.edge_count)])
    mesh.flat_faces = array("B", [i % 3 == 0 for i in range(mesh.face_count)])
    mesh.edge_creases = array("f", [(i % 5) / 4 for i in range(mesh.edge_count)])
    mesh.vertex_creases = array("f", [(i % 3) / 2 for i in range(mesh.vertex_count)])
    return mesh


def authored_mesh() -> Mesh:
    """Materials, per-layer data and marks that only some applications author."""
    mesh = surface_mesh()
    corners = len(mesh.corners)
    mesh.material_slots = ["Skin", "", "Metal"]
    mesh.face_materials = array("I", [(i // 9) % 3 for i in range(mesh.face_count)])
    mesh.uv_seams = {"Detail": array("B", [i % 5 == 0 for i in range(mesh.edge_count)])}
    mesh.tangent_layers = {
        name: array("f", [1.0, 0.0, 0.0, 1.0] * corners) for name in mesh.uv_layers
    }
    mesh.sharp_corners = array("B", [i % 13 == 0 for i in range(corners)])
    mesh.smoothing_groups = array("I", [i // 16 for i in range(corners)])
    mesh.crease_chain_offsets = array("I", [0, 2, 4])
    mesh.crease_chain_vertices = array("I", mesh.edges[:4])
    mesh.color_layers = {
        "Paint": ColorLayer(array("B", [7] * corners * 4), True, True),
        "Heat": ColorLayer(array("f", [0.5] * mesh.vertex_count * 4), False),
    }
    mesh.tangent_method = "MikkTSpace, test"
    mesh.vertex_groups = ["Face"]
    mesh.weight_offsets = array("I", range(mesh.vertex_count + 1))
    mesh.weight_groups = array("I", [0]) * mesh.vertex_count
    mesh.weight_values = array("f", [1.0]) * mesh.vertex_count
    mesh.attributes = attributes_for(mesh)
    mesh.shape_targets = shape_targets_for(mesh)
    return mesh


def attributes_for(mesh: Mesh) -> dict:
    points, edges = mesh.vertex_count, mesh.edge_count
    faces, corners = mesh.face_count, len(mesh.corners)
    return {
        "mask": Attribute(
            AttributeDomain.POINT,
            AttributeType.BOOL,
            1,
            array("B", [i % 3 == 0 for i in range(points)]),
        ),
        "wear": Attribute(
            AttributeDomain.EDGE,
            AttributeType.FLOAT32,
            1,
            array("f", [i * 0.1 for i in range(edges)]),
        ),
        "id": Attribute(
            AttributeDomain.FACE,
            AttributeType.INT32,
            1,
            array("i", [i - 50 for i in range(faces)]),
        ),
        "flow": Attribute(
            AttributeDomain.CORNER,
            AttributeType.FLOAT32,
            3,
            array("f", [i * 0.5 for i in range(corners * 3)]),
            AttributeMeaning.VECTOR,
        ),
        "pair": Attribute(
            AttributeDomain.POINT,
            AttributeType.INT16,
            2,
            array("h", [i - 9 for i in range(points * 2)]),
        ),
        "seed": Attribute(
            AttributeDomain.MESH, AttributeType.UINT32, 1, array("I", [4000000000])
        ),
    }


def shape_targets_for(mesh: Mesh) -> list:
    moved = array("f", mesh.positions)
    for vertex in range(0, mesh.vertex_count, 5):
        moved[vertex * 3 + 2] += 0.5
    normals = array("f", [0.0]) * len(mesh.positions)
    normals[0] = 0.25
    return [
        ShapeTarget("Basis", array("f", mesh.positions)),
        ShapeTarget(
            "Smile",
            moved,
            value=0.5,
            minimum=-1.0,
            maximum=2.0,
            vertex_group="Face",
            normal_offsets=normals,
            tangent_offsets=array("f", normals),
        ),
        ShapeTarget(
            "HalfSmile",
            array("f", moved),
            is_relative=False,
            frame=10.0,
            is_muted=True,
            interpolation=ShapeInterpolation.BSPLINE,
            in_between_of=1,
            in_between_weight=0.5,
        ),
    ]


def holed_mesh() -> Mesh:
    """A square with a triangular hole, beside a plain triangle."""
    positions = [0, 0, 0, 4, 0, 0, 4, 4, 0, 0, 4, 0, 1, 1, 0, 1, 3, 0, 3, 3, 0, 9, 9, 0]
    return Mesh(
        array("f", positions),
        array("I", [7, 3]),
        array("I", [0, 1, 2, 3, 4, 5, 6, 0, 1, 7]),
        array("I", [0, 1, 1, 2, 2, 3, 0, 3, 4, 5, 5, 6, 4, 6, 1, 7, 0, 7]),
        corner_normals=array("f", [0.0, 0.0, 1.0] * 10),
        hole_starts=array("I", [4]),
    )


def strip_mesh() -> Mesh:
    """Enough triangles to fill several strip blocks, with a far jump and a flip."""
    side = 24
    positions = array("f")
    for y in range(side):
        for x in range(side):
            positions.extend((x, y, (x * y) % 5))
    corners = array("I")
    for y in range(side - 1):
        for x in range(side - 1):
            v = y * side + x
            corners.extend((v, v + 1, v + side + 1, v, v + side + 1, v + side))
    edges = set()
    for at in range(0, len(corners), 3):
        for j in range(3):
            a, b = corners[at + j], corners[at + (j + 1) % 3]
            edges.add((min(a, b), max(a, b)))
    return Mesh(
        positions,
        array("I"),
        corners,
        array("I", [v for edge in sorted(edges) for v in edge]),
        arity=3,
    )


def quad_and_triangle_mesh() -> Mesh:
    """Quads and triangles side by side, with a few larger faces."""
    side = 20
    positions = array("f")
    for y in range(side):
        for x in range(side):
            positions.extend((x, y, (x * y) % 3))
    sizes = array("I")
    corners = array("I")
    edges = set()
    for y in range(side - 1):
        for x in range(side - 1):
            v = y * side + x
            quad = (v, v + 1, v + side + 1, v + side)
            faces = (
                [quad[:3], (quad[0], quad[2], quad[3])] if (x + y) % 3 == 0 else [quad]
            )
            for face in faces:
                sizes.append(len(face))
                corners.extend(face)
                for at, vertex in enumerate(face):
                    other = face[(at + 1) % len(face)]
                    edges.add((min(vertex, other), max(vertex, other)))
    return Mesh(
        positions,
        sizes,
        corners,
        array("I", [v for edge in sorted(edges) for v in edge]),
    )


def uv_fit_mesh() -> Mesh:
    """UVs that follow position, with some corners that disagree."""
    mesh = grid_mesh(40)
    rows = mesh.positions
    values = array("f")
    for corner, vertex in enumerate(mesh.corners):
        u = rows[vertex * 3] * 0.0125
        v = rows[vertex * 3 + 1] * -0.0125
        values.extend((u + 0.5, v) if corner % 97 == 0 else (u, v))
    mesh.uv_layers = {"Flat": values}
    return mesh


def empty_mesh() -> Mesh:
    return Mesh(array("f"), array("I"), array("I"), array("I"))


class CodecTests(unittest.TestCase):
    def assert_round_trip(self, source):
        decoded = decode(encode(source))
        self.assertTrue(same_geometry(snap_mesh(source), decoded))

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
        snapped = snap_mesh(source)
        extent = max(
            max(source.positions[axis::3]) - min(source.positions[axis::3])
            for axis in range(3)
        )
        moved = max(
            abs(a - b) for a, b in zip(source.positions, snapped.positions, strict=True)
        )
        self.assertLessEqual(moved, extent / 2**21)
        self.assertEqual(
            snap_mesh(snapped).positions.tobytes(), snapped.positions.tobytes()
        )

    def test_shared_positions_stay_shared(self):
        source = real_fixture()
        snapped = snap_mesh(source)
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
        self.assertTrue(names & {"CORNER_STRIP_BITS", "CORNER_BACK_DISTANCES"})
        self.assertIn("EDGE_FIRST_USE", names)
        self.assertNotIn("EDGE_CORNERS", names)

    def test_split_vertices_share_a_normal_through_their_position(self):
        mesh = split_vertex_mesh()
        data = encode(mesh)
        regions = {region.name: region for region in map_file(data)}
        self.assertTrue(regions["NORMAL_VERTEX_A"].flags & SectionFlags.BY_POSITION)
        again = decode(data)
        self.assertEqual(again.vertex_count, mesh.vertex_count)
        for corner, vertex in enumerate(again.corners):
            x, y = again.positions[vertex * 3], again.positions[vertex * 3 + 1]
            expected = _split_normal(round(x) - round(y) * 12)
            self.assertAlmostEqual(
                again.corner_normals[corner * 3 + 2], expected[2], delta=0.003
            )

    def test_string_attributes_round_trip_and_follow_reordering(self):
        mesh = grid_mesh(6)
        labels = ["left", "right", "\u00e9t\u00e9"]
        mesh.attributes["label"] = Attribute(
            AttributeDomain.POINT,
            AttributeType.STRING,
            1,
            array("I", [v % 3 for v in range(mesh.vertex_count)]),
            strings=labels,
        )
        expected = {
            tuple(mesh.positions[v * 3 : v * 3 + 3]): labels[v % 3]
            for v in range(mesh.vertex_count)
        }
        again = decode(encode(mesh))
        stored = again.attributes["label"]
        self.assertEqual(stored.value_type, AttributeType.STRING)
        for vertex, index in enumerate(stored.values):
            at = tuple(again.positions[vertex * 3 : vertex * 3 + 3])
            self.assertEqual(stored.strings[index], expected[at])

    def test_string_attribute_naming_a_missing_string_is_refused(self):
        mesh = grid_mesh(3)
        mesh.attributes["label"] = Attribute(
            AttributeDomain.FACE,
            AttributeType.STRING,
            1,
            array("I", [5] * mesh.face_count),
            strings=["only"],
        )
        with self.assertRaises(FormatError):
            encode(mesh)

    def test_a_layer_splitting_like_the_first_shares_its_map(self):
        data = encode(shared_map_mesh())
        names = {region.name for region in map_file(data)}
        self.assertIn("UV_REPEAT_MASK", names)
        self.assertNotIn("UV_REPEAT_MASK[1]", names)
        again = decode(data)
        for a, b in zip(again.uv_layers["A"], again.uv_layers["B"], strict=True):
            self.assertIn(round(b - a, 4), (0.5, 0.25))

    def test_sparse_tracks_store_only_what_changed(self):
        track = Track(
            ResourceKind.SCENE,
            0,
            "weights",
            [1.0, 2.0, 3.0],
            [1, 2, 3, 9, 7],
            value_type=TrackValue.INT,
            components=3,
            changed=[True, True, True, False, True, False, False, False, True],
        )
        scene = Scene(clips=[Clip("Morph", tracks=[track])])
        stored = decode_scene(encode_scene(scene)).clips[0].tracks[0]
        self.assertEqual(stored.changed, track.changed)
        self.assertEqual(stored.expanded_values(), [1, 2, 3, 1, 9, 3, 1, 9, 7])
        track.changed[0] = False
        with self.assertRaises(FormatError):
            encode_scene(scene)

    def test_bakes_and_image_variants_round_trip_and_are_checked(self):
        bake = {"role": "base_color", "image": 1, "settings": {"samples": 64}}
        variant = {"image": 1, "format": "ktx2", "policy": {"quality": 128}}
        scene = Scene(
            materials=[Material("Skin", bakes=[bake])],
            images=[Image("Source", variants=[variant]), Image("Baked")],
        )
        stored = decode_scene(encode_scene(scene))
        self.assertEqual(stored.materials[0].bakes, [bake])
        self.assertEqual(stored.images[0].variants, [variant])
        scene.images[0].variants = [{"image": 9, "format": "ktx2", "policy": {}}]
        with self.assertRaises(FormatError):
            encode_scene(scene)

    def test_keys_naming_a_mesh_follow_it_when_repeats_are_removed(self):
        scene = Scene(
            meshes=[triangle(), triangle(), grid_mesh(3)],
            objects=[SceneObject("A", ObjectType.MESH, 2)],
            clips=[
                Clip(
                    "Swap",
                    tracks=[
                        Track(
                            ResourceKind.OBJECT,
                            0,
                            "data",
                            [1.0, 2.0],
                            [1, 2],
                            value_type=TrackValue.REFERENCE,
                            target_kind=ResourceKind.MESH,
                        )
                    ],
                )
            ],
        )
        stored = decode_scene(encode_scene(scene))
        self.assertEqual(len(stored.meshes), 2)
        self.assertEqual(stored.clips[0].tracks[0].values, [0, 1])
        self.assertEqual(stored.objects[0].data, 1)

    def test_a_vertex_parent_keeps_its_mesh_in_vertex_order(self):
        mesh = real_fixture()
        scene = Scene(
            meshes=[mesh],
            objects=[
                SceneObject("Base", ObjectType.MESH, 0),
                SceneObject(
                    "Follower",
                    ObjectType.EMPTY,
                    NO_INDEX,
                    0,
                    parent_type=ParentType.VERTEX,
                    parent_vertices=(5, 0, 0),
                ),
            ],
        )
        stored = decode_scene(encode_scene(scene)).meshes[0]
        expected = snap_mesh(mesh)
        self.assertEqual(
            stored.positions[15:18].tobytes(), expected.positions[15:18].tobytes()
        )

    def test_weights_adding_up_to_one_store_no_last_value(self):
        mesh = summed_weight_mesh()
        data = encode(mesh)
        regions = {region.name: region for region in map_file(data)}
        self.assertTrue(regions["WEIGHT_VALUES"].flags & SectionFlags.WEIGHT_SUM)
        again = decode(data)
        for vertex in range(again.vertex_count):
            start, end = again.weight_offsets[vertex], again.weight_offsets[vertex + 1]
            row = again.weight_values[start:end]
            self.assertAlmostEqual(sum(row), 1.0, places=5)
            self.assertEqual(list(row), sorted(row, reverse=True))
        self.assertTrue(same_geometry(snap_mesh(mesh), again))

    def test_weights_not_adding_up_to_one_stay_as_they_are(self):
        mesh = grid_mesh(3)
        mesh.vertex_groups = ["A", "B"]
        mesh.weight_offsets = array("I", [v * 2 for v in range(mesh.vertex_count + 1)])
        mesh.weight_groups = array("I", [1, 0] * mesh.vertex_count)
        mesh.weight_values = array("f", [0.25, 0.5] * mesh.vertex_count)
        again = decode(encode(mesh))
        self.assertEqual(list(again.weight_groups[:2]), [1, 0])
        self.assertAlmostEqual(again.weight_values[1], 0.5, places=4)

    def test_surface_data_round_trip_and_follows_reordering(self):
        source = surface_mesh()
        decoded = decode(encode(source))
        self.assertTrue(same_geometry(snap_mesh(source), decoded))
        self.assertEqual(sorted(decoded.uv_layers), ["Detail", "UVMap"])
        self.assertTrue(decoded.has_custom_normals)
        self.assertEqual(sum(decoded.sharp_edges), sum(source.sharp_edges))

    def test_flat_normals_are_calculated_not_stored(self):
        mesh = grid_mesh(20)
        faces = len(mesh.corners)
        mesh.corner_normals = array("f", [0.0, 0.0, -1.0] * faces)
        names = {region.name for region in map_file(encode(mesh))}
        self.assertFalse(any(name.startswith("NORMAL_") for name in names))
        decoded = decode(encode(mesh))
        self.assertEqual(list(decoded.corner_normals[:3]), [0.0, 0.0, -1.0])
        mesh.corner_normals = array("f", [0.0, 0.6, -0.8] * faces)
        names = {region.name for region in map_file(encode(mesh))}
        self.assertTrue(any(name.startswith("NORMAL_") for name in names))
        mesh.corner_normals = array("f", [0.0, 0.0, -1.0] * faces)
        mesh.has_custom_normals = True
        names = {region.name for region in map_file(encode(mesh))}
        self.assertTrue(any(name.startswith("NORMAL_") for name in names))

    def test_uvs_that_follow_position_are_predicted_from_it(self):
        mesh = grid_mesh(40)
        rows = mesh.positions
        mesh.uv_layers = {
            "Flat": array(
                "f",
                [
                    part
                    for vertex in mesh.corners
                    for part in (
                        rows[vertex * 3] * 0.0125,
                        rows[vertex * 3 + 1] * -0.0125,
                    )
                ],
            )
        }
        data = encode(mesh)
        names = {region.name for region in map_file(data)}
        self.assertIn("UV_FIT_A_X", names)
        self.assertTrue(same_geometry(snap_mesh(mesh), decode(data)))

    def test_corners_one_step_apart_share_a_normal(self):
        mesh = triangle()
        mesh.corners = array("I", [0, 1, 2, 0, 2, 1])
        mesh.face_sizes = array("I", [3, 3])
        close = decode_normal(2000, 2000)
        nudged = decode_normal(2001, 2000)
        far = decode_normal(2100, 2000)
        mesh.corner_normals = array(
            "f", [*close, *close, *close, *nudged, *far, *close]
        )
        snapped = snap_mesh(mesh).corner_normals
        self.assertEqual(snapped[0:3], snapped[9:12])
        self.assertNotEqual(snapped[6:9], snapped[12:15])
        again = snap_mesh(snap_mesh(mesh)).corner_normals
        self.assertEqual(snapped.tobytes(), again.tobytes())

    def test_quads_and_mixed_faces_can_use_strips(self):
        mesh = quad_and_triangle_mesh()
        data = encode(mesh)
        names = {region.name for region in map_file(data)}
        self.assertIn("CORNER_STRIP_BITS", names)
        self.assertTrue(same_geometry(snap_mesh(mesh), decode(data)))

    def test_strips_keep_winding_when_they_turn_a_face(self):
        mesh = strip_mesh()
        decoded = decode(encode(mesh))
        wound = {
            min(tuple(face[at:] + face[:at]) for at in range(3))
            for face in (
                [tuple(mesh.positions[v * 3 : v * 3 + 3]) for v in f]
                for f in face_lists(snap_mesh(mesh))
            )
        }
        result = {
            min(tuple(face[at:] + face[:at]) for at in range(3))
            for face in (
                [tuple(decoded.positions[v * 3 : v * 3 + 3]) for v in f]
                for f in face_lists(decoded)
            )
        }
        self.assertEqual(wound, result)

    def test_triangles_can_use_strips_and_read_back_exactly(self):
        mesh = strip_mesh()
        data = encode(mesh)
        names = {region.name for region in map_file(data)}
        self.assertIn("CORNER_STRIP_BITS", names)
        self.assertTrue(same_geometry(snap_mesh(mesh), decode(data)))

    def test_faces_with_no_area_do_not_stop_flat_normals(self):
        mesh = Mesh(
            array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0, 2, 0, 0, 3, 0, 0, 4, 0, 0]),
            array("I", [3, 3]),
            array("I", [0, 1, 2, 3, 4, 5]),
            array("I", [0, 1, 1, 2, 0, 2, 3, 4, 4, 5, 3, 5]),
            corner_normals=array("f", [0.0, 0.0, 1.0] * 6),
        )
        data = encode(mesh)
        names = {region.name for region in map_file(data)}
        self.assertFalse(any(name.startswith("NORMAL_") for name in names))
        self.assertEqual(len(decode(data).corner_normals), 18)

    def test_colors_tangents_and_weights_round_trip(self):
        source = surface_mesh()
        source.color_layers = {
            "Paint": ColorLayer(
                array("B", [i % 256 for i in range(len(source.corners) * 4)]), True
            ),
            "Heat": ColorLayer(
                array("f", [i * 0.5 for i in range(source.vertex_count * 4)]), False
            ),
        }
        source.tangent_layers = {
            "UVMap": array(
                "f",
                [
                    part
                    for i in range(len(source.corners))
                    for part in (1.0, 0.0, 0.0, -1.0 if i % 2 else 1.0)
                ],
            )
        }
        source.vertex_groups = ["Head", "Arm"]
        offsets = array("I", [0])
        groups = array("I")
        values = array("f")
        for vertex in range(source.vertex_count):
            for influence in range(vertex % 3):
                groups.append(influence % 2)
                values.append(0.25 * (influence + 1))
            offsets.append(len(values))
        source.weight_offsets = offsets
        source.weight_groups = groups
        source.weight_values = values
        decoded = decode(encode(source))
        self.assertTrue(same_geometry(snap_mesh(source), decoded))
        self.assertEqual(decoded.color_layers["Paint"].values.typecode, "B")
        self.assertTrue(decoded.color_layers["Heat"].is_float)
        self.assertEqual(decoded.vertex_groups, ["Head", "Arm"])
        self.assertEqual(len(decoded.weight_values), len(values))

    def test_identical_weights_compress_and_still_read(self):
        mesh = grid_mesh(60)
        mesh.vertex_groups = ["All"]
        mesh.weight_offsets = array("I", range(mesh.vertex_count + 1))
        mesh.weight_groups = array("I", [0]) * mesh.vertex_count
        mesh.weight_values = array("f", [1.0]) * mesh.vertex_count
        decoded = decode(encode(mesh))
        self.assertEqual(len(decoded.weight_values), mesh.vertex_count)
        self.assertEqual(set(decoded.weight_values), {1.0})

    def test_weights_without_groups_are_refused(self):
        mesh = triangle()
        mesh.weight_offsets = array("I", [0, 1, 1, 1])
        mesh.weight_groups = array("I", [0])
        mesh.weight_values = array("f", [1.0])
        with self.assertRaises(FormatError):
            encode(mesh)

    def test_materials_and_authored_marks_round_trip(self):
        source = authored_mesh()
        decoded = decode(encode(source))
        self.assertTrue(same_geometry(snap_mesh(source), decoded))
        self.assertEqual(decoded.material_slots, ["Skin", "", "Metal"])
        self.assertEqual(sorted(decoded.tangent_layers), ["Detail", "UVMap"])
        self.assertTrue(decoded.color_layers["Paint"].is_srgb)
        self.assertFalse(decoded.color_layers["Heat"].is_srgb)

    def test_reordering_keeps_each_material_together(self):
        source = grid_mesh(40)
        source.material_slots = ["A", "B"]
        source.face_materials = array("I", [i % 2 for i in range(source.face_count)])
        slots = reorder_mesh(snap_mesh(source)).face_materials
        changes = sum(a != b for a, b in zip(slots, slots[1:], strict=False))
        self.assertEqual(changes, 1)

    def test_seams_matching_the_mesh_store_nothing(self):
        source = surface_mesh()
        source.uv_seams = {"Detail": array("B", source.seam_edges)}
        names = {region.name for region in map_file(encode(source))}
        self.assertFalse(any(name.startswith("UV_SEAMS") for name in names))
        self.assertEqual(decode(encode(source)).uv_seams, {})

    def test_holes_round_trip_with_stored_normals_and_endpoint_edges(self):
        source = holed_mesh()
        data = encode(source)
        decoded = decode(data)
        self.assertTrue(same_geometry(snap_mesh(source), decoded))
        self.assertEqual(len(decoded.hole_starts), 1)
        names = {region.name for region in map_file(data)}
        self.assertIn("EDGE_FIRST", names)
        self.assertTrue(any(name.startswith("NORMAL_") for name in names))

    def test_invalid_rings_chains_and_slots_are_refused(self):
        too_few = holed_mesh()
        too_few.hole_starts = array("I", [5])
        at_face_start = holed_mesh()
        at_face_start.hole_starts = array("I", [0])
        outside = holed_mesh()
        outside.hole_starts = array("I", [4, 99])
        no_edge = grid_mesh(4)
        no_edge.crease_chain_offsets = array("I", [0, 2])
        no_edge.crease_chain_vertices = array("I", [0, 15])
        no_slot = triangle()
        no_slot.face_materials = array("I", [1])
        for mesh in (too_few, at_face_start, outside, no_edge, no_slot):
            with self.assertRaises(FormatError):
                encode(mesh)

    def test_attributes_stay_exact_for_every_bit_pattern(self):
        source = triangle()
        bits = array("I", [0x80000000, 0x7FC01234, 0xFF800000])
        values = array("f")
        values.frombytes(bits.tobytes())
        source.attributes = {
            "odd": Attribute(AttributeDomain.POINT, AttributeType.FLOAT32, 1, values)
        }
        decoded = decode(encode(source)).attributes["odd"].values
        self.assertEqual(sorted(decoded.tobytes()), sorted(values.tobytes()))

    def test_targets_store_only_what_moved_and_come_back_exact(self):
        source = grid_mesh(40)
        moved = array("f", source.positions)
        moved[2] = 0.75
        source.shape_targets = [
            ShapeTarget("Basis", array("f", source.positions)),
            ShapeTarget("Bump", moved),
        ]
        snapped = snap_mesh(source)
        data = encode(source)
        decoded = decode(data)
        self.assertTrue(same_geometry(snapped, decoded))
        regions = {r.name: r.length for r in map_file(data)}
        self.assertLess(regions["SHAPE_VERTICES"], 32)

    def test_targets_without_a_grid_keep_their_bits(self):
        source = triangle()
        moved = array("f", source.positions)
        moved[0] = float("inf")
        source.shape_targets = [ShapeTarget("Far", moved)]
        decoded = decode(encode(source))
        self.assertEqual(decoded.shape_targets[0].positions[0], float("inf"))

    def test_invalid_attributes_and_targets_are_refused(self):
        wrong_size = triangle()
        wrong_size.attributes = {
            "x": Attribute(
                AttributeDomain.POINT, AttributeType.INT32, 1, array("i", [1])
            )
        }
        wrong_code = triangle()
        wrong_code.attributes = {
            "x": Attribute(
                AttributeDomain.MESH, AttributeType.INT32, 1, array("f", [1.0])
            )
        }
        wrong_target = triangle()
        wrong_target.shape_targets = [ShapeTarget("x", array("f", [1.0]))]
        missing_reference = triangle()
        missing_reference.shape_targets = [
            ShapeTarget("x", array("f", missing_reference.positions), reference=3)
        ]
        for mesh in (wrong_size, wrong_code, wrong_target, missing_reference):
            with self.assertRaises(FormatError):
                encode(mesh)

    def test_normal_error_stays_tiny(self):
        worst = 0.0
        for i in range(2000):
            a, b = i * 0.0031, i * 0.0177
            n = (math.sin(a) * math.cos(b), math.sin(a) * math.sin(b), math.cos(a))
            back = decode_normal(*encode_normal(*n))
            dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(n, back, strict=True))))
            worst = max(worst, math.degrees(math.acos(dot)))
        self.assertLess(worst, 0.1)

    def test_snapping_surface_twice_changes_nothing(self):
        once = snap_mesh(surface_mesh())
        self.assertTrue(same_geometry(once, snap_mesh(once)))

    def test_every_byte_is_named_exactly_once(self):
        meshes = (
            triangle(),
            mixed_mesh(),
            grid_mesh(),
            empty_mesh(),
            surface_mesh(),
            authored_mesh(),
            holed_mesh(),
        )
        for mesh in meshes:
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
            DIRECTORY_ENTRY.unpack_from(
                data, FILE_HEADER.size + i * DIRECTORY_ENTRY.size
            )[0]
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
        )[3]
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
            save(path, Scene(meshes=[triangle()]))
            original = path.read_bytes()
            self.assertEqual(load(path).meshes[0].vertex_count, 3)
            invalid = triangle()
            invalid.corners[0] = 99
            with self.assertRaises(FormatError):
                save(path, Scene(meshes=[invalid]))
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(FormatError):
                load(path, max_bytes=64)
            self.assertEqual(list(path.parent.iterdir()), [path])


def _value_at(track: Track, time: float) -> float:
    for at in range(len(track.times) - 1):
        start, end = track.times[at], track.times[at + 1]
        if start <= time <= end:
            part = (time - start) / (end - start)
            return track.values[at] + (track.values[at + 1] - track.values[at]) * part
    raise AssertionError("Time outside the track")


def sample_scene() -> Scene:
    """One of everything scene files hold, with values that survive as Float32."""
    shared = b"encoded image bytes"
    mesh = grid_mesh(6)
    mesh.material_slots = ["Skin"]
    mesh.vertex_groups = ["Hip", "Spine"]
    mesh.weight_offsets = array("I", [0] * (mesh.vertex_count + 1))
    return Scene(
        SceneSpace(0.5, SpaceAxis.Y, SpaceAxis.NEG_Z),
        [mesh, triangle()],
        [
            Material(
                "Skin",
                (0.5, 0.25, 0.125, 0.5),
                0.25,
                0.75,
                (1.0, 0.5, 0.25),
                2.0,
                0.5,
                0.25,
                AlphaMode.CUTOFF,
                False,
                [
                    TextureSlot("base_color", 0, 0, "UVMap"),
                    TextureSlot(
                        "clearcoat normal", 1, 1, "", (0.5, 0.25), 0.5, (2.0, 4.0)
                    ),
                ],
            ),
            Material(
                "Glass",
                base_color=(0.5, 0.5, 0.5, 1.0),
                model="glass",
                parameters={"ior": 1.5, "tint": [0.5, 0.25, 0.125]},
            ),
        ],
        [
            Sampler(),
            Sampler(SamplerFilter.NEAREST, SamplerWrap.CLIP, SamplerWrap.MIRROR),
        ],
        [
            Image(
                "Albedo", "sRGB", 64, 32, 4, True, [ImageTile(0, "albedo.png", shared)]
            ),
            Image(
                "Normals",
                "Non-Color",
                8,
                8,
                3,
                False,
                [
                    ImageTile(1001, "n.1001.png", shared),
                    ImageTile(1002, "n.1002.png"),
                ],
            ),
        ],
        [
            SceneObject(
                "Rig",
                ObjectType.ARMATURE,
                0,
                pose=[
                    PoseChannel(
                        1,
                        (0.5, 0.0, 0.0),
                        (0.5, 0.25, 0.0, 0.0),
                        (2.0, 2.0, 2.0),
                        RotationMode.XYZ,
                    )
                ],
            ),
            SceneObject(
                "Body",
                ObjectType.MESH,
                0,
                parent=0,
                parent_bone=1,
                parent_type=ParentType.BONE,
                skin_armature=0,
                skin_flags=SkinFlags.VERTEX_GROUPS,
                skin_bones=[0, NO_INDEX],
            ),
            SceneObject("Copy A", ObjectType.MESH, 1, location=(1.0, 2.0, 3.0)),
            SceneObject(
                "Copy B",
                ObjectType.MESH,
                1,
                scale=(2.0, 0.5, 1.0),
                rotation_mode=RotationMode.ZYX,
                rotation=(0.5, 0.25, 0.125, 0.0),
                flags=ObjectFlags.HIDDEN_IN_RENDER,
            ),
            SceneObject(
                "Lamp",
                ObjectType.LIGHT,
                0,
                properties={"note": "key", "level": 3, "on": True},
                settings={"pass_index": 3},
                material_overrides=[{"slot": 0, "material": 1}],
                parent=1,
                parent_type=ParentType.VERTEX_3,
                parent_vertices=(4, 5, 6),
                modifiers=[
                    {"name": "Mod", "type": "X", "settings": {"object": Reference(0)}}
                ],
                constraints=[{"name": "C", "type": "COPY_LOCATION", "settings": {}}],
                drivers=[{"path": "location", "index": 0, "expression": "var*2"}],
            ),
            SceneObject("Line", ObjectType.CURVE, 0),
            SceneObject("Cage", ObjectType.LATTICE, 0),
            SceneObject("Eye", ObjectType.CAMERA, 0, properties={"lens": [50.0, 35.0]}),
            SceneObject("Set", ObjectType.EMPTY, instance_collection=1),
        ],
        [Collection("Props", NO_INDEX, [2, 3]), Collection("Inner", 0, [3, 4])],
        [
            Light(
                "Key",
                LightType.AREA,
                (1.0, 0.5, 0.25),
                100.0,
                LightUnit.WATTS,
                False,
                0.5,
                0.0,
                0.0,
                0.0,
                AreaShape.RECTANGLE,
                2.0,
                4.0,
            )
        ],
        [
            Armature(
                "Skeleton",
                [
                    Bone(
                        "Hip",
                        NO_INDEX,
                        (0.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0),
                        head_radius=0.25,
                        tail_radius=0.125,
                    ),
                    Bone(
                        "Spine",
                        0,
                        (0.0, 0.0, 1.0),
                        (0.0, 0.0, 2.0),
                        0.5,
                        head_radius=0.25,
                        tail_radius=0.125,
                        is_connected=True,
                        inherit_scale=InheritScale.AVERAGE,
                        settings={"bbone_x": 0.5},
                    ),
                ],
            )
        ],
        [
            Curve(
                "Road",
                [
                    Spline(
                        SplineType.BEZIER,
                        SplineFlags.CYCLIC,
                        4,
                        12,
                        [0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                        [1.0, 1.0],
                        [1.0, 0.5],
                        [0.0, 0.25],
                        [-0.5, 0.0, 0.0, 0.5, 0.0, 0.0],
                        [0.5, 0.0, 0.0, 1.5, 0.0, 0.0],
                        [
                            HandleType.AUTO,
                            HandleType.AUTO,
                            HandleType.FREE,
                            HandleType.VECTOR,
                        ],
                    ),
                    Spline(
                        SplineType.NURBS,
                        SplineFlags.ENDPOINT,
                        3,
                        6,
                        [0.0, 1.0, 0.0, 0.0, 2.0, 0.0, 0.0, 3.0, 0.0],
                        [1.0, 2.0, 1.0],
                        [1.0, 1.0, 1.0],
                        [0.0, 0.0, 0.0],
                        knots=[0.0, 0.0, 0.5, 1.0, 1.0],
                    ),
                ],
                {"bevel_depth": 0.25},
            )
        ],
        [
            Lattice(
                "Box",
                2,
                1,
                1,
                [-0.5, 0.0, 0.0, 0.5, 0.0, 0.0],
                [-0.5, 0.25, 0.0, 0.5, 0.0, 0.0],
                [1.0, 0.5],
                ["Top"],
                [0, 1, 1],
                [0],
                [0.5],
                {"interpolation_type_u": "KEY_LINEAR"},
            )
        ],
        [Camera("Lens", {"lens": 35.0})],
        [
            Clip(
                "Walk",
                30.0,
                1.0,
                25.0,
                [
                    Track(
                        ResourceKind.OBJECT,
                        0,
                        "location",
                        [1.0, 13.0, 25.0],
                        [0.0, 0.5, 0.0],
                        array_index=2,
                    ),
                    Track(
                        ResourceKind.OBJECT,
                        2,
                        "rotation_quaternion",
                        [1.0, 13.0, 25.0],
                        [1.0, 0.5, 0.25, 0.0] * 3,
                        components=4,
                        flags=TrackFlags.SPHERICAL_ROTATION | TrackFlags.BAKED,
                        key_modes=[KeyMode.BEZIER, KeyMode.CONSTANT, KeyMode.LINEAR],
                        left_handle_types=[3, 0, 0],
                        right_handle_types=[3, 0, 0],
                        handles=[0.5, 0.0, 1.5, 0.0] * 3,
                    ),
                    Track(
                        ResourceKind.MATERIAL,
                        0,
                        "name_tag",
                        [1.0, 5.0],
                        ["open", "closed"],
                        value_type=TrackValue.STRING,
                    ),
                    Track(
                        ResourceKind.OBJECT,
                        1,
                        "data",
                        [1.0, 5.0],
                        [0, 1],
                        value_type=TrackValue.REFERENCE,
                        target_kind=ResourceKind.MESH,
                    ),
                    Track(
                        ResourceKind.OBJECT,
                        0,
                        "shape_weights",
                        [1.0, 5.0],
                        [0.25, 0.5, 0.75],
                        components=2,
                        changed=[True, True, False, True],
                    ),
                ],
            )
        ],
        [
            Extension("studio.notes", 2, False, b"keep me"),
        ],
        {"author": "test", "tags": ["a", "b"], "inner": {"depth": 1}},
        {"frame_start": 1, "camera": Reference(7)},
    )


class SceneTests(unittest.TestCase):
    def test_hierarchy_tables_place_parents_first(self):
        scene = Scene(
            objects=[
                SceneObject("Child", parent=2),
                SceneObject("Alone"),
                SceneObject("Middle", parent=3),
                SceneObject("Root"),
            ]
        )
        regions = map_file(encode_scene(scene))
        names = {region.name for region in regions}
        self.assertTrue({"OBJECT_ORDER", "OBJECT_CHILDREN"} <= names)
        data = encode_scene(scene)
        reader = SectionReader(data)
        order = read_indices(reader, SectionKind.OBJECT_ORDER, 4)
        self.assertLess(order.index(3), order.index(2))
        self.assertLess(order.index(2), order.index(0))

    def test_keys_on_a_line_are_dropped_and_the_rest_stay_close(self):
        times = [float(key) for key in range(100)]
        values = [2.0 * time for time in times[:50]] + [
            98.0 + math.sin(time) for time in times[50:]
        ]
        track = Track(ResourceKind.OBJECT, 0, "x", list(times), list(values))
        reduce_track(track)
        self.assertLess(len(track.times), 60)
        self.assertEqual((track.times[0], track.times[-1]), (0.0, 99.0))
        limit = (max(values) - min(values)) * 1e-5
        for time, value in zip(times, values, strict=True):
            self.assertLessEqual(abs(_value_at(track, time) - value), limit * 1.01)

    def test_vertices_with_equal_weights_share_a_row(self):
        mesh = grid_mesh(30)
        mesh.vertex_groups = ["A", "B"]
        mesh.weight_offsets = array("I", [v * 2 for v in range(mesh.vertex_count + 1)])
        mesh.weight_groups = array("I", [0, 1] * mesh.vertex_count)
        mesh.weight_values = array("f", [0.25, 0.75] * mesh.vertex_count)
        data = encode(mesh)
        names = {region.name for region in map_file(data)}
        self.assertIn("WEIGHT_SET_OF_VERTEX", names)
        self.assertTrue(same_geometry(snap_mesh(mesh), decode(data)))

    def test_targets_moving_the_same_vertices_share_a_list(self):
        mesh = grid_mesh(10)
        first = array("f", mesh.positions)
        second = array("f", mesh.positions)
        for vertex in range(0, 40, 2):
            first[vertex * 3 + 2] = 0.5
            second[vertex * 3 + 2] = 0.25
        mesh.shape_targets = [ShapeTarget("A", first), ShapeTarget("B", second)]
        data = encode(mesh)
        names = {region.name for region in map_file(data)}
        self.assertIn("SHAPE_LIST_STARTS", names)
        self.assertTrue(same_geometry(snap_mesh(mesh), decode(data)))

    def test_every_table_round_trips(self):
        source = sample_scene()
        decoded = decode_scene(encode_scene(source))
        for name in (
            "space",
            "materials",
            "samplers",
            "images",
            "objects",
            "collections",
            "lights",
            "armatures",
            "curves",
            "lattices",
            "cameras",
            "clips",
            "extensions",
            "properties",
        ):
            self.assertEqual(getattr(decoded, name), getattr(source, name), name)
        for first, second in zip(source.meshes, decoded.meshes, strict=True):
            self.assertTrue(same_geometry(snap_mesh(first), second))

    def test_identical_image_bytes_are_stored_once(self):
        scene = sample_scene()
        regions = {r.name: r.length for r in map_file(encode_scene(scene))}
        self.assertEqual(regions["IMAGE_PAYLOADS"], align(len(b"encoded image bytes")))

    def test_tracks_with_the_same_times_share_them(self):
        regions = {r.name: r.length for r in map_file(encode_scene(sample_scene()))}
        self.assertLessEqual(regions["TIME_STARTS"], 12)

    def test_identical_meshes_are_stored_once(self):
        scene = Scene(
            meshes=[grid_mesh(5), grid_mesh(5), triangle()],
            objects=[
                SceneObject("A", ObjectType.MESH, 0),
                SceneObject("B", ObjectType.MESH, 1),
                SceneObject("C", ObjectType.MESH, 2),
            ],
        )
        decoded = decode_scene(encode_scene(scene))
        self.assertEqual(len(decoded.meshes), 2)
        self.assertEqual([item.data for item in decoded.objects], [0, 0, 1])

    def test_unknown_required_extensions_fail_and_optional_ones_pass(self):
        scene = Scene(extensions=[Extension("future.thing", 1, True, b"x")])
        with self.assertRaisesRegex(FormatError, "future.thing"):
            decode_scene(encode_scene(scene))

    def test_unknown_optional_sections_are_skipped(self):
        sections = [
            Section(SectionKind.SCENE_MESH_STARTS, bytes(8)),
            Section(OPTIONAL_KIND_START + 5, b"future data!"),
        ]
        data = write_file(sections)
        self.assertEqual(decode_scene(data).objects, [])
        self.assertEqual(sum(r.length for r in map_file(data)), len(data))

    def test_an_empty_scene_is_valid_and_mesh_files_are_not_scenes(self):
        self.assertEqual(decode_scene(encode_scene(Scene())).objects, [])
        with self.assertRaises(FormatError):
            decode_scene(encode(triangle()))

    def test_every_scene_byte_is_named_exactly_once(self):
        data = encode_scene(sample_scene())
        at = 0
        for region in map_file(data):
            self.assertEqual(region.offset, at, region.name)
            at += region.length
        self.assertEqual(at, len(data))

    def test_broken_links_and_cycles_are_refused(self):
        def missing_data(scene):
            scene.objects[0].data = 9

        def missing_parent(scene):
            scene.objects[0].parent = 99

        def object_cycle(scene):
            scene.objects[0].parent = 1
            scene.objects[1].parent = 0

        def collection_cycle(scene):
            scene.collections[0].parent = 1

        def bone_cycle(scene):
            scene.armatures[0].bones[0].parent = 1

        def missing_image(scene):
            scene.materials[0].slots[0].image = 9

        def posed_missing_bone(scene):
            scene.objects[0].pose[0].bone = 9

        def tree_missing_object(scene):
            scene.objects[4].properties["link"] = Reference(99)

        def track_missing_owner(scene):
            scene.clips[0].tracks[0].owner = 99

        def track_keys_missing_mesh(scene):
            scene.clips[0].tracks[3].values[1] = 9

        def track_going_back(scene):
            scene.clips[0].tracks[0].times[1] = 0.0

        def lattice_wrong_size(scene):
            scene.lattices[0].points_w = 9

        for change in (
            missing_data,
            missing_parent,
            object_cycle,
            collection_cycle,
            bone_cycle,
            missing_image,
            posed_missing_bone,
            tree_missing_object,
            track_missing_owner,
            track_keys_missing_mesh,
            track_going_back,
            lattice_wrong_size,
        ):
            scene = sample_scene()
            change(scene)
            with self.subTest(change.__name__), self.assertRaises(FormatError):
                encode_scene(scene)

    def test_every_truncation_is_refused(self):
        data = encode_scene(sample_scene())
        for length in range(0, len(data), 97):
            with self.assertRaises(FormatError):
                decode_scene(data[:length])


if __name__ == "__main__":
    unittest.main()
