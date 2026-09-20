using System.Buffers.Binary;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using K4os.Compression.LZ4;

namespace Tenkabana.Services;

/// <summary>
/// Minimal UnityFS reader.
///
/// Three jobs:
///   - recover asset names from the leading blocks (indexing)
///   - list the CAB files a bundle CONTAINS   (blocksInfo nodes)
///   - list the CAB files a bundle REFERENCES (externals in the serialized data)
///
/// The last two give the dependency graph. Asset names in this game carry no
/// character identity for meshes ("BodySkin", "FrontHair", "ATAMA_shadow"), so
/// geometry can only be found by following a prefab's references.
/// </summary>
public static class UnityFs
{
    public static bool HasPrefix(ReadOnlySpan<byte> d) =>
        d.Length >= 4 && d[0] == 0xBA && d[1] == 0x01 && d[2] == 0x00 && d[3] == 0x00;

    public static bool IsBundle(ReadOnlySpan<byte> d) =>
        d.Length >= 7 && d[0] == (byte)'U' && d[1] == (byte)'n' && d[2] == (byte)'i' &&
        d[3] == (byte)'t' && d[4] == (byte)'y' && d[5] == (byte)'F' && d[6] == (byte)'S';

    public static ReadOnlySpan<byte> StripPrefix(ReadOnlySpan<byte> d) =>
        HasPrefix(d) ? d[4..] : d;

    private static void SkipCString(ReadOnlySpan<byte> d, ref int o)
    {
        while (o < d.Length && d[o] != 0) o++;
        o++;
    }

    private readonly record struct Block(uint Uncompressed, uint Compressed, ushort Flags);

    private sealed record Layout(List<Block> Blocks, List<string> Nodes, int DataOffset);

    private static Layout ReadLayout(ReadOnlySpan<byte> data)
    {
        if (!IsBundle(data)) throw new InvalidDataException("not UnityFS");
        int o = 8;
        uint ver = BinaryPrimitives.ReadUInt32BigEndian(data[o..]); o += 4;
        SkipCString(data, ref o);   // unity version
        SkipCString(data, ref o);   // unity revision

        o += 8;                                                             // size
        uint ci = BinaryPrimitives.ReadUInt32BigEndian(data[o..]); o += 4;  // compressed blocksInfo
        uint ui = BinaryPrimitives.ReadUInt32BigEndian(data[o..]); o += 4;  // uncompressed blocksInfo
        uint flags = BinaryPrimitives.ReadUInt32BigEndian(data[o..]); o += 4;

        if (ver >= 7) o = (o + 15) & ~15;

        bool dirAtEnd = (flags & 0x80) != 0;
        int biOff = dirAtEnd ? data.Length - (int)ci : o;
        if (biOff < 0 || biOff + ci > data.Length) throw new InvalidDataException("blocksInfo out of range");

        var raw = data.Slice(biOff, (int)ci);
        byte[] info;
        uint comp = flags & 0x3F;
        if (comp == 0) info = raw.ToArray();
        else if (comp is 2 or 3)
        {
            info = new byte[ui];
            if (LZ4Codec.Decode(raw, info) < 0) throw new InvalidDataException("blocksInfo LZ4 failed");
        }
        else throw new NotSupportedException($"blocksInfo compression {comp}");

        int p = 16;                                                   // skip hash
        uint blockCount = BinaryPrimitives.ReadUInt32BigEndian(info.AsSpan(p)); p += 4;
        var blocks = new List<Block>((int)blockCount);
        for (int i = 0; i < blockCount; i++)
        {
            blocks.Add(new Block(
                BinaryPrimitives.ReadUInt32BigEndian(info.AsSpan(p)),
                BinaryPrimitives.ReadUInt32BigEndian(info.AsSpan(p + 4)),
                BinaryPrimitives.ReadUInt16BigEndian(info.AsSpan(p + 8))));
            p += 10;
        }

        uint nodeCount = BinaryPrimitives.ReadUInt32BigEndian(info.AsSpan(p)); p += 4;
        var nodes = new List<string>((int)nodeCount);
        for (int i = 0; i < nodeCount; i++)
        {
            p += 20;                                                  // offset, size, flags
            int end = Array.IndexOf(info, (byte)0, p);
            if (end < 0) break;
            nodes.Add(Encoding.UTF8.GetString(info, p, end - p));
            p = end + 1;
        }

        int dOff = dirAtEnd ? o : biOff + (int)ci;
        if ((flags & 0x200) != 0) dOff = (dOff + 15) & ~15;           // BlockInfoNeedPaddingAtStart
        return new Layout(blocks, nodes, dOff);
    }

