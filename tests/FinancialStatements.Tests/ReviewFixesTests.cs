using System.Text.Json.Nodes;
using ClosedXML.Excel;
using FinancialStatements.Mcp;

namespace FinancialStatements.Tests;

// Cases raised by the independent review of the finished demo: scale, fiscal calendar, re-approved exports,
// blank printed rates, and arithmetic done by a tool instead of by Claude.
public class ReviewFixesTests
{
    const string Q = "Quarter to Date Actual";

    static string Folder(Action<string, JsonNode>? change = null)
    {
        var dir = Paths.CopyOf(Paths.Samples);
        var m = JsonNode.Parse(File.ReadAllText(Path.Combine(dir, "approved.json")))!;
        change?.Invoke(dir, m);
        foreach (var s in m["snapshots"]!.AsArray())
        {
            var f = Path.Combine(dir, s!["file"]!.GetValue<string>());
            if (File.Exists(f)) s["sha256"] = SnapshotStore.Sha256Of(f);
        }
        File.WriteAllText(Path.Combine(dir, "approved.json"), m.ToJsonString());
        return dir;
    }

    static void EditJune(string dir, Action<IXLWorksheet> edit)
    {
        var path = Path.Combine(dir, "IS_2026-06.xlsx");
        using (var wb = new XLWorkbook(path)) { foreach (var ws in wb.Worksheets) edit(ws); wb.Save(); }
    }

    static SnapshotStore.Snapshot June(string dir) => SnapshotStore.Load(dir).Snapshots.Single(s => s.Entry.File == "IS_2026-06.xlsx");

    [Fact]
    public void An_export_stated_in_thousands_is_blocked_when_the_profile_expects_dollars()
    {
        var dir = Folder((d, _) => EditJune(d, ws => ws.Cell("A5").Value = "Amounts in Thousands of US Dollars"));
        var s = June(dir);
        Assert.False(s.Usable);
        Assert.Contains(s.Failures, f => f.Contains("thousands") && f.Contains("expects units"));
    }

    [Fact]
    public void An_export_in_thousands_is_served_with_its_scale_when_the_profile_says_thousands()
    {
        var dir = Folder((d, m) => { EditJune(d, ws => ws.Cell("A5").Value = "Amounts in Thousands of US Dollars"); m["scale"] = "thousands"; });
        var st = new Statements(SnapshotStore.Load(dir));
        var r = st.GetValues("2026-06", null, new[] { "Net sales" }, new[] { Q });
        Assert.Equal("thousands", r["scale"]!.GetValue<string>());
        Assert.Equal("thousands", r["values"]![0]!["scale"]!.GetValue<string>());
        Assert.Equal("$6,748,503 thousand", r["values"]![0]!["display"]!.GetValue<string>());
    }

    [Fact]
    public void An_unrecognized_or_missing_amounts_line_blocks()
    {
        var dir = Folder((d, _) => EditJune(d, ws => ws.Cell("A5").Value = "Amounts in Euros"));
        Assert.Contains(June(dir).Failures, f => f.Contains("does not state a recognized currency and scale"));
        var dir2 = Folder((d, _) => EditJune(d, ws => ws.Cell("A5").Clear()));
        Assert.False(June(dir2).Usable);
    }

    [Fact]
    public void A_blank_printed_rate_blocks()
    {
        var dir = Folder((d, _) => EditJune(d, ws => { if (ws.Name == "Online") ws.Cell("C21").Clear(); }));
        Assert.Contains(June(dir).Failures, f => f.Contains("\"Gross margin %\" / Quarter to Date Actual is blank"));
    }

    [Fact]
    public void Without_a_declared_fiscal_year_no_start_date_is_written()
    {
        var dir = Folder((_, m) => m.AsObject().Remove("fiscal_year_start_month"));
        var r = new Statements(SnapshotStore.Load(dir)).GetValues("2026-06", null, new[] { "Net sales" }, new[] { Q });
        var scope = r["column_scope"]![Q]!.GetValue<string>();
        Assert.Contains("start date not declared", scope);
        Assert.DoesNotContain("April", scope);
    }

    [Fact]
    public void Fiscal_boundaries_follow_the_declared_start_month()
    {
        var jan = new Statements(SnapshotStore.Load(Paths.Samples)).GetValues("2026-06", null, new[] { "Net sales" }, new[] { Q, "Year to Date Actual" });
        Assert.StartsWith("April 1, 2026 to June 30, 2026 (the full fiscal quarter 2)", jan["column_scope"]![Q]!.GetValue<string>());
        var dir = Folder((_, m) => m["fiscal_year_start_month"] = 7);
        var jul = new Statements(SnapshotStore.Load(dir)).GetValues("2026-06", null, new[] { "Net sales" }, new[] { Q });
        Assert.StartsWith("April 1, 2026 to June 30, 2026 (the full fiscal quarter 4)", jul["column_scope"]![Q]!.GetValue<string>());
        Assert.StartsWith("July 1, 2025 to June 30, 2026", jul["column_scope"]!["Year to Date Actual"]!.GetValue<string>());
    }

