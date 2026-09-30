using System.Security.Cryptography;
using static FinancialStatements.Mcp.ExportReader;

namespace FinancialStatements.Mcp;

/// <summary>
/// Loads the approved exports of one folder and decides, for each, whether it may be used.
/// A snapshot is usable only if every check passes: file listed in approved.json, SHA-256 unchanged
/// since approval, header block readable, every reporting unit of the tree present, printed totals
/// tie to their lines and units within the rounding policy, printed rates match their inputs, and the
/// control totals read off the rendered report match the export to the dollar.
/// A blocked snapshot is never read for figures, and never replaced by another one silently.
/// </summary>
public sealed class SnapshotStore
{
    public sealed class Snapshot
    {
        public required SnapshotEntry Entry { get; init; }
        public required string Id { get; init; }            // "2026-06"
        public required DateOnly PeriodEnd { get; init; }
        public string ActualSha256 { get; set; } = "";
        public List<string> Failures { get; } = new();
        public List<string> RoundingNotes { get; } = new();
        public Dictionary<string, SheetRead> SheetsByUnit { get; } = new(StringComparer.OrdinalIgnoreCase);
        public DateTimeOffset ImportedAt { get; set; }
        public string Scale { get; set; } = "";
        public string Currency { get; set; } = "";
        public int Order { get; init; }
        /// <summary>Another approved export for the same period that is served instead of this one.</summary>
        public string? ReplacedBy { get; set; }
        public bool Usable => Failures.Count == 0;
        public bool Served => ReplacedBy is null;
    }

    public string Folder { get; }
    public Manifest Manifest { get; }
    public List<Snapshot> Snapshots { get; } = new();
    public List<string> UnlistedFiles { get; } = new();
    public Dictionary<string, RowKind> RowKinds { get; } = new(StringComparer.OrdinalIgnoreCase);
    public List<string> RowOrder { get; } = new();
    public List<string> Columns { get; } = new();

    public IEnumerable<string> AllUnits => new[] { Manifest.ReportingTree.Root }.Concat(Manifest.ReportingTree.Units);

    SnapshotStore(string folder, Manifest manifest) { Folder = folder; Manifest = manifest; }

    public static SnapshotStore Load(string folder)
    {
        folder = Path.GetFullPath(folder);
        var manifestPath = Path.Combine(folder, "approved.json");
        if (!File.Exists(manifestPath)) throw new FileNotFoundException($"No approved.json in {folder}. Nothing will be read without it.");
        var store = new SnapshotStore(folder, Manifest.Load(manifestPath));
        var listed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int order = 0;
        foreach (var entry in store.Manifest.Snapshots)
        {
            listed.Add(entry.File);
            store.Snapshots.Add(store.LoadOne(entry, order++));
        }
        foreach (var f in Directory.EnumerateFiles(folder, "*.xlsx").Select(Path.GetFileName))
            if (f is not null && !listed.Contains(f) && !f.StartsWith("~$")) store.UnlistedFiles.Add(f);
        var sorted = store.Snapshots.OrderBy(x => x.PeriodEnd).ThenBy(x => x.Order).ToList();
        store.Snapshots.Clear();
        store.Snapshots.AddRange(sorted);
        // Several approved exports for one period (a re-export after a correction): serve the most recently
        // approved one that passes its checks; if none passes, the most recently approved one, blocked.
        foreach (var group in store.Snapshots.GroupBy(x => x.Id).Where(g => g.Count() > 1))
        {
            var byApproval = group.OrderByDescending(x => x.Entry.ApprovedOn, StringComparer.Ordinal).ThenByDescending(x => x.Order).ToList();
            var served = byApproval.FirstOrDefault(x => x.Usable) ?? byApproval[0];
            foreach (var other in group) if (other != served) other.ReplacedBy = served.Entry.File;
        }
        return store;
    }

