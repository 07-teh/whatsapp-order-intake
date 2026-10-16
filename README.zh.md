# WhatsApp 订单识别 → SQL Account

把一家家禽批发商 WhatsApp 订单群里的自由格式订单，转成 **SQL Account**（马来西亚常用的会计 / ERP 系统）里的 Sales Order。
LLM 负责读消息，确定性检查决定哪些草稿能信，最后由人确认整批交付，才写进系统。

> **本仓库里的所有客户、产品和订单数据都是虚构的**，按真实消息的特点仿造。真实数据不离开公司。

> 本项目在大量 AI 协助下完成，见文末「开发过程与 AI 协作」。

## 要解决的问题

每晚三个 WhatsApp 群会收到 60–90 张第二天的订单，写法五花八门：英文、马来文、中文混写，错字，
单位混用（kg / ctn / pkt / pcs / ekor），一条消息里有加单回复、staff meal 另开单、PO 号、价钱。
以前由文员一张张手动录进 SQL Account。录的时候要在约 500 个启用客户、1,100 个分店里认出是哪家，
把每行换成货号（有的客户同一个词指的是另一个货号），换算单位（ctn = 12 kg、pkt = 2 kg、按只数的行数量填 0，
件数写进品名），还要把凌晨发的当日单留给早班处理。

## 成果（真实运行，只列汇总数字）

- 第一晚实盘（2026-10-09）：经 SQL Account SDK 录入 **83 张**，每张都读回系统核对一致。
- 前 6 晚（10/09–10/15）：SDK 脚本共录入 **423 张** Sales Order，每批都由人核对并确认后才写入。
- DeepSeek 影子模式回放 5 晚（346 张人工核对过的单）：放行的草稿 **96.2%** 每个字段都对；**76.6%** 自动放行，
  其余扣下交人处理，每条都写明原因。没参与调提示词的两晚分别是 97.7% 和 96.7%。

## 怎么运作

Mac 读本机 WhatsApp 数据库 → 从历史单学客户和货号的叫法 → 为每条消息挑候选客户和历史单 → DeepSeek 输出 JSON →
确定性检查（客户必须在候选里且启用、分店存在、日期在明天以后、货号和单位用过、按只数的单不能填 kg……）→
放行的单加一份人工核对清单 → 人确认 → 公司电脑用 SDK 先预览再写入，写完读回核对 → 回传包（已录的单 + 最新主档）带回 Mac。
下一晚识别时，这些刚录进系统、核对过的单会加进历史资料，新客户、新叫法、人工改过的地方都会被用上
（这里的"学"是更新对照表和历史资料，不是重新训练模型）。n8n 每晚 22:00 定时跑，结果发到本地的微信式总控台。

架构图、设计取舍和目录说明见 [README.md](README.md)（英文）。

## 试跑 demo

```bash
python3 demo/run_demo.py
```

只需要 Python 3.10+ 标准库。会生成虚构的工作区，**原封不动地**跑生产代码，DeepSeek 的回答从 `demo/recorded/` 重放
（这些是 DeepSeek 对这批虚构数据的真实回答）。

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

## 开发过程与 AI 协作

This project was developed with extensive AI assistance. AI was responsible for much of the initial code
implementation, while I defined the requirements, directed the development, reviewed outputs, identified
issues, and guided corrections. I also validated the system against its intended workflow.

（中文大意：本项目在大量 AI 协助下完成。大部分初版代码由 AI 实现；需求由我定义，开发方向由我主导，产出由我审查，
问题由我发现并指导修正。我还按实际业务流程验收了整个系统。）

- 需求怎么拿、怎么确认（含前期人工审核、确认最终交付）：[docs/requirements-process.md](docs/requirements-process.md)
- 怎么用 AI、有哪些防护、抓到过哪些问题：[docs/ai-workflow.md](docs/ai-workflow.md)

代码注释、提示词和报表用中文写，每个主要模块开头有英文说明。

## License

MIT
