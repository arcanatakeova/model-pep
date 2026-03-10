---
name: data-analysis
description: >
  Perform structured data analysis with statistical methodology.
  Use when analyzing datasets, CSVs, logs, or metrics.
  Produces consistent reports with summary statistics, distributions,
  correlations, and actionable insights.
allowed-tools: Read, Bash(python *), Write
---

# Data Analysis Workflow

Execute this methodology for every data analysis request.

## Step 1: Data Profiling

- Read the target file(s) and determine format (CSV, JSON, logs, etc.)
- Count rows, columns, and identify data types
- Check for missing values, duplicates, and outliers
- Report initial data quality assessment

## Step 2: Summary Statistics

- Compute mean, median, mode, std dev for numeric columns
- Compute frequency distributions for categorical columns
- Identify the top 5 most interesting patterns

## Step 3: Analysis

- For detailed statistical methodology, read
  [references/methodology.md](references/methodology.md)
- Apply appropriate tests based on data characteristics
- Generate visualizations if Python matplotlib is available

## Step 4: Report Generation

- Follow the output format in
  [references/output-format.md](references/output-format.md)
- For an example of a completed report, see
  [examples/sample-report.md](examples/sample-report.md)
- Include all findings, methodology notes, and confidence levels

## Step 5: Validation

Run the validation script to check report completeness:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/validate-output.sh <report-file>
```
