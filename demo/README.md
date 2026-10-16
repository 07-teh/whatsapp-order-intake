# Demo (synthetic data)

> **Everything in this folder is synthetic.** Customers, addresses, products, prices, messages and orders were
> invented by `make_data.py`. They imitate the *patterns* of the real order chats, not their content.

```bash
python3 demo/run_demo.py            # replay recorded DeepSeek answers, no key or network needed
python3 demo/run_demo.py --record   # call DeepSeek live (DEEPSEEK_API_KEY) and refresh demo/recorded/
```

`run_demo.py` builds `demo/workspace/` and runs the production code there unchanged:

1. `tools/learn_aliases.py` pairs 264 historical messages with their Sales Orders by date and quantities,
   and learns how each customer is written in the chat;
2. `tools/learn_items.py` learns item words, including per-customer overrides;
3. `pipeline/run_night.py` processes the demo night (14 Oct 22:00 → 15 Oct 22:00): candidate customers → prompt →
   DeepSeek → deterministic checks → `orders_ds.json` + review list;
4. `pipeline/eval.py` compares the result field by field with the human-verified answer
   (`workspace/output/orders_20261015.json`).

Output: `demo/workspace/output/ds/20261015/` with `demo_result.json` (marked `"synthetic": true`),
`orders_ds.txt` (review list) and `raw.json` (prompt candidates and raw model output).

## What the demo night contains

| Message | What it tests | Expected |
|---|---|---|
| `16/10 / Golden Spoon / Melawati / Wing 10kg / Boneless leg 2 ctn` | date-first layout, outlet on line 2, ctn → 24 kg | released |
| `Add wing 3kg` (reply to the above) | add-on reply merged into the original order | merged (wing 13 kg) |
| `Tomyam House / 15/10 / Feet 5kg` posted 05:32 | same-day order after midnight | left for the morning shift |
| `Kedai Makan Aminah / esok / Kepak 5kg / Peha ayam 40 ekor` | Malay words, no date ("esok" = tomorrow), head count → qty 0 + `- 40PCS` | released |
| `Saffron Bistro Bangsar / Boneless leg 8kg` | per-customer override: this customer's "boneless leg" is the cubed item | released |
| `Burger Station Kepong ... Staff meal (separate invoice): Wing 2kg` | one message → two orders, staff order gets Ref1 `STAFF` | released ×2 |
| `Happy Wok 快乐镬 / Drumstick 10kg @ 9.80 / Wing 5kg (RM11.50)` | Chinese in the header, prices in the text | released with prices |
| `Wong Kee Duck / Duck 10 nos` | ambiguous item (Eden or regular duck) | **held**, question for a person |
| `CRISPY CORNER SDN BHD / ...` | customer exists in master data but has no order history | **held** (new customer) |
| `sri maju nk / Ayam 20 ekor / kaki ayam 3pkt` | lower-case nickname; customer entered by head count (NOS); pkt → 6 kg | released |
| `SUNRISE CATERING SDN BHD / PO: PO-2026-0412 / ...` | PO number → Ref1, pcs line | released |
| `Kedai Pak Lah (cash) / Wing 5kg` | shared CASH DEBTOR account, company name taken from precedent | released |
| `Rasa Kita TTDI / ... / 鸡胸 Breast 4kg` | mixed Chinese/English item line | released |
| `Got order 👍`, `noted` | chatter | skipped |

The "expected" column is what a careful clerk would do. The demo prints what DeepSeek and the checks actually did.

## What DeepSeek actually did (recorded run)

Model `deepseek-flash`, low reasoning effort, 12 calls, ~32k input tokens. The demo night is fixed
(`20261015`, window 14 Oct 22:00 → 15 Oct 22:00) and no check reads today's date, so every replay gives the
same result. `eval.py` prints:

```
== 20261015  答案 12 张 | DeepSeek 出了 11（全对 9，有错 2） | 扣下 1 | 附件 0 | 漏了 0 | 多出 0
   放行的单正确率 81.8%   覆盖率 91.7%   错的字段 {'品项': 2}
```

That is, of the 12 orders a person finally entered, **11 were released** (9 correct in every field, 2 wrong)
and **1 was held**:

| Order | Result | Why |
|---|---|---|
| 9 orders (mixed languages, ctn/pkt conversion, head count as qty 0, staff-meal split, PO → Ref1, shared cash account, per-customer override) | released, correct | |
| Golden Spoon (`20261014-Ami-91AD4`) | released, **wrong**: wing 10 kg, answer 13 kg | Its `Add wing 3kg` reply was **held**: the model wrote `CHIC WING` without the size this customer always uses, and the description-shape check stopped it. The original order went out without the extra 3 kg, which is left for a person to add. The add-on is not an order of its own, so `eval.py` counts it as an error on Golden Spoon. |
| Wong Kee Duck (`Duck 10 nos`) | released, **wrong**: regular duck, answer Eden duck | The rules say *ask* when Eden vs. regular duck cannot be told apart. The model went by this customer's history instead, and no check caught it. |
| Crispy Corner | **held** | Customer is in the master data but has no order history. |

In the refs, `Ami` / `Mei` / `Off` are the first three letters of the chat name (`Amin - Ordering Group`, ...),
not the customer.

The duck miss points to the next improvement: a deterministic rule that holds any duck line that does not
say which kind, instead of trusting the model to ask. The demo night is deliberately packed with edge cases,
so its 81.8% precision (9/11) is not comparable with the 96.2% measured over 346 real orders.
