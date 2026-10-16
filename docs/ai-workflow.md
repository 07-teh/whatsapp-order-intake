# How AI was used

This project was developed with extensive AI assistance. AI was responsible for much of the initial code
implementation, while I defined the requirements, directed the development, reviewed outputs, identified
issues, and guided corrections. I also validated the system against its intended workflow.

## Who did what

| Role | Who / what |
|---|---|
| Requirements, priorities, business rules, final approval | Me |
| Implementation, data analysis, first drafts | Claude Code (Anthropic) |
| Second opinion from a different model family | Codex CLI (OpenAI): asked to *disprove* a diagnosis before a fix, and "where will this break?" on diffs before commit |
| Nightly order recognition in production | DeepSeek (cheap enough to run every night) |
| Validation against the real workflow | Me, on the company PC and in SQL Account |

## Guardrails

AI-written code is only useful if mistakes are caught before they reach the ERP. These guardrails were
part of the workflow from the start:

- **Checkpoint commit before every change.** The git history is a chain of
  `checkpoint: before <change>` commits, so any step can be inspected or rolled back.
- **Read-only first.** The SDK was explored with read-only scripts (`sdk_dump_so.py`) before any write code existed.
- **Preview before write, read back after write.** The entry script fills each order in memory and prints
  it; only `--write` saves, and every saved order is read back and compared.
- **Deterministic checks around the model.** The LLM proposes and plain code verifies (see `check_order()` in
  `pipeline/run_night.py`).
- **Replay evaluation and shadow mode.** Any change to prompts or rules is measured against nights that a
  person has already verified.
- **Unit tests** for the parsing, unit conversion and checks (`tests/`).

## Issues caught and corrected

These were found during review or live testing and fixed:

| Issue | How it was caught | Fix |
|---|---|---|
| Delivery date entered one day off | First real order entered through the SDK, checked in SQL Account | Date handling corrected before any batch ran |
| SDK-created orders missing the header description that manual entry fills automatically | Compared an SDK order with a manually entered one | Description set explicitly |
| Valid item codes such as `E/DUCK-2.4` or `B+W` rejected | Entry check failed on real codes | Validation pattern widened; quotes still rejected because the query is built as a string |
| Learned customer table pointing to an old account | A customer had moved from the shared cash account to its own | Only the most recent entries vote |
| Model copying next-morning weights into head-count lines | Replay evaluation, item-line errors | History shown to the model "as entered" (quantity 0) |
| Inactive customers or items offered to the model | Questions to the user about held drafts | Inactive records removed from candidates |
| Model pasting the address into the branch field | Reviewing drafts | Branch name cleaned before checks |

## What I would tell an interviewer

AI wrote most of the first drafts quickly. My job was knowing what "correct" meant for this business,
checking the output against the real system, spotting where the code and the business rules disagreed, and
building the checks, previews, tests and evaluation so that mistakes would show up in review instead of in
a customer's delivery.
