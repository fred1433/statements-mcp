using System.Globalization;

namespace FinancialStatements.Mcp;

/// <summary>How numbers are written in tool results. Claude is asked to quote these strings as they are.</summary>
public static class Format
{
    static readonly CultureInfo Us = CultureInfo.GetCultureInfo("en-US");

    public static string Amount(double v)
    {
        var r = Math.Round(v, MidpointRounding.AwayFromZero);
        return (r < 0 ? "-$" : "$") + Math.Abs(r).ToString("#,##0", Us);
    }

    public static string Change(double v)
    {
        var r = Math.Round(v, MidpointRounding.AwayFromZero);
        return (r > 0 ? "+$" : r < 0 ? "-$" : "$") + Math.Abs(r).ToString("#,##0", Us);
    }

    /// <summary>A ratio (0.2764) as a percent with the given decimals ("27.64%").</summary>
    public static string Percent(double ratio, int decimals = 2) =>
        (ratio * 100).ToString("F" + decimals, Us) + "%";

    /// <summary>A difference of two ratios in percentage points ("-0.46 pts").</summary>
    public static string Points(double ratioDiff, int decimals = 2)
    {
        var p = Math.Round(ratioDiff * 100, decimals, MidpointRounding.AwayFromZero);
        return (p > 0 ? "+" : p < 0 ? "-" : "") + Math.Abs(p).ToString("F" + decimals, Us) + " pts";
    }

    /// <summary>A relative change ("+12.1%").</summary>
    public static string Relative(double ratio)
    {
        var p = Math.Round(ratio * 100, 1, MidpointRounding.AwayFromZero);
        return (p > 0 ? "+" : p < 0 ? "-" : "") + Math.Abs(p).ToString("F1", Us) + "%";
    }
}
