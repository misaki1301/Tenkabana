using Velopack;
using Velopack.Sources;

namespace Tenkabana.Services;

/// <summary>
/// Auto-update from the project's GitHub releases, via Velopack.
///
/// Every v* tag publishes a release (release.yml). The app checks on startup and
/// every few hours, downloads a newer release in the background, then either
/// restarts into it on request or installs it when the app exits.
///
/// Only an installed build updates. Running from bin/ or a portable unzip has no
/// Velopack metadata, and every call here is then a quiet no-op.
/// </summary>
public static class Updater
{
    public const string RepoUrl = "https://github.com/misaki1301/Tenkabana";

    public static readonly TimeSpan CheckInterval = TimeSpan.FromHours(6);

    private static UpdateManager? _mgr;
    private static UpdateInfo? _ready;
    private static readonly SemaphoreSlim Gate = new(1, 1);

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

    /// <summary>The version that is downloaded and waiting to be applied, if any.</summary>
    public static string? ReadyVersion => _ready?.TargetFullRelease.Version.ToString();

    /// <summary>
    /// Check GitHub Releases and download anything newer. Returns the version now
    /// ready to apply, or null. Never throws: offline, rate-limited or no releases
    /// yet all just mean "no update this time".
    /// </summary>
    public static async Task<string?> CheckAndDownloadAsync(Action<int>? progress = null)
    {
        if (!IsInstalled) return null;
        if (!await Gate.WaitAsync(0).ConfigureAwait(false)) return ReadyVersion;   // a check is running
        try
        {
            var info = await Manager.CheckForUpdatesAsync().ConfigureAwait(false);
            if (info is null) return ReadyVersion;

            if (_ready is not null && _ready.TargetFullRelease.Version >= info.TargetFullRelease.Version)
                return ReadyVersion;   // already have this one

            await Manager.DownloadUpdatesAsync(info, progress).ConfigureAwait(false);
            _ready = info;
            return ReadyVersion;
        }
        catch
        {
            return ReadyVersion;
        }
        finally
        {
            Gate.Release();
        }
    }

    /// <summary>Install the downloaded update and restart into it. Does not return on success.</summary>
    public static void ApplyAndRestart()
    {
        if (_ready is null) return;
        Manager.ApplyUpdatesAndRestart(_ready.TargetFullRelease);
    }

    /// <summary>
    /// Install the downloaded update once this process exits, without restarting,
    /// so the next launch is already up to date.
    /// </summary>
    public static void ApplyOnExit()
    {
        if (_ready is null) return;
        try { Manager.WaitExitThenApplyUpdates(_ready.TargetFullRelease, silent: true, restart: false); }
        catch { /* an update that cannot be staged is retried next session */ }
    }
}
