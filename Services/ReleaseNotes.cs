using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Tenkabana.Services;

/// <summary>One published release, as GitHub shows it.</summary>
public sealed record ReleaseNote(string Version, string Title, DateTimeOffset? Published, string Markdown, string Url);

public enum NoteBlockKind { Heading, Bullet, Paragraph, Rule }

/// <summary>A run of text inside a block; Url is set for links.</summary>
public sealed record NoteRun(string Text, bool Bold = false, bool Code = false, string? Url = null);

public sealed record NoteBlock(NoteBlockKind Kind, IReadOnlyList<NoteRun> Runs, int Level = 0);

/// <summary>
/// Changelogs for the update dialog: GitHub release bodies between the running
/// version and the offered one, and a small Markdown reader for what GitHub's
/// generated notes contain (headings, bullets, bold, code, links, bare URLs).
/// </summary>
public static class ReleaseNotes
{
    private static readonly HttpClient Http = CreateClient();

    private static HttpClient CreateClient()
    {
        var c = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        c.DefaultRequestHeaders.UserAgent.ParseAdd("Tenkabana-Updater");   // GitHub rejects requests without one
        c.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
        return c;
    }

    /// <summary>
    /// Published, non-prerelease releases with current &lt; version &lt;= target, newest
    /// first. Empty on any failure: the dialog then falls back to the notes in the
    /// update package itself.
    /// </summary>
    public static async Task<List<ReleaseNote>> FetchAsync(string repoUrl, string current, string target,
                                                           CancellationToken ct = default)
    {
        try
        {
            var api = repoUrl.Replace("https://github.com/", "https://api.github.com/repos/").TrimEnd('/')
                      + "/releases?per_page=50";
            var json = await Http.GetStringAsync(api, ct).ConfigureAwait(false);
            return Between(Parse(json), current, target);
        }
        catch
        {
            return new List<ReleaseNote>();
        }
    }

    /// <summary>Releases from the GitHub REST "list releases" response.</summary>
    public static List<ReleaseNote> Parse(string json)
    {
        var list = new List<ReleaseNote>();
        using var doc = JsonDocument.Parse(json);
        foreach (var r in doc.RootElement.EnumerateArray())
        {
            if (Bool(r, "draft") || Bool(r, "prerelease")) continue;
            var tag = Str(r, "tag_name");
            if (string.IsNullOrEmpty(tag)) continue;
            DateTimeOffset? published = DateTimeOffset.TryParse(Str(r, "published_at"), out var p) ? p : null;
            list.Add(new ReleaseNote(
                tag.TrimStart('v', 'V'),
                string.IsNullOrWhiteSpace(Str(r, "name")) ? tag : Str(r, "name"),
                published,
                Str(r, "body"),
                Str(r, "html_url")));
        }
        return list;
    }

    public static List<ReleaseNote> Between(IEnumerable<ReleaseNote> all, string current, string target)
    {
        var lo = ParseVersion(current);
        var hi = ParseVersion(target);
        return all
            .Where(n => ParseVersion(n.Version) is { } v && (lo is null || v > lo) && (hi is null || v <= hi))
            .OrderByDescending(n => ParseVersion(n.Version))
            .ToList();
    }

    /// <summary>"1.2.3", "v1.2", "1.2.3-beta" (pre-release suffix ignored) -> Version.</summary>
    public static Version? ParseVersion(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        var core = s.Trim().TrimStart('v', 'V').Split('-', '+')[0];
        var parts = core.Split('.');
        if (parts.Length == 0 || parts.Length > 4) return null;
        var nums = new int[Math.Max(parts.Length, 3)];
        for (int i = 0; i < parts.Length; i++)
            if (!int.TryParse(parts[i], out nums[i]) || nums[i] < 0) return null;
        return parts.Length == 4 ? new Version(nums[0], nums[1], nums[2], nums[3]) : new Version(nums[0], nums[1], nums[2]);
    }

