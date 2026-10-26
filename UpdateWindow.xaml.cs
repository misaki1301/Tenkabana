using System.Diagnostics;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using Tenkabana.Interop;
using Tenkabana.Services;

namespace Tenkabana;

/// <summary>
/// Shows what changed and lets the user decide: update now, later, or skip this
/// version. Nothing is downloaded until they press Update.
/// </summary>
public partial class UpdateWindow : Window
{
    private readonly UpdateOffer _offer;
    private readonly Func<bool> _isBusy;
    private CancellationTokenSource? _cts;

    /// <summary>True when the user chose "Skip this version".</summary>
    public bool Skipped { get; private set; }

    public UpdateWindow(UpdateOffer offer, Func<bool> isBusy)
    {
        InitializeComponent();
        _offer = offer;
        _isBusy = isBusy;
        HeaderText.Text = $"Tenkabana {offer.NewVersion} is available";
        VersionText.Text = $"You have {offer.CurrentVersion}. Nothing is downloaded until you choose to update.";
        NotesViewer.Document = BuildDocument(offer.Notes);
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        bool dark = WindowEffects.IsDarkTheme();
        WindowEffects.Apply(this, dark);
        Background = new SolidColorBrush(dark
            ? Color.FromRgb(0x20, 0x20, 0x20)
            : Color.FromRgb(0xF3, 0xF3, 0xF3));
    }

    private async void OnUpdate(object sender, RoutedEventArgs e)
    {
        // Restarting mid-export would leave a half-written folder behind.
        if (_isBusy())
        {
            ShowStatus("Finish or cancel the current job in the main window, then update.");
            return;
        }

        UpdateButton.IsEnabled = false;
        SkipButton.IsEnabled = false;
        LaterButton.Content = "Cancel";
        ProgressPanel.Visibility = Visibility.Visible;
        DownloadProgress.Visibility = Visibility.Visible;
        DownloadProgress.Value = 0;
        ProgressText.Text = "Downloading...";

        _cts = new CancellationTokenSource();
        try
        {
            await Updater.DownloadAsync(p => Dispatcher.BeginInvoke(() =>
            {
                DownloadProgress.Value = p;
                ProgressText.Text = $"Downloading... {p}%";
            }), _cts.Token);

            if (_isBusy())
            {
                ResetButtons();
                ShowStatus("Downloaded. A job started meanwhile: finish it, then press Update and restart.");
                return;
            }
            ProgressText.Text = "Installing and restarting...";
            Updater.ApplyAndRestart();
        }
        catch (OperationCanceledException)
        {
            ResetButtons();
            ShowStatus("Download cancelled. Nothing was installed.");
        }
        catch (Exception ex)
        {
            ResetButtons();
            ShowStatus($"The update couldn't be downloaded: {ex.Message}");
        }
        finally
        {
            _cts?.Dispose();
            _cts = null;
        }
    }

    private void OnLater(object sender, RoutedEventArgs e)
    {
        if (_cts is not null) { _cts.Cancel(); return; }
        Close();
    }

