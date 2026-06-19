# Inkwell 测试文档

这是一段普通正文，包含 **加粗**、*斜体*、`行内代码` 和[链接](https://example.com)。
还有行内公式 $E = mc^2$ 与货币 $5 和 $10（不应被当作公式）。

## 二级标题 A

### 三级标题 A-1

一个列表：

- 项目一
- 项目二
  - 子项 a
  - 子项 b
- 项目三

> 这是一段引用文字，用来检验 blockquote 样式。

### 三级标题 A-2

行内公式：质能方程 $E=mc^2$，欧拉恒等式 $e^{i\pi}+1=0$。

块级公式：

$$
\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}
$$

$$
\frac{\partial u}{\partial t} = \alpha \nabla^2 u
$$

## 二级标题 B：代码

普通 Python 代码块：

```python
import math

def softmax(values):
    m = max(values)
    exps = [math.exp(v - m) for v in values]
    total = sum(exps)
    return [e / total for e in exps]

result = softmax([1.0, 2.0, 3.0])
print(result)
```

带文件路径的代码块：

```javascript:src/utils/format.js
function formatName(first, last) {
  const first_clean = first.trim();
  const last_clean = last.trim();
  return `${first_clean} ${last_clean}`;
}
```

Bash 示例：

```bash
for f in *.md; do
  echo "processing $f"
done
```

## 二级标题 C：表格

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| id | int | 主键 |
| name | str | 名称 |
| price | float | 价格 |

一段很长的行用于检验横向自适应：aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 结束。
