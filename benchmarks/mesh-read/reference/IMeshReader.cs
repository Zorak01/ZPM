// SPDX-License-Identifier: GPL-3.0-or-later

namespace Zpm.Benchmarks;

/// <summary>What the benchmark calls on any reader, so each runs the same code.</summary>
unsafe interface IMeshReader
{
    uint vertexCount { get; }
    uint faceCount { get; }
    uint cornerCount { get; }
    uint edgeCount { get; }
    bool Validate();
    ZpmVertex GetVertex(uint index);
    uint GetCorner(uint index);
    uint GetFace(uint face, uint* corners);
    ZpmEdge GetEdge(uint index);
    void GetVertices(uint first, uint length, uint* destination);
    void GetCorners(uint first, uint length, uint* destination);
    void GetEdges(uint first, uint length, uint* destination);
    void GetMesh(uint* positions, uint* faceSizes, uint* corners, uint* edges);
}

/// <summary>Lets the benchmark call ZpmMeshView, which knows nothing about benchmarks.</summary>
readonly unsafe struct ZpmReader : IMeshReader
{
    readonly ZpmMeshView _view;

    public ZpmReader(in ZpmMeshView view) => _view = view;

    public uint vertexCount => _view.vertexCount;
    public uint faceCount => _view.faceCount;
    public uint cornerCount => _view.cornerCount;
    public uint edgeCount => _view.edgeCount;
    public bool Validate() => _view.Validate();
    public ZpmVertex GetVertex(uint index) => _view.GetVertex(index);
    public uint GetCorner(uint index) => _view.GetCorner(index);
    public uint GetFace(uint face, uint* corners) => _view.GetFace(face, corners);
    public ZpmEdge GetEdge(uint index) => _view.GetEdge(index);
    public void GetVertices(uint first, uint length, uint* destination) => _view.GetVertices(first, length, destination);
    public void GetCorners(uint first, uint length, uint* destination) => _view.GetCorners(first, length, destination);
    public void GetEdges(uint first, uint length, uint* destination) => _view.GetEdges(first, length, destination);
    public void GetMesh(uint* positions, uint* faceSizes, uint* corners, uint* edges) => _view.GetMesh(positions, faceSizes, corners, edges);
}