    [Fact]
    public void A_corrected_export_for_the_same_period_is_served_and_the_original_is_shown_as_replaced()
    {
        var dir = Folder((d, m) =>
        {
            File.Copy(Path.Combine(d, "IS_2026-06.xlsx"), Path.Combine(d, "IS_2026-08-corrected.xlsx"));
            using (var wb = new XLWorkbook(Path.Combine(d, "IS_2026-08-corrected.xlsx")))
            { foreach (var ws in wb.Worksheets) ws.Cell("A3").Value = "For the Period Ending August 31, 2026"; wb.Save(); }
            var june = m["snapshots"]![1]!;
            var corrected = june.DeepClone();
            corrected["file"] = "IS_2026-08-corrected.xlsx"; corrected["period_end"] = "2026-08-31"; corrected["approved_on"] = "2026-09-20";
            m["snapshots"]!.AsArray().Add(corrected);
        });
        var store = SnapshotStore.Load(dir);
        var original = store.Snapshots.Single(s => s.Entry.File == "IS_2026-08.xlsx");
        Assert.Equal("IS_2026-08-corrected.xlsx", original.ReplacedBy);
        var st = new Statements(store);
        Assert.Equal("IS_2026-08-corrected.xlsx", st.GetValues("2026-08", null, new[] { "Net sales" }, new[] { Q })["snapshot"]!.GetValue<string>());
        var list = st.ListSnapshots()["snapshots"]!.AsArray();
        Assert.Contains(list, s => s!["file"]!.GetValue<string>() == "IS_2026-08.xlsx" && s["status"]!.GetValue<string>().StartsWith("replaced by IS_2026-08-corrected.xlsx"));
    }

    // ---------- calculate ----------
    static readonly Statements St = new(SnapshotStore.Load(Paths.Samples));
    static CellSpec C(string period, string row, string? unit = null, string column = Q) => new(period, row, column, unit);

    [Fact]
    public void Share_sum_difference_and_relative_change_carry_formula_and_inputs()
    {
        var share = St.Calculate("share", new[] { C("2026-06", "Gross profit", "Service"), C("2026-06", "Gross profit") });
        Assert.Equal("10.9%", share["display"]!.GetValue<string>());
        Assert.Equal("part / whole", share["formula"]!.GetValue<string>());
        Assert.Equal(new[] { "[IS_2026-06.xlsx]Service!C20", "[IS_2026-06.xlsx]Total!C20" }, share["inputs"]!.AsArray().Select(x => x!.GetValue<string>()));

        var sum = St.Calculate("sum", new[] { C("2026-03", "Net sales"), C("2026-06", "Net sales") });
        Assert.Equal("$12,769,721", sum["display"]!.GetValue<string>());

        var rel = St.Calculate("relative_change", new[] { C("2026-03", "Gross margin %"), C("2026-06", "Gross margin %") });
        Assert.Equal("-1.6%", rel["display"]!.GetValue<string>());
        Assert.Contains("-0.46 pts", rel["note"]!.GetValue<string>());

        var diff = St.Calculate("difference", new[] { C("2026-03", "Gross profit"), C("2026-06", "Gross profit") });
        Assert.Equal("+$173,400", diff["display"]!.GetValue<string>());
    }

    [Fact]
    public void Calculate_refuses_double_counting_rate_sums_and_blocked_inputs()
    {
        Assert.Equal("double_count", Assert.Throws<QueryException>(() => St.Calculate("sum", new[] { C("2026-06", "Net sales"), C("2026-06", "Net sales", "Wholesale") })).Code);
        Assert.Equal("rate_sum", Assert.Throws<QueryException>(() => St.Calculate("sum", new[] { C("2026-06", "Gross margin %"), C("2026-03", "Gross margin %") })).Code);
        Assert.Equal("snapshot_blocked", Assert.Throws<QueryException>(() => St.Calculate("sum", new[] { C("2026-08", "Net sales"), C("2026-06", "Net sales") })).Code);
        Assert.Equal("unknown_operation", Assert.Throws<QueryException>(() => St.Calculate("average", new[] { C("2026-06", "Net sales") })).Code);
    }
}
