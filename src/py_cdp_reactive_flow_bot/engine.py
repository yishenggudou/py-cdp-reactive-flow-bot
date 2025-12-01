#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @time    : 2024/8/21 16:32
# @author  : timger/yishenggudou
import asyncio
import re
from typing import Any, Dict, List, Callable, Optional
from enum import Enum
from dataclasses import dataclass
from rx import operators as ops
from rx.core import Observable
from rx.subject import Subject
from rx.scheduler.eventloop import AsyncIOScheduler
from playwright.async_api import async_playwright, Page, BrowserContext
import logging
logger = logging.getLogger(__name__)

class TaskState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"

@dataclass
class ExecutionContext:
    page: Page
    data: Dict[str, Any]
    state: Dict[str, Any]
    last_result: Any = None

@dataclass
class PatternMatch:
    pattern_type: str  # "url", "content", "element", "custom"
    pattern: str  # 正则表达式、CSS选择器、或自定义匹配条件
    timeout: int = 5000

class ReactiveAutomationFramework:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.event_stream = Subject()
        self.state_stream = Subject()
        self.current_context: Optional[ExecutionContext] = None
        self.scheduler = AsyncIOScheduler()
        
    async def initialize(self, cdp_endpoint: Optional[str] = None):
        """初始化浏览器环境
        
        Args:
            cdp_endpoint: 可选的CDP服务端点地址，如果提供则连接到现有浏览器实例，
                         否则启动一个新的浏览器实例
        """
        logger.debug("开始初始化浏览器环境...")
        self.playwright = await async_playwright().start()
        logger.debug("Playwright启动成功")
        
        if cdp_endpoint:
            # 连接到现有的CDP端点
            logger.debug(f"连接到CDP端点: {cdp_endpoint}")
            self.browser = await self.playwright.chromium.connect_over_cdp(cdp_endpoint)
            logger.debug("成功连接到CDP端点")
            
            # 获取第一个上下文，如果没有则创建新的
            contexts = self.browser.contexts
            if contexts:
                self.context = contexts[0]
                logger.debug("使用现有浏览器上下文")
            else:
                self.context = await self.browser.new_context()
                logger.debug("创建新的浏览器上下文")
        else:
            # 启动新的浏览器实例
            logger.debug("启动新的Chrome浏览器实例")
            self.browser = await self.playwright.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
            logger.debug("Chrome浏览器启动成功")
            
            self.context = await self.browser.new_context()
            logger.debug("浏览器上下文创建成功")
        
    def create_dsl_executor(self, dsl_config: Dict) -> Observable:
        """基于DSL配置创建可观察执行流"""
        logger.debug(f"创建DSL执行器，配置: {dsl_config.get('name', 'unnamed')}")
        # 确保浏览器已初始化
        if not self.browser:
            logger.error("创建执行器失败：浏览器未初始化")
            raise RuntimeError("Browser not initialized. Call initialize() first.")
        
        return Observable.create(lambda observer: self._execute_dsl_pipeline(observer, dsl_config))
    
    async def _execute_dsl_pipeline(self, observer, dsl_config: Dict):
        """执行DSL任务管道"""
        try:
            page = await self.context.new_page()
            self.current_context = ExecutionContext(
                page=page,
                data=dsl_config.get("initial_data", {}),
                state={"current_step": 0, "retry_count": 0}
            )
            
            # 创建主执行流
            execution_flow = self._build_execution_flow(dsl_config["steps"])
            
            # 订阅执行流
            subscription = execution_flow.subscribe(
                on_next=lambda result: self._handle_step_result(result, observer),
                on_error=lambda error: observer.on_error(error),
                on_completed=lambda: observer.on_completed(),
                scheduler=self.scheduler
            )
            
            # 启动执行
            await self._start_execution(execution_flow)
            
        except Exception as e:
            observer.on_error(e)
    
    def _build_execution_flow(self, steps: List[Dict]) -> Observable:
        """构建基于模式匹配的执行流"""
        step_observables = []
        
        for i, step in enumerate(steps):
            step_obs = self._create_step_observable(step, i)
            step_observables.append(step_obs)
        
        # 使用 concat 按顺序执行，但允许基于模式匹配的动态路由
        return Observable.concat(*step_observables).pipe(
            ops.flat_map(lambda result: self._pattern_based_routing(result, steps))
        )
    
    def _create_step_observable(self, step: Dict, step_index: int) -> Observable:
        """为每个步骤创建可观察对象，支持每个step自定义重试和超时设置
        
        支持不限超时的情况：当timeout设置为0或None时，表示不应用超时控制，
        适用于需要等待任意长时间直到条件满足的场景，类似于Playwright的wait_for_selector无超时模式。
        """
        logger.debug(f"创建步骤可观察对象 #{step_index}: {step.get('type', 'unknown')}，名称: {step.get('name', 'unnamed')}")
        # 从step配置中获取重试和超时参数，每个step可以完全自定义这些值
        max_retries = step.get("max_retries", 3)  # 最大重试次数，默认3次
        timeout = step.get("timeout", 30000)  # 超时时间，默认30秒，设置为0或None表示不限超时
        
        # 日志记录配置，特别标记不限超时的情况
        timeout_info = "无超时限制" if timeout == 0 or timeout is None else f"{timeout}ms"
        logger.debug(f"  步骤配置: max_retries={max_retries}, timeout={timeout_info}")
        
        # 创建一个包装器，在重试时更新状态
        def execute_with_retry_state_update():
            try:
                return self._execute_single_step(step, step_index)
            except Exception as e:
                # 更新状态为失败
                self._emit_state_update(step_index, TaskState.FAILED, str(e))
                # 重新抛出异常以触发重试机制
                raise
        
        # 创建步骤可观察对象
        step_observable = Observable.defer(execute_with_retry_state_update)
        
        # 应用超时控制（仅当timeout不为0或None时）
        if timeout != 0 and timeout is not None:
            step_observable = step_observable.pipe(
                ops.timeout(timeout)
            )
        else:
            logger.debug(f"  步骤 #{step_index}: 已启用无超时限制模式")
        
        # 应用自定义重试机制（如果配置了重试）
        if max_retries > 0:
            # 使用RxPy的retry操作符并传入最大重试次数
            step_observable = step_observable.pipe(
                ops.retry(max_retries)
            )
        
        # 添加状态更新监听器
        step_observable = step_observable.pipe(
            ops.do_action(
                on_next=lambda result: self._emit_state_update(step_index, TaskState.RUNNING, result)
            )
        )
        
        return step_observable
    
    async def _execute_single_step(self, step: Dict, step_index: int) -> Dict[str, Any]:
        """执行单个步骤"""
        step_type = step["type"]
        step_name = step.get("name", f"step_{step_index}")
        logger.debug(f"执行步骤 #{step_index}: {step_type} (名称: {step_name})")
        
        context = self.current_context
        
        self._emit_state_update(step_index, TaskState.RUNNING)
        
        try:
            if step_type == "navigate":
                url = step["url"]
                logger.debug(f"  导航到: {url}")
                await context.page.goto(url)
                logger.debug(f"  导航成功")
                result = {"type": "navigation", "url": url, "status": "success"}
                
            elif step_type == "click":
                selector = step["selector"]
                logger.debug(f"  点击元素: {selector}")
                await context.page.click(selector)
                logger.debug(f"  点击成功")
                result = {"type": "click", "selector": selector, "status": "success"}
                
            elif step_type == "type":
                selector = step["selector"]
                text = step["text"]
                logger.debug(f"  在元素 {selector} 中输入文本: {text[:20]}{'...' if len(text) > 20 else ''}")
                await context.page.fill(selector, text)
                logger.debug(f"  文本输入成功")
                result = {"type": "type", "selector": selector, "text": text, "status": "success"}
                
            elif step_type == "wait_for_pattern":
                pattern = step["pattern"]
                logger.debug(f"  等待模式匹配: {pattern['type']} = {pattern['value']}")
                match_result = await self._wait_for_pattern(pattern)
                logger.debug(f"  模式匹配结果: {match_result['matched']}")
                result = {"type": "pattern_match", "pattern": pattern, "match": match_result}
                
            elif step_type == "extract_data":
                extraction_config = step["extract"]
                logger.debug(f"  提取数据，配置项数量: {len(extraction_config)}")
                extracted_data = await self._extract_data(extraction_config)
                logger.debug(f"  数据提取成功，提取字段数: {len(extracted_data)}")
                result = {"type": "extraction", "data": extracted_data}
                
            elif step_type == "conditional":
                condition = step["condition"]
                logger.debug(f"  评估条件: {condition['type']}")
                condition_result = await self._evaluate_condition(condition)
                logger.debug(f"  条件评估结果: {condition_result}")
                result = {"type": "conditional", "condition": condition, "result": condition_result}
                
            elif step_type == "cdp_command":
                command = step["command"]
                params = step.get("params", {})
                logger.debug(f"  执行CDP命令: {command}")
                cdp_result = await self._execute_cdp_command(command, params)
                logger.debug(f"  CDP命令执行成功")
                result = {"type": "cdp", "command": command, "result": cdp_result}
                
            else:
                raise ValueError(f"未知的步骤类型: {step_type}")
            
            # 更新上下文
            context.last_result = result
            context.state["current_step"] = step_index
            
            logger.debug(f"  步骤 #{step_index} 执行成功")
            self._emit_state_update(step_index, TaskState.SUCCESS, result)
            return result
            
        except Exception as e:
            logger.debug(f"  步骤 #{step_index} 执行失败: {str(e)}")
            self._emit_state_update(step_index, TaskState.FAILED, str(e))
            raise
    
    def _pattern_based_routing(self, step_result: Dict, all_steps: List[Dict]) -> Observable:
        """基于模式匹配的路由决策"""
        current_step_index = self.current_context.state["current_step"]
        current_step = all_steps[current_step_index]
        
        # 检查是否有基于模式匹配的下一步决策
        if "next_step_patterns" in current_step:
            for pattern_rule in current_step["next_step_patterns"]:
                if self._matches_pattern(step_result, pattern_rule["pattern"]):
                    target_step = pattern_rule["goto_step"]
                    # 创建从目标步骤开始的新执行流
                    remaining_steps = all_steps[target_step:]
                    return self._build_execution_flow(remaining_steps)
        
        # 默认继续下一个步骤
        return Observable.just(step_result)
    
    async def _wait_for_pattern(self, pattern_config: Dict) -> Dict[str, Any]:
        """等待模式匹配"""
        pattern_type = pattern_config["type"]
        pattern_value = pattern_config["value"]
        timeout = pattern_config.get("timeout", 5000)
        
        context = self.current_context
        
        if pattern_type == "url":
            # 等待URL匹配正则表达式
            await context.page.wait_for_function(
                f"window.location.href.match(/{pattern_value}/)",
                timeout=timeout
            )
            current_url = context.page.url
            return {"type": "url", "matched": bool(re.search(pattern_value, current_url)), "url": current_url}
            
        elif pattern_type == "content":
            # 等待页面内容包含特定文本
            try:
                await context.page.wait_for_selector(
                    f"text={pattern_value}",
                    timeout=timeout
                )
                return {"type": "content", "matched": True, "content": pattern_value}
            except:
                return {"type": "content", "matched": False, "content": pattern_value}
                
        elif pattern_type == "element":
            # 等待元素出现
            try:
                element = await context.page.wait_for_selector(pattern_value, timeout=timeout)
                return {"type": "element", "matched": True, "selector": pattern_value}
            except:
                return {"type": "element", "matched": False, "selector": pattern_value}
        
        elif pattern_type == "custom":
            # 自定义模式匹配函数
            custom_check = pattern_config["check_function"]
            result = await custom_check(context)
            return {"type": "custom", "matched": result}
    
    async def _extract_data(self, extraction_config: Dict) -> Dict[str, Any]:
        """提取数据"""
        context = self.current_context
        extracted = {}
        
        for key, config in extraction_config.items():
            if config["method"] == "selector_text":
                element = await context.page.query_selector(config["selector"])
                extracted[key] = await element.text_content() if element else None
            elif config["method"] == "page_content":
                extracted[key] = await context.page.content()
            elif config["method"] == "evaluate_js":
                extracted[key] = await context.page.evaluate(config["expression"])
            elif config["method"] == "cdp":
                cdp_session = await context.context.new_cdp_session(context.page)
                result = await cdp_session.send(config["command"], config.get("params", {}))
                extracted[key] = result
        
        return extracted
    
    async def _evaluate_condition(self, condition_config: Dict) -> bool:
        """评估条件"""
        context = self.current_context
        
        if condition_config["type"] == "pattern_match":
            pattern_result = await self._wait_for_pattern(condition_config["pattern"])
            return pattern_result["matched"]
        elif condition_config["type"] == "data_check":
            # 检查之前提取的数据
            target_data = condition_config["data_path"]
            expected_value = condition_config["expected"]
            actual_value = context.data.get(target_data)
            return actual_value == expected_value
    
    async def _execute_cdp_command(self, command: str, params: Dict) -> Any:
        """执行CDP命令"""
        context = self.current_context
        cdp_session = await context.context.new_cdp_session(context.page)
        return await cdp_session.send(command, params)
    
    def _matches_pattern(self, result: Dict, pattern: Dict) -> bool:
        """检查结果是否匹配模式"""
        if pattern["field"] == "type" and result.get("type") == pattern["value"]:
            return True
        elif pattern["field"] == "data" and result.get("data"):
            # 实现更复杂的数据模式匹配
            return self._deep_pattern_match(result["data"], pattern["value"])
        return False
    
    def _deep_pattern_match(self, data: Any, pattern: Any) -> bool:
        """深度模式匹配"""
        if isinstance(pattern, dict) and isinstance(data, dict):
            return all(self._deep_pattern_match(data.get(k), v) for k, v in pattern.items())
        elif isinstance(pattern, list) and isinstance(data, list):
            return all(any(self._deep_pattern_match(d, p) for d in data) for p in pattern)
        else:
            return data == pattern
    
    def _emit_state_update(self, step_index: int, state: TaskState, result: Any = None):
        """发射状态更新事件"""
        update = {
            "step_index": step_index,
            "state": state,
            "result": result,
            "timestamp": asyncio.get_event_loop().time()
        }
        logger.debug(f"状态更新: 步骤 #{step_index} -> {state.name}")
        self.state_stream.on_next(update)
    
    async def _start_execution(self, execution_flow: Observable):
        """启动执行流"""
        # 这里可以添加更复杂的启动逻辑
        pass
    
    async def close(self):
        """关闭框架"""
        logger.debug("开始关闭框架...")
        if self.browser:
            logger.debug("关闭浏览器...")
            await self.browser.close()
            logger.debug("浏览器已关闭")
        if self.playwright:
            logger.debug("关闭Playwright...")
            await self.playwright.stop()
            logger.debug("Playwright已关闭")
        logger.debug("框架关闭完成")