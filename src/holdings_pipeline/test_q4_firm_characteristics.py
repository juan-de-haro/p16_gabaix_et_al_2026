"""Check market-cap authority, gvkey accounting, and coverage audit."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from holdings_pipeline.build_q4_firm_characteristics import build_firm_table

Q = pd.Timestamp("2022-12-31")


def _jkp_row(permno, permco, gvkey, me, be, size="large", **overrides):
    row = dict(
        permno=permno,
        permco=permco,
        gvkey=gvkey,
        eom=Q,
        size_grp=size,
        me=me,
        book_equity=be,
        at_gr1=0.1,
        gp_at=0.2,
        ret_12_1=0.3,
        div_at=0.01,
        beta_60m=1.0,
    )
    row.update(overrides)
    return row


def _crsp_row(permno, permco, price, shrout):
    return dict(
        permno=permno,
        permco=permco,
        mthcaldt=pd.Timestamp("2022-12-30"),
        mthprc=price,
        shrout=shrout,
        securitytype="EQTY",
        securitysubtype="COM",
        sharetype="NS",
        issuertype="CORP",
        primaryexch="N",
    )


def test_provisional_table_uses_crsp_me_and_counts_book_once():
    holdings = pd.DataFrame(
        [
            dict(
                quarter=Q,
                permco=10,
                source="s12",
                sample_status="provisional_overlap_unresolved",
            ),
            dict(
                quarter=Q,
                permco=10,
                source="s34",
                sample_status="provisional_overlap_unresolved",
            ),
            dict(
                quarter=Q,
                permco=20,
                source="s34",
                sample_status="provisional_overlap_unresolved",
            ),
        ]
    )
    jkp = pd.DataFrame(
        [
            _jkp_row(1, 10, "A", 60, 50, at_gr1=0.1),
            _jkp_row(2, 10, "A", 20, 50, at_gr1=0.5),
            _jkp_row(3, 20, "B", 10, -1),
        ]
    )
    monthly = pd.DataFrame(
        [
            _crsp_row(1, 10, -5, 10000),
            _crsp_row(2, 10, 10, 5000),
            _crsp_row(3, 20, 5, 1000),
        ]
    )
    table, coverage, audit = build_firm_table(holdings, jkp, monthly)
    assert table.permco.tolist() == [10]
    row = table.iloc[0]
    assert row.me == 100  # CRSP: (5*10000 + 10*5000)/1000; JKP sums to 80
    assert row.be == 50  # One gvkey across two share classes
    assert row.at_gr1 == pytest.approx(0.2)
    assert row.log_be_me == pytest.approx(np.log(0.5))
    assert row.holdings_sources == "s12,s34"
    assert audit["holdings_firms"] == 2
    assert audit["eligible_firms"] == 1
    assert bool(coverage.set_index("permco").loc[20, "nonpositive_or_missing_be"])


def test_inconsistent_gvkey_book_equity_is_quarantined():
    holdings = pd.DataFrame(
        [
            dict(
                quarter=Q,
                permco=10,
                source="s12",
                sample_status="provisional_overlap_unresolved",
            )
        ]
    )
    jkp = pd.DataFrame([_jkp_row(1, 10, "A", 80, 50), _jkp_row(2, 10, "A", 20, 55)])
    monthly = pd.DataFrame([_crsp_row(1, 10, 5, 10000), _crsp_row(2, 10, 10, 5000)])
    table, coverage, audit = build_firm_table(holdings, jkp, monthly)
    assert table.empty
    assert bool(coverage.iloc[0].book_equity_conflict)
    assert audit["book_equity_conflict_firms"] == 1
