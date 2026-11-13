// SPDX-License-Identifier: GPL-3.0-or-later
// Encodes one mesh with meshoptimizer's own pipeline, then times decoding it.
// node meshopt_worker.mjs MESHOPT_DIR MESH_BIN
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const [, , packageDir, meshPath] = process.argv;
const importFrom = (file) => import(pathToFileURL(join(packageDir, file)).href);
const { MeshoptEncoder } = await importFrom("meshopt_encoder.js");
const { MeshoptDecoder } = await importFrom("meshopt_decoder.mjs");
await MeshoptEncoder.ready;
await MeshoptDecoder.ready;

const ROUNDS = 9;
const RUNS_PER_ROUND = 25;

const file = readFileSync(meshPath);
const counts = new Uint32Array(file.buffer, file.byteOffset, 4);
const [vertexCount, indexCount, hasNormals, hasUvs] = counts;
let at = 16;
function take(type, length) {
  const bytes = file.subarray(at, at + length * type.BYTES_PER_ELEMENT);
  at += bytes.length;
  const copy = new Uint8Array(bytes);
  return new type(copy.buffer);
}
const positions = take(Int32Array, vertexCount * 3);
const normals = hasNormals ? take(Uint16Array, vertexCount * 2) : null;
const uvs = hasUvs ? take(Int32Array, vertexCount * 2) : null;
const indices = take(Uint32Array, indexCount);

// Meshopt's recommended order, so it is seen at its best.
const [remap, uniqueCount] = MeshoptEncoder.reorderMesh(indices, true, true);
function reordered(values, width) {
  const output = new values.constructor(uniqueCount * width);
  for (let i = 0; i < vertexCount; i++) {
    if (remap[i] === 0xffffffff) continue;
    for (let part = 0; part < width; part++) output[remap[i] * width + part] = values[i * width + part];
  }
  return output;
}

const streams = [
  ["positions", reordered(positions, 3), 12],
  ...(normals ? [["normals", reordered(normals, 2), 4]] : []),
  ...(uvs ? [["uvs", reordered(uvs, 2), 8]] : []),
].map(([name, values, size]) => {
  const raw = new Uint8Array(values.buffer);
  const encoded = MeshoptEncoder.encodeVertexBuffer(raw, uniqueCount, size);
  return { name, size, encoded, target: new Uint8Array(uniqueCount * size) };
});
const encodedIndices = MeshoptEncoder.encodeIndexBuffer(new Uint8Array(indices.buffer), indexCount, 4);
const indexTarget = new Uint8Array(indexCount * 4);

function decodeAll() {
  for (const stream of streams) {
    MeshoptDecoder.decodeVertexBuffer(stream.target, uniqueCount, stream.size, stream.encoded);
  }
  MeshoptDecoder.decodeIndexBuffer(indexTarget, indexCount, 4, encodedIndices);
}
for (let i = 0; i < RUNS_PER_ROUND; i++) decodeAll();
const times = [];
for (let round = 0; round < ROUNDS; round++) {
  const start = process.hrtime.bigint();
  for (let run = 0; run < RUNS_PER_ROUND; run++) decodeAll();
  times.push(Number(process.hrtime.bigint() - start) / 1000 / RUNS_PER_ROUND);
}
times.sort((a, b) => a - b);

const stored = {};
for (const stream of streams) stored[stream.name] = stream.encoded.length;
stored.indices = encodedIndices.length;
const zstdInput = Buffer.concat([...streams.map((stream) => stream.encoded), encodedIndices]);
process.stdout.write(
  JSON.stringify({
    vertices: uniqueCount,
    stored,
    decode_us: times[Math.floor(ROUNDS / 2)],
    decoded_bytes: uniqueCount * streams.reduce((sum, s) => sum + s.size, 0) + indexCount * 4,
    payload_base64: zstdInput.toString("base64"),
  }),
);
