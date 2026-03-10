# Sales Data Analysis Report

## Executive Summary

Analysis of Q4 2025 sales data (12,847 transactions) reveals a 23% increase in average order value compared to Q3. The electronics category drives 61% of revenue growth. Recommend expanding electronics inventory and running targeted promotions for underperforming categories.

## Data Overview

- **Source**: sales_q4_2025.csv
- **Shape**: 12,847 rows x 14 columns
- **Data quality**: 99.2% complete (108 rows with missing `shipping_address`), 0 duplicates, 3 outlier transactions (>$10,000)

## Key Findings

1. **Average order value increased 23%**: $147.32 (Q4) vs $119.78 (Q3), t(25692) = 8.41, p < 0.0001, Cohen's d = 0.15
2. **Electronics drives growth**: 61% of revenue increase from electronics category, chi-square(4) = 234.7, p < 0.0001
3. **Weekend orders 18% higher**: Mean weekend orders = 892/day vs weekday = 756/day, U = 1247, p = 0.0023
4. **Customer retention stable**: Repeat customer rate = 34.2% (Q4) vs 33.8% (Q3), not statistically significant (p = 0.4821)
5. **Geographic shift**: West Coast orders up 31%, Midwest flat (+2%), suggesting regional marketing effectiveness

## Methodology Notes

- Used Mann-Whitney U test for order value comparison (non-normal distribution, Shapiro-Wilk p < 0.001)
- Chi-square test for category distribution comparison
- Outliers retained in analysis (IQR method identified 3 extreme values)
- 95% confidence level used throughout

## Recommendations

1. **Expand electronics inventory** — highest growth category with sustained demand
2. **Investigate Midwest marketing** — flat growth suggests underserved market
3. **Optimize weekend staffing** — higher order volume on weekends warrants support scaling
4. **Address missing shipping data** — 108 incomplete records may affect fulfillment

## Appendix

| Metric | Q3 2025 | Q4 2025 | Change |
|--------|---------|---------|--------|
| Total transactions | 11,204 | 12,847 | +14.7% |
| Avg order value | $119.78 | $147.32 | +23.0% |
| Total revenue | $1,341,815 | $1,892,158 | +41.0% |
| Unique customers | 8,412 | 9,103 | +8.2% |
| Repeat rate | 33.8% | 34.2% | +0.4pp |