    // ---------------------------------------------------------------------
    // Markdown, as much of it as release notes use
    // ---------------------------------------------------------------------

    private static readonly Regex HeadingRx = new(@"^(#{1,6})\s+(.*)$", RegexOptions.Compiled);
    private static readonly Regex BulletRx = new(@"^\s*[-*+]\s+(.*)$", RegexOptions.Compiled);
    private static readonly Regex RuleRx = new(@"^\s*([-*_])(\s*\1){2,}\s*$", RegexOptions.Compiled);

    // Order matters: links before bare URLs, so a link's URL isn't matched twice.
    private static readonly Regex InlineRx = new(
        @"\[(?<ltext>[^\]]+)\]\((?<lurl>[^)\s]+)\)" +
        @"|\*\*(?<bold>.+?)\*\*" +
        @"|`(?<code>[^`]+)`" +
        @"|(?<url>https?://[^\s)<>]+)",
        RegexOptions.Compiled);

    public static List<NoteBlock> ToBlocks(string? markdown)
    {
        var blocks = new List<NoteBlock>();
        if (string.IsNullOrWhiteSpace(markdown)) return blocks;

        var para = new List<string>();
        void FlushPara()
        {
            if (para.Count == 0) return;
            blocks.Add(new NoteBlock(NoteBlockKind.Paragraph, Runs(string.Join(" ", para))));
            para.Clear();
        }

        foreach (var raw in markdown.Replace("\r\n", "\n").Split('\n'))
        {
            var line = raw.TrimEnd();
            if (line.Length == 0) { FlushPara(); continue; }
            if (line.TrimStart().StartsWith("<!--", StringComparison.Ordinal)) continue;   // GitHub template comments

            Match m;
            if (RuleRx.IsMatch(line)) { FlushPara(); blocks.Add(new NoteBlock(NoteBlockKind.Rule, Array.Empty<NoteRun>())); }
            else if ((m = HeadingRx.Match(line)).Success)
            {
                FlushPara();
                blocks.Add(new NoteBlock(NoteBlockKind.Heading, Runs(m.Groups[2].Value), m.Groups[1].Value.Length));
            }
            else if ((m = BulletRx.Match(line)).Success)
            {
                FlushPara();
                blocks.Add(new NoteBlock(NoteBlockKind.Bullet, Runs(m.Groups[1].Value)));
            }
            else para.Add(line.Trim());
        }
        FlushPara();
        return blocks;
    }

    public static List<NoteRun> Runs(string text)
    {
        var runs = new List<NoteRun>();
        int pos = 0;
        foreach (Match m in InlineRx.Matches(text))
        {
            if (m.Index > pos) runs.Add(new NoteRun(text[pos..m.Index]));
            if (m.Groups["ltext"].Success) runs.Add(new NoteRun(m.Groups["ltext"].Value, Url: m.Groups["lurl"].Value));
            else if (m.Groups["bold"].Success) runs.Add(new NoteRun(m.Groups["bold"].Value, Bold: true));
            else if (m.Groups["code"].Success) runs.Add(new NoteRun(m.Groups["code"].Value, Code: true));
            else runs.Add(new NoteRun(ShortUrl(m.Groups["url"].Value), Url: m.Groups["url"].Value));
            pos = m.Index + m.Length;
        }
        if (pos < text.Length) runs.Add(new NoteRun(text[pos..]));
        return runs;
    }

    /// <summary>".../pull/12" -> "#12", ".../compare/v1.0.0...v1.0.1" -> "v1.0.0...v1.0.1".</summary>
    private static string ShortUrl(string url)
    {
        var m = Regex.Match(url, @"/(?:pull|issues)/(\d+)$");
        if (m.Success) return "#" + m.Groups[1].Value;
        m = Regex.Match(url, @"/compare/(.+)$");
        return m.Success ? m.Groups[1].Value : url;
    }

    private static string Str(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";

    private static bool Bool(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;
}
