"""실행: python test_year_rollover.py (앱 실행 없이 연도 전환을 검사)."""
import ast
import re
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


경로 = Path(__file__).with_name("상품수익율검색기.py")
트리 = ast.parse(경로.read_text(encoding="utf-8-sig"))
환경 = {"pd": pd, "np": np, "re": re}


def 실행(노드들):
    exec(compile(ast.Module(body=노드들, type_ignores=[]), str(경로), "exec"), 환경)


# 로그인과 파일 읽기를 건너뛰고 실제 앱의 함수·상수·집계 구문을 검사한다.
for 노드 in 트리.body:
    if isinstance(노드, ast.FunctionDef):
        노드.decorator_list = []
        실행([노드])
    elif isinstance(노드, ast.Assign):
        try:
            ast.literal_eval(노드.value)
        except (ValueError, TypeError):
            continue
        실행([노드])
환경["_RX_SIZE"] = re.compile(r"\s*\([^)]*\)\s*$")
환경["load_stock"] = lambda sig: None


def 대입(이름):
    return next(n for n in ast.walk(트리) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 이름 for t in n.targets))


시작 = 트리.body.index(대입("years"))
끝 = 트리.body.index(대입("g_n")) + 1
for 연도 in (2026, 2027, 2028):
    자료 = pd.DataFrame({
        "출고날짜": pd.to_datetime([f"{연도-3}-12-31", f"{연도-2}-01-01",
                                f"{연도-1}-12-31", f"{연도}-01-01"]),
        "모델명": ["TEST (M)"] * 4, "브랜드": ["TEST"] * 4,
        "수량": [1] * 4, "최종판매가": [100] * 4,
        "수익원(실배송비)": [90, 10, 10, 30],
        "정산금_수익": [100] * 4, "정산금": [100] * 4, "매장": [False] * 4,
    })
    자료["연도"] = 자료["출고날짜"].dt.year
    환경.update(df=자료, hit=자료)
    실행(트리.body[시작:끝])
    assert 환경["years"] == [연도-2, 연도-1, 연도]
    assert 환경["g_n"] == 3
    assert np.isclose(환경["g_rate"], 50 / 3)
    실행([대입("periods")])
    assert 환경["periods"][0][1]["수량"].sum() == 3

    환경["load_all_data"] = lambda *args: 자료.copy()
    표 = 환경["전체등급표"]((), None, None)
    assert 표.iloc[0]["건수"] == 3
    assert 표.iloc[0]["이익율(%)"] == round(50 / 3, 2)
    assert 표.iloc[0]["등급"] == 환경["g_res"][0] == "B"

    환경.update(yr_in=2020, label=f"{연도-2}년")
    귀속 = next(n for n in ast.walk(트리) if isinstance(n, ast.Assign)
              and ast.unparse(n).startswith("yr_in = max("))
    실행([귀속, 대입("inb_label")])
    assert 환경["yr_in"] == 연도-2
    assert 환경["inb_label"] == f"입고(~{(연도-2) % 100:02d}년)"

빈자료 = pd.DataFrame({"출고날짜": pd.to_datetime([])})
assert 환경["최근3개년"](빈자료)[-1] == pd.Timestamp.now(tz="Asia/Seoul").year

# 완판 상품이 재고 목록에 없거나 0개로 남아 있어도 하단을 표시한다.
재고시작 = 트리.body.index(대입("_RX_IN"))
재고끝 = 트리.body.index(대입("GRADE_ORDER"))
하단 = next(n for n in ast.walk(트리) if isinstance(n, ast.If)
          and any(isinstance(b, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_t"
                  for t in b.targets) for b in n.body))
판매 = 자료.iloc[-2:].copy()
판매["매장"] = [False, True]
환경.update(hit=판매, df=자료, QUERY="TEST", get_stock_sig=lambda: None)
재고 = pd.DataFrame({
    "모델명_U": ["TEST (M)"], "수량": [0], "총입고량": [2],
    "입고이력": ["400일전/2"], "입고경과일": [400], "기준일": ["2028-01-01"],
})
for 입력, 현재고 in ((재고.iloc[:0], 0), (재고, 0), (재고.assign(수량=5), 5), (None, None)):
    환경["load_stock"] = lambda sig: 입력
    실행(트리.body[재고시작:재고끝])
    정보 = 환경["stock_info"]
    if 입력 is None:
        assert 정보 is None                       # 파일이 없으면 완판으로 단정하지 않는다.
        continue
    assert 정보["판매량"] == 2 and 정보["현재고"] == 현재고
    assert 정보["총입고량"] == 2 + 현재고
    출력 = []
    환경["st"] = SimpleNamespace(markdown=lambda s, **kw: 출력.append(s), caption=출력.append)
    실행([하단])
    html = "".join(출력)
    assert "총입고 <b>" in html and "총판매 <b>2개" in html
    assert f"현재고 <b>{현재고}개" in html
    assert ("✅ 완판" in html) == (현재고 == 0)
    assert ("판매량 기준 추정" in html) == 입력.empty

# 매출과 수익금액은 매장을 포함하되 수익율은 온라인 기준을 유지한다.
통계 = 환경["agg_stats"](판매)
assert 통계["매출"] == 200 and 통계["수익금액"] == 40
assert 통계["수익율"] == 10
assert 환경["agg_stats"](판매.iloc[:0])["수익금액"] == 0
assert 환경["agg_stats"](판매.assign(**{"수익원(실배송비)": -50000}))["수익금액"] == -100000
환경.update(stock_info=None, inbound_by_year={}, cols=[nullcontext()] * 4,
          periods=[("최근 3개년", 판매), ("2026년", 판매.iloc[:0])])
환경["st"].metric = lambda *args: None
상단 = next(n for n in 트리.body if isinstance(n, ast.For)
          and ast.unparse(n.target) == "(col, (label, sub))")
출력.clear()
실행([상단])
assert sum("수익금액" in s for s in 출력) == 2
print("PASS: 연도 전환, 완판·보유 재고 표시, 매출·수익금액 표시")
