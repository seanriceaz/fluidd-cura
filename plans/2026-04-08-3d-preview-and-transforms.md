# Plan: 3D Preview + Rotation/Scale/Placement for fluidd-cura

## Context

The current slicer UI has no model preview. Users upload an STL, pick a profile, and slice blind — they can't see orientation, scale, or placement within the build volume. This adds an inline 3D preview panel between the upload and profile steps, with rotation (±90° per axis), uniform scale, and auto-placement (center on bed, drop to Z=0). Transforms are accumulated in the browser and applied server-side to the binary STL before CuraEngine runs.

---

## Files to Modify

| File | Change summary |
|------|---------------|
| `install.sh` | Download Three.js r128 + STLLoader + OrbitControls to `ui/` |
| `ui/index.html` | 3D canvas panel, transform controls, updated `startSlice()` |
| `moonraker-plugin/cura_slicer.py` | STL transform function, `transform` param in `/slice`, build volume in profile GET |

---

## Part 1 — install.sh

After the existing Vue download block, download three files to `$UI_DEST/`:

```
three.min.js         ← https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js
three.STLLoader.js   ← https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js
three.OrbitControls.js ← https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js
```

Use the same `wget -q -O` pattern already in the script with existence checks. Three.js r128 is used because it's the last version with non-ESM global builds (`THREE.STLLoader`, `THREE.OrbitControls`) compatible with the no-build-step single-HTML approach.

---

## Part 2 — ui/index.html

### 2.1 Script tags (in `<head>`, after vue.min.js)
```html
<script src="three.min.js"></script>
<script src="three.STLLoader.js"></script>
<script src="three.OrbitControls.js"></script>
```

### 2.2 CSS additions (inside existing `<style>`)
Minimal additions:
- `.preview-wrap` — 320px-tall dark container, `position:relative`, `overflow:hidden`
- `.preview-wrap canvas` — fills the container
- `.preview-overlay` — absolute hint text ("Drag to orbit · Scroll to zoom")
- `.transform-row` — flex row with gap for button groups
- `.btn-xs` — small variant of existing `.btn`

### 2.3 New "Preview & Transform" card (between upload card and profile card)

Show only when `uploadedStlPath` is set (`v-if="uploadedStlPath"`).

Contains:
- **Canvas container** with `ref="previewCanvas"` inside `ref="previewWrap"` div
- **Non-STL notice** (`v-if="!isStl"`) — "Preview only available for .stl files. Transforms not applied."
- **Transform controls** (`v-if="isStl"`):
  - Rotate X: `[-90°] [+90°]`
  - Rotate Y: `[-90°] [+90°]`
  - Rotate Z: `[-90°] [+90°]`
  - Scale: number input (`v-model.number="modelScale"`, min 0.01, step 0.1) + "× uniform" label + Reset button
  - "↺ Reset All Transforms" button + auto-placement note

Profile card title becomes `{{ 2 + stepOffset }} · Select Profile` (where `stepOffset = uploadedStlPath ? 1 : 0`). Slice card similarly bumped.

### 2.4 Vue state additions

**Reactive:**
```javascript
const modelScale = ref(1.0);
const rotMatrix = ref([1,0,0, 0,1,0, 0,0,1]);  // row-major 3×3, Z-up CuraEngine space
const previewWrap = ref(null);   // template ref
const previewCanvas = ref(null); // template ref
const isStl = computed(() =>
  uploadedFile.value?.name?.toLowerCase().endsWith('.stl') ?? false
);
const stepOffset = computed(() => uploadedStlPath.value ? 1 : 0);
```

**Non-reactive (stored as closure variable):**
```javascript
let three = null; // { renderer, camera, scene, controls, mesh, originalGeometry,
                  //   buildVolume: {w,d,h}, rafId }
```

### 2.5 Three.js functions

**`initPreview(stlUrl)`** — called after upload completes (STL only):
1. `destroyPreview()` to clean up any prior session
2. Create `WebGLRenderer`, `PerspectiveCamera`, `Scene` (bg `#121212`)
3. Add ambient + directional lights
4. Load STL via `THREE.STLLoader().load(stlUrl, geo => {...})`:
   - Store raw geometry as `three.originalGeometry`
   - Create `MeshPhongMaterial({ color: 0x2196f3 })`
   - Create Mesh, add to scene