    private static byte[] Decompress(ReadOnlySpan<byte> data, Layout layout, int maxBytes)
    {
        using var outBuf = new MemoryStream(Math.Min(maxBytes, 1 << 20));
        int cur = layout.DataOffset;
        foreach (var b in layout.Blocks)
        {
            if (cur + b.Compressed > data.Length) break;
            var chunk = data.Slice(cur, (int)b.Compressed);
            cur += (int)b.Compressed;
            uint m = (uint)(b.Flags & 0x3F);
            if (m == 0) outBuf.Write(chunk);
            else if (m is 2 or 3)
            {
                var dst = new byte[b.Uncompressed];
                int n = LZ4Codec.Decode(chunk, dst);
                if (n < 0) throw new InvalidDataException("block LZ4 failed");
                outBuf.Write(dst, 0, n);
            }
            else throw new NotSupportedException($"block compression {m}");
            if (outBuf.Length >= maxBytes) break;
        }
        return outBuf.ToArray();
    }

    /// <summary>Decompress leading blocks only - asset names cluster near the start.</summary>
    public static byte[] FirstBlocks(ReadOnlySpan<byte> data, int maxBytes = 400_000)
        => Decompress(data, ReadLayout(data), maxBytes);

    /// <summary>CAB files this bundle contains. Cheap: blocksInfo only, no block decode.</summary>
    public static HashSet<string> Provides(ReadOnlySpan<byte> data)
    {
        var set = new HashSet<string>(StringComparer.Ordinal);
        foreach (var n in ReadLayout(data).Nodes)
        {
            var leaf = n[(n.LastIndexOf('/') + 1)..];
            if (leaf.StartsWith("CAB-", StringComparison.Ordinal)) set.Add(leaf);
        }
        return set;
    }

    private static readonly Regex CabRef =
        new(@"CAB-[0-9a-f]{32}", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    /// <summary>
    /// CAB files this bundle references, excluding its own. Needs the data blocks,
    /// so this is the expensive half - resolve it on demand, not for the whole cache.
    /// </summary>
    public static HashSet<string> Requires(ReadOnlySpan<byte> data, HashSet<string>? own = null)
    {
        var layout = ReadLayout(data);
        own ??= Provides(data);
        var blob = Decompress(data, layout, int.MaxValue);
        var text = Encoding.ASCII.GetString(blob);
        var set = new HashSet<string>(StringComparer.Ordinal);
        foreach (Match m in CabRef.Matches(text))
            if (!own.Contains(m.Value)) set.Add(m.Value);
        return set;
    }

    private static readonly Regex[] Shapes =
    {
        new(@"^\d{2}_(face|hair|bodyskin|eye|skin|cheek|mouth)_", RegexOptions.Compiled),
        new(@"^m_(head|hair|dress|acce|face|body)_", RegexOptions.Compiled),
        new(@"^sd_head_\d+", RegexOptions.Compiled),
        new(@"^cut_s\d+_", RegexOptions.Compiled),
        new(@"^mot_", RegexOptions.Compiled),
        new(@"^dress_", RegexOptions.Compiled),
        new(@"^acce_", RegexOptions.Compiled),
    };

    private static bool IsTokenChar(byte b) =>
        (b >= '0' && b <= '9') || (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z') || b == '_' || b == '.';

    /// <summary>Pull plausible asset names out of decompressed bundle bytes.</summary>
    public static HashSet<string> NamesIn(byte[] blob)
    {
        var found = new HashSet<string>(StringComparer.Ordinal);
        int i = 0;
        while (i < blob.Length)
        {
            if (!IsTokenChar(blob[i])) { i++; continue; }
            int start = i;
            while (i < blob.Length && IsTokenChar(blob[i])) i++;
            int len = i - start;
            if (len < 6) continue;
            var tok = Encoding.ASCII.GetString(blob, start, len);
            foreach (var s in Shapes)
                if (s.IsMatch(tok)) { found.Add(tok); break; }
        }
        return found;
    }
}
