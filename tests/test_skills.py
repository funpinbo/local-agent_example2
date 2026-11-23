"""4편 5장 Skills. 가짜 모델, GPU 없이."""

from chat_ui.agent import skills, toolcall


def _t(name, source="local", out="ok"):
    return toolcall.Tool(name, f"{name} 설명 " * 20, {"type": "object", "properties": {"q": {"type": "string"}}},
                         lambda **kw: out, source=source)


ALL = [_t("find_mails", "mcp:groupware"), _t("API-post-search", "mcp:notion", '{"results":[{"id":"abc"}]}'),
       _t("API-retrieve-page-markdown", "mcp:notion", "# 페이지"), _t("API-query-data-source", "mcp:notion")]


def test_skill_files_parse():
    by = {s.name: s for s in skills.load_all()}
    assert set(by) >= {"notion-page-summary", "monthly-report"}
    assert by["notion-page-summary"].allowed_tools == ["API-post-search", "API-retrieve-page-markdown"]
    assert "절대 지어내지 않는다" in by["notion-page-summary"].body


def test_loading_a_skill_narrows_tools_and_shrinks_prompt():
    sk = skills.load_all()
    events = []
    session = skills.SkillSession(ALL, sk, base=["find_mails"], emit=events.append)
    assert [t.name for t in session.tools()] == ["load_skill", "find_mails"]        # Notion 도구는 아직 안 보인다
    llm = toolcall.ScriptedRaw([
        '<function=load_skill>{"name": "notion-page-summary"}</function>',
        '<function=API-post-search>{"q": "AGENT"}</function>',
        '<function=API-retrieve-page-markdown>{"q": "abc"}</function>',
        "요약입니다 [notion:abc]"])
    msgs = [{"role": "system", "content": "비서" + skills.skill_rules(sk)}, {"role": "user", "content": "AGENT 페이지 요약"}]
    r = toolcall.run_loop(llm, msgs, session.tools, emit=events.append)
    assert r.answer == "요약입니다 [notion:abc]" and r.tool_calls == 3
    first, second = llm.prompts[0], llm.prompts[1]
    assert "- notion-page-summary: Notion에 있는 페이지를" in first             # 평소에는 한 줄 목록만
    assert "Use the function 'API-post-search'" not in first
    assert "Use the function 'API-post-search'" in second and "Use the function 'find_mails'" not in second
    assert "[Skill: notion-page-summary]" in second and "절대 지어내지 않는다" in second   # 절차가 대화에 들어왔다
    sk_ev = next(e for e in events if e["type"] == "skill")
    assert (sk_ev["tools_before"], sk_ev["tools_after"]) == (2, 2)
    # 도구 세트가 바뀌면 시스템 블록이 바뀌므로 "이번에 붙은 글자"가 없다(프롬프트 앞부분부터 달라짐)
    raws = [e for e in events if e["type"] == "llm_raw"]
    assert raws[1]["prompt_delta"] is None and raws[2]["prompt_delta"] is not None


def test_unknown_skill_is_an_error_for_the_model():
    session = skills.SkillSession(ALL, skills.load_all(), base=[])
    assert "없습니다" in session.loader.run({"name": "없는-스킬"})
    assert session.active is None
