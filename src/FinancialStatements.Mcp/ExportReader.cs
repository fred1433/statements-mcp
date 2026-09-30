using System.Globalization;
using System.Text.RegularExpressions;
using ClosedXML.Excel;

namespace FinancialStatements.Mcp;

/// <summary>
/// The import adapter: the only code that knows where things sit in an exported workbook. If a real
/// export differs (header rows elsewhere, row codes in a visible column, a whole tree on one sheet),
/// the change belongs here and nowhere else.
///
/// It reads the values stored in the cells (what Excel shows when the file is opened), never
/// recalculates a formula, and reports a formula that has no stored value instead of guessing one.
/// The layout is modeled on documented Management Reporter concepts; it has not been validated
/// against a real export.
/// </summary>
public static class ExportReader
{
    public static class Layout
    {
        public const int CompanyRow = 1;
        public const int ReportRow = 2;
        public const int PeriodRow = 3;   // "For the Period Ending June 30, 2026"
        public const int UnitRow = 4;     // "Reporting Unit: Wholesale"
        public const int AmountsRow = 5;  // "Amounts in US Dollars" / "Amounts in Thousands of US Dollars"
        public const int LabelColumn = 1;
        public const int MaxHeaderScan = 12;
        public const string CommentsHeading = "Report comments";
    }

    public enum RowKind { Section, Amount, Percent }

    public sealed record CellRead(string Address, double? Value, bool IsPercent, string? Problem);

    public sealed record RowRead(string Label, RowKind Kind, int RowNumber);

    public sealed record CommentRead(string Text, string Address);

    public sealed class SheetRead
    {
        public required string Name { get; init; }
        public required string Company { get; init; }
        public required string Report { get; init; }
        public required DateOnly PeriodEnd { get; init; }
        public required string Unit { get; init; }
        public required string AmountsLine { get; init; }
        public List<string> Columns { get; } = new();
        public List<RowRead> Rows { get; } = new();
        public Dictionary<(string Row, string Column), CellRead> Cells { get; } = new();
        public List<CommentRead> Comments { get; } = new();
    }

    public sealed record WorkbookRead(List<SheetRead> Sheets, List<string> Problems);

    static readonly Regex PeriodRe = new(@"ending\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", RegexOptions.IgnoreCase);

    public static DateOnly? ParsePeriod(string text)
    {
        var m = PeriodRe.Match(text);
        if (!m.Success) return null;
        return DateOnly.TryParseExact($"{m.Groups[1].Value} {m.Groups[2].Value} {m.Groups[3].Value}", "MMMM d yyyy",
            CultureInfo.InvariantCulture, DateTimeStyles.None, out var d) ? d : null;
    }

    static string Text(IXLCell c)
    {
        var v = c.HasFormula ? c.CachedValue : c.Value;
        return v.IsBlank ? "" : v.ToString(CultureInfo.InvariantCulture).Trim();
    }

    /// <summary>
    /// The currency and scale a sheet states in its header ("Amounts in Thousands of US Dollars").
    /// Null when the line is missing or not one of the forms below: the snapshot is then blocked.
    /// </summary>
    public static (string Currency, string Scale)? ParseAmounts(string line)
    {
        var m = Regex.Match(line.Trim(), @"^Amounts in (?:(Thousands|Millions) of )?(US Dollars|USD)$", RegexOptions.IgnoreCase);
        if (!m.Success) return null;
        string scale = m.Groups[1].Success ? m.Groups[1].Value.ToLowerInvariant() : "units";
        return ("USD", scale);
    }

