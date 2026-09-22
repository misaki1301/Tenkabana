using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Text.RegularExpressions;
using System.Windows.Media.Media3D;
using System.Windows.Input;
using System.Windows.Controls;
using Microsoft.Win32;
using Tenkabana.Interop;
using Tenkabana.Models;
using Tenkabana.Services;

namespace Tenkabana;

public partial class MainWindow : Window
{
    private static readonly string ToolsDir =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "prism_tools");
    private static string IndexPath => Path.Combine(ToolsDir, "cache_index.json");
    private static string StatePath => Path.Combine(ToolsDir, "state.json");
    private static string CabMapPath => Path.Combine(ToolsDir, "cab_map.json");

    private CacheIndex? _index;
    private Extractor? _extractor;
    private string _outputDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "prism_extract");
    private CancellationTokenSource? _cts;
    private bool _busy;

    private PreviewService? _preview;
    private List<AssetRow> _assetRows = new();
    private CancellationTokenSource? _previewCts;

    private GeometryModel3D? _model;
    private readonly AxisAngleRotation3D _yaw = new(new Vector3D(0, 1, 0), 0);
    private readonly AxisAngleRotation3D _pitch = new(new Vector3D(1, 0, 0), 0);
    private Point3D _modelCenter;
    private double _modelRadius = 1, _zoom = 1;
    private System.Windows.Point _lastMouse;
    private bool _dragging;

    public sealed record AssetRow(string Name, string KindText, AssetEntry Entry, string Group);

    private static readonly Regex CostumeCode =
        new(@"(?<![0-9a-z])(\d{2})([a-z]{2})(\d{2})(?![0-9a-z])",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

    // dress_0237_14mgar_00_col / acce_40237_14mgar_14_col -> code is explicit
    private static readonly Regex CostumeNamed =
        new(@"^(?:m_)?(?:dress|acce)_(\d+)_([a-z0-9]{4,8})_",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

    // m_hair_14_005_40237.prefab -> only a number ties it to the outfit
    private static readonly Regex IdToken =
        new(@"(?<![0-9])([0-9]{4,6})(?![0-9])", RegexOptions.Compiled);

    private Dictionary<string, string> _idToCode = new();

    /// <summary>
    /// Costume ("skin") if the name carries one, otherwise the body part it
    /// belongs to. Asset names are the only grouping signal available - the
    /// costume code is the game's own identifier for an outfit.
    /// </summary>
    private string GroupOf(string name)
    {
        // 1. the name states its costume code outright
        var named = CostumeNamed.Match(name);
        if (named.Success) return "Costume " + named.Groups[2].Value.ToLowerInvariant();

        // 2. idol-scoped code embedded in the name (15sw02, 01ev01...)
        var m = CostumeCode.Match(name);
        if (m.Success) return "Costume " + m.Value.ToLowerInvariant();

        // 3. only a numeric id - look it up against the costumes that do name themselves
        foreach (Match t in IdToken.Matches(name))
        {
            var id = t.Groups[1].Value;
            if (id.Length > 4) id = id[^4..];
            if (id == "0000") continue;
            if (_idToCode.TryGetValue(id, out var code)) return "Costume " + code;
        }

        if (name.StartsWith("m_head_", StringComparison.OrdinalIgnoreCase) ||
            name.StartsWith("sd_head_", StringComparison.OrdinalIgnoreCase) ||
            Regex.IsMatch(name, @"^\d{2}_face", RegexOptions.IgnoreCase)) return "Head and face";
        if (name.StartsWith("m_hair_", StringComparison.OrdinalIgnoreCase) ||
            Regex.IsMatch(name, @"^\d{2}_hair", RegexOptions.IgnoreCase)) return "Hair";
        if (Regex.IsMatch(name, @"^\d{2}_eye", RegexOptions.IgnoreCase)) return "Eyes";
        if (Regex.IsMatch(name, @"^\d{2}_(bodyskin|skin)", RegexOptions.IgnoreCase)) return "Body";
        if (name.StartsWith("m_dress_", StringComparison.OrdinalIgnoreCase) ||
            name.StartsWith("dress_", StringComparison.OrdinalIgnoreCase)) return "Dress (uncoded)";
        if (name.StartsWith("m_acce_", StringComparison.OrdinalIgnoreCase) ||
            name.StartsWith("acce_", StringComparison.OrdinalIgnoreCase)) return "Accessories";
        if (name.StartsWith("mot_", StringComparison.OrdinalIgnoreCase) ||
            name.StartsWith("cut_", StringComparison.OrdinalIgnoreCase)) return "Animation";
        return "Other";
    }

    public MainWindow()
    {
        InitializeComponent();
        OutputPathLabel.Text = _outputDir;
        Loaded += OnLoaded;
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        bool dark = WindowEffects.IsDarkTheme();
        ApplyPalette(dark);
        WindowEffects.Apply(this, dark);   // rounded corners + themed title bar
        // Paint the Windows 11 base layer ourselves: Mica through WPF is unreliable
        // and a transparent background lets the desktop show through.
        Background = new SolidColorBrush(dark
            ? Color.FromRgb(0x20, 0x20, 0x20)
            : Color.FromRgb(0xF3, 0xF3, 0xF3));
    }

    private void ApplyPalette(bool dark)
    {
        var r = Application.Current.Resources;

        // Replace the brushes directly. Going through Color keys lets the merged
        // dictionary's own defaults win, which silently leaves text unreadable.
        void Brush(string key, string hex)
        {
            var b = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)!);
            b.Freeze();
            r[key] = b;
        }

        if (dark)
        {
            Brush("TextPrimary", "#FFFFFFFF");
            Brush("TextSecondary", "#A5FFFFFF");
            Brush("Layer", "#2D2D2D");
            Brush("LayerAlt", "#383838");
            Brush("Stroke", "#1FFFFFFF");
            Brush("StrokeStrong", "#33FFFFFF");
        }
        else
        {
            Brush("TextPrimary", "#E4000000");
            Brush("TextSecondary", "#9B000000");
            Brush("Layer", "#FBFBFB");
            Brush("LayerAlt", "#FFFFFF");
            Brush("Stroke", "#14000000");
            Brush("StrokeStrong", "#29000000");
        }

        var accent = new SolidColorBrush(WindowEffects.AccentColor());
        accent.Freeze();
        r["Accent"] = accent;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (!File.Exists(IndexPath))
        {
            SubtitleLabel.Text = $"No index found at {IndexPath}";
            StatusLabel.Text = "Run the indexer first, or press Refresh index to build one.";
            IdolList.ItemsSource = Idol.All;
            return;
        }

        SubtitleLabel.Text = "Loading index...";
        try
        {
            var idx = await Task.Run(() => CacheIndex.Load(IndexPath));
            _index = idx;
            _extractor = new Extractor(idx);

            _preview = new PreviewService(idx);
            _idToCode = await Task.Run(idx.CostumeIdMap);

            if (File.Exists(CabMapPath))
            {
                var cabs = await Task.Run(() => CabMap.Load(CabMapPath));
                _extractor.Cabs = cabs;
                _preview.Cabs = cabs;
            }
            else
            {
                ChipDeps.IsChecked = false;
                ChipDeps.IsEnabled = false;
                ChipDeps.ToolTip = $"No cab map at {CabMapPath} - run Tenkabana.exe --build-cabmap";
            }

            var counts = await Task.Run(() => idx.CountsByIdol(Idol.All.Select(i => i.Id)));
            foreach (var idol in Idol.All)
            {
                idol.AssetCount = counts.TryGetValue(idol.Id, out var c) ? c : 0;
                idol.Portrait = LoadPortrait(idol.Id);
            }

            IdolList.ItemsSource = Idol.All;
            SubtitleLabel.Text =
                $"{idx.NameCount:N0} assets indexed  ·  {Idol.All.Count(i => i.AssetCount > 0)} of {Idol.All.Count} characters cached";

            _ = CheckForUpdateAsync();
        }
        catch (Exception ex)
        {
            SubtitleLabel.Text = "Failed to load index";
            StatusLabel.Text = ex.Message;
            IdolList.ItemsSource = Idol.All;
        }
    }

    /// <summary>Non-blocking, best effort: a failed check must never hold up startup.</summary>
    private async Task CheckForUpdateAsync()
    {
        var r = await Updater.CheckAsync();
        if (r.Available && r.Version is not null)
            SubtitleLabel.Text += $"   ·   update {r.Version} available";
    }

    private static ImageSource? LoadPortrait(int id)
    {
        var p = Path.Combine(AppContext.BaseDirectory, "Assets", "portraits", $"{id:D2}.png");
        if (!File.Exists(p)) return null;
        try
        {
            var bmp = new BitmapImage();
            bmp.BeginInit();
            bmp.CacheOption = BitmapCacheOption.OnLoad;
            bmp.UriSource = new Uri(p);
            bmp.EndInit();
            bmp.Freeze();
            return bmp;
        }
        catch { return null; }
    }

    private HashSet<AssetKind> SelectedKinds()
    {
        var set = new HashSet<AssetKind>();
        if (ChipMesh.IsChecked == true) set.Add(AssetKind.Mesh);
        if (ChipTex.IsChecked == true) set.Add(AssetKind.Texture);
        if (ChipAnim.IsChecked == true) set.Add(AssetKind.Animation);
        if (ChipOther.IsChecked == true) set.Add(AssetKind.Other);
        return set;
    }

    private void UpdateReady()
    {
        if (_busy) return;
        int n = IdolList.SelectedItems.Count;
        var kinds = SelectedKinds();
        ExtractButton.IsEnabled = n > 0 && kinds.Count > 0 && _index is not null;

        if (n == 0) StatusLabel.Text = "Select one or more characters.";
        else if (kinds.Count == 0) StatusLabel.Text = "Select at least one asset category.";
        else
        {
            int total = IdolList.SelectedItems.Cast<Idol>()
                .Sum(i => _index is null ? 0 : _index.For(i.Id).Count(a => kinds.Contains(a.Kind)));
            var who = n == 1 ? ((Idol)IdolList.SelectedItems[0]!).DisplayName : $"{n} characters";
            StatusLabel.Text = $"{who}  ·  {total:N0} assets match the current filter.";
        }
    }

    private void OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        UpdateReady();
        PopulateAssetList();
    }

    private async void PopulateAssetList()
    {
        if (_index is null) return;
        var idols = IdolList.SelectedItems.Cast<Idol>().ToList();
        var kinds = SelectedKinds();
        bool shared = ChipShared.IsChecked == true;

        // Building this can touch tens of thousands of rows. Do it off the UI
        // thread and drop the result if the selection moved on meanwhile.
        int generation = ++_populateGeneration;
        var rows = await Task.Run(() => BuildRows(idols, kinds, shared));
        if (generation != _populateGeneration) return;

        _assetRows = rows;
        _assetHeader = idols.Count switch
        {
            0 => "Assets",
            1 => "Assets - " + idols[0].DisplayName,
            _ => "Assets - " + idols.Count + " characters",
        };
        ApplyAssetFilter();
    }

    private List<AssetRow> BuildRows(List<Idol> idols, HashSet<AssetKind> kinds, bool shared)
    {
        if (_index is null) return new List<AssetRow>();

        var rows = idols
            .SelectMany(i => _index.For(i.Id)
                .Where(a => kinds.Contains(a.Kind))
                .Select(a => new AssetRow(a.Name, a.Kind + " - " + a.Bundles.Count + " bundle(s)", a, GroupOf(a.Name))))
            .GroupBy(r => r.Name, StringComparer.Ordinal)
            .Select(g => g.First())
            .OrderBy(r => GroupRank(r.Group))
            .ThenBy(r => r.Group, StringComparer.Ordinal)
            .ThenBy(r => r.Name, StringComparer.Ordinal)
            .ToList();

        if (shared)
        {
            _shared ??= _index.SharedCostumes();
            // 23k+ rows: build them once, then filter the cached list by kind
            _sharedRowCache ??= _shared
                .SelectMany(kv => kv.Value.Select(a => new AssetRow(a.Name,
                    a.Kind + " - " + a.Bundles.Count + " bundle(s)", a, "Shared " + kv.Key)))
                .ToList();
            var extra = _sharedRowCache.Where(r => kinds.Contains(r.Entry.Kind)).ToList();
            rows = rows.Concat(extra)
                .GroupBy(r => r.Name, StringComparer.Ordinal)
                .Select(g => g.First())
                .OrderBy(r => GroupRank(r.Group))
                .ThenBy(r => r.Group, StringComparer.Ordinal)
                .ThenBy(r => r.Name, StringComparer.Ordinal)
                .ToList();
        }

        return rows;
    }

    private string _assetHeader = "Assets";
    private Dictionary<string, List<AssetEntry>>? _shared;
    private List<AssetRow>? _sharedRowCache;
    private bool _repopulating;
    private IReadOnlyList<string> _objPaths = Array.Empty<string>();
    private bool _settingMeshList;
    private int _populateGeneration;
    private System.Windows.Threading.DispatcherTimer? _searchDebounce;

    private static int GroupRank(string g) => g switch
    {
        var x when x.StartsWith("Costume", StringComparison.Ordinal) => 0,
        var x when x.StartsWith("Shared", StringComparison.Ordinal) => 9,
        "Head and face" => 1,
        "Hair" => 2,
        "Eyes" => 3,
        "Body" => 4,
        "Dress (uncoded)" => 5,
        "Accessories" => 6,
        "Animation" => 8,
        _ => 7,
    };

    private void ApplyAssetFilter()
    {
        _repopulating = true;
        try { ApplyAssetFilterCore(); }
        finally { _repopulating = false; }
    }

    private void ApplyAssetFilterCore()
    {
        var q = AssetSearch.Text?.Trim() ?? "";
        var rows = q.Length == 0
            ? _assetRows
            : _assetRows.Where(r => r.Name.Contains(q, StringComparison.OrdinalIgnoreCase)).ToList();

        const int cap = 2000;
        var shown = rows.Count > cap ? rows.Take(cap).ToList() : rows;

        var view = new System.Windows.Data.CollectionViewSource { Source = shown };
        view.GroupDescriptions.Add(new System.Windows.Data.PropertyGroupDescription("Group"));
        AssetList.ItemsSource = view.View;
        AssetListHeader.Text = _assetRows.Count == 0
            ? _assetHeader
            : _assetHeader + "  (" + rows.Count.ToString("N0")
              + (rows.Count > cap ? ", showing " + cap.ToString("N0") : "") + ")";
    }

    private void OnAssetSearchChanged(object sender, TextChangedEventArgs e)
    {
        // re-grouping on every keystroke is what makes typing feel sticky
        _searchDebounce ??= new System.Windows.Threading.DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(180)
        };
        _searchDebounce.Tick -= OnSearchDebounceTick;
        _searchDebounce.Tick += OnSearchDebounceTick;
        _searchDebounce.Stop();
        _searchDebounce.Start();
    }

    private void OnSearchDebounceTick(object? sender, EventArgs e)
    {
        _searchDebounce?.Stop();
        ApplyAssetFilter();
    }

    private async void OnAssetSelected(object sender, SelectionChangedEventArgs e)
    {
        if (_repopulating) return;

        // the costume button acts on whatever group the selected asset is in
        var grp = (AssetList.SelectedItem as AssetRow)?.Group;
        bool isCostume = grp is not null &&
                         grp.StartsWith("Costume ", StringComparison.Ordinal);
        ExportCostumeButton.IsEnabled = isCostume && !_busy && _extractor is not null;
        ExportSelectedButton.IsEnabled = !_busy && _extractor is not null &&
                                         AssetList.SelectedItem is AssetRow;
        ExportCostumeButton.Content = isCostume
            ? "Export " + grp!["Costume ".Length..]
            : "Export costume";

        if (AssetList.SelectedItem is not AssetRow row || _preview is null) return;

        _previewCts?.Cancel();
        _previewCts = new CancellationTokenSource();
        var ct = _previewCts.Token;

        PreviewPlaceholder.Visibility = Visibility.Collapsed;
        PreviewBusy.Visibility = Visibility.Visible;
        PreviewInfo.Text = row.Name;

        try
        {
            var r = await _preview.GetAsync(row.Entry, ct);
            if (ct.IsCancellationRequested) return;
            ShowPreview(r, row);
        }
        catch (Exception ex)
        {
            PreviewInfo.Text = row.Name + " - " + ex.Message;
        }
        finally
        {
            if (!ct.IsCancellationRequested) PreviewBusy.Visibility = Visibility.Collapsed;
        }
    }

    private void ShowPreview(PreviewResult r, AssetRow row)
    {
        PreviewImage.Visibility = Visibility.Collapsed;
        ViewportHost.Visibility = Visibility.Collapsed;
        PreviewPlaceholder.Visibility = Visibility.Collapsed;

        MeshPickerHost.Visibility = Visibility.Collapsed;

        if (r.ObjPaths.Count > 0 && ObjModel.Load(r.ObjPaths[0]) is { } mesh)
        {
            _objPaths = r.ObjPaths;

            // a prefab resolves to many meshes (face, eyes, hair, teeth...) -
            // list them all instead of silently showing only the largest
            _settingMeshList = true;
            MeshPicker.ItemsSource = r.ObjPaths
                .Select(System.IO.Path.GetFileNameWithoutExtension)
                .ToList();
            MeshPicker.SelectedIndex = 0;
            _settingMeshList = false;
            MeshPickerHost.Visibility = r.ObjPaths.Count > 1 ? Visibility.Visible : Visibility.Collapsed;

            SetModel(mesh);
            ViewportHost.Visibility = Visibility.Visible;
            PreviewInfo.Text = row.Name + " - " + r.Info + " - drag to orbit, wheel to zoom";
            return;
        }

        _objPaths = Array.Empty<string>();

        if (r.ImagePath is not null && File.Exists(r.ImagePath))
        {
            try
            {
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.UriSource = new Uri(r.ImagePath);
                bmp.EndInit();
                bmp.Freeze();
                PreviewImage.Source = bmp;
                PreviewImage.Visibility = Visibility.Visible;
                PreviewInfo.Text = row.Name + " - " + bmp.PixelWidth + "x" + bmp.PixelHeight
                                   + " - " + r.Info;
                return;
            }
            catch { }
        }

        PreviewPlaceholder.Text = r.Info;
        PreviewPlaceholder.Visibility = Visibility.Visible;
        PreviewInfo.Text = row.Name;
    }

    private void OnMeshPicked(object sender, SelectionChangedEventArgs e)
    {
        if (_settingMeshList) return;
        int i = MeshPicker.SelectedIndex;
        if (i < 0 || i >= _objPaths.Count) return;

        var path = _objPaths[i];
        if (ObjModel.Load(path) is not { } mesh)
        {
            PreviewInfo.Text = "Could not load " + System.IO.Path.GetFileName(path);
            return;
        }
        SetModel(mesh);
        ViewportHost.Visibility = Visibility.Visible;
        PreviewInfo.Text = System.IO.Path.GetFileNameWithoutExtension(path)
            + " - " + mesh.Positions.Count.ToString("N0") + " verts, "
            + (mesh.TriangleIndices.Count / 3).ToString("N0") + " tris - drag to orbit, wheel to zoom";
    }

    private void SetModel(MeshGeometry3D mesh)
    {
        for (int i = PreviewModelGroup.Children.Count - 1; i >= 0; i--)
            if (PreviewModelGroup.Children[i] is GeometryModel3D)
                PreviewModelGroup.Children.RemoveAt(i);

        var mat = new MaterialGroup();
        mat.Children.Add(new DiffuseMaterial(new SolidColorBrush(Color.FromRgb(0xC8, 0xC6, 0xD0))));
        mat.Children.Add(new SpecularMaterial(new SolidColorBrush(Color.FromRgb(0x50, 0x50, 0x50)), 24));

        _model = new GeometryModel3D(mesh, mat) { BackMaterial = mat };

        var b = mesh.Bounds;
        _modelCenter = new Point3D(b.X + b.SizeX / 2, b.Y + b.SizeY / 2, b.Z + b.SizeZ / 2);
        _modelRadius = Math.Max(0.0001,
            0.5 * Math.Sqrt(b.SizeX * b.SizeX + b.SizeY * b.SizeY + b.SizeZ * b.SizeZ));

        var rot = new Transform3DGroup();
        rot.Children.Add(new TranslateTransform3D(-_modelCenter.X, -_modelCenter.Y, -_modelCenter.Z));
        rot.Children.Add(new RotateTransform3D(_pitch));
        rot.Children.Add(new RotateTransform3D(_yaw));
        _model.Transform = rot;

        PreviewModelGroup.Children.Add(_model);

        _yaw.Angle = 0;
        _pitch.Angle = 0;
        _zoom = 1;
        UpdateCamera();
    }

    private void UpdateCamera()
    {
        double dist = _modelRadius / Math.Tan(PreviewCamera.FieldOfView * Math.PI / 360.0) * 1.6 * _zoom;
        PreviewCamera.Position = new Point3D(0, 0, dist);
        PreviewCamera.LookDirection = new Vector3D(0, 0, -1);
        PreviewCamera.UpDirection = new Vector3D(0, 1, 0);
        PreviewCamera.NearPlaneDistance = Math.Max(0.001, dist / 1000);
        PreviewCamera.FarPlaneDistance = dist * 10;
    }

    private void OnViewportMouseDown(object sender, MouseButtonEventArgs e)
    {
        _lastMouse = e.GetPosition(ViewportHost);
        _dragging = true;
        ViewportHost.CaptureMouse();
    }

    private void OnViewportMouseMove(object sender, MouseEventArgs e)
    {
        if (!_dragging || e.LeftButton != MouseButtonState.Pressed)
        {
            if (_dragging) { _dragging = false; ViewportHost.ReleaseMouseCapture(); }
            return;
        }
        var p = e.GetPosition(ViewportHost);
        _yaw.Angle += (p.X - _lastMouse.X) * 0.5;
        _pitch.Angle = Math.Clamp(_pitch.Angle + (p.Y - _lastMouse.Y) * 0.5, -89, 89);
        _lastMouse = p;
    }

    private void OnViewportWheel(object sender, MouseWheelEventArgs e)
    {
        _zoom = Math.Clamp(_zoom * (e.Delta > 0 ? 0.88 : 1.14), 0.1, 8);
        UpdateCamera();
    }

    private void OnFilterChanged(object sender, RoutedEventArgs e) { UpdateReady(); PopulateAssetList(); }

    private void OnSelectAll(object sender, RoutedEventArgs e)
    {
        // SelectAll() is the supported path for a multi-select ListBox; building the
        // selection by hand through SelectedItems is unreliable here.
        IdolList.SelectAll();
        foreach (var i in Idol.All.Where(i => i.AssetCount == 0))
            IdolList.SelectedItems.Remove(i);
        UpdateReady();
    }

    private void OnClearSelection(object sender, RoutedEventArgs e)
    {
        IdolList.SelectedItems.Clear();
        UpdateReady();
    }

    private async void OnExportSelected(object sender, RoutedEventArgs e)
    {
        if (_extractor is null || AssetList.SelectedItem is not AssetRow row) return;

        var name = System.IO.Path.GetFileNameWithoutExtension(row.Name);
        var dlg = new OpenFolderDialog
        {
            Title = $"Export {row.Name} to...",
            InitialDirectory = _outputDir,
        };
        if (dlg.ShowDialog(this) != true) return;

        await RunExportAsync(name, new List<AssetEntry> { row.Entry }, dlg.FolderName, row.Name);
    }

    /// <summary>Shared tail for both export buttons: export, optionally convert, report.</summary>
    private async Task RunExportAsync(string setName, List<AssetEntry> assets, string target, string label)
    {
        _cts = new CancellationTokenSource();
        SetBusy(true);
        Progress.IsIndeterminate = false;
        Progress.Value = 0;
        var progress = new Progress<(int done, int total, string label)>(p =>
        {
            if (p.total > 0) { Progress.Maximum = p.total; Progress.Value = p.done; }
            Title = $"Tenkabana  —  {setName} {p.done}/{p.total}";
        });

        try
        {
            var r = await _extractor!.ExportSetAsync(
                setName, assets, target, ChipDeps.IsChecked == true, progress, _cts.Token);
            var deps = r.DependencyBundles > 0 ? $" (incl. {r.DependencyBundles:N0} dependency bundles)" : "";
            var msg = $"Exported {label}: {r.Bundles:N0} bundles{deps}, {r.Bytes / 1024.0 / 1024.0:N1} MB -> {r.OutputDir}";

            if (ChipBlender.IsChecked == true)
            {
                Title = $"Tenkabana  —  converting {setName}";
                Progress.IsIndeterminate = true;
                var b = await BlenderExport.ConvertAsync(r.OutputDir, progress, _cts.Token);
                msg = b.Error is null
                    ? $"Exported {label}: {b.Meshes:N0} meshes + {b.Textures:N0} textures ready for Blender -> {b.OutputDir}"
                    : msg + "  (conversion skipped: " + b.Error + ")";
            }
            SetBusy(false, msg);
        }
        catch (OperationCanceledException) { SetBusy(false, "Cancelled."); }
        catch (Exception ex) { SetBusy(false, $"Failed: {ex.Message}"); }
        finally { Title = "Tenkabana"; }
    }

    private async void OnExportCostume(object sender, RoutedEventArgs e)
    {
        if (_extractor is null || AssetList.SelectedItem is not AssetRow row) return;
        if (!row.Group.StartsWith("Costume ", StringComparison.Ordinal)) return;

        var code = row.Group["Costume ".Length..];
        var assets = _assetRows
            .Where(r => string.Equals(r.Group, row.Group, StringComparison.Ordinal))
            .Select(r => r.Entry)
            .ToList();
        if (assets.Count == 0) return;

        var dlg = new OpenFolderDialog
        {
            Title = $"Export costume {code} ({assets.Count} assets) to...",
            InitialDirectory = _outputDir,
        };
        if (dlg.ShowDialog(this) != true) return;
        var target = dlg.FolderName;

        _cts = new CancellationTokenSource();
        SetBusy(true);
        Progress.IsIndeterminate = false;
        Progress.Value = 0;
        var progress = new Progress<(int done, int total, string label)>(p =>
        {
            if (p.total > 0)
            {
                Progress.Maximum = p.total;
                Progress.Value = p.done;
            }
            Title = $"Tenkabana  —  {code} {p.done}/{p.total}";
        });

        try
        {
            var r = await _extractor.ExportSetAsync(
                code, assets, target, ChipDeps.IsChecked == true, progress, _cts.Token);
            var deps = r.DependencyBundles > 0 ? $" (incl. {r.DependencyBundles:N0} dependency bundles)" : "";
            var msg = $"Exported costume {code}: {r.Bundles:N0} bundles{deps}, {r.Bytes / 1024.0 / 1024.0:N1} MB -> {r.OutputDir}";

            if (ChipBlender.IsChecked == true)
            {
                Title = $"Tenkabana  —  converting {code}";
                Progress.IsIndeterminate = true;
                var b = await BlenderExport.ConvertAsync(r.OutputDir, progress, _cts.Token);
                msg = b.Error is null
                    ? $"Exported costume {code}: {b.Meshes:N0} meshes + {b.Textures:N0} textures ready for Blender -> {b.OutputDir}"
                    : msg + "  (conversion skipped: " + b.Error + ")";
            }

            SetBusy(false, msg);
        }
        catch (OperationCanceledException) { SetBusy(false, "Cancelled."); }
        catch (Exception ex) { SetBusy(false, $"Failed: {ex.Message}"); }
        finally { Title = "Tenkabana"; }
    }

    private void OnBrowse(object sender, RoutedEventArgs e)
    {
        var dlg = new OpenFolderDialog { Title = "Choose output folder", InitialDirectory = _outputDir };
        if (dlg.ShowDialog(this) == true)
        {
            _outputDir = dlg.FolderName;
            OutputPathLabel.Text = _outputDir;
        }
    }

    private void OnOpenOutput(object sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(_outputDir);
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{_outputDir}\"") { UseShellExecute = true });
        }
        catch (Exception ex) { StatusLabel.Text = ex.Message; }
    }

    private void SetBusy(bool busy, string? label = null)
    {
        _busy = busy;
        ExtractButton.IsEnabled = !busy;
        RefreshButton.IsEnabled = !busy;
        ExportCostumeButton.IsEnabled = !busy &&
            (AssetList.SelectedItem as AssetRow)?.Group.StartsWith("Costume ", StringComparison.Ordinal) == true;
        ExportSelectedButton.IsEnabled = !busy && AssetList.SelectedItem is AssetRow;
        IdolList.IsEnabled = !busy;
        Progress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        StatusLabel.Visibility = busy ? Visibility.Collapsed : Visibility.Visible;
        if (!busy && label is not null) StatusLabel.Text = label;
    }

    private async void OnExtract(object sender, RoutedEventArgs e)
    {
        if (_extractor is null || IdolList.SelectedItems.Count == 0) return;

        var idols = IdolList.SelectedItems.Cast<Idol>().ToList();
        var req = new ExtractRequest(idols, SelectedKinds(), _outputDir,
            IncludeDependencies: ChipDeps.IsChecked == true);

        _cts = new CancellationTokenSource();
        SetBusy(true);
        Progress.IsIndeterminate = false;
        Progress.Value = 0;

        var progress = new Progress<(int done, int total, string label)>(p =>
        {
            Progress.Maximum = Math.Max(1, p.total);
            Progress.Value = p.done;
            Title = $"Tenkabana  —  {p.done}/{p.total}";
        });

        try
        {
            var r = await _extractor.RunAsync(req, progress, _cts.Token);
            var deps = r.DependencyBundles > 0 ? $" (incl. {r.DependencyBundles:N0} dependency bundles)" : "";
            var msg = $"Extracted {r.Bundles:N0} bundles{deps}, {r.Bytes / 1024.0 / 1024.0:N1} MB, from {r.NamesMatched:N0} assets to {r.OutputDir}";

            if (ChipBlender.IsChecked == true)
            {
                Progress.IsIndeterminate = true;
                int converted = 0, tex = 0;
                foreach (var dir in Directory.GetDirectories(r.OutputDir))
                {
                    Title = $"Tenkabana  —  converting {System.IO.Path.GetFileName(dir)}";
                    var b = await BlenderExport.ConvertAsync(dir, progress, _cts.Token);
                    converted += b.Meshes;
                    tex += b.Textures;
                }
                if (converted > 0 || tex > 0)
                    msg = $"Extracted and converted: {converted:N0} meshes + {tex:N0} textures ready for Blender -> {r.OutputDir}";
            }

            SetBusy(false, msg);
        }
        catch (OperationCanceledException) { SetBusy(false, "Cancelled."); }
        catch (Exception ex) { SetBusy(false, $"Failed: {ex.Message}"); }
        finally { Title = "Tenkabana"; }
    }

    private async void OnRefresh(object sender, RoutedEventArgs e)
    {
        if (_extractor is null || _index is null) return;

        var since = ReadLastRun();
        SetBusy(true);
        Progress.IsIndeterminate = true;

        var progress = new Progress<(int done, int total, string label)>(p =>
        {
            if (p.total > 0)
            {
                Progress.IsIndeterminate = false;
                Progress.Maximum = p.total;
                Progress.Value = p.done;
            }
            Title = $"Tenkabana  —  indexing {p.done}/{p.total}";
        });

        try
        {
            int added = await _extractor.RefreshAsync(since, IndexPath, progress);
            WriteLastRun(DateTime.Now);

            var counts = await Task.Run(() => _index.CountsByIdol(Idol.All.Select(i => i.Id)));
            foreach (var idol in Idol.All)
                idol.AssetCount = counts.TryGetValue(idol.Id, out var c) ? c : 0;
            IdolList.ItemsSource = null;
            IdolList.ItemsSource = Idol.All;

            SubtitleLabel.Text =
                $"{_index.NameCount:N0} assets indexed  ·  {Idol.All.Count(i => i.AssetCount > 0)} of {Idol.All.Count} characters cached";
            SetBusy(false, added == 0
                ? "Index is up to date — nothing new in the cache."
                : $"Indexed {added:N0} new assets.");
        }
        catch (Exception ex) { SetBusy(false, $"Refresh failed: {ex.Message}"); }
        finally { Title = "Tenkabana"; }
    }

    private static DateTime ReadLastRun()
    {
        try
        {
            if (!File.Exists(StatePath)) return DateTime.MinValue;
            using var doc = JsonDocument.Parse(File.ReadAllText(StatePath));
            if (doc.RootElement.TryGetProperty("last_run", out var v))
                return DateTimeOffset.FromUnixTimeSeconds((long)v.GetDouble()).LocalDateTime;
        }
        catch { }
        return DateTime.MinValue;
    }

    private static void WriteLastRun(DateTime t)
    {
        try
        {
            var epoch = new DateTimeOffset(t).ToUnixTimeSeconds();
            File.WriteAllText(StatePath, $"{{\"last_run\": {epoch}}}");
        }
        catch { }
    }
}
