# -*- coding: utf-8 -*-
"""SCU3.0 联盟链前景规划与扩展方向 PDF 生成器"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# 注册中文字体
font_paths = [
    ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
    ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
    ("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc"),
]
CN_FONT = "Helvetica"
CN_FONT_BOLD = "Helvetica-Bold"
for name, path in font_paths:
    try:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(name, path))
            if name == "MicrosoftYaHei":
                CN_FONT = name
                CN_FONT_BOLD = name
            elif name == "SimHei" and CN_FONT == "Helvetica":
                CN_FONT = name
                CN_FONT_BOLD = name
            break
    except Exception:
        continue

# 颜色方案（与 UI 蓝本一致）
C_PRIMARY = HexColor("#4F46E5")
C_PRIMARY_SOFT = HexColor("#EEF2FF")
C_ACCENT = HexColor("#6366F1")
C_SUCCESS = HexColor("#10B981")
C_DANGER = HexColor("#EF4444")
C_TEXT = HexColor("#111111")
C_MUTED = HexColor("#6B7280")
C_BORDER = HexColor("#E5E7EB")
C_BG_SOFT = HexColor("#FAFAFA")

# 文档配置
output_path = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'SCU3.0联盟链前景规划.pdf')
doc = SimpleDocTemplate(
    output_path,
    pagesize=A4,
    leftMargin=20*mm, rightMargin=20*mm,
    topMargin=18*mm, bottomMargin=18*mm,
    title="SCU3.0 联盟链前景规划与扩展方向",
    author="SCU3.0"
)

# 样式
styles = getSampleStyleSheet()

style_title = ParagraphStyle('CnTitle', parent=styles['Title'],
    fontName=CN_FONT_BOLD, fontSize=22, leading=30, textColor=C_TEXT,
    alignment=TA_CENTER, spaceAfter=6*mm)

style_subtitle = ParagraphStyle('CnSubtitle', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=13, leading=18, textColor=C_MUTED,
    alignment=TA_CENTER, spaceAfter=10*mm)

style_h1 = ParagraphStyle('CnH1', parent=styles['Heading1'],
    fontName=CN_FONT_BOLD, fontSize=16, leading=22, textColor=C_PRIMARY,
    spaceBefore=8*mm, spaceAfter=4*mm, borderWidth=0,
    borderPadding=0, leftIndent=0)

style_h2 = ParagraphStyle('CnH2', parent=styles['Heading2'],
    fontName=CN_FONT_BOLD, fontSize=13, leading=18, textColor=C_TEXT,
    spaceBefore=5*mm, spaceAfter=3*mm)

style_body = ParagraphStyle('CnBody', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=10.5, leading=17, textColor=C_TEXT,
    alignment=TA_JUSTIFY, spaceAfter=3*mm, firstLineIndent=21)

style_body_noindent = ParagraphStyle('CnBodyNoIndent', parent=style_body,
    firstLineIndent=0)

style_bullet = ParagraphStyle('CnBullet', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=10.5, leading=16, textColor=C_TEXT,
    leftIndent=18, bulletIndent=6, spaceAfter=2*mm)

style_quote = ParagraphStyle('CnQuote', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=10, leading=15, textColor=C_MUTED,
    leftIndent=15, rightIndent=15, spaceBefore=3*mm, spaceAfter=3*mm,
    backColor=C_BG_SOFT, borderPadding=8, borderWidth=0)

style_footer = ParagraphStyle('CnFooter', parent=styles['Normal'],
    fontName=CN_FONT, fontSize=9, leading=12, textColor=C_MUTED,
    alignment=TA_CENTER)

story = []

# ============ 封面 ============
story.append(Spacer(1, 50*mm))
story.append(Paragraph("SCU3.0 联盟链", style_title))
story.append(Paragraph("前景规划与扩展方向", style_title))
story.append(Spacer(1, 8*mm))
story.append(Paragraph("AI 原生区块链形态 · 分布式智能计算单元网络", style_subtitle))
story.append(Spacer(1, 30*mm))

# 封面信息表
cover_info = [
    ["文档版本", "SCU3.0"],
    ["架构基础", "v3 三维度分离 + 阴阳双签 + 分布式执行"],
    ["生成日期", "2026-08-11"],
    ["文档定位", "战略规划 · 扩展蓝图 · 演进路径"],
]
cover_table = Table(cover_info, colWidths=[40*mm, 120*mm])
cover_table.setStyle(TableStyle([
    ('FONT', (0,0), (-1,-1), CN_FONT, 10.5),
    ('FONT', (0,0), (0,-1), CN_FONT_BOLD, 10.5),
    ('TEXTCOLOR', (0,0), (0,-1), C_PRIMARY),
    ('TEXTCOLOR', (1,0), (1,-1), C_TEXT),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING', (0,0), (-1,-1), 10),
    ('LINEBELOW', (0,0), (-1,-1), 0.5, C_BORDER),
    ('LINEABOVE', (0,0), (-1,0), 0.5, C_BORDER),
]))
story.append(cover_table)
story.append(PageBreak())

# ============ 第一章：为什么 AI 适配区块链模式 ============
story.append(Paragraph("一、为什么 AI 适配区块链模式", style_h1))

story.append(Paragraph("AI 的三大天然痛点，正好是区块链的三大优势。这种互补性不是巧合，而是因为 AI 与区块链在信任、可验证性、经济约束三个维度上存在结构性互补。", style_body))

# 痛点对照表
pain_data = [
    ["AI 痛点", "区块链解药", "SCU 对应能力"],
    ["不可验证\n（黑盒推理，无法证明结果可信）", "链上存证 + 多签验证", "阴阳双签\n（γ_yin≥0.75 / γ_yang≥0.65）"],
    ["数据不可溯\n（训练数据/推理过程不可追）", "不可篡改的哈希链", "熵税账本\n_hash_chain 哈希链接"],
    ["中心化风险\n（单点控制 AI = 控制决策权）", "去中心化共识", "分布式 Worker\n+ 多单元部署"],
]
pain_table = Table(pain_data, colWidths=[55*mm, 55*mm, 55*mm])
pain_table.setStyle(TableStyle([
    ('FONT', (0,0), (-1,0), CN_FONT_BOLD, 10),
    ('FONT', (0,1), (-1,-1), CN_FONT, 9.5),
    ('BACKGROUND', (0,0), (-1,0), C_PRIMARY),
    ('TEXTCOLOR', (0,0), (-1,0), white),
    ('TEXTCOLOR', (0,1), (-1,-1), C_TEXT),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('ALIGN', (0,0), (-1,-1), 'LEFT'),
    ('TOPPADDING', (0,0), (-1,-1), 8),
    ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('GRID', (0,0), (-1,-1), 0.5, C_BORDER),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, C_BG_SOFT]),
]))
story.append(pain_table)
story.append(Spacer(1, 5*mm))

story.append(Paragraph("SCU 架构与智能合约的高度同构", style_h2))
story.append(Paragraph("SCU 已经具备区块链的核心基因，同构度高达 80%。这不是\"给 AI 加区块链\"，而是\"AI 架构自然涌现了区块链形态\"。", style_body))

iso_data = [
    ["SCU 现有能力", "区块链对应概念"],
    ["D 层四公理（A1/A2/A3/A4）", "链的宪法（Constitution）"],
    ["熵税账本 + 五维计税", "链上交易 + Gas 机制"],
    ["哈希链 _hash_chain", "区块哈希链接"],
    ["阴阳双签（阴方+阳方+合一）", "多签合约（Multi-sig）"],
    ["代码自修改 + 人工审批", "链上治理（On-chain Governance）"],
    ["D 层 MANIFEST 哈希基线", "创世块（Genesis Block）"],
    ["插件市场（能力匹配+下载加载）", "智能合约市场"],
    ["经验沉淀（成功/失败记录）", "链上信誉系统（Reputation）"],
]
iso_table = Table(iso_data, colWidths=[85*mm, 85*mm])
iso_table.setStyle(TableStyle([
    ('FONT', (0,0), (-1,0), CN_FONT_BOLD, 10),
    ('FONT', (0,1), (-1,-1), CN_FONT, 10),
    ('BACKGROUND', (0,0), (-1,0), C_ACCENT),
    ('TEXTCOLOR', (0,0), (-1,0), white),
    ('TEXTCOLOR', (0,1), (-1,-1), C_TEXT),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('GRID', (0,0), (-1,-1), 0.5, C_BORDER),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, C_BG_SOFT]),
]))
story.append(iso_table)
story.append(Spacer(1, 3*mm))

story.append(Paragraph("当前缺少的关键要素：无 P2P 网络层、无共识算法、无跨节点账本同步、无分叉处理、无区块重组。但这些是\"补\"上去的，不是\"改\"出来的——架构基因已经就位。", style_quote))

story.append(PageBreak())

# ============ 第二章：四个高价值场景 ============
story.append(Paragraph("二、四个高价值应用场景", style_h1))

# 场景一
story.append(Paragraph("场景一：可审计 AI 决策联盟（合规驱动）", style_h2))
story.append(Paragraph("<b>需求</b>：金融、医疗、政务领域的 AI 决策必须可审计、可追责、不可篡改。这是当前阻碍 AI 落地的最大障碍。", style_body_noindent))
story.append(Paragraph("<b>架构</b>：多个机构的 SCU 终端通过 PBFT 共识组成联盟链，每次 AI 决策上链存证，带阴阳双签证据。监管方作为只读节点实时审计。", style_body_noindent))
story.append(Paragraph("<b>价值</b>：D 层公理 = 合规底线，代码强制不可篡改；医疗事故可回溯到具体 SCU 单元的推理链；解决 AI 责任归属问题。", style_body_noindent))

# 场景二
story.append(Paragraph("场景二：分布式 AI 推理市场（经济驱动）", style_h2))
story.append(Paragraph("<b>需求</b>：大模型推理成本高，中小机构买不起 GPU 集群，算力闲置与算力需求并存。", style_body_noindent))
story.append(Paragraph("<b>架构</b>：算力提供方（SCU+GPU）与算力需求方通过智能合约竞价，推理任务自动分发，阴阳双签作为结果正确性证明，熵税 = 推理 Gas。", style_body_noindent))
story.append(Paragraph("<b>价值</b>：把 SCU 的分布式执行器直接变成去中心化推理市场，无需改造架构；经验沉淀转化为算力提供方的信誉评分。", style_body_noindent))

# 场景三
story.append(Paragraph("场景三：AI 自主协作网络（智能体驱动）", style_h2))
story.append(Paragraph("<b>需求</b>：多个 AI Agent 需要可信协作，避免单点控制，当前行业痛点是 Agent 间无法互信。", style_body_noindent))
story.append(Paragraph("<b>架构</b>：每个 SCU 终端是一个自主 Agent，Agent 间通过智能合约协作（任务分发+结果验收），阴阳双签 = Agent 间的信任协议，代码自修改 = Agent 的链上进化（需社区投票）。", style_body_noindent))
story.append(Paragraph("<b>价值</b>：构建 AI Agent 网络的信任基础设施，为多智能体系统提供去中心化协调层。", style_body_noindent))

# 场景四
story.append(Paragraph("场景四：AI 治理与宪法（治理驱动）", style_h2))
story.append(Paragraph("<b>需求</b>：强 AI 需要约束机制防止失控，这是 AI Safety 领域最前沿的方向——可验证的 AI 约束机制。", style_body_noindent))
story.append(Paragraph("<b>架构</b>：SCU 的 D 层 = AI 的宪法层，上链后真正不可篡改。四公理对应四大约束：A1 基线不可变（保护核心代码）、A2 熵税经济（限制行为频率）、A3 契约闭环（高危动作强制审计）、A4 层级单向（防止反向修改约束）。", style_body_noindent))
story.append(Paragraph("<b>价值</b>：代码自修改需阴阳双签 + 人工审批 = 链上治理提案；熵税 = AI 行为的经济约束，防止无限制自我进化。", style_body_noindent))

story.append(PageBreak())

# ============ 第三章：行业趋势验证 ============
story.append(Paragraph("三、行业趋势验证", style_h1))
story.append(Paragraph("AI × Blockchain 已成为行业前沿方向，多个项目获得大额融资，验证了该形态的商业价值。", style_body))

trend_data = [
    ["项目", "定位", "进展"],
    ["Fetch.ai", "去中心化 AGI 市场", "已落地，自主经济代理"],
    ["Ocean Protocol", "去中心化数据市场", "已落地，数据确权+交易"],
    ["Bittensor (TAO)", "去中心化机器学习网络", "市值 Top 50，矿工贡献模型"],
    ["Ritual AI", "AI × Crypto 基础设施", "获 3000 万美元融资"],
    ["EigenLayer", "主动验证服务", "可验证 AI 推理结果"],
    ["SCU3.0", "AI 原生区块链形态", "架构同构度 80%，待补网络层"],
]
trend_table = Table(trend_data, colWidths=[35*mm, 65*mm, 70*mm])
trend_table.setStyle(TableStyle([
    ('FONT', (0,0), (-1,0), CN_FONT_BOLD, 10),
    ('FONT', (0,1), (-1,-1), CN_FONT, 9.5),
    ('FONT', (0,-1), (-1,-1), CN_FONT_BOLD, 9.5),
    ('BACKGROUND', (0,0), (-1,0), C_SUCCESS),
    ('TEXTCOLOR', (0,0), (-1,0), white),
    ('TEXTCOLOR', (0,1), (-1,-1), C_TEXT),
    ('BACKGROUND', (0,-1), (-1,-1), C_PRIMARY_SOFT),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 7),
    ('BOTTOMPADDING', (0,0), (-1,-1), 7),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('GRID', (0,0), (-1,-1), 0.5, C_BORDER),
    ('ROWBACKGROUNDS', (0,1), (-1,-2), [white, C_BG_SOFT]),
]))
story.append(trend_table)
story.append(Spacer(1, 5*mm))

story.append(Paragraph("关键差异", style_h2))
story.append(Paragraph("上述项目大多<b>从区块链出发，试图嫁接 AI</b>——先有链，再找 AI 场景。SCU 的优势是<b>从 AI 出发，天然具备区块链基因</b>——先有 AI 架构，自然涌现区块链形态。这种原生同构意味着改造成本极低，而落地价值极高。", style_body))

story.append(PageBreak())

# ============ 第四章：演进路径 ============
story.append(Paragraph("四、三阶段演进路径", style_h1))

# Phase 1
story.append(Paragraph("Phase 1：终端联邦（1-2 个月）", style_h2))
story.append(Paragraph("<b>目标</b>：验证账本同步和阴阳双签的跨终端验证。", style_body_noindent))
phase1_items = [
    "多个 SCU 终端通过 P2P 互联（gRPC 或 libp2p）",
    "账本互相同步：广播熵税交易，各节点验证后写入本地账本",
    "简单 PBFT 共识（3-7 节点，适合许可链）",
    "阴阳双签跨终端验证：阴方在终端 A，阳方在终端 B，合一在终端 C",
    "状态持久化扩展：distributed_state.json 增加区块高度字段",
]
for item in phase1_items:
    story.append(Paragraph(f"• {item}", style_bullet))

story.append(Paragraph("<b>验收标准</b>：3 个 SCU 终端组成联盟链，任一终端发起的 AI 决策可被其他终端验证并上链；账本状态三方一致。", style_quote))

# Phase 2
story.append(Paragraph("Phase 2：链上治理（3-6 个月）", style_h2))
story.append(Paragraph("<b>目标</b>：实现真正的链上治理，D 层公理成为链宪法。", style_body_noindent))
phase2_items = [
    "代码自修改提案上链投票（持有 E 点的终端有投票权）",
    "阴阳双签结果作为链上证据，不可篡改",
    "D 层四公理真正成为链宪法，修改需超级多数（2/3+）同意",
    "插件市场升级为智能合约市场，合约部署需双签+审计",
    "跨终端经验沉淀：信誉系统，成功/失败记录全网可见",
]
for item in phase2_items:
    story.append(Paragraph(f"• {item}", style_bullet))

story.append(Paragraph("<b>验收标准</b>：代码自修改提案从提交到应用全程上链；D 层公理修改需 2/3 终端投票通过；插件合约部署需阴阳双签验证。", style_quote))

# Phase 3
story.append(Paragraph("Phase 3：推理市场（6-12 个月）", style_h2))
story.append(Paragraph("<b>目标</b>：构建去中心化 AI 推理市场，实现算力与需求的智能匹配。", style_body_noindent))
phase3_items = [
    "算力竞价智能合约：算力提供方挂单，需求方自动匹配",
    "信誉系统：基于经验沉淀，成功次数高的终端优先接单",
    "熵税 = 推理 Gas，按实际消耗计费，自动结算",
    "跨链桥：与其他 AI 链（Bittensor/Ocean）互联，扩大市场",
    "分层市场：CPU 推理 / GPU 推理 / 阴阳双签验证，分层定价",
]
for item in phase3_items:
    story.append(Paragraph(f"• {item}", style_bullet))

story.append(Paragraph("<b>验收标准</b>：算力提供方可挂单接单；需求方可自动匹配最优算力；推理结果带阴阳双签证明；跨链交易可与其他 AI 链互通。", style_quote))

story.append(PageBreak())

# ============ 第五章：技术改造成本评估 ============
story.append(Paragraph("五、技术改造成本评估", style_h1))

cost_data = [
    ["模块", "当前状态", "改造内容", "工作量"],
    ["P2P 网络层", "无（HTTP 主从）", "新增 libp2p/gRPC 节点发现+消息广播", "高（新增）"],
    ["共识机制", "无（单节点决策）", "新增 PBFT 共识（3-7 节点）", "中（新增）"],
    ["账本同步", "独立账本", "交易广播+验证+打包成区块", "中（改造）"],
    ["区块结构", "_history 列表", "封装为区块，前后哈希链接", "低（改造）"],
    ["阴阳双签", "单节点内双签", "跨终端双签（阴方A+阳方B）", "低（扩展）"],
    ["代码自修改", "本地审批", "上链投票+自动执行", "中（改造）"],
    ["插件市场", "本地加载", "合约市场+链上部署", "中（改造）"],
    ["经验沉淀", "本地存储", "链上信誉，全网可见", "低（扩展）"],
    ["D 层公理", "本地哈希校验", "链宪法，2/3+ 投票修改", "低（扩展）"],
]
cost_table = Table(cost_data, colWidths=[28*mm, 38*mm, 62*mm, 22*mm])
cost_table.setStyle(TableStyle([
    ('FONT', (0,0), (-1,0), CN_FONT_BOLD, 9.5),
    ('FONT', (0,1), (-1,-1), CN_FONT, 9),
    ('BACKGROUND', (0,0), (-1,0), C_PRIMARY),
    ('TEXTCOLOR', (0,0), (-1,0), white),
    ('TEXTCOLOR', (0,1), (-1,-1), C_TEXT),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
    ('GRID', (0,0), (-1,-1), 0.5, C_BORDER),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, C_BG_SOFT]),
]))
story.append(cost_table)
story.append(Spacer(1, 5*mm))

story.append(Paragraph("成本评估结论", style_h2))
story.append(Paragraph("9 个模块中，2 个为新增（P2P 网络层、共识机制），4 个为改造（账本同步、区块结构、代码自修改、插件市场），3 个为扩展（阴阳双签、经验沉淀、D 层公理）。<b>核心改造集中在网络层和共识层，AI 业务层无需大改</b>——这正是架构同构度高的红利。", style_body))

story.append(PageBreak())

# ============ 第六章：风险与挑战 ============
story.append(Paragraph("六、风险与挑战", style_h1))

risks = [
    ("性能挑战", "PBFT 共识在节点数超过 20 时性能下降明显。联盟链适合 7-15 节点，若需更大规模需切换为 PoS 或分片。SCU 的熵税机制天然限制了交易频率（MAX_TRANSACTION_PER_SECOND=50），反而缓解了 TPS 压力。"),
    ("隐私挑战", "账本上链意味着 AI 决策记录公开。医疗/金融场景需结合零知识证明（ZKP）或可信执行环境（TEE），只上链验证证据，不上链原始数据。SCU 的 ContentFilter 可作为上链前脱敏层。"),
    ("治理挑战", "链上治理需要明确的投票权重设计。按 E 点余额加权？按终端数一人一票？按信誉评分？需设计防女巫攻击机制。SCU 的经验沉淀可作为初始信誉来源。"),
    ("合规挑战", "联盟链需符合数据本地化要求。可设计\"主权子链\"模式：每个机构运行独立子链，跨机构交易通过中继链验证，原始数据不出域。"),
    ("技术挑战", "阴阳双签跨终端执行需保证 LLM 可用性。若阴方终端的 DeepSeek 不可用，需设计降级策略（回退到本地双签或延迟执行）。"),
]
for title, desc in risks:
    story.append(Paragraph(f"<b>{title}</b>", style_h2))
    story.append(Paragraph(desc, style_body_noindent))

story.append(PageBreak())

# ============ 第七章：总结 ============
story.append(Paragraph("七、总结与建议", style_h1))

story.append(Paragraph("核心判断", style_h2))
story.append(Paragraph("SCU3.0 的前景非常大，且架构适配度罕见地高。核心原因是：SCU 在设计 AI 系统的过程中，已经把区块链的核心要素——<b>不可变基线、哈希链账本、双签验证、熵税经济、链上治理</b>——全部以 AI 原生的方式实现了。这不是\"给 AI 加区块链\"，而是\"AI 架构自然涌现了区块链形态\"。", style_body))

story.append(Paragraph("架构优势", style_h2))
advantages = [
    "<b>同构度高</b>：9 个模块中 7 个为改造/扩展，仅 2 个为新增，改造成本低",
    "<b>AI 原生</b>：从 AI 出发涌现区块链形态，而非反向嫁接，业务契合度高",
    "<b>安全基因</b>：D 层公理 + 阴阳双签 + 熵税经济，天然具备链上治理要素",
    "<b>场景明确</b>：合规审计、推理市场、Agent 协作、AI 治理四个方向都有刚需",
    "<b>演进平滑</b>：三阶段路径渐进式推进，每阶段都有可验收的 PoC",
]
for adv in advantages:
    story.append(Paragraph(f"• {adv}", style_bullet))

story.append(Paragraph("行动建议", style_h2))
actions = [
    "<b>短期（1 月内）</b>：完成 Phase 1 终端联邦 PoC，验证账本同步和跨终端阴阳双签。选取 3 个 SCU 终端搭建测试链。",
    "<b>中期（3 月内）</b>：完成 Phase 2 链上治理，D 层公理上链，代码自修改提案投票机制落地。",
    "<b>长期（6 月内）</b>：启动 Phase 3 推理市场，与 Bittensor/Ocean 等链探索跨链互联。",
    "<b>持续</b>：关注 ZKP + TEE 技术成熟度，为隐私场景做技术储备；关注 AI Safety 监管动态，抢占\"可验证 AI 约束\"标准制定先机。",
]
for act in actions:
    story.append(Paragraph(f"• {act}", style_bullet))

story.append(Spacer(1, 8*mm))
story.append(Paragraph("所有扩展点都有对应的 API 端点和单例管理器，架构弹性充足。当前架构无需大改，主要工作量在 P2P 网络层和共识层——这是\"补\"上去的，不是\"改\"出来的。", style_quote))

story.append(Spacer(1, 15*mm))
story.append(Paragraph("— 文档结束 —", style_footer))
story.append(Paragraph("SCU3.0 · AI 原生区块链形态 · 2026-08-11", style_footer))

# ============ 页眉页脚 ============
def add_page_decoration(canvas, doc):
    canvas.saveState()
    # 页眉
    canvas.setFont(CN_FONT, 8)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(20*mm, A4[1] - 12*mm, "SCU3.0 联盟链前景规划与扩展方向")
    canvas.drawRightString(A4[0] - 20*mm, A4[1] - 12*mm, "2026-08-11")
    # 页眉线
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(20*mm, A4[1] - 14*mm, A4[0] - 20*mm, A4[1] - 14*mm)
    # 页脚
    canvas.drawString(20*mm, 10*mm, "SCU3.0")
    canvas.drawRightString(A4[0] - 20*mm, 10*mm, f"第 {doc.page} 页")
    # 页脚线
    canvas.line(20*mm, 12*mm, A4[0] - 20*mm, 12*mm)
    canvas.restoreState()

doc.build(story, onFirstPage=add_page_decoration, onLaterPages=add_page_decoration)
print(f"PDF 已保存：{output_path}")
