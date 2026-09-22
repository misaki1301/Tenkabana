using System.IO;
using System.Text;
using System.Windows;
using Velopack;
using Tenkabana.Models;
using Tenkabana.Services;

namespace Tenkabana;

public partial class App : Application
{
    private static string ToolsDir =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "prism_tools");
    private static string IndexPath => Path.Combine(ToolsDir, "cache_index.json");
    private static string CabMapPath => Path.Combine(ToolsDir, "cab_map.json");

    private static async Task BuildCabMapAsync()
    {
        var log = new StringBuilder();
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var progress = new Progress<(int done, int total, string label)>();
            var map = await Task.Run(() => CabMap.Build(CacheIndex.DefaultRoots, progress));
            map.Save(CabMapPath);
            log.AppendLine($"cab_map: {map.Count} CABs in {sw.Elapsed.TotalSeconds:F0}s -> {CabMapPath}");
        }
        catch (Exception ex) { log.AppendLine($"ERROR {ex.GetType().Name}: {ex.Message}"); }
        try { File.WriteAllText(Path.Combine(ToolsDir, "cabmap-result.txt"), log.ToString(), new UTF8Encoding(false)); }
        catch { }
    }

    /// <summary>
    ///   Tenkabana.exe --export-costume 14mgar --out D:\out [--with-deps]
    /// </summary>
    private static async Task RunCostumeCliAsync(string[] args)
    {
        string Arg(string name, string fallback)
        {
            int i = Array.FindIndex(args, a => a.Equals(name, StringComparison.OrdinalIgnoreCase));
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback;
        }

        var code = Arg("--export-costume", "");
        var outDir = Arg("--out",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "prism_extract"));
        bool withDeps = args.Contains("--with-deps", StringComparer.OrdinalIgnoreCase);

        var log = new StringBuilder();
        try
        {
            var index = CacheIndex.Load(IndexPath);
            var ex = new Extractor(index);
            if (withDeps && File.Exists(CabMapPath)) ex.Cabs = CabMap.Load(CabMapPath);

            var assets = index.ForCostume(code);
            log.AppendLine($"costume {code}: {assets.Count} assets");
            foreach (var a in assets.OrderBy(a => a.Name, StringComparer.Ordinal).Take(40))
                log.AppendLine($"   {a.Kind,-8} {a.Name}");
            if (assets.Count > 40) log.AppendLine($"   ... and {assets.Count - 40} more");

            if (assets.Count > 0)
            {
                var progress = new Progress<(int, int, string)>();
                var r = await ex.ExportSetAsync(code, assets, outDir, withDeps, progress);
                log.AppendLine($"RESULT bundles={r.Bundles} deps={r.DependencyBundles} bytes={r.Bytes} out={r.OutputDir}");

                if (args.Contains("--blender", StringComparer.OrdinalIgnoreCase))
                {
                    var b = await BlenderExport.ConvertAsync(r.OutputDir, progress);
                    log.AppendLine(b.Error is null
                        ? $"BLENDER meshes={b.Meshes} textures={b.Textures} out={b.OutputDir}"
                        : $"BLENDER ERROR {b.Error}");
                }
            }
        }
        catch (Exception exx) { log.AppendLine($"ERROR {exx.GetType().Name}: {exx.Message}"); }

        try
        {
            Directory.CreateDirectory(outDir);
            File.WriteAllText(Path.Combine(outDir, "costume-result.txt"), log.ToString(), new UTF8Encoding(false));
        }
        catch { }
    }

    /// <summary>Tenkabana.exe --export-asset m_hair_15_005_00000.prefab --out D:\out --with-deps --blender</summary>
    private static async Task RunAssetCliAsync(string[] args)
    {
        string Arg(string name, string fallback)
        {
            int i = Array.FindIndex(args, a => a.Equals(name, StringComparison.OrdinalIgnoreCase));
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback;
        }

        var assetName = Arg("--export-asset", "");
        var outDir = Arg("--out",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "prism_extract"));
        bool withDeps = args.Contains("--with-deps", StringComparer.OrdinalIgnoreCase);

        var log = new StringBuilder();
        try
        {
            var index = CacheIndex.Load(IndexPath);
            var ex = new Extractor(index);
            if (withDeps && File.Exists(CabMapPath)) ex.Cabs = CabMap.Load(CabMapPath);

            if (!index.Names.TryGetValue(assetName, out var bundles))
            {
                log.AppendLine($"asset not found: {assetName}");
            }
            else
            {
                var asset = new AssetEntry(assetName, bundles, CacheIndex.Classify(assetName));
                log.AppendLine($"asset {assetName}: {bundles.Length} bundle(s), kind {asset.Kind}");
                var setName = Path.GetFileNameWithoutExtension(assetName);
                var progress = new Progress<(int, int, string)>();
                var r = await ex.ExportSetAsync(setName, new[] { asset }, outDir, withDeps, progress);
                log.AppendLine($"RESULT bundles={r.Bundles} deps={r.DependencyBundles} bytes={r.Bytes} out={r.OutputDir}");

                if (args.Contains("--blender", StringComparer.OrdinalIgnoreCase))
                {
                    var b = await BlenderExport.ConvertAsync(r.OutputDir, progress);
                    log.AppendLine(b.Error is null
                        ? $"BLENDER meshes={b.Meshes} textures={b.Textures} out={b.OutputDir}"
                        : $"BLENDER ERROR {b.Error}");
                }
            }
        }
        catch (Exception exx) { log.AppendLine($"ERROR {exx.GetType().Name}: {exx.Message}"); }

        try
        {
            Directory.CreateDirectory(outDir);
            File.WriteAllText(Path.Combine(outDir, "asset-result.txt"), log.ToString(), new UTF8Encoding(false));
        }
        catch { }
    }

    /// <summary>
    /// Velopack's contract: Run() must execute first and exactly once, because
    /// anything before it is re-executed during install, update and uninstall
    /// hooks. That rules out App.OnStartup, so WPF's generated entry point is
    /// replaced by this one (see StartupObject in the csproj).
    /// </summary>
    [STAThread]
    public static void Main(string[] args)
    {
        VelopackApp.Build().Run();

        var app = new App();
        app.InitializeComponent();
        app.Run();
    }

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        if (e.Args.Contains("--build-cabmap", StringComparer.OrdinalIgnoreCase))
        {
            await BuildCabMapAsync();
            Shutdown();
            return;
        }

        if (e.Args.Contains("--export-asset", StringComparer.OrdinalIgnoreCase))
        {
            await RunAssetCliAsync(e.Args);
            Shutdown();
            return;
        }

        if (e.Args.Contains("--export-costume", StringComparer.OrdinalIgnoreCase))
        {
            await RunCostumeCliAsync(e.Args);
            Shutdown();
            return;
        }

        if (e.Args.Contains("--extract", StringComparer.OrdinalIgnoreCase))
        {
            await RunCliAsync(e.Args);
            Shutdown();
            return;
        }

        new MainWindow().Show();
    }

    /// <summary>
    /// Headless mode:
    ///   Tenkabana.exe --extract tenka,amana --out D:\path [--kinds mesh,texture]
    /// Writes a summary to &lt;out&gt;\cli-result.txt so it can be scripted.
    /// </summary>
    private static async Task RunCliAsync(string[] args)
    {
        string Arg(string name, string fallback)
        {
            int i = Array.FindIndex(args, a => a.Equals(name, StringComparison.OrdinalIgnoreCase));
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback;
        }

        var who = Arg("--extract", "");
        var outDir = Arg("--out",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "prism_extract"));
        var kindArg = Arg("--kinds", "mesh,texture,other");
        bool withDeps = args.Contains("--with-deps", StringComparer.OrdinalIgnoreCase);

        var log = new StringBuilder();
        try
        {
            var kinds = new HashSet<AssetKind>();
            foreach (var k in kindArg.Split(',', StringSplitOptions.RemoveEmptyEntries))
                if (Enum.TryParse<AssetKind>(k.Trim(), true, out var kk)) kinds.Add(kk);

            var idols = new List<Idol>();
            foreach (var token in who.Split(',', StringSplitOptions.RemoveEmptyEntries))
            {
                var t = token.Trim();
                var match = int.TryParse(t, out var id)
                    ? Idol.All.FirstOrDefault(i => i.Id == id)
                    : Idol.All.FirstOrDefault(i => i.Name.Equals(t, StringComparison.OrdinalIgnoreCase));
                if (match is null) log.AppendLine($"unknown idol: {t}");
                else idols.Add(match);
            }

            if (idols.Count == 0) { log.AppendLine("no idols resolved"); }
            else
            {
                var index = CacheIndex.Load(IndexPath);
                var ex = new Extractor(index);
                if (withDeps && File.Exists(CabMapPath))
                {
                    ex.Cabs = CabMap.Load(CabMapPath);
                    log.AppendLine($"cab map: {ex.Cabs.Count} CABs");
                }
                else if (withDeps)
                {
                    log.AppendLine($"WARNING --with-deps but no cab map at {CabMapPath}; run --build-cabmap first");
                }
                log.AppendLine($"index: {index.NameCount} names");
                foreach (var i in idols)
                    log.AppendLine($"  {i.Id:D2} {i.Name}: {index.For(i.Id).Count(a => kinds.Contains(a.Kind))} assets match");

                var progress = new Progress<(int, int, string)>();
                var r = await ex.RunAsync(new ExtractRequest(idols, kinds, outDir, withDeps), progress);
                log.AppendLine($"RESULT bundles={r.Bundles} deps={r.DependencyBundles} bytes={r.Bytes} names={r.NamesMatched} out={r.OutputDir}");
            }
        }
        catch (Exception ex) { log.AppendLine($"ERROR {ex.GetType().Name}: {ex.Message}"); }

        try
        {
            Directory.CreateDirectory(outDir);
            File.WriteAllText(Path.Combine(outDir, "cli-result.txt"), log.ToString(), new UTF8Encoding(false));
        }
        catch { }
    }
}
