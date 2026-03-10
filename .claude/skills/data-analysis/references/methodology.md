# Statistical Methodology Reference

## Choosing the Right Test

| Data Type | Comparison | Test |
|-----------|-----------|------|
| Numeric, normal | 2 groups | t-test |
| Numeric, non-normal | 2 groups | Mann-Whitney U |
| Numeric, normal | 3+ groups | ANOVA |
| Categorical | Association | Chi-square |
| Numeric | Relationship | Pearson/Spearman correlation |

## Normality Testing

Before applying parametric tests, check normality:
- Shapiro-Wilk test for small samples (n < 50)
- Kolmogorov-Smirnov test for larger samples
- Visual inspection via histogram or Q-Q plot

## Outlier Detection

Use IQR method: values below Q1 - 1.5*IQR or above Q3 + 1.5*IQR.
Report outliers but do not remove them without explicit user approval.

For multivariate outlier detection, use Mahalanobis distance.

## Confidence Reporting

Always state confidence level. Default to 95% (alpha = 0.05).
Report p-values to 4 decimal places.

## Effect Size

Report effect size alongside significance:
- Cohen's d for t-tests (small: 0.2, medium: 0.5, large: 0.8)
- Eta-squared for ANOVA (small: 0.01, medium: 0.06, large: 0.14)
- Cramér's V for chi-square
