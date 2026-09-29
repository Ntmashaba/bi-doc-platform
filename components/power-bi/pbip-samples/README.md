# Public sample projects for DirectQuery / live-connection coverage

Test data only, sourced from public GitHub repositories. Remove them if this project is ever distributed.

| Folder | Case | Origin |
|---|---|---|
| `directquery-to-analysis-services/` | Own model; tables in DirectQuery mode read from a remote model (`AnalysisServices.Database` via a shared expression) | MicrosoftLearning/mslearn-fabric, `Allfiles/Labs/16b/Solution` (MIT) |
| `thin-report-live-connection/` | Thin PBIR report, `definition.pbir` `byConnection` to a `powerbi://` semantic model | data-goblin/power-bi-agentic-development, `pbir-format/examples/K201-MonthSlicer.Report` (GPL-3.0) |

The `DP500 *` files in `pbix-samples/` come from MicrosoftLearning/DP-500-Azure-Data-Analyst (MIT):
04 = all tables DirectQuery to SQL Server; 08 = composite (DirectQuery + Import, server/database as parameters);
11 = Dual storage mode. They need pbi-tools to extract, so run them locally with `--pbix`.

Not yet found publicly: a thin report on Azure/SQL Server Analysis Services; DirectQuery on Snowflake/Databricks/Oracle/etc.
