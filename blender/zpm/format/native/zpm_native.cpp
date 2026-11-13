// SPDX-License-Identifier: GPL-3.0-or-later
// Native reader of a packed mesh's geometry: connectivity, splits, positions, normals,
// UVs and the edges faces reach. Python's reader in blender/zpm/format/codec/packed is
// the reference; this one must return exactly what it returns, or refuse.
//
// It refuses anything Python refuses, and anything it is not sure it reads alike, such
// as values past 32 bits; the caller then reads with Python. It never reads or writes
// past a buffer it was given, so a damaged file cannot crash the program holding it.
//
// zig c++ -O2 -std=c++17 -shared -ffp-contract=off zpm_native.cpp -o zpm_native.dll
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

namespace
{
const uint32_t None = 0xFFFFFFFFu;
// Rows past this stay with Python, so every product below fits in 64 bits.
const int64_t LargestRow = 1LL << 29;
const int64_t LargestValue = 0x7FFFFFFFLL;
const int TotalSlots = 1024;
const int ProbabilityBits = 10;
const uint32_t LowestState = 1u << 16;
const int ChoiceEscape = 200;
const int MaxGolombZeros = 24;
const int MaxRawBits = 62;
const uint32_t MaxSteps = 64;
const uint32_t HistoryMinRows = 4000;
const int NormalMax = 4095;
const double UvScale = 65536.0;

struct Refused
{
};

inline void need(bool ok)
{
    if (!ok)
        throw Refused{};
}

template <class T> T readAt(const uint8_t* at)
{
    T value;
    memcpy(&value, at, sizeof(T));
    return value;
}

inline int magnitude(int64_t value)
{
    uint64_t v = (uint64_t)(value < 0 ? -value : value);
    int bits = v ? 64 - __builtin_clzll(v) : 0;
    return bits < 14 ? bits : 14;
}

inline int64_t floorDivide(int64_t a, int64_t b)
{
    int64_t q = a / b;
    return (a % b != 0 && (a < 0) != (b < 0)) ? q - 1 : q;
}

inline int32_t checkedValue(int64_t value)
{
    need(value >= -LargestValue - 1 && value <= LargestValue);
    return (int32_t)value;
}

inline int32_t checkedRow(int64_t value)
{
    need(value >= -LargestRow && value <= LargestRow);
    return (int32_t)value;
}

// One static-table rANS stream, as storage/rans.py writes it.
struct Stream
{
    uint32_t state = 0;
    const uint8_t* wordAt = nullptr;
    const uint8_t* wordEnd = nullptr;
    const uint8_t* rawAt = nullptr;
    const uint8_t* rawEnd = nullptr;
    uint64_t pending = 0;
    int pendingBits = 0;
    int fine = 0;
    std::vector<uint32_t> entries;
    std::vector<uint8_t> hasTable;

    struct Bits
    {
        const uint8_t* at;
        const uint8_t* end;
        uint64_t pending = 0;
        int count = 0;

        uint64_t take(int bits)
        {
            while (count < bits)
            {
                need(at < end);
                pending |= (uint64_t)*at++ << count;
                count += 8;
            }
            uint64_t value = bits ? pending & ((1ULL << bits) - 1) : 0;
            pending = bits < 64 ? pending >> bits : 0;
            count -= bits;
            return value;
        }

        uint64_t golomb(int k)
        {
            int zeros = 0;
            while (!take(1))
            {
                zeros++;
                need(zeros <= MaxGolombZeros);
            }
            int top = zeros + k;
            return ((1ULL << top) | take(top)) - (1ULL << k);
        }
    };

    static uint64_t readVarint(const uint8_t*& at, const uint8_t* end)
    {
        uint64_t value = 0;
        for (int shift = 0;; shift += 7)
        {
            need(at < end && shift <= 28);
            uint8_t byte = *at++;
            value |= (uint64_t)(byte & 0x7F) << shift;
            if (byte < 0x80)
                return value;
        }
    }