    public static string Sha256Of(string path)
    {
        using var s = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(s)).ToLowerInvariant();
    }

    Snapshot LoadOne(SnapshotEntry entry, int order)
    {
        var periodEnd = DateOnly.TryParse(entry.PeriodEnd, System.Globalization.CultureInfo.InvariantCulture, out var pe) ? pe : default;
        var snap = new Snapshot { Entry = entry, Id = periodEnd.ToString("yyyy-MM"), PeriodEnd = periodEnd, ImportedAt = DateTimeOffset.Now, Order = order };
        var f = snap.Failures;

        // Access boundary: a manifest entry is a bare file name inside this folder, nothing else.
        if (entry.File.IndexOfAny(new[] { '/', '\\', ':' }) >= 0 || entry.File.Contains("..") || !entry.File.EndsWith(".xlsx", StringComparison.OrdinalIgnoreCase))
        { f.Add($"\"{entry.File}\" is not a plain .xlsx file name in the approved folder; it was not opened"); return snap; }
        var path = Path.Combine(Folder, entry.File);
        if (Path.GetDirectoryName(Path.GetFullPath(path)) != Folder) { f.Add($"\"{entry.File}\" resolves outside the approved folder; it was not opened"); return snap; }
        if (!File.Exists(path)) { f.Add($"{entry.File} is listed as approved but is not in the folder"); return snap; }

        snap.ActualSha256 = Sha256Of(path);
        if (!snap.ActualSha256.Equals(entry.Sha256, StringComparison.OrdinalIgnoreCase))
        { f.Add($"{entry.File} has changed since it was approved (SHA-256 {Short(snap.ActualSha256)} on disk, {Short(entry.Sha256)} approved)"); return snap; }

        WorkbookRead wb;
        try { wb = ExportReader.Read(path); }
        catch (Exception e) { f.Add($"{entry.File} could not be read as a workbook: {e.Message}"); return snap; }
        f.AddRange(wb.Problems.Select(p => $"{entry.File}, {p}"));

        foreach (var s in wb.Sheets)
        {
            if (!s.Company.Equals(Manifest.Company, StringComparison.OrdinalIgnoreCase)) f.Add($"{entry.File}, sheet \"{s.Name}\": company is \"{s.Company}\", expected \"{Manifest.Company}\"");
            if (!s.Report.Equals(Manifest.Report, StringComparison.OrdinalIgnoreCase)) f.Add($"{entry.File}, sheet \"{s.Name}\": report is \"{s.Report}\", expected \"{Manifest.Report}\"");
            if (s.PeriodEnd != periodEnd) f.Add($"{entry.File}, sheet \"{s.Name}\": period ends {s.PeriodEnd:yyyy-MM-dd}, approved as {entry.PeriodEnd}");
            var amounts = ExportReader.ParseAmounts(s.AmountsLine);
            if (amounts is null)
                f.Add($"{entry.File}, sheet \"{s.Name}\": the amounts line \"{s.AmountsLine}\" (row {Layout.AmountsRow}) does not state a recognized currency and scale");
            else
            {
                if (!amounts.Value.Scale.Equals(Manifest.Scale, StringComparison.OrdinalIgnoreCase) || !amounts.Value.Currency.Equals(Manifest.Currency, StringComparison.OrdinalIgnoreCase))
                    f.Add($"{entry.File}, sheet \"{s.Name}\": the export states \"{s.AmountsLine}\" ({amounts.Value.Scale}, {amounts.Value.Currency}) but the approved profile expects {Manifest.Scale}, {Manifest.Currency}");
                if (snap.Scale != "" && snap.Scale != amounts.Value.Scale)
                    f.Add($"{entry.File}: sheets state different scales");
                snap.Scale = amounts.Value.Scale; snap.Currency = amounts.Value.Currency;
            }
            if (!AllUnits.Contains(s.Unit, StringComparer.OrdinalIgnoreCase)) f.Add($"{entry.File}, sheet \"{s.Name}\": reporting unit \"{s.Unit}\" is not in the reporting tree");
            else snap.SheetsByUnit[s.Unit] = s;
            foreach (var cell in s.Cells.Values.Where(c => c.Problem is not null)) f.Add($"{entry.File}, sheet \"{s.Name}\": {cell.Problem}");
            foreach (var row in s.Rows)
            {
                if (!RowOrder.Contains(row.Label, StringComparer.OrdinalIgnoreCase)) RowOrder.Add(row.Label);
                if (!RowKinds.TryGetValue(row.Label, out var k) || k == RowKind.Section) RowKinds[row.Label] = row.Kind;
            }
            foreach (var c in s.Columns) if (!Columns.Contains(c)) Columns.Add(c);
        }
        foreach (var unit in AllUnits)
            if (!snap.SheetsByUnit.ContainsKey(unit)) f.Add($"{entry.File}: reporting unit \"{unit}\" is missing from the export");
        if (f.Count > 0) return snap;

        CheckTies(snap);
        CheckUnitsAddUp(snap);
        CheckRates(snap);
        CheckControlTotals(snap);
        return snap;
    }

    public static string Short(string sha) => sha.Length >= 12 ? sha[..12] : sha;

    static double? V(SheetRead s, string row, string col) => s.Cells.TryGetValue((row, col), out var c) ? c.Value : null;

    int Tolerance(int cells) => Manifest.RoundingPolicy.MaxDifferencePerSummedCell * cells;

    void CheckTies(Snapshot snap)
    {
        foreach (var s in snap.SheetsByUnit.Values)
            foreach (var rule in Manifest.RowDefinition.Totals)
                foreach (var col in s.Columns)
                {
                    var printed = V(s, rule.Total, col);
                    if (printed is null) { snap.Failures.Add($"{snap.Entry.File}, sheet \"{s.Name}\": \"{rule.Total}\" / {col} is blank"); continue; }
                    double sum = rule.Plus.Sum(r => V(s, r, col) ?? 0) - rule.Minus.Sum(r => V(s, r, col) ?? 0);
                    double diff = printed.Value - sum;
                    int n = rule.Plus.Count + rule.Minus.Count;
                    if (Math.Abs(diff) > Tolerance(n))
                        snap.Failures.Add($"{snap.Entry.File}, sheet \"{s.Name}\": \"{rule.Total}\" / {col} is {Format.Amount(printed.Value)} but its lines add up to {Format.Amount(sum)} ({Format.Change(diff)}, more than the ${Tolerance(n)} rounding allowance)");
                    else if (Math.Abs(diff) >= 0.5)
                        snap.RoundingNotes.Add($"sheet \"{s.Name}\", \"{rule.Total}\" / {col}: printed total differs from its printed lines by {Format.Change(diff)} (rounding)");
                }
    }

    void CheckUnitsAddUp(Snapshot snap)
    {
        var root = snap.SheetsByUnit[Manifest.ReportingTree.Root];
        foreach (var row in root.Rows.Where(r => RowKinds.GetValueOrDefault(r.Label) == RowKind.Amount))
            foreach (var col in root.Columns)
            {
                var total = V(root, row.Label, col);
                if (total is null) continue;
                double sum = Manifest.ReportingTree.Units.Sum(u => V(snap.SheetsByUnit[u], row.Label, col) ?? 0);
                double diff = total.Value - sum;
                if (Math.Abs(diff) > Tolerance(Manifest.ReportingTree.Units.Count))
                    snap.Failures.Add($"{snap.Entry.File}: \"{row.Label}\" / {col} for the company is {Format.Amount(total.Value)} but the reporting units add up to {Format.Amount(sum)}");
                else if (Math.Abs(diff) >= 0.5)
                    snap.RoundingNotes.Add($"\"{row.Label}\" / {col}: company total differs from the sum of units by {Format.Change(diff)} (rounding)");
            }
    }

    void CheckRates(Snapshot snap)
    {
        foreach (var s in snap.SheetsByUnit.Values)
            foreach (var rule in Manifest.RowDefinition.Rates)
                foreach (var col in s.Columns)
                {
                    var printed = V(s, rule.Row, col);
                    var num = V(s, rule.Numerator, col);
                    var den = V(s, rule.Denominator, col);
                    if (num is null || den is null || den == 0) continue;
                    if (printed is null)
                    {
                        snap.Failures.Add($"{snap.Entry.File}, sheet \"{s.Name}\": \"{rule.Row}\" / {col} is blank although {rule.Numerator} and {rule.Denominator} are printed");
                        continue;
                    }
                    // Printed rates come from unrounded amounts; allow for the dollar rounding of the inputs.
                    double tol = 0.0005 + 1.0 / Math.Abs(den.Value);
                    if (Math.Abs(printed.Value - num.Value / den.Value) > tol)
                        snap.Failures.Add($"{snap.Entry.File}, sheet \"{s.Name}\": \"{rule.Row}\" / {col} is printed as {Format.Percent(printed.Value)} but {rule.Numerator} / {rule.Denominator} gives {Format.Percent(num.Value / den.Value)}");
                }
    }

    void CheckControlTotals(Snapshot snap)
    {
        foreach (var ct in snap.Entry.ControlTotals)
        {
            if (!snap.SheetsByUnit.TryGetValue(ct.Unit, out var s)) { snap.Failures.Add($"control total for \"{ct.Unit}\" cannot be checked: unit not in the export"); continue; }
            var v = V(s, ct.Row, ct.Column);
            if (v is null || Math.Round(v.Value) != ct.Value)
                snap.Failures.Add($"{snap.Entry.File}: \"{ct.Row}\" / {ct.Column} for {ct.Unit} is {(v is null ? "blank" : Format.Amount(v.Value))} in the export but {Format.Amount(ct.Value)} on the approved report (control total)");
        }
    }
}
