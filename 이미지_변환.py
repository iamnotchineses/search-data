# -*- coding: utf-8 -*-
"""
이미지.xlsx → 이미지.parquet
==========================================================================
앱은 '모델명 → 이미지 URL' 표를 parquet 으로 읽습니다.
저장소는 .gitignore 로 xlsx 를 막아두어(용량 문제) parquet 만 올라갑니다.

  이미지.xlsx 를 새로 받으면 이 파일(또는 이미지_변환.bat)을 한 번 실행하고
  저장소_반영.bat 으로 올리면 됩니다.
"""

import os
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
SHEET = "이미지"


def main() -> int:
    후보 = sorted(SCRIPT_DIR.glob("*이미지*.xlsx"))
    후보 = [p for p in 후보 if not p.name.startswith("~$")]
    if not 후보:
        print("[오류] 이미지 xlsx 를 찾을 수 없습니다.")
        print(f"       (찾은 폴더: {SCRIPT_DIR})")
        return 1

    src = max(후보, key=lambda p: p.stat().st_mtime)
    out = SCRIPT_DIR / "이미지.parquet"
    print(f"읽는 중: {src.name}  ({src.stat().st_size / 1024 / 1024:.1f} MB)")

    df = pd.read_excel(src, sheet_name=SHEET)
    df.columns = [str(c).strip() for c in df.columns]
    누락 = {"모델명", "이미지"} - set(df.columns)
    if 누락:
        print(f"[오류] 컬럼이 없습니다: {sorted(누락)}")
        print(f"       현재 컬럼: {list(df.columns)}")
        return 1

    df = df[["모델명", "이미지"]].dropna()
    df["모델명"] = df["모델명"].astype(str).str.strip()
    df["이미지"] = df["이미지"].astype(str).str.strip()
    중복 = int(df["모델명"].duplicated().sum())
    df = df.drop_duplicates(subset=["모델명"], keep="last")

    df.to_parquet(out, compression="zstd", index=False)
    print(f"[완료] {out.name}  {len(df):,}행  "
          f"{out.stat().st_size / 1024:.0f} KB"
          + (f"  (중복 모델명 {중복:,}건은 마지막 것만 남김)" if 중복 else ""))
    print()
    print("이제 저장소_반영.bat 으로 올리면 앱에 반영됩니다.")
    return 0


if __name__ == "__main__":
    코드 = 1
    try:
        코드 = main()
    except Exception:
        import traceback
        print("\n[전체 오류 내용]")
        traceback.print_exc()
        코드 = 2
    try:
        input("\n[Enter] 를 누르면 창이 닫힙니다...")
    except Exception:
        pass
    sys.exit(코드)