5. Add wireframe build volume box (`EdgesGeometry(BoxGeometry(w,d,h))` + `LineSegments`) positioned with bottom face at Y=0
6. Use fallback build volume `{w:220, d:220, h:250}` until profile is selected
7. Attach `THREE.OrbitControls(camera, canvas)` with damping
8. Position camera to frame the build volume
9. Start `requestAnimationFrame` render loop
10. Call `applyTransformToMesh()` to place model on first render

**`applyTransformToMesh()`** — called after any rotation or scale change:
1. Clone `three.originalGeometry`
2. Build `THREE.Matrix4` from `rotMatrix.value` + `modelScale.value` (accounting for Y-up/Z-up swap for display — see Coordinate Note below)
3. Apply matrix to cloned geometry
4. Compute `boundingBox`, then translate:
   - Center X/Y over build plate
   - Drop Z (or Y in Three.js space) to bed
5. Assign new geometry to mesh

**`rotate(axis, deg)`** — 90° step rotation:
1. Build a 3×3 rotation matrix `R` for `axis` ∈ `{x,y,z}` in CuraEngine Z-up space
2. Multiply `rotMatrix.value = R × rotMatrix.value` (9-float row-major multiply, pure JS)
3. Call `applyTransformToMesh()`

**`resetTransform()`**: reset `rotMatrix` to identity, `modelScale` to 1.0, call `applyTransformToMesh()`.

**`destroyPreview()`**: cancel RAF, dispose renderer/geometry/material, set `three = null`.

**`loadBuildVolume(profileName)`** — triggered by `watch(selectedProfile, ...)`:
1. `GET /server/cura_slicer/profiles/{name}` — reads `result.build_volume` from the response (see Part 3.3)
2. Update `three.buildVolume`, rebuild wireframe box, reposition camera

**Trigger**: in `handleStlFile()`, after `uploadedStlPath.value = path`:
```javascript
resetTransform();
if (isStl.value) {
  const url = `${MOONRAKER}/server/files/gcodes/${encodeURIComponent(path)}`;
  initPreview(url);
}
```

**Cleanup**: `onUnmounted(() => destroyPreview())`.

### 2.6 Coordinate system note

