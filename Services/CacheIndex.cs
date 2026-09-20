using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Tenkabana.Services;

public enum AssetKind { Mesh, Texture, Animation, Other }

public sealed record AssetEntry(string Name, IReadOnlyList<string> Bundles, AssetKind Kind);

/// <summary>
/// Loads the name -> bundle-hash index produced by the indexer, and answers
/// "which assets belong to idol N".
/// </summary>
public sealed class CacheIndex
{
    /// <summary>Discovered at runtime - see GamePaths.</summary>
    public static string[] DefaultRoots => GamePaths.Roots();

    public Dictionary<string, string[]> Names { get; } = new(StringComparer.Ordinal);
    private Dictionary<string, string>? _locations;

    public int NameCount => Names.Count;

    public static CacheIndex Load(string jsonPath)
    {
        var ci = new CacheIndex();
        using var fs = File.OpenRead(jsonPath);
        using var doc = JsonDocument.Parse(fs);
        foreach (var p in doc.RootElement.EnumerateObject())
        {
            var arr = new string[p.Value.GetArrayLength()];
            int i = 0;
            foreach (var v in p.Value.EnumerateArray()) arr[i++] = v.GetString() ?? "";
            ci.Names[p.Name] = arr;
        }
        return ci;
    }

    /// <summary>basename -> full path for every file in the cache roots. Built once, lazily.</summary>
    public Dictionary<string, string> Locations(IEnumerable<string>? roots = null)
    {
        if (_locations is not null) return _locations;
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var root in roots ?? DefaultRoots)
        {
            if (!Directory.Exists(root)) continue;
            foreach (var f in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
                map[Path.GetFileName(f)] = f;
        }
        return _locations = map;
    }

    public void InvalidateLocations()
    {
        _locations = null;
        _forCache.Clear();
        _sharedFlat = null;
        _sharedCostumes = null;
        _idToCode = null;
    }

    private static readonly Dictionary<int, Regex> PatternCache = new();

    public static Regex PatternFor(int id)
    {
        if (PatternCache.TryGetValue(id, out var r)) return r;
        var d2 = id.ToString("D2");
        // Costume codes come in two shapes and BOTH are idol-scoped:
        //   14sw02  = <id><2 letters><2 digits>
        //   14mgar  = <id><4 letters>     <- dress/accessory sets live here
        // Missing the second shape hides a character's clothes entirely.
        var pattern =
            $@"^{d2}_" + "|" +
            $@"^m_(head|hair|dress|acce|face)_{d2}([_.]|$)" + "|" +
            $@"^sd_head_{d2}([_.]|$)" + "|" +
            $@"(?<![0-9a-z]){d2}[a-z]{{2}}[0-9]{{2}}(?![0-9a-z])" + "|" +
            $@"(?<![0-9a-z]){d2}[a-z]{{4}}(?![0-9a-z])";
        r = new Regex(pattern, RegexOptions.IgnoreCase | RegexOptions.Compiled);
        PatternCache[id] = r;
        return r;
    }

    public static AssetKind Classify(string name)
    {
        if (name.StartsWith("m_head_", StringComparison.Ordinal) ||
            name.StartsWith("m_hair_", StringComparison.Ordinal) ||
            name.StartsWith("m_dress_", StringComparison.Ordinal) ||
            name.StartsWith("m_acce_", StringComparison.Ordinal) ||
            name.StartsWith("sd_head_", StringComparison.Ordinal))
            return name.EndsWith(".asset", StringComparison.OrdinalIgnoreCase)
                ? AssetKind.Other : AssetKind.Mesh;

        if (name.StartsWith("mot_", StringComparison.Ordinal) ||
            name.StartsWith("admot_", StringComparison.Ordinal) ||
            name.StartsWith("cut_", StringComparison.Ordinal))
            return AssetKind.Animation;

        if (name.EndsWith(".png", StringComparison.OrdinalIgnoreCase) ||
            name.Contains("_col", StringComparison.Ordinal) ||
            name.Contains("mask", StringComparison.OrdinalIgnoreCase) ||
            name.Contains("_noise", StringComparison.OrdinalIgnoreCase))
            return AssetKind.Texture;

        return AssetKind.Other;
    }

