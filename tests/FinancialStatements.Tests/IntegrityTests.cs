using System.Text.Json;
using System.Text.Json.Nodes;
using FinancialStatements.Mcp;

namespace FinancialStatements.Tests;

// Export integrity: which snapshots may be used, and why the others are blocked.
public class IntegrityTests
{
    static readonly SnapshotStore Store = SnapshotStore.Load(Paths.Samples);

    [Fact]
    public void Quarter_exports_are_usable_with_rounding_differences_reported()
    {
        var mar = Store.Snapshots.Single(s => s.Id == "2026-03");
        var jun = Store.Snapshots.Single(s => s.Id == "2026-06");
        Assert.True(mar.Usable, string.Join("\n", mar.Failures));
        Assert.True(jun.Usable, string.Join("\n", jun.Failures));
        Assert.NotEmpty(jun.RoundingNotes); // lines and totals are rounded separately in the export
    }

    [Fact]
    public void August_is_blocked_for_a_missing_unit()
    {
        var aug = Store.Snapshots.Single(s => s.Id == "2026-08");
        Assert.False(aug.Usable);
        Assert.Contains(aug.Failures, f => f.Contains("reporting unit \"Service\" is missing"));
    }

    [Fact]
    public void A_control_total_mismatch_blocks_even_when_every_unit_is_present()
    {
        var dir = Paths.CopyOf(Paths.Samples);
        var m = JsonNode.Parse(File.ReadAllText(Path.Combine(dir, "approved.json")))!;
        m["snapshots"]![1]!["control_totals"]![0]!["value"] = 6_748_000;
        File.WriteAllText(Path.Combine(dir, "approved.json"), m.ToJsonString());
        var jun = SnapshotStore.Load(dir).Snapshots.Single(s => s.Id == "2026-06");
        Assert.False(jun.Usable);
        Assert.Contains(jun.Failures, f => f.Contains("control total") && f.Contains("$6,748,000"));
    }

    [Fact]
    public void A_file_changed_after_approval_is_blocked()
    {
        var dir = Paths.CopyOf(Paths.Samples);
        File.Copy(Path.Combine(Paths.Fixtures, "rounding_only.xlsx"), Path.Combine(dir, "IS_2026-03.xlsx"), overwrite: true);
        var mar = SnapshotStore.Load(dir).Snapshots.Single(s => s.Id == "2026-03");
        Assert.False(mar.Usable);
        Assert.Contains("has changed since it was approved", Assert.Single(mar.Failures));
    }

    [Fact]
    public void A_printed_total_that_does_not_tie_beyond_rounding_is_blocked()
    {
        var dir = OneFileFolder("total_off.xlsx", "2026-06-30");
        var s = Assert.Single(SnapshotStore.Load(dir).Snapshots);
        Assert.Contains(s.Failures, f => f.Contains("\"Net sales\" / Quarter to Date Actual") && f.Contains("rounding allowance"));
    }

    [Fact]
    public void A_formula_without_value_or_a_moved_header_blocks_the_snapshot()
    {
        Assert.Contains(SnapshotStore.Load(OneFileFolder("formula_no_value.xlsx", "2026-06-30")).Snapshots[0].Failures, f => f.Contains("no stored value"));
        Assert.Contains(SnapshotStore.Load(OneFileFolder("header_moved.xlsx", "2026-06-30")).Snapshots[0].Failures, f => f.Contains("header block"));
    }

    [Fact]
    public void Files_not_listed_in_approved_json_are_never_opened()
    {
        var dir = Paths.CopyOf(Paths.Samples);
        File.Copy(Path.Combine(Paths.Fixtures, "total_off.xlsx"), Path.Combine(dir, "IS_2026-07.xlsx"));
        var store = SnapshotStore.Load(dir);
        Assert.Equal(new[] { "IS_2026-07.xlsx" }, store.UnlistedFiles);
        Assert.DoesNotContain(store.Snapshots, s => s.Entry.File == "IS_2026-07.xlsx");
    }

    [Theory]
    [InlineData("../IS_2026-06.xlsx")]
    [InlineData("/etc/passwd")]
    [InlineData("sub/IS_2026-06.xlsx")]
    [InlineData("C:\\exports\\IS_2026-06.xlsx")]
    [InlineData("approved.json")]
    public void Manifest_entries_outside_the_folder_are_refused(string file)
    {
        var dir = Paths.CopyOf(Paths.Samples);
        var m = JsonNode.Parse(File.ReadAllText(Path.Combine(dir, "approved.json")))!;
        m["snapshots"]![0]!["file"] = file;
        File.WriteAllText(Path.Combine(dir, "approved.json"), m.ToJsonString());
        var s = SnapshotStore.Load(dir).Snapshots.Single(x => x.Entry.File == file);
        Assert.False(s.Usable);
        Assert.Contains("not a plain .xlsx file name", Assert.Single(s.Failures));
    }

    static string OneFileFolder(string fixture, string periodEnd)
    {
        var dir = Directory.CreateTempSubdirectory("fs-test-").FullName;
        File.Copy(Path.Combine(Paths.Fixtures, fixture), Path.Combine(dir, "IS.xlsx"));
        var m = JsonNode.Parse(File.ReadAllText(Path.Combine(Paths.Samples, "approved.json")))!;
        m["snapshots"] = new JsonArray(new JsonObject
        {
            ["file"] = "IS.xlsx", ["period_end"] = periodEnd, ["approved_by"] = "test", ["approved_on"] = "2026-07-08",
            ["sha256"] = SnapshotStore.Sha256Of(Path.Combine(dir, "IS.xlsx")), ["control_totals"] = new JsonArray(),
        });
        File.WriteAllText(Path.Combine(dir, "approved.json"), m.ToJsonString());
        return dir;
    }
}
