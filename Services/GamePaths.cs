using System.IO;
using System.Text.Json;

namespace Tenkabana.Services;

/// <summary>
/// Where the game keeps its assets on this machine.
///
/// The download cache always lives under the user profile, so that path is
/// derived. The install directory varies by drive, so it is discovered from the
/// DMM Game Player config and can be overridden in settings.json.
/// </summary>
public static class GamePaths
{
    private const string ProductId = "prism";

    public static string SettingsPath => Path.Combine(AppContext.BaseDirectory, "settings.json");

    /// <summary>%USERPROFILE%\AppData\LocalLow\BNE\imasscprism\D</summary>
    public static string DownloadCache => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        @"AppData\LocalLow\BNE\imasscprism\D");

    /// <summary>Assets shipped with the install, if the game directory can be found.</summary>
    public static string? StreamingAssets
    {
        get
        {
            var dir = GameDirectory;
            if (dir is null) return null;
            var p = Path.Combine(dir, @"imasscprism_Data\StreamingAssets\L");
            return Directory.Exists(p) ? p : null;
        }
    }

    private static string? _gameDir;
    private static bool _gameDirResolved;

    public static string? GameDirectory
    {
        get
        {
            if (_gameDirResolved) return _gameDir;
            _gameDirResolved = true;
            _gameDir = FromSettings() ?? FromDmmConfig() ?? FromCommonLocations();
            return _gameDir;
        }
    }

    private static string? FromSettings()
    {
        try
        {
            if (!File.Exists(SettingsPath)) return null;
            using var doc = JsonDocument.Parse(File.ReadAllText(SettingsPath));
            if (doc.RootElement.TryGetProperty("gameDirectory", out var v))
            {
                var p = v.GetString();
                if (!string.IsNullOrWhiteSpace(p) && Directory.Exists(p)) return p;
            }
        }
        catch { }
        return null;
    }

    /// <summary>DMM Game Player records every installed game and its path.</summary>
    private static string? FromDmmConfig()
    {
        try
        {
            var cnf = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"dmmgameplayer5\dmmgame.cnf");
            if (!File.Exists(cnf)) return null;

            using var doc = JsonDocument.Parse(File.ReadAllText(cnf));
            if (!doc.RootElement.TryGetProperty("contents", out var contents)) return null;

            foreach (var c in contents.EnumerateArray())
            {
                if (!c.TryGetProperty("productId", out var id)) continue;
                if (!string.Equals(id.GetString(), ProductId, StringComparison.OrdinalIgnoreCase)) continue;
                if (!c.TryGetProperty("detail", out var d)) continue;
                if (!d.TryGetProperty("path", out var path)) continue;
                var p = path.GetString();
                if (!string.IsNullOrWhiteSpace(p) && Directory.Exists(p)) return p;
            }
        }
        catch { }
        return null;
    }

    private static string? FromCommonLocations()
    {
        foreach (var drive in DriveInfo.GetDrives())
        {
            if (!drive.IsReady) continue;
            foreach (var rel in new[] { @"Games\SONGforPRISM", @"DMMGames\SONGforPRISM" })
            {
                try
                {
                    var p = Path.Combine(drive.RootDirectory.FullName, rel);
                    if (Directory.Exists(p)) return p;
                }
                catch { }
            }
        }
        return null;
    }

    /// <summary>Every root worth scanning, skipping any that do not exist.</summary>
    public static string[] Roots()
    {
        var list = new List<string>();
        if (Directory.Exists(DownloadCache)) list.Add(DownloadCache);
        var sa = StreamingAssets;
        if (sa is not null) list.Add(sa);
        return list.ToArray();
    }
}
