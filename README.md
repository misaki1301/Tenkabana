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
- `_col` base colour, `_rmask` packed mask map, `_smask` shadow, `_spmap`
  specular, `_rnoise` hair anisotropy.

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
