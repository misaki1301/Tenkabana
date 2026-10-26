using System.IO;
using System.Reflection;
using System.Text.Json;
using Velopack;
using Velopack.Sources;

namespace Tenkabana.Services;

/// <summary>A newer release the user can choose to install.</summary>
public sealed record UpdateOffer(string CurrentVersion, string NewVersion, IReadOnlyList<ReleaseNote> Notes);

/// <summary>
/// Update check against the project's GitHub releases, via Velopack.
///
/// Every v* tag publishes a release (release.yml). The app only ever checks on
/// its own; downloading and installing happen when the user asks, from the
/// update dialog. Nothing is applied silently, not even on exit.
///
/// Only an installed build updates. Running from bin/ or a portable unzip has no
/// Velopack metadata, and checks then report nothing.
/// </summary>
public static class Updater
{
    public const string RepoUrl = "https://github.com/misaki1301/Tenkabana";
    public const string ReleasesUrl = RepoUrl + "/releases";

    public static readonly TimeSpan CheckInterval = TimeSpan.FromHours(6);

    private static readonly string StatePath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "prism_tools", "update.json");

    private static UpdateManager? _mgr;
    private static UpdateInfo? _info;

    private static UpdateManager Manager =>
        _mgr ??= new UpdateManager(new GithubSource(RepoUrl, null, false));

    public static bool IsInstalled
    {
        get
        {
            try { return Manager.IsInstalled; }
            catch { return false; }
        }
    }

    /// <summary>The running version: Velopack's when installed, else the assembly's.</summary>
    public static string CurrentVersion
    {
        get
        {
            try
            {
                if (Manager.IsInstalled && Manager.CurrentVersion is { } v) return v.ToString();
            }
            catch { }
            var asm = Assembly.GetEntryAssembly() ?? typeof(Updater).Assembly;
            var info = asm.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
            return (info ?? asm.GetName().Version?.ToString(3) ?? "0.0.0").Split('+')[0];
        }
    }

    /// <summary>
    /// Look for a newer release. Downloads nothing. Returns null when up to date,
    /// not installed, offline, rate-limited or no releases exist yet.
    /// A version the user skipped is only offered when <paramref name="includeSkipped"/>.
    /// </summary>
    public static async Task<UpdateOffer?> CheckAsync(bool includeSkipped = false, CancellationToken ct = default)
    {
        if (!IsInstalled) return null;
        try
        {
            var info = await Manager.CheckForUpdatesAsync().ConfigureAwait(false);
            if (info is null) return null;
            _info = info;

            var current = CurrentVersion;
            var target = info.TargetFullRelease.Version.ToString();
            if (!includeSkipped && string.Equals(SkippedVersion, target, StringComparison.OrdinalIgnoreCase))
                return null;

            var notes = await ReleaseNotes.FetchAsync(RepoUrl, current, target, ct).ConfigureAwait(false);
            if (notes.Count == 0 && !string.IsNullOrWhiteSpace(info.TargetFullRelease.NotesMarkdown))
                notes.Add(new ReleaseNote(target, "Tenkabana " + target, null,
                                          info.TargetFullRelease.NotesMarkdown, ReleasesUrl));
            return new UpdateOffer(current, target, notes);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Download the offered release. Only called once the user has said yes.</summary>
    public static async Task DownloadAsync(Action<int>? progress, CancellationToken ct)
    {
        if (_info is null) throw new InvalidOperationException("Check for updates first.");
        await Manager.DownloadUpdatesAsync(_info, progress, ct).ConfigureAwait(false);
    }

    /// <summary>Install the downloaded release and restart into it. Does not return on success.</summary>
    public static void ApplyAndRestart()
    {
        if (_info is null) return;
        Manager.ApplyUpdatesAndRestart(_info.TargetFullRelease);
    }

    // ---------------------------------------------------------------------
    // "Skip this version"
    // ---------------------------------------------------------------------

    public static string? SkippedVersion
    {
        get
        {
            try
            {
                if (!File.Exists(StatePath)) return null;
                using var doc = JsonDocument.Parse(File.ReadAllText(StatePath));
                return doc.RootElement.TryGetProperty("skipped", out var s) ? s.GetString() : null;
            }
            catch { return null; }
        }
        set
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(StatePath)!);
                File.WriteAllText(StatePath, JsonSerializer.Serialize(new { skipped = value }));
            }
            catch { /* not remembering a skip only means being asked again */ }
        }
    }
}
