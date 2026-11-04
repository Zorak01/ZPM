// SPDX-License-Identifier: GPL-3.0-or-later
// A second, native decoder for packed meshes: proves the format by checksum, and times it.
// zig c++ -O3 -std=c++17 -mavx2 packed_decode.cpp -o packed_decode.exe
// packed_decode MESH prints JSON. Covers geometry: positions, corners, normals and UVs.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <vector>

namespace
{
const uint32_t None = 0xFFFFFFFFu;
const int Rounds = 15;
const int RunsPerRound = 5;

template <class T> T readAt(const uint8_t* at)
{
    T value;
    memcpy(&value, at, sizeof(T));
    return value;
}

struct Stream
{
    uint32_t state = 0;
    const uint8_t* wordAt = nullptr;
    const uint8_t* rawAt = nullptr;
    uint64_t pending = 0;
    int pendingBits = 0;
    // Fine level 1 puts a number's second bit in its symbol too; level 2 its second and third.
    int fine = 0;
    // Per slot of each context: symbol, then frequency, then the slot's offset in its symbol.
    std::vector<uint32_t> entries;

    static uint32_t readVarint(const uint8_t*& at)
    {
        uint32_t value = 0;
        for (int shift = 0;; shift += 7)
        {
            uint8_t byte = *at++;
            value |= (uint32_t)(byte & 0x7F) << shift;
            if (byte < 0x80)
                return value;
        }
    }

    struct Bits
    {
        const uint8_t* at;
        uint64_t pending = 0;
        int count = 0;

        uint32_t take(int bits)
        {
            while (count < bits)
            {
                pending |= (uint64_t)*at++ << count;
                count += 8;
            }
            uint32_t value = (uint32_t)(pending & ((1ULL << bits) - 1));
            pending >>= bits;
            count -= bits;
            return value;
        }

        uint32_t golomb(int k)
        {
            int zeros = 0;
            while (!take(1))
                zeros++;
            int top = zeros + k;
            return ((1u << top) | take(top)) - (1u << k);
        }
    };

    void open(const uint8_t* data, int contexts)
    {
        state = readAt<uint32_t>(data);
        const uint8_t* at = data + 4;
        uint32_t words = readVarint(at);
        readVarint(at);
        Bits bits{at};
        entries.assign((size_t)contexts * 1024, 0);
        uint32_t symbols[256], frequencies[256];
        for (int context = 0; context < contexts; context++)
        {
            uint32_t count = bits.golomb(0);
            if (!count)
                continue;
            int k = (int)bits.take(4);
            int previous = -1;
            for (uint32_t entry = 0; entry < count; entry++)
            {
                previous += (int)bits.golomb(0) + 1;
                symbols[entry] = (uint32_t)previous;
            }
            uint32_t used = 0;
            for (uint32_t entry = 0; entry + 1 < count; entry++)
            {
                frequencies[entry] = bits.golomb(k) + 1;
                used += frequencies[entry];
            }
            frequencies[count - 1] = 1024 - used;
            uint32_t slot = 0;
            for (uint32_t entry = 0; entry < count; entry++)
            {
                uint32_t frequency = frequencies[entry];
                for (uint32_t step = 0; step < frequency; step++)
                    entries[(size_t)context * 1024 + slot + step] = symbols[entry] | (frequency << 8) | (step << 20);
                slot += frequency;
            }
        }
        wordAt = bits.at;
        rawAt = wordAt + words * 2;
        pending = 0;
        pendingBits = 0;
    }

    void restart(const uint8_t* data)
    {
        state = readAt<uint32_t>(data);
        pending = 0;
        pendingBits = 0;
    }

    inline uint32_t read(int context)
    {
        uint32_t entry = entries[(size_t)context * 1024 + (state & 1023)];
        state = ((entry >> 8) & 0xFFF) * (state >> 10) + (entry >> 20);
        if (state < 65536)
        {
            state = (state << 16) | readAt<uint16_t>(wordAt);
            wordAt += 2;
        }
        return entry & 255;
    }

    inline int64_t readNumber(int context)
    {
        uint32_t symbol = read(context);
        uint64_t stored = symbol;
        if (symbol > 1)
        {
            int extra;
            if (fine == 2 && symbol > 3)
            {
                extra = (int)(symbol / 4 + 2) - 3;
                stored = (uint64_t)(4 | (symbol & 3)) << extra;
            }
            else if (fine)
            {
                extra = (int)((symbol + 2) / 2) - 2;
                stored = (uint64_t)(2 | (symbol & 1)) << extra;
            }
            else
            {
                extra = (int)symbol - 1;
                stored = 1ULL << extra;
            }
            while (pendingBits < extra)
            {
                pending |= (uint64_t)*rawAt++ << pendingBits;
                pendingBits += 8;
            }
            stored |= pending & ((1ULL << extra) - 1);
            pending >>= extra;
            pendingBits -= extra;
        }
        return (stored & 1) ? -(int64_t)(stored >> 1) - 1 : (int64_t)(stored >> 1);
    }

