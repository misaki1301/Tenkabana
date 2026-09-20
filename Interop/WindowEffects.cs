using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using Microsoft.Win32;

namespace Tenkabana.Interop;

/// <summary>Windows 11 window personality: Mica backdrop, rounded corners, themed title bar.</summary>
public static class WindowEffects
{
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;
    private const int DWMWA_WINDOW_CORNER_PREFERENCE = 33;
    private const int DWMWA_SYSTEMBACKDROP_TYPE = 38;
    private const int DWMSBT_MAINWINDOW = 2;   // Mica
    private const int DWMWCP_ROUND = 2;

    public static bool IsDarkTheme()
    {
        try
        {
            using var k = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            return k?.GetValue("AppsUseLightTheme") is int v && v == 0;
        }
        catch { return false; }
    }

    public static Color AccentColor()
    {
        try
        {
            using var k = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\DWM");
            if (k?.GetValue("AccentColor") is int abgr)
            {
                byte r = (byte)(abgr & 0xFF), g = (byte)((abgr >> 8) & 0xFF), b = (byte)((abgr >> 16) & 0xFF);
                return Color.FromRgb(r, g, b);
            }
        }
        catch { }
        return Color.FromRgb(0x4C, 0x7E, 0xFF);
    }

    /// <summary>Returns true if the Mica backdrop was accepted by DWM.</summary>
    public static bool Apply(Window w, bool dark)
    {
        var hwnd = new WindowInteropHelper(w).Handle;
        if (hwnd == IntPtr.Zero) return false;

        int d = dark ? 1 : 0;
        DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ref d, sizeof(int));

        int corner = DWMWCP_ROUND;
        DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ref corner, sizeof(int));

        int backdrop = DWMSBT_MAINWINDOW;
        int hr = DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ref backdrop, sizeof(int));
        return hr == 0;
    }
}