    void open(const uint8_t* data, size_t size, int contexts, int fineLevel)
    {
        const uint8_t* end = data + size;
        need(size >= 4);
        state = readAt<uint32_t>(data);
        const uint8_t* at = data + 4;
        uint64_t words = readVarint(at, end);
        uint64_t rawBytes = readVarint(at, end);
        Bits bits{at, end};
        entries.assign((size_t)contexts * TotalSlots, 0);
        hasTable.assign((size_t)contexts, 0);
        uint32_t symbols[256];
        uint64_t frequencies[256];
        for (int context = 0; context < contexts; context++)
        {
            uint64_t count = bits.golomb(0);
            if (!count)
                continue;
            need(count <= 256);
            int k = (int)bits.take(4);
            int64_t previous = -1;
            for (uint64_t entry = 0; entry < count; entry++)
            {
                previous += (int64_t)bits.golomb(0) + 1;
                need(previous <= 255);
                symbols[entry] = (uint32_t)previous;
            }
            uint64_t used = 0;
            for (uint64_t entry = 0; entry + 1 < count; entry++)
            {
                frequencies[entry] = bits.golomb(k) + 1;
                used += frequencies[entry];
                need(used < TotalSlots);
            }
            need(used < TotalSlots);
            frequencies[count - 1] = TotalSlots - used;
            uint32_t slot = 0;
            for (uint64_t entry = 0; entry < count; entry++)
            {
                uint32_t frequency = (uint32_t)frequencies[entry];
                for (uint32_t step = 0; step < frequency; step++)
                    entries[(size_t)context * TotalSlots + slot + step] = symbols[entry] | (frequency << 8) | (step << 20);
                slot += frequency;
            }
            hasTable[context] = 1;
        }
        wordAt = bits.at;
        need((uint64_t)(end - wordAt) >= words * 2 && (uint64_t)(end - wordAt) - words * 2 >= rawBytes);
        wordEnd = wordAt + words * 2;
        rawAt = wordEnd;
        rawEnd = rawAt + rawBytes;
        pending = 0;
        pendingBits = 0;
        fine = fineLevel;
    }

    inline uint32_t read(int context)
    {
        need(context >= 0 && (size_t)context < hasTable.size() && hasTable[context]);
        uint32_t entry = entries[(size_t)context * TotalSlots + (state & (TotalSlots - 1))];
        state = ((entry >> 8) & 0xFFF) * (state >> ProbabilityBits) + (entry >> 20);
        if (state < LowestState)
        {
            need(wordAt < wordEnd);
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
                need(extra <= MaxRawBits);
                stored = (uint64_t)(4 | (symbol & 3)) << extra;
            }
            else if (fine)
            {
                extra = (int)((symbol + 2) / 2) - 2;
                need(extra <= MaxRawBits);
                stored = (uint64_t)(2 | (symbol & 1)) << extra;
            }
            else
            {
                extra = (int)symbol - 1;
                need(extra <= MaxRawBits);
                stored = 1ULL << extra;
            }
            while (pendingBits < extra)
            {
                need(rawAt < rawEnd);
                pending |= (uint64_t)*rawAt++ << pendingBits;
                pendingBits += 8;
            }
            stored |= pending & ((1ULL << extra) - 1);
            pending = extra < 64 ? pending >> extra : 0;
            pendingBits -= extra;
        }
        return (stored & 1) ? -(int64_t)(stored >> 1) - 1 : (int64_t)(stored >> 1);
    }

    inline int64_t readChoice(int context)
    {
        uint32_t choice = read(context);
        return choice == ChoiceEscape ? ChoiceEscape + readNumber(context) : (int64_t)choice;
    }
};

struct Gate
{
    uint32_t u, v, cornerU, cornerV, cornerThird;
};

