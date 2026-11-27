# Manually-Created Data

This folder is used to hold manually created data that cannot be easily replicated.
You may keep data here and keep it under version control if it is small enough.
Keeping this data under version control can provide some peace of mind
that the data is not inadvertently modified.
Also, keep in mind that Git LFS is a good option if the data is large.

## paper_figure2.csv

Annual values from Figure 2 of Gabaix, Koijen, Richmond, and Yogo (2026), "Asset and Investor Embeddings" (SSRN 4507511, July 16, 2026 version): the average number of firms, investors, and holdings (thousands) per quarter, by year, 2005–2022. Extracted from the vector graphics of the figure (page 24) with PyMuPDF: each data point's coordinates were converted to values with a linear fit to the axis tick labels. Values are rounded to whole units and are accurate to about ±1 unit. Used by `src/holdings_pipeline/report_s34_coverage.py`.
