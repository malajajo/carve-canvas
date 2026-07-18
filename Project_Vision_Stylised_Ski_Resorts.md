# Project Vision: Stylised 3D Ski Resort Explorer

## Vision

Create a system that transforms real-world ski resorts into beautiful,
stylised floating 3D dioramas that are instantly recognisable while
sharing a consistent artistic style.

The product is **not** a GIS viewer or a realistic simulator. It is a
"terrain stylisation engine" that converts real geography into
collectible miniature worlds.

## Guiding Principles

-   Reality is the foundation.
-   Stylisation improves readability.
-   Geography should be recognisable, not perfectly literal.
-   Every resort should feel like it belongs to the same universe.

## High-Level Pipeline

1.  Acquire terrain (DEM + resort data)
2.  Convert DEM into a stylised floating terrain block
3.  Add ski infrastructure
4.  Populate procedurally (trees, rocks, villages)
5.  Apply materials and lighting
6.  Publish to an interactive experience

## Technology Stack

-   Blender
-   Geometry Nodes
-   Python
-   Unreal Engine (optional runtime)
-   AI coding assistant (Claude Code / ChatGPT)
-   AI image generation for concept art

## Biggest Technical Risks

1.  DEM → beautiful terrain
2.  Automatic floating-world boundary generation
3.  Consistent stylisation across resorts

## Long-Term Goal

Given any mountain on Earth, automatically produce a beautiful,
recognisable floating miniature world in the project's signature style.
