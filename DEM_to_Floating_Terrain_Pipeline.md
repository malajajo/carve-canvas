# DEM → Floating Terrain Pipeline

## Objective

Convert real elevation data into a stylised floating terrain suitable
for later population.

------------------------------------------------------------------------

## Step 1 --- Obtain DEM

Possible sources:

-   Copernicus DEM
-   IGN France (for French resorts)
-   OpenTopography

Output: - GeoTIFF / heightmap

------------------------------------------------------------------------

## Step 2 --- Import

Import the DEM into Blender as a displaced mesh.

Result: - Accurate terrain - No stylisation

------------------------------------------------------------------------

## Step 3 --- Define the World Boundary

Instead of using administrative borders, choose a visually pleasing
polygon.

Design goals:

-   Preserve iconic peaks
-   Avoid awkward cut-offs
-   Leave space around villages
-   Create a strong silhouette

Future idea: AI-assisted boundary suggestion.

------------------------------------------------------------------------

## Step 4 --- Generate Floating Block

Automatically:

-   Duplicate outer edge
-   Extrude vertically
-   Bridge edge loops
-   Create vertical cliff walls

This should be procedural rather than manually modelled.

------------------------------------------------------------------------

## Step 5 --- Stylise

Apply non-destructive modifiers:

-   Smooth terrain
-   Reduce small bumps
-   Exaggerate major peaks
-   Deepen valleys
-   Round snow edges
-   Add snow overhang
-   Soften ridgelines

These become adjustable parameters.

------------------------------------------------------------------------

## Step 6 --- Save as Base Terrain

The result becomes the reusable foundation for:

-   pistes
-   lifts
-   villages
-   vegetation
-   buildings

The stylisation pipeline should work on many resorts with minimal
tuning.

## Future Automation

Eventually, the pipeline could become:

DEM → Boundary optimisation → Floating block generation → Terrain
stylisation → Ready for resort assets

The core intellectual property is not the terrain import itself, but the
repeatable transformation from real-world topography into a charming
miniature world.
