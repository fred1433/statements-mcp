using System.ComponentModel;
using System.Text.Json.Nodes;
using ModelContextProtocol;
using ModelContextProtocol.Server;

namespace FinancialStatements.Mcp;

/// <summary>Five read tools. No write tool, no path argument, no free-form query.</summary>
[McpServerToolType]
public sealed class StatementTools(StoreProvider provider)
{
    static string Run(Func<JsonObject> query)
    {
        try { return query().ToJsonString(Json.Indented); }
        catch (QueryException e) { throw new McpException(e.ToJson().ToJsonString(Json.Indented)); }
    }

    [McpServerTool(Name = "list_snapshots", ReadOnly = true, Destructive = false, Idempotent = true, OpenWorld = false),
     Description("The approved exports: company, reporting units, rows, columns, and each month-end snapshot with its status (usable or blocked, with the failed checks). Call this first.")]
    public string ListSnapshots() => Run(() => provider.Statements.ListSnapshots());

    [McpServerTool(Name = "get_values", ReadOnly = true, Destructive = false, Idempotent = true, OpenWorld = false),
     Description("Printed values for some rows and columns of one snapshot and one reporting unit (omit unit for the company total). Each value comes with its cell reference.")]
    public string GetValues(
        [Description("Snapshot period, YYYY-MM")] string period,
        [Description("Row names, exactly as listed by list_snapshots")] string[] rows,
        [Description("Column names, exactly as listed by list_snapshots")] string[] columns,
        [Description("Reporting unit; omit for the company total")] string? unit = null)
        => Run(() => provider.Statements.GetValues(period, unit, rows, columns));

    [McpServerTool(Name = "compare_periods", ReadOnly = true, Destructive = false, Idempotent = true, OpenWorld = false),
     Description("The same column, unit and rows in two snapshots, with the change (dollars, or points for rates), its formula and the cells it was computed from. Omit rows to compare every row.")]
    public string ComparePeriods(
        [Description("Earlier snapshot, YYYY-MM")] string period_from,
        [Description("Later snapshot, YYYY-MM")] string period_to,
        [Description("Column name")] string column,
        [Description("Reporting unit; omit for the company total")] string? unit = null,
        [Description("Row names; omit for all rows")] string[]? rows = null)
        => Run(() => provider.Statements.ComparePeriods(period_from, period_to, column, unit, rows));

    [McpServerTool(Name = "margin_bridge", ReadOnly = true, Destructive = false, Idempotent = true, OpenWorld = false),
     Description("Explains a change in the company's gross margin rate between two snapshots as a rate effect (each unit's own margin moved) and a mix effect (units' shares of net sales moved), with formulas and input cells.")]
    public string MarginBridge(
        [Description("Earlier snapshot, YYYY-MM")] string period_from,
        [Description("Later snapshot, YYYY-MM")] string period_to,
        [Description("Column name, for example Quarter to Date Actual")] string column)
        => Run(() => provider.Statements.MarginBridge(period_from, period_to, column, null));

    [McpServerTool(Name = "get_report_comments", ReadOnly = true, Destructive = false, Idempotent = true, OpenWorld = false),
     Description("Comments written into an approved report by its author, with their cell references. Commentary, not figures, and not verified.")]
    public string GetReportComments([Description("Snapshot period, YYYY-MM")] string period)
        => Run(() => provider.Statements.GetCommentary(period));
}

public static class Json
{
    public static readonly System.Text.Json.JsonSerializerOptions Indented = new() { WriteIndented = true };
}

/// <summary>Reloads the folder when a file or approved.json changes, so a newly approved export is picked up without a restart.</summary>
public sealed class StoreProvider(string folder)
{
    readonly object gate = new();
    string signature = "";
    Statements? statements;
    public string Folder => folder;

    public Statements Statements
    {
        get
        {
            lock (gate)
            {
                var sig = string.Join("|", Directory.EnumerateFiles(folder).Order().Select(f => $"{Path.GetFileName(f)}:{File.GetLastWriteTimeUtc(f).Ticks}:{new FileInfo(f).Length}"));
                if (statements is null || sig != signature) { statements = new Statements(SnapshotStore.Load(folder)); signature = sig; }
                return statements;
            }
        }
    }
}
