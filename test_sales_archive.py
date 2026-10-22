"""실행: python test_sales_archive.py — 통합 전 중복·반품·당월 우선순위 유지."""
import ast
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

path = Path(__file__).with_name('상품수익율검색기.py')
tree = ast.parse(path.read_text(encoding='utf-8-sig'))
nodes = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in ('_read_one', '_키해시', 'load_all_data'):
        node.decorator_list = []
        nodes.append(node)
    elif isinstance(node, ast.Assign):
        try:
            ast.literal_eval(node.value)
        except (ValueError, TypeError):
            if not any(isinstance(t, ast.Name) and t.id in ('CAT_COLS', 'USE_COLS') for t in node.targets):
                continue
        nodes.append(node)
ns = {'pd': pd, 'np': np, 'os': os, 're': re}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
ns['load_store_malls'] = lambda _: frozenset()


def sale(order, date, price=100, qty=1):
    return {'주문번호': order, '모델명': order, '출고날짜': date, '수량': qty,
            '출고원가': 50 * qty, '최종판매가': price, '수익원(실배송비)': 10 * qty,
            '쇼핑몰': 'TEST', '브랜드': 'TEST'}


with TemporaryDirectory() as directory:
    root = Path(directory)
    june = pd.DataFrame([sale('A', '2026-06-30'), sale('B', '2026-06-30'),
                         sale('B', '2026-06-30'), sale('RETURN', '2026-06-30')])
    july = pd.DataFrame([sale('A', '2026-07-01', 999), sale('C', '2026-07-01', 200),
                         sale('RETURN', '2026-06-30', -100, -1)])
    table = pa.Table.from_pandas(pd.concat([june, july], ignore_index=True), preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    sources = [{'rows': len(june), 'mtime': 30}, {'rows': len(july), 'mtime': 20}]
    metadata.update({b'sales_archive_sources': json.dumps(sources).encode(), b'sales_archive_end': b'2026-09'})
    archive = root / '2026년매출.parquet'
    pq.write_table(table.replace_schema_metadata(metadata), archive)
    current = root / '2026-10월매출.parquet'
    pd.DataFrame([sale('C', '2026-10-01', 300)]).to_parquet(current, index=False)
    sigs = ((str(archive), 9999), (str(current), 10))
    result = ns['load_all_data'](sigs)
    assert len(result) == 4 and result['최종판매가'].sum() == 600
    assert result['모델명'].astype(str).tolist().count('B') == 2
    assert result.loc[result['모델명'] == 'A', '최종판매가'].iloc[0] == 100
    assert result.loc[result['모델명'] == 'C', '출고날짜'].iloc[0] == pd.Timestamp('2026-10-01')
    assert 'RETURN' not in set(result['모델명'])
    sources[-1]['rows'] -= 1
    metadata[b'sales_archive_sources'] = json.dumps(sources).encode()
    pq.write_table(table.replace_schema_metadata(metadata), archive)
    try:
        ns['load_all_data'](sigs)
    except ValueError as error:
        assert '행수 합계' in str(error)
    else:
        raise AssertionError('Invalid archive metadata must fail instead of losing rows')

print('PASS: original source priority, current month, legitimate duplicate rows, returns and invalid metadata')
