// SPDX-License-Identifier: GPL-3.0-or-later
// Times native meshoptimizer decoding of the mesh file meshopt_worker.mjs reads, using the same steps.
// zig c++ -O3 -std=c++17 -mavx2 -mpopcnt meshopt_native.cpp MESHOPT/src/vertexcodec.cpp MESHOPT/src/indexcodec.cpp
//     MESHOPT/src/indexgenerator.cpp MESHOPT/src/vcacheoptimizer.cpp MESHOPT/src/vfetchoptimizer.cpp
//     MESHOPT/src/allocator.cpp -IMESHOPT/src -o meshopt_native.exe
// meshopt_native MESH_BIN prints JSON.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <vector>

#include "meshoptimizer.h"

namespace
{
const int Rounds = 9;
const int RunsPerRound = 25;

struct Stream
{
    size_t size;
    std::vector<unsigned char> raw, encoded, target;
};

std::vector<unsigned char> readFile(const char* path)
{
    FILE* file = fopen(path, "rb");
    fseek(file, 0, SEEK_END);
    std::vector<unsigned char> bytes(ftell(file));
    fseek(file, 0, SEEK_SET);
    fread(bytes.data(), 1, bytes.size(), file);
    fclose(file);
    return bytes;
}

// Follows the same order the worker asks for: cache-friendly triangles, then vertices by first use.
std::vector<unsigned char> reorder(const unsigned char* values, const std::vector<unsigned int>& remap, size_t count, size_t unique, size_t size)
{
    std::vector<unsigned char> output(unique * size);
    for (size_t i = 0; i < count; ++i)
    {
        if (remap[i] != ~0u)
            std::copy(values + i * size, values + (i + 1) * size, output.begin() + remap[i] * size);
    }
    return output;
}
}

int main(int, char** arguments)
{
    std::vector<unsigned char> file = readFile(arguments[1]);
    const uint32_t* counts = reinterpret_cast<const uint32_t*>(file.data());
    size_t vertexCount = counts[0], indexCount = counts[1];
    bool hasNormals = counts[2] != 0, hasUvs = counts[3] != 0;
    size_t at = 16;
    const unsigned char* positions = file.data() + at;
    at += vertexCount * 12;
    const unsigned char* normals = file.data() + at;
    at += hasNormals ? vertexCount * 4 : 0;
    const unsigned char* uvs = file.data() + at;
    at += hasUvs ? vertexCount * 8 : 0;
    const unsigned int* source = reinterpret_cast<const unsigned int*>(file.data() + at);

    std::vector<unsigned int> indices(indexCount);
    meshopt_optimizeVertexCache(indices.data(), source, indexCount, vertexCount);
    std::vector<unsigned int> remap(vertexCount);
    size_t unique = meshopt_optimizeVertexFetchRemap(remap.data(), indices.data(), indexCount, vertexCount);
    meshopt_remapIndexBuffer(indices.data(), indices.data(), indexCount, remap.data());

    std::vector<Stream> streams;
    auto add = [&](const unsigned char* values, size_t size)
    {
        Stream stream;
        stream.size = size;
        stream.raw = reorder(values, remap, vertexCount, unique, size);
        stream.encoded.resize(meshopt_encodeVertexBufferBound(unique, size));
        stream.encoded.resize(meshopt_encodeVertexBuffer(stream.encoded.data(), stream.encoded.size(), stream.raw.data(), unique, size));
        stream.target.resize(unique * size);
        streams.push_back(std::move(stream));
    };
    add(positions, 12);
    if (hasNormals)
        add(normals, 4);
    if (hasUvs)
        add(uvs, 8);
    std::vector<unsigned char> encodedIndices(meshopt_encodeIndexBufferBound(indexCount, unique));
    encodedIndices.resize(meshopt_encodeIndexBuffer(encodedIndices.data(), encodedIndices.size(), indices.data(), indexCount));
    std::vector<unsigned int> indexTarget(indexCount);

    size_t stored = encodedIndices.size();
    for (const Stream& stream : streams)
        stored += stream.encoded.size();
    std::vector<double> times;
    for (int round = 0; round < Rounds; ++round)
    {
        auto start = std::chrono::steady_clock::now();
        for (int run = 0; run < RunsPerRound; ++run)
        {
            for (Stream& stream : streams)
                meshopt_decodeVertexBuffer(stream.target.data(), unique, stream.size, stream.encoded.data(), stream.encoded.size());
            meshopt_decodeIndexBuffer(indexTarget.data(), indexCount, encodedIndices.data(), encodedIndices.size());
        }
        std::chrono::duration<double, std::micro> took = std::chrono::steady_clock::now() - start;
        times.push_back(took.count() / RunsPerRound);
    }
    std::sort(times.begin(), times.end());
    printf("{\"decode_us\":%.3f,\"bytes\":%zu}\n", times[Rounds / 2], stored);
    return 0;
}