struct Mesh
{
    uint32_t vertices = 0, faces = 0, cornerCount = 0, arity = 0;
    uint32_t positionCount = 0;
    std::vector<uint32_t> faceStart, welded, corners, positionOf;
    std::vector<Gate> faceGate;
    std::vector<int32_t> rows, extras;
    std::vector<uint32_t> headInto, headOut, nodeFrom, nodeTo, nextInto, nextOut;
    std::vector<uint32_t> head, tail, nextSeen;
    std::vector<uint8_t> seenCount;
    std::vector<uint32_t> recent;
    std::vector<Gate> gates;
    std::vector<uint32_t> faceTotal, facePlaced;
    std::vector<uint32_t> across, nodeCornerFrom, nodeCornerTo;
    std::vector<uint8_t> seamVertex;
    int connectivityModel = 0, contextGate = 10, contextFar = 11, contextArity = 12;
    int contextSteps = 13, contextCount = 14, connectivityContexts = 14;
    int splitModel = 0;

    void size()
    {
        size_t bound = (size_t)cornerCount + 1;
        headInto.assign(bound, None);
        headOut.assign(bound, None);
        nodeFrom.assign(bound, 0);
        nodeTo.assign(bound, 0);
        nextInto.assign(bound, None);
        nextOut.assign(bound, None);
        head.assign(bound, 0);
        tail.assign(bound, 0);
        nextSeen.assign(bound, 0);
        seenCount.assign(bound, 0);
        faceTotal.assign(bound, 0);
        facePlaced.assign(bound, 0);
        across.assign(bound, None);
        nodeCornerFrom.assign(bound, 0);
        nodeCornerTo.assign(bound, 0);
        seamVertex.assign(bound, 0);
    }
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
    int left = facesLeft(m, position);
    return 1 + (left < 0 ? 0 : left > 3 ? 3 : left);
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

// Removes the newest open edge from u to v, which must be open.
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

struct WalkState
{
    int64_t newest = -1;
    uint32_t previous = 0, before = 0, nodes = 0, previousCount = 6;
};

uint32_t readCorner(Stream& s, Mesh& m, uint32_t first, uint32_t at, uint32_t count, WalkState& w)
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
        context = (int)(w.previous < 4 ? w.previous : 4) * 2 + last;
        if (m.connectivityModel == 1)
            context = (context * 5 + (int)(w.before < 4 ? w.before : 4)) * 4 + kinds;
    }
    uint32_t symbol = s.read(context);
    need(symbol <= 6);
    w.before = w.previous;
    w.previous = symbol;
    uint32_t vertex;
    if (symbol == 0)
    {
        w.newest++;
        need(w.newest < (int64_t)m.cornerCount);
        vertex = (uint32_t)w.newest;
        if (m.connectivityModel == 2)
        {
            int firstCount = hasRing ? 1 + (int)(m.faceTotal[m.welded[first]] < 9 ? m.faceTotal[m.welded[first]] : 9) : 0;
            int64_t total = s.readChoice(m.contextCount + (int)(w.previousCount < 9 ? w.previousCount : 9) * 11 + firstCount);
            need(total >= 1 && total <= (int64_t)m.cornerCount);
            m.faceTotal[vertex] = (uint32_t)total;
            m.facePlaced[vertex] = 0;
            w.previousCount = (uint32_t)total;
        }
        return vertex;
    }
    if (symbol == 4)
    {
        int64_t named = w.newest - s.readNumber(m.contextFar);
        need(named >= 0 && named <= w.newest);
        return (uint32_t)named;
    }
    need(hasRing);
    if (symbol == 5 || symbol == 6)
    {
        int64_t steps = s.readChoice(m.contextSteps) + 1;
        need(steps >= 1 && steps <= (int64_t)MaxSteps);
        vertex = symbol == 5 ? m.welded.back() : m.welded[first];
        for (int64_t step = 0; step < steps; step++)
        {
            uint32_t node = symbol == 5 ? m.headOut[vertex] : m.headInto[vertex];
            need(node != None);
            vertex = symbol == 5 ? m.nodeTo[node] : m.nodeFrom[node];
        }
        return vertex;
    }
    if (symbol == 1)
    {
        need(m.headInto[m.welded.back()] != None);
        return m.nodeFrom[m.headInto[m.welded.back()]];
    }
    if (symbol == 2)
    {
        need(m.headOut[m.welded[first]] != None);
        return m.nodeTo[m.headOut[m.welded[first]]];
    }
    uint32_t newestInto = m.headInto[m.welded.back()];
    need(newestInto != None && m.nextInto[newestInto] != None);
    return m.nodeFrom[m.nextInto[newestInto]];
}

