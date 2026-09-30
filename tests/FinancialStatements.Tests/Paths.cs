namespace FinancialStatements.Tests;

static class Paths
{
    public static string Root { get; } = FindRoot();
    public static string Samples => Path.Combine(Root, "samples", "approved-exports");
    public static string Adversarial => Path.Combine(Root, "samples", "adversarial");
    public static string Fixtures => Path.Combine(Root, "tests", "FinancialStatements.Tests", "fixtures");

    static string FindRoot()
    {
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d is not null && !File.Exists(Path.Combine(d.FullName, "FinancialStatements.slnx"))) d = d.Parent;
        return d?.FullName ?? throw new DirectoryNotFoundException("repository root");
    }

    /// <summary>A fresh copy of a folder in a new temporary directory, for tests that change files.</summary>
    public static string CopyOf(string folder)
    {
        var dest = Directory.CreateTempSubdirectory("fs-test-").FullName;
        foreach (var f in Directory.EnumerateFiles(folder)) File.Copy(f, Path.Combine(dest, Path.GetFileName(f)));
        return dest;
    }
}
