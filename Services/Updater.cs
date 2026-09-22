using Velopack;
using Velopack.Sources;

namespace Tenkabana.Services;

public sealed record UpdateCheck(bool Available, string? Version, string? Error);

/// <summary>
/// Velopack update check against the project's GitHub releases.
///
/// Only meaningful for an installed build - running from bin/ or a portable
/// unzip has no Velopack metadata, and the check is skipped rather than failing.
/// </summary>
public static class Updater
{
    public const string RepoUrl = "https://github.com/misaki1301/Tenkabana";

    public static async Task<UpdateCheck> CheckAsync(CancellationToken ct = default)
    {
        try
        {
            var mgr = new UpdateManager(new GithubSource(RepoUrl, null, false));
            if (!mgr.IsInstalled)
                return new UpdateCheck(false, null, null);   // dev or portable build

            var info = await mgr.CheckForUpdatesAsync().ConfigureAwait(false);
            return info is null
                ? new UpdateCheck(false, null, null)
                : new UpdateCheck(true, info.TargetFullRelease.Version.ToString(), null);
        }
        catch (Exception ex)
        {
            // offline, rate-limited, no releases yet - never block startup for this
            return new UpdateCheck(false, null, ex.Message);
        }
    }

    /// <summary>Download and apply, then restart. Only call after a positive check.</summary>
    public static async Task ApplyAsync(CancellationToken ct = default)
    {
        var mgr = new UpdateManager(new GithubSource(RepoUrl, null, false));
        if (!mgr.IsInstalled) return;

        var info = await mgr.CheckForUpdatesAsync().ConfigureAwait(false);
        if (info is null) return;

        await mgr.DownloadUpdatesAsync(info).ConfigureAwait(false);
        mgr.ApplyUpdatesAndRestart(info);
    }
}
