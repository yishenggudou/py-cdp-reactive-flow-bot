#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @time    : 2024/8/21 16:32
# @author  : timger/yishenggudou
import asyncio
import logging
import sys
from pathlib import Path
import yaml
import typer
from typing import Optional
from py_cdp_reactive_flow_bot.engine import ReactiveAutomationFramework

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 创建Typer应用实例
app = typer.Typer(
    name="py-cdp-reactive-flow-bot",
    help="基于CDP的响应式自动化工作流机器人",
    add_completion=False
)

# 主命令，支持cdp endpoint和playbook参数
@app.command("run")
def run(
    playbook: Path = typer.Argument(
        ...,  # 必填参数
        help="YAML格式的playbook脚本文件路径",
        exists=True,
        readable=True,
        resolve_path=True
    ),
    cdp_endpoint: Optional[str] = typer.Option(
        None,
        "--cdp-endpoint",
        "-c",
        help="CDP服务端点地址，例如: http://localhost:9222"
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="启用调试日志模式"
    )
):
    """运行指定的playbook自动化脚本"""
    # 启用调试模式时设置日志级别为DEBUG
    if debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("py_cdp_reactive_flow_bot").setLevel(logging.DEBUG)
    
    logger.info(f"开始执行playbook: {playbook}")
    logger.debug(f"CDP端点配置: {cdp_endpoint or '使用默认Playwright浏览器'}")
    
    try:
        # 读取并解析YAML playbook文件
        playbook_config = load_playbook(playbook)
        
        # 运行主程序逻辑
        asyncio.run(run_playbook(playbook_config, cdp_endpoint))
        
        logger.info("Playbook执行完成")
        return 0
    except Exception as e:
        logger.error(f"执行失败: {str(e)}")
        return 1

def load_playbook(playbook_path: Path) -> dict:
    """加载并解析YAML格式的playbook文件"""
    logger.debug(f"加载playbook文件: {playbook_path}")
    try:
        with open(playbook_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error(f"解析YAML文件失败: {e}")
        raise ValueError(f"无效的YAML文件格式: {playbook_path}") from e
    except Exception as e:
        logger.error(f"读取playbook文件失败: {e}")
        raise IOError(f"无法读取文件: {playbook_path}") from e

async def run_playbook(playbook_config: dict, cdp_endpoint: Optional[str]):
    """运行playbook自动化脚本
    
    Args:
        playbook_config: 解析后的playbook配置
        cdp_endpoint: CDP服务端点地址，如果为None则启动新的浏览器实例
    """
    # 初始化框架
    framework = ReactiveAutomationFramework()
    
    # 创建事件用于等待执行完成
    completion_event = asyncio.Event()
    error_occurred = None
    
    try:
        # 初始化浏览器环境，传入cdp_endpoint参数
        await framework.initialize(cdp_endpoint=cdp_endpoint)
        
        # 创建并执行DSL执行器
        logger.debug("创建DSL执行器")
        executor = framework.create_dsl_executor(playbook_config)
        
        # 订阅执行状态更新
        def on_state_update(update):
            step_index = update["step_index"]
            state = update["state"]
            result = update.get("result")
            logger.info(f"步骤 #{step_index} 状态: {state.name}")
            if result:
                logger.debug(f"  结果: {result}")
        
        state_subscription = framework.state_stream.subscribe(on_state_update)
        
        # 订阅执行器，处理结果和错误
        def on_next(result):
            logger.debug(f"执行器产生结果: {result}")
        
        def on_error(error):
            nonlocal error_occurred
            logger.error(f"执行器出错: {error}")
            error_occurred = error
            completion_event.set()
        
        def on_completed():
            logger.info("执行器完成所有任务")
            completion_event.set()
        
        # 订阅执行流
        executor_subscription = executor.subscribe(
            on_next=on_next,
            on_error=on_error,
            on_completed=on_completed
        )
        
        # 等待执行完成或超时
        logger.info("开始执行playbook...")
        
        # 从playbook配置中获取超时时间，如果没有设置则使用默认值300秒
        timeout = playbook_config.get("timeout", 300)
        logger.debug(f"设置执行超时时间: {timeout}秒")
        
        try:
            # 等待执行完成，带超时保护
            await asyncio.wait_for(completion_event.wait(), timeout=timeout)
            
            # 检查是否有错误发生
            if error_occurred:
                raise error_occurred
                
        except asyncio.TimeoutError:
            logger.error(f"执行超时: 超过{timeout}秒未完成")
            raise TimeoutError(f"Playbook执行超时，超过{timeout}秒")
            
        logger.info("Playbook执行成功完成")
            
    finally:
        # 确保清理资源
        if 'state_subscription' in locals():
            state_subscription.dispose()
        if 'executor_subscription' in locals():
            executor_subscription.dispose()
        # 关闭框架资源
        await framework.close()

@app.command("version")
def version():
    """显示当前版本信息"""
    from py_cdp_reactive_flow_bot import __version__
    typer.echo(f"py-cdp-reactive-flow-bot v{__version__}")
    return 0

if __name__ == "__main__":
    sys.exit(app())