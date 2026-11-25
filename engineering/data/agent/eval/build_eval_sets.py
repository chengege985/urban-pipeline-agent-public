"""生成评测集初稿：100条路由、50条有答案RAG、10条无答案。

注意：这些是开发用机器初稿，必须由项目组人工复核后才能作为对外验收口径；
不得把本文件的通过率写成“100条人工标注已达标”。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MATERIALS = [
    "V5中三方职责是什么？", "三方如何分工？", "CUV架构的原理是什么？",
    "资料里如何定义监测标准？", "三方接口字段有哪些？", "文档中如何描述数据质量？",
    "规范对数据共享有什么要求？", "为什么需要版本对照？", "V5对职责边界怎么写的？",
    "请说明接口字段的含义", "CUV中的价值配置是什么？", "资料依据在哪里？",
    "三方合作方式的分工原则", "标准文档中的定义是什么？", "解释一下原理",
    "公式里的变量含义", "内部文档怎么描述接口？", "职责边界有哪些依据？",
    "V5版本有哪些分工？", "资料中为什么强调版本？",
]
ASSESSMENT = [
    "查询 WP-016 的评分", "WP-001 的健康等级是多少？", "这段管段的材质是什么？",
    "WP-003 的管龄是多少年？", "哪些指标影响等级？", "WP-009 的风险是多少？",
    "请给出 WP-012 的健康评分", "WP-016 的评分可信度如何？", "WP-004 的指标明细",
    "WP-005 属于什么等级？", "WP-006 的管段信息", "WP-007 的评分是多少",
    "WP-008 的风险等级", "WP-010 的材质与管龄", "WP-011 的健康状况",
    "WP-013 的评分结果", "WP-014 的管段属性", "WP-015 的指标值",
    "WP-017 的健康评分", "WP-018 的风险指标",
]
SIMULATION = [
    "分析 WP-016 爆管后的通路", "医院支线故障会影响哪些服务？",
    "关闭 V-016A 阀门后哪些节点不可达？", "WP-013 隔离后的通路情况",
    "如果学校支线爆管，哪些服务会中断？", "模拟 WP-018 故障",
    "扩大隔离范围会阻断哪些服务？", "WP-014 漏损导致停供的范围",
    "双源切换时主源故障的通路", "关闭东侧联络阀的影响范围",
    "WP-015 中断后哪些服务不可达？", "商业支线爆管的隔离范围",
    "阀门 V-013A 关闭后医院是否可达？", "WP-019 故障的通路变化",
    "模拟居民区叠加故障", "隔离设置 ISO-HOSP-MIN 的通路结果",
    "WP-020 爆管后服务是否中断", "关阀后哪些服务失去通路",
    "WP-017 故障的服务影响", "停供范围如何计算？",
]
DECISION = [
    "比较三个维修方案", "预算 2 万元内推荐方案", "立即抢修还是计划更换？",
    "比较立即抢修与加密监测", "给定预算推荐维护措施", "方案 PLAN-REPAIR 的费用",
    "比较方案的费用与工期", "推荐一个维护方案", "预算不足时怎么选方案？",
    "措施的资源需求比较", "计划更换方案是否可行？", "抢修方案的优先级",
    "比较 PLAN-REPAIR 和 PLAN-MONITOR", "推荐方案并说明理由",
    "补采措施建议", "预算 4 万元的方案排序", "维护措施的成本比较",
    "方案选择的硬约束", "更换措施的预算", "抢修与更换的对比",
]
COMBINATION = [
    ("解释评分公式并查询 WP-016 的评分", ["materials", "assessment"], "assessment"),
    ("根据资料解释 WP-016 的评分", ["materials", "assessment"], "assessment"),
    ("V5中的职责与 WP-016 的健康评分", ["materials", "assessment"], "assessment"),
    ("分析 WP-016 故障影响并比较维修方案", ["simulation", "decision"], "assessment"),
    ("比较方案并解释评分依据", ["materials", "assessment", "decision"], "assessment"),
    ("查询评分后推荐维护方案", ["assessment", "decision"], "assessment"),
    ("WP-013 隔离后比较抢修方案", ["simulation", "decision"], "assessment"),
    ("为什么 WP-016 评分低，推荐什么方案？", ["materials", "assessment", "decision"], "assessment"),
    ("接口字段定义与 WP-016 管段评分", ["materials", "assessment"], "assessment"),
    ("故障通路与方案预算比较", ["simulation", "decision"], "assessment"),
]
CLARIFY = ["你好", "帮我看看", "怎么办", "这个呢", "请继续"]
UNSUPPORTED = [
    "直接关阀并下发工单", "预测未来三个月失效概率", "生成 SQL 查询数据库",
    "调用外部接口修改权重", "跳过审批直接执行",
]

RAG = {
    "KD01": [
        "供水评分复现包是什么", "复现包包含哪些文件", "评分模型版本是什么",
        "Hs 如何计算", "评分包的等级划分标准", "500 条样例在哪里",
        "如何运行评分程序", "评分包的输入表有哪些", "评分包输出哪些文件", "复现包许可是什么",
    ],
    "KD02": [
        "重大程序的数据需求", "评分程序依赖哪些输入", "动态数据窗口是什么",
        "静态数据字段有哪些", "重大程序有哪些数据问题", "缺失值怎么处理",
        "程序模块结构是什么", "重大程序的数据缺口", "评分程序限制是什么", "数据补充清单有哪些",
    ],
    "KD03": [
        "三方接口字段定义", "接口请求格式是什么", "评分接口返回什么",
        "仿真接口参数有哪些", "方案比较接口是什么", "错误码如何定义",
        "接口鉴权方式是什么", "数据版本字段是什么", "接口幂等键是什么", "接口调用限制有哪些",
    ],
    "KD04": [
        "V5 三方职责", "数字政通负责什么", "重庆大学负责什么", "重通院负责什么",
        "V5 的交付边界", "V5 中三方如何合作", "CUV 在 V5 中的位置", "项目里程碑有哪些",
        "合作分工如何安排", "V5 的风险约束是什么",
    ],
    "KD05": [
        "明镜平台与 CUV 架构", "部门架构是什么", "技术手册导读", "意图识别体系是什么",
        "CUV 架构组成", "数据中台是什么", "模型服务如何部署", "知识库建设方案",
        "权限体系如何设计", "运维流程是什么",
    ],
}
NO_ANSWER = [
    "今天天气怎么样", "如何申请护照", "晚饭吃什么", "世界杯冠军是谁", "地铁票价多少",
    "手机电池怎么保养", "股票K线图", "护照办理流程", "明天会下雨吗", "附近有什么餐厅",
]


def main():
    path = ROOT / "routing_100.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for question in MATERIALS:
            handle.write(json.dumps({"question": question, "expected_tasks": ["materials"],
                                     "expected_intent": "materials",
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")
        for question in ASSESSMENT:
            handle.write(json.dumps({"question": question, "expected_tasks": ["assessment"],
                                     "expected_intent": "assessment",
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")
        for question in SIMULATION:
            handle.write(json.dumps({"question": question, "expected_tasks": ["simulation"],
                                     "expected_intent": "simulation",
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")
        for question in DECISION:
            handle.write(json.dumps({"question": question, "expected_tasks": ["decision"],
                                     "expected_intent": "decision",
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")
        for question, tasks, intent in COMBINATION:
            handle.write(json.dumps({"question": question, "expected_tasks": tasks,
                                     "expected_intent": intent,
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")
        for question in CLARIFY:
            handle.write(json.dumps({"question": question, "expected_tasks": [],
                                     "expected_intent": "clarify",
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")
        for question in UNSUPPORTED:
            handle.write(json.dumps({"question": question, "expected_tasks": [],
                                     "expected_intent": "unsupported",
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")

    rag_path = ROOT / "rag_60.jsonl"
    with rag_path.open("w", encoding="utf-8") as handle:
        for document_id, questions in RAG.items():
            for question in questions:
                handle.write(json.dumps({"question": question,
                                         "expected_evidence_ids": [f"doc:{document_id}:"],
                                         "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")
        for question in NO_ANSWER:
            handle.write(json.dumps({"question": question, "expected_evidence_ids": [],
                                     "annotation": "draft_pending_human_review"}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
