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

# 24개월로도 온라인 100건 이하이면 전체 판매실적으로 표본을 확보한다.
# 구간이 듬성듬성 있어도 기준기간과 최근 1년 승급이 실제 날짜와 일치해야 한다.
환경["load_stock"] = lambda sig: None
for 날짜들, 수량들, 매출들, 수익들, 정산들, 예상기간, 예상등급 in (
    (["2024-05-01", "2025-06-17"], [97, 235], [4346800, 7976600],
     [1800000, 3099439], [3700000, 7743800], "전체", "S"),
    (["2024-05-01"], [31], [10000000], [4000000], [8000000], "전체", "S"),
    (["2025-08-01"] * 101, [1] * 101, [1000000] * 101,
     [10000] * 101, [100000] * 101, "최근 15개월", "C"),
    (["2026-10-01"] * 101, [1] * 101, [10000] * 101,
     [3000] * 101, [10000] * 101, "최근 3개월", "A"),
):
    검사자료 = pd.DataFrame({
        "출고날짜": pd.to_datetime(날짜들 + ["2026-10-07"]),
        "모델명": ["TEST"] * len(날짜들) + ["CLOCK"],
        "브랜드": ["TEST"] * (len(날짜들) + 1),
        "수량": 수량들 + [1], "최종판매가": 매출들 + [100],
        "수익원(실배송비)": 수익들 + [10],
        "정산금_수익": 정산들 + [100], "정산금": 정산들 + [100],
        "매장": [False] * (len(날짜들) + 1),
    })
    검사자료["연도"] = 검사자료["출고날짜"].dt.year
    환경.update(df=검사자료, hit=검사자료.iloc[:-1])
    실행(트리.body[시작:끝])
    환경["load_all_data"] = lambda *args: 검사자료.copy()
    표 = 환경["전체등급표"]((), None, None).set_index("라인명").loc["TEST"]
    assert 표["등급"] == 환경["g_res"][0] == 예상등급
    assert 표["기준기간"] == 예상기간
    if 예상기간 != "전체":
        assert 표["기준기간"] == 환경["g_basis"]
    assert 표["수량"] == 환경["_g_base"]["수량"].sum()
    assert 표["매출"] == 환경["_g_base"]["최종판매가"].sum()
    assert np.isclose(표["이익율(%)"], round(환경["g_rate"], 2), atol=0.001)

# 완판도 남기고 현재고·추정 재고액을 추가한다. 원가는 수량으로 가중 평균한다.
엑셀재고 = pd.DataFrame({
    "라인명": ["SOLD", "MIX", "MIX", "MIX", "NOHIST"],
    "모델명": ["SOLD (M)", "MIX (M)", "MIX (L)", "MIX (XL)", "NOHIST"],
    "수량": [0, 0, 4, 3, 2], "입고이력": [""] * 4 + ["150일전/2"],
    "브랜드": ["TEST"] * 5,
})
엑셀매출 = pd.DataFrame({
    "모델명": ["SOLD (M)", "MIX (M)", "MIX (L)", "MIX (L)", "MIX (L)", "MISSING", "CLOCK"],
    "출고날짜": pd.to_datetime(["2026-10-07"] * 7), "연도": [2026] * 7,
    "브랜드": ["TEST"] * 7, "매장": [False] * 7,
    "수량": [50, 1, 2, 1, 1, 10, 1], "출고원가": [10000, 100, 600, 900, 0, 1000, 100],
    "최종판매가": [20000, 200, 1000, 1200, 100, 2000, 200],
    "수익원(실배송비)": [5000, 100, 400, 300, 100, 1000, 100],
    "정산금_수익": [15000, 200, 1000, 1200, 100, 2000, 200],
    "정산금": [15000, 200, 1000, 1200, 100, 2000, 200],
})
환경.update(load_stock=lambda sig: 엑셀재고, load_all_data=lambda *args: 엑셀매출.copy())
엑셀원본 = 환경["전체등급표"]((), None, None)
재고표 = 엑셀원본.set_index("라인명")
assert 재고표.loc["SOLD", "재고"] == 0 and 재고표.loc["SOLD", "재고액"] == 0
assert 재고표.loc["MISSING", "재고"] == 0 and 재고표.loc["MISSING", "재고액"] == 0
assert 재고표.loc["MIX", "재고"] == 7 and 재고표.loc["MIX", "재고액"] == 3200
assert 재고표.loc["NOHIST", "재고"] == 2 and pd.isna(재고표.loc["NOHIST", "재고액"])
import io
엑셀 = pd.read_excel(io.BytesIO(환경["_엑셀바이트"](엑셀원본, "상품등급"))).set_index("라인명")
assert set(엑셀.index) == set(재고표.index) and 엑셀.loc["MIX", "재고액"] == 3200
assert pd.isna(엑셀.loc["NOHIST", "재고액"])
환경["load_stock"] = lambda sig: None
미확인표 = 환경["전체등급표"]((), None, None)
assert 미확인표["재고"].isna().all() and 미확인표["재고액"].isna().all()
print("PASS: 연도·등급·완판 표시 유지, 엑셀 재고·재고액·미확인 원가")
