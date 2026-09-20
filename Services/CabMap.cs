using System.IO;
using System.Text.Json;

namespace Tenkabana.Services;

/// <summary>
/// CAB file -> bundle that contains it.
///
/// Built from blocksInfo only (a few hundred bytes per bundle), so a full-cache
/// map is cheap compared to indexing asset names. This is what turns a prefab's
/// external references into actual files on disk.
/// </summary>
public sealed class CabMap
{
    private readonly Dictionary<string, string> _map;
    public int Count => _map.Count;

    private CabMap(Dictionary<string, string> map) => _map = map;

    public string? Owner(string cab) => _map.TryGetValue(cab, out var b) ? b : null;

    public static CabMap Load(string path)
    {
        using var fs = File.OpenRead(path);
        var d = JsonSerializer.Deserialize<Dictionary<string, string>>(fs)
                ?? new Dictionary<string, string>();
        return new CabMap(d);
    }

    public void Save(string path)
    {
        using var fs = File.Create(path);
        JsonSerializer.Serialize(fs, _map);
    }

    public static CabMap Build(
        IEnumerable<string> roots,
        IProgress<(int done, int total, string label)>? progress = null,
        CancellationToken ct = default)
    {
        var files = new List<string>();
        foreach (var r in roots)
        {
            if (!Directory.Exists(r)) continue;
            files.AddRange(Directory.EnumerateFiles(r, "*", SearchOption.AllDirectories));
        }

        var map = new Dictionary<string, string>(StringComparer.Ordinal);
        var head = new byte[8192];
        int done = 0;

        foreach (var f in files)
        {
            ct.ThrowIfCancellationRequested();
            done++;
            if (done % 5000 == 0) progress?.Report((done, files.Count, "mapping"));

            try
            {
                HashSet<string> provides;
                using (var fs = File.OpenRead(f))
                {
                    int n = fs.Read(head, 0, head.Length);
                    var span = UnityFs.StripPrefix(head.AsSpan(0, n));
                    if (!UnityFs.IsBundle(span)) continue;
                    try
                    {
                        provides = UnityFs.Provides(span);
                    }
                    catch
                    {
                        // blocksInfo sits at the end of this bundle: needs the whole file
                        fs.Position = 0;
                        var all = new byte[fs.Length];
                        fs.ReadExactly(all);
                        provides = UnityFs.Provides(UnityFs.StripPrefix(all));
                    }
                }

                var name = Path.GetFileName(f);
                foreach (var c in provides) map[c] = name;
            }
            catch { /* unreadable or not a bundle */ }
        }

        progress?.Report((files.Count, files.Count, $"{map.Count} CABs"));
        return new CabMap(map);
    }

    /// <summary>
    /// Expand <paramref name="seeds"/> with everything they transitively reference.
    /// Returns the full closure including the seeds.
    /// </summary>
    public HashSet<string> Closure(
        IEnumerable<string> seeds,
        Func<string, string?> locate,
        int maxDepth = 6,
        IProgress<(int done, int total, string label)>? progress = null,
        CancellationToken ct = default)
    {
        var selected = new HashSet<string>(seeds, StringComparer.OrdinalIgnoreCase);
        var frontier = new HashSet<string>(selected, StringComparer.OrdinalIgnoreCase);

        for (int depth = 0; depth < maxDepth && frontier.Count > 0; depth++)
        {
            var needed = new HashSet<string>(StringComparer.Ordinal);
            int i = 0;
            foreach (var b in frontier)
            {
                ct.ThrowIfCancellationRequested();
                if (++i % 25 == 0)
                    progress?.Report((i, frontier.Count, $"depth {depth + 1}"));
                var path = locate(b);
                if (path is null) continue;
                try
                {
                    var data = UnityFs.StripPrefix(File.ReadAllBytes(path));
                    foreach (var c in UnityFs.Requires(data)) needed.Add(c);
                }
                catch { }
            }

            var next = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var c in needed)
            {
                var owner = Owner(c);
                if (owner is not null && selected.Add(owner)) next.Add(owner);
            }
            progress?.Report((0, 0, $"depth {depth + 1}: +{next.Count}"));
            frontier = next;
        }

        return selected;
    }
}
