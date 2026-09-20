using System.Windows.Media;

namespace Tenkabana.Models;

public sealed record Unit(string Name, string Accent);

public sealed class Idol
{
    public int Id { get; init; }
    public string Name { get; init; } = "";        // romaji given name (matches --idols tokens)
    public string FullName { get; init; } = "";
    public string JpName { get; init; } = "";
    public string UnitName { get; init; } = "";
    public Color A { get; init; }                  // gradient start
    public Color B { get; init; }                  // gradient end

    public string Initial => string.IsNullOrEmpty(Name) ? "?" : Name[..1].ToUpperInvariant();
    public string IdText => Id.ToString("D2");
    public string DisplayName => char.ToUpperInvariant(Name[0]) + Name[1..];

    /// <summary>Asset count from the index; set at startup before binding.</summary>
    public int AssetCount { get; set; }
    public string CountText => AssetCount == 0 ? "not cached" : $"{AssetCount} assets";

    /// <summary>Optional real portrait loaded from Assets/portraits/NN.png.</summary>
    public System.Windows.Media.ImageSource? Portrait { get; set; }
    public bool HasPortrait => Portrait is not null;

    private Brush? _grad;
    public Brush Gradient
    {
        get
        {
            if (_grad is null)
            {
                var g = new LinearGradientBrush(A, B, 60);
                g.Freeze();
                _grad = g;
            }
            return _grad;
        }
    }

    public static readonly IReadOnlyList<Idol> All = Build();

    private static Idol Mk(int id, string name, string full, string jp, string unit, string a, string b) => new()
    {
        Id = id, Name = name, FullName = full, JpName = jp, UnitName = unit,
        A = (Color)ColorConverter.ConvertFromString(a)!,
        B = (Color)ColorConverter.ConvertFromString(b)!,
    };

    private static List<Idol> Build()
    {
        // Unit palettes are display groupings chosen for this UI, not official brand colours.
        const string s1 = "#FFB3C7", s2 = "#FF7FA8";   // illumination STARS
        const string l1 = "#F2C57C", l2 = "#D99A4E";   // L'Antica
        const string h1 = "#8FD9A8", h2 = "#4FB477";   // Houkago Climax Girls
        const string a1 = "#C9A7EB", a2 = "#9B6FD1";   // Alstroemeria
        const string t1 = "#7FC8F8", t2 = "#3E8FD0";   // Straylight
        const string n1 = "#9FB3C8", n2 = "#5E7A99";   // noctchill
        const string z1 = "#F29CA3", z2 = "#D4646E";   // SHHis
        const string c1 = "#A8E6E2", c2 = "#5FB7B2";   // CoMETIK
        const string x1 = "#CBD2D9", x2 = "#94A1AD";   // staff

        return new List<Idol>
        {
            Mk(1,"mano","Mano Sakuragi","桜木真乃","illumination STARS",s1,s2),
            Mk(2,"hiori","Hiori Kazano","風野灯織","illumination STARS",s1,s2),
            Mk(3,"meguru","Meguru Hachimiya","八宮めぐる","illumination STARS",s1,s2),
            Mk(4,"kogane","Kogane Tsukioka","月岡恋鐘","L'Antica",l1,l2),
            Mk(5,"mamimi","Mamimi Tanaka","田中摩美々","L'Antica",l1,l2),
            Mk(6,"sakuya","Sakuya Shirase","白瀬咲耶","L'Antica",l1,l2),
            Mk(7,"yuika","Yuika Mitsumine","三峰結華","L'Antica",l1,l2),
            Mk(8,"kiriko","Kiriko Yukoku","幽谷霧子","L'Antica",l1,l2),
            Mk(9,"kaho","Kaho Komiya","小宮果穂","Houkago Climax Girls",h1,h2),
            Mk(10,"chiyoko","Chiyoko Sonoda","園田智代子","Houkago Climax Girls",h1,h2),
            Mk(11,"juri","Juri Saijo","西城樹里","Houkago Climax Girls",h1,h2),
            Mk(12,"rinze","Rinze Morino","杜野凛世","Houkago Climax Girls",h1,h2),
            Mk(13,"natsuha","Natsuha Arisugawa","有栖川夏葉","Houkago Climax Girls",h1,h2),
            Mk(14,"amana","Amana Osaki","大崎甘奈","Alstroemeria",a1,a2),
            Mk(15,"tenka","Tenka Osaki","大崎甜花","Alstroemeria",a1,a2),
            Mk(16,"chiyuki","Chiyuki Kuwayama","桑山千雪","Alstroemeria",a1,a2),
            Mk(17,"asahi","Asahi Serizawa","芹沢あさひ","Straylight",t1,t2),
            Mk(18,"fuyuko","Fuyuko Mayuzumi","黛冬優子","Straylight",t1,t2),
            Mk(19,"mei","Mei Izumi","和泉愛依","Straylight",t1,t2),
            Mk(20,"toru","Toru Asakura","浅倉透","noctchill",n1,n2),
            Mk(21,"madoka","Madoka Higuchi","樋口円香","noctchill",n1,n2),
            Mk(22,"koito","Koito Fukumaru","福丸小糸","noctchill",n1,n2),
            Mk(23,"hinana","Hinana Ichikawa","市川雛菜","noctchill",n1,n2),
            Mk(24,"nichika","Nichika Nanakusa","七草にちか","SHHis",z1,z2),
            Mk(25,"mikoto","Mikoto Aketa","緋田美琴","SHHis",z1,z2),
            Mk(26,"luca","Luca Ikaruga","斑鳩ルカ","CoMETIK",c1,c2),
            Mk(27,"hana","Hana Suzuki","鈴木羽那","CoMETIK",c1,c2),
            Mk(28,"haruki","Haruki Ikuta","郁田はるき","CoMETIK",c1,c2),
            Mk(90,"hazuki","Hazuki Nanakusa","七草はづき","283 Production",x1,x2),
        };
    }
}
