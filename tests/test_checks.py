"""Deterministic checks that decide whether a model draft is released or held (pipeline/run_night.py)."""
import collections
from types import SimpleNamespace

import pytest

import run_night
from kb import shape

NIGHT = '20261015'


@pytest.fixture
def kb():
    uoms = collections.defaultdict(collections.Counter, {'CHK-WING': collections.Counter(KG=9),
                                                        'CHK-WLEG': collections.Counter(KG=5)})
    return SimpleNamespace(
        customers={'300-G0001': {'CompanyName': 'GOLDEN SPOON CAFE SDN BHD', 'Status': 'A'},
                   '300-C0001': {'CompanyName': 'CASH DEBTOR', 'Status': 'A'},
                   '300-O0001': {'CompanyName': 'OLD SHOP', 'Status': 'I'}},
        history={'300-G0001': [{'branch': 'MELAWATI', 'company': 'GOLDEN SPOON CAFE SDN BHD'}],
                 '300-C0001': [{'branch': '', 'company': 'CASH DEBTOR / AMIN - KEDAI ALI'}],
                 '300-O0001': [{'branch': '', 'company': 'OLD SHOP'}]},
        branches={'300-G0001': [{'BranchName': 'MELAWATI', 'Address1': 'NO 1, JALAN MELAWATI'}]},
        items={'CHK-WING': 'CHIC WING', 'CHK-WLEG': 'CHIC WHOLE LEG'},
        uoms=uoms,
        desc_shapes=collections.defaultdict(set, {'CHK-WING': {shape('CHIC WING (90-100G)')},
                                                  'CHK-WLEG': {shape('CHIC WHOLE LEG (240-260G) - 40PCS')}}),
    )


def order(**kw):
    o = {'customer_code': '300-G0001', 'branch_name': 'MELAWATI', 'delivery_date': '16/10/2026',
         'items': [{'source_line': 'Wing 10kg', 'item_code': 'CHK-WING', 'description': 'CHIC WING (90-100G)',
                    'qty': 10, 'uom': 'KG', 'unit_price': None}]}
    o.update(kw)
    return o


def test_clean_order_passes(kb):
    assert run_night.check_order(kb, order(), ['300-G0001'], NIGHT) == ([], [])


def test_customer_must_come_from_candidates(kb):
    hold, _ = run_night.check_order(kb, order(), ['300-X9999'], NIGHT)
    assert any('候选' in h for h in hold)


def test_inactive_customer_is_held(kb):
    hold, _ = run_night.check_order(kb, order(customer_code='300-O0001', branch_name=''), ['300-O0001'], NIGHT)
    assert any('启用' in h for h in hold)


def test_same_day_delivery_is_held(kb):
    hold, _ = run_night.check_order(kb, order(delivery_date='15/10/2026'), ['300-G0001'], NIGHT)
    assert any('明天以后' in h for h in hold)


def test_unknown_branch_is_held(kb):
    hold, _ = run_night.check_order(kb, order(branch_name='KEPONG'), ['300-G0001'], NIGHT)
    assert any('分店' in h for h in hold)


def test_piece_order_entered_as_kg_is_held(kb):
    item = {'source_line': 'Whole leg 40pcs', 'item_code': 'CHK-WLEG',
            'description': 'CHIC WHOLE LEG (240-260G) - 40PCS', 'qty': 40, 'uom': 'KG', 'unit_price': None}
    hold, _ = run_night.check_order(kb, order(items=[item]), ['300-G0001'], NIGHT)
    assert any('只数' in h for h in hold)


def test_piece_order_with_zero_qty_passes(kb):
    item = {'source_line': 'Whole leg 40pcs', 'item_code': 'CHK-WLEG',
            'description': 'CHIC WHOLE LEG (240-260G) - 40PCS', 'qty': 0, 'uom': 'KG', 'unit_price': None}
    assert run_night.check_order(kb, order(items=[item]), ['300-G0001'], NIGHT) == ([], [])


def test_unknown_item_and_new_description_shape_are_held(kb):
    items = [{'source_line': 'x 1kg', 'item_code': 'NOPE', 'description': 'X', 'qty': 1, 'uom': 'KG'},
             {'source_line': 'Wing 10kg', 'item_code': 'CHK-WING', 'description': 'CHIC WING JUMBO', 'qty': 10, 'uom': 'KG'}]
    hold, _ = run_night.check_order(kb, order(items=items), ['300-G0001'], NIGHT)
    assert any('不在主档' in h for h in hold)
    assert any('没见过' in h for h in hold)


def test_shared_cash_account_needs_company_name(kb):
    hold, _ = run_night.check_order(kb, order(customer_code='300-C0001', branch_name=''), ['300-C0001'], NIGHT)
    assert any('cash debtor' in h for h in hold)


def test_branch_with_address_is_trimmed():
    assert run_night.clean_branch('[MELAWATI] NO 1, JALAN MELAWATI') == 'MELAWATI'
    assert run_night.clean_branch('BILLING （地址: NO 1）') == 'BILLING'


def test_entry_merges_repeated_lines_and_marks_staff(kb):
    o = order(staff=True, items=[
        {'item_code': 'CHK-WING', 'description': 'CHIC WING (90-100G)', 'qty': 4, 'uom': 'KG', 'unit_price': None},
        {'item_code': 'CHK-WING', 'description': 'chic wing (90-100g) ', 'qty': 6, 'uom': 'KG', 'unit_price': None}])
    e = run_night.to_entry(kb, o, 'REF-STAFF')
    assert e['doc_ref1'] == 'STAFF'
    assert e['d_address1'] == 'NO 1, JALAN MELAWATI'
    assert e['items'] == [{'item_code': 'CHK-WING', 'description': 'CHIC WING (90-100G)', 'qty': 10, 'uom': 'KG'}]
