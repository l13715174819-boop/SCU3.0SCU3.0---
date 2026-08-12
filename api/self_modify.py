# -*- coding: utf-8 -*-
"""api/self_modify.py — 代码自修改路由

从 server.py 抽取的 8 个自修改路由：
  POST /self-modify/propose      — 提议代码修改（管理员）
  GET  /self-modify/pending      — 列出待审批的修改（管理员）
  POST /self-modify/approve      — 审批通过并应用修改（管理员）
  POST /self-modify/reject       — 拒绝修改（管理员）
  POST /self-modify/rollback     — 回滚已应用的修改（管理员）
  GET  /self-modify/history      — 修改历史（管理员）
  GET  /self-modify/status       — 自修改引擎状态（管理员）
  POST /self-modify/auto-propose — 自动生成改进提案（管理员）
"""
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import verify_admin_key, get

logger = logging.getLogger("SCU3.api.self_modify")

router = APIRouter(tags=["self_modify"])


# ─── 请求模型 ────────────────────────────────
class CodeModificationRequest(BaseModel):
    target_file: str
    description: str
    new_code: str
    proposer: str = "manual"
    reasoning: str = ""
    mode: str = "replace"  # replace / append / prepend


class ModificationActionRequest(BaseModel):
    modification_id: str
    reason: str = ""


class AutoProposeRequest(BaseModel):
    trigger: str = "manual"            # manual / feedback / defect
    feedback: str = ""
    description: str = ""


# ─── 代码自修改 ────────────────────────────────
@router.post("/self-modify/propose")
async def self_modify_propose(req: CodeModificationRequest, api_key: str = Depends(verify_admin_key)):
    """提议代码修改（需管理员权限，经安全审查+阴阳双签）"""
    code_modifier = get("code_modifier")
    proposal = code_modifier.propose_modification(
        target_file=req.target_file,
        description=req.description,
        new_code=req.new_code,
        proposer=req.proposer,
        reasoning=req.reasoning,
        mode=req.mode,
    )
    return JSONResponse(proposal)


@router.get("/self-modify/pending")
async def self_modify_pending(api_key: str = Depends(verify_admin_key)):
    """列出待审批的修改"""
    code_modifier = get("code_modifier")
    return JSONResponse({"pending": code_modifier.list_pending()})


@router.post("/self-modify/approve")
async def self_modify_approve(req: ModificationActionRequest, api_key: str = Depends(verify_admin_key)):
    """审批通过并应用修改（需管理员权限）"""
    code_modifier = get("code_modifier")
    result = code_modifier.approve_modification(req.modification_id, approved_by="admin")
    return JSONResponse(result)


@router.post("/self-modify/reject")
async def self_modify_reject(req: ModificationActionRequest, api_key: str = Depends(verify_admin_key)):
    """拒绝修改（需管理员权限）"""
    code_modifier = get("code_modifier")
    result = code_modifier.reject_modification(req.modification_id, req.reason)
    return JSONResponse(result)


@router.post("/self-modify/rollback")
async def self_modify_rollback(req: ModificationActionRequest, api_key: str = Depends(verify_admin_key)):
    """回滚已应用的修改（需管理员权限）"""
    code_modifier = get("code_modifier")
    result = code_modifier.rollback_modification(req.modification_id)
    return JSONResponse(result)


@router.get("/self-modify/history")
async def self_modify_history(limit: int = 20, api_key: str = Depends(verify_admin_key)):
    """修改历史"""
    code_modifier = get("code_modifier")
    return JSONResponse({"history": code_modifier.get_history(limit)})


@router.get("/self-modify/status")
async def self_modify_status(api_key: str = Depends(verify_admin_key)):
    """自修改引擎状态"""
    code_modifier = get("code_modifier")
    return JSONResponse(code_modifier.get_status())


@router.post("/self-modify/auto-propose")
async def self_modify_auto_propose(req: AutoProposeRequest,
                                   api_key: str = Depends(verify_admin_key)):
    """根据触发源（反馈/缺陷/手动）自动生成改进提案

    流程：调用 self_evolution 的 ProposalGenerator 生成方案 →
    提交到 code_modifier 走安全审查+阴阳双签 → 返回提案ID
    """
    try:
        from m_layer.self_evolution import ProposalGenerator, DefectAnalyzer
        from m_layer.llm_client import get_client

        code_modifier = get("code_modifier")

        # 构造缺陷/改进描述
        desc = req.description or (f"根据反馈改进：{req.feedback}" if req.feedback else f"自动改进（trigger={req.trigger}）")

        # 用 LLM 基于反馈生成改进方案
        client = get_client()
        prompt = (
            f"你是代码改进专家。根据以下反馈/需求生成一个具体的代码修改方案。\n\n"
            f"反馈/需求：{desc}\n\n"
            f"请返回 JSON 格式：\n"
            f'{{"target_file": "相对路径如w1_layer/action.py", "description": "修改说明", '
            f'"reasoning": "修改理由", "mode": "replace/append/prepend", "new_code": "代码内容"}}\n'
            f"只返回JSON，不要其他文字。"
        )
        result = client.chat(prompt=prompt, system_prompt="analytical", context="")
        llm_output = result.get("content", "").strip()

        # 解析 LLM 输出
        import json as _json
        if llm_output.startswith("```"):
            import re as _re
            llm_output = _re.sub(r"^```(?:json)?\s*", "", llm_output)
            llm_output = _re.sub(r"\s*```$", "", llm_output)

        try:
            proposal_data = _json.loads(llm_output)
        except _json.JSONDecodeError:
            return JSONResponse({
                "success": False,
                "error": "LLM 输出解析失败，无法生成有效提案",
                "raw_output": llm_output[:200],
            })

        # 提交到 code_modifier 走安全审查+双签
        proposal = code_modifier.propose_modification(
            target_file=proposal_data.get("target_file", ""),
            description=proposal_data.get("description", desc),
            new_code=proposal_data.get("new_code", ""),
            proposer="auto_propose",
            reasoning=proposal_data.get("reasoning", ""),
            mode=proposal_data.get("mode", "replace"),
        )

        all_passed = proposal.get("status") != "rejected"
        message = proposal.get("reject_reason") or "提案已生成，待审批"
        return JSONResponse({
            "success": True,
            "data": {
                "proposal_id": proposal.get("id", ""),
                "message": message,
                "all_passed": all_passed,
                "status": proposal.get("status"),
                "security_review": proposal.get("security_review"),
            },
        })
    except Exception as e:
        logger.error(f"auto-propose 异常: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})
