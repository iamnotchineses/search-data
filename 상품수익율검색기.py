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
import hashlib
import hmac
import os
import re

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_PATTERN_XLSX = os.path.join(BASE_DIR, "*매출*.xlsx")
RAW_PATTERN_PQ = os.path.join(BASE_DIR, "*매출*.parquet")
MALL_CLASS_PATTERN = os.path.join(BASE_DIR, "*몰분류*.xlsx")   # 분류=매장 → 통계 제외
STOCK_FILE = os.path.join(BASE_DIR, "재고.parquet")             # 변환기가 생성
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
# 비밀번호 잠금
# ──────────────────────────────────────────────
# 평문 대신 SHA-256 해시만 보관 (저장소에 비밀번호가 그대로 남지 않도록)
PASSWORD_SHA256 = "c356b589ca5af32ef0049f110a6c82d03f78b7a791bc85eda9e1793b72bd86ee"


def _password_ok(pw: str) -> bool:
    """Secrets에 app_password가 있으면 그 값을, 없으면 내장 해시를 사용"""
    try:
        expected = st.secrets.get("app_password")
    except Exception:
        expected = None
    if expected:
        return hmac.compare_digest(pw, str(expected))
    return hmac.compare_digest(
        hashlib.sha256(pw.encode("utf-8")).hexdigest(), PASSWORD_SHA256)


def require_login() -> None:
    if st.session_state.get("_authed"):
        return

    st.title("🔒 상품 수익율 검색기")
    st.caption("비밀번호를 입력하세요.")
    with st.form("login_form"):
        pw = st.text_input("비밀번호", type="password", label_visibility="collapsed",
                           placeholder="비밀번호")
        submitted = st.form_submit_button("로그인")

    if submitted:
        if _password_ok(pw):
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


require_login()