void readFace(Stream& s, Mesh& m, int known, uint32_t k0, uint32_t k1, const Gate& reached, WalkState& w)
{
    int64_t count = m.arity ? (int64_t)m.arity : s.readChoice(m.contextArity);
    need(count >= 3 && (int64_t)m.welded.size() + count <= (int64_t)m.cornerCount);
    uint32_t first = (uint32_t)m.welded.size();
    m.faceStart.push_back(first);
    m.faceGate.push_back(reached);
    if (known)
    {
        m.welded.push_back(k0);
        m.welded.push_back(k1);
    }
    for (uint32_t at = (uint32_t)known; at < count; at++)
        m.welded.push_back(readCorner(s, m, first, at, (uint32_t)count, w));
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
        uint32_t node = w.nodes++;
        need(node < m.headInto.size());
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
    WalkState w;
    const Gate noGate = {0, 0, 0, 0, None};
    while (m.faceStart.size() < m.faces)
    {
        readFace(s, m, 0, 0, 0, noGate, w);
        while (!m.gates.empty() && m.faceStart.size() < m.faces)
        {
            Gate gate = m.gates.back();
            m.gates.pop_back();
            if (!isOpen(m, gate.u, gate.v))
                continue;
            int context = m.contextGate;
            if (m.connectivityModel == 2)
            {
                int left_u = facesLeft(m, gate.u), left_v = facesLeft(m, gate.v);
                context += (left_u < 2 ? left_u : 2) * 3 + (left_v < 2 ? left_v : 2);
            }
            if (s.read(context))
                readFace(s, m, 2, gate.v, gate.u, gate, w);
            else
                removeOpen(m, gate.u, gate.v);
        }
    }
    m.faceStart.push_back((uint32_t)m.welded.size());
    need(m.welded.size() == m.cornerCount);
    m.positionCount = (uint32_t)(w.newest + 1);
}

struct SeenList
{
    std::vector<uint32_t> head, tail, next, count;

