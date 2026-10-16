# How the requirements were gathered and confirmed

The hard part of this project was not code. It was finding out exactly how a clerk turns a chat message
into a Sales Order, including all the unwritten exceptions, and making sure each rule was right
*before* it was automated. This is the process that was used.

## 1. Start from the real workflow and the real output

- I wrote down the input (three WhatsApp groups) and the output (Sales Orders in SQL Account) and agreed
  on the target with the user: one message in, one correctly structured order out. Nothing missed, nothing invented.
- Rules were not taken from memory. Historical Sales Orders were exported from SQL Account and **paired with the
  chat messages they came from**, matched only by delivery date and quantities. Those pairs showed how each
  message had actually been entered: which customer, which branch, which item code, what description. They
  became the reference for every rule below.

## 2. Ask in rounds, confirm each rule, write it down

Questions were asked in rounds. Each round only covered what could be decided from earlier answers, and
each question came with a recommended answer the user could accept or correct. Every confirmed rule went
into a rule table and then into the model prompt (`pipeline/rules_prompt.md`). Examples:

| Question | Confirmed rule |
|---|---|
| A line says `40 pcs` / `40 ekor`. What quantity is entered? | Quantity **0**, count written into the description (`- 40PCS`). The weight is filled in after packing. Exception: customers whose history is entered in NOS. |
| What do `pkt` and `ctn` mean in kg? | `pkt` = 2 kg, `ctn` = 12 kg (ducks follow the pack size in the item name). |
| A price is written in the message. | Enter it. Prices inside PDF purchase orders are **not** entered; the system price is used. |
| Which messages belong to tonight's batch? | Batch boundary at 00:00 (22:00 during testing). Same-day orders posted after midnight belong to the morning shift. |
| The company on the message differs from the delivery shop. | The billing company is the customer, and the shop is one of its branches. Only ask if the address is not in the branch list. |
| "Duck" without saying which kind. | Two item families (Eden vs. regular). Choose by what the message says; if it cannot be told apart, **ask**, never infer from history. |
| Inactive customers or item codes? | Never offered as candidates, because they cannot be selected in SQL Account anyway. |
| Returns / refunds in the group? | Out of scope. Ignore. |

## 3. When unsure, ask. Don't guess from history

Anything the rules don't settle (a new customer, an ambiguous item, an address not in the master data)
becomes a question. Questions go at the **top** of the nightly handoff, so the user deals with them before
anything else. The same principle is coded into the pipeline: the model must put any doubt into
`questions`, and a draft with questions is held.

## 4. Early-stage human review and sign-off of the final delivery

In the first phase, nothing went into the ERP without a person checking it:

1. Each night's recognized orders were put into a review list, with uncertain items first.
2. The user reviewed the batch and answered the open questions. Corrections were applied before packing.
3. On the company PC the user ran **preview** first (the order is filled in memory and printed, not saved),
   and only then **write**.
4. After writing, the script read every order back from SQL Account and compared it field by field. The
   result came back in the return package and was reconciled against what had been approved.
5. The user confirmed the final delivery of each batch. The system never signed off on its own work.

The first live night went through this process: 83 orders entered, every one read back and matched.

## 5. Shadow mode before automation

The cheaper DeepSeek pipeline was not trusted just because it ran. It runs **in parallel** with the
reviewed process, and its drafts are compared with what was actually entered (`pipeline/eval.py`) but never
entered themselves. Numbers decide when it can take over more of the work: currently 96.2% of the drafts it
releases are correct in every field, and everything else is held with a reason.

## 6. Turn every correction into a rule or a test

When the user corrected something, the fix went into the rules, the checks or the tests rather than being
patched by hand. Examples from the history:

- "Stopped customers and stopped item codes never become candidates" came out of a round of questions
  about the held drafts.
- "Use only the most recent ways a customer was entered" was added after a customer moved from the shared
  cash account to its own account and the old history kept pointing to the wrong one.
- Head-count lines in old orders show a weight that was filled in the next morning. The model was copying
  those weights, so history is now shown to it "as entered" (quantity 0).
