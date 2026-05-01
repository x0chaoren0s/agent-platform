---
name: yubai-drama-flow
description: AI 漫剧创作五阶段 SOP（故事/风格/资产/分镜/视频），强约束风格锚定与一致性控制。Use when 收到 漫剧/AI 视频/剧本到分镜到视频/网文改编 类任务。
---

# yubai-drama-flow

## 何时使用

当任务目标是把一个故事创意、小说或剧本，系统化产出为可执行分镜和视频提示词时，使用本 Skill。它不是单次提示词技巧，而是一套从前期策划到视频合成的生产流程。

## 入口路由（按用户起点选择）

- 有故事或网文：先读 `references/网文改写指南.md`，再用 `templates/网文改写模板.md`
- 已有剧本准备开做：先完成 `templates/风格定义模板.md`，再走 `templates/AI可行性评估模板.md`、`templates/人物设计模板.md`、`templates/分镜表模板.md`
- 从零开始：先读 `docs/快速开始.md`，再依次完成 `templates/故事大纲模板.md` 与 `templates/风格定义模板.md`
- 新手怕踩坑：先读 `docs/新手避坑指南.md` 和 `docs/常见问题.md`

## 五阶段总览

1. 故事层：故事大纲 -> 小说文本/专业剧本 -> AI 可行性评估  
2. 风格层：风格定义 -> 风格锚定提示词 -> 风格参考图  
3. 设计层：人物设计 -> 场景设计 -> 资产库  
4. 分镜层：分镜脚本 -> 分镜表 -> 静帧图  
5. 视频层：视频提示词 -> 分镜视频片段 -> 合成成片

## Step 1：故事层（Story）

目标：把原始创意变成适合 AI 生成的视频剧本。  
输入：主题、人物、冲突、篇幅目标。  
输出：`故事大纲.md`、`剧本.md`、`AI可行性评估报告.md`。  

关键动作：
- 使用 `templates/故事大纲模板.md` 固化核心叙事
- 有网文原文时，配合 `templates/网文改写模板.md` 与 `references/网文改写指南.md`
- 对复杂动作、多角色同框、超现实特效做可行性标注，参考 `references/AI生成难度评估体系.md`

自检：
- 10 分钟内建议场景数可控（通常 <= 8）
- 高难动作和群像镜头是否有降级方案

## Step 2：风格层（Style）

目标：锁定统一视觉语言，防止后续风格漂移。  
输入：题材、受众、情绪基调、平台定位。  
输出：`风格定义.md`、风格锚定提示词（中英）、参考图集。  

关键动作：
- 必须先填 `templates/风格定义模板.md`
- 风格一致性策略参考 `references/一致性控制方案.md`
- 对每次生成图像都附加相同风格锚定段

自检：
- 样图的角色比例、线条密度、色温是否一致
- 锚定词是否可复用且可读

## Step 3：设计层（Design）

目标：形成可复用的人物与场景资产库。  
输入：剧本角色表、场景清单、风格锚定词。  
输出：`人物设计.md`、`场景设计.md`、资产目录。  

关键动作：
- 人物：按 `templates/人物设计模板.md` 生成正侧背设定
- 场景：按 `templates/场景设计模板.md` 定义主场景与时间变体
- 保留统一命名规范，便于分镜阶段索引

自检：
- 主角在不同角度下是否可识别
- 关键场景是否覆盖主要剧情节点

## Step 4：分镜层（Storyboard）

目标：把剧本拆成可直接生产静帧和视频片段的镜头表。  
输入：剧本、人物资产、场景资产。  
输出：`分镜表.md`、静帧图批次。  

关键动作：
- 使用 `templates/分镜表模板.md`
- 分镜规则参考 `references/分镜设计指南.md`
- 每个镜头准备中文提示词、英文提示词、负面提示词，并注入风格锚定

自检：
- 镜头时长是否满足节奏（避免过短碎镜）
- 人物造型是否跨镜头稳定

## Step 5：视频层（Video）

目标：将静帧与视频提示词转为可剪辑片段并合成成片。  
输入：分镜表、静帧图、视频提示词。  
输出：视频片段、合成视频、问题复盘。  

关键动作：
- 视频提示词参考 `references/视频提示词指南.md`
- 质量标准参考 `references/质量评估标准.md`
- 对失败镜头回退：静帧 + 运镜替代，保证整体可交付

自检：
- 成片时长误差是否在目标范围内
- 是否出现明显风格突变或角色漂移

## 风格锚定与一致性（强约束）

硬规则：每次生图/视频提示词都必须包含同一段风格锚定词，不允许手动删减核心锚点。  

建议锚定结构：
- 画风锚定：`anime style, cinematic composition, coherent character design`
- 光影锚定：`high contrast lighting, controlled color palette`
- 质量锚定：`masterpiece, best quality, highly detailed`

执行时可写为：
- 角色提示词 = 角色描述 + 场景描述 + 风格锚定 + 负面提示词
- 分镜提示词 = 镜头动作 + 情绪目标 + 风格锚定 + 负面提示词

## 提示词三件套范式

- 中文提示词：描述主体、动作、情绪、镜头关系
- 英文提示词：用于主流文生图/文生视频模型的高一致输入
- 负面提示词：抑制崩脸、畸形、低清、错字、水印等问题

示例（精简）：
- 中文：李凡，17岁，黑短发，觉醒时双眼金光，坚定表情，日漫热血风，中近景。
- 英文：Li Fan, 17-year-old anime boy, glowing golden eyes, determined expression, dynamic medium shot, anime style, highly detailed.
- 负面：blurry, low quality, deformed anatomy, extra limbs, watermark, text.

## 工具选择速查

AI 剧本（御三家）：
- Claude Opus 4.7：指令遵循稳，适合对白与剧本结构
- GPT-6：长上下文与快速迭代
- Gemini 3.1 Pro：复杂改写与多模态推理

文生图：
- 首选：GPT-Image-2（理解稳定）
- 次选：NanoBanana 系列（真实感强）
- 备选：Midjourney（风格多）
- 开源：Flux（可定制）

图生视频：
- 期待：HappyHorse
- 首选：Seedance 2.0
- 次选：可灵 O3
- 快速：Vidu 2.0
- 动作表现：Hailuo

预算组合（参考）：
- 新手：约 ¥70/月
- 标准：约 ¥500/月
- 专业：约 ¥1000/月

## References（本地子文件索引）

你可用 `load_skill` 的 `file` 参数读取子文件，例如：

```tool_call
{"tool":"load_skill","args":{"name":"yubai-drama-flow","file":"templates/风格定义模板.md"}}
```

建议优先文件：
- `templates/故事大纲模板.md`
- `templates/风格定义模板.md`
- `templates/AI可行性评估模板.md`
- `templates/人物设计模板.md`
- `templates/场景设计模板.md`
- `templates/分镜表模板.md`
- `references/AI生成难度评估体系.md`
- `references/一致性控制方案.md`
- `references/分镜设计指南.md`
- `references/视频提示词指南.md`
- `references/质量评估标准.md`
- `docs/快速开始.md`
- `docs/新手避坑指南.md`
- `docs/常见问题.md`
- `examples/获得异能的那一天，我和校花成为了同桌/分镜表.md`

## 常见错误（避免）

- 直接跳过风格定义，后期才发现风格混乱
- 每次都改风格词，导致人物跨镜头不一致
- 只写中文提示词，不准备英文与负面提示词
- 不做 AI 可行性评估，后期大量镜头不可生成
- 分镜表缺少时长和运镜信息，导致视频阶段返工
- 发现问题不做回退方案（静帧+运镜），拖慢交付
