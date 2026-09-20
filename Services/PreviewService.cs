using System.Diagnostics;
using System.IO;

namespace Tenkabana.Services;

public sealed record PreviewResult(string? ImagePath, IReadOnlyList<string> ObjPaths, string Info);

/// <summary>
/// Decodes a single asset for on-screen preview by shelling out to
/// AssetStudioModCLI. Results are cached per (bundle, asset) so re-selecting is
/// instant; a cold decode is roughly half a second.
/// </summary>
public sealed class PreviewService(CacheIndex index)
{
    private static readonly string CacheDir =
        Path.Combine(Path.GetTempPath(), "TenkabanaPreview");

    private readonly Dictionary<string, PreviewResult> _memo = new(StringComparer.Ordinal);

    public CabMap? Cabs { get; set; }

    public static string? FindCli()
    {
        var root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            "prism_tools", "astudio");
        if (!Directory.Exists(root)) return null;
        foreach (var f in Directory.EnumerateFiles(root, "AssetStudioModCLI.exe",
                     SearchOption.AllDirectories))
            return f;
        return null;
    }

    private static bool Run(string cli, string args, int timeoutMs = 60_000)
    {
        var psi = new ProcessStartInfo(cli, args)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        using var p = Process.Start(psi);
        if (p is null) return false;
        // drain so the child never blocks on a full pipe
        _ = p.StandardOutput.ReadToEndAsync();
        _ = p.StandardError.ReadToEndAsync();
        return p.WaitForExit(timeoutMs);
    }

    /// <summary>Copy a cache bundle, header stripped, into <paramref name="dir"/>.</summary>
    private bool Stage(string bundleHash, string dir)
    {
        if (!index.Locations().TryGetValue(bundleHash, out var src)) return false;
        try
        {
            var data = UnityFs.StripPrefix(File.ReadAllBytes(src)).ToArray();
            File.WriteAllBytes(Path.Combine(dir, bundleHash + ".unity3d"), data);
            return true;
        }
        catch { return false; }
    }

    public async Task<PreviewResult> GetAsync(
        AssetEntry asset, CancellationToken ct = default)
    {
        var key = asset.Name + "|" + string.Join(',', asset.Bundles);
        if (_memo.TryGetValue(key, out var hit)) return hit;

        var cli = FindCli();
        if (cli is null)
            return new PreviewResult(null, Array.Empty<string>(),
                "AssetStudioModCLI not found under %USERPROFILE%\\prism_tools\\astudio");

        var result = await Task.Run(() =>
        {
            var safe = string.Concat(asset.Name.Select(c =>
                Path.GetInvalidFileNameChars().Contains(c) ? '_' : c));
            var work = Path.Combine(CacheDir, $"{safe}_{asset.Bundles.Count}");
            var stage = Path.Combine(work, "in");
            var outDir = Path.Combine(work, "out");

            if (Directory.Exists(outDir) && Directory.EnumerateFiles(outDir).Any())
                return Collect(outDir, asset);

            Directory.CreateDirectory(stage);
            Directory.CreateDirectory(outDir);
            foreach (var f in Directory.EnumerateFiles(stage)) File.Delete(f);

            foreach (var b in asset.Bundles) Stage(b, stage);

            if (asset.Kind == AssetKind.Mesh && Cabs is not null)
            {
                // A prefab holds no geometry - the meshes live in the bundles it
                // references, so pull in its direct dependencies before exporting.
                var loc = index.Locations();
                var closure = Cabs.Closure(
                    asset.Bundles,
                    b => loc.TryGetValue(b, out var p) ? p : null,
                    maxDepth: 1, ct: ct);
                foreach (var b in closure) Stage(b, stage);
            }

            var types = asset.Kind == AssetKind.Mesh ? "mesh" : "tex2d,sprite";
            var args = $"\"{stage}\" -m export -t {types} -g none -o \"{outDir}\" " +
                       $"--image-format png --log-level error";
            if (asset.Kind != AssetKind.Mesh)
                args += $" --filter-by-name \"{asset.Name.Split('.')[0]}\"";

            Run(cli, args);
            return Collect(outDir, asset);
        }, ct);

        _memo[key] = result;
        return result;
    }

    private static PreviewResult Collect(string outDir, AssetEntry asset)
    {
        var pngs = Directory.EnumerateFiles(outDir, "*.png", SearchOption.AllDirectories).ToList();
        var objs = Directory.EnumerateFiles(outDir, "*.obj", SearchOption.AllDirectories)
                            .OrderByDescending(f => new FileInfo(f).Length).ToList();

        string info;
        if (objs.Count > 0)
            info = $"{objs.Count} mesh(es) · showing {Path.GetFileNameWithoutExtension(objs[0])}";
        else if (pngs.Count > 0)
        {
            var fi = new FileInfo(pngs[0]);
            info = $"{Path.GetFileName(pngs[0])} · {fi.Length / 1024.0:N0} KB";
        }
        else
            info = $"No previewable data in this bundle ({asset.Kind}).";

        return new PreviewResult(pngs.FirstOrDefault(), objs, info);
    }

    public static void ClearCache()
    {
        try { if (Directory.Exists(CacheDir)) Directory.Delete(CacheDir, true); }
        catch { }
    }
}