    inline uint32_t readChoice(int context)
    {
        uint32_t choice = read(context);
        return choice == 200 ? 200 + (uint32_t)readNumber(context) : choice;
    }
};

struct Sections
{
    std::map<std::pair<int, int>, const uint8_t*> at;
    const uint8_t* find(int kind, int layer = 0) const
    {
        auto item = at.find({kind, layer});
        return item == at.end() ? nullptr : item->second;
    }
};

inline int64_t floorDivide(int64_t a, int64_t b)
{
    int64_t q = a / b;
    return (a % b != 0 && (a < 0) != (b < 0)) ? q - 1 : q;
}

inline int magnitude(int64_t value)
{
    uint64_t v = (uint64_t)(value < 0 ? -value : value);
    int bits = v ? 64 - __builtin_clzll(v) : 0;
    return bits < 14 ? bits : 14;
}

void encodeNormal(double x, double y, double z, int64_t& a, int64_t& b)
{
    double total = std::fabs(x) + std::fabs(y) + std::fabs(z);
    if (total == 0)
    {
        x = 0;
        y = 0;
        z = 1;
        total = 1;
    }
    double u = x / total, v = y / total;
    if (z < 0)
    {
        double nu = (1 - std::fabs(v)) * (u >= 0 ? 1 : -1);
        double nv = (1 - std::fabs(u)) * (v >= 0 ? 1 : -1);
        u = nu;
        v = nv;
    }
    a = (int64_t)std::nearbyint((u * 0.5 + 0.5) * 4095);
    b = (int64_t)std::nearbyint((v * 0.5 + 0.5) * 4095);
}

struct Gate
{
    uint32_t u, v, cornerU, cornerV, cornerThird;
};

struct Mesh
{
    uint32_t vertices, faces, cornerCount, arity;
    uint32_t positionCount = 0;
    std::vector<uint32_t> faceStart, welded, corners, positionOf;
    std::vector<Gate> faceGate; // cornerThird is None for a face no gate reached.
    std::vector<int32_t> rows, extras, normals;
    std::vector<std::vector<int32_t>> uvs;
    std::vector<int64_t> totals;
    // Open edges: nodes in two singly linked lists, newest first.
    std::vector<uint32_t> headInto, headOut, nodeFrom, nodeTo, nextInto, nextOut;
    std::vector<uint32_t> head, tail, nextSeen;
    std::vector<uint8_t> seenCount;
    std::vector<uint32_t> recent;
    std::vector<Gate> gates;
    // Model 2: each position's face count, and how many of its faces are placed.
    std::vector<uint32_t> faceTotal, facePlaced;
    // The earlier corner at the same position across each closed edge, or None.
    std::vector<uint32_t> across, nodeCornerFrom, nodeCornerTo;
    std::vector<uint8_t> seamVertex;
    // Model 1 adds the symbol before last and which candidates exist; model 2 keys on
    // the faces each end still needs.
    int connectivityModel = 0, contextGate = 10, contextFar = 11, contextArity = 12;
    int contextSteps = 13, contextCount = 14, connectivityContexts = 14;
    int splitModel = 0;
};

struct SeenList
{
    std::vector<uint32_t> head, tail, next, count;

    void reset(size_t keys, size_t corners)
    {
        count.assign(keys, 0);
        head.resize(keys);
        tail.resize(keys);
        next.resize(corners);
    }

    void add(uint32_t key, uint32_t corner)
    {
        if (count[key])
            next[tail[key]] = corner;
        else
            head[key] = corner;
        tail[key] = corner;
        count[key]++;
    }

    uint32_t nth(uint32_t key, uint32_t n) const
    {
        uint32_t corner = head[key];
        for (uint32_t step = 1; step < n; step++)
            corner = next[corner];
        return corner;
    }

    void addIfNew(uint32_t key, uint32_t corner, const int32_t* values)
    {
        uint32_t item = head[key];
        for (uint32_t step = 0; step < count[key]; step++, item = next[item])
        {
            if (values[item * 2ULL] == values[corner * 2ULL] && values[item * 2ULL + 1] == values[corner * 2ULL + 1])
                return;
        }
        add(key, corner);
    }
};

// Where a corner looks for a value already seen: 0 its position, 1 its vertex, 2 its
// vertex if it has any, else its position.
struct Lookup
{
    SeenList byPosition, byVertex;
    int lookup = 0, vertexContexts = 0;