    void reset(size_t keys, size_t corners)
    {
        count.assign(keys, 0);
        head.assign(keys, 0);
        tail.assign(keys, 0);
        next.assign(corners, 0);
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

struct Lookup
{
    SeenList byPosition, byVertex;
    int lookup = 0, vertexContexts = 0;

    void reset(const Mesh& m, int mode, int contexts)
    {
        lookup = mode;
        vertexContexts = contexts;
        byPosition.reset(m.positionCount, m.welded.size());
        byVertex.reset(m.positionOf.size(), m.welded.size());
    }

    SeenList& consult(const Mesh& m, uint32_t corner, uint32_t& key, int& context)
    {
        uint32_t vertex = m.corners[corner];
        if (lookup == 1 || (lookup == 2 && byVertex.count[vertex]))
        {
            key = vertex;
            uint32_t seen = byVertex.count[vertex];
            context = (int)(seen < 2 ? seen : 2) + (lookup == 2 ? vertexContexts : 0);
            return byVertex;
        }
        key = m.welded[corner];
        uint32_t seen = byPosition.count[key];
        context = (int)(seen < 2 ? seen : 2);
        return byPosition;
    }

    void record(const Mesh& m, uint32_t corner, const int32_t* values)
    {
        if (lookup != 1)
            byPosition.addIfNew(m.welded[corner], corner, values);
        if (lookup != 0)
            byVertex.addIfNew(m.corners[corner], corner, values);
    }
};

void readSplits(Stream& s, Mesh& m)
{
    m.corners.assign(m.welded.size(), 0);
    m.recent.assign(m.positionCount, 0);
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
            int64_t at = 0;
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
                    at = s.readChoice((int)(count < 3 ? count : 3) + (fromGate ? 4 : 0) + extra);
                    need(at >= 0 && at <= (int64_t)total);
                    if (at)
                        vertex = options[at - 1];
                }
                else
                {
                    at = s.readChoice(count < 3 ? (int)count : 3);
                    need(at >= 0 && at <= (int64_t)count);
                    if (at)
                    {
                        vertex = m.head[position];
                        for (int64_t step = 1; step < at; step++)
                            vertex = m.nextSeen[vertex];
                    }
                }
            }
            if (count)
                faceChoice = at == 0 ? 1 : at == 1 ? 3 : 2;
            if (vertex == None)
            {
                // Python keeps any number of vertices at one position; past 254 it reads.
                need(count < 255 && m.positionOf.size() < m.vertices);
                vertex = (uint32_t)m.positionOf.size();
                m.positionOf.push_back(position);
                if (count)
                    m.seamVertex[vertex] = 1;
                if (count)
                    m.nextSeen[m.tail[position]] = vertex;
                else
                    m.head[position] = vertex;
                m.tail[position] = vertex;
                m.seenCount[position] = (uint8_t)(count + 1);
            }
            m.corners[corner] = vertex;
            m.recent[position] = vertex;
        }
    }
}

void readPositions(Stream& s, Mesh& m, int keying, bool shrink)
{
    m.rows.assign((size_t)m.positionCount * 3, 0);
    int64_t previous[3] = {0, 0, 0};
    int history[3] = {0, 0, 0};
    bool usesHistory = m.positionCount >= HistoryMinRows;
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
            int64_t guess[3] = {previous[0], previous[1], previous[2]};
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
                            guess[axis] = floorDivide(7 * ((int64_t)a[axis] + b[axis]) - 6 * (int64_t)c[axis] + 4, 8);
                        else
                            guess[axis] = (int64_t)a[axis] + b[axis] - c[axis];
                    }
                    if (keying == 2)
                    {
                        int64_t ux = (int64_t)a[0] - c[0], uy = (int64_t)a[1] - c[1], uz = (int64_t)a[2] - c[2];
                        int64_t vx = (int64_t)b[0] - c[0], vy = (int64_t)b[1] - c[1], vz = (int64_t)b[2] - c[2];
                        int64_t normal[3] = {std::llabs(uy * vz - uz * vy), std::llabs(uz * vx - ux * vz), std::llabs(ux * vy - uy * vx)};
                        int64_t total = normal[0] + normal[1] + normal[2];
                        if (total)
                        {
                            for (int axis = 0; axis < 3; axis++)
                            {
                                int64_t share = 4 * normal[axis] / total;
                                shares[axis] = (int)(share < 3 ? share : 3);
                            }
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
                    context = (shares[axis] + 1) * 6 + (history[axis] / 3 < 5 ? history[axis] / 3 : 5);
                else if (keying == 1)
                {
                    int widest = axis ? reach : history[axis];
                    context = (widest / 2 < 7 ? widest / 2 : 7) * 6 + (history[axis] / 3 < 5 ? history[axis] / 3 : 5);
                }
                else
                    context = usesHistory ? (history[axis] / 4 < 3 ? history[axis] / 4 : 3) : 0;
                int64_t difference = s.readNumber(context);
                row[axis] = checkedRow(guess[axis] + difference);
                history[axis] = magnitude(difference);
                reach = reach > history[axis] ? reach : history[axis];
                previous[axis] = row[axis];
            }
            made++;
        }
    }
    need(made == m.positionCount);
    need(m.positionOf.size() <= m.vertices);
    uint32_t extra = m.vertices - (uint32_t)m.positionOf.size();
    m.extras.assign((size_t)extra * 3, 0);
    for (uint32_t at = 0; at < extra; at++)
    {
        for (int axis = 0; axis < 3; axis++)
        {
            previous[axis] = checkedRow(previous[axis] + s.readNumber(0));
            m.extras[at * 3ULL + axis] = (int32_t)previous[axis];
        }
    }
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
    a = (int64_t)std::nearbyint((u * 0.5 + 0.5) * NormalMax);
    b = (int64_t)std::nearbyint((v * 0.5 + 0.5) * NormalMax);
}

