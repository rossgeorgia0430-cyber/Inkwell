# Inkwell 渲染校验

这是一段正文：行内代码 `(Retargeting L1)`、单字母 `I`、`A`，以及加粗行内代码 **`TArray<T>`** 和 `TObjectPtr<T>`。它们应当是**中性浅底、发丝边框、文字不发红**。

链接里的代码 [`FName`](https://example.com) 颜色应跟随链接。

## 表格（复制到飞书应保持原样、不空）

| 模块 | 类型 | 说明 |
| --- | --- | --- |
| Retargeting | `Layer` | 动画重定向层 `L1` |
| TArray\<T\> | 容器 | 动态数组 `TArray<int32>` |
| TObjectPtr | 智能指针 | UObject 引用 |

## 代码块

```cpp:Source/Foundations.h
USTRUCT(BlueprintType)
struct FFoundation {
    GENERATED_BODY()
    UPROPERTY() TArray<TObjectPtr<UObject>> Items;  // 双击 TArray 应高亮同名
};
```

## 公式

行内 $E = mc^2$ 与块级：

$$\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}$$
