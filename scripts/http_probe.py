"""Local HTTP roundtrip; uses retrieval mode and therefore never calls the model."""
import json
import urllib.error
import urllib.request

BASE="http://127.0.0.1:8765"

def request(path,payload=None,method=None):
    data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    req=urllib.request.Request(BASE+path,data=data,headers={"Content-Type":"application/json"},method=method)
    with urllib.request.urlopen(req,timeout=30) as response:
        return json.load(response)

def main():
    status=request("/api/status")
    assert status["stats"]["documents"]==40
    sid=request("/api/sessions",{"title":"接口验证：德赛电池"})["id"]
    result=request("/api/chat",{"session_id":sid,"question":"德赛电池2024年营业收入是多少",
            "mode":"retrieval","as_of":"2025-08-31"})
    assert result["mode"]=="retrieval" and result["status"]=="complete"
    assert result["citations"] and result["usage"]=={}
    follow=request("/api/chat",{"session_id":sid,"question":"那经营现金流呢？","mode":"retrieval","as_of":"2025-08-31"})
    assert follow["companies"]==["德赛电池"]
    assert all(x["company"]=="德赛电池" for x in follow["citations"])
    for index in range(5):
        follow=request("/api/chat",{"session_id":sid,"question":"继续查看经营现金流与净利润的原始披露",
                "mode":"retrieval","as_of":"2025-08-31"})
        assert follow["status"]=="complete"
    context=follow["memory"]
    assert isinstance(context["summary"],str) and 0<len(context["summary"])<=1000
    assert 1<=context["recent_turns"]<=4 and context["summarized_turns"]>0
    assert context["recent_turns"]+context["summarized_turns"]==6
    assert "long_term" not in context
    assert len(request("/api/sessions/"+sid)["turns"])==7
    with urllib.request.urlopen(BASE+"/",timeout=30) as response:
        assert 'memory-button' not in response.read().decode("utf-8")
    try:
        request("/api/memories")
        raise AssertionError("Retired memory route still exists")
    except urllib.error.HTTPError as exc:
        assert exc.code==404
    page=request("/api/documents/000049_2024/page/7")
    assert "20,859,251,590.68" in page["text"]
    try:
        request("/api/documents?as_of=2025-09-01")
        raise AssertionError("Future accepted")
    except urllib.error.HTTPError as exc:
        assert exc.code==422
    report={"status":"passed","documents":status["stats"]["documents"],"first_citations":len(result["citations"]),
            "follow_up_company":follow["companies"],"future_rejected":True,
            "model_configured":status["configured"],"live_model_called":False,
            "context_mode":"recent_dialogue_and_earlier_summary",
            "recent_turns":context["recent_turns"],"summarized_turns":context["summarized_turns"],
            "summary_chars":len(context["summary"]),"full_history_turns":7,"retired_memory_api":404}
    from pathlib import Path
    Path("docs/http-validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),"utf-8")
    print(json.dumps(report,ensure_ascii=False))

if __name__=="__main__":
    main()
