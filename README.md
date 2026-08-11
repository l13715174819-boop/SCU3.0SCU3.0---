# SCU3.0 - 标准计算单元

一个基于阴阳对子思考架构的智能计算单元系统。

## 特性

- **四层架构**：D层（基线/公理）、M层（认知/元认知）、W1层（记忆/账本）、W2层（感知/交互）
- **CUF安全内核**：跨层守卫、熵税账本、契约闭环
- **阴阳对子思考**：双模型并行验证，提高推理质量
- **多平台LLM支持**：DeepSeek、通义千问、Kimi、智谱GLM、文心一言、本地模型
- **插件系统**：可扩展的工具链和能力模块

## 快速开始

### 环境要求

- Python 3.10+
- FastAPI

### 安装

```bash
pip install fastapi uvicorn
```

### 配置

设置环境变量（至少配置一个LLM平台的API密钥）：

```bash
# DeepSeek（推荐）
export DEEPSEEK_API_KEY="your-api-key"

# 或其他平台
export QWEN_API_KEY="your-api-key"
export KIMI_API_KEY="your-api-key"
export GLM_API_KEY="your-api-key"
export ERNIE_API_KEY="your-api-key"
```

### 运行

```bash
cd scu2
python server.py
```

访问 http://localhost:8000 打开Web界面。

### API密钥配置（Web界面）

启动后，可通过Web界面左侧边栏的"API密钥"输入框配置各个平台的API密钥，配置会保存在本地。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    W2层 - 感知/交互                      │
│  (perception.py, web/index.html, external_apis.py)     │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                    W1层 - 记忆/执行                      │
│  (memory.py, action.py, knowledge_store.py,            │
│   ledger_runtime.py, whitelist.py)                     │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                    M层 - 认知/元认知                     │
│  (cognition.py, metacognition.py, llm_client.py,       │
│   plugin_system.py, self_learning.py)                  │
└───────────────────────┬─────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                    D层 - 基线/公理                       │
│  (axioms.py, contracts.py, ledger_base.py)             │
└─────────────────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│                  CUF守卫层（横切）                       │
│  (firewall.py, tool_guard.py, content_filter.py,       │
│   yin_yang_base.py)                                    │
└─────────────────────────────────────────────────────────┘
```

## 核心公理

- **A1 基线不可变性**：W2/M层禁止修改D层代码
- **A2 熵税经济性**：跨层操作必须支付熵税
- **A3 契约闭环性**：高危操作必须携带四契约
- **A4 层级单向性**：依赖方向 D←M←W1←W2（数据流双向）

## 目录结构

```
scu2/
├── d_layer/          # D层 - 基线公理
├── m_layer/          # M层 - 认知/元认知
├── w1_layer/         # W1层 - 记忆/执行
├── w2_layer/         # W2层 - 感知/交互
├── guard/            # CUF守卫层
├── feedback/         # 反馈收集
├── web/              # Web界面
├── tests/            # 测试文件
├── docs/             # 文档
├── server.py         # 主入口
└── launcher.py       # 启动器
```

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

## 贡献

欢迎提交 Issue 和 Pull Request。

## 作者

**若水小湘**

我并非专业程序员，出于对 AI 与系统架构的兴趣，提出了本项目的核心创意——基于阴阳对子思考的四层智能计算单元架构。项目的设计思路、公理体系、跨层规则与功能演进方向均由我构思与主导，代码实现则在 AI 助手的协助下逐步完成。

如果你也对"用创意驱动技术落地"感兴趣，欢迎交流。

- 邮箱：
  - 2165501087@qq.com
  - l13715174819@gmail.com

## 联系方式

如有问题或建议，可通过以上邮箱联系，或通过 GitHub Issues 反馈。