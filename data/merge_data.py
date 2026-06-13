#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键合并脚本：将新生成的光伏数据合并到主数据文件
"""

import json
import os

def merge_json_files(source_files, target_file='new_energy.json', output_file='new_energy_merged.json'):
    """
    合并多个JSON文件到主数据文件
    :param source_files: 新数据文件列表
    :param target_file: 现有主数据文件
    :param output_file: 合并后的输出文件
    """

    # 读取现有数据
    if os.path.exists(target_file):
        with open(target_file, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        print(f"读取现有数据: {len(existing)} 条")
    else:
        existing = []
        print("现有数据文件不存在，将创建新文件")

    existing_names = {item['name'] for item in existing}
    added_total = 0
    skipped_total = 0

    # 逐个合并新文件
    for source_file in source_files:
        if not os.path.exists(source_file):
            print(f"跳过不存在的文件: {source_file}")
            continue

        with open(source_file, 'r', encoding='utf-8') as f:
            new_data = json.load(f)

        added = 0
        skipped = 0
        for item in new_data:
            if item['name'] not in existing_names:
                existing.append(item)
                existing_names.add(item['name'])
                added += 1
            else:
                skipped += 1

        print(f"  {source_file}: 新增 {added} 条, 跳过重复 {skipped} 条")
        added_total += added
        skipped_total += skipped

    # 保存合并结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"合并完成！")
    print(f"原有数据: {len(existing) - added_total} 条")
    print(f"新增数据: {added_total} 条")
    print(f"跳过重复: {skipped_total} 条")
    print(f"合并后总计: {len(existing)} 条")
    print(f"保存至: {output_file}")
    print(f"{'='*50}")

    return existing


if __name__ == '__main__':
    # 使用示例：合并光伏数据
    source_files = [
        #'pv_batch_15.json',  # 新生成的光伏数据
        # 可以继续添加其他批次
        #'hydrogen_10.json',
        'ne_vehicle_10.json',
    ]

    merge_json_files(source_files)