    private void OnSkip(object sender, RoutedEventArgs e)
    {
        Updater.SkippedVersion = _offer.NewVersion;
        Skipped = true;
        Close();
    }

    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        _cts?.Cancel();   // closing mid-download abandons it; nothing gets installed
        base.OnClosing(e);
    }

    private void ResetButtons()
    {
        UpdateButton.IsEnabled = true;
        SkipButton.IsEnabled = true;
        LaterButton.Content = "Later";
    }

    /// <summary>A message under the changelog, with no progress bar.</summary>
    private void ShowStatus(string text)
    {
        ProgressPanel.Visibility = Visibility.Visible;
        DownloadProgress.Visibility = Visibility.Collapsed;
        ProgressText.Text = text;
    }

    // ---------------------------------------------------------------------
    // Changelog rendering
    // ---------------------------------------------------------------------

    private static FlowDocument BuildDocument(IReadOnlyList<ReleaseNote> notes)
    {
        var doc = new FlowDocument
        {
            PagePadding = new Thickness(14, 10, 14, 10),
            FontFamily = (FontFamily)Application.Current.Resources["TextFont"],
            FontSize = (double)Application.Current.Resources["BodySize"],
        };
        doc.SetResourceReference(TextElement.ForegroundProperty, "TextPrimary");

        if (notes.Count == 0)
        {
            doc.Blocks.Add(new Paragraph(new Run("No release notes were published for this version.")));
        }

        foreach (var note in notes)
        {
            var title = new Paragraph { Margin = new Thickness(0, doc.Blocks.Count == 0 ? 0 : 18, 0, 4) };
            title.Inlines.Add(new Run(note.Title) { FontSize = 18, FontWeight = FontWeights.SemiBold });
            if (note.Published is { } when)
            {
                var date = new Run($"   {when.LocalDateTime:MMM d, yyyy}") { FontSize = 12 };
                date.SetResourceReference(TextElement.ForegroundProperty, "TextSecondary");
                title.Inlines.Add(date);
            }
            doc.Blocks.Add(title);

            List? bullets = null;
            foreach (var block in ReleaseNotes.ToBlocks(note.Markdown))
            {
                if (block.Kind != NoteBlockKind.Bullet) bullets = null;
                switch (block.Kind)
                {
                    case NoteBlockKind.Heading:
                        var h = Para(block, new Thickness(0, 10, 0, 2));
                        h.FontWeight = FontWeights.SemiBold;
                        h.FontSize = block.Level <= 1 ? 17 : block.Level == 2 ? 15 : 14;
                        doc.Blocks.Add(h);
                        break;
                    case NoteBlockKind.Bullet:
                        if (bullets is null)
                        {
                            bullets = new List { MarkerStyle = TextMarkerStyle.Disc, Margin = new Thickness(0, 2, 0, 2), Padding = new Thickness(20, 0, 0, 0) };
                            doc.Blocks.Add(bullets);
                        }
                        bullets.ListItems.Add(new ListItem(Para(block, new Thickness(0, 1, 0, 1))));
                        break;
                    case NoteBlockKind.Rule:
                        var rule = new Paragraph { Margin = new Thickness(0, 6, 0, 6), BorderThickness = new Thickness(0, 0, 0, 1) };
                        rule.SetResourceReference(Block.BorderBrushProperty, "StrokeStrong");
                        doc.Blocks.Add(rule);
                        break;
                    default:
                        doc.Blocks.Add(Para(block, new Thickness(0, 4, 0, 4)));
                        break;
                }
            }
        }

        var footer = new Paragraph { Margin = new Thickness(0, 18, 0, 0), FontSize = 12 };
        footer.Inlines.Add(Link("All releases on GitHub", Updater.ReleasesUrl));
        doc.Blocks.Add(footer);
        return doc;
    }

    private static Paragraph Para(NoteBlock block, Thickness margin)
    {
        var p = new Paragraph { Margin = margin };
        foreach (var run in block.Runs)
        {
            if (run.Url is not null) p.Inlines.Add(Link(run.Text, run.Url));
            else if (run.Code) p.Inlines.Add(new Run(run.Text) { FontFamily = new FontFamily("Consolas") });
            else if (run.Bold) p.Inlines.Add(new Bold(new Run(run.Text)));
            else p.Inlines.Add(new Run(run.Text));
        }
        return p;
    }

    private static Inline Link(string text, string url)
    {
        // Only web links: release notes are text from GitHub, not something to execute.
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttps && uri.Scheme != Uri.UriSchemeHttp))
            return new Run(text);

        var link = new Hyperlink(new Run(text)) { NavigateUri = uri };
        link.SetResourceReference(TextElement.ForegroundProperty, "Accent");
        link.RequestNavigate += (_, e) =>
        {
            try { Process.Start(new ProcessStartInfo(e.Uri.AbsoluteUri) { UseShellExecute = true }); }
            catch { /* no browser registered */ }
            e.Handled = true;
        };
        return link;
    }
}
