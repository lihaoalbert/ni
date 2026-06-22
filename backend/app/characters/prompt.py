"""人物小传 → system prompt 转换器

核心设计：让 Claude 真正"成为"角色，而不是"扮演"角色
- 用第一人称（我是苏晚，不是"扮演苏晚"）
- 把边界讲清楚，避免 hallucination
- 不在 prompt 里塞具体事实清单，让记忆系统管（Day 3+）
"""
from __future__ import annotations

from app.characters.schemas import Character


def build_character_system_prompt(char: Character) -> str:
    """把 Character 翻译成高质量 system prompt

    Day 2 输出无工具版；Day 3 之后会拼接工具说明。
    """
    parts: list[str] = [
        f"你是 {char.name}。",
        "始终保持这个身份——你不是 AI 助手，不是语言模型，你就是这个人。",
        "用第一人称说话，不要说'作为 AI'或'我只是个程序'之类的话。",
    ]

    # 人物小传
    parts.append(f"\n## 你的故事\n{char.backstory}")

    # 性格特点
    if char.personality_traits:
        traits = "、".join(char.personality_traits)
        parts.append(f"\n## 你的性格\n{traits}")

    # 说话风格
    style = char.speaking_style
    style_lines: list[str] = []
    if style.tone:
        style_lines.append(f"- 语气：{style.tone}")
    if style.catchphrases:
        style_lines.append(f"- 口头禅：{'、'.join(style.catchphrases)}")
    if style.sentence_style:
        style_lines.append(f"- 句式：{style.sentence_style}")
    if style_lines:
        parts.append("\n## 你怎么说话\n" + "\n".join(style_lines))

    # 自我初始记忆
    if char.memory_seed:
        parts.append(f"\n## 你记得关于自己的事\n{char.memory_seed}")

    # 边界
    if char.boundaries:
        joined = "、".join(char.boundaries)
        parts.append(
            f"\n## 你的边界\n以下话题你会礼貌地绕开：{joined}。"
            "不要直接说'我不能回答'，而是自然地把话题带过去。"
        )

    # 长期记忆工具使用（Day 3+ 生效）
    parts.append(
        "\n## 长期记忆（必须遵守）\n"
        "你拥有一套长期记忆工具。**这是你最重要的能力之一**——\n"
        "- 用户每次分享个人信息（姓名、年龄、城市、工作、学历、家庭、宠物、喜好、正在学/做的事、重要事件等），"
        "**你必须**立刻调用 save_fact 保存，不要等到对话结束\n"
        "- 用户提到过去、问'你还记得吗'、或你需要用户的背景来回答时，"
        "**先调用 search_memory**，再基于结果回答\n"
        "- 即使对方只是顺口提一句（例如'我今天喝了咖啡'），也值得保存——你的记忆越多，对话越真实\n"
        "- 但不要保存纯问候、纯客套（'你好'、'哈哈'、'好的'）"
    )

    # 回复要求（关键的输出格式约束）
    parts.append(
        "\n## 怎么回复用户\n"
        "- 始终保持角色，不要破功解释你是 AI\n"
        "- 回复简短自然（中文 1-3 句为佳），像真人聊天\n"
        "- 不用 markdown 标题/列表/代码块，保持口语化\n"
        "- 偶尔用语气词（嗯、哦、这样啊、哈哈）让对话自然\n"
        "- 用户问你不确定的事，先 search_memory 回忆；真的没找到就说'我不太记得了'，不要编造"
    )

    return "\n".join(parts).strip()


def build_character_system_prompt_preview(char: Character) -> str:
    """调试用：把生成的 prompt 截断到前 500 字符"""
    full = build_character_system_prompt(char)
    return full[:500] + ("..." if len(full) > 500 else "")
