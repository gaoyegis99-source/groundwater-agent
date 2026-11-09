# CSV statistics method

The capability accepts a host-staged UTF-8 CSV file and one exact column name.
The header must be present and unique. Every selected cell must parse as a
finite floating-point number; blank, infinite, NaN, missing, and malformed
values block the workflow before approval.

The compile-time parameter profile bounds input bytes and data rows and fixes
the number of decimal places. After a rule-bound PASS receipt and human
approval, the calculation publishes count, minimum, maximum, and arithmetic
mean as a content-addressed JSON artifact. It performs no network access and
does not infer units, weighting, missing-value policy, or scientific meaning.