Three.js is Y-up; CuraEngine is Z-up. `rotMatrix` is stored in **CuraEngine Z-up** space (this is what's sent to the backend). For display in Three.js, `applyTransformToMesh()` applies a static basis swap (`Y_SWAP = [[1,0,0],[0,0,1],[0,1,0]]`) before applying rotMatrix. The backend never sees the swap — it applies `rotMatrix` directly in Z-up space.

Auto-placement translation in Three.js display: after bounding box, shift so `minY = 0` (bed) and `centerX/centerZ` align to build plate center.

### 2.7 Updated `startSlice()`

```javascript
const body = {
  filename: uploadedStlPath.value,
  profile: selectedProfile.value,
  settings,
  print_after: printAfter.value,
};
if (isStl.value) {
  body.transform = {
    rotation: rotMatrix.value,   // 9 floats, row-major 3×3, Z-up
    scale: modelScale.value,     // uniform float
  };
}
```

---

## Part 3 — moonraker-plugin/cura_slicer.py

### 3.1 New top-level function `_transform_stl_binary(src, dst, rot, scale, build_volume)`

Pure Python, no external deps. Uses only `struct`, `math`, `os`.

```
- Read 80-byte header + uint32 tri count + 50-byte triangle records (struct '<3f3f3f3f3fH')
- Detect ASCII STL heuristic: if header starts with b'solid' AND expected binary size doesn't
  match actual file size → raise ValueError("ASCII STL")
- First pass: transform all vertices (scale then rotate via 3×3 matrix multiply) + track bbox
- Compute translation: tx = bv_w/2 - (minX+maxX)/2, ty = bv_d/2 - (minY+maxY)/2, tz = -minZ
  (If no build_volume, just center around 0 and drop to Z=0)
- Rotate normals (rotation only, no scale/translate)
- Write new binary STL to dst_path
```

Performance: ~3-8s on Pi 4 for 100k triangles (acceptable; slicing takes much longer). Run via `run_in_executor` to avoid blocking the async event loop.

Place this function above the `CuraSlicer` class (module-level, ~70 lines).

### 3.2 `_handle_slice()` — accept `transform` param (~line 419)

```python
transform = body.get("transform", None)
```

Pass `transform` through to `_run_slice` as an argument.

### 3.3 `_run_slice()` — apply transform before CuraEngine (~line 476)

Between job status set to `"slicing"` and `cmd = [self.cura_engine, ...]`:

```python
input_path = stl_path
if transform and stl_path.suffix.lower() == '.stl':
    rot = transform.get('rotation', [1,0,0,0,1,0,0,0,1])
    scale = float(transform.get('scale', 1.0))
    bv = self._get_build_volume(def_name)  # reads w/d/h from def.json, returns tuple or None
    transformed = self.temp_dir / f"transformed_{job['id']}.stl"
    try:
        await loop.run_in_executor(
            None, _transform_stl_binary,
            str(stl_path), str(transformed), rot, scale, bv
        )
        input_path = transformed
    except ValueError as e:
        logger.warning(f"STL transform skipped: {e}")
    except Exception as e:
        logger.error(f"STL transform error: {e}")
```

Change existing line to use `input_path`:
```python
cmd += ["-o", str(output_path), "-l", str(input_path)]
```

After subprocess completes, clean up: `if input_path != stl_path: input_path.unlink(missing_ok=True)`

### 3.4 New helper `_get_build_volume(def_name)` on `CuraSlicer`

```python
def _get_build_volume(self, def_name):
    if not def_name:
        return None
    path = self._resolve_definition_path(def_name)
    if not path:
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        s = data.get('settings', {})
        w = float(s.get('machine_width',  {}).get('default_value', 220))
        d = float(s.get('machine_depth',  {}).get('default_value', 220))
        h = float(s.get('machine_height', {}).get('default_value', 250))
        return (w, d, h)
    except Exception:
        return None
```

### 3.5 Profile GET response — add `build_volume`

In `_handle_profile` (GET branch), after loading profile data:

```python
bv = self._get_build_volume(data.get("printer_definition", ""))
data["build_volume"] = {
    "width":  bv[0] if bv else 220.0,
    "depth":  bv[1] if bv else 220.0,
    "height": bv[2] if bv else 250.0,
}
return data
```

The frontend `loadBuildVolume()` reads `result.build_volume` from this response.

---

## Edge Cases

| Case | Behavior |
|------|----------|
| OBJ / 3MF uploaded | `isStl` is false. No transform panel shown, only a notice. No `transform` key sent. Server ignores transform even if somehow present. |
| ASCII STL | `_transform_stl_binary` raises `ValueError`. Python catches it, logs warning, uses original file. Slicing proceeds. |
| Identity transform (no rotation, scale=1) | `transform` is still sent but is a no-op. Server applies it — the copy is cheap. |
| Profile has no printer definition | `_get_build_volume` returns `None`. Build volume defaults to 220×220×250 in preview. Transform still applied, centered around 0. |
| Definition uses `inherits` | `default_value` not in child JSON — `_get_build_volume` returns `None`, fallback defaults used. |
| Very large STL | `run_in_executor` prevents event loop blocking. No hard limit in v1. |

---

## Verification

1. **Install**: run `bash install.sh` on a Pi — confirm `three.min.js`, `three.STLLoader.js`, `three.OrbitControls.js` appear in the UI deploy dir.
2. **Upload STL**: upload a known STL (e.g., Benchy). Confirm preview canvas appears with model and wireframe build volume box.
3. **Rotation**: click each rotation button. Confirm model visually re-orients and auto-drops to bed.
4. **Scale**: set scale to 0.5 and 2.0. Confirm model shrinks/grows relative to bed box.
5. **Reset**: click Reset All. Confirm original orientation restored.
6. **Slice with transform**: rotate X+90, set scale 1.5, click Slice Now. Inspect the resulting G-code in a viewer (e.g., OrcaSlicer) and confirm the model is rotated and scaled correctly.
7. **Non-STL**: upload an OBJ file. Confirm no transform controls appear, only the notice.
8. **ASCII STL**: upload an ASCII STL. Confirm slicing still works (transform skipped, warning in Moonraker log).
9. **Build volume**: select a profile with a known printer definition (e.g., Ender 3). Confirm the wireframe box updates to 220×220×250.
