using System.Globalization;
using System.IO;
using System.Windows.Media;
using System.Windows.Media.Media3D;

namespace Tenkabana.Services;

/// <summary>Minimal Wavefront OBJ loader: positions, normals and triangulated faces.</summary>
public static class ObjModel
{
    public static MeshGeometry3D? Load(string path, int maxTriangles = 400_000)
    {
        var positions = new Point3DCollection();
        var normalsSrc = new List<Vector3D>();
        var normals = new Vector3DCollection();
        var indices = new Int32Collection();
        var remap = new Dictionary<(int v, int n), int>();

        try
        {
            using var sr = new StreamReader(path);
            var raw = new List<Point3D>();
            string? line;
            int tris = 0;

            while ((line = sr.ReadLine()) is not null)
            {
                if (line.Length < 2) continue;
                if (line[0] == 'v' && line[1] == ' ')
                {
                    var p = Split(line, 3);
                    if (p is not null) raw.Add(new Point3D(p[0], p[1], p[2]));
                }
                else if (line[0] == 'v' && line[1] == 'n')
                {
                    var p = Split(line, 3);
                    if (p is not null) normalsSrc.Add(new Vector3D(p[0], p[1], p[2]));
                }
                else if (line[0] == 'f' && line[1] == ' ')
                {
                    var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length < 4) continue;
                    // fan-triangulate n-gons
                    for (int k = 2; k + 1 < parts.Length; k++)
                    {
                        if (tris >= maxTriangles) break;
                        foreach (var corner in new[] { parts[1], parts[k], parts[k + 1] })
                        {
                            var (vi, ni) = Corner(corner, raw.Count, normalsSrc.Count);
                            if (vi < 0) continue;
                            var key = (vi, ni);
                            if (!remap.TryGetValue(key, out var idx))
                            {
                                idx = positions.Count;
                                positions.Add(raw[vi]);
                                if (ni >= 0 && ni < normalsSrc.Count) normals.Add(normalsSrc[ni]);
                                remap[key] = idx;
                            }
                            indices.Add(idx);
                        }
                        tris++;
                    }
                }
            }
        }
        catch { return null; }

        if (positions.Count == 0 || indices.Count == 0) return null;

        var mesh = new MeshGeometry3D { Positions = positions, TriangleIndices = indices };
        if (normals.Count == positions.Count) mesh.Normals = normals;
        mesh.Freeze();
        return mesh;
    }

    private static double[]? Split(string line, int n)
    {
        var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length < n + 1) return null;
        var v = new double[n];
        for (int i = 0; i < n; i++)
            if (!double.TryParse(parts[i + 1], NumberStyles.Float, CultureInfo.InvariantCulture, out v[i]))
                return null;
        return v;
    }

    /// <summary>Parse "v", "v/t", "v//n" or "v/t/n"; OBJ indices are 1-based and may be negative.</summary>
    private static (int v, int n) Corner(string s, int vCount, int nCount)
    {
        var bits = s.Split('/');
        int vi = Idx(bits.Length > 0 ? bits[0] : "", vCount);
        int ni = bits.Length > 2 ? Idx(bits[2], nCount) : -1;
        return (vi, ni);
    }

    private static int Idx(string s, int count)
    {
        if (!int.TryParse(s, out var i)) return -1;
        return i > 0 ? i - 1 : (i < 0 ? count + i : -1);
    }
}
