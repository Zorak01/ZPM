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
        uint32_t length = read(context);
        uint64_t stored = length;
        if (length > 1)
        {
            int extra = (int)length - 1;
            while (pendingBits < extra)
            {
                pending |= (uint64_t)*rawAt++ << pendingBits;
                pendingBits += 8;
            }
            stored = (1ULL << extra) | (pending & ((1ULL << extra) - 1));
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
    // Connectivity model 1 adds the symbol before last and which candidates exist.
    int connectivityModel = 0, contextGate = 10, contextFar = 11, contextArity = 12;
    int splitModel = 0;
};

int connectivityContexts(int model)
{
    return (model ? 10 * 20 : 10) + 3;
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

inline void removeOpen(Mesh& m, uint32_t u, uint32_t v)
{
    uint32_t* link = &m.headInto[v];
    while (m.nodeFrom[*link] != u)
        link = &m.nextInto[*link];
    *link = m.nextInto[*link];
    link = &m.headOut[u];
    while (m.nodeTo[*link] != v)
        link = &m.nextOut[*link];
    *link = m.nextOut[*link];
}

inline void readFace(Stream& s, Mesh& m, int known, uint32_t k0, uint32_t k1, const Gate& reached, uint32_t& newest, uint32_t& previous, uint32_t& before, uint32_t& nodes)
{
    uint32_t count = m.arity ? m.arity : (uint32_t)s.readNumber(m.contextArity);
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
        int context = (int)previous * 2 + (at == count - 1 ? 1 : 0);
        if (m.connectivityModel)
        {
            int kinds = 0;
            if (m.welded.size() > first)
                kinds = (m.headInto[m.welded.back()] != None ? 1 : 0) | (m.headOut[m.welded[first]] != None ? 2 : 0);
            context = (context * 5 + (int)before) * 4 + kinds;
        }
        uint32_t symbol = s.read(context);
        before = previous;
        previous = symbol;
        uint32_t vertex;
        if (symbol == 0)
            vertex = ++newest;
        else if (symbol == 4)
            vertex = newest - (uint32_t)s.readNumber(m.contextFar);
        else if (symbol == 1)
            vertex = m.nodeFrom[m.headInto[m.welded.back()]];
        else if (symbol == 2)
            vertex = m.nodeTo[m.headOut[m.welded[first]]];
        else
            vertex = m.nodeFrom[m.nextInto[m.headInto[m.welded.back()]]];
        m.welded.push_back(vertex);
    }
    for (uint32_t at = 0; at < count; at++)
    {
        uint32_t next = at + 1 == count ? 0 : at + 1;
        uint32_t u = m.welded[first + at], v = m.welded[first + next];
        if (isOpen(m, v, u))
        {
            removeOpen(m, v, u);
            continue;
        }
        uint32_t node = nodes++;
        m.nodeFrom[node] = u;
        m.nodeTo[node] = v;
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
    uint32_t newest = None, previous = 0, before = 0, nodes = 0;
    const Gate noGate = {0, 0, 0, 0, None};
    while (m.faceStart.size() < m.faces)
    {
        readFace(s, m, 0, 0, 0, noGate, newest, previous, before, nodes);
        while (!m.gates.empty() && m.faceStart.size() < m.faces)
        {
            Gate gate = m.gates.back();
            m.gates.pop_back();
            if (!isOpen(m, gate.u, gate.v))
                continue;
            if (s.read(m.contextGate))
                readFace(s, m, 2, gate.v, gate.u, gate, newest, previous, before, nodes);
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
void readSplits(Stream& s, Mesh& m)
{
    resetSeen(m);
    m.corners.resize(m.welded.size());
    m.recent.resize(m.positionCount);
    m.positionOf.clear();
    uint32_t options[256];
    for (uint32_t face = 0; face < m.faces; face++)
    {
        const Gate& gate = m.faceGate[face];
        uint32_t first = m.faceStart[face], end = m.faceStart[face + 1];
        for (uint32_t corner = first; corner < end; corner++)
        {
            uint32_t position = m.welded[corner];
            uint32_t count = m.seenCount[position];
            uint32_t vertex = None;
            if (count)
            {
                uint32_t at;
                if (m.splitModel)
                {
                    bool fromGate = gate.cornerThird != None && corner - first < 2;
                    uint32_t guess = fromGate ? m.corners[corner == first ? gate.cornerV : gate.cornerU] : m.recent[position];
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
                    at = s.readChoice((int)std::min<uint32_t>(count, 3) + (fromGate ? 4 : 0));
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
            if (vertex == None)
            {
                vertex = (uint32_t)m.positionOf.size();
                m.positionOf.push_back(position);
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

void readPositions(Stream& s, Mesh& m)
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
                        guess[axis] = a[axis] + b[axis] - c[axis];
                }
            }
            int32_t* row = rows + made * 3ULL;
            for (int axis = 0; axis < 3; axis++)
            {
                int context = usesHistory ? std::min(history[axis] / 4, 3) : 0;
                int64_t difference = s.readNumber(context);
                row[axis] = guess[axis] + (int32_t)difference;
                history[axis] = magnitude(difference);
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
void readNormals(Stream& s, Mesh& m, bool byVertex)
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
    resetSeen(m, keyCount);
    size_t cornerCount = m.welded.size();
    m.normals.resize(cornerCount * 2);
    int32_t* values = m.normals.data();
    int history[2] = {0, 0};
    for (uint32_t corner = 0; corner < cornerCount; corner++)
    {
        uint32_t position = keys[corner];
        uint32_t count = m.seenCount[position];
        if (count)
        {
            uint32_t at = s.readChoice(count < 2 ? (int)count : 2);
            if (at)
            {
                uint32_t found = m.head[position];
                for (uint32_t step = 1; step < at; step++)
                    found = m.nextSeen[found];
                values[corner * 2ULL] = values[found * 2ULL];
                values[corner * 2ULL + 1] = values[found * 2ULL + 1];
                continue;
            }
        }
        int64_t base[2];
        if (count)
        {
            base[0] = values[m.tail[position] * 2ULL];
            base[1] = values[m.tail[position] * 2ULL + 1];
        }
        else
            encodeNormal((double)totals[position * 3ULL], (double)totals[position * 3ULL + 1], (double)totals[position * 3ULL + 2], base[0], base[1]);
        int offset = count ? 1 : 0;
        for (int part = 0; part < 2; part++)
        {
            int64_t difference = s.readNumber(3 + offset * 5 + history[part] / 3);
            values[corner * 2ULL + part] = (int32_t)(base[part] + difference);
            history[part] = magnitude(difference);
        }
        if (count)
            m.nextSeen[m.tail[position]] = corner;
        else
            m.head[position] = corner;
        m.tail[position] = corner;
        m.seenCount[position] = (uint8_t)std::min<uint32_t>(count + 1, 255);
    }
}

void readUvs(Stream& s, Mesh& m, std::vector<int32_t>& store)
{
    resetSeen(m);
    store.resize(m.welded.size() * 2);
    int32_t* values = store.data();
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
            uint32_t count = m.seenCount[position];
            if (count)
            {
                uint32_t choice = s.readChoice(count < 2 ? (int)count : 2);
                if (choice)
                {
                    uint32_t found = m.head[position];
                    for (uint32_t step = 1; step < choice; step++)
                        found = m.nextSeen[found];
                    values[corner * 2ULL] = values[found * 2ULL];
                    values[corner * 2ULL + 1] = values[found * 2ULL + 1];
                    continue;
                }
            }
            uint32_t at = corner - first;
            double base[2];
            if (at == 0)
            {
                uint32_t from = m.tail[position];
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
                int64_t difference = s.readNumber(4 + history[part] / 4);
                values[corner * 2ULL + part] = (int32_t)((int64_t)std::nearbyint(base[part]) + difference);
                history[part] = magnitude(difference);
            }
            if (count)
                m.nextSeen[m.tail[position]] = corner;
            else
                m.head[position] = corner;
            m.tail[position] = corner;
            m.seenCount[position] = (uint8_t)std::min<uint32_t>(count + 1, 255);
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
    m.connectivityModel = *connectivity++;
    int contexts = connectivityContexts(m.connectivityModel);
    m.contextGate = contexts - 3;
    m.contextFar = contexts - 2;
    m.contextArity = contexts - 1;
    m.splitModel = *splits++;
    Stream connectivityStream, splitStream, positionStream, normalStream;
    std::vector<Stream> uvStreams(uvData.size());
    connectivityStream.open(connectivity, contexts);
    splitStream.open(splits, 8);
    positionStream.open(positions, 4);
    bool normalsByVertex = normals && normals[0] == 1;
    if (normals)
        normals++;
    if (normals)
        normalStream.open(normals, 13);
    for (size_t layer = 0; layer < uvData.size(); layer++)
        uvStreams[layer].open(uvData[layer], 8);
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
            readPositions(positionStream, m);
            if (normals)
            {
                normalStream.restart(normals);
                normalStream.wordAt = wordStarts[3];
                normalStream.rawAt = rawStarts[3];
                readNormals(normalStream, m, normalsByVertex);
            }
            for (size_t layer = 0; layer < uvData.size(); layer++)
            {
                uvStreams[layer].restart(uvData[layer]);
                uvStreams[layer].wordAt = uvWords[layer];
                uvStreams[layer].rawAt = uvRaw[layer];
                readUvs(uvStreams[layer], m, m.uvs[layer]);
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
