# -*- coding: utf-8 -*-
"""
상품 수익율 검색기 (Streamlit)
- raw 데이터: 스크립트와 같은 폴더의 "*년도매출.xlsx" (예: 24년도매출.xlsx, 25년도매출.xlsx, 26년도매출.xlsx)
- 최초 실행 시 xlsx → parquet 캐시 변환 (파일 수정시각 기준 자동 갱신)
- 수익율 = 수익원(실배송비) / 최종판매가 * 100 (직접 계산)
- 반품(음수 수량) 행은 원매출건과 함께 제거 (주문번호+모델명 키 단위)
- 판매가 0원 행(쇼핑백/사은품 등) 제외

실행: streamlit run 상품수익율검색기.py
"""

import glob
import os
import re

import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_PATTERN = os.path.join(BASE_DIR, "*년도매출.xlsx")
RAW_PATTERN_PQ = os.path.join(BASE_DIR, "*년도매출.parquet")  # 사전 변환본 (배포용 권장)
CACHE_DIR = os.path.join(BASE_DIR, "_cache")

HEADER_ROW = 1          # 헤더가 2번째 행 (0-index 1)
SHEET_NAME = 0          # 첫 번째 시트

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

USE_COLS = [
    COL_ORDER, COL_MALL, COL_BRAND, "대카테고리", "카테고리",
    COL_MODEL, COL_QTY, COL_COST, COL_PRICE, COL_PROFIT, COL_DATE, COL_NOTE,
]

st.set_page_config(page_title="상품 수익율 검색기", page_icon="🔍", layout="wide")


# ──────────────────────────────────────────────
# 데이터 로딩 (xlsx → parquet 캐시)
# ──────────────────────────────────────────────
def _cache_path(xlsx_path: str) -> str:
    base = os.path.splitext(os.path.basename(xlsx_path))[0]
    mtime = int(os.path.getmtime(xlsx_path))
    return os.path.join(CACHE_DIR, f"{base}_{mtime}.parquet")


def _convert_xlsx(xlsx_path: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path, sheet_name=SHEET_NAME, header=HEADER_ROW)
    # 필요한 컬럼만 (없는 컬럼은 무시)
    cols = [c for c in USE_COLS if c in df.columns]
    df = df[cols].copy()
    return df


