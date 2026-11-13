// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>
/// Reads corners that mostly rise, keeping its place in the strips, so each costs almost nothing
/// instead of a block decode. Lives on the caller's stack and allocates nothing.
/// </summary>
public unsafe struct ZpmCornerCursor
{
    readonly ZpmCornerStrips _strips;
    ZpmStripState _state;
    uint _block;
    bool _isStarted;

    public ZpmCornerCursor(in ZpmCornerStrips strips)
    {
        _strips = strips;
        _state = default;
        _block = 0;
        _isStarted = false;
    }

    /// <summary>Returns one corner's vertex. Going backward, or to a later block, starts its block again.</summary>
    public uint Get(uint corner)
    {
        if (_isStarted && corner >= _state.faceStart && corner < _state.faceStart + _state.faceSize)
            return _state.vertices[corner - _state.faceStart];
        uint block = corner / ZpmCornerStrips.CornersPerBlock;
        // A face starting in the previous block can reach into this one.
        bool isAhead = _isStarted && corner >= _state.faceStart && block == _block;
        if (!isAhead)
        {
            uint start = block;
            _strips.StartBlock(start, out _state);
            if (_state.faceStart > corner && start != 0)
                _strips.StartBlock(--start, out _state);
            _block = start;
            _isStarted = true;
        }
        while (corner >= _state.faceStart + _state.faceSize)
            _strips.Advance(ref _state);
        return _state.vertices[corner - _state.faceStart];
    }
}
