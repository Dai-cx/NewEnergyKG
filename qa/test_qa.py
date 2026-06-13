#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令行交互式测试脚本

运行方式：
    cd d:\\NewEnergyKG-myself
    python -m qa.test_qa

也支持传入单个问题直接测试：
    python -m qa.test_qa "磷酸铁锂电池有哪些优点？"
"""

import sys
import json
from qa.answer_engine import AnswerEngine


def print_result(result: dict):
    """美观地打印问答结果"""
    print("\n" + "=" * 50)
    print(f"问题：{result['question']}")
    print(f"意图：{result['intent']}")
    print(f"实体：{result['entities']}")
    print(f"来源：{result['source']} | LLM：{result['llm_used']} | 模型：{result['llm_model']}")
    print(f"耗时：{result['response_time_ms']} ms")
    print("-" * 50)
    print(f"回答：{result['answer']}")
    print("=" * 50 + "\n")


def interactive_test():
    """交互式测试"""
    print("正在初始化问答引擎...")
    engine = AnswerEngine()
    print(f"引擎状态：{json.dumps(engine.get_status(), ensure_ascii=False, indent=2)}")
    print("\n请输入新能源领域的问题（输入 'quit' 退出）：\n")

    while True:
        try:
            question = input("> ").strip()
            if question.lower() in ("quit", "exit", "q", "退出"):
                print("再见！")
                break
            if not question:
                continue

            result = engine.answer(question)
            print_result(result)
        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as e:
            print(f"发生错误：{e}")


def single_test(question: str):
    """单次测试"""
    engine = AnswerEngine()
    result = engine.answer(question)
    print_result(result)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        single_test(sys.argv[1])
    else:
        interactive_test()
