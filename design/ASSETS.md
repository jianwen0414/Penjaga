# 3D assets and licences

All third-party assets are from **Poly Haven** (https://polyhaven.com) and are released under **CC0 1.0** (public domain;
no attribution required, commercial use and IP transfer allowed — compatible with the hackathon IP terms, PRD §1.3).
Downloaded 2026-10-15 via the official Poly Haven API (`api.polyhaven.com`). Textures converted to WebP; models
optimised with glTF-Transform 4.5.1 (meshopt compression, WebP textures ≤1024 px, mesh simplification).

| Asset | Type | Used for | Source |
|---|---|---|---|
| laminate_floor_02 | texture | bedroom / family oak floor | https://polyhaven.com/a/laminate_floor_02 |
| painted_plaster_wall | texture (normal + roughness only) | wall paint surface | https://polyhaven.com/a/painted_plaster_wall |
| oak_veneer_01 | texture | oak furniture, doors, stair treads | https://polyhaven.com/a/oak_veneer_01 |
| natural_walnut_veneer | texture | main door, trays | https://polyhaven.com/a/natural_walnut_veneer |
| marble_01 | texture | worktops / vanity tops | https://polyhaven.com/a/marble_01 |
| rough_linen | texture (normal + roughness only) | bedding, cushions | https://polyhaven.com/a/rough_linen |
| poly_wool_herringbone | texture (normal + roughness only) | sofa upholstery | https://polyhaven.com/a/poly_wool_herringbone |
| brown_photostudio_02 | HDRI (1k) | image-based lighting / reflections | https://polyhaven.com/a/brown_photostudio_02 |
| potted_plant_01, potted_plant_02, potted_plant_04 | model | plants | https://polyhaven.com/models |
| ceramic_vase_01, ceramic_vase_03 | model | decor | https://polyhaven.com/models |
| ceiling_fan | model | ceiling fans (blades animated) | https://polyhaven.com/a/ceiling_fan |
| exterior_aircon_unit | model | aircon condensers on the porch roof | https://polyhaven.com/a/exterior_aircon_unit |

Everything else (walls, furniture, fixtures, cars, pipes, cables) is generated procedurally in `src/` from `house.spec.json`.
Every asset has a procedural fallback, so the model still renders if an asset fails to load.
