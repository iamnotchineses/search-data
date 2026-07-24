# -*- coding: utf-8 -*-
"""
상품 수익율 검색기 (Streamlit)

데이터: 스크립트와 같은 폴더의 "NN년도매출.parquet" (권장) 또는 "NN년도매출.xlsx"
        → xlsx는 로컬에서 '변환_xlsx_to_parquet.py'로 미리 변환해서 올릴 것

산식
  수익율 = 수익원(실배송비) / 최종판매가 * 100
  정산금 = 출고원가 + 수익원(실배송비)   (= 판매가 - 수수료)

정제
  - 반품(음수 수량) 행은 원매출건과 함께 제거 (주문번호+모델명 키 단위)
  - 판매가 0원 행(쇼핑백/사은품 등) 제외

실행: streamlit run 상품수익율검색기.py
"""

import glob
import os
import re

import numpy as np
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_PATTERN_XLSX = os.path.join(BASE_DIR, "*년도매출.xlsx")
RAW_PATTERN_PQ = os.path.join(BASE_DIR, "*년도매출.parquet")
CACHE_DIR = os.path.join(BASE_DIR, "_cache")

HEADER_ROW = 1   # 헤더가 2번째 행
SHEET_NAME = 0   # 첫 번째 시트
XLSX_SIZE_LIMIT_MB = 20   # 이보다 큰 xlsx는 서버에서 변환하지 않음 (메모리 초과 방지)

COL_ORDER = "주문번호"
COL_MODEL = "모델명"
COL_MALL = "쇼핑몰"
COL_BRAND = "브랜드"
COL_QTY = "수량"
COL_COST = "출고원가"
COL_PRICE = "최종판매가"
COL_PROFIT = "수익원(실배송비)"
COL_DATE = "출고날짜"
COL_NOTE = "비고"

USE_COLS = [COL_ORDER, COL_MALL, COL_BRAND, "대카테고리", "카테고리",
            COL_MODEL, COL_QTY, COL_COST, COL_PRICE, COL_PROFIT, COL_DATE, COL_NOTE]

CAT_COLS = [COL_MALL, COL_BRAND, "대카테고리", "카테고리", COL_NOTE, COL_MODEL]

st.set_page_config(page_title="상품 수익율 검색기", page_icon="🔍", layout="wide")


# ──────────────────────────────────────────────
# 데이터 로딩
# ──────────────────────────────────────────────
def get_file_sigs():
    pq_files = sorted(glob.glob(RAW_PATTERN_PQ))
    pq_bases = {os.path.splitext(os.path.basename(f))[0] for f in pq_files}
    xlsx_files = [f for f in sorted(glob.glob(RAW_PATTERN_XLSX))
                  if os.path.splitext(os.path.basename(f))[0] not in pq_bases
                  and not os.path.basename(f).startswith("~$")]
    files = pq_files + xlsx_files
    return tuple((f, int(os.path.getmtime(f))) for f in files)


