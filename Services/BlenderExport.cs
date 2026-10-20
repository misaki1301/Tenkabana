using System.Diagnostics;
using System.IO;
using System.Text;

namespace Tenkabana.Services;

public sealed record BlenderResult(int Meshes, int Textures, string OutputDir, string? Error);

/// <summary>
/// Turns exported .unity3d bundles into files Blender can actually open:
/// meshes to .obj, textures to .png. Shells out to AssetStudioModCLI.
/// </summary>
public static class BlenderExport
{
    public static string? FindCli() => PreviewService.FindCli();

    private static bool Run(string cli, string args, int timeoutMs = 600_000)
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
        _ = p.StandardOutput.ReadToEndAsync();
        _ = p.StandardError.ReadToEndAsync();
        return p.WaitForExit(timeoutMs);
    }

    public static async Task<BlenderResult> ConvertAsync(
        string bundlesDir,
        IProgress<(int done, int total, string label)> progress,
        CancellationToken ct = default)
    {
        return await Task.Run(() =>
        {
            var cli = FindCli();
            if (cli is null)
                return new BlenderResult(0, 0, bundlesDir,
                    "AssetStudioModCLI not found under %USERPROFILE%\\prism_tools\\astudio");

            var meshDir = Path.Combine(bundlesDir, "meshes");
            var texDir = Path.Combine(bundlesDir, "textures");
            Directory.CreateDirectory(meshDir);
            Directory.CreateDirectory(texDir);

            progress.Report((0, 2, "meshes"));
            Run(cli, $"\"{bundlesDir}\" -m export -t mesh -g none -o \"{meshDir}\" --log-level error");
            ct.ThrowIfCancellationRequested();

            progress.Report((1, 2, "textures"));
            Run(cli, $"\"{bundlesDir}\" -m export -t tex2d,sprite -g none -o \"{texDir}\" " +
                     $"--image-format png --log-level error");
            progress.Report((2, 2, "done"));

            int meshes = Directory.Exists(meshDir)
                ? Directory.GetFiles(meshDir, "*.obj", SearchOption.AllDirectories).Length : 0;
            int textures = Directory.Exists(texDir)
                ? Directory.GetFiles(texDir, "*.png", SearchOption.AllDirectories).Length : 0;

            WriteScripts(bundlesDir);
            WriteReadme(bundlesDir, meshes, textures);
            return new BlenderResult(meshes, textures, bundlesDir, null);
        }, ct);
    }

    /// <summary>Drop the Blender add-on, template builder and material extractor next to the export.</summary>
    private static void WriteScripts(string dir)
    {
        var asm = typeof(BlenderExport).Assembly;
        foreach (var name in new[] { "tenkabana_toon.py", "build_template.py", "prism_extract.py" })
        {
            try
            {
                using var src = asm.GetManifestResourceStream(name);
                if (src is null) continue;
                using var dst = File.Create(Path.Combine(dir, name));
                src.CopyTo(dst);
            }
            catch (IOException) { }
        }
    }

    private static void WriteReadme(string dir, int meshes, int textures)
    {
        var name = new DirectoryInfo(dir).Name;
        var sb = new StringBuilder();
        sb.AppendLine($"{name} - exported for Blender");
        sb.AppendLine(new string('=', 40));
        sb.AppendLine();
        sb.AppendLine($"meshes/    {meshes} .obj files");
        sb.AppendLine($"textures/  {textures} .png files");
        sb.AppendLine($"*.unity3d  the raw bundles these came from");
        sb.AppendLine();
        sb.AppendLine("IMPORT");
        sb.AppendLine("  File > Import > Wavefront (.obj), select everything in meshes/.");
        sb.AppendLine("  Unity is Y-up and Blender is Z-up; if a part arrives lying on its");
        sb.AppendLine("  back, rotate +90 on X.");
        sb.AppendLine();
        sb.AppendLine("GAME SHADING (recommended)");
        sb.AppendLine("  1. pip install UnityPy");
        sb.AppendLine("     python prism_extract.py \"<this folder>\"");
        sb.AppendLine("     Reads the bundles here and writes blender/: model.glb with every part");
        sb.AppendLine("     and both UV sets, materials.json with the game's material values, and");
        sb.AppendLine("     the textures those use.");
        sb.AppendLine("  2. In Blender: Edit > Preferences > Add-ons > Install from Disk >");
        sb.AppendLine("     tenkabana_toon.py, then N sidebar > Tenkabana > set this folder > Import.");
        sb.AppendLine("     Parts come in with the game's shaders rebuilt as nodes, outlines, the");
        sb.AppendLine("     fake hair shadow and body height applied.");
        sb.AppendLine("  Or headless:  blender -b -P build_template.py -- --export \"<this folder>\"");
        sb.AppendLine("  Skipping step 1 still works: parts are matched to textures by name.");
        sb.AppendLine();
        sb.AppendLine("TEXTURES");
        sb.AppendLine("  Without the add-on, materials do not survive the trip - wire them by hand.");
        sb.AppendLine("    _col     base colour");
        sb.AppendLine("    _Rmask   rim light mask (R)");
        sb.AppendLine("    _Smask   shadow/shading mask");
        sb.AppendLine("    _SPmap   specular: R mask, G roughness, B metallic");
        sb.AppendLine("    _Rnoise  angel ring noise (hair, second UV set)");
        sb.AppendLine("    _bs_a / _bs_b / _bss   blendshape-related maps");
        sb.AppendLine();
        sb.AppendLine("LIMITATION - NO RIG");
        sb.AppendLine("  .obj carries geometry only: no armature, no bone weights, no shape");
        sb.AppendLine("  keys. AssetStudioMod exports meshes as .obj, so that is the ceiling");
        sb.AppendLine("  here. For a rigged model with blendshapes, run AssetRipper against");
        sb.AppendLine("  the .unity3d bundles in this folder and export FBX or glTF instead.");
        sb.AppendLine();
        sb.AppendLine("BODY PROPORTIONS");
        sb.AppendLine("  Characters share a base body, shaped per idol at runtime. Without");
        sb.AppendLine("  applying these the model is the neutral base:");
        sb.AppendLine("    Tenka (15)  height 0.99375  bust 0.478");
        sb.AppendLine("    Amana (14)  height 0.99375  bust 0.435");
        sb.AppendLine("  height is a uniform scale; bust is a blendshape weight (needs a rig).");
        try { File.WriteAllText(Path.Combine(dir, "HOW-TO-IMPORT.txt"), sb.ToString(), new UTF8Encoding(false)); }
        catch { }
    }
}
