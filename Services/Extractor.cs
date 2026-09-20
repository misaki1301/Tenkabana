using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using Tenkabana.Models;

namespace Tenkabana.Services;

public sealed record ExtractRequest(
    IReadOnlyList<Idol> Idols,
    IReadOnlySet<AssetKind> Kinds,
    string OutputDir,
    bool IncludeDependencies = false,
    int MaxDepth = 6);

public sealed record ExtractResult(int Bundles, long Bytes, string OutputDir, int NamesMatched, int DependencyBundles = 0);

public sealed class Extractor(CacheIndex index)
{
    public CabMap? Cabs { get; set; }

    private static readonly Regex Unsafe = new(@"[^0-9A-Za-z_.\-]", RegexOptions.Compiled);

    public async Task<ExtractResult> RunAsync(
        ExtractRequest req,
        IProgress<(int done, int total, string label)> progress,
        CancellationToken ct = default)
    {
        return await Task.Run(() =>
        {
            // Plan per idol first so each gets its own folder. A bundle shared by two
            // selected idols is written into both - each folder stays self-contained.
            var plan = new List<(Idol Idol, Dictionary<string, SortedSet<string>> Bundles)>();
            int namesMatched = 0;

            foreach (var idol in req.Idols)
            {
                ct.ThrowIfCancellationRequested();
                var byBundle = new Dictionary<string, SortedSet<string>>(StringComparer.Ordinal);
                foreach (var e in index.For(idol.Id))
                {
                    if (!req.Kinds.Contains(e.Kind)) continue;
                    namesMatched++;
                    foreach (var b in e.Bundles)
                    {
                        if (!byBundle.TryGetValue(b, out var set))
                            byBundle[b] = set = new SortedSet<string>(StringComparer.Ordinal);
                        set.Add(e.Name);
                    }
                }
                if (byBundle.Count > 0) plan.Add((idol, byBundle));
            }

            // Mesh assets are named generically (BodySkin, FrontHair, ATAMA_shadow),
            // so geometry is only reachable by following each prefab's CAB references.
            int depCount = 0;
            if (req.IncludeDependencies && Cabs is not null)
            {
                var loc = index.Locations();
                for (int i = 0; i < plan.Count; i++)
                {
                    ct.ThrowIfCancellationRequested();
                    var (idol, byBundle) = plan[i];
                    var seeds = byBundle.Keys.ToList();
                    var closure = Cabs.Closure(
                        seeds,
                        b => loc.TryGetValue(b, out var p) ? p : null,
                        req.MaxDepth, progress, ct);

                    foreach (var b in closure)
                    {
                        if (byBundle.ContainsKey(b)) continue;
                        byBundle[b] = new SortedSet<string>(StringComparer.Ordinal) { "dep" };
                        depCount++;
                    }
                }
            }

            Directory.CreateDirectory(req.OutputDir);
            var locations = index.Locations();
            int total = plan.Sum(p => p.Bundles.Count);

            int done = 0, written = 0;
            long bytes = 0;
            var rootManifest = new StringBuilder();
            rootManifest.AppendLine("idol	asset	bundle_hash	bytes	all_names_in_bundle");

            foreach (var (idol, byBundle) in plan)
            {
                ct.ThrowIfCancellationRequested();

                // NN_name keeps folders sorted by idol id
                var folder = Path.Combine(req.OutputDir, $"{idol.Id:D2}_{idol.Name}");
                Directory.CreateDirectory(folder);

                var manifest = new StringBuilder();
                manifest.AppendLine("asset	bundle_hash	bytes	all_names_in_bundle");

                foreach (var (hash, names) in byBundle.OrderBy(k => k.Key, StringComparer.Ordinal))
                {
                    ct.ThrowIfCancellationRequested();
                    done++;

                    if (!locations.TryGetValue(hash, out var src))
                    {
                        progress.Report((done, total, $"missing {hash}"));
                        continue;
                    }

                    byte[] data;
                    try { data = File.ReadAllBytes(src); }
                    catch (IOException) { continue; }

                    var span = data.AsSpan();
                    if (UnityFs.HasPrefix(span)) span = span[4..];

                    var label = names.OrderBy(s => s.Length).ThenBy(s => s, StringComparer.Ordinal).First();
                    var safe = Unsafe.Replace(label, "_");
                    File.WriteAllBytes(Path.Combine(folder, $"{safe}__{hash}.unity3d"), span.ToArray());

                    written++;
                    bytes += span.Length;
                    var joined = string.Join(';', names);
                    manifest.Append(label).Append('	').Append(hash).Append('	')
                            .Append(span.Length).Append('	').AppendLine(joined);
                    rootManifest.Append(idol.Name).Append('	').Append(label).Append('	')
                                .Append(hash).Append('	').Append(span.Length).Append('	')
                                .AppendLine(joined);

                    if (done % 10 == 0 || done == total)
                        progress.Report((done, total, $"{idol.Name}/{label}"));
                }

                File.WriteAllText(Path.Combine(folder, "manifest.tsv"),
                    manifest.ToString(), new UTF8Encoding(false));
            }

            File.WriteAllText(Path.Combine(req.OutputDir, "manifest.tsv"),
                rootManifest.ToString(), new UTF8Encoding(false));

            return new ExtractResult(written, bytes, req.OutputDir, namesMatched, depCount);
        }, ct);
    }

