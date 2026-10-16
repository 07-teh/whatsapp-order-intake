# Production setup (one page)

The real deployment runs on two machines. Nobody outside the company can reproduce it, because it needs
the company's SQL Account licence and its WhatsApp groups. This page describes how it fits together.
To see the pipeline run, use the [demo](../demo/README.md).

## Machines

| Machine | Runs | Needs |
|---|---|---|
| Mac in the office | `tools/wa_extract.py`, `pipeline/run_night.py`, n8n, the console | WhatsApp Desktop logged in to the order groups; Python 3.10+; `pdftotext`/`pdftoppm` (poppler) for PDF orders; n8n; `DEEPSEEK_API_KEY` in `.env` |
| Windows PC with SQL Account | `company_pc/*.py` | SQL Account with SDK Live (COM); Python with `pywin32`; a user logged in to SQL Account |

## Nightly cycle

1. **22:00 n8n** (`n8n/workflow_nightly_ds.json`) runs `wa_extract.py` and then `run_night.py --auto`. The
   batch window starts where the previous run ended.
2. The run writes `orders_ds.json` (released drafts), `orders_ds.txt` (review list, held items first) and a
   zip, and `console/notify.py` posts it to the console.
3. A person reviews the list, answers the held items and approves the batch. `tools/pack.py` builds the zip for
   the company PC, with two double-click files: `1_preview.bat` and `2_write.bat`.
4. On the company PC: run preview, check it, then write. `sdk_enter_so.py` saves each order, reads it back,
   and logs it in `entered_log.json` so a rerun never enters an order twice.
5. `sdk_dump_master.py` then exports the orders from 3 days back to 7 days ahead plus customers, branches and items
   into `back_*.zip`.
6. The back zip is dropped into the console. n8n (`workflow_upload_back.json`) runs `pipeline/ingest_back.py`,
   which puts it into `data/`. The next night learns from it.

## One-time setup

```bash
python3 tools/wa_extract.py                 # all history from the order groups
# on the company PC: export master data and a year of Sales Orders
python sdk_dump_master.py --so-from 01/01/2026 --so-to 09/10/2026
python3 pipeline/ingest_back.py back_xxx.zip
python3 tools/learn_aliases.py              # message <-> order pairs, customer aliases
python3 tools/learn_items.py                # item aliases and per-customer overrides
python3 pipeline/eval.py <night> ...        # replay past nights before trusting the run
```

## Hard-coded paths

The Mac side was written for one machine: an Apple Silicon Mac with Homebrew in `/opt/homebrew`.
These absolute paths need changing on any other setup:

| File | Path |
|---|---|
| `pipeline/attachments.py` | `/opt/homebrew/bin/pdftotext`, `/opt/homebrew/bin/pdftoppm` (poppler), `/usr/bin/sips` (macOS) |
| `console/bots.json` | `/opt/homebrew/bin/python3` for the balance query |
| `n8n/start_n8n.sh` | Homebrew `node@24` on `PATH` (`/opt/homebrew/opt/node@24/bin`) |
| `n8n/desktop_launcher.sh` | `/opt/homebrew/bin/python3` to start the console |
| `n8n/workflow_*.json` | `/opt/homebrew/bin/python3` and `cd /path/to/whatsapp-order-intake` in every Execute Command node |
| `tools/wa_extract.py`, `pipeline/attachments.py` | the WhatsApp Desktop container under `~/Library/Group Containers/` (macOS only) |

The demo and the tests do not use any of these, and CI runs them on Linux.

`data/`, `output/`, `.env` and anything with real customer data is git-ignored.
