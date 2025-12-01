import asyncio
import time
from typing import Dict, Any

# 直接模拟engine.py中的关键部分进行测试
class TaskState:
    """模拟TaskState枚举"""
    RUNNING = type('TaskStateEnum', (), {'name': 'RUNNING'})()
    FAILED = type('TaskStateEnum', (), {'name': 'FAILED'})()

class MockReactiveFramework:
    """简化的框架实现，只测试重试和超时机制"""
    
    def __init__(self):
        self.state_updates = []
    
    def _emit_state_update(self, step_index: int, state: Any, data: Any):
        """模拟状态更新"""
        update = {"step_index": step_index, "state": state.name, "data": data}
        self.state_updates.append(update)
        print(f"状态更新: {update}")
    
    async def _execute_single_step(self, step: Dict, step_index: int) -> Dict[str, Any]:
        """模拟执行单个步骤"""
        processor = step.get("processor")
        if not processor:
            raise ValueError("步骤必须包含processor")
        return await processor({})
    
    def _create_step_observable(self, step: Dict, step_index: int) -> Any:
        """模拟_create_step_observable，使用简单的异步函数实现重试机制"""
        # 从step配置中获取重试和超时参数，每个step可以完全自定义这些值
        max_retries = step.get("max_retries", 3)  # 最大重试次数，默认3次
        timeout = step.get("timeout", 30000)  # 超时时间，默认30秒
        
        print(f"步骤 {step_index} 配置: max_retries={max_retries}, timeout={timeout}ms")
        
        # 这个函数模拟Observable的行为
        async def execute_with_retry():
            retries = 0
            start_time = time.time()
            
            while True:
                try:
                    # 检查超时
                    elapsed = (time.time() - start_time) * 1000  # 转换为毫秒
                    if elapsed > timeout:
                        raise asyncio.TimeoutError(f"执行超时: {elapsed:.2f}ms > {timeout}ms")
                    
                    # 执行步骤
                    result = await self._execute_single_step(step, step_index)
                    self._emit_state_update(step_index, TaskState.RUNNING, result)
                    return result
                    
                except Exception as e:
                    # 更新失败状态
                    self._emit_state_update(step_index, TaskState.FAILED, str(e))
                    
                    # 检查是否需要重试
                    if retries < max_retries:
                        retries += 1
                        print(f"步骤 {step_index} 失败，将重试 {retries}/{max_retries}...")
                        await asyncio.sleep(0.1)  # 简单延迟
                    else:
                        print(f"步骤 {step_index} 达到最大重试次数 {max_retries}")
                        raise
        
        # 返回可执行的协程对象
        return execute_with_retry

# 测试用的处理器
async def create_flaky_processor(fail_count: int = 2):
    """创建一个会失败指定次数然后成功的处理器"""
    class Counter:
        def __init__(self):
            self.count = 0
    
    counter = Counter()
    
    async def processor(ctx: Dict[str, Any]):
        counter.count += 1
        print(f"处理器被调用: 第 {counter.count} 次")
        if counter.count <= fail_count:
            raise Exception(f"模拟失败 #{counter.count}")
        return {"result": "success", "call_count": counter.count}
    
    return processor

async def timeout_processor(ctx: Dict[str, Any]):
    """模拟一个会超时的处理器"""
    print("超时处理器被调用，将休眠3秒...")
    await asyncio.sleep(3)  # 休眠3秒，对于短超时会触发超时
    return {"result": "success"}

# 主测试函数
async def run_test():
    # 创建框架实例
    framework = MockReactiveFramework()
    
    # 定义测试步骤
    steps = [
        {
            "name": "默认重试步骤",
            "processor": await create_flaky_processor(2),
        },
        {
            "name": "自定义重试步骤",
            "max_retries": 5,
            "timeout": 10000,  # 10秒
            "processor": await create_flaky_processor(3),
        },
        {
            "name": "无重试步骤",
            "max_retries": 0,
            "timeout": 2000,  # 2秒
            "processor": timeout_processor,
        }
    ]
    
    # 运行测试
    print("\n===== 开始测试 =====\n")
    
    for i, step in enumerate(steps):
        print(f"\n测试步骤 {i}: {step['name']}")
        try:
            # 获取并执行observable
            observable_fn = framework._create_step_observable(step, i)
            result = await observable_fn()
            print(f"✓ 步骤 {i} 成功: {result}")
        except Exception as e:
            print(f"✗ 步骤 {i} 失败: {str(e)}")
    
    print("\n===== 测试完成 =====\n")
    print(f"总共记录了 {len(framework.state_updates)} 个状态更新")

# 运行测试
if __name__ == "__main__":
    asyncio.run(run_test())
