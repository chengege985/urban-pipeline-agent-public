"""跨层错误类型，避免入口为捕获异常而提前导入pandas等重依赖。"""


class InputIssues(ValueError):
    """输入不满足安全计算条件；issues为可逐条定位的结构化列表。"""

    def __init__(self, issues):
        super().__init__("输入不满足安全计算条件")
        self.issues = issues
