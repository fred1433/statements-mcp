# statements-mcp

A read-only MCP server that lets Claude answer questions on approved income statement exports, and stop when an export cannot support the answer.

Synthetic demonstration. Workbook structure modeled on documented Management Reporter reporting concepts; not generated from a real system and not validated against a real export layout. Every company name and figure in `samples/` is invented.

## What it does

- Reads only the Excel exports listed in `approved.json` (file name, SHA-256, who approved it, control totals read off the rendered report). No path argument, no SQL, no write tool.
- Reads the values stored in the cells and never recalculates a formula. A formula with no stored value blocks the export.
- Blocks an export that changed after approval, states a currency or scale other than the approved profile's ("Amounts in Thousands of US Dollars" against a profile in dollars), misses a reporting unit, has totals that do not tie to their lines or units beyond the written rounding policy, leaves a printed rate blank or prints one that does not match its inputs, or does not match its control totals. A blocked export is never replaced by another period silently; an older usable one is offered as such.
- When a corrected export for the same period is approved later, the most recently approved one that passes its checks is served; the other is listed as replaced.
- Writes the start date of a quarter or a year to date only from a fiscal year start declared in `approved.json`; without it, it says the start date is not declared. Management Reporter follows the ledger's fiscal calendar, which the export does not state.
- Returns every printed value with its cell (`[IS_2026-06.xlsx]Total!C13`), its scale as read from the export, and snapshot context (company, period end, column scope, scenario, currency, SHA-256, approval). Computed values (changes, shares, sums, relative changes, the margin bridge) come with their formula and input cells, so Claude does no arithmetic of its own.
- `margin_bridge` splits a change in the company's gross margin rate into a rate effect and a mix effect. In the sample, every unit's margin rises from Q1 to Q2 while the company's falls 0.46 points, because sales shift toward the lowest-margin unit.

Six tools: `list_snapshots`, `get_values`, `compare_periods`, `margin_bridge`, `calculate`, `get_report_comments`.

Stack: C# on .NET 10, official MCP C# SDK `ModelContextProtocol` 2.2.0, ClosedXML 0.105.1. The only file that knows the workbook layout is `src/FinancialStatements.Mcp/ExportReader.cs`; a real export is mapped there.

## Run the tests on a clean clone

Needs the .NET 10 SDK and [uv](https://docs.astral.sh/uv/).

```sh
dotnet test                       # parser, integrity checks, queries, access boundaries, MCP over stdio (44 tests)
uv run control/test_check.py      # the answer checker catches invented figures, wrong cells, points written as percent
uv run control/check.py           # re-checks the 12 recorded conversations against the workbooks
```

`tests/FinancialStatements.Tests/expected.json` is computed by `tools/expected.py` with a different reader (openpyxl) and plain arithmetic, not by the code under test.

## Recorded conversations

`control/transcripts/` holds 12 conversations recorded with Claude Code as the MCP client (model `claude-sonnet-5-5`), every tool call and tool result included. `sh control/run.sh` records them again. `control/RESULTS.md` has the counts: 72 figures written in the answers, none untraced; 44 cell citations, none to a cell no tool returned, none with a different figure; behavior graded by a person in `control/grades.json`, with remarks.

The server returns source-linked values and deterministic calculations. The published test results separately check Claude's answers for unsupported financial claims. That is a measurement on these 12 answers, not a guarantee about the next one.

## Install in Claude Desktop

`sh packaging/build.sh` builds `dist/financial-statements-macos-arm64.mcpb` and `dist/financial-statements-windows-x64.mcpb` (self-contained, no .NET install needed). Open the file with Claude Desktop, then choose the approved exports folder in the extension settings, or leave it empty to use the sample.

Built for Claude Desktop; tested with Claude Code as the MCP client. Checked: the macOS package's server over stdio. Not yet checked: installation inside Claude Desktop; the Windows package on a clean Windows machine. The binaries are not code-signed.

## Privacy

The connector runs locally and reads approved exports without modifying them. Returned figures and their report context are sent to Anthropic through Claude; your organization's Claude data policies apply. Hidden rows in a workbook are not an access control: only put in the folder what may be returned. `approved.json` is not signed: whoever can write to the folder can approve an export, so give write access to the controller only.

## What this does not prove

This demonstrates the connector and its checks on synthetic data. Compatibility with your exports and diagnosis of your report errors require validation on your system. Whether a report schedule can deposit Excel files in a folder without a person exporting them is unverified until demonstrated on the relevant Management Reporter build; the starting point is a workbook exported by hand.

## Appendix: Management Reporter 2012 and Dynamics GP

Documented report errors, each with its specific condition:

- "Object reference not set to an instance of an object" when generating a report: an Excel link in the row definition pointing to a CALC column, or a summary level of the reporting tree with a mask in the Dimension column. [KB2976908](https://learn.microsoft.com/en-us/troubleshoot/dynamics/gp/object-reference-not-set-to-instance-of-object-error-when-creating-report-in-management-reporter)
- "Index was out of range" in the Dimension Value task of the data mart integration: account segment references in GL40200 missing from SY00300 or GL00100. [KB3029952](https://learn.microsoft.com/en-us/troubleshoot/dynamics/gp/management-reporter-2012-data-mart-integration-error-index-was-out-of-range)
- "The operation could not be completed due to a problem in the data provider framework" when opening a row or column definition: a blank, duplicate or reserved name among GL user-defined fields and Analytical Accounting dimensions. [KB2776705](https://learn.microsoft.com/en-us/troubleshoot/dynamics/gp/data-provider-framework-error-in-microsoft-management-reporter-2012-for-dynamics-gp)
- Process Service stops and restarts continually after installation: the 32-bit SP3 CLR Types for SQL Server 2012 installed without the 64-bit one. [KB3144801](https://learn.microsoft.com/en-us/troubleshoot/dynamics/gp/management-reporter-2012-process-service-stops-and-restarts-continually)

Approach: compare one failing report with a working one under the same company, period and user; capture the exact error and the matching Application event on the client and the server; check the row, column and reporting tree definitions it implicates before changing the integration.

Dynamics GP lifecycle ([Microsoft Learn](https://learn.microsoft.com/en-us/dynamics-gp/terms/lifecycle), read September 30, 2026): fixed-lifecycle GP 2016 extended support ended July 14, 2026; GP 2018's ends January 11, 2028; under the Modern Lifecycle Policy, enhancements, tax updates and technical support end December 31, 2029, security patches if needed April 30, 2031.

## License

MIT
