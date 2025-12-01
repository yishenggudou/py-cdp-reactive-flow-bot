import asyncio
import time
from typing import Dict, Any
from py_cdp_reactive_flow_bot.engine import ReactiveAutomationFramework, TaskState

# 模拟一个会失败然后成功的步骤处理器
def create_flaky_processor(fail_count: int = 2):
    """创建一个处理器，在前fail_count次调用时失败，之后成功"""
    class Counter:
        def __init__(self):
            self.count = 0
    
    counter = Counter()
    
    async def processor(ctx: Dict[str, Any]) -> Dict[str, Any]:
        counter.count += 1
        print(f"处理器被调用: 第 {counter.count} 次")
        if counter.count <= fail_count:
            raise Exception(f"模拟失败 #{counter.count}")
        return {"result": "success", "call_count": counter.count}
    
    return processor

# 模拟一个会超时的处理器
async def timeout_processor(ctx: Dict[str, Any]) -> Dict[str, Any]:
    print("超时处理器被调用，将休眠10秒...")
    await asyncio.sleep(10)  # 休眠10秒，应该会触发超时
    return {"result": "success"}

# 定义工作流步骤
workflow_steps = [
    {
        "id": "step1",
        "name": "默认重试步骤",  # 使用默认重试(3次)和超时(30秒)
        "processor": create_flaky_processor(2),  # 在前2次失败
    },
    {
        "id": "step2",
        "name": "自定义重试步骤",
        "max_retries": 5,  # 自定义重试次数为5
        "timeout": 15000,  # 自定义超时时间为15秒
        "processor": create_flaky_processor(3),  # 在前3次失败
    },
    {
        "id": "step3",
        "name": "无重试步骤",
        "max_retries": 0,  # 关闭重试
        "timeout": 5000,  # 自定义超时时间为5秒
        "processor": timeout_processor,  # 应该会触发超时
    },
    {
        "id": "step4",
        "name": "正常成功步骤",
        "max_retries": 2,
        "timeout": 10000,
        "processor": lambda ctx: {"result": "immediate success"},
    }
]

# 状态更新监听器
async def state_update_listener(step_index: int, state: TaskState, data: Any):
    step = workflow_steps[step_index]
    print(f"状态更新: 步骤 '{step['name']}' ({step_index}) -> {state.name}: {data}")

# 主函数
async def main():
    # 创建框架实例
    framework = ReactiveAutomationFramework(
        workflow_steps,
        state_update_listener=state_update_listener
    )
    
    # 执行工作流
    print("开始执行工作流...")
    start_time = time.time()
    
    try:
        result = await framework.execute_workflow()
        print(f"工作流执行完成: {result}")
    except Exception as e:
        print(f"工作流执行失败: {str(e)}")
    
    print(f"总执行时间: {time.time() - start_time:.2f} 秒")

# 运行主函数
if __name__ == "__main__":
    asyncio.run(main())
