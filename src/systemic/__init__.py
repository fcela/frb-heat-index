"""Systemic-risk indicators from the published JBF (2017) version of the heat map.

Two market-based indicators added in the published version
(Journal of Banking and Finance 81 (2017), pp. 36-64):

1. Total SRISK relative to total assets (Acharya, Engle & Richardson 2012):
   aggregate expected capital shortfall of large financial firms conditional
   on a severe market decline, scaled by their total book assets.
   SRISK itself is replicated from public inputs using the V-LAB "without
   simulation" method (publicly documented at vlab.stern.nyu.edu/docs/srisk):
     LRMES_i = 1 - exp(log(1-0.40) * beta_i)
     SRISK_i = max(0, k*DEBT_i - (1-k)*EQUITY_i*(1-LRMES_i)), k = 8%
   V-LAB's downloadable data requires a login, so firm-level SRISK is
   rebuilt from: Y-9C book liabilities (public), market cap from public
   prices x public shares outstanding (SEC EDGAR), and rolling market betas
   from public returns. Pre-2009 shares are split-adjusted backward carries
   from XBRL (spot-checked against 10-Ks); see fetch_shares.py.

2. Significant Granger-causality connections among large financial
   institutions (Billio, Getmansky, Lo & Pelizzon 2012): pairwise linear
   Granger-causality tests on monthly returns of large listed financial
   firms, 36-month rolling windows, 5% significance; the indicator is the
   share of significant directed edges out of all n*(n-1) possible.
   Hedge funds (25 of the 100 firms in Billio et al.) have no public
   return data and are excluded; delisted crisis-era firms (Lehman, Bear
   Stearns, Merrill, Wachovia, WaMu) have no public price history on
   Yahoo/Nasdaq and are excluded. Both exclusions are documented in the
   proxy catalog.

Public data only. Polars throughout.
"""
