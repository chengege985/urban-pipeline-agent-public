"""Read the delivered catalogue only; produce the isolated GAS-M0 field matrix.

Classification is an explicit resource policy plus reviewed row overrides. It is
not a name-similarity importer and never reads an enterprise record or endpoint.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

SOURCE_NAME = "数据目录及数据项20261015160600.xlsx"
SOURCE_SHA256 = "c4d1e7f70da7be4e2b895b3e8cee9342fed4f5b0219d38060a6617f78a348c9b"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SHEET = "数据编目"
MATRIX_VERSION = "gas-field-matrix-candidate-0.1.0"

# Inclusive source row intervals. All 50 resources were read before assigning
# policy. Resource identities are preserved from the workbook, never invented.
RESOURCE_POLICIES = [
    (3, 8, "D", "人员通讯与岗位信息", "personnel_record", "首个实例不需要个人通讯或岗位信息"),
    (9, 11, "D", "城区隐患汇总", "regional_indicator", "区域隐患指数不能作为管段事实"),
    (12, 14, "D", "城区定检率汇总", "regional_indicator", "区域比例不能作为单管段检验记录"),
    (15, 17, "D", "全市站场数量", "regional_indicator", "数量不能定位气源或站场连接"),
    (18, 21, "D", "城区巡检汇总", "regional_indicator", "月巡检完成率不能替代单管段计划及完成记录"),
    (22, 24, "D", "经营企业数量", "regional_indicator", "与首个故障隔离案例无直接输入关系"),
    (25, 27, "D", "城区月供气量", "regional_indicator", "月汇总不能推断终端分时供气或停气"),
    (28, 30, "D", "全市里程", "regional_indicator", "总里程不能构造管网连边"),
    (31, 33, "D", "监测设备总数", "regional_indicator", "设备数量不是浓度读数或报警记录"),
    (34, 36, "D", "全市月供气量", "regional_indicator", "城市汇总不能分配到案例用户"),
    (37, 40, "D", "城区月抢险数", "regional_indicator", "汇总次数不含逐事件与管段的可靠关联"),
    (41, 44, "D", "城区站场数量", "regional_indicator", "数量不能定义有效气源"),
    (45, 47, "D", "城区里程", "regional_indicator", "总里程不能构造拓扑"),
    (48, 50, "D", "城区年供气量", "regional_indicator", "年累计量不能还原停气过程"),
    (51, 53, "D", "抢险变化率", "regional_indicator", "变化率不是本实例事件概率"),
    (54, 57, "D", "全市及城区风险值", "regional_indicator", "区域风险值不能下放为单管段风险"),
    (58, 61, "D", "报警抢险率", "regional_indicator", "汇总报警抢险率不是泄漏概率"),
    (62, 77, "B", "凯源隐患明细", "hazard_record", "核验隐患、处置和时间，不替代复气记录"),
    (78, 85, "B", "重燃隐患明细", "hazard_record", "核验隐患与整改情况"),
    (86, 96, "B", "凯源抢险明细", "incident_record", "核验报警、对象和处置事件"),
    (97, 113, "B", "凯源管道检验", "inspection_record", "核验资产身份、状态和已有检验结果"),
    (114, 119, "D", "培训机构备案", "training_record", "首个案例不需要培训备案信息"),
    (120, 126, "B", "凯源管线原文件索引", "source_file", "定位原始管线文件，文件地址本身不是GIS内容"),
    (127, 133, "B", "凯源场站原文件索引", "source_file", "定位站场原件，不从文件名称推断气源"),
    (134, 140, "B", "重燃南岸渝中管线索引", "source_file", "先查历史交付的管线原文件"),
    (141, 146, "B", "中燃管网原文件索引", "source_file", "先查历史交付的管网原文件"),
    (147, 154, "B", "渝川管网原文件索引", "source_file", "定位原始文件；单个X字段不能构造空间关系"),
    (155, 166, "B", "重燃站场位置及索引", "station_record", "核验站场身份及位置；是否为有效气源另确认"),
    (167, 178, "B", "重燃管网空间资产", "pipe_record", "核验管线身份和几何，坐标不证明连接"),
    (179, 195, "B", "重燃管道检验", "inspection_record", "核验资产身份、状态和已有检验结果"),
    (196, 203, "B", "重庆市站场资产", "station_record", "核验站场属性，设计范围不是实时运行边界"),
    (204, 212, "B", "管网资产字段", "pipe_record", "核验资产属性；当前目录没有可信管段主键"),
    (213, 217, "B", "站场基础字段", "station_record", "核验站场名与位置；名称并非统一稳定ID"),
    (218, 225, "D", "从业人员证照", "personal_certificate", "首个实例不需要姓名身份证或证照信息"),
    (226, 246, "D", "城市体征指标值", "regional_indicator", "未确认映射到G01—G15，不当作单管段原始指标"),
    (247, 250, "D", "全市燃气消费量", "regional_indicator", "全市日消费量不能分配到服务对象"),
    (251, 290, "B", "燃气泄露风险点设备台账", "device_record", "仅用于设备身份及位置关联；没有浓度采样读数"),
    (291, 310, "B", "管道燃气风险点位", "risk_point_record", "核验风险点描述和阈值，未声明单位与采样时点"),
    (311, 321, "B", "重燃抢险明细", "incident_record", "核验报警出警与处理，不把抢险结束当复气"),
    (322, 325, "D", "全市年供气量", "regional_indicator", "累计量不支持案例停气时段判断"),
    (326, 345, "B", "隐患体征指标定义", "indicator_definition", "仅核对规则出处，不能默认等于燃气G01—G15"),
    (346, 349, "B", "隐患体征评价定义", "indicator_definition", "核对颜色定义，不代替燃气正式五级规则"),
    (350, 366, "D", "区域隐患指标结果", "regional_indicator", "区域结果不能替代管段隐患明细"),
    (367, 369, "D", "城区抢险数量", "regional_indicator", "汇总数量不支持逐事件回放"),
    (370, 372, "D", "城区实时负荷率", "regional_indicator", "区域负荷率不是个体气源流量边界"),
    (373, 377, "D", "当日抢险体征明细", "regional_indicator", "只有区域业务分类，没有本例所需管段及动作"),
    (378, 397, "D", "当日抢险体征规则", "indicator_definition", "首期不复现城市抢险统计指标"),
    (398, 414, "D", "区域当日抢险指标", "regional_indicator", "汇总结果不能证明具体管段动作或恢复"),
    (415, 419, "D", "隐患体征明细", "regional_indicator", "区域业务分类没有可用的管段关联"),
    (420, 440, "D", "巡检完成率指标值", "regional_indicator", "已注销目录，且区域汇总不能作为单管段巡检"),
]

# Exact source-cell mappings, reviewed by resource. Null is deliberate for
# unsupported or unnecessary fields. Names preserve source context.
ROW_TARGETS = {}
def _targets(start, names):
    for row, name in enumerate(names.split(), start):
        if name != "-": ROW_TARGETS[row] = name

_targets(62, "hazard.external_record_id hazard.district hazard.operator hazard.asset_name hazard.address hazard.description hazard.level_raw hazard.type_raw hazard.pipe_type_raw hazard.proposed_remediation hazard.interim_controls - hazard.discovered_at hazard.planned_completion_at hazard.remediation_completed_raw hazard.completion_time_raw")
_targets(78, "hazard.external_record_id hazard.district hazard.operator hazard.description hazard.level_raw hazard.recorded_at hazard.remediation_status_raw hazard.completion_time_raw")
_targets(86, "incident.external_record_id incident.alarm_received_at incident.operator incident.pipe_name incident.pipe_category_raw incident.design_pressure_class_raw incident.location_text incident.description incident.discovery_method_raw incident.response_description incident.response_ended_at")
_targets(97, "inspection.external_record_id inspection.operator inspection.pipe_external_id inspection.pipe_name inspection.district inspection.pipe_level_raw inspection.length_m inspection.design_pressure_mpa inspection.nominal_diameter_mm inspection.material_raw inspection.medium_raw inspection.commissioned_date inspection.organization inspection.inspected_date inspection.conclusion_raw inspection.next_inspection_date inspection.asset_status_raw")
_targets(179, "inspection.external_record_id inspection.operator inspection.pipe_external_id inspection.pipe_name inspection.district inspection.pipe_level_raw inspection.length_m inspection.design_pressure_mpa inspection.nominal_diameter_mm inspection.material_raw inspection.medium_raw inspection.commissioned_date inspection.organization inspection.inspected_date inspection.conclusion_raw inspection.next_inspection_date inspection.asset_status_raw")
_targets(155, "station.external_record_id station.source_object_id station.name station.operator station.source_x station.source_y source_file.uploaded_at source_file.locator source_file.original_name source_file.type source_file.name source_file.size_raw")
_targets(167, "pipe.source_object_id pipe.source_geometry pipe.region pipe.source_x1 pipe.source_y1 pipe.source_x2 pipe.source_y2 pipe.name - pipe.operator pipe.length_raw pipe.source_spatial_data")
_targets(196, "station.sequence station.name station.commissioned_date station.design_pressure_mpa station.design_throughput_raw station.inlet_pressure_range_raw_mpa station.outlet_pressure_range_raw_mpa station.location_text")
_targets(204, "pipe.operator pipe.material_raw pipe.pressure_raw pipe.commissioned_date pipe.diameter_raw pipe.management_unit pipe.length_raw pipe.source_x pipe.source_y")
_targets(213, "station.operator station.commissioned_date station.source_x station.source_y station.name")
_targets(251, "device.external_id device.source_platform_code device.previous_id device.project_name - - device.builder_type device.department - device.name device.administrative_region device.installed_at device.district device.street device.community device.address - device.status_raw - - device.connectivity_status_raw device.manufacturer device.maintenance_unit - device.coordinate_reference_raw device.longitude_raw device.latitude_raw device.sharing_attribute_raw - - device.submitted_at - - device.updated_at device.thing_model_name device.thing_model_category device.thing_model_directory_id device.thing_model_directory_name device.thing_model_directory_category device.tags_raw")
_targets(291, "risk_point.name risk_point.level_raw risk_point.type_code risk_point.subtype_raw risk_point.current_value_raw risk_point.threshold_raw risk_point.threshold_description risk_point.district risk_point.street risk_point.community risk_point.grid risk_point.address risk_point.longitude_raw risk_point.latitude_raw risk_point.elevation_raw - - risk_point.response_plan_code - risk_point.external_id")
_targets(311, "incident.external_record_id incident.alarm_received_at incident.receiving_unit incident.executing_unit incident.address incident.description incident.category_raw incident.source_raw incident.dispatched_at incident.response_ended_at incident.result_raw")
_targets(326, "indicator_definition.external_id - - - indicator_definition.level_raw indicator_definition.parent_code indicator_definition.code indicator_definition.name indicator_definition.description indicator_definition.calculation_logic_raw indicator_definition.unit_text indicator_definition.business_update_frequency indicator_definition.delivery_frequency indicator_definition.data_owner indicator_definition.drilldown_dimensions indicator_definition.evaluation_rule_raw indicator_definition.evaluable_raw indicator_definition.threshold_raw indicator_definition.threshold_description indicator_definition.notes")
_targets(346, "indicator_definition.code indicator_definition.name indicator_definition.color indicator_definition.color_description")

FILE_ROWS = {120: 7, 127: 7, 134: 7, 141: 6, 147: 8}
for start, count in FILE_ROWS.items():
    if start == 141:
        _targets(start, "source_file.name source_file.locator source_file.size_raw source_file.type source_file.original_name source_file.uploaded_at")
    else:
        _targets(start, "source_file.external_id source_file.uploaded_at source_file.locator source_file.original_name source_file.type source_file.name source_file.size_raw" + (" source_file.unpaired_x_raw" if count == 8 else ""))

# A means a field concept used by the synthetic case, NOT business records
# received from this catalogue. No original catalogue field supplies node
# endpoints, valve ownership, service attachment or events in the generator.
A_ROWS = {156, 157, 159, 160, 167, 170, 171, 172, 173, 174, 197, 203, 215, 216, 217}
C_ROWS = {104, 105, 107, 186, 187, 189, 199, 200, 201, 202, 206, 208}
# Do not retain personal/internal infrastructure fields simply because the
# surrounding resource has a legitimate asset or incident purpose.
D_ROWS = {73, 175, 255, 256, 259, 267, 269, 270, 274, 279, 280, 282, 283, 306, 307, 309, 327, 328, 329}

DECLARED_UNITS = {
    103: "m", 104: "MPa", 105: "mm", 185: "m", 186: "MPa", 187: "mm",
    199: "MPa", 200: "天/万方", 201: "MPa", 202: "MPa",
}
TIME_ROWS = {
    74: "隐患发现时间", 75: "计划整改完成时间", 77: "字段名为整改完成时间；描述为数据记录时间，冲突待确认",
    83: "录入时间", 85: "字段名为整改完成时间；描述为数据记录时间，冲突待确认",
    87: "接警时间，不等于泄漏发生时间", 96: "抢险结束时间，不等于服务恢复或安全复气",
    108: "投用日期YYYY-MM-DD", 110: "定期检验日期YYYY-MM-DD", 112: "下次检验日期YYYY-MM-DD",
    121: "文件上传时间，不等于资产生效时间", 128: "文件上传时间", 135: "文件上传时间", 146: "文件上传时间", 148: "文件上传时间", 161: "文件上传时间",
    190: "投用日期YYYY-MM-DD", 192: "定期检验日期YYYY-MM-DD", 194: "下次检验日期YYYY-MM-DD",
    198: "投运时间，精度与时区未给", 207: "投运时间，精度与时区未给", 214: "投运时间，精度与时区未给",
    262: "安装时间", 281: "提交时间，不等于观测时间", 284: "修改时间，不等于观测时间",
    312: "接警时间，不等于事件起点", 319: "出警时间，不等于到场或关阀时间", 320: "抢险结束时间，不等于安全复气",
}

INDICATOR_CANDIDATES = {
    "G01": {"rows": [108, 190, 207], "kind": "candidate_asset_input", "gap": "设计寿命版本、评价日期、稳定管段映射缺失；备注已有不等于已核验原表"},
    "G02": {"rows": [106, 188, 205], "kind": "candidate_asset_input", "gap": "材质字典与稳定管段映射待确认；材质列被标主键不能直接采用"},
    "G03": {"rows": [], "kind": "not_in_catalog", "gap": "缺阴保电位mV、检测日期、管段绑定、防腐状态"},
    "G04": {"rows": [91, 102, 104, 184, 186, 206], "kind": "candidate_design_attribute", "gap": "设计压力/级别需字典；206压力单位未知，不能按MPa默认"},
    "G05": {"rows": [251, 260, 268, 275, 276, 277, 291, 295, 296], "kind": "device_or_risk_point_clue_only", "gap": "没有燃气浓度ppm采样序列、确认告警、完整30日窗口和管段映射"},
    "G06": {"rows": [104, 186, 199, 201, 202, 206], "kind": "pressure_attribute_clue_only", "gap": "缺实测压力、流量时序与工况基线；站场进出压力范围性质未确认，不能直接作为设计或运行边界"},
    "G07": {"rows": [], "kind": "not_in_catalog", "gap": "缺阀门井接入关系、积水湿度及气体监测"},
    "G08": {"rows": [62, 65, 66, 67, 68, 69, 74, 76, 77, 78, 81, 82, 84, 85, 310], "kind": "candidate_hazard_records", "gap": "有效时间窗、隐患销号、管段长度及空间关联仍须核验"},
    "G09": {"rows": [86, 87, 89, 93, 95, 96, 311, 312, 317, 320, 321], "kind": "candidate_incident_clues", "gap": "抢险未必泄漏；缺确认状态、稳定管段、起因及完整统计年窗"},
    "G10": {"rows": [168, 170, 171, 172, 173, 178], "kind": "geometry_clue_only", "gap": "缺施工许可、点位、开挖/占压状态与有效日期"},
    "G11": {"rows": [], "kind": "not_in_catalog", "gap": "缺土壤pH、电阻率、采样日期及空间关联"},
    "G12": {"rows": [], "kind": "not_in_catalog", "gap": "缺沉降序列、地灾点位等级及时间"},
    "G13": {"rows": [], "kind": "not_in_catalog", "gap": "缺起终点埋深及道路交通荷载；目录管径不替代覆土"},
    "G14": {"rows": [18, 420, 428, 435, 436, 437], "kind": "aggregate_clue_only", "gap": "区域比例不能替代单管段计划、工单、完成及闭环；420–440已注销"},
    "G15": {"rows": [], "kind": "not_in_catalog", "gap": "缺阀门作用管段/节点、状态、远控与演练时间；不得由坐标推断"},
}


def read_catalog(path: Path) -> list[dict]:
    if hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("source hash differs from reviewed catalogue; review mapping before rebuild")
    with ZipFile(path) as z:
        strings = ["".join(e.itertext()) for e in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        names = [x.attrib["name"] for x in workbook.findall("s:sheets/s:sheet", NS)]
        if names != [SHEET]:
            raise ValueError(f"unexpected sheets {names}")
        records = []
        for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall("s:sheetData/s:row", NS):
            number = int(row.attrib["r"])
            cells = {}
            for cell in row.findall("s:c", NS):
                v = cell.find("s:v", NS)
                value = v.text if v is not None else ""
                if cell.attrib.get("t") == "s" and value:
                    value = strings[int(value)]
                if value:
                    column = "".join(c for c in cell.attrib["r"] if c.isalpha())
                    cells[column] = value
            if number > 2 and cells.get("A") and cells.get("Q"):
                records.append({"row": number, "cells": cells})
    if [r["row"] for r in records] != list(range(3, 441)):
        raise ValueError("expected exactly the 438 reviewed catalogue rows, not formatted empty rows")
    if len({r["cells"]["B"] for r in records}) != 50:
        raise ValueError("resource count changed")
    return records


def policy_for(row: int):
    matches = [p for p in RESOURCE_POLICIES if p[0] <= row <= p[1]]
    if len(matches) != 1:
        raise ValueError(f"policy coverage invalid at row {row}")
    return matches[0]


def field_record(record: dict, pk_fields: dict) -> dict:
    row, c = record["row"], record["cells"]
    _, _, category, group, kind, purpose = policy_for(row)
    if row in {201, 202}:
        purpose = "核验站场进出压力范围原文；范围性质未确认，不直接作为设计或运行边界"
    if row in A_ROWS: category = "A"
    if row in C_ROWS: category = "C"
    if row in D_ROWS: category = "D"
    target = ROW_TARGETS.get(row) if category != "D" else None
    notes = []
    if category == "A":
        notes.append("合成A无需对方交真实记录；仅有字段概念对应，值取自HTML合成结构/我方显式fixture，不读目录作业务值")
    if category == "C":
        notes.append("专业模型启动条件或设计属性，当前不计算气体流动/压力/扩散；不能沿用供水水力单位或边界")
    if category == "D":
        notes.append("本阶段不索取、不导入；保留目录证据，不自动加入知识库或外发")
    if row in {77, 85}: notes.append("字段名称与字段描述冲突：整改完成时间/数据记录时间；不可直接映射真实完成时刻")
    if row == 200: notes.append("单位原文为天/万方，量纲和标况不明确；禁止擅自改为万m³/天")
    if row in {201, 202}: notes.append("原目录仅声明进站/出站压力范围及MPa；范围性质未确认，不擅加设计、运行或实测含义")
    if row in {96, 320}: notes.append("抢险结束仅可标该作业结束，不能写成停气结束或安全复气")
    if row in {175, 205}: notes.append("原目录存在含义不明或不合理主键声明，原样保留并要求校验")
    if row in {167, 174, 156, 197, 217, 205, 125, 132, 139, 141, 152, 165}:
        notes.append("目录主键/名称/对象ID声明不等于跨表稳定资产ID；须另建版本化ID映射")
    if row in {159, 160, 168, 170, 171, 172, 173, 178, 211, 212, 215, 216, 275, 276, 277, 303, 304, 305}:
        notes.append("坐标/几何仅用于定位；不能依据距离或画线推断端点连接、阀门作用边或供气方向")
    if c.get("D") == "已注销": notes.append("目录已注销：取得有效继承资源及授权前不可使用")
    if kind == "device_record": notes.append("设备台账不是浓度/压力/流量观测；联网状态不能替代实际读数")
    if kind == "risk_point_record": notes.append("当前值和阈值的指标、单位、时点未知，不能直接作为G05/G06评分输入")
    if kind == "indicator_definition": notes.append("城市体征指标编码尚无与G01—G15的权威映射")
    is_time = row in TIME_ROWS
    unit = DECLARED_UNITS.get(row)
    unit_status = "source_explicit" if unit else "not_declared"
    if is_time:
        unit = "YYYY-MM-DD" if "YYYY-MM-DD" in TIME_ROWS[row] else None
        unit_status = "date_format_declared" if unit else "time_precision_timezone_not_declared"
    elif target and any(target.endswith(s) for s in ("_id", ".name", ".operator", ".description", ".code", ".type", ".locator", ".original_name")):
        unit, unit_status = None, "identifier_or_text_no_physical_unit"
    if category == "D":
        required = "本阶段不需要"
    elif category == "A":
        required = "合成对象身份/显示概念按fixture需要；原目录字段本身不是本例运行必填，缺失不从外部补造"
    elif category == "C":
        required = "仅在对应专业模型启动且合同确认后条件必填；首个图通路案例不要求"
    else:
        required = "后续使用该类真实资料时条件必填；按现成记录提供，缺项如实说明"
    owner = "目录登记提供方协调实际燃气运营/权属单位；双方确认具体持有人"
    if kind == "source_file": owner = "目录登记提供方及对应燃气企业/GIS资料持有人；先查历史交付"
    elif kind in {"inspection_record", "pipe_record", "station_record"}: owner = "对应燃气运营/权属单位的资产或运行管理人员；数字政通协助联系，目录提供方另列"
    elif kind in {"hazard_record", "incident_record", "risk_point_record"}: owner = "对应燃气企业安全、抢修或风险台账持有人；不要求其计算模型结果"
    elif kind == "device_record": owner = "设备平台/运维单位与燃气运营方确认映射；目录不证明已获得接口权限"
    elif kind == "indicator_definition": owner = "指标定义及评价责任方确认；不自动等于燃气评分负责人"
    if category == "A": owner = "本例值由重通院开发团队从HTML构建；真实扩展由对应运营/权属单位确认"
    if category == "D": owner = "本阶段不索取；原目录提供方仅作为来源记录"
    return {
        "field_key": f"{c['B']}:{SHEET}!Q{row}",
        "source_file": SOURCE_NAME, "source_sha256": SOURCE_SHA256, "source_sheet": SHEET,
        "source_row": row, "source_name_cell": f"Q{row}", "source_code_cell": f"R{row}",
        "resource_id": c["B"], "resource_name": c["A"], "catalog_provider": c.get("C"),
        "catalog_status": c.get("D"), "catalog_collection_status": c.get("E"),
        "field_name": c["Q"], "field_code": c.get("R"), "field_description": c.get("AG", ""),
        "source_data_type": c.get("U"), "source_nullable": c.get("AA"),
        "source_primary_key_declared": c.get("Y") == "是",
        "source_sensitivity": c.get("Z"), "resource_sensitivity": c.get("M"),
        "sharing_attribute": c.get("AB"), "sharing_conditions": c.get("AC"),
        "open_attribute": c.get("AD"), "open_conditions": c.get("AE"),
        "primary_category": category, "category_meaning": {"A": "合成案例字段概念对照", "B": "后续真实业务核验", "C": "专业模型启动条件", "D": "本阶段不需要"}[category],
        "resource_review_group": group, "target_field": target,
        "mapping_status": "candidate_requires_identity_and_semantic_confirmation" if target else "not_mapped_in_m0",
        "mapping_contract_scope": "future_adapter_concept_only_not_registered" if target else "out_of_scope_not_mapped",
        "unit": unit, "unit_status": unit_status,
        "object_kind": kind, "object_id": {"catalog_primary_key_declarations": pk_fields[c["B"]], "target_id_status": "unverified_cross_table_mapping", "requirement": "保留原ID及其来源；另建立稳定对象ID，禁止以字段相似、名称相同或坐标邻近自动连接"},
        "time_attribute": TIME_ROWS.get(row, "本字段不是已确认事件时点；所属记录生效时点/采样窗需另提供"),
        "business_use": purpose if category != "D" else "暂不进入燃气M0输入；" + purpose,
        "requiredness": required,
        "current_real_records": "未确认取得；当前仅持有字段目录，既有原表先内部查交付",
        "has_verified_real_records": False,
        "responsible_party": owner,
        "secondary_uses": "压力范围含义需业务确认；不能直接作为设计上限、运行上下限或实测值" if row in {201, 202} else ("真实扩展后可作资产核验；当前值由我方合成" if category == "A" else ("设计属性也可供评分口径核验，不能单独证明运行工况" if category == "C" else "不得超出所列对象粒度和确认用途")),
        "notes": notes + (["目标字段为待审Adapter字段概念，不是当前已注册或已实现的业务导入映射"] if target else []),
        "source_cells": c,
    }


def minimum_inputs() -> dict:
    rows = [
        ("nodes", "Node.id", "无量纲字符串", "原型nodes[].id", "构成可引用节点", "必须", "无显式Node ID目录字段；不能把空间点自动并为节点"),
        ("nodes", "Node.schematic_position", "原型示意坐标，不是GIS米/经纬度", "原型nodes[].x/y", "示意图展示", "显示时必须", "目录GIS坐标仅概念对照，禁止反推拓扑"),
        ("pipes", "Pipe.id/Edge.id", "无量纲字符串", "原型edges[].id", "指定故障并引用图边", "必须", "目录对象ID/线名需真实ID映射，不能混作Node或Valve ID"),
        ("pipes", "Edge.from_node_id/to_node_id", "无量纲字符串", "原型edges[].a/b", "定义明确连边", "必须", "438字段没有明确起终节点ID；X1/Y1/X2/Y2不构成该关系"),
        ("pipes", "Edge.direction_semantics", "枚举", "原型connected/disconnected使用双向邻接", "明示原型无向连通，不推断实际气流上下游", "必须", "实际方向、调压和止回约束缺失"),
        ("valves", "Valve.id/node_id/action_scope", "字符串及显式ID列表", "原型type=valve节点与unitValves", "明确阀门在节点阻断语义", "隔离例必须", "438字段无阀门唯一ID及作用管段/节点映射"),
        ("sources", "Source.id/node_id/enabled", "字符串/布尔", "原型type=source的节点", "多源通路起点", "必须", "站场名不自动等于可用气源；缺站场—节点及状态映射"),
        ("services", "Service.id/node_ids/supply_rule", "字符串/ID列表/any或all", "原型节点用户与设施归属；新增显式synthetic合同", "识别潜在停气对象", "必须", "438字段没有服务对象、接入节点及any/all语义"),
        ("services", "Consumer.counts", "户/对象，按原型字段标记", "原型hh/com/fac", "展示潜在影响规模，不估计伤亡", "需展示数量时必须", "没有真实接入户数，不能由人口密度/距离推断"),
        ("scenarios", "Scenario.id/fault_edge_id", "字符串", "原型选中边；我方固定一个合成情景", "明确本例故障对象", "必须", "抢险管线名称不是权威pipe_id"),
        ("isolation", "IsolationPlan.id/valve_ids", "字符串/ID列表", "原型隔离单元纯函数，保存候选结果", "回放给定模拟隔离，不下发操作", "必须", "真实阀操作方案与批准依据缺失"),
        ("events", "Event.id/type/target_id/time", "显式时间单位与事件类型", "我方新建synthetic事件，不冒充原型历史", "事件排序与部分结果", "出现事件时必须", "现有接警/结束字段不能替代故障、关阀、检漏、放行或复气"),
        ("identity", "network_id/case_id/content_hash", "版本化合成ID与SHA-256", "我方构建产物", "绑定本地网络与案例，保存输入内容哈希", "必须", "本地合成身份不是平台dataset/snapshot/run身份"),
        ("platform_identity", "dataset_id/snapshot_id/run_id", "服务端签发的版本化ID", "待M1平台可信身份和持久化上下文签发", "后续平台绑定、授权和只读报告", "M1接入时必须，M0不生成", "不由HTML、客户端字段或本地候选代签，不向业务人员索要"),
        ("classification", "data_mode/provenance", "synthetic/candidate及源文件SHA", "我方构建产物", "防止演示被误认真实", "必须", "必须保留服务端可信分类；输入标签不构成真实授权"),
        ("status", "result.status/limitations", "枚举与文字", "我方适配器", "区分unknown/partial/unsupported", "必须", "无压力/扩散模型就不生成对应物理值"),
    ]
    paths = {
        "Node.id": ["network.nodes[].node_id"],
        "Node.schematic_position": ["network.nodes[].layout.x", "network.nodes[].layout.y", "network.nodes[].layout.unit", "network.nodes[].layout.coordinate_system"],
        "Pipe.id/Edge.id": ["network.pipes[].pipe_id", "network.pipes[].edge_id", "network.edges[].edge_id", "network.edges[].pipe_id"],
        "Edge.from_node_id/to_node_id": ["network.edges[].from_node_id", "network.edges[].to_node_id"],
        "Edge.direction_semantics": ["network.edges[].direction"],
        "Valve.id/node_id/action_scope": ["network.valves[].valve_id", "network.valves[].node_id", "network.valves[].cut_semantics", "network.valves[].incident_edge_ids"],
        "Source.id/node_id/enabled": ["network.sources[].source_id", "network.sources[].node_id", "network.sources[].enabled"],
        "Service.id/node_ids/supply_rule": ["network.services[].service_id", "network.services[].consumer_id", "network.services[].node_ids", "network.services[].supply_rule"],
        "Consumer.counts": ["network.consumers[].consumer_id", "network.consumers[].node_id", "network.consumers[].count", "network.consumers[].count_unit"],
        "Scenario.id/fault_edge_id": ["case.scenario.scenario_id", "case.scenario.fault_edge_ids"],
        "IsolationPlan.id/valve_ids": ["case.isolation_plan.plan_id", "case.isolation_plan.scenario_id", "case.isolation_plan.closed_valve_ids", "case.isolation_plan.disabled_edge_ids", "case.isolation_plan.semantics"],
        "Event.id/type/target_id/time": ["case.events[].event_id", "case.events[].kind", "case.events[].origin", "case.events[].time_hours", "case.events[].fault_edge_ids", "case.events[].closed_valve_ids", "case.events[].disabled_edge_ids"],
        "network_id/case_id/content_hash": ["network.network_id", "case.network_id", "case.case_id", "result.evidence.network_content_sha256", "result.evidence.case_content_sha256"],
        "dataset_id/snapshot_id/run_id": [],
        "data_mode/provenance": ["network.data_mode", "network.industry", "network.source_provenance.sha256", "network.source_provenance.construction_block_sha256", "case.data_mode"],
        "result.status/limitations": ["result.status", "result.semantic", "result.validation_status", "result.metrics.actual_gas_restoration_hours.status", "result.metrics.pressure.status", "result.safety.simulation_only"],
    }
    inputs = []
    for e,t,u,s,p,req,gap in rows:
        inputs.append({"entity": e, "target_field": t, "target_field_kind": "business_concept_not_literal_json_key",
                       "m0_json_paths": paths[t], "contract_scope": "planned_platform" if e == "platform_identity" else "current_fixture",
                       "unit": u, "synthetic_source": s, "purpose": p, "requiredness": req,
                       "catalog_gap": gap, "real_record_status": "未确认取得",
                       "responsible_party": "合成由重通院开发团队构建；真实对象关系由燃气运营/权属单位确认",
                       "time_attribute": "对象需版本/生效时点；事件需显式时点和单位"})
    return {
        "version": MATRIX_VERSION, "data_mode": "synthetic", "approval_status": "candidate_pending_gas_m0_review",
        "catalog_supplies_business_values": False,
        "category_definitions": {"A": "首个合成案例实际输入由HTML与我方fixture产生；438行中的A只是相应字段概念对照，不要求外部交付", "B": "后续真实核验时查旧交付并接收现成原表/图层/工单", "C": "专业模型单独启动并确认合同后才需，不迁移供水物理假设", "D": "本阶段不索取、不导入"},
        "inputs": inputs,
        "m0_path_contract": {"network": "data/gas54_network.json", "case": "data/gas54_case.json", "result": "data/gas54_result.json", "array_marker": "[]表示当前集合中每个记录，路径只描述现有候选格式，不授予数据来源可信性", "identity_boundary": "network_id/case_id为合成命名空间；dataset_id/snapshot_id/run_id在M0不存在，待M1服务端签发", "provenance_boundary": "SHA字段格式或非空source_ref不构成来源认证；本例来源靠固定原HTML和提取重建核对，真实入口另需可信平台分类授权"},
        "professional_model_gates": [
            {"model": "气体管网压力流量", "inputs": ["气体组分/温度/状态方程及标况", "内径/粗糙度/长度/高程", "气源与用户边界", "调压/止回/阀门机理", "同步压力流量实测与校准案例"], "owner": "专业模型负责人及燃气运营调度方", "status": "not_available_in_catalog_as_complete_inputs"},
            {"model": "泄漏扩散及地下积聚", "inputs": ["泄漏口及释放工况", "气体组成", "土壤/通道/地下空间几何", "通风/风场边界", "浓度观测及验证案例"], "owner": "专业模型负责人、运营监测及地下空间资料持有方", "status": "not_available_in_catalog_as_complete_inputs"},
            {"model": "事故后果与安全复气", "inputs": ["确认的事故情景与后果模型", "人口及设施暴露依据", "检漏/置换/放行判据及记录", "适用范围和不确定性说明"], "owner": "燃气安全业务负责人及专业模型团队", "status": "requires_separate_business_confirmation"},
        ],
        "request_rule": "片区、时间段和数量按对方已有资料接收；已交资料不重索；截图或备注已有的原表先内部查交付；允许原系统Excel、GIS、工单和说明，不要求对方编写JSON或计算模型结果。",
    }


def build(source: Path, out: Path) -> dict:
    raw = read_catalog(source)
    pk = defaultdict(list)
    for r in raw:
        c = r["cells"]
        if c.get("Y") == "是": pk[c["B"]].append({"field": c["Q"], "code": c.get("R"), "cell": f"Q{r['row']}"})
    fields = [field_record(r, pk) for r in raw]
    resources = []
    by_resource = defaultdict(list)
    for f in fields: by_resource[f["resource_id"]].append(f)
    for rid, items in by_resource.items():
        resources.append({"resource_id": rid, "resource_name": items[0]["resource_name"], "source_rows": [f["source_row"] for f in items], "field_count": len(items), "categories": dict(Counter(f["primary_category"] for f in items)), "catalog_provider": items[0]["catalog_provider"], "catalog_status": items[0]["catalog_status"]})
    catalog = {"version": MATRIX_VERSION, "source_file": SOURCE_NAME, "source_sha256": SOURCE_SHA256, "source_sheet": SHEET,
               "resource_count": len(resources), "field_count": len(fields), "category_counts": dict(sorted(Counter(f["primary_category"] for f in fields).items())),
               "status_counts": dict(Counter(f["catalog_status"] for f in fields)), "classification_method": "50 reviewed resource policies plus explicit source-row mappings and exceptions; not fuzzy keyword import",
               "has_verified_real_records": False, "no_network_or_database_read": True, "resources": resources, "indicator_source_candidates": INDICATOR_CANDIDATES, "fields": fields}
    # Reserve a new output directory; existing files/directories/links are never overwritten.
    out.mkdir(parents=True, exist_ok=False)
    for name, value in [("gas_catalog_438.json", catalog), ("gas_minimum_inputs.json", minimum_inputs())]:
        # Preserve the delivered CRLF contract on both Linux and Windows.
        with (out/name).open("x", encoding="utf-8", newline="\r\n") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    columns = ["字段行号", "分类", "分类含义", "来源文件", "工作表", "名称单元格", "资源ID", "资源名称", "原字段名称", "原字段代码", "原字段描述", "目标字段", "映射状态", "单位", "单位状态", "对象类型", "对象ID依据", "时间属性", "业务用途", "必填性", "当前真实记录", "目录提供方", "建议责任方", "目录状态", "目录字段可空", "敏感级别", "共享属性", "共享条件", "开放属性", "开放条件", "其他用途", "限制"]
    with (out/"gas_field_matrix.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader()
        for f in fields:
            values = [f["source_row"],f["primary_category"],f["category_meaning"],f["source_file"],f["source_sheet"],f["source_name_cell"],f["resource_id"],f["resource_name"],f["field_name"],f["field_code"],f["field_description"],f["target_field"] or "暂不映射",f["mapping_status"],f["unit"] or "未声明/不适用（见状态）",f["unit_status"],f["object_kind"],json.dumps(f["object_id"],ensure_ascii=False),f["time_attribute"],f["business_use"],f["requiredness"],f["current_real_records"],f["catalog_provider"],f["responsible_party"],f["catalog_status"],f["source_nullable"],f["source_sensitivity"],f["sharing_attribute"],f["sharing_conditions"],f["open_attribute"],f["open_conditions"],f["secondary_uses"],"；".join(f["notes"])]
            writer.writerow(dict(zip(columns,values)))
    return {k: catalog[k] for k in ["resource_count", "field_count", "category_counts", "status_counts", "source_sha256"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
