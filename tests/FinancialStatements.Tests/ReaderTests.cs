using FinancialStatements.Mcp;
using static FinancialStatements.Mcp.ExportReader;

namespace FinancialStatements.Tests;

// Parser tests: what the import adapter reads from a workbook, before any check or query.
public class ReaderTests
{
    static readonly WorkbookRead June = ExportReader.Read(Path.Combine(Paths.Samples, "IS_2026-06.xlsx"));

    [Fact]
    public void Reads_header_block_and_one_sheet_per_unit()
    {
        Assert.Empty(June.Problems);
        Assert.Equal(new[] { "Total", "Wholesale", "Online", "Service" }, June.Sheets.Select(s => s.Name));
        var w = June.Sheets.Single(s => s.Name == "Wholesale");
        Assert.Equal("Alder & Finch Supply Co.", w.Company);
        Assert.Equal(new DateOnly(2026, 6, 30), w.PeriodEnd);
        Assert.Equal("Wholesale", w.Unit);
        Assert.Equal(new[] { "Current Period Actual", "Quarter to Date Actual", "Year to Date Actual" }, w.Columns);
    }

    [Fact]
    public void Blank_is_not_zero()
    {
        var service = June.Sheets.Single(s => s.Name == "Service");
        Assert.Null(service.Cells[("Freight billed to customers", "Quarter to Date Actual")].Value);
        var online = June.Sheets.Single(s => s.Name == "Online");
        Assert.Equal(0, online.Cells[("Service revenue", "Quarter to Date Actual")].Value);
    }

    [Fact]
    public void Reads_report_comments_as_text_with_their_cell()
    {
        var c = Assert.Single(June.Sheets.Single(s => s.Name == "Total").Comments);
        Assert.StartsWith("Wholesale: deliveries under the Harbor County", c.Text);
        Assert.Equal("A36", c.Address);
    }

    [Fact]
    public void A_formula_without_a_stored_value_is_reported_not_recalculated()
    {
        var wb = ExportReader.Read(Path.Combine(Paths.Fixtures, "formula_no_value.xlsx"));
        var cell = wb.Sheets.Single(s => s.Name == "Total").Cells[("Revenue", "Current Period Actual")];
        Assert.Null(cell.Value);
        Assert.Contains("formula with no stored value", cell.Problem);
    }

    [Fact]
    public void A_moved_header_block_is_refused_not_guessed()
    {
        var wb = ExportReader.Read(Path.Combine(Paths.Fixtures, "header_moved.xlsx"));
        Assert.DoesNotContain(wb.Sheets, s => s.Name == "Total");
        Assert.Contains("header block", Assert.Single(wb.Problems));
    }

    [Theory]
    [InlineData("For the Period Ending February 28, 2026", 2026, 2, 28)]
    [InlineData("For the period ending June 30, 2026", 2026, 6, 30)]
    public void Parses_period_line(string text, int y, int m, int d) => Assert.Equal(new DateOnly(y, m, d), ExportReader.ParsePeriod(text));

    [Fact]
    public void Unparseable_period_line_is_null() => Assert.Null(ExportReader.ParsePeriod("Year ended"));
}