// As quantize.decode_normal: the unit direction two stored values stand for.
void decodeNormal(int32_t a, int32_t b, float* out)
{
    double u = (double)a / NormalMax * 2 - 1, v = (double)b / NormalMax * 2 - 1;
    double z = 1 - std::fabs(u) - std::fabs(v);
    if (z < 0)
    {
        double nu = (1 - std::fabs(v)) * (u >= 0 ? 1 : -1);
        double nv = (1 - std::fabs(u)) * (v >= 0 ? 1 : -1);
        u = nu;
        v = nv;
    }
    double length = std::sqrt(u * u + v * v + z * z);
    out[0] = (float)(u / length);
    out[1] = (float)(v / length);
    out[2] = (float)(z / length);
}

void readNormals(Stream& s, Mesh& m, bool byVertex, Lookup& seen, int lookup, bool paired, int32_t* values)
{
    const uint32_t* keys = byVertex ? m.corners.data() : m.welded.data();
    size_t keyCount = byVertex ? m.positionOf.size() : m.positionCount;
    std::vector<__int128> totals(keyCount * 3, 0);
    const int32_t* rows = m.rows.data();
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
            int64_t ux = (int64_t)pb[0] - pa[0], uy = (int64_t)pb[1] - pa[1], uz = (int64_t)pb[2] - pa[2];
            int64_t vx = (int64_t)pc[0] - pa[0], vy = (int64_t)pc[1] - pa[1], vz = (int64_t)pc[2] - pa[2];
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
            int64_t at = s.readChoice(context);
            need(at >= 0 && at <= (int64_t)count);
            if (at)
            {
                uint32_t found = list.nth(key, (uint32_t)at);
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
            int partContext = 3 + offset * 5 + history[part] / 3;
            if (paired && part)
                partContext = 13 + (lookup == 2 ? 3 : 0) + (history[0] / 2 < 7 ? history[0] / 2 : 7);
            int64_t difference = s.readNumber(partContext);
            values[corner * 2ULL + part] = checkedValue(base[part] + difference);
            history[part] = magnitude(difference);
        }
        seen.record(m, corner, values);
    }
}

void readUvs(Stream& s, Mesh& m, int32_t* values, Lookup& seen, int lookup, bool paired)
{
    int contexts = 8 + (lookup == 2 ? 3 : 0);
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
                int64_t choice = s.readChoice(context);
                need(choice >= 0 && choice <= (int64_t)count);
                if (choice)
                {
                    uint32_t found = list.nth(key, (uint32_t)choice);
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
                double ex = (double)((int64_t)pb[0] - pa[0]), ey = (double)((int64_t)pb[1] - pa[1]), ez = (double)((int64_t)pb[2] - pa[2]);
                double qx = (double)((int64_t)pv[0] - pa[0]), qy = (double)((int64_t)pv[1] - pa[1]), qz = (double)((int64_t)pv[2] - pa[2]);
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
                {
                    uint32_t symbol = s.read(3);
                    need(symbol <= 1);
                    side = (int)symbol;
                }
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
                int partContext = 4 + history[part] / 4;
                if (paired)
                    partContext = part ? contexts + (history[0] / 2 < 7 ? history[0] / 2 : 7) : contexts + 8 + (int)(at < 2 ? at : 2) * 4 + history[0] / 4;
                int64_t difference = s.readNumber(partContext);
                need(std::isfinite(base[part]) && std::fabs(base[part]) < 2147483648.0);
                values[corner * 2ULL + part] = checkedValue((int64_t)std::nearbyint(base[part]) + difference);
                history[part] = magnitude(difference);
            }
            seen.record(m, corner, values);
            last[0] = values[corner * 2ULL];
            last[1] = values[corner * 2ULL + 1];
        }
    }
}

