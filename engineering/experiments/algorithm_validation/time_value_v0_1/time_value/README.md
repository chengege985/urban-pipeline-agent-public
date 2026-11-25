# 时间价值算例

数据synthetic_fixture、模型candidate。结果由`src/algorithm_validation/run_time_value.py`实际生成，`results.json`包含完整请求、结果、源码哈希和读取的水力结果哈希。

基础情景借用179工程S01的6h抢修、1h备用、4m³/h需水和5m³/h容量；S01原SHA256为`12022d6fd60f15a1b60c35943b2db87eff5e95060b5599588a85125529f9942b`。100m³库存、已验证的接入、明确可送达流量、计划停止时刻是本轮新增的合成假设。原S01没有提供水力求解的实际供水量，本目录不能作为其实际供水验证。

同文件的水力接入使用另一套5节点基准的H（7.2m³/h），通过代码显式映射到`fixture-hydraulic-H`；没有将它与S01医院或原500条管段自动关联。0—1h断供、1—6h保持备用稳态、6—8h保持正常稳态是分段恒定假设，未运行延时水力或水质仿真。

重复运行：在项目根目录执行`.venv-agent/Scripts/python.exe -B -m src.algorithm_validation.run_time_value`。先重跑水力入口可刷新实际稳态结果。测试中10m³/h样例用于独立整数手算，并非S01需水改版。