    public static WorkbookRead Read(string path)
    {
        var problems = new List<string>();
        var sheets = new List<SheetRead>();
        using var wb = new XLWorkbook(path);
        foreach (var ws in wb.Worksheets)
        {
            string company = Text(ws.Cell(Layout.CompanyRow, Layout.LabelColumn));
            string report = Text(ws.Cell(Layout.ReportRow, Layout.LabelColumn));
            var period = ParsePeriod(Text(ws.Cell(Layout.PeriodRow, Layout.LabelColumn)));
            string unitText = Text(ws.Cell(Layout.UnitRow, Layout.LabelColumn));
            string unit = Regex.Replace(unitText, @"^reporting unit:\s*", "", RegexOptions.IgnoreCase);
            if (company == "" || report == "" || period is null || !unitText.StartsWith("Reporting Unit:", StringComparison.OrdinalIgnoreCase))
            {
                problems.Add($"sheet \"{ws.Name}\": the header block (company, report, period, reporting unit in rows 1 to 4) was not found where the export profile expects it");
                continue;
            }

            string amounts = Text(ws.Cell(Layout.AmountsRow, Layout.LabelColumn));
            var sheet = new SheetRead { Name = ws.Name, Company = company, Report = report, PeriodEnd = period.Value, Unit = unit, AmountsLine = amounts };
            int headerRow = 0;
            var colIndex = new List<(int Index, string Name)>();
            for (int r = Layout.AmountsRow + 1; r <= Layout.MaxHeaderScan && headerRow == 0; r++)
            {
                var found = new List<(int, string)>();
                int last = ws.Row(r).LastCellUsed()?.Address.ColumnNumber ?? 0;
                for (int c = Layout.LabelColumn + 1; c <= last; c++)
                {
                    var cell = ws.Cell(r, c);
                    var v = cell.HasFormula ? cell.CachedValue : cell.Value;
                    if (v.IsText && v.GetText().Trim() != "") found.Add((c, v.GetText().Trim()));
                }
                if (found.Count >= 2) { headerRow = r; colIndex.AddRange(found); }
            }
            if (headerRow == 0) { problems.Add($"sheet \"{ws.Name}\": no column header row"); continue; }
            foreach (var dup in colIndex.GroupBy(c => c.Name, StringComparer.OrdinalIgnoreCase).Where(g => g.Count() > 1))
                problems.Add($"sheet \"{ws.Name}\": column \"{dup.Key}\" appears twice; ambiguous, nothing is chosen");
            sheet.Columns.AddRange(colIndex.Select(c => c.Name));

            int lastRow = ws.LastRowUsed()?.RowNumber() ?? 0;
            bool inComments = false;
            for (int r = headerRow + 1; r <= lastRow; r++)
            {
                string label = Text(ws.Cell(r, Layout.LabelColumn));
                if (label == "") continue;
                if (label.Equals(Layout.CommentsHeading, StringComparison.OrdinalIgnoreCase)) { inComments = true; continue; }
                if (inComments) { sheet.Comments.Add(new CommentRead(label, ws.Cell(r, Layout.LabelColumn).Address.ToString()!)); continue; }

                var reads = new List<(string Column, CellRead Read)>();
                foreach (var (index, name) in colIndex)
                {
                    var cell = ws.Cell(r, index);
                    string address = cell.Address.ToString()!;
                    bool pct = cell.Style.NumberFormat.Format.Contains('%');
                    if (cell.HasFormula && cell.CachedValue.IsBlank)
                        reads.Add((name, new CellRead(address, null, pct, $"cell {address} holds a formula with no stored value; the connector does not recalculate formulas")));
                    else
                    {
                        var v = cell.HasFormula ? cell.CachedValue : cell.Value;
                        if (v.IsNumber) reads.Add((name, new CellRead(address, v.GetNumber(), pct, null)));
                        else if (v.IsBlank) reads.Add((name, new CellRead(address, null, pct, null)));
                        else reads.Add((name, new CellRead(address, null, pct, $"cell {address} holds text where a number is expected")));
                    }
                }
                var kind = reads.All(x => x.Read.Value is null && x.Read.Problem is null) ? RowKind.Section
                    : reads.Where(x => x.Read.Value is not null).All(x => x.Read.IsPercent) && reads.Any(x => x.Read.Value is not null) ? RowKind.Percent
                    : RowKind.Amount;
                // A row whose cells are all blank is a section heading unless the row definition says otherwise;
                // the snapshot checks decide whether a blank amount row is acceptable.
                if (sheet.Rows.Any(x => x.Label.Equals(label, StringComparison.OrdinalIgnoreCase)))
                    problems.Add($"sheet \"{ws.Name}\": row \"{label}\" appears twice (rows {sheet.Rows.First(x => x.Label.Equals(label, StringComparison.OrdinalIgnoreCase)).RowNumber} and {r}); ambiguous, nothing is chosen");
                sheet.Rows.Add(new RowRead(label, kind, r));
                foreach (var (col, read) in reads) sheet.Cells[(label, col)] = read;
            }
            sheets.Add(sheet);
        }
        return new WorkbookRead(sheets, problems);
    }
}
