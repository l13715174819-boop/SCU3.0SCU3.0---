# -*- coding: utf-8 -*-
"""SCU 区块链架构分层归属与传统区块链对比 PDF 生成器"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# 注册中文字体
font_paths = [
    ("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc"),
    ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
    ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
]
CN_FONT = "Helvetica"
CN_FONT_BOLD = "Helvetica-Bold"
for name, path in font_paths:
    try:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(name, path))
            CN_FONT = name
            CN_FONT_BOLD = name
            break
    except Exception:
        continue

# 颜色方案
C_PRIMARY = HexColor("#4F46E5")
C_PRIMARY_SOFT = HexColor("#EEF2FF")
C_ACCENT = HexColor("#6366F1")
C_SUCCESS = HexColor("#10B981")
C_SUCCESS_SOFT = HexColor("#ECFDF5")
C_DANGER = HexColor("#EF4444")
C_DANGER_SOFT = HexColor("#FEF2F2")
C_WARN = HexColor("#F59E0B")
C_WARN_SOFT = HexColor("#FFFBEB")
C_TEXT = HexColor("#111111")
C_MUTED = HexColor("#6B7280")
C_BORDER = HexColor("#E5E7EB")
C_BG_SOFT = HexColor("#FAFAFA")

output_path = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'SCU区块链架构对比分析.pdf')
doc = SimpleDocTemplate(
    output_path, pagesize=A4,
    leftMargin=18*mm, rightMargin=18*mm,
    topMargin=18*mm, bottomMargin=18*mm,
    title="SCU 区块链架构分层归属与传统区块链对比分析",
    author="SCU3.0"
)

styles = getSampleStyleSheet()
style_title = ParagraphStyle('CnTitle', parent=styles['Title'],
    fontName=CN_FONT_BOLD, fontSize=22, leading=30, textColor=C_TEXT,
    alignment=TA_CENTER, spaceAfter=6*mm)
style_subtitle = ParagraphStyle('CnSubtitle', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=12, leading=16, textColor=C_MUTED,
    alignment=TA_CENTER, spaceAfter=8*mm)
style_h1 = ParagraphStyle('CnH1', parent=styles['Heading1'],
    fontName=CN_FONT_BOLD, fontSize=15, leading=21, textColor=C_PRIMARY,
    spaceBefore=7*mm, spaceAfter=4*mm)
style_h2 = ParagraphStyle('CnH2', parent=styles['Heading2'],
    fontName=CN_FONT_BOLD, fontSize=12, leading=17, textColor=C_TEXT,
    spaceBefore=4*mm, spaceAfter=2*mm)
style_body = ParagraphStyle('CnBody', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=10, leading=16, textColor=C_TEXT,
    alignment=TA_JUSTIFY, spaceAfter=2.5*mm, firstLineIndent=20)
style_body_noindent = ParagraphStyle('CnBodyNoIndent', parent=style_body, firstLineIndent=0)
style_bullet = ParagraphStyle('CnBullet', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=10, leading=15, textColor=C_TEXT,
    leftIndent=16, bulletIndent=4, spaceAfter=1.5*mm)
style_quote = ParagraphStyle('CnQuote', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=9.5, leading=14, textColor=C_MUTED,
    leftIndent=12, rightIndent=12, spaceBefore=2*mm, spaceAfter=3*mm,
    backColor=C_BG_SOFT, borderPadding=6, borderWidth=0)
style_footer = ParagraphStyle('CnFooter', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=8.5, leading=11, textColor=C_MUTED, alignment=TA_CENTER)

def base_table_style(header_color=C_PRIMARY, header_text=white, font_size=9):
    return TableStyle([
        ('FONT', (0,0), (-1,0), CN_FONT_BOLD, font_size),
        ('FONT', (0,1), (-1,-1), CN_FONT, font_size),
        ('BACKGROUND', (0,0), (-1,0), header_color),
        ('TEXTCOLOR', (0,0), (-1,0), header_text),
        ('TEXTCOLOR', (0,1), (-1,-1), C_TEXT),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 7),
        ('GRID', (0,0), (-1,-1), 0.5, C_BORDER),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, C_BG_SOFT]),
    ])

story = []

# ============ 封面 ============
story.append(Spacer(1, 40*mm))
story.append(Paragraph("SCU 区块链架构", style_title))
story.append(Paragraph("分层归属与对比分析", style_title))
story.append(Spacer(1, 6*mm))
story.append(Paragraph("AI 原生区块链 vs 传统区块链 · 跨层合规性验证", style_subtitle))
story.append(Spacer(1, 25*mm))
cover_info = [
    ["文档版本", "SCU3.0"],
    ["分析维度", "分层归属 / 跨层验证 / 架构异同 / 优劣对比"],
    ["核心结论", "区块链扩展不跨层，A4 规则为数据流预留通道"],
    ["生成日期", "2026-08-11"],
]
ct = Table(cover_info, colWidths=[38*mm, 124*mm])
ct.setStyle(TableStyle([
    ('FONT', (0,0), (-1,-1), CN_FONT, 10),
    ('FONT', (0,0), (0,-1), CN_FONT_BOLD, 10),
    ('TEXTCOLOR', (0,0), (0,-1), C_PRIMARY),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING', (0,0), (-1,-1), 10),
    ('LINEBELOW', (0,0), (-1,-1), 0.5, C_BORDER),
    ('LINEABOVE', (0,0), (-1,0), 0.5, C_BORDER),
]))
story.append(ct)
story.append(PageBreak())

# ============ 第一章：SCU 跨层规则回顾 ============
story.append(Paragraph("一、SCU 跨层规则回顾", style_h1))
story.append(Paragraph("分析区块链扩展是否跨层，首先需要明确 SCU 现有的跨层规则。SCU 采用 CUF（Compute Unit Fabric）三维度分离架构，其中 A4 公理（层级单向性）是跨层约束的核心。", style_body))

story.append(Paragraph("A4 公理的关键设计：只管依赖方向，不管数据流方向", style_h2))
rule_data = [
    ["规则维度", "管辖范围", "典型动作", "是否拦截"],
    ["依赖方向\n(D←M←W1←W2)", "import / modify / patch /\nbase_modify / delete", "D 层 import W2\nM 层 modify D 层代码", "拦截\n(反向依赖违规)"],
    ["数据流方向\n(W2→W1→M→D)", "query / tool_call / check /\ninspect / read / write", "W2 发送数据到 W1\nM 层调用 W1 账本", "不拦截\n(A4 豁免)"],
    ["同层流动", "src == tgt", "W1→W1 账本操作\nM→M 认知层调用", "不拦截\n(同层免审)"],
]
rt = Table(rule_data, colWidths=[40*mm, 50*mm, 42*mm, 30*mm])
rt.setStyle(base_table_style(header_color=C_PRIMARY))
story.append(rt)
story.append(Spacer(1, 4*mm))

story.append(Paragraph("关键代码验证", style_h2))
story.append(Paragraph("从 firewall.py 第 168-169 行可以确认：数据流类动作不受 A4 限制。这是区块链扩展不跨层的法理基础。", style_body_noindent))
story.append(Paragraph(
    "# 数据流类动作不受 A4 限制（原则二核心）<br/>"
    "if op.action not in DEPENDENCY_ACTIONS:<br/>"
    "&nbsp;&nbsp;&nbsp;&nbsp;return True, f\"A4 跳过（数据流动作 {op.action} 不受 A4 约束）\"",
    style_quote))

story.append(PageBreak())

# ============ 第二章：区块链模块分层归属图 ============
story.append(Paragraph("二、区块链模块分层归属图", style_h1))
story.append(Paragraph("将区块链所需的 7 个核心模块归入 SCU 现有四层架构，分析其归属依据与跨层风险。", style_body))

story.append(Paragraph("分层归属总览", style_h2))
layer_data = [
    ["层", "现有职责", "区块链模块归属", "归属依据", "跨层风险"],
    ["D 层\n(只读)", "四公理\n基线常量", "区块结构基类\n共识协议常量\n公理定义(不变)", "只定义结构，\n不含运行时状态", "无\n(纯定义)"],
    ["M 层\n(元认知)", "推理生成\n阴阳双签", "跨终端双签\n链上治理提案\n信誉评分", "与现有阴阳对子\n同层，治理属\n元认知范畴", "无\n(同层扩展)"],
    ["W1 层\n(工作层)", "账本运行时\n分布式执行", "P2P 网络层\nPBFT 共识引擎\n区块+账本同步\n交易池", "通信=数据流\n与 HTTP server\n分布式执行器同层", "无\n(数据流豁免)"],
    ["W2 层\n(感知)", "意图识别\n输入处理", "无新增", "保持不变", "无"],
]
lt = Table(layer_data, colWidths=[18*mm, 28*mm, 42*mm, 42*mm, 24*mm])
lt.setStyle(base_table_style(header_color=C_ACCENT, font_size=8.5))
story.append(lt)
story.append(Spacer(1, 4*mm))

story.append(Paragraph("分层归属图（文字版）", style_h2))
arch_text = (
    "D 层（只读·不可链上修改）<br/>"
    "├─ 四公理定义（A1/A2/A3/A4）　　　　　　　← 保持不变<br/>"
    "├─ 区块结构基类　　　　　　　　　　　　　　← 新增（只定义结构）<br/>"
    "└─ 共识协议常量（PBFT 节点数阈值等）　　　← 新增（常量定义）<br/>"
    "<br/>"
    "M 层（元认知·链上治理）<br/>"
    "├─ 阴阳双签（跨终端）　　　　　　　　　　　← 扩展现有 cognition.py<br/>"
    "├─ 链上治理提案（投票/执行）　　　　　　　← 新增（基于 meta_guard）<br/>"
    "└─ 信誉评分系统　　　　　　　　　　　　　　← 新增（基于经验沉淀）<br/>"
    "<br/>"
    "W1 层（工作层·区块链运行时）<br/>"
    "├─ P2P 网络层（节点发现/消息广播）　　　　← 新增（与 HTTP server 同层）<br/>"
    "├─ PBFT 共识引擎（投票/区块提议）　　　　　← 新增（与分布式执行器同层）<br/>"
    "├─ 区块 + 账本同步（扩展 _hash_chain）　　← 扩展现有 ledger_runtime<br/>"
    "└─ 交易池（待打包交易）　　　　　　　　　　← 新增<br/>"
    "<br/>"
    "W2 层（感知·不变）<br/>"
    "└─ 用户输入处理　　　　　　　　　　　　　　← 保持不变"
)
story.append(Paragraph(arch_text, style_quote))

story.append(PageBreak())

# ============ 第三章：跨层风险点分析 ============
story.append(Paragraph("三、跨层风险点分析", style_h1))
story.append(Paragraph("虽然分层归属清晰，但实际推进中有 3 个真实风险点需要警惕。", style_body))

risk_data = [
    ["风险点", "问题描述", "解决方案", "可控性"],
    ["风险1\n账本同步\n违反 A1",
     "同步远程账本时，远程区块\n与本地 D 层公理冲突",
     "同步只写 W1 层 _history，\n不碰 D 层公理；\n冲突时拒绝远程区块",
     "可控"],
    ["风险2\n链上治理\n违反 A4",
     "链上投票修改 D 层公理 =\n顶层修改底层 = 依赖反向",
     "D 层公理不可链上修改，\n只能硬分叉变更；\n链上治理只管 M/W1 配置",
     "可控\n(需明确边界)"],
    ["风险3\n跨终端双签\n依赖方向",
     "终端A(M)调用终端B(M)\n是否算跨层依赖？",
     "不算。P2P 通信是数据流\n(tool_call)，不是依赖\n(import)。A4 不拦截。",
     "可控"],
]
rkt = Table(risk_data, colWidths=[28*mm, 50*mm, 58*mm, 18*mm])
rkt.setStyle(base_table_style(header_color=C_DANGER, font_size=8.5))
story.append(rkt)
story.append(Spacer(1, 4*mm))

story.append(Paragraph("核心结论", style_h2))
story.append(Paragraph("区块链的所有网络通信、共识投票、账本同步，都归入 DATAFLOW_ACTIONS，A4 不拦截。这是 SCU 架构设计时的远见——A4 只管依赖方向，为数据流方向预留了通道，使得区块链扩展天然不跨层。", style_body))

story.append(Paragraph("唯一的硬约束", style_h2))
story.append(Paragraph("D 层公理不能通过链上治理修改。这是必须坚守的底线。如果允许链上投票改 D 层，就破坏了 A1（基线不可变性），整个架构的信任基础崩塌。", style_body_noindent))
story.append(Paragraph(
    "正确做法：<br/>"
    "• D 层 = 宪法，不可链上修改（需硬分叉）<br/>"
    "• M 层 = 法律，可链上治理（2/3 投票）<br/>"
    "• W1 层 = 执行，可链上配置（简单多数）",
    style_quote))

story.append(PageBreak())

# ============ 第四章：与传统区块链对比 ============
story.append(Paragraph("四、SCU 区块链 vs 传统区块链：架构异同", style_h1))

story.append(Paragraph("相同点", style_h2))
same_data = [
    ["维度", "共同特征"],
    ["账本结构", "哈希链 + 时间戳，前后区块哈希链接，不可篡改"],
    ["共识机制", "多方验证 + 投票达成一致（PBFT/PoS/PoW）"],
    ["多签验证", "关键操作需多方签名确认（SCU 阴阳双签 = 传统多签合约）"],
    ["经济激励", "Gas 机制防止滥用（SCU 熵税 = 传统 Gas）"],
    ["不可变性", "基线/创世块不可修改，修改需硬分叉"],
    ["治理机制", "链上提案 + 投票 + 执行（SCU 代码自修改 = 传统链上治理）"],
    ["去中心化", "多节点对等，无单点控制"],
]
st = Table(same_data, colWidths=[35*mm, 119*mm])
st.setStyle(base_table_style(header_color=C_SUCCESS, font_size=9))
story.append(st)
story.append(Spacer(1, 4*mm))

story.append(Paragraph("差异点", style_h2))
diff_data = [
    ["维度", "传统区块链", "SCU 区块链"],
    ["起源",
     "从区块链出发\n找 AI 应用场景",
     "从 AI 出发\n自然涌现区块链形态"],
    ["同构度",
     "AI 是外挂模块\n需适配链的架构",
     "AI 是本体\n区块链是 AI 架构的延伸"],
    ["分层架构",
     "网络层/共识层/数据层/合约层\n四层并列",
     "D/M/W1/W2 四层\n依赖方向单向 + 数据流双向"],
    ["账本内容",
     "交易记录（转账/合约调用）",
     "AI 决策记录（推理链/双签证据/熵税）"],
    ["共识对象",
     "交易有效性（余额/签名）",
     "AI 结果正确性（双签/γ评分）"],
    ["智能合约",
     "图灵完备代码\n（Solidity/Move）",
     "AI 推理流程\n（阴方+阳方+合一）"],
    ["Gas 计算",
     "按指令消耗\n（固定单价）",
     "五维计税\n（基础+层深+状态+自定义+模式）"],
    ["治理对象",
     "协议升级/参数调整",
     "AI 行为约束/代码自修改/D 层公理不可改"],
    ["隐私保护",
     "ZKP/TEE（外挂）",
     "ContentFilter 脱敏 + 分段共享（原生）"],
    ["性能瓶颈",
     "TPS 限制\n（共识开销）",
     "AI 推理延迟 + 熵税限频\n（MAX_TX_PER_SEC=50）"],
    ["安全模型",
     "51% 算力攻击 / 女巫攻击",
     "D 层熔断 + AST 预检 + 双签 + 熵税"],
    ["扩展方向",
     "Layer 2 / 分片 / 跨链",
     "横向加 Worker / 纵向多单元 / 阴阳叠加"],
]
dt = Table(diff_data, colWidths=[28*mm, 63*mm, 63*mm])
dt.setStyle(base_table_style(header_color=C_PRIMARY, font_size=8.5))
story.append(dt)

story.append(PageBreak())

# ============ 第五章：优劣势对比 ============
story.append(Paragraph("五、优劣势对比", style_h1))

story.append(Paragraph("SCU 区块链的独特优势", style_h2))
adv_data = [
    ["优势", "说明"],
    ["AI 原生",
     "区块链要素（账本/双签/Gas/治理）以 AI 原生方式实现，\n不是给 AI 套区块链外壳，业务契合度高"],
    ["架构同构",
     "9 个区块链模块中 7 个为改造/扩展，仅 2 个新增，\n改造成本远低于从零搭建链"],
    ["分层清晰",
     "D/M/W1/W2 四层 + A4 数据流豁免，\n区块链扩展天然不跨层，无需架构重构"],
    ["双签即共识",
     "阴阳双签（γ_yin≥0.75/γ_yang≥0.65）本身就是轻量共识，\nPBFT 只需在此基础上扩展，无需重新设计"],
    ["熵税即 Gas",
     "五维计税（基础+层深+状态+自定义+模式）比传统 Gas 更精细，\n能区分 AI 推理的不同场景"],
    ["治理基因",
     "代码自修改 + 人工审批机制已就位，\n扩展为链上治理只需增加投票环节"],
    ["安全纵深",
     "D 层熔断 + AST 预检 + 沙箱 + 内容过滤 + 双签，\n多层防护比传统链单一签名验证更厚"],
]
at = Table(adv_data, colWidths=[30*mm, 124*mm])
at.setStyle(base_table_style(header_color=C_SUCCESS, font_size=9))
story.append(at)
story.append(Spacer(1, 4*mm))

story.append(Paragraph("SCU 区块链的劣势与挑战", style_h2))
dis_data = [
    ["劣势", "说明", "缓解方案"],
    ["无 P2P 网络层",
     "当前是 HTTP 主从架构，\n非对等网络",
     "新增 libp2p/gRPC，\n工作量中等"],
    ["无共识算法",
     "单节点决策，无多方共识",
     "实现 PBFT（3-7 节点），\n适合联盟链场景"],
    ["性能受限",
     "AI 推理延迟（秒级）+\n熵税限频（50 TPS）",
     "适合低频高价值场景，\n不适合高频微交易"],
    ["生态空白",
     "无开发者社区/工具链/\n钱包/浏览器",
     "聚焦 B2B 联盟链，\n不追求公链生态"],
    ["隐私不足",
     "无 ZKP/TEE 原生支持",
     "Phase 2 引入 ZKP，\nPhase 3 评估 TEE"],
    ["标准缺失",
     "非标准区块链协议，\n与其他链不互通",
     "Phase 3 跨链桥对接\nBittensor/Ocean"],
]
dit = Table(dis_data, colWidths=[30*mm, 60*mm, 64*mm])
dit.setStyle(base_table_style(header_color=C_DANGER, font_size=8.5))
story.append(dit)

story.append(PageBreak())

# ============ 第六章：适用场景对比 ============
story.append(Paragraph("六、适用场景对比", style_h1))
story.append(Paragraph("不同的架构适合不同的场景。SCU 区块链与传统区块链各有最佳应用领域。", style_body))

scene_data = [
    ["场景", "传统区块链", "SCU 区块链", "推荐"],
    ["加密货币支付",
     "★★★★★\n原生支持",
     "★☆☆☆☆\n非设计目标",
     "传统链"],
    ["DeFi 金融合约",
     "★★★★★\n图灵完备合约",
     "★★☆☆☆\nAI 推理非金融逻辑",
     "传统链"],
    ["NFT 数字资产",
     "★★★★★\n原生支持",
     "★☆☆☆☆\n非设计目标",
     "传统链"],
    ["AI 决策审计",
     "★★☆☆☆\n外挂审计层",
     "★★★★★\nAI 原生全栈",
     "SCU 链"],
    ["AI 治理与约束",
     "★★☆☆☆\n通用治理",
     "★★★★★\nD 层公理 = AI 宪法",
     "SCU 链"],
    ["多 Agent 协作",
     "★★☆☆☆\n合约协调",
     "★★★★★\n双签 = 信任协议",
     "SCU 链"],
    ["分布式推理市场",
     "★★★☆☆\n算力市场",
     "★★★★★\n推理+验证一体",
     "SCU 链"],
    ["合规审计存证",
     "★★★☆☆\n通用存证",
     "★★★★☆\nAI 决策全链路存证",
     "SCU 链"],
    ["供应链溯源",
     "★★★★☆\n成熟方案",
     "★★☆☆☆\n非设计目标",
     "传统链"],
    ["身份管理",
     "★★★★☆\nDID 方案",
     "★★☆☆☆\n非设计目标",
     "传统链"],
]
sct = Table(scene_data, colWidths=[38*mm, 40*mm, 46*mm, 30*mm])
sct.setStyle(base_table_style(header_color=C_ACCENT, font_size=8.5))
story.append(sct)
story.append(Spacer(1, 4*mm))

story.append(Paragraph("场景定位总结", style_h2))
story.append(Paragraph(
    "传统区块链：适合价值转移（支付/DeFi/NFT）和通用存证（溯源/身份），核心是\"去中心化的信任机器\"。<br/><br/>"
    "SCU 区块链：适合 AI 决策的可验证治理（审计/约束/协作/推理市场），核心是\"AI 行为的可信基础设施\"。<br/><br/>"
    "两者不是竞争关系，而是互补关系。SCU 链不追求取代以太坊/Solana，而是填补\"AI 决策可信\"这个传统链无法覆盖的空白。",
    style_quote))

story.append(PageBreak())

# ============ 第七章：总结 ============
story.append(Paragraph("七、总结", style_h1))

story.append(Paragraph("核心判断", style_h2))
story.append(Paragraph("SCU 往区块链方向推进不会导致跨层。A4 公理的\"只管依赖方向、不管数据流方向\"设计，为区块链扩展天然预留了通道。所有区块链模块（P2P/共识/账本同步/双签/治理）都归入数据流范畴，A4 不拦截。", style_body))

story.append(Paragraph("分层归属结论", style_h2))
results = [
    "✓ P2P 网络层 → W1 层（数据流，A4 豁免）",
    "✓ PBFT 共识引擎 → W1 层（与分布式执行器同层）",
    "✓ 区块+账本同步 → W1 层（扩展 _hash_chain）",
    "✓ 跨终端双签 → M 层（与现有阴阳对子同层）",
    "✓ 链上治理 → M 层（基于现有 meta_guard）",
    "✓ 区块结构基类 → D 层（只读定义，不含运行时状态）",
    "✗ D 层公理不可链上修改（A1 硬约束）",
]
for r in results:
    story.append(Paragraph(r, style_bullet))

story.append(Paragraph("与传统区块链的关系", style_h2))
story.append(Paragraph(
    "SCU 区块链不是传统区块链的替代品，而是 AI 时代的补充品。传统链解决\"价值转移的可信\"，SCU 链解决\"AI 决策的可信\"。"
    "两者在技术栈上高度相似（账本/共识/多签/Gas/治理），但在起源（AI 原生 vs 链原生）、"
    "账本内容（AI 决策 vs 金融交易）、共识对象（结果正确性 vs 交易有效性）上有本质差异。"
    "SCU 链的独特价值在于：它是唯一从 AI 架构内部涌现的区块链形态，同构度高达 80%，改造成本最低。",
    style_body))

story.append(Paragraph("行动建议", style_h2))
actions = [
    "分层归属已验证不跨层，可放心推进 Phase 1（终端联邦 PoC）",
    "坚守 D 层公理不可链上修改的硬约束，这是架构信任基础",
    "聚焦 AI 决策审计/治理/协作场景，不与传统链在支付/DeFi 领域竞争",
    "Phase 2 引入 ZKP 解决隐私问题，Phase 3 评估跨链桥与传统链互联",
]
for a in actions:
    story.append(Paragraph(f"• {a}", style_bullet))

story.append(Spacer(1, 10*mm))
story.append(Paragraph("— 文档结束 —", style_footer))
story.append(Paragraph("SCU3.0 · 区块链架构对比分析 · 2026-08-11", style_footer))

# 页眉页脚
def add_decoration(canvas, doc):
    canvas.saveState()
    canvas.setFont(CN_FONT, 8)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(18*mm, A4[1] - 12*mm, "SCU 区块链架构对比分析")
    canvas.drawRightString(A4[0] - 18*mm, A4[1] - 12*mm, "2026-08-11")
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(18*mm, A4[1] - 14*mm, A4[0] - 18*mm, A4[1] - 14*mm)
    canvas.drawString(18*mm, 10*mm, "SCU3.0")
    canvas.drawRightString(A4[0] - 18*mm, 10*mm, f"第 {doc.page} 页")
    canvas.line(18*mm, 12*mm, A4[0] - 18*mm, 12*mm)
    canvas.restoreState()

doc.build(story, onFirstPage=add_decoration, onLaterPages=add_decoration)
print(f"PDF 已保存：{output_path}")