def _read_one(path: str) -> pd.DataFrame:
    """parquet은 바로, xlsx는 캐시 변환 후 로딩"""
    if path.lower().endswith(".parquet"):
        return pd.read_parquet(path)

    size_mb = os.path.getsize(path) / 1024 / 1024
    if size_mb > XLSX_SIZE_LIMIT_MB:
        raise MemoryError(
            f"'{os.path.basename(path)}'({size_mb:.0f}MB)는 서버에서 직접 변환하기에 너무 큽니다.\n"
            f"로컬 PC에서 '변환_xlsx_to_parquet.py'를 실행해 .parquet으로 만든 뒤 그 파일만 올려주세요."
        )

    os.makedirs(CACHE_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    pq = os.path.join(CACHE_DIR, f"{base}_{int(os.path.getmtime(path))}.parquet")
    if os.path.exists(pq):
        return pd.read_parquet(pq)

    with st.spinner(f"{os.path.basename(path)} 변환 중... (최초 1회)"):
        df = pd.read_excel(path, sheet_name=SHEET_NAME, header=HEADER_ROW)
        df = df[[c for c in USE_COLS if c in df.columns]]
        df.to_parquet(pq)
    for old in glob.glob(os.path.join(CACHE_DIR, f"{base}_*.parquet")):
        if old != pq:
            try:
                os.remove(old)
            except OSError:
                pass
    return df


@st.cache_data(show_spinner="데이터 불러오는 중...")
def load_all_data(file_sigs: tuple) -> pd.DataFrame:
    frames = [_read_one(p) for p, _ in file_sigs]
    df = pd.concat(frames, ignore_index=True)
    del frames

    # 필수 컬럼 확인
    missing = [c for c in (COL_ORDER, COL_MODEL, COL_QTY, COL_COST,
                           COL_PRICE, COL_PROFIT, COL_DATE) if c not in df.columns]
    if missing:
        raise KeyError(f"필수 컬럼 누락: {missing} — 변환기 버전을 확인하세요.")

    # 날짜
    df[COL_DATE] = pd.to_datetime(df[COL_DATE], errors="coerce")
    df = df.dropna(subset=[COL_DATE])

    # 숫자형
    for c in (COL_QTY, COL_COST, COL_PRICE, COL_PROFIT):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # 반품 제거: 음수 수량이 포함된 (주문번호+모델명) 키 전체 삭제
    key = pd.factorize(df[COL_ORDER].astype(str) + "\x00" + df[COL_MODEL].astype(str))[0]
    bad = np.unique(key[df[COL_QTY].values < 0])
    df = df[~np.isin(key, bad)]
    del key, bad

    # 판매가 0원 행 제외
    df = df[df[COL_PRICE] > 0].drop(columns=[COL_ORDER])

    # 메모리 절감
    for c in CAT_COLS:
        if c in df.columns:
            df[c] = df[c].astype("category")
    df[COL_QTY] = df[COL_QTY].astype("int32")
    for c in (COL_COST, COL_PRICE, COL_PROFIT):
        df[c] = df[c].astype("float32")

    # 파생
    df["연도"] = df[COL_DATE].dt.year.astype("int16")
    df["수익율"] = (df[COL_PROFIT] / df[COL_PRICE] * 100).astype("float32")
    df["정산금"] = (df[COL_COST] + df[COL_PROFIT]).astype("float32")

    return df.reset_index(drop=True)


# ──────────────────────────────────────────────
# 포맷 헬퍼
# ──────────────────────────────────────────────
def fmt_won(v):
    return "-" if v is None or pd.isna(v) else f"{v:,.0f}원"


def fmt_pct(v):
    return "-" if v is None or pd.isna(v) else f"{v:.2f}%"


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
st.title("🔍 상품 수익율 검색기")

file_sigs = get_file_sigs()
if not file_sigs:
    st.error("데이터 파일이 없습니다. 이 스크립트와 같은 폴더에 "
             "'NN년도매출.parquet' (또는 .xlsx) 형식으로 넣어주세요.")
    st.caption(f"현재 폴더: {BASE_DIR}")
    st.stop()

try:
    df = load_all_data(file_sigs)
except Exception as e:
    st.error(f"데이터 로딩 실패: {e}")
    st.exception(e)
    st.stop()

st.caption("데이터: " + ", ".join(os.path.basename(f) for f, _ in file_sigs)
           + f" · {len(df):,}건 · 반품·0원 행 제외 · 수익율 = 수익(실배송비) ÷ 판매가 · 정산금 = 원가 + 수익")

query = st.text_input("상품 라인명 검색", placeholder="예: A158WA, 590702 ...",
                      help="모델명 부분일치 (대소문자 무시, 공백으로 여러 단어 AND 검색)")

if not query.strip():
    st.info("모델명(라인명)을 입력하면 연도별 평균 수익·수익율과 주문건별 상세가 표시됩니다.")
    st.stop()

# 검색: 고유 모델명(카테고리)에서 먼저 매칭 → 속도/메모리 절약
terms = [t.upper() for t in query.strip().split()]
cats = df[COL_MODEL].cat.categories
cats_up = cats.astype(str).str.upper()
hit_mask = np.ones(len(cats), dtype=bool)
for t in terms:
    hit_mask &= np.asarray(cats_up.str.contains(re.escape(t), na=False, regex=True), dtype=bool)
matched = set(cats[hit_mask])

if not matched:
    st.warning(f"'{query}' 검색 결과가 없습니다.")
    st.stop()

hit = df[df[COL_MODEL].isin(matched)]

brands = [str(b) for b in hit[COL_BRAND].dropna().unique()[:5]]
st.markdown(f"**검색 결과 {len(hit):,}건** · 모델 {len(matched):,}종 · "
            f"몰 {hit[COL_MALL].nunique()}곳 · 브랜드: {', '.join(brands)}")


# ── 상단 KPI ──
def agg_stats(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"건수": 0, "평균수익": None, "수익율": None}
    sales = sub[COL_PRICE].sum()
    profit = sub[COL_PROFIT].sum()
    return {"건수": len(sub),
            "평균수익": sub[COL_PROFIT].mean(),
            "수익율": (profit / sales * 100) if sales else None}


years = [2024, 2025, 2026]
overall = agg_stats(hit[hit["연도"].isin(years)])

cols = st.columns(4)
with cols[0]:
    st.metric("최근 3개년 평균 수익", fmt_won(overall["평균수익"]),
              fmt_pct(overall["수익율"]) + " (수익율)", delta_color="off")
for col, yr in zip(cols[1:], years):
    s = agg_stats(hit[hit["연도"] == yr])
    with col:
        if s["건수"]:
            st.metric(f"{yr}년 평균 수익 ({s['건수']:,}건)", fmt_won(s["평균수익"]),
                      fmt_pct(s["수익율"]) + " (수익율)", delta_color="off")
        else:
            st.metric(f"{yr}년", "데이터 없음")

st.divider()

# ── 주문건별 상세 ──
st.subheader("주문건별 상세")

f1, f2, f3 = st.columns([2, 2, 1])
with f1:
    mall_sel = st.multiselect("쇼핑몰", sorted(str(x) for x in hit[COL_MALL].dropna().unique()))
with f2:
    model_sel = st.multiselect("모델명", sorted(str(x) for x in matched))
with f3:
    year_sel = st.multiselect("연도", sorted(hit["연도"].unique(), reverse=True))

detail = hit
if mall_sel:
    detail = detail[detail[COL_MALL].astype(str).isin(mall_sel)]
if model_sel:
    detail = detail[detail[COL_MODEL].astype(str).isin(model_sel)]
if year_sel:
    detail = detail[detail["연도"].isin(year_sel)]

detail = detail.sort_values(COL_DATE, ascending=False)

show = detail[[COL_DATE, COL_MALL, COL_BRAND, COL_MODEL, COL_QTY, COL_COST,
               COL_PRICE, "정산금", COL_PROFIT, "수익율", COL_NOTE]].rename(
    columns={COL_COST: "원가", COL_PRICE: "최종판매가",
             COL_PROFIT: "수익(실배송비)", "수익율": "수익율(%)"})
show[COL_DATE] = show[COL_DATE].dt.strftime("%Y-%m-%d")

WON = st.column_config.NumberColumn(format="%.0f")
st.dataframe(
    show,
    hide_index=True,
    height=520,
    column_config={"원가": WON, "최종판매가": WON, "정산금": WON, "수익(실배송비)": WON,
                   "수익율(%)": st.column_config.NumberColumn(format="%.2f%%")},
)

st.download_button("CSV 다운로드", show.to_csv(index=False).encode("utf-8-sig"),
                   file_name=f"수익율_{query.strip()}.csv", mime="text/csv")

# ── 몰별 요약 ──
with st.expander("몰별 요약 보기"):
    g = (detail.groupby(COL_MALL, observed=True)
         .agg(건수=(COL_MODEL, "size"), 수량합=(COL_QTY, "sum"),
              원가합=(COL_COST, "sum"), 매출합=(COL_PRICE, "sum"),
              정산금합=("정산금", "sum"), 수익합=(COL_PROFIT, "sum"))
         .sort_values("매출합", ascending=False))
    g["수익율(%)"] = (g["수익합"] / g["매출합"] * 100).round(2)
    st.dataframe(
        g.reset_index(), hide_index=True,
        column_config={"원가합": WON, "매출합": WON, "정산금합": WON, "수익합": WON,
                       "수익율(%)": st.column_config.NumberColumn(format="%.2f%%")},
    )
