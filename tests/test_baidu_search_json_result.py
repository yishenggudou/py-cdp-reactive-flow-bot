import asyncio
import json
import pytest
from pathlib import Path
from py_cdp_reactive_flow_bot.engine import ReactiveAutomationFramework
from py_cdp_reactive_flow_bot.cli import load_playbook

# 测试用例配置
PLAYBOOK_PATH = Path(__file__).parent / "playbooks" / "baidu_search.json_result.yml"
EXPECTED_SEARCH_TERM = "CDP自动化测试"

@pytest.mark.asyncio
async def test_baidu_search_json_result():
    """测试百度搜索playbook能否正确返回JSON格式的搜索结果"""
    framework = None
    try:
        # 1. 加载playbook配置
        playbook_config = load_playbook(PLAYBOOK_PATH)
        assert playbook_config, "未能加载playbook配置文件"

        # 2. 初始化自动化框架
        framework = ReactiveAutomationFramework()
        await framework.initialize()  # 使用默认浏览器，不指定CDP端点

        # 3. 创建执行器并执行playbook
        executor = await framework.create_dsl_executor(playbook_config)
        results = []
        errors = []
        
        # 创建事件用于等待执行完成
        completion_event = asyncio.Event()

        # 订阅执行结果
        def on_next(result):
            if result.get("type") == "extraction" and "json_result" in result.get("data", {}):
                results.append(result["data"]["json_result"])

        def on_error(error):
            nonlocal errors
            errors.append(error)
            completion_event.set()

        def on_completed():
            completion_event.set()

        subscription = executor.subscribe(
            on_next=on_next,
            on_error=on_error,
            on_completed=on_completed
        )

        # 等待执行完成或超时
        try:
            await asyncio.wait_for(
                completion_event.wait(),
                timeout=playbook_config.get("timeout", 120)
            )
        except asyncio.TimeoutError:
            pytest.fail(f"执行超时，超过{playbook_config.get('timeout', 120)}秒")

        # 4. 验证执行结果
        if errors:
            pytest.fail(f"执行过程中发生错误: {errors[0]}")
            
        assert len(results) > 0, "未获取到JSON结果数据"

        # 解析JSON结果
        json_result = json.loads(results[0])

        # 验证结果结构和内容
        assert json_result.get("status") == "success", "执行状态不是成功"
        assert json_result.get("search_query") == EXPECTED_SEARCH_TERM, "搜索关键词不匹配"
        assert "extracted_results" in json_result, "结果中不包含搜索结果数据"
        assert isinstance(json_result["extracted_results"], list), "搜索结果不是列表格式"
        assert len(json_result["extracted_results"]) > 0, "未提取到任何搜索结果"

        # 验证单个结果的结构
        for result in json_result["extracted_results"]:
            assert "title" in result, "搜索结果缺少标题"
            assert "url" in result, "搜索结果缺少URL"
            assert "rank" in result, "搜索结果缺少排名信息"

        print("百度搜索JSON结果测试通过！")
        print(f"提取到{len(json_result['extracted_results'])}条搜索结果")

    finally:
        # 确保资源清理
        if 'subscription' in locals():
            subscription.dispose()
        if framework:
            await framework.close()

if __name__ == "__main__":
    pytest.main([__file__])