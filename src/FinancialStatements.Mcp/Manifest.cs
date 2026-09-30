using System.Text.Json;
using System.Text.Json.Serialization;

namespace FinancialStatements.Mcp;

/// <summary>
/// approved.json, kept by the controller in the exports folder. It lists the exports that may be read,
/// their SHA-256, who approved them, the control totals read off the rendered report, and the agreed
/// export profile (reporting tree, row definition totals, rounding policy). A workbook that is not
/// listed here is never opened.
/// </summary>
public sealed record Manifest(
    string Company,
    string Report,
    ReportingTree ReportingTree,
    string Currency,
    string Scale,
    string Scenario,
    RowDefinition RowDefinition,
    RoundingPolicy RoundingPolicy,
    List<SnapshotEntry> Snapshots)
{
    static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
    };

    public static Manifest Load(string path) =>
        JsonSerializer.Deserialize<Manifest>(File.ReadAllText(path), Options)
        ?? throw new InvalidDataException($"{path} is empty");
}

public sealed record ReportingTree(string Root, List<string> Units);

public sealed record RowDefinition(List<TotalRule> Totals, List<RateRule> Rates);

public sealed record TotalRule(string Total, List<string> Plus, List<string> Minus);

public sealed record RateRule(string Row, string Numerator, string Denominator);

public sealed record RoundingPolicy(int MaxDifferencePerSummedCell, string Explanation);

public sealed record SnapshotEntry(
    string File,
    string PeriodEnd,
    string ApprovedBy,
    string ApprovedOn,
    string Sha256,
    List<ControlTotal> ControlTotals);

public sealed record ControlTotal(string Unit, string Row, string Column, long Value);