// The edges faces reach, each once as (lower, higher) in the order first reached. Each
// vertex lists the higher ends of its edges; a vertex has few, so a scan is quick.
uint32_t faceEdges(const Mesh& m, uint32_t* edges)
{
    std::vector<uint32_t> first(m.positionOf.size(), None), next(m.welded.size());
    uint32_t count = 0;
    for (uint32_t face = 0; face < m.faces; face++)
    {
        uint32_t start = m.faceStart[face], end = m.faceStart[face + 1];
        for (uint32_t corner = start; corner < end; corner++)
        {
            uint32_t a = m.corners[corner], b = m.corners[corner + 1 == end ? start : corner + 1];
            uint32_t low = a < b ? a : b, high = a < b ? b : a;
            uint32_t edge = first[low];
            while (edge != None && edges[edge * 2ULL + 1] != high)
                edge = next[edge];
            if (edge != None)
                continue;
            edges[count * 2ULL] = low;
            edges[count * 2ULL + 1] = high;
            next[count] = first[low];
            first[low] = count;
            count++;
        }
    }
    return count;
}

int keyingOf(uint8_t form)
{
    return form >> 2 & 3;
}
}

extern "C" {

// The streams of one packed mesh, each from its form byte on, and what MESH_INFO says.
struct ZpmPackedInput
{
    uint32_t vertices, faces, corners, arity;
    // Positions are grid rows times this.
    double unit;
    const uint8_t* connectivity;
    uint64_t connectivitySize;
    const uint8_t* splits;
    uint64_t splitsSize;
    const uint8_t* positions;
    uint64_t positionsSize;
    // Null when the mesh stores no normals.
    const uint8_t* normals;
    uint64_t normalsSize;
    uint32_t uvLayers;
    const uint8_t* const* uvs;
    const uint64_t* uvSizes;
};

// Buffers the caller owns: sized by the counts given, as the comments say.
struct ZpmPackedOutput
{
    uint32_t* welded;       // corners
    uint32_t* faceStarts;   // faces + 1
    uint32_t* corners;      // corners
    float* positions;       // vertices * 3
    int32_t* rows;          // corners * 3: grid rows of welded positions
    int32_t* normalPairs;   // corners * 2, or null without normals
    float* normals;         // corners * 3, or null without normals
    int32_t* uvPairs;       // uvLayers * corners * 2
    float* uvs;             // uvLayers * corners * 2
    uint32_t* edges;        // corners * 2, or null to skip edges
    int32_t* extras;        // vertices * 3: grid rows of vertices on no face, or null
    uint32_t positionCount;
    uint32_t edgeCount;
};

// Returns 0 with every output filled, or 1 when it refuses: the caller then reads the
// mesh with Python, which says why or reads what this did not.
int zpm_read_packed_geometry(const ZpmPackedInput* in, ZpmPackedOutput* out)
{
    try
    {
        Mesh m;
        m.vertices = in->vertices;
        m.faces = in->faces;
        m.cornerCount = in->corners;
        m.arity = in->arity;
        need(in->connectivitySize >= 1 && in->splitsSize >= 1 && in->positionsSize >= 1);
        need(in->connectivity[0] < 3 && in->splits[0] < 3);
        m.size();
        setConnectivityModel(m, in->connectivity[0]);
        Stream connectivity;
        connectivity.open(in->connectivity + 1, in->connectivitySize - 1, m.connectivityContexts, 0);
        readConnectivity(connectivity, m);
        m.splitModel = in->splits[0];
        Stream splits;
        splits.open(in->splits + 1, in->splitsSize - 1, m.splitModel == 2 ? 128 : 8, 0);
        readSplits(splits, m);
        uint8_t positionForm = in->positions[0];
        int keying = keyingOf(positionForm);
        need((positionForm & 3) < 3 && keying < 3 && !(positionForm >> 5));
        Stream positions;
        positions.open(in->positions + 1, in->positionsSize - 1, keying == 2 ? 30 : keying == 1 ? 48 : 4, positionForm & 3);
        readPositions(positions, m, keying, (positionForm & 16) != 0);
        Lookup seen;
        if (in->normals)
        {
            need(in->normalsSize >= 1);
            uint8_t form = in->normals[0];
            int lookup = form >> 2 & 3;
            need(lookup < 3 && !(form >> 5));
            bool paired = (form & 16) != 0;
            Stream normals;
            normals.open(in->normals + 1, in->normalsSize - 1, 13 + (lookup == 2 ? 3 : 0) + (paired ? 8 : 0), form & 2 ? 1 : 0);
            readNormals(normals, m, (form & 1) != 0, seen, lookup, paired, out->normalPairs);
            for (uint32_t corner = 0; corner < m.cornerCount; corner++)
                decodeNormal(out->normalPairs[corner * 2ULL], out->normalPairs[corner * 2ULL + 1], out->normals + corner * 3ULL);
        }
        for (uint32_t layer = 0; layer < in->uvLayers; layer++)
        {
            need(in->uvSizes[layer] >= 1);
            uint8_t form = in->uvs[layer][0];
            int lookup = form >> 1 & 3;
            need(lookup < 3 && !(form >> 4));
            bool paired = (form & 8) != 0;
            Stream uvs;
            uvs.open(in->uvs[layer] + 1, in->uvSizes[layer] - 1, 8 + (lookup == 2 ? 3 : 0) + (paired ? 8 + 12 : 0), form & 1);
            int32_t* pairs = out->uvPairs + (size_t)layer * m.cornerCount * 2;
            readUvs(uvs, m, pairs, seen, lookup, paired);
            float* values = out->uvs + (size_t)layer * m.cornerCount * 2;
            for (size_t at = 0; at < (size_t)m.cornerCount * 2; at++)
                values[at] = (float)(pairs[at] / UvScale);
        }
        const float largest = std::numeric_limits<float>::max();
        size_t written = 0;
        for (uint32_t position : m.positionOf)
        {
            for (int axis = 0; axis < 3; axis++)
            {
                double value = m.rows[position * 3ULL + axis] * in->unit;
                need(std::fabs(value) <= largest);
                out->positions[written++] = (float)value;
            }
        }
        for (int32_t row : m.extras)
        {
            double value = row * in->unit;
            need(std::fabs(value) <= largest);
            out->positions[written++] = (float)value;
        }
        memcpy(out->welded, m.welded.data(), m.welded.size() * sizeof(uint32_t));
        memcpy(out->faceStarts, m.faceStart.data(), m.faceStart.size() * sizeof(uint32_t));
        memcpy(out->corners, m.corners.data(), m.corners.size() * sizeof(uint32_t));
        memcpy(out->rows, m.rows.data(), m.rows.size() * sizeof(int32_t));
        if (out->extras)
            memcpy(out->extras, m.extras.data(), m.extras.size() * sizeof(int32_t));
        out->positionCount = m.positionCount;
        out->edgeCount = out->edges ? faceEdges(m, out->edges) : 0;
        return 0;
    }
    catch (...)
    {
        return 1;
    }
}
}
