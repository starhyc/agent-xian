---
name: purchase_data_cleaning_skill
description: Clean purchase data and calculate summaries.
---

# purchase_data_cleaning_skill

Clean purchase data and calculate summaries.

## 字段说明

- `data_dir`: 数据文件目录，包含：
  - `purchase_orders_raw.csv` - PO 数据
  - `vendors.csv` - 供应商主数据
  - `category_taxonomy.csv` - 品类定义
  - `attachment_manifest.csv` - 附件清单
  - `queries.csv` - 查询条件

## Input

```json
{
  "data_dir": "path/to/data"
}
```

## Output

```json
{
  "answer": "122377,128945,97826,..."
}
```

## Usage

Call `skill_run` with the data directory.