@st.cache_data(show_spinner=False)
def load_all_data(file_sigs: tuple) -> pd.DataFrame:
    """file_sigs: ((경로, mtime), ...) — 캐시 무효화 키 겸용"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    frames = []
    for xlsx_path, _mtime in file_sigs:
        # parquet 원본은 바로 로딩
        if xlsx_path.lower().endswith(".parquet"):
            frames.append(pd.read_parquet(xlsx_path))
            continue
        pq = _cache_path(xlsx_path)
        if os.path.exists(pq):
            df = pd.read_parquet(pq)
        else:
            with st.spinner(f"{os.path.basename(xlsx_path)} 변환 중... (최초 1회, 수 분 소요)"):
                df = _convert_xlsx(xlsx_path)
                df.to_parquet(pq)
            # 같은 원본의 이전 mtime 캐시 정리
            base = os.path.splitext(os.path.basename(xlsx_path))[0]
            for old in glob.glob(os.path.join(CACHE_DIR, f"{base}_*.parquet")):
                if old != pq:
                    try:
                        os.remove(old)
                    except OSError:
                        pass
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    # ── 정제 ──
    # 날짜 파싱
    df[COL_DATE] = pd.to_datetime(df[COL_DATE], errors="coerce")
    df = df.dropna(subset=[COL_DATE])
    df["연도"] = df[COL_DATE].dt.year

    # 숫자형 보정
    for c in (COL_QTY, COL_COST, COL_PRICE, COL_PROFIT):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # 반품 제거: 음수 수량이 포함된 (주문번호+모델명) 키 전체 삭제
    neg_keys = df.loc[df[COL_QTY] < 0, [COL_ORDER, COL_MODEL]].drop_duplicates()
    neg_idx = pd.MultiIndex.from_frame(neg_keys)
    df_idx = pd.MultiIndex.from_frame(df[[COL_ORDER, COL_MODEL]])
    df = df[~df_idx.isin(neg_idx)]

    # 판매가 0원 행 제외 (쇼핑백/사은품 등)
    df = df[df[COL_PRICE] > 0]

    # 수익율 직접 계산
    df["수익율"] = (df[COL_PROFIT] / df[COL_PRICE] * 100).round(2)

    # 정산금 = 판매가 - 수수료 = 출고원가 + 수익(실배송비)
    df["정산금"] = (df[COL_COST] + df[COL_PROFIT]).round(0)

    return df.reset_index(drop=True)


def get_file_sigs():
    pq_files = sorted(glob.glob(RAW_PATTERN_PQ))
    pq_bases = {os.path.splitext(os.path.basename(f))[0] for f in pq_files}
    # 같은 이름의 parquet이 있으면 xlsx는 건너뜀 (parquet 우선)
    xlsx_files = [f for f in sorted(glob.glob(RAW_PATTERN))
                  if os.path.splitext(os.path.basename(f))[0] not in pq_bases]
    files = pq_files + xlsx_files
    return tuple((f, int(os.path.getmtime(f))) for f in files)


# ──────────────────────────────────────────────
# 포맷 헬퍼
# ──────────────────────────────────────────────
def fmt_won(v):
    if pd.isna(v):
        return "-"
    return f"{v:,.0f}원"


def fmt_pct(v):
    if pd.isna(v):
        return "-"
    return f"{v:.2f}%"


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
st.title("🔍 상품 수익율 검색기")

file_sigs = get_file_sigs()
if not file_sigs:
    st.error(f"raw 파일이 없습니다. 스크립트와 같은 폴더에 'NN년도매출.xlsx' 형식으로 넣어주세요.\n(패턴: {RAW_PATTERN})")
    st.stop()

st.caption("데이터: " + ", ".join(os.path.basename(f) for f, _ in file_sigs)
           + " · 반품건(원매출 포함) 및 판매가 0원 행 제외 · 수익율 = 수익원(실배송비) ÷ 최종판매가")

df = load_all_data(file_sigs)

query = st.text_input("상품 라인명 검색", placeholder="예: A158WA, 590702, T425 ...",
                      help="모델명 부분일치 검색 (대소문자 무시, 공백으로 여러 단어 AND 검색)")

if not query.strip():
    st.info("모델명(라인명)을 입력하면 연도별 평균 판매수익·수익율과 주문건별 상세가 표시됩니다.")
    st.stop()

# 부분일치 검색 (공백 구분 AND)
terms = [re.escape(t) for t in query.strip().split()]
mask = pd.Series(True, index=df.index)
model_upper = df[COL_MODEL].astype(str).str.upper()
for t in terms:
    mask &= model_upper.str.contains(t.upper(), na=False, regex=True)
hit = df[mask]

if hit.empty:
    st.warning(f"'{query}' 검색 결과가 없습니다.")
    st.stop()

st.markdown(f"**검색 결과: {len(hit):,}건** (모델 {hit[COL_MODEL].nunique():,}종 / "
            f"몰 {hit[COL_MALL].nunique()}곳 / 브랜드: {', '.join(hit[COL_BRAND].dropna().unique()[:5])})")

# ── 상단: 최근 3개년 평균 + 연도별 KPI ──
def agg_stats(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"건수": 0, "평균판매수익": None, "평균수익율": None}
    total_sales = sub[COL_PRICE].sum()
    total_profit = sub[COL_PROFIT].sum()
    return {
        "건수": len(sub),
        "평균판매수익": sub[COL_PROFIT].mean(),          # 주문건당 평균 수익
        "평균수익율": (total_profit / total_sales * 100) if total_sales else None,  # 가중평균
    }

years = [2024, 2025, 2026]
recent3 = hit[hit["연도"].isin(years)]
overall = agg_stats(recent3)

c0, c1, c2, c3 = st.columns(4)
with c0:
    st.metric("최근 3개년 평균 수익",
              fmt_won(overall["평균판매수익"]),
              fmt_pct(overall["평균수익율"]) + " (수익율)")
for col, yr in zip((c1, c2, c3), years):
    s = agg_stats(hit[hit["연도"] == yr])
    with col:
        if s["건수"]:
            st.metric(f"{yr}년 평균 수익 ({s['건수']:,}건)",
                      fmt_won(s["평균판매수익"]),
                      fmt_pct(s["평균수익율"]) + " (수익율)")
        else:
            st.metric(f"{yr}년", "데이터 없음")

st.divider()

# ── 하단: 주문건별 상세 ──
st.subheader("주문건별 상세")

f1, f2, f3 = st.columns([2, 2, 1])
with f1:
    mall_sel = st.multiselect("쇼핑몰 필터", sorted(hit[COL_MALL].dropna().unique()))
with f2:
    model_sel = st.multiselect("모델명 필터", sorted(hit[COL_MODEL].dropna().unique()))
with f3:
    year_sel = st.multiselect("연도", sorted(hit["연도"].unique(), reverse=True))

detail = hit.copy()
if mall_sel:
    detail = detail[detail[COL_MALL].isin(mall_sel)]
if model_sel:
    detail = detail[detail[COL_MODEL].isin(model_sel)]
if year_sel:
    detail = detail[detail["연도"].isin(year_sel)]

detail = detail.sort_values(COL_DATE, ascending=False)

show = detail[[COL_DATE, COL_MALL, COL_BRAND, COL_MODEL, COL_QTY, COL_COST,
               COL_PRICE, "정산금", COL_PROFIT, "수익율", COL_NOTE]].rename(
    columns={COL_COST: "원가", COL_PRICE: "최종판매가",
             COL_PROFIT: "수익(실배송비)", "수익율": "수익율(%)"})
show[COL_DATE] = show[COL_DATE].dt.strftime("%Y-%m-%d")

st.dataframe(
    show,
    use_container_width=True,
    hide_index=True,
    height=520,
    column_config={
        "원가": st.column_config.NumberColumn(format="%,d원"),
        "최종판매가": st.column_config.NumberColumn(format="%,d원"),
        "정산금": st.column_config.NumberColumn(format="%,d원"),
        "수익(실배송비)": st.column_config.NumberColumn(format="%,d원"),
        "수익율(%)": st.column_config.NumberColumn(format="%.2f%%"),
    },
)

# 몰별 요약 (참고)
with st.expander("몰별 요약 보기"):
    g = detail.groupby(COL_MALL).agg(
        건수=(COL_MODEL, "size"),
        수량합=(COL_QTY, "sum"),
        원가합=(COL_COST, "sum"),
        매출합=(COL_PRICE, "sum"),
        정산금합=("정산금", "sum"),
        수익합=(COL_PROFIT, "sum"),
    ).sort_values("매출합", ascending=False)
    g["수익율(%)"] = (g["수익합"] / g["매출합"] * 100).round(2)
    st.dataframe(
        g.reset_index(),
        use_container_width=True,
        hide_index=True,
        column_config={
            "원가합": st.column_config.NumberColumn(format="%,d원"),
            "매출합": st.column_config.NumberColumn(format="%,d원"),
            "정산금합": st.column_config.NumberColumn(format="%,d원"),
            "수익합": st.column_config.NumberColumn(format="%,d원"),
            "수익율(%)": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )
