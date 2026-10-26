# Tenkabana 甜花

A Windows tool for finding and extracting character assets from
**THE iDOLM@STER SHINY COLORS Song for Prism** — browse all 29 idols, preview
meshes and textures, and export a whole costume in one click.

Named for 大崎甜花 (Osaki Tenka), whose given name reads "sweet flower".

---

## What it does

- **Browse by character.** All 29 idols with their in-game portraits and asset counts.
- **Group by costume.** Assets are collected under the game's own costume codes
  (`14mgar`, `15sw02`, `hllw03`), so an outfit is one group instead of 40
  scattered filenames.
- **Preview before extracting.** Textures decode to PNG; meshes render in an
  orbitable 3D viewport with a dropdown for every mesh a prefab resolves to.
- **Export what you actually want.** One asset, one costume, or a whole
  character — with dependencies followed so geometry comes along.
- **Blender-ready output.** Bundles are converted to `meshes/*.obj` and
  `textures/*.png`, with an import guide written into every export.

## Requirements

- Windows 10/11, .NET 8 desktop runtime
- The game installed via DMM Game Player (the PC client), and played at least
  once so its asset cache is populated
- [AssetStudioModCLI](https://github.com/aelurum/AssetStudioMod) extracted to
  `%USERPROFILE%\prism_tools\astudio\` — used for texture and mesh decoding

Paths are discovered automatically: the download cache from your user profile,
the install directory from DMM's own config. Only override via `settings.json`
(see `settings.sample.json`) if detection fails.

## Setup

```
dotnet build Tenkabana.csproj
Tenkabana.exe --build-cabmap      # ~10 min, once
```

`--build-cabmap` maps every CAB file in the cache to the bundle containing it.
Without it, meshes can't be resolved.

The asset index builds on first run; **Refresh index** picks up anything the
game has downloaded since.

## Command line

```
Tenkabana.exe --extract tenka,amana --out D:\out --with-deps --blender
Tenkabana.exe --export-costume 14mgar --out D:\out --with-deps --blender
Tenkabana.exe --export-asset m_hair_15_005_00000.prefab --out D:\out --with-deps
Tenkabana.exe --build-cabmap
```

Idols take names or numeric ids. `--kinds mesh,texture,animation,other` filters
by asset type.

## Releases and updates

Pushing a `v*` tag releases that version:

```
git tag v1.1.0
git push origin v1.1.0
```

`release.yml` builds it, packs it with [Velopack](https://velopack.io) and
publishes a GitHub release with the installer and update packages. The release
notes are generated from what was merged since the previous tag.

Installed copies never update on their own. They check GitHub on startup and
every 6 hours, and only download when you choose to:

1. When a newer release exists, an **Update available · x.y.z** button appears
   in the header. **Check for updates** next to the version does the same on
   demand.
2. The button opens the changelog for every release between your version and
   the new one.
3. From there you pick **Update and restart** (download, with progress and
   Cancel, then install), **Later**, or **Skip this version** (no more
   reminders for that version; **Check for updates** still finds it).

Nothing installs on exit, and an update won't restart the app in the middle of
an export. Builds run from `bin/` or a portable unzip don't update.

---

## How it works

Most of the work here was figuring out how the game names things. The findings
are more interesting than the code.

### The cache is already decrypted

`%USERPROFILE%\AppData\LocalLow\BNE\imasscprism\D\` holds every asset the client
has downloaded — around 24 GB across 186,000 files on a full account. Each is a
standard `UnityFS` bundle behind a 4-byte `BA 01 00 00` prefix. Strip those four
bytes and any Unity tool can read it. No key, no CDN round trip.

Files are named by a Base32 content hash, so the hard part is knowing which is
which.

### Indexing by name, and where that fails

The indexer decompresses the leading blocks of each bundle and harvests strings
that look like asset names. ~42,000 names over the whole cache.

That works for textures and prefabs. It does **not** work for geometry, because
mesh assets are named generically:

```
BodySkin   FrontHair   backHead   ATAMA_shadow   dress_BS_b
```

No character id anywhere. Roughly 86,000 meshes sit in the cache that name
matching can never attribute to anyone.

### Dependency resolution

Unity bundles carry the graph, in two halves:

- **`provides`** — the CAB files a bundle contains, listed in its `blocksInfo`.
  A few hundred bytes per bundle, so a whole-cache `CAB → bundle` map is cheap
  (191,000 CABs in ~10 minutes).
- **`requires`** — the CAB files it references, appearing verbatim as
  `CAB-<32 hex>` in the decompressed data. Needs full decompression, so it's
  resolved on demand.

Seed from a character's name-matched bundles, follow references, repeat. That
turns 2 meshes into 237.

### Costume codes

An outfit is identified by a code, in one of three shapes:

```
14sw02    <idol id><2 letters><2 digits>    idol-scoped
14mgar    <idol id><4 letters>              idol-scoped  (896 of these)
bridal    no idol prefix                    shared, any character can wear it
```

Missing the second shape hides a character's clothes entirely — dress and
accessory sets live almost exclusively there.

Worse, the pieces of one outfit share only a number:

```
dress_0237_14mgar_00_col      code spelled out
acce_40237_14mgar_14_col      code, with a slot prefix on the id
m_hair_14_005_40237.prefab    number only
14_face_0237_col              number only
```

So the app builds `id → code` from the names that declare themselves, then
groups everything else by looking up its number.

### Things worth knowing

- Most costumes have **no hair of their own** — only 4 of Tenka's 38 do. The
  rest reuse one of her 22 base hairstyles, which live under the character, not
  the outfit.
- A costume only exists in the cache once the client has downloaded it, which in
  practice means ones you own or have displayed.
- `_col` base colour, `_Rmask` rim-light mask, `_Smask` shadow, `_SPmap`
  specular (R mask, G roughness, B metallic), `_Rnoise` angel-ring noise. The
  materials say so: see below.

## Limitation: no rig

`.obj` carries geometry only — no armature, no bone weights, no shape keys.
That's the ceiling of AssetStudioMod's mesh export, so it's the ceiling here.

It matters because each idol's body is a **shared base mesh** shaped at runtime
by per-character parameters, and the bust value is a blendshape weight:

| | height | bust |
|---|---|---|
| Tenka (15) | 0.99375 | 0.478 |
| Amana (14) | 0.99375 | 0.435 |

Height is a uniform scale you can apply by hand. Bust needs a rig.

For a rigged model, point [AssetRipper](https://github.com/AssetRipper/AssetRipper)
at the `.unity3d` bundles left in every export folder and export FBX or glTF.
Same source data, keeps bone weights and blendshapes.

## The game's shading, in Blender

Characters are drawn by five URP shaders: `Anim2D3D/CharacterOutline`,
`Hair`, `EyeBall`, `FakeShadow` and `Character Unlit`. Their D3D11 bytecode
ships in the bundles, so their maths can be read (DXBC → SPIR-V with vkd3d →
GLSL with spirv-cross, with constant-buffer slots named from Unity's parameter
blobs). `Blender/tenkabana_toon.py` rebuilds them as node groups.

What they do, which isn't what a toon shader usually does:

- **Shading is mostly painted.** Diffuse is `albedo × lerp(_DarkColor,
  _BrightColor, ramp)`, where the ramp thresholds `ShadeTex.r × ½(N·L + 1)` and
  the result is scaled by `_ShadeEffect`. Skin and face set Dark = Bright,
  so they get no light-dependent shadow. Amana's 14mgar dress gets a faint
  purple one.
- **`_SPmap`** packs specular mask / roughness / metallic for a GGX-style
  highlight (the game takes a square root of it). **`_Rmask`** gates a
  view-dependent rim.
- **The angel ring** isn't in the hair texture. The view ray is intersected
  with a 12 cm sphere at the head centre, and that direction, jittered by
  `_Rnoise` on the second UV set, samples a ring cubemap.
- **The fake hair shadow** is the front hair drawn again 6 mm away from the
  light as a flat multiply, stencilled onto the face. Blender has no stencil,
  so a Geometry Nodes modifier lays the shifted copy onto the face surface
  (0.6 mm above it); the real bangs are always in front of it, so it darkens
  only skin. Without that, the copy darkens the bangs themselves in blotches.
- **Outlines** extrude along directions baked into the mesh tangents, sideways
  in screen space only, pushed back in depth, masked by `_OutlineTex`.
- **It's gamma-space.** The maths runs on raw sRGB values.

A few inputs are per-stage globals set at runtime (rim power, diffuse border,
light colour). They aren't in any bundle, so they live in one **TKB Prism
Globals** group, editable from the sidebar. Where a screenshot pins them down,
they're measured rather than guessed:

- **Skin shadow.** Skin and face materials ship Dark = Bright (no shadow), but
  in-game shadowed skin is exactly texture × (0.886, 0.745, 0.739). The game
  swaps in a warm Dark colour at runtime; *Skin Shadow* reproduces it, and the
  rendered shadow matches the screenshot within one level per channel.
- **Diffuse border.** 0.45 with a hard 0.05 blur. The face (ShadeOn = 0, t fixed
  at 0.5) has to stay lit, and the arm terminator in-game is 2–3 px wide.

### Getting the exact materials

```
pip install UnityPy
python Blender/prism_extract.py D:\out\14mgar [D:\out\14_amana ...]
```

Pass several folders to merge a costume with the idol's own face/character
export. A costume prefab only carries generic stand-ins for the eyes, lashes,
teeth and brows (from a shared `new_anim.fbx`), so the eye shape and the
smile expressions only appear once the idol's face prefab is included.

This reads the export's bundles and writes `blender/` next to them:

- `model.glb`: each part as its own object, with both UV sets, real submesh
  material slots, the baked outline directions and the blendshapes as shape
  keys (bust gets the idol's value; eye close, A/I/U/E/O are there to pose)
- `materials.json`: which part uses which material, with every value the
  game's materials set
- the referenced textures, with the ring cubemap unwrapped to an equirect

It also resolves the base-body placeholders the way the game does at runtime:
face parts get the costume's face material, eyes get the eye material, and
parts the costume replaces are dropped. Every `--blender` export includes the
script.

Then install `tenkabana_toon.py` (*Edit › Preferences › Add-ons › Install from
Disk*) and use **Import** in the **Tenkabana** sidebar tab. Without the
extractor's output, Import falls back to matching OBJ parts to textures by name
with typical material values. That's close, but not exact.

### MMD-ready rig

`tenkabana_mmd.py` is a second add-on (install it the same way). Select the
character's armature and press **Convert to MMD** in the **Tenkabana MMD**
panel. It works in place on the game's humanoid skeleton:

- points every limb bone at its child, so IK and twist have a real axis;
- re-rests the arms at the MMD A-pose (35° by default), baking the change into
  the meshes, shape keys, custom normals and outline directions. VMD arm keys
  assume that pose; on the game's T-pose they bend elbows and twist forearms;
- adds `全ての親` / `センター` / `グルーブ`, leg and toe IK with knee limits;
- drives the game's `*_rot` twist helpers so wrist and shoulder twist spreads
  along the limb instead of pinching one ring of vertices;
- keeps at most 4 normalised weights per vertex (PMX BDEF4);
- renames bones to the MMD standard names (vertex groups follow) and fills
  mmd_tools' Japanese/English names when that add-on is installed.

Then load a dance with mmd_tools' *Import Motion*. Use a motion made for a
normal-proportioned model: chibi variants (ちび, ぷち) move the IK feet and
centre for short legs. Actions made before converting no longer match.

### Template scene

```
blender -b -P Blender/build_template.py -- --export D:\out\14mgar --render amana.png
blender -b -P Blender/build_template.py -- --out amana_template.blend
```

With `--export`, it builds a camera, key light, stage and the imported
character. Without it, it builds a stand-in mannequin in Amana's palette from
generated textures. `--idol` picks someone else; `--engine cycles` renders
without a GPU.

**Not reproduced:** real-time self-shadows (the game's `PrismSelfShadow` map
feeds the same Dark→Bright ramp), the per-character light buffer, gamma-space
alpha blending (the translucent overskirt reads slightly more opaque) and the
rig.

## Portraits

Character tiles use the game's own `Photo_Chr_00NN` textures. **They are not in
this repository** — they're Bandai Namco's art, and the point of this tool is to
make them, not to ship them.

To generate them: export `Photo_Chr_*` with AssetStudioModCLI and drop the
results into `Assets/portraits/NN.png` (zero-padded idol id). Tiles fall back to
a gradient and initial when a portrait is missing.

## Credits

- [AssetStudioMod](https://github.com/aelurum/AssetStudioMod) by aelurum —
  texture and mesh decoding
- [PrismCatalogDownloader](https://github.com/LukeFZ/PrismCatalogDownloader) by
  LukeFZ — the catalog format work this built on
- [imas-sc-prism-localify](https://github.com/Kimjio/imas-sc-prism-localify) by
  Kimjio — for documenting the PC client

## Legal

A tool for reading files already on your own disk. It ships no game assets and
circumvents no protection — the cache it reads is written unencrypted by the
game itself.

Everything it extracts is copyright Bandai Namco Entertainment. Personal use —
renders, MMD, VRChat avatars for yourself, study. Don't sell it, don't
redistribute the assets, don't pass them off as your own.

Not affiliated with or endorsed by Bandai Namco Entertainment.
