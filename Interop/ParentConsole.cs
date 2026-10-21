using System.Runtime.InteropServices;

namespace Tenkabana.Interop;

/// <summary>
/// Tenkabana is a WinExe, so command-line runs have no console of their own.
/// Attaching to the parent's lets the CLI modes print where they were typed.
/// </summary>
internal static class ParentConsole
{
    [DllImport("kernel32.dll")]
    private static extern bool AttachConsole(int processId);

    public static void Attach()
    {
        try { AttachConsole(-1); } catch { }
    }

    public static void Write(string text)
    {
        try { Console.WriteLine(); Console.Write(text); } catch { }
    }
}