    /// <summary>
    /// Export one named set of assets (a costume group) into
    /// <paramref name="outputDir"/>/<paramref name="setName"/>.
    /// Same pipeline as RunAsync but driven by an explicit asset list rather
    /// than an idol pattern.
    /// </summary>
    public async Task<ExtractResult> ExportSetAsync(
        string setName,
        IReadOnlyList<AssetEntry> assets,
        string outputDir,
        bool includeDependencies,
        IProgress<(int done, int total, string label)> progress,
        CancellationToken ct = default)
    {
        return await Task.Run(() =>
        {
            var byBundle = new Dictionary<string, SortedSet<string>>(StringComparer.Ordinal);
            foreach (var a in assets)
            {
                ct.ThrowIfCancellationRequested();
                foreach (var b in a.Bundles)
                {
                    if (!byBundle.TryGetValue(b, out var set))
                        byBundle[b] = set = new SortedSet<string>(StringComparer.Ordinal);
                    set.Add(a.Name);
                }
            }

            var loc = index.Locations();
            int depCount = 0;
            if (includeDependencies && Cabs is not null)
            {
                var closure = Cabs.Closure(
                    byBundle.Keys.ToList(),
                    b => loc.TryGetValue(b, out var p) ? p : null,
                    maxDepth: 2, progress: progress, ct: ct);
                foreach (var b in closure)
                {
                    if (byBundle.ContainsKey(b)) continue;
                    byBundle[b] = new SortedSet<string>(StringComparer.Ordinal) { "dep" };
                    depCount++;
                }
            }

            var safeSet = Unsafe.Replace(setName, "_");
            var folder = Path.Combine(outputDir, safeSet);
            Directory.CreateDirectory(folder);

            var manifest = new StringBuilder();
            manifest.AppendLine("asset\tbundle_hash\tbytes\tall_names_in_bundle");

            int done = 0, written = 0;
            long bytes = 0;
            foreach (var (hash, names) in byBundle.OrderBy(k => k.Key, StringComparer.Ordinal))
            {
                ct.ThrowIfCancellationRequested();
                done++;
                if (!loc.TryGetValue(hash, out var src)) continue;

                byte[] data;
                try { data = File.ReadAllBytes(src); }
                catch (IOException) { continue; }

                var span = data.AsSpan();
                if (UnityFs.HasPrefix(span)) span = span[4..];

                var label = names.OrderBy(x => x.Length).ThenBy(x => x, StringComparer.Ordinal).First();
                File.WriteAllBytes(
                    Path.Combine(folder, $"{Unsafe.Replace(label, "_")}__{hash}.unity3d"),
                    span.ToArray());

                written++;
                bytes += span.Length;
                manifest.Append(label).Append('\t').Append(hash).Append('\t')
                        .Append(span.Length).Append('\t')
                        .AppendLine(string.Join(';', names));

                if (done % 10 == 0 || done == byBundle.Count)
                    progress.Report((done, byBundle.Count, label));
            }

            File.WriteAllText(Path.Combine(folder, "manifest.tsv"),
                manifest.ToString(), new UTF8Encoding(false));

            return new ExtractResult(written, bytes, folder, assets.Count, depCount);
        }, ct);
    }

    /// <summary>Re-index cache files newer than <paramref name="since"/>. Returns names added.</summary>
    public async Task<int> RefreshAsync(
        DateTime since,
        string indexPath,
        IProgress<(int done, int total, string label)> progress,
        CancellationToken ct = default)
    {
        return await Task.Run(() =>
        {
            var files = new List<string>();
            foreach (var root in CacheIndex.DefaultRoots)
            {
                if (!Directory.Exists(root)) continue;
                foreach (var f in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
                {
                    ct.ThrowIfCancellationRequested();
                    try { if (File.GetLastWriteTime(f) > since) files.Add(f); }
                    catch (IOException) { }
                }
            }

            int added = 0, done = 0;
            foreach (var f in files)
            {
                ct.ThrowIfCancellationRequested();
                done++;
                if (done % 50 == 0) progress.Report((done, files.Count, Path.GetFileName(f)));

                byte[] data;
                try { data = File.ReadAllBytes(f); }
                catch (IOException) { continue; }

                var span = data.AsSpan();
                if (UnityFs.HasPrefix(span)) span = span[4..];
                if (!UnityFs.IsBundle(span)) continue;

                byte[] blob;
                try { blob = UnityFs.FirstBlocks(span); }
                catch { continue; }

                var name = Path.GetFileName(f);
                foreach (var n in UnityFs.NamesIn(blob))
                {
                    if (index.Names.TryGetValue(n, out var cur))
                    {
                        if (!cur.Contains(name))
                            index.Names[n] = cur.Append(name).OrderBy(s => s, StringComparer.Ordinal).ToArray();
                    }
                    else { index.Names[n] = new[] { name }; added++; }
                }
            }

            index.InvalidateLocations();
            SaveIndex(indexPath);
            progress.Report((files.Count, files.Count, $"{added} new"));
            return added;
        }, ct);
    }

    private void SaveIndex(string path)
    {
        using var fs = File.Create(path);
        using var w = new System.Text.Json.Utf8JsonWriter(fs,
            new System.Text.Json.JsonWriterOptions { Indented = false });
        w.WriteStartObject();
        foreach (var (k, v) in index.Names.OrderBy(k => k.Key, StringComparer.Ordinal))
        {
            w.WriteStartArray(k);
            foreach (var s in v) w.WriteStringValue(s);
            w.WriteEndArray();
        }
        w.WriteEndObject();
    }
}
