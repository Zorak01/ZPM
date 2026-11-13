// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What one blendshape is, apart from the vertices it moves.</summary>
public readonly struct ZpmShapeTarget
{
    /// <summary>The value of inBetweenOf when a target is not an in-between.</summary>
    public const uint NoTarget = uint.MaxValue;

    /// <summary>The target this one is relative to; its own index if none.</summary>
    public readonly uint reference;

    /// <summary>The target this one shapes on its way to, or NoTarget.</summary>
    public readonly uint inBetweenOf;

    /// <summary>Its current weight, allowed range, place in time and in-between weight.</summary>
    public readonly float value, minimum, maximum, frame, inBetweenWeight;

    /// <summary>Whether it blends as an offset from reference, not by time between targets.</summary>
    public readonly bool isRelative;

    /// <summary>Whether it is switched off.</summary>
    public readonly bool isMuted;

    /// <summary>How an absolute target blends with its neighbours.</summary>
    public readonly ZpmShapeInterpolation interpolation;

    public ZpmShapeTarget(uint reference, uint inBetweenOf, float value, float minimum, float maximum, float frame,
        float inBetweenWeight, bool isRelative, bool isMuted, ZpmShapeInterpolation interpolation)
    {
        this.reference = reference;
        this.inBetweenOf = inBetweenOf;
        this.value = value;
        this.minimum = minimum;
        this.maximum = maximum;
        this.frame = frame;
        this.inBetweenWeight = inBetweenWeight;
        this.isRelative = isRelative;
        this.isMuted = isMuted;
        this.interpolation = interpolation;
    }
}