    void reset(const Mesh& m, int mode, int contexts);
    // Returns the list consulted and its key; context set for the choice.
    SeenList& consult(const Mesh& m, uint32_t corner, uint32_t& key, int& context);
    void record(const Mesh& m, uint32_t corner, const int32_t* values);
};

void setConnectivityModel(Mesh& m, int model)
{
    int vertexContexts = model == 2 ? 5 * 5 * 2 * 4 : model == 1 ? 10 * 5 * 4 : 10;
    int gateContexts = model == 2 ? 9 : 1;
    m.connectivityModel = model;
    m.contextGate = vertexContexts;
    m.contextFar = vertexContexts + gateContexts;
    m.contextArity = m.contextFar + 1;
    m.contextSteps = m.contextFar + 2;
    m.contextCount = m.contextFar + 3;
    m.connectivityContexts = m.contextCount + (model == 2 ? 10 * 11 : 0);
}

inline int facesLeft(const Mesh& m, uint32_t position)
{
    return (int)m.faceTotal[position] - (int)m.facePlaced[position];
}

inline int leftKind(const Mesh& m, uint32_t position)
{
    return 1 + std::min(std::max(facesLeft(m, position), 0), 3);
}

inline bool isOpen(const Mesh& m, uint32_t u, uint32_t v)
{
    for (uint32_t node = m.headInto[v]; node != None; node = m.nextInto[node])
    {
        if (m.nodeFrom[node] == u)
            return true;
    }
    return false;
}

inline uint32_t removeOpen(Mesh& m, uint32_t u, uint32_t v)
{
    uint32_t* link = &m.headInto[v];
    while (m.nodeFrom[*link] != u)
        link = &m.nextInto[*link];
    uint32_t removed = *link;
    *link = m.nextInto[*link];
    link = &m.headOut[u];
    while (m.nodeTo[*link] != v)
        link = &m.nextOut[*link];
    *link = m.nextOut[*link];
    return removed;
}

inline void readFace(Stream& s, Mesh& m, int known, uint32_t k0, uint32_t k1, const Gate& reached, uint32_t& newest, uint32_t& previous, uint32_t& before, uint32_t& nodes, uint32_t& previousCount)
{
    uint32_t count = m.arity ? m.arity : s.readChoice(m.contextArity);
    uint32_t first = (uint32_t)m.welded.size();
    m.faceStart.push_back(first);
    m.faceGate.push_back(reached);
    if (known)
    {
        m.welded.push_back(k0);
        m.welded.push_back(k1);
    }
    for (uint32_t at = known; at < count; at++)
    {
        bool hasRing = m.welded.size() > first;
        int last = at == count - 1 ? 1 : 0;
        int kinds = 0;
        if (hasRing)
            kinds = (m.headInto[m.welded.back()] != None ? 1 : 0) | (m.headOut[m.welded[first]] != None ? 2 : 0);
        int context;
        if (m.connectivityModel == 2)
        {
            int firstKind = hasRing ? leftKind(m, m.welded[first]) : 0;
            int lastKind = hasRing ? leftKind(m, m.welded.back()) : 0;
            context = ((firstKind * 5 + lastKind) * 2 + last) * 4 + kinds;
        }
        else
        {
            context = (int)std::min<uint32_t>(previous, 4) * 2 + last;
            if (m.connectivityModel == 1)
                context = (context * 5 + (int)std::min<uint32_t>(before, 4)) * 4 + kinds;
        }
        uint32_t symbol = s.read(context);
        before = previous;
        previous = symbol;
        uint32_t vertex;
        if (symbol == 0)
        {
            vertex = ++newest;
            if (m.connectivityModel == 2)
            {
                int firstCount = hasRing ? 1 + (int)std::min<uint32_t>(m.faceTotal[m.welded[first]], 9) : 0;
                uint32_t total = s.readChoice(m.contextCount + (int)std::min<uint32_t>(previousCount, 9) * 11 + firstCount);
                m.faceTotal[vertex] = total;
                m.facePlaced[vertex] = 0;
                previousCount = total;
            }
        }
        else if (symbol == 4)
            vertex = newest - (uint32_t)s.readNumber(m.contextFar);
        else if (symbol == 5)
        {
            uint32_t steps = s.readChoice(m.contextSteps) + 1;
            vertex = m.welded.back();
            for (uint32_t step = 0; step < steps; step++)
                vertex = m.nodeTo[m.headOut[vertex]];
        }
        else if (symbol == 6)
        {
            uint32_t steps = s.readChoice(m.contextSteps) + 1;
            vertex = m.welded[first];
            for (uint32_t step = 0; step < steps; step++)
                vertex = m.nodeFrom[m.headInto[vertex]];
        }
        else if (symbol == 1)
            vertex = m.nodeFrom[m.headInto[m.welded.back()]];
        else if (symbol == 2)
            vertex = m.nodeTo[m.headOut[m.welded[first]]];
        else
            vertex = m.nodeFrom[m.nextInto[m.headInto[m.welded.back()]]];
        m.welded.push_back(vertex);
    }
    if (m.connectivityModel == 2)
    {
        for (uint32_t at = 0; at < count; at++)
            m.facePlaced[m.welded[first + at]]++;
    }
    for (uint32_t at = 0; at < count; at++)
    {
        uint32_t next = at + 1 == count ? 0 : at + 1;
        uint32_t u = m.welded[first + at], v = m.welded[first + next];
        if (isOpen(m, v, u))
        {
            uint32_t closed = removeOpen(m, v, u);
            if (m.across[first + at] == None)
                m.across[first + at] = m.nodeCornerTo[closed];
            if (m.across[first + next] == None)
                m.across[first + next] = m.nodeCornerFrom[closed];
            continue;
        }
        uint32_t node = nodes++;
        m.nodeFrom[node] = u;
        m.nodeTo[node] = v;
        m.nodeCornerFrom[node] = first + at;
        m.nodeCornerTo[node] = first + next;
        m.nextInto[node] = m.headInto[v];
        m.headInto[v] = node;
        m.nextOut[node] = m.headOut[u];
        m.headOut[u] = node;
        uint32_t after = next + 1 == count ? 0 : next + 1;
        m.gates.push_back({u, v, first + at, first + next, first + after});
    }
}

void readConnectivity(Stream& s, Mesh& m)
{
    m.faceStart.clear();
    m.welded.clear();
    m.faceGate.clear();
    m.gates.clear();
    std::fill(m.headInto.begin(), m.headInto.end(), None);
    std::fill(m.headOut.begin(), m.headOut.end(), None);
    std::fill(m.across.begin(), m.across.end(), None);
    uint32_t newest = None, previous = 0, before = 0, nodes = 0, previousCount = 6;
    const Gate noGate = {0, 0, 0, 0, None};
    while (m.faceStart.size() < m.faces)
    {
        readFace(s, m, 0, 0, 0, noGate, newest, previous, before, nodes, previousCount);
        while (!m.gates.empty() && m.faceStart.size() < m.faces)
        {
            Gate gate = m.gates.back();
            m.gates.pop_back();
            if (!isOpen(m, gate.u, gate.v))
                continue;
            int context = m.contextGate;
            if (m.connectivityModel == 2)
                context += std::min(facesLeft(m, gate.u), 2) * 3 + std::min(facesLeft(m, gate.v), 2);
            if (s.read(context))
                readFace(s, m, 2, gate.v, gate.u, gate, newest, previous, before, nodes, previousCount);
            else
                removeOpen(m, gate.u, gate.v);
        }
    }
    m.faceStart.push_back((uint32_t)m.welded.size());
    m.positionCount = newest + 1;
}

void resetSeen(Mesh& m, size_t count)
{
    std::fill(m.seenCount.begin(), m.seenCount.begin() + count, 0);
}

void resetSeen(Mesh& m)
{
    resetSeen(m, m.positionCount);
}

// Model 0 names a vertex at its position by when it was made; model 1 first guesses
// the vertex across the face's gate, or the one last used here, then the rest newest first.
void Lookup::reset(const Mesh& m, int mode, int contexts)
{
    lookup = mode;
    vertexContexts = contexts;
    byPosition.reset(m.positionCount, m.welded.size());
    byVertex.reset(m.positionOf.size(), m.welded.size());
}

SeenList& Lookup::consult(const Mesh& m, uint32_t corner, uint32_t& key, int& context)
{
    uint32_t vertex = m.corners[corner];
    if (lookup == 1 || (lookup == 2 && byVertex.count[vertex]))
    {
        key = vertex;
        context = (int)std::min<uint32_t>(byVertex.count[vertex], 2) + (lookup == 2 ? vertexContexts : 0);
        return byVertex;
    }
    key = m.welded[corner];
    context = (int)std::min<uint32_t>(byPosition.count[key], 2);
    return byPosition;
}

void Lookup::record(const Mesh& m, uint32_t corner, const int32_t* values)
{
    if (lookup != 1)
        byPosition.addIfNew(m.welded[corner], corner, values);
    if (lookup != 0)
        byVertex.addIfNew(m.corners[corner], corner, values);
}

void readSplits(Stream& s, Mesh& m)
{
    resetSeen(m);
    m.corners.resize(m.welded.size());
    m.recent.resize(m.positionCount);
    m.positionOf.clear();
    std::fill(m.seamVertex.begin(), m.seamVertex.end(), 0);
    uint32_t options[256];
    uint32_t lastFace = None, faceChoice = 0;
    for (uint32_t face = 0; face < m.faces; face++)
    {
        const Gate& gate = m.faceGate[face];
        uint32_t first = m.faceStart[face], end = m.faceStart[face + 1];
        for (uint32_t corner = first; corner < end; corner++)
        {
            uint32_t position = m.welded[corner];
            uint32_t count = m.seenCount[position];
            uint32_t vertex = None;
            uint32_t at = 0;
            if (count)
            {
                if (m.splitModel)
                {
                    bool fromGate;
                    uint32_t guess;
                    int extra = 0;
                    if (m.splitModel == 2)
                    {
                        if (face != lastFace)
                        {
                            lastFace = face;
                            faceChoice = 0;
                        }
                        // Only corners read so far can be across.
                        uint32_t other = m.across[corner];
                        fromGate = other != None && other < corner;
                        guess = fromGate ? m.corners[other] : m.recent[position];
                        int seam = fromGate && m.seamVertex[guess] ? 1 : 0;
                        int through = gate.cornerThird != None && corner - first < 2 ? 1 : 0;
                        extra = 8 * ((int)faceChoice * 2 + seam) + 64 * through;
                    }
                    else
                    {
                        fromGate = gate.cornerThird != None && corner - first < 2;
                        guess = fromGate ? m.corners[corner == first ? gate.cornerV : gate.cornerU] : m.recent[position];
                    }
                    uint32_t known[256], held = 0;
                    for (uint32_t item = m.head[position]; held < count; item = m.nextSeen[item])
                        known[held++] = item;
                    uint32_t total = 0;
                    options[total++] = guess;
                    for (uint32_t back = held; back-- > 0;)
                    {
                        if (known[back] != guess)
                            options[total++] = known[back];
                    }
                    at = s.readChoice((int)std::min<uint32_t>(count, 3) + (fromGate ? 4 : 0) + extra);
                    if (at)
                        vertex = options[at - 1];
                }
                else
                {
                    at = s.readChoice(count < 3 ? (int)count : 3);
                    if (at)
                    {
                        vertex = m.head[position];
                        for (uint32_t step = 1; step < at; step++)
                            vertex = m.nextSeen[vertex];
                    }
                }
            }
            if (count)
                faceChoice = at == 0 ? 1 : at == 1 ? 3 : 2;
            if (vertex == None)
            {
                vertex = (uint32_t)m.positionOf.size();
                m.positionOf.push_back(position);
                if (count)
                    m.seamVertex[vertex] = 1;
                if (count)
                    m.nextSeen[m.tail[position]] = vertex;
                else
                    m.head[position] = vertex;
                m.tail[position] = vertex;
                m.seenCount[position] = (uint8_t)std::min<uint32_t>(count + 1, 255);
            }
            m.corners[corner] = vertex;
            m.recent[position] = vertex;
        }
    }
}

// Across keys each axis on the earlier axes of the same position and its own last size.
// Keying 1 adds the earlier axes of the same position; keying 2 how much the
// parallelogram's triangle faces along the axis. Shrunk, the far corner moves a quarter
// of the way back toward the shared edge's midpoint.
void readPositions(Stream& s, Mesh& m, int keying, bool shrink)
{
    m.rows.resize((size_t)m.positionCount * 3);
    int32_t previous[3] = {0, 0, 0};
    int history[3] = {0, 0, 0};
    bool usesHistory = m.positionCount >= 4000;
    uint32_t made = 0;
    int32_t* rows = m.rows.data();
    const uint32_t* welded = m.welded.data();
    for (uint32_t face = 0; face < m.faces; face++)
    {
        uint32_t first = m.faceStart[face], end = m.faceStart[face + 1];
        for (uint32_t corner = first; corner < end; corner++)
        {
            if (welded[corner] != made)
                continue;
            uint32_t at = corner - first;
            int32_t guess[3] = {previous[0], previous[1], previous[2]};
            int shares[3] = {-1, -1, -1};
            if (at == 1)
            {
                for (int axis = 0; axis < 3; axis++)
                    guess[axis] = rows[welded[first] * 3ULL + axis];
            }
            else if (at >= 2)
            {
                const int32_t* a = rows + welded[corner - 1] * 3ULL;
                const int32_t* b = rows + welded[first] * 3ULL;
                uint32_t third = at == 2 ? m.faceGate[face].cornerThird : first + 1;
                if (third == None)
                {
                    for (int axis = 0; axis < 3; axis++)
                        guess[axis] = a[axis];
                }
                else
                {
                    const int32_t* c = rows + welded[third] * 3ULL;
                    for (int axis = 0; axis < 3; axis++)
                    {
                        if (shrink)
                            guess[axis] = (int32_t)floorDivide(7 * ((int64_t)a[axis] + b[axis]) - 6 * (int64_t)c[axis] + 4, 8);
                        else
                            guess[axis] = a[axis] + b[axis] - c[axis];
                    }
                    if (keying == 2)
                    {
                        int64_t ux = a[0] - c[0], uy = a[1] - c[1], uz = a[2] - c[2];
                        int64_t vx = b[0] - c[0], vy = b[1] - c[1], vz = b[2] - c[2];
                        int64_t normal[3] = {std::llabs(uy * vz - uz * vy), std::llabs(uz * vx - ux * vz), std::llabs(ux * vy - uy * vx)};
                        int64_t total = normal[0] + normal[1] + normal[2];
                        if (total)
                        {
                            for (int axis = 0; axis < 3; axis++)
                                shares[axis] = (int)std::min<int64_t>(4 * normal[axis] / total, 3);
                        }
                    }
                }
            }
            int32_t* row = rows + made * 3ULL;
            int reach = 0;
            for (int axis = 0; axis < 3; axis++)
            {
                int context;
                if (keying == 2)
                    context = (shares[axis] + 1) * 6 + std::min(history[axis] / 3, 5);
                else if (keying == 1)
                {
                    int widest = axis ? reach : history[axis];
                    context = std::min(widest / 2, 7) * 6 + std::min(history[axis] / 3, 5);
                }
                else
                    context = usesHistory ? std::min(history[axis] / 4, 3) : 0;
                int64_t difference = s.readNumber(context);
                row[axis] = guess[axis] + (int32_t)difference;
                history[axis] = magnitude(difference);
                reach = std::max(reach, history[axis]);
                previous[axis] = row[axis];
            }
            made++;
        }
    }
    uint32_t extra = m.vertices - (uint32_t)m.positionOf.size();
    m.extras.resize((size_t)extra * 3);
    for (uint32_t at = 0; at < extra; at++)
    {
        for (int axis = 0; axis < 3; axis++)
        {
            previous[axis] += (int32_t)s.readNumber(0);
            m.extras[at * 3ULL + axis] = previous[axis];
        }
    }
}

// Predicts each corner's normal around its key: its welded position, or its vertex.
void readNormals(Stream& s, Mesh& m, bool byVertex, Lookup& seen, int lookup, bool paired)
{
    const uint32_t* keys = byVertex ? m.corners.data() : m.welded.data();
    size_t keyCount = byVertex ? m.positionOf.size() : m.positionCount;
    m.totals.assign(keyCount * 3, 0);
    const int32_t* rows = m.rows.data();
    int64_t* totals = m.totals.data();
    for (uint32_t face = 0; face < m.faces; face++)
    {
        uint32_t first = m.faceStart[face], end = m.faceStart[face + 1];
        uint32_t a = keys[first];
        const int32_t* pa = rows + m.welded[first] * 3ULL;
        for (uint32_t at = first + 1; at + 1 < end; at++)
        {
            uint32_t b = keys[at], c = keys[at + 1];
            const int32_t* pb = rows + m.welded[at] * 3ULL;
            const int32_t* pc = rows + m.welded[at + 1] * 3ULL;
            int64_t ux = pb[0] - pa[0], uy = pb[1] - pa[1], uz = pb[2] - pa[2];
            int64_t vx = pc[0] - pa[0], vy = pc[1] - pa[1], vz = pc[2] - pa[2];
            int64_t nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
            uint32_t all[3] = {a, b, c};
            for (uint32_t item : all)
            {
                totals[item * 3ULL] += nx;
                totals[item * 3ULL + 1] += ny;
                totals[item * 3ULL + 2] += nz;
            }
        }
    }
    size_t cornerCount = m.welded.size();
    m.normals.resize(cornerCount * 2);
    int32_t* values = m.normals.data();
    seen.reset(m, lookup, 13);
    int history[2] = {0, 0};
    for (uint32_t corner = 0; corner < cornerCount; corner++)
    {
        uint32_t key;
        int context;
        SeenList& list = seen.consult(m, corner, key, context);
        uint32_t count = list.count[key];
        if (count)
        {
            uint32_t at = s.readChoice(context);
            if (at)
            {
                uint32_t found = list.nth(key, at);
                values[corner * 2ULL] = values[found * 2ULL];
                values[corner * 2ULL + 1] = values[found * 2ULL + 1];
                seen.record(m, corner, values);
                continue;
            }
        }
        int64_t base[2];
        uint32_t predicted = keys[corner];
        if (count)
        {
            base[0] = values[list.tail[key] * 2ULL];
            base[1] = values[list.tail[key] * 2ULL + 1];
        }
        else
            encodeNormal((double)totals[predicted * 3ULL], (double)totals[predicted * 3ULL + 1], (double)totals[predicted * 3ULL + 2], base[0], base[1]);
        int offset = count ? 1 : 0;
        for (int part = 0; part < 2; part++)
        {
            int context = 3 + offset * 5 + history[part] / 3;
            if (paired && part)
                context = 13 + (lookup == 2 ? 3 : 0) + std::min(history[0] / 2, 7);
            int64_t difference = s.readNumber(context);
            values[corner * 2ULL + part] = (int32_t)(base[part] + difference);
            history[part] = magnitude(difference);
        }
        seen.record(m, corner, values);
    }
}

void readUvs(Stream& s, Mesh& m, std::vector<int32_t>& store, Lookup& seen, int lookup, bool paired)
{
    int contexts = 8 + (lookup == 2 ? 3 : 0);
    store.resize(m.welded.size() * 2);
    int32_t* values = store.data();
    seen.reset(m, lookup, 8);
    const int32_t* rows = m.rows.data();
    const uint32_t* welded = m.welded.data();
    int history[2] = {0, 0};
    int32_t last[2] = {0, 0};
    for (uint32_t face = 0; face < m.faces; face++)
    {
        uint32_t first = m.faceStart[face], end = m.faceStart[face + 1];
        const Gate& gate = m.faceGate[face];
        for (uint32_t corner = first; corner < end; corner++)
        {
            uint32_t position = welded[corner];
            uint32_t key;
            int context;
            SeenList& list = seen.consult(m, corner, key, context);
            uint32_t count = list.count[key];
            if (count)
            {
                uint32_t choice = s.readChoice(context);
                if (choice)
                {
                    uint32_t found = list.nth(key, choice);
                    values[corner * 2ULL] = values[found * 2ULL];
                    values[corner * 2ULL + 1] = values[found * 2ULL + 1];
                    seen.record(m, corner, values);
                    continue;
                }
            }
            uint32_t at = corner - first;
            double base[2];
            if (at == 0)
            {
                uint32_t from = list.tail[key];
                base[0] = count ? values[from * 2ULL] : last[0];
                base[1] = count ? values[from * 2ULL + 1] : last[1];
            }
            else if (at == 1)
            {
                base[0] = values[first * 2ULL];
                base[1] = values[first * 2ULL + 1];
            }
            else
            {
                uint32_t a = corner - 1, b = first;
                const int32_t* pa = rows + welded[a] * 3ULL;
                const int32_t* pb = rows + welded[b] * 3ULL;
                const int32_t* pv = rows + position * 3ULL;
                int32_t ua0 = values[a * 2ULL], ua1 = values[a * 2ULL + 1], ub0 = values[b * 2ULL], ub1 = values[b * 2ULL + 1];
                double ex = (double)(pb[0] - pa[0]), ey = (double)(pb[1] - pa[1]), ez = (double)(pb[2] - pa[2]);
                double qx = (double)(pv[0] - pa[0]), qy = (double)(pv[1] - pa[1]), qz = (double)(pv[2] - pa[2]);
                double length = ex * ex + ey * ey + ez * ez;
                double one[2], two[2];
                if (length == 0.0)
                {
                    one[0] = two[0] = ua0;
                    one[1] = two[1] = ua1;
                }
                else
                {
                    double du = (double)((int64_t)ub0 - ua0), dv = (double)((int64_t)ub1 - ua1);
                    double along = (ex * qx + ey * qy + ez * qz) / length;
                    double rx = qx - along * ex, ry = qy - along * ey, rz = qz - along * ez;
                    double away = std::sqrt((rx * rx + ry * ry + rz * rz) / length);
                    double bu = ua0 + along * du, bv = ua1 + along * dv;
                    one[0] = bu - away * dv;
                    one[1] = bv + away * du;
                    two[0] = bu + away * dv;
                    two[1] = bv - away * du;
                }
                uint32_t far = None;
                if (at > 2)
                    far = first + 1;
                else if (gate.cornerThird != None && values[gate.cornerV * 2ULL] == values[first * 2ULL] && values[gate.cornerV * 2ULL + 1] == values[first * 2ULL + 1]
                    && values[gate.cornerU * 2ULL] == values[(first + 1) * 2ULL] && values[gate.cornerU * 2ULL + 1] == values[(first + 1) * 2ULL + 1])
                    far = gate.cornerThird;
                int side;
                if (far == None)
                    side = (int)s.read(3);
                else
                {
                    double across = (double)((int64_t)ub0 - ua0) * (double)((int64_t)values[far * 2ULL + 1] - ua1)
                        - (double)((int64_t)ub1 - ua1) * (double)((int64_t)values[far * 2ULL] - ua0);
                    side = across > 0.0 ? 1 : 0;
                }
                base[0] = side ? two[0] : one[0];
                base[1] = side ? two[1] : one[1];
            }
            for (int part = 0; part < 2; part++)
            {
                int context = 4 + history[part] / 4;
                if (paired)
                    context = part ? contexts + std::min(history[0] / 2, 7) : contexts + 8 + (int)std::min<uint32_t>(at, 2) * 4 + history[0] / 4;
                int64_t difference = s.readNumber(context);
                values[corner * 2ULL + part] = (int32_t)((int64_t)std::nearbyint(base[part]) + difference);
                history[part] = magnitude(difference);
            }
            seen.record(m, corner, values);
            last[0] = values[corner * 2ULL];
            last[1] = values[corner * 2ULL + 1];
        }
    }
}

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
    uint32_t sectionCount = readAt<uint16_t>(data + 6);
    Sections sections;
    for (uint32_t at = 0; at < sectionCount; at++)
    {
        const uint8_t* entry = data + 16 + at * 8;
        sections.at[{readAt<uint16_t>(entry), entry[3]}] = data + readAt<uint32_t>(entry + 4);
    }
    const uint8_t* info = sections.find(1);
    Mesh m;
    m.vertices = readAt<uint32_t>(info);
    m.faces = readAt<uint32_t>(info + 4);
    m.cornerCount = readAt<uint32_t>(info + 8);
    m.arity = readAt<uint32_t>(info + 20);
    std::vector<const uint8_t*> uvData;
    for (int layer = 0; sections.find(282, layer); layer++)
        uvData.push_back(sections.find(282, layer));
    const uint8_t* connectivity = sections.find(278);
    const uint8_t* splits = sections.find(279);
    const uint8_t* positions = sections.find(280) + 4;
    const uint8_t* normals = sections.find(281);
    setConnectivityModel(m, *connectivity++);
    m.splitModel = *splits++;
    uint8_t positionForm = *positions++;
    int keying = positionForm >> 2 & 3;
    bool shrink = (positionForm & 16) != 0;
    Stream connectivityStream, splitStream, positionStream, normalStream;
    std::vector<Stream> uvStreams(uvData.size());
    connectivityStream.open(connectivity, m.connectivityContexts);
    splitStream.open(splits, m.splitModel == 2 ? 128 : 8);
    positionStream.open(positions, keying == 2 ? 30 : keying == 1 ? 48 : 4);
    positionStream.fine = positionForm & 3;
    bool normalsByVertex = normals && (normals[0] & 1);
    bool normalsFine = normals && (normals[0] & 2);
    int normalLookup = normals ? normals[0] >> 2 & 3 : 0;
    bool normalsPaired = normals && (normals[0] & 16);
    if (normals)
        normals++;
    if (normals)
    {
        normalStream.open(normals, 13 + (normalLookup == 2 ? 3 : 0) + (normalsPaired ? 8 : 0));
        normalStream.fine = normalsFine;
    }
    std::vector<int> uvLookups;
    std::vector<bool> uvPaired;
    for (size_t layer = 0; layer < uvData.size(); layer++)
    {
        bool uvFine = (uvData[layer][0] & 1) != 0;
        uvLookups.push_back(uvData[layer][0] >> 1 & 3);
        uvPaired.push_back((uvData[layer][0] & 8) != 0);
        uvData[layer]++;
        uvStreams[layer].open(uvData[layer], 8 + (uvLookups.back() == 2 ? 3 : 0) + (uvPaired.back() ? 8 + 12 : 0));
        uvStreams[layer].fine = uvFine;
    }
    Lookup seen;
    const uint8_t* wordStarts[4] = {connectivityStream.wordAt, splitStream.wordAt, positionStream.wordAt, normalStream.wordAt};
    const uint8_t* rawStarts[4] = {connectivityStream.rawAt, splitStream.rawAt, positionStream.rawAt, normalStream.rawAt};
    std::vector<const uint8_t*> uvWords, uvRaw;
    for (auto& stream : uvStreams)
    {
        uvWords.push_back(stream.wordAt);
        uvRaw.push_back(stream.rawAt);
    }
    uint32_t bound = m.cornerCount + 1;
    m.headInto.resize(bound);
    m.headOut.resize(bound);
    m.nodeFrom.resize(bound);
    m.nodeTo.resize(bound);
    m.nextInto.resize(bound);
    m.nextOut.resize(bound);
    m.head.resize(bound);
    m.tail.resize(bound);
    m.nextSeen.resize(bound);
    m.seenCount.resize(bound);
    m.faceTotal.resize(bound);
    m.facePlaced.resize(bound);
    m.across.resize(bound);
    m.nodeCornerFrom.resize(bound);
    m.nodeCornerTo.resize(bound);
    m.seamVertex.resize(bound);
    m.uvs.resize(uvData.size());
    std::vector<double> times;
    for (int round = 0; round < Rounds; round++)
    {
        auto start = std::chrono::steady_clock::now();
        for (int run = 0; run < RunsPerRound; run++)
        {
            connectivityStream.restart(connectivity);
            connectivityStream.wordAt = wordStarts[0];
            connectivityStream.rawAt = rawStarts[0];
            splitStream.restart(splits);
            splitStream.wordAt = wordStarts[1];
            splitStream.rawAt = rawStarts[1];
            positionStream.restart(positions);
            positionStream.wordAt = wordStarts[2];
            positionStream.rawAt = rawStarts[2];
            readConnectivity(connectivityStream, m);
            readSplits(splitStream, m);
            readPositions(positionStream, m, keying, shrink);
            if (normals)
            {
                normalStream.restart(normals);
                normalStream.wordAt = wordStarts[3];
                normalStream.rawAt = rawStarts[3];
                readNormals(normalStream, m, normalsByVertex, seen, normalLookup, normalsPaired);
            }
            for (size_t layer = 0; layer < uvData.size(); layer++)
            {
                uvStreams[layer].restart(uvData[layer]);
                uvStreams[layer].wordAt = uvWords[layer];
                uvStreams[layer].rawAt = uvRaw[layer];
                readUvs(uvStreams[layer], m, m.uvs[layer], seen, uvLookups[layer], uvPaired[layer]);
            }
        }
        std::chrono::duration<double, std::micro> took = std::chrono::steady_clock::now() - start;
        times.push_back(took.count() / RunsPerRound);
    }
    std::sort(times.begin(), times.end());
    uint64_t hash = 14695981039346656037ULL;
    for (uint32_t position : m.positionOf)
    {
        for (int axis = 0; axis < 3; axis++)
            hash = mix(hash, m.rows[position * 3ULL + axis]);
    }
    for (int32_t value : m.extras)
        hash = mix(hash, value);
    for (uint32_t corner : m.corners)
        hash = mix(hash, corner);
    if (normals)
    {
        for (int32_t value : m.normals)
            hash = mix(hash, value);
    }
    for (auto& layer : m.uvs)
    {
        for (int32_t value : layer)
            hash = mix(hash, value);
    }
    printf("{\"decode_us\":%.1f,\"checksum\":\"%016llx\"}\n", times[Rounds / 2], (unsigned long long)hash);
    return 0;
}