    private static readonly Regex CostumeToken =
        new(@"^(?:m_)?(?:dress|acce)_(\d+)_([a-z0-9]{4,8})_",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);
    // 01-28 and 90 are real idol ids; 00 and other prefixes are not.
    private static readonly Regex IdolScoped =
        new(@"^(0[1-9]|1[0-9]|2[0-8]|90)([a-z]{2}[0-9]{2}|[a-z]{4})$",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

    /// <summary>
    /// Costumes whose code is NOT tied to an idol id - outfits any character can
    /// wear. Idol-pattern matching can never find these, which is why an idol
    /// wearing a shared costume appears to have no costume at all.
    /// </summary>
    private Dictionary<string, List<AssetEntry>>? _sharedCostumes;

    private Dictionary<string, string>? _idToCode;

    /// <summary>
    /// Costume numeric id (last 4 digits) -> costume code.
    ///
    /// One outfit's pieces are scattered across name shapes that share only a
    /// number: dress_0237_14mgar_00_col, acce_40237_14mgar_14_col and
    /// m_hair_14_005_40237.prefab are all the same costume, but only the first
    /// two carry the code. This map lets the odd ones out be grouped with them.
    /// </summary>
    public Dictionary<string, string> CostumeIdMap()
    {
        if (_idToCode is not null) return _idToCode;
        var map = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var name in Names.Keys)
        {
            var m = CostumeToken.Match(name);
            if (!m.Success) continue;
            var id = m.Groups[1].Value;
            if (id.Length > 4) id = id[^4..];           // 40237 -> 0237 (slot prefix)
            if (id.Length < 4 || id == "0000") continue; // 0000 is a catch-all, useless
            map.TryAdd(id, m.Groups[2].Value.ToLowerInvariant());
        }
        return _idToCode = map;
    }

    public Dictionary<string, List<AssetEntry>> SharedCostumes()
    {
        if (_sharedCostumes is not null) return _sharedCostumes;
        var result = new Dictionary<string, List<AssetEntry>>(StringComparer.OrdinalIgnoreCase);
        foreach (var (name, bundles) in Names)
        {
            var m = CostumeToken.Match(name);
            if (!m.Success) continue;
            var code = m.Groups[2].Value.ToLowerInvariant();
            if (IdolScoped.IsMatch(code)) continue;
            if (!result.TryGetValue(code, out var list))
                result[code] = list = new List<AssetEntry>();
            list.Add(new AssetEntry(name, bundles, Classify(name)));
        }
        return _sharedCostumes = result;
    }

    private readonly Dictionary<int, List<AssetEntry>> _forCache = new();

    /// <summary>
    /// Assets belonging to an idol. Memoised: this walks all ~42k names with a
    /// regex, and the UI asks for it on every selection and filter change.
    /// </summary>
    private static readonly Regex IdTokenRx =
        new(@"(?<![0-9])([0-9]{4,6})(?![0-9])", RegexOptions.Compiled);

    /// <summary>
    /// Every asset belonging to one costume code. Three ways a name can claim
    /// membership: the code is spelled out, an idol-scoped code is embedded, or
    /// the name carries only the costume's numeric id.
    /// </summary>
    public List<AssetEntry> ForCostume(string code)
    {
        code = code.ToLowerInvariant();
        var ids = CostumeIdMap()
            .Where(kv => kv.Value == code)
            .Select(kv => kv.Key)
            .ToHashSet(StringComparer.Ordinal);

        var list = new List<AssetEntry>();
        foreach (var (name, bundles) in Names)
        {
            bool hit = false;

            var named = CostumeToken.Match(name);
            if (named.Success && named.Groups[2].Value.ToLowerInvariant() == code)
                hit = true;

            if (!hit && ids.Count > 0)
            {
                foreach (Match t in IdTokenRx.Matches(name))
                {
                    var id = t.Groups[1].Value;
                    if (id.Length > 4) id = id[^4..];
                    if (id == "0000") continue;
                    if (ids.Contains(id)) { hit = true; break; }
                }
            }

            if (hit) list.Add(new AssetEntry(name, bundles, Classify(name)));
        }
        return list;
    }

    public List<AssetEntry> For(int idolId)
    {
        if (_forCache.TryGetValue(idolId, out var hit)) return hit;

        var rx = PatternFor(idolId);
        var list = new List<AssetEntry>();
        foreach (var (name, bundles) in Names)
            if (rx.IsMatch(name))
                list.Add(new AssetEntry(name, bundles, Classify(name)));

        _forCache[idolId] = list;
        return list;
    }

    private List<AssetEntry>? _sharedFlat;

    /// <summary>Flattened shared-costume assets, built once.</summary>
    public List<AssetEntry> SharedFlat()
    {
        if (_sharedFlat is not null) return _sharedFlat;
        _sharedFlat = SharedCostumes().SelectMany(kv => kv.Value).ToList();
        return _sharedFlat;
    }

    /// <summary>Per-idol counts, used to label the character tiles.</summary>
    public Dictionary<int, int> CountsByIdol(IEnumerable<int> ids)
    {
        var result = new Dictionary<int, int>();
        foreach (var id in ids) result[id] = 0;
        var pats = result.Keys.ToDictionary(id => id, PatternFor);
        foreach (var name in Names.Keys)
            foreach (var (id, rx) in pats)
                if (rx.IsMatch(name)) result[id]++;
        return result;
    }
}
