"""Message parsing: units, order headers, item lines (tools/learn_aliases.py, tools/learn_items.py)."""
import collections

import learn_aliases as LA
import learn_items as LI


def msg(text, time='2026-10-15 21:30:00', mid='M1'):
    return {'id': mid, 'chat': 'Office Ordering Group', 'time': time, 'type': 'text', 'text': text, 'reply_to': None}


# ---------- units ----------

def test_ctn_and_pkt_convert_to_kg():
    sig = LA.quantities('Wing 10kg\nWhole leg 2 ctn\nBreast 3 pkt\nDrumstick 40pcs')
    assert sig == collections.Counter({('kg', 10): 1, ('kg', 24): 1, ('kg', 6): 1, ('cnt', 40): 1})


def test_size_specs_are_not_quantities():
    # "(90-100g)" and "1.8-2.0kg" describe the piece size, only "5kg" is the order quantity
    assert LA.quantities('Wing (90-100g) 1.8-2.0kg 5kg') == collections.Counter({('kg', 5): 1})


def test_ekor_counts_as_pieces():
    assert LA.quantities('Ayam 10 ekor') == collections.Counter({('cnt', 10): 1})


# ---------- order header ----------

def test_name_first_then_delivery_date():
    po = LA.parse_order(msg('Kedai Makan Aminah\nDelivery: 16/10\nWing 10kg'))
    assert po['date'] == '16/10/2026'
    assert po['names'] == ['Kedai Makan Aminah']


def test_date_first_then_name_and_outlet():
    po = LA.parse_order(msg('16/10\nGolden Spoon Cafe\nTaman Melawati\nWing 10kg'))
    assert po['date'] == '16/10/2026'
    assert po['names'] == ['Golden Spoon Cafe', 'Taman Melawati']


def test_marker_line_is_not_a_name():
    po = LA.parse_order(msg('Delivery\nGolden Spoon Cafe\nDelivery: 16/10\nWing 10kg'))
    assert po['names'][0] == 'Golden Spoon Cafe'


def test_add_on_and_bad_dates_are_not_new_orders():
    assert LA.parse_order(msg('Add\nWing 2kg')) is None
    assert LA.parse_order(msg('Golden Spoon Cafe\nDelivery: 32/13\nWing 10kg')) is None


def test_alias_uses_only_recent_history():
    # a customer that moved from the shared cash account to its own account: the last 5 entries win
    history = [(f'2026-09-0{d}', 'CASH') for d in range(1, 9)] + [(f'2026-10-0{d}', 'OWN') for d in range(1, 6)]
    target, share, seen = LA.decide(history)
    assert (target, share, seen) == ('OWN', 1.0, 13)


# ---------- item lines ----------

def test_item_line_basic():
    assert LI.item_line('Boneless leg 20kg') == ('boneless leg', False, ('kg', 20))


def test_item_line_frozen_and_carton():
    alias, frozen, sig = LI.item_line('Keras wing 2 ctn')
    assert (alias, frozen, sig) == ('wing', True, ('kg', 24))


def test_cut_inside_spec_bracket_still_counts():
    # the bracket is dropped because it has a number, but "cube" still decides the item code
    assert LI.item_line('Boneless leg (cube 2cm) 10kg')[0] == 'boneless leg +cube'


def test_line_with_two_quantities_is_ambiguous():
    assert LI.item_line('Wing 5kg drumstick 3kg') is None


def test_customer_override_beats_general_alias():
    samples = [{'key': ('wing', False), 'customer': c, 'code': 'CHK-WING'} for c in ('A', 'B', 'C')]
    samples += [{'key': ('wing', False), 'customer': 'X', 'code': 'CHK-WING-MID'}] * 3
    general, overrides = LI.build(samples)
    assert LI.lookup(general, overrides, 'X', ('wing', False)) == 'CHK-WING-MID'
    assert LI.lookup(general, overrides, 'A', ('wing', False)) == 'CHK-WING'


def test_unsure_alias_returns_none():
    samples = [{'key': ('ayam', False), 'customer': 'A', 'code': 'CHK-WHOLE'},
               {'key': ('ayam', False), 'customer': 'B', 'code': 'CHK-WHOLE-OFFAL'}]
    general, overrides = LI.build(samples)
    assert LI.lookup(general, overrides, 'A', ('ayam', False)) is None
