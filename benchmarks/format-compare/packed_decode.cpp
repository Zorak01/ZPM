// SPDX-License-Identifier: GPL-3.0-or-later
// Times the native packed reader the add-on uses, and prints a checksum of what it read.
// python -m ziglang c++ -O3 -std=c++17 -mavx2 -ffp-contract=off packed_decode.cpp -o packed_decode.exe
// packed_decode MESH prints JSON: the median decode time of the geometry, from the
// streams to float positions, normals and UVs as Draco gives them, and a checksum
// Python can match. Edges are left out, since Draco has none to give.
#include "../../blender/zpm/format/native/zpm_native.cpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <map>

namespace
{
const int Rounds = 15;
const int RunsPerRound = 5;
const uint32_t HeaderBytes = 16;
const uint32_t EntryBytes = 8;
const uint32_t TailBytes = 32;
const int MeshInfo = 1;
const int Connectivity = 278, Splits = 279, Positions = 280, Normals = 281, Uvs = 282;
const uint32_t StepBytes = 4;

struct Section
{
    const uint8_t* at;
    uint64_t size;
};

uint64_t mix(uint64_t hash, int64_t value)
{
    hash ^= (uint64_t)value;
    return hash * 1099511628211ULL;
}
}

int main(int, char** arguments)
{
    FILE* file = fopen(arguments[1], "rb");
    fseek(file, 0, SEEK_END);
    std::vector<uint8_t> bytes(ftell(file));
    fseek(file, 0, SEEK_SET);
    fread(bytes.data(), 1, bytes.size(), file);
    fclose(file);
    const uint8_t* data = bytes.data();
    uint32_t count = readAt<uint16_t>(data + 6);
    std::map<std::pair<int, int>, Section> sections;
    for (uint32_t at = 0; at < count; at++)
    {
        const uint8_t* entry = data + HeaderBytes + at * EntryBytes;
        uint32_t offset = readAt<uint32_t>(entry + 4);
        uint32_t end = at + 1 < count ? readAt<uint32_t>(entry + EntryBytes + 4) : (uint32_t)bytes.size() - TailBytes;
        sections[{readAt<uint16_t>(entry), entry[3]}] = {data + offset, end - offset};
    }
    const uint8_t* info = sections[{MeshInfo, 0}].at;
    ZpmPackedInput in{};
    in.vertices = readAt<uint32_t>(info);
    in.faces = readAt<uint32_t>(info + 4);
    in.corners = readAt<uint32_t>(info + 8);
    in.arity = readAt<uint32_t>(info + 20);
    Section positions = sections[{Positions, 0}];
    float step = readAt<float>(positions.at);
    in.unit = step != 0 ? step : std::ldexp(1.0, readAt<int32_t>(info + 24));
    in.connectivity = sections[{Connectivity, 0}].at;
    in.connectivitySize = sections[{Connectivity, 0}].size;
    in.splits = sections[{Splits, 0}].at;
    in.splitsSize = sections[{Splits, 0}].size;
    in.positions = positions.at + StepBytes;
    in.positionsSize = positions.size - StepBytes;
    bool hasNormals = sections.count({Normals, 0}) != 0;
    if (hasNormals)
    {
        in.normals = sections[{Normals, 0}].at;
        in.normalsSize = sections[{Normals, 0}].size;
    }
    std::vector<const uint8_t*> uvData;
    std::vector<uint64_t> uvSizes;
    for (int layer = 0; sections.count({Uvs, layer}); layer++)
    {
        uvData.push_back(sections[{Uvs, layer}].at);
        uvSizes.push_back(sections[{Uvs, layer}].size);
    }
    in.uvLayers = (uint32_t)uvData.size();
    in.uvs = uvData.data();
    in.uvSizes = uvSizes.data();
    size_t corners = in.corners;
    std::vector<uint32_t> welded(corners), starts(in.faces + 1), vertexOf(corners);
    std::vector<float> placed(in.vertices * 3ULL), normals(hasNormals ? corners * 3 : 0), uvs(uvData.size() * corners * 2);
    std::vector<int32_t> rows(corners * 3), normalPairs(hasNormals ? corners * 2 : 0), uvPairs(uvData.size() * corners * 2), extras(in.vertices * 3ULL);
    ZpmPackedOutput out{welded.data(), starts.data(), vertexOf.data(), placed.data(), rows.data(), hasNormals ? normalPairs.data() : nullptr, hasNormals ? normals.data() : nullptr, uvPairs.data(), uvs.data(), nullptr, extras.data(), 0, 0};
    std::vector<double> times;
    for (int round = 0; round < Rounds; round++)
    {
        auto start = std::chrono::steady_clock::now();
        for (int run = 0; run < RunsPerRound; run++)
        {
            if (zpm_read_packed_geometry(&in, &out))
            {
                printf("{\"error\":\"refused\"}\n");
                return 1;
            }
        }
        std::chrono::duration<double, std::micro> took = std::chrono::steady_clock::now() - start;
        times.push_back(took.count() / RunsPerRound);
    }
    std::sort(times.begin(), times.end());
    std::vector<uint32_t> positionOf;
    for (size_t corner = 0; corner < corners; corner++)
    {
        if (vertexOf[corner] >= positionOf.size())
            positionOf.resize(vertexOf[corner] + 1);
        positionOf[vertexOf[corner]] = welded[corner];
    }
    uint64_t hash = 14695981039346656037ULL;
    for (uint32_t position : positionOf)
    {
        for (int axis = 0; axis < 3; axis++)
            hash = mix(hash, rows[position * 3ULL + axis]);
    }
    for (size_t at = positionOf.size() * 3; at < in.vertices * 3ULL; at++)
        hash = mix(hash, extras[at - positionOf.size() * 3]);
    for (uint32_t vertex : vertexOf)
        hash = mix(hash, vertex);
    for (int32_t value : normalPairs)
        hash = mix(hash, value);
    for (int32_t value : uvPairs)
        hash = mix(hash, value);
    printf("{\"decode_us\":%.1f,\"checksum\":\"%016llx\"}\n", times[Rounds / 2], (unsigned long long)hash);
    return 0;
}