st.markdown("""<style>
div[data-testid="stMetricValue"] { font-size: 1.55rem; }
div[data-testid="stMetricLabel"] { font-size: 0.78rem; }
h1 { font-size: 1.6rem !important; }
h3 { font-size: 1.15rem !important; }
</style>""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# 데이터 로딩
# ──────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_stock(sig):
    if sig is None:
        return None
    df = pd.read_parquet(STOCK_FILE)
    df["모델명_U"] = df["모델명"].astype(str).str.upper()
    return df


def get_stock_sig():
    if not os.path.exists(STOCK_FILE):
        return None
    return int(os.path.getmtime(STOCK_FILE))


def get_mall_class_sig():
    files = sorted(glob.glob(MALL_CLASS_PATTERN))
    if not files:
        return None
    return (files[0], int(os.path.getmtime(files[0])))


def load_store_malls(sig) -> frozenset:
    """몰분류 파일에서 분류='매장'인 몰 목록"""
    if sig is None:
        return frozenset()
    m = pd.read_excel(sig[0], header=0)
    m = m.iloc[:, :2]
    m.columns = ["몰명", "분류"]
    return frozenset(m.loc[m["분류"].astype(str).str.strip() == "매장", "몰명"]
                     .dropna().astype(str).str.strip())


def _is_sales_parquet(path: str) -> bool:
    """스키마에 주문번호·출고날짜가 있으면 매출 데이터로 인식"""
    try:
        import pyarrow.parquet as pq
        cols = set(pq.ParquetFile(path).schema_arrow.names)
        return {"주문번호", "출고날짜", "쇼핑몰"} <= cols
    except Exception:
        return False


def get_file_sigs():
    # parquet: 파일명 무관, 스키마로 매출 데이터 판별 (재고.parquet 등은 자동 제외)
    pq_files = [f for f in sorted(glob.glob(os.path.join(BASE_DIR, "*.parquet")))
                if _is_sales_parquet(f)]
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
def load_all_data(file_sigs: tuple, mall_sig=None) -> pd.DataFrame:
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

    # 매장(오프라인) 판매 제외
    store_malls = load_store_malls(mall_sig)
    if store_malls:
        df = df[~df[COL_MALL].astype(str).str.strip().isin(store_malls)]

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


WON = st.column_config.NumberColumn(format="localized")
NUM = st.column_config.NumberColumn(format="localized")


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
st.title("🔍 상품 수익율 검색기")

file_sigs = get_file_sigs()
if not file_sigs:
    st.error("데이터 파일이 없습니다. 이 스크립트와 같은 폴더에 "
             "파일명에 '매출'이 들어간 .parquet(또는 .xlsx)을 넣어주세요.")
    st.caption(f"현재 폴더: {BASE_DIR}")
    st.stop()

mall_sig = get_mall_class_sig()

try:
    df = load_all_data(file_sigs, mall_sig)
except Exception as e:
    st.error(f"데이터 로딩 실패: {e}")
    st.exception(e)
    st.stop()

st.caption("데이터: " + ", ".join(os.path.basename(f) for f, _ in file_sigs)
           + f" · {len(df):,}건 · 반품·0원 행"
           + (" · 매장(오프라인) 판매" if mall_sig else "")
           + " 제외 · 수익율 = 수익(실배송비) ÷ 판매가 · 정산금 = 원가 + 수익")
if mall_sig is None:
    st.warning("몰분류 파일(*몰분류*.xlsx)이 없어 매장(오프라인) 판매가 제외되지 않았습니다.")

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

# 상품등급: 이익율(수익 ÷ 정산금) 기준 A~E
GRADE_CUTS = [(30, "A", "#1a7f37"), (15, "B", "#0969da"),
              (5, "C", "#bf8700"), (-5, "D", "#d4691e")]

def grade_of(sub: pd.DataFrame):
    settle = sub["정산금"].sum()
    if sub.empty or settle <= 0:
        return None, None
    rate = sub[COL_PROFIT].sum() / settle * 100
    for cut, gr, color in GRADE_CUTS:
        if rate >= cut:
            return (gr, color), rate
    return ("E", "#cf222e"), rate

g_res, g_rate = grade_of(hit[hit["연도"].isin([2024, 2025, 2026])])

# ── 재고 매칭 (등급 밑 표시 + 연도별 입고 역산) ──
_RX_IN = re.compile(r"(\d+)일전/(\d+)")
stock_df = load_stock(get_stock_sig())
stock_info, inbound_by_year = None, {}
if stock_df is not None:
    smask = pd.Series(True, index=stock_df.index)
    for t in terms:
        smask &= stock_df["모델명_U"].str.contains(re.escape(t), na=False, regex=True)
    s_hit = stock_df[smask]
    if not s_hit.empty:
        _qty = int(s_hit["수량"].sum())
        _inb = int(s_hit["총입고량"].sum())
        _sold = max(_inb - _qty, 0)
        stock_info = {
            "현재고": _qty, "총입고량": _inb,
            "회전율": (_sold / _inb) if _inb else None,
            "최근입고": s_hit["입고경과일"].min(),
        }
        # 연도별 입고량 역산 + FIFO 기준 "가장 오래된 잔여재고"의 입고 경과일
        # (오래된 입고분부터 판매 소진했다고 보고, 현재고가 걸쳐 있는 입고 회차의 경과일 사용)
        oldest_days = None
        if "입고이력" in s_hit.columns and "기준일" in s_hit.columns:
            for _, r in s_hit.iterrows():
                base = pd.Timestamp(r["기준일"])
                events = [(int(d), int(q)) for d, q in
                          _RX_IN.findall(str(r["입고이력"]).replace(",", ""))]
                for d_, q_ in events:
                    yr_in = (base - pd.Timedelta(days=d_)).year
                    yr_in = max(yr_in, 2024)   # 24년 이전 입고 → 24년 귀속
                    inbound_by_year[yr_in] = inbound_by_year.get(yr_in, 0) + q_
                # FIFO: 이 모델의 잔여재고가 어느 입고 회차부터 남았는지
                remain = int(r["수량"])
                if remain <= 0 or not events:
                    continue
                row_days = None
                for d_, q_ in sorted(events, key=lambda x: x[0]):   # 최신 → 과거 순
                    remain -= q_
                    row_days = d_
                    if remain <= 0:
                        break
                # remain>0이면 입고이력(최대 3회)보다 재고가 많음 → 가장 오래된 회차로
                if row_days is not None:
                    oldest_days = row_days if oldest_days is None else max(oldest_days, row_days)
        stock_info["최초입고"] = oldest_days

        # 완판(현재고 0) 상품: 최초 입고 → 마지막 판매까지 소요 기간
        stock_info["완판일수"] = None
        if stock_info["현재고"] == 0 and "기준일" in s_hit.columns:
            first_in, base_ts = None, None
            for _, r in s_hit.iterrows():
                evs = _RX_IN.findall(str(r["입고이력"]).replace(",", ""))
                if not evs:
                    continue
                b = pd.Timestamp(r["기준일"])
                d_max = max(int(d) for d, _ in evs)
                cand = b - pd.Timedelta(days=d_max)
                if first_in is None or cand < first_in:
                    first_in, base_ts = cand, b
            last_sale = hit[COL_DATE].max() if not hit.empty else None
            if first_in is not None and pd.notna(last_sale):
                stock_info["완판일수"] = max((pd.Timestamp(last_sale) - first_in).days, 0)

# 최초 입고 300일 경과마다 한 등급씩 강등 (600일=2등급, 900일=3등급 ...)
GRADE_ORDER = ["A", "B", "C", "D", "E"]
GRADE_COLORS = {"A": "#1a7f37", "B": "#0969da", "C": "#bf8700", "D": "#d4691e", "E": "#cf222e"}
demote_steps = 0
if (g_res and stock_info and stock_info.get("최초입고") is not None):
    demote_steps = int(stock_info["최초입고"] // 300)
    if demote_steps > 0:
        _i = min(GRADE_ORDER.index(g_res[0]) + demote_steps, len(GRADE_ORDER) - 1)
        demote_steps = _i - GRADE_ORDER.index(g_res[0])   # 실제 적용된 단계
        _g2 = GRADE_ORDER[_i]
        g_res = (_g2, GRADE_COLORS[_g2])

st.markdown(f"**검색 결과 {len(hit):,}건** · 모델 {len(matched):,}종 · "
            f"몰 {hit[COL_MALL].nunique()}곳 · 브랜드: {', '.join(brands)}")


# ── 상단 KPI ──
def agg_stats(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"수량": 0, "수익율": None, "평균정산금": None}
    sales = sub[COL_PRICE].sum()
    profit = sub[COL_PROFIT].sum()
    qty = sub[COL_QTY].sum()
    return {"수량": int(qty),
            "수익율": (profit / sales * 100) if sales else None,
            "평균정산금": (sub["정산금"].sum() / qty) if qty else None}


years = [2024, 2025, 2026]
periods = [("최근 3개년", hit[hit["연도"].isin(years)])]
periods += [(f"{y}년", hit[hit["연도"] == y]) for y in years]

cols = st.columns(5)
for col, (label, sub) in zip(cols, periods):
    s = agg_stats(sub)
    with col:
        if label == "최근 3개년":
            inb = sum(inbound_by_year.get(y, 0) for y in years)
        else:
            inb = inbound_by_year.get(int(label[:4]), 0)
        inb_txt = f"{inb:,}" if stock_info else "-"
        inb_label = "입고(~24년)" if label == "2024년" else "입고"
        if s["수량"]:
            st.markdown(f"**{label}**")
            st.caption(f"{inb_label} {inb_txt}개 / 판매 {s['수량']:,}개")
            st.metric("평균 수익율", fmt_pct(s["수익율"]))
            st.metric("평균 정산금", fmt_won(s["평균정산금"]))
        else:
            st.markdown(f"**{label}**")
            st.caption(f"{inb_label} {inb_txt}개 / 판매 0개")
            st.metric("평균 수익율", "-")
            st.metric("평균 정산금", "-")

with cols[4]:
    st.markdown("**상품등급**")
    if g_res:
        st.markdown(
            f"<div style='line-height:1.05;margin-bottom:0.4rem'>"
            f"<span style='font-size:2.6rem;font-weight:900;color:{g_res[1]}'>{g_res[0]}</span>"
            f" <span style='font-size:0.8rem;color:#666'>이익율 {g_rate:.2f}%"
            + (f"<br>⬇ 최초입고 {stock_info['최초입고']:,}일 경과 -{demote_steps}등급" if demote_steps else "")
            + "</span></div>",
            unsafe_allow_html=True)
    else:
        st.caption("등급 산출 불가")
    if stock_info:
        _t = stock_info["회전율"]
        _r = stock_info["최근입고"]
        st.markdown(
            "<div style='font-size:0.85rem;line-height:1.7;border-top:1px solid #e6e6e6;padding-top:0.35rem'>"
            f"총입고 <b>{stock_info['총입고량']:,}개</b><br>"
            f"현재고 <b>{stock_info['현재고']:,}개</b><br>"
            f"회전율(판매÷입고) <b>{_t:.1%}</b><br>".replace("nan%", "-")
            + (f"최근입고 <b>{int(_r)}일 전</b>" if pd.notna(_r) else "최근입고 <b>-</b>")
            + (f"<br>✅ 완판까지 <b>{stock_info['완판일수']:,}일</b>"
               if stock_info.get("완판일수") is not None else "")
            + "</div>", unsafe_allow_html=True)
    else:
        st.caption("재고 데이터 없음")

st.divider()

# ── 수익율 구간별 판매수량 ──
st.subheader("수익율 구간별 판매수량")

BIN_LO, BIN_HI, BIN_STEP = -5, 50, 5
edges = list(range(BIN_LO, BIN_HI + BIN_STEP, BIN_STEP))
labels = [f"{BIN_LO}% 미만"] + [f"{a}~{b}%" for a, b in zip(edges[:-1], edges[1:])] + [f"{BIN_HI}% 이상"]
bins = [-np.inf] + edges + [np.inf]

hit_b = hit.assign(구간=pd.cut(hit["수익율"], bins=bins, labels=labels, right=False))

# 전체 기준으로 사용할 구간 범위 결정 (연도 간 x축 통일)
tot = hit_b.groupby("구간", observed=False)[COL_QTY].sum()
tot = tot[tot.cumsum() > 0]
tot = tot[::-1][(tot[::-1].cumsum() > 0)][::-1]
used = list(tot.index)

# 연도 간 y축 통일: 전체 연도에서 구간별 최대 판매수량
y_max = int((hit_b[hit_b["구간"].isin(used)]
             .groupby(["연도", "구간"], observed=True)[COL_QTY].sum().max() or 0))

chart_cols = st.columns(len(years))
for col, yr in zip(chart_cols, years):
    with col:
        sub = hit_b[(hit_b["연도"] == yr) & (hit_b["구간"].isin(used))]
        st.markdown(f"**{yr}년**")
        if sub.empty:
            st.caption("데이터 없음")
            continue

        agg = (sub.groupby("구간", observed=False)
               .agg(판매수량=(COL_QTY, "sum"), 정산금합=("정산금", "sum"))
               .reindex(used))
        agg["평균정산금"] = agg["정산금합"] / agg["판매수량"]
        agg["판매수량"] = agg["판매수량"].fillna(0).astype(int)
        agg = agg[agg["판매수량"] > 0]          # 판매 없는 구간 제거

        # x축 라벨: 구간 + (해당 연도 평균 정산금, 만원 축약) 2줄 표시
        def _lab(b, v):
            return f"{b}|({v/10000:,.1f}만)" if pd.notna(v) else f"{b}|(-)"
        chart_df = agg.reset_index()
        chart_df["구간라벨"] = [_lab(b, v) for b, v in zip(chart_df["구간"].astype(str),
                                                           chart_df["평균정산금"])]
        chart_df["평균정산금"] = chart_df["평균정산금"].round(0)

        st.altair_chart(
            alt.Chart(chart_df).mark_bar().encode(
                x=alt.X("구간라벨:N", sort=list(chart_df["구간라벨"]),
                        title=None,
                        axis=alt.Axis(labelAngle=0, labelExpr="split(datum.label, '|')",
                                      labelFontSize=11, labelFontWeight="bold",
                                      labelColor="#31333F", labelLimit=0,
                                      labelOverlap=False)),
                y=alt.Y("판매수량:Q", title=None,
                        scale=alt.Scale(domain=[0, y_max])),
                tooltip=[alt.Tooltip("구간:N", title="수익율 구간"),
                         alt.Tooltip("판매수량:Q", format=","),
                         alt.Tooltip("평균정산금:Q", format=",.0f", title="평균 정산금(원)")],
            ).properties(height=260),
            use_container_width=True,
        )

st.divider()

# ── 몰별 상세 ──
st.subheader("몰별 상세")


def mall_summary(sub: pd.DataFrame) -> pd.DataFrame:
    g = (sub.groupby(COL_MALL, observed=True)
         .agg(수량합=(COL_QTY, "sum"), 총매출=(COL_PRICE, "sum"),
              _정산금합=("정산금", "sum"), _수익합=(COL_PROFIT, "sum"))
         .sort_values("총매출", ascending=False))
    g["평균정산금"] = (g["_정산금합"] / g["수량합"]).round(0)
    g["평균수익율(%)"] = (g["_수익합"] / g["총매출"] * 100).round(2)
    return g.drop(columns=["_정산금합", "_수익합"]).reset_index()


sum_cols = st.columns(len(years))
for col, yr in zip(sum_cols, years):
    with col:
        st.markdown(f"**{yr}년**")
        sub_y = hit[hit["연도"] == yr]
        if sub_y.empty:
            st.caption("데이터 없음")
            continue
        st.dataframe(
            mall_summary(sub_y), hide_index=True,
            column_config={"수량합": NUM, "총매출": WON, "평균정산금": WON,
                           "평균수익율(%)": st.column_config.NumberColumn(format="%.2f%%")},
        )

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

st.dataframe(
    show,
    hide_index=True,
    height=520,
    column_config={"수량": NUM, "원가": WON, "최종판매가": WON, "정산금": WON,
                   "수익(실배송비)": WON,
                   "수익율(%)": st.column_config.NumberColumn(format="%.2f%%")},
)

st.download_button("CSV 다운로드", show.to_csv(index=False).encode("utf-8-sig"),
                   file_name=f"수익율_{query.strip()}.csv", mime="text/csv")


