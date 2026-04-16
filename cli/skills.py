"""Skill 管理：保存 / 加载 / 列举 / 删除用户自定义技能。"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

SKILLS_DIR = Path.home() / ".polyagent" / "skills"


# ─────────────────────────────────────────────────────────────────────────────
# 文件操作
# ─────────────────────────────────────────────────────────────────────────────

def _skill_path(name: str) -> Path:
    safe = re.sub(r"[^\w\-]", "_", name)
    return SKILLS_DIR / f"{safe}.json"


def list_skills() -> list[dict]:
    """返回所有已保存的 skills（按名称排序）。"""
    if not SKILLS_DIR.exists():
        return []
    skills = []
    for path in sorted(SKILLS_DIR.glob("*.json")):
        try:
            skills.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return skills


def load_skill(name: str) -> Optional[dict]:
    """按名称加载 skill，不存在返回 None。"""
    path = _skill_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_skill(skill: dict) -> Path:
    """持久化 skill 到 ~/.polyagent/skills/<name>.json（权限 600）。"""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path = _skill_path(skill["name"])
    path.write_text(json.dumps(skill, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


def delete_skill(name: str) -> bool:
    """删除 skill，成功返回 True。"""
    path = _skill_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Skill 数据结构
# ─────────────────────────────────────────────────────────────────────────────

def make_skill(name: str, description: str, prompt: str, env: dict | None = None) -> dict:
    """构造 skill dict，自动扫描 {变量} 占位符。"""
    variables = list(dict.fromkeys(re.findall(r"\{(\w+)\}", prompt)))
    return {
        "name": name,
        "description": description,
        "prompt": prompt,
        "env": env or {},
        "variables": variables,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def expand_skill(skill: dict, overrides: dict) -> tuple[str, dict]:
    """
    展开 skill 模板，返回 (expanded_prompt, merged_env)。

    overrides 中的键若匹配 skill.env 中的键（不区分大小写），则同步更新 env。
    """
    prompt = skill["prompt"]
    for var, val in overrides.items():
        prompt = prompt.replace(f"{{{var}}}", val)

    merged_env = dict(skill.get("env", {}))
    for override_key, val in overrides.items():
        for env_key in list(merged_env):
            if env_key.upper() == override_key.upper():
                merged_env[env_key] = val

    return prompt, merged_env


def missing_variables(skill: dict, overrides: dict) -> list[str]:
    """返回 skill 中未被 overrides 填充、且无默认值的变量列表。"""
    filled = {k.lower() for k in overrides}
    result = []
    for var in skill.get("variables", []):
        if var.lower() not in filled:
            result.append(var)
    return result
