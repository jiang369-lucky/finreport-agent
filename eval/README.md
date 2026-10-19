# 中文评测

- verified_facts.jsonl：20 道经原始摘录核对的数值参考，包含来源物理页码。题量小，不代表真实用户分布。
- tasks_100.jsonl：100 道任务草稿，开发 60 题、留出 40 题，按公司划分。答案金标尚待逐项审核，不能声称是已完整标注的金融基准。
- results_retrieval_dev.json：15 道数值参考开发题的证据页检索结果。
- results_retrieval_test.json：5 道数值参考留出题的证据页检索结果，覆盖 4 家公司。

运行：

    .venv\Scripts\python.exe -m scripts.evaluate --mode retrieval --limit 20
    .venv\Scripts\python.exe -m scripts.evaluate --mode rag --limit 5
    .venv\Scripts\python.exe -m scripts.evaluate --mode agent --limit 5

后两条使用用户自己的通义千问密钥，会产生模型调用。可用 --split dev 或 --split test 筛选，用 --dataset eval/tasks_100.jsonl 选择任务草稿。

证据页命中率、答案中的数值出现和页码引用只作为诊断信号。模型答案语义正确性、解释是否有依据、拒答是否合理需要人工审核。当前检索结果不能转述为 Agent 答案准确率。

对比时使用同一固定模型、同一语料、同一问题与日期边界。新增评测任务应优先覆盖多页推理、重述、单位混用、合并口径、缺失资料和上下文压缩。
